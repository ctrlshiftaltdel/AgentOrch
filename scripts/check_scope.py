#!/usr/bin/env python3
"""Read-only G2 scope check: changed paths vs the allowed paths of one task.

Merge gate G2 asks whether every changed path is inside the allowed paths of the
task under review. The historical evidence was a human comparison of
``git diff --name-only`` + ``git status --short``. That comparison has no base
SHA: a bare ``git diff`` compares worktree to index, so it ignores work already
committed on the branch, and it silently drops untracked files. This tool turns
the same question into a set judgement:

* **base** - an explicit ``--base <40-hex>`` wins; otherwise the selected task's
  ``approved_sha``, then its ``head_sha``. When both are empty the answer is
  "undeterminable" (exit 1), never a silent pass.
* **changes** - three sources, unioned: committed paths in ``base...HEAD``
  (three-dot: only this branch's commits), worktree/staged edits against ``HEAD``,
  and every untracked file (``--exclude-standard``). Rename detection is off, so
  a rename reports both the deleted and the added path, and a deletion is never
  lost.
* **allowed paths** - ``paths_json`` of one task in the local task database.
  "One task" is resolved as: ``--task <id>`` when given (any status, so an
  archived task can be audited), otherwise the single task whose status is in
  ``scripts.orchestrator.ACTIVE``. Zero or several active tasks is exit 1 with
  the candidate ids; the activity set is imported, never re-declared here.

Exit codes mirror ``scripts/check_drift.py``:

* ``0`` - every change is inside the allowed paths (``in-scope``)
* ``2`` - at least one change is outside them (``out-of-scope``)
* ``1`` - invalid input, unreadable task database, Git error, or a base that
  cannot be resolved to a commit (``error``); undeterminable is never a pass

Read-only guarantee: the task database is copied into a private temporary
directory and read there, so not even SQLite's ``-shm`` sidecar appears next to
the original registry. A copy is accepted only when the source is observed
unchanged while it is copied and SQLite's ``integrity_check`` accepts the copy;
otherwise the answer is ``error`` (exit 1), never ``in-scope``. A live
write-ahead log or rollback journal is refused outright: each file would have to
be copied separately, which cannot prove that both belong to one commit state,
and a mixed snapshot must never be reported as ``in-scope``.

Two layouts are recognised, and neither is guessed:

* **flat** - the script lives at ``<repository>/scripts/check_scope.py``. The
  repository root is the Git work-tree top level (the historical contract); the
  task database is ``<repository>/.orchestration/state.sqlite3``.
* **isolated** - the script lives at
  ``<host>/agentorch/scripts/check_scope.py``. Git commands run on the host
  work-tree top level ``H``, while the task database is
  ``H/agentorch/.local/orchestration/state.sqlite3`` and is never taken from
  ``H/.orchestration``. The layout is accepted only when ``H`` is the Git top
  level, ``H/agentorch`` carries the AgentOrch markers and the store path has
  no symlinked or hard-linked component. Anything else is exit 1.

The Git calls are ``diff`` / ``ls-files`` / ``rev-parse`` / ``cat-file -e``.
They run with system/global configuration, fsmonitor, external diff, optional
locks and network protocols disabled, and with environment variables that
redirect the repository removed. A Git failure is reported with its exit code
only - never stderr, which could carry file content. Nothing under the project
root is written, nothing is staged, committed, checked out or cleaned, no hook
is installed, and ``.git/config`` is never modified. ``ACTIVE`` and ``overlaps``
are imported from ``scripts/orchestrator`` so the activity set and the
path-overlap rule have one definition each.

Only the standard library is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:  # repository root on sys.path: `python -m unittest`, CI, module import
    from scripts.orchestrator import ACTIVE, ISOLATED_DIRNAME, overlaps
except ImportError:  # `python scripts/check_scope.py`: only scripts/ is on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.orchestrator import ACTIVE, ISOLATED_DIRNAME, overlaps

# Schema 2 adds the resolved ``layout`` and the root-relative ``task_database``
# label, so evidence can show which store was read without the host path.
REPORT_SCHEMA_VERSION = 2

EXIT_IN_SCOPE = 0
EXIT_ERROR = 1
EXIT_OUT_OF_SCOPE = 2

STATUS_IN_SCOPE = 'in-scope'
STATUS_OUT_OF_SCOPE = 'out-of-scope'
STATUS_ERROR = 'error'

# A base must be a full commit SHA. Short SHAs, revision expressions (`main`,
# `HEAD~2`) and prefixes are rejected on purpose: the base is evidence, and a
# moving name is not reproducible.
SHA_RE = re.compile(r'^[0-9a-fA-F]{40}$')

DB_DIR = '.orchestration'
DB_NAME = 'state.sqlite3'
TASK_COLUMNS = 'id,status,title,paths_json,approved_sha,head_sha'

# Open flags are read through ``getattr``: importing this module must not raise
# on a platform that does not define them, and an absent ``O_NOFOLLOW`` must not
# degrade into a plain open that follows a link. Where the flag is missing the
# entry is inspected before the open and the opened descriptor's identity must
# equal the inspected one; an identity the platform cannot report is a stop.
_O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
_O_BINARY = getattr(os, 'O_BINARY', 0)
_O_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_O_NONBLOCK = getattr(os, 'O_NONBLOCK', 0)

AGENTORCH_DIRNAME = ISOLATED_DIRNAME
ISOLATED_STATE_DIR = ('.local', 'orchestration')
LAYOUT_FLAT = 'flat'
LAYOUT_ISOLATED = 'isolated'

# Environment variables that make Git ignore ``-C`` and operate on another
# repository, or inject configuration. They are removed, not merely overridden.
GIT_ENV_DROP = frozenset({
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE', 'GIT_COMMON_DIR',
    'GIT_EXTERNAL_DIFF', 'GIT_DIFF_OPTS', 'GIT_PREFIX', 'GIT_QUARANTINE_PATH',
    'GIT_CEILING_DIRECTORIES', 'GIT_DISCOVERY_ACROSS_FILESYSTEM',
    'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_EXEC_PATH',
    'GIT_SSH', 'GIT_SSH_COMMAND', 'GIT_PROXY_COMMAND', 'GIT_ASKPASS',
    'SSH_ASKPASS',
})
GIT_CONFIG_ENV_PREFIXES = ('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_')

# Global options that keep the commands plumbing-only. Unknown options or
# values make Git fail, which this tool turns into exit 1 (fail closed).
GIT_HARDENING = (
    '--no-pager',
    '--no-optional-locks',
    '-c', 'protocol.allow=never',
    '-c', 'core.fsmonitor=false',
    '-c', 'core.untrackedCache=false',
)


class ScopeError(Exception):
    """Invalid input, unreadable state, or a Git failure (exit code 1)."""


class GitCommandError(ScopeError):
    """A Git command exited non-zero; stderr is deliberately discarded."""


@dataclass(frozen=True)
class ScopeRoots:
    """The two roots a check needs, resolved and proven before any read.

    ``git_root`` is where Git runs; ``state_root`` is the AgentOrch root that
    owns the task database. In a flat checkout they are the same directory.
    """

    layout: str
    git_root: Path
    state_root: Path
    database: Path
    database_label: str


# --- task database (read-only) ----------------------------------------------

def _stat_signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _dir_listing(path):
    try:
        return sorted(item.name for item in Path(path).iterdir())
    except OSError as exc:
        raise ScopeError(f'cannot list the task-database directory: {exc}')


def _file_identity(info):
    """``(st_dev, st_ino)`` when the platform reports a usable inode identity."""
    device = getattr(info, 'st_dev', None)
    inode = getattr(info, 'st_ino', None)
    if not isinstance(device, int) or not isinstance(inode, int) or inode == 0:
        return None
    return device, inode


def _inspect_regular_file(path, label):
    """``lstat`` *path* and refuse a symlink; ``(None, None)`` when it is absent.

    This is the only existence check the task database gets. ``Path.is_file()``
    follows a link, so its answer cannot stand in for "a regular file that was
    not reached through a link": a link to a readable SQLite database would be
    reported as the task database. The returned identity is what a later open
    must match when the kernel cannot refuse the link itself.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise ScopeError(f'cannot inspect the {label}: {exc.strerror or exc}')
    if stat.S_ISLNK(info.st_mode):
        raise ScopeError(f'the {label} is a symlink; refusing to follow it')
    if not stat.S_ISREG(info.st_mode):
        raise ScopeError(f'the {label} is not a regular file')
    return info, _file_identity(info)


def _open_regular_file(source, label):
    """Open *source* read-only and prove no final-component link was followed.

    With ``O_NOFOLLOW`` the kernel refuses the link as part of the open. Without
    it, the entry is inspected first and the opened descriptor's identity must
    equal the inspected one; when the platform reports no identity the call
    fails closed before a byte is read. "Cannot prove it was not followed" is
    never "may follow".

    The descriptor is returned already proved regular, together with its
    ``fstat``, so the caller's before/after comparison has a real baseline.
    """
    expected = None
    if not _O_NOFOLLOW:
        info, expected = _inspect_regular_file(source, label)
        if info is None:
            raise ScopeError(f'the {label} disappeared before it could be read')
        if expected is None:
            raise ScopeError(
                f'cannot prove the {label} was opened without following a link: '
                'this platform reports no file identity')
    flags = os.O_RDONLY | _O_BINARY | _O_NOFOLLOW | _O_CLOEXEC | _O_NONBLOCK
    try:
        descriptor = os.open(source, flags)
    except FileNotFoundError:
        raise ScopeError(f'the {label} disappeared before it could be read')
    except OSError as exc:
        raise ScopeError(f'cannot read the {label}: {exc.strerror or exc}')
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ScopeError(f'the {label} is not a regular file')
        if expected is not None and _file_identity(before) != expected:
            raise ScopeError(f'the {label} changed while it was being opened')
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, before


def _digest_file(path, label='task database'):
    """SHA-256 of *path*, read through the same no-follow open as the copy."""
    descriptor, _ = _open_regular_file(path, label)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _reject_component_link(path, label, *, is_dir):
    """Refuse a symlink (or, for files, a hard link) before reading it.

    These are pre-read checks, not atomic isolation: a same-UID writer can swap
    the entry between the check and the read. The claim is only that a
    recognisably isolated invocation does not follow an existing link.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ScopeError(f'cannot inspect the {label}: {exc.strerror or exc}')
    if stat.S_ISLNK(info.st_mode):
        raise ScopeError(f'isolated layout refuses a symlinked {label}')
    if is_dir:
        if not stat.S_ISDIR(info.st_mode):
            raise ScopeError(f'the {label} is not a directory')
        return
    if not stat.S_ISREG(info.st_mode):
        raise ScopeError(f'the {label} is not a regular file')
    if info.st_nlink > 1:
        raise ScopeError(f'isolated layout refuses a hard-linked {label}')


def _validate_isolated_store(roots):
    base = roots.state_root
    state = roots.database.parent
    for label, path, is_dir in (
            ('AgentOrch root', base, True),
            ('state directory .local', base / ISOLATED_STATE_DIR[0], True),
            ('state directory orchestration', state, True),
            ('task database', roots.database, False),
            ('task database write-ahead log',
             roots.database.with_name(roots.database.name + '-wal'), False),
    ):
        _reject_component_link(path, label, is_dir=is_dir)


def _copy_regular_file(source, destination, label):
    """Copy one regular file into a path this call creates, refusing links.

    The source is opened by ``_open_regular_file``: ``O_NOFOLLOW`` closes the
    final-component race at open time, and without the flag the inspected
    identity must match the opened one or the copy never starts. The destination
    is created exclusively (``O_CREAT`` / ``O_EXCL``) in the private temporary
    directory ``load_tasks`` owns, so a link planted at that path is refused
    rather than followed out of the directory, and an existing entry is never
    truncated. The fstat before/after comparison proves only that the source was
    not observed to change during this copy - it is not a lock and not a claim
    about a same-UID writer.
    """
    descriptor, before = _open_regular_file(source, label)
    digest = hashlib.sha256()
    try:
        copy_flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY
                      | _O_NOFOLLOW | _O_CLOEXEC)
        try:
            copy_descriptor = os.open(destination, copy_flags, 0o600)
        except OSError as exc:
            raise ScopeError(
                'cannot create the private copy of the '
                f'{label}: {exc.strerror or exc}')
        try:
            with os.fdopen(descriptor, 'rb', closefd=False) as stream, \
                    os.fdopen(copy_descriptor, 'wb', closefd=False) as copy:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
                    copy.write(chunk)
        finally:
            os.close(copy_descriptor)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _stat_signature(before) != _stat_signature(after):
        raise ScopeError(
            f'cannot prove a consistent task-database snapshot: the {label} '
            'changed while it was copied')
    return digest.hexdigest()


def load_tasks(roots):
    """Every task in the resolved registry as a dict keyed by id.

    The registry is read from a copy in a private temporary directory. Opening
    the original in place would make SQLite create a ``-shm`` sidecar next to it
    (WAL databases need shared memory even for read-only readers), which would
    break the promise that this check writes nothing under the project root.
    ``orchestrator.connect()` is deliberately not used: it migrates the database
    on open.

    The source must be observed unchanged while it is copied and while the copy
    is read; otherwise the answer is "undeterminable", never a snapshot that
    might silently omit the newest rows. The source is opened without following
    a final-component link: ``O_NOFOLLOW`` where the platform has it, otherwise
    an inspected identity that the opened descriptor must match - and a platform
    that reports no identity is itself "undeterminable". A live write-ahead log
    or rollback journal is refused before any copy: the files would be copied one
    at a time, and a same-UID writer can replace them between copies, so no
    answer can prove they are one commit state. A mixed snapshot is never
    reported as in-scope.
    """
    path = roots.database
    display = roots.database_label if roots.layout == LAYOUT_ISOLATED else str(path)
    if roots.layout == LAYOUT_ISOLATED:
        _validate_isolated_store(roots)
    if _inspect_regular_file(path, 'task database')[0] is None:
        raise ScopeError(
            f'task database not found: {display}; this check reads the local registry '
            f'({DB_DIR}/{DB_NAME}) and never creates it')
    wal = path.with_name(path.name + '-wal')
    journal = path.with_name(path.name + '-journal')
    if os.path.lexists(wal):
        raise ScopeError(
            'cannot prove a consistent task-database snapshot: a write-ahead log '
            'is present and a separate copy cannot prove it belongs to the same '
            'commit state as the database')
    if os.path.lexists(journal):
        raise ScopeError(
            'cannot prove a consistent task-database snapshot: a rollback journal '
            'is present and a separate copy cannot prove it belongs to the same '
            'commit state as the database')
    try:
        with tempfile.TemporaryDirectory(prefix='check_scope-') as tmp:
            copy = Path(tmp) / DB_NAME
            listing_before = _dir_listing(path.parent)
            database_digest = _copy_regular_file(path, copy, 'task database')
            tasks = _read_tasks(copy, display)
            if _digest_file(path) != database_digest:
                raise ScopeError(
                    'cannot prove a consistent task-database snapshot: the database '
                    'changed while it was read')
            if os.path.lexists(wal) or os.path.lexists(journal):
                raise ScopeError(
                    'cannot prove a consistent task-database snapshot: a '
                    'write-ahead log or rollback journal appeared while it was read')
            if _dir_listing(path.parent) != listing_before:
                raise ScopeError(
                    'cannot prove the task database was read-only: its directory '
                    'changed while it was read')
            return tasks
    except OSError as exc:
        raise ScopeError(f'cannot read the task database {display}: {exc}')


def _read_tasks(path, display=None):
    label = display or path
    try:
        con = sqlite3.connect(path)
    except sqlite3.Error:
        # The driver text can quote schema or file content; never forward it.
        raise ScopeError(f'cannot open the task database {label}') from None
    try:
        con.execute('PRAGMA query_only=ON')
        verdict = [row[0] for row in con.execute('PRAGMA integrity_check').fetchall()]
        if verdict != ['ok']:
            raise ScopeError(
                f'cannot prove a consistent task-database snapshot: integrity_check '
                f'rejected {label}')
        con.row_factory = sqlite3.Row
        rows = con.execute(f'SELECT {TASK_COLUMNS} FROM tasks').fetchall()
    except sqlite3.Error:
        raise ScopeError(f'cannot read the task database {label}') from None
    finally:
        con.close()

    tasks = {}
    for row in rows:
        try:
            paths = json.loads(row['paths_json'])
        except (TypeError, ValueError):
            # Neither the parser text nor the row id (both database content) may
            # reach the report.
            raise ScopeError(
                'the task database contains an unreadable allowed-paths value') from None
        if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
            raise ScopeError(
                'the task database contains an allowed-paths value that is not a '
                'non-empty list of strings')
        tasks[row['id']] = {
            'id': row['id'],
            'status': row['status'],
            'title': row['title'],
            'allowed_paths': list(paths),
            'approved_sha': (row['approved_sha'] or '').strip(),
            'head_sha': (row['head_sha'] or '').strip(),
        }
    return tasks


def select_task(tasks, task_id=None):
    """Return (task, notes). Never guesses when the answer is ambiguous."""
    if task_id:
        if task_id not in tasks:
            raise ScopeError(
                f'unknown task {task_id!r} in the task database; known ids: '
                + (', '.join(sorted(tasks)) or '(none)'))
        task = tasks[task_id]
        notes = []
        if task['status'] not in ACTIVE:
            notes.append(f'task {task["id"]} is {task["status"]}, not an active status; '
                         'it was checked because it was named explicitly')
        return task, notes

    active = [task for task in tasks.values() if task['status'] in ACTIVE]
    if not active:
        raise ScopeError(
            'no active task in the task database: every task is terminal or the '
            'registry is empty, so no allowed paths can be attributed; pass '
            '--task <id> to name the task (a terminal task is accepted there)')
    if len(active) > 1:
        listed = ', '.join(f'{task["id"]} ({task["status"]})' for task in sorted(
            active, key=lambda item: item['id']))
        raise ScopeError(
            f'multiple active tasks: {listed}; pass --task <id> to select exactly '
            'one set of allowed paths (this check never unions them)')
    return active[0], []


# --- base resolution ---------------------------------------------------------

def _checked_sha(value, label):
    text = (value or '').strip()
    if not SHA_RE.match(text):
        raise ScopeError(f'{label} must be a 40-hex commit SHA, got {value!r}')
    return text.lower()


def resolve_base(explicit, task):
    """Return (sha, source). Undeterminable is an error, never an empty base."""
    if explicit is not None:
        return _checked_sha(explicit, '--base'), '--base'
    for source in ('approved_sha', 'head_sha'):
        value = task.get(source) or ''
        if value:
            return _checked_sha(value, f'task {task["id"]} {source}'), source
    raise ScopeError(
        f'task {task["id"]} has neither approved_sha nor head_sha; pass --base '
        '<40-hex commit sha> so the diff has a fixed anchor')


# --- git plumbing (read-only) ------------------------------------------------

def _decode(raw):
    return raw.decode('utf-8', 'surrogateescape')


def _git_environment():
    """A Git environment with every repository redirect and config injection gone.

    ``GIT_CONFIG_GLOBAL`` / ``GIT_CONFIG_SYSTEM`` pointing at the null device and
    ``GIT_CONFIG_NOSYSTEM=1`` keep user/system configuration out. ``GIT_ATTR_NOSYSTEM``
    keeps system attributes out. A Git that does not understand one of these
    settings fails the command, which is exit 1 here - never a silent downgrade.
    """
    env = {}
    for name, value in os.environ.items():
        if name in GIT_ENV_DROP:
            continue
        if any(name.startswith(prefix) for prefix in GIT_CONFIG_ENV_PREFIXES):
            continue
        env[name] = value
    env.update({
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_CONFIG_SYSTEM': os.devnull,
        'GIT_ATTR_NOSYSTEM': '1',
        'GIT_OPTIONAL_LOCKS': '0',
        'GIT_TERMINAL_PROMPT': '0',
        'GIT_PAGER': 'cat',
        'PAGER': 'cat',
    })
    return env


def _git(root, *args):
    """Run one plumbing command; never forward stderr into the report."""
    command = ['git', *GIT_HARDENING, '-C', str(root), *args]
    try:
        proc = subprocess.run(command, capture_output=True, env=_git_environment())
    except OSError as exc:
        raise ScopeError(f'cannot run git: {exc}')
    if proc.returncode != 0:
        # Only the exit code: stderr is not evidence and could carry file text.
        operation = args[0] if args else 'command'
        raise GitCommandError(f'git {operation} failed (exit {proc.returncode})')
    return proc.stdout


def _z_paths(raw):
    return [path for path in _decode(raw).split('\0') if path]


def work_tree_root(root='.'):
    """Resolve and require the top level of a Git work tree."""
    root_path = Path(root)
    if not root_path.exists():
        raise ScopeError(f'project root does not exist: {root}')
    if not root_path.is_dir():
        raise ScopeError(f'project root is not a directory: {root}')
    root_path = root_path.resolve()
    try:
        top = _decode(_git(root_path, 'rev-parse', '--show-toplevel')).strip()
    except GitCommandError as exc:
        raise ScopeError(f'not inside a git work tree: {root_path} ({exc})')
    if not top:
        raise ScopeError(f'not inside a git work tree: {root_path}')
    top_path = Path(top).resolve()
    if top_path != root_path:
        raise ScopeError(
            f'{root_path} is not the top level of its git work tree ({top_path}); '
            'allowed paths are repository-relative, so pass the repository root')
    return root_path


def _script_under_agentorch(path):
    """True for the isolated script shape; a hint, never an authentication."""
    path = Path(path)
    return (path.name == 'check_scope.py'
            and path.parent.name == 'scripts'
            and path.parent.parent.name == AGENTORCH_DIRNAME)


def _flat_roots(root):
    root_path = work_tree_root('.' if root is None else root)
    return ScopeRoots(LAYOUT_FLAT, root_path, root_path,
                      root_path / DB_DIR / DB_NAME, f'{DB_DIR}/{DB_NAME}')


def resolve_roots(root=None):
    """Resolve (and prove) the Git root and task-store root for this layout.

    A flat checkout keeps the historical ``--root`` / current-directory contract.
    An isolated install derives ``H`` from the script's own location, pins Git
    there and reads ``H/agentorch/.local/orchestration/state.sqlite3``. Anything
    that cannot be proven returns an error; the host's flat registry is never a
    fallback.
    """
    lexical = Path(os.path.abspath(__file__))
    actual = Path(__file__).resolve()
    if not (_script_under_agentorch(lexical) or _script_under_agentorch(actual)):
        return _flat_roots(root)

    kit = actual.parent.parent
    if _script_under_agentorch(lexical):
        lexical_kit = lexical.parent.parent
        if lexical_kit.is_symlink():
            raise ScopeError('isolated layout refuses a symlinked AgentOrch root')
        if lexical_kit.resolve() != kit:
            raise ScopeError(
                'cannot prove the isolated layout: the script path and its resolved '
                'path point at different AgentOrch roots')
    try:
        own_top = Path(_decode(_git(kit, 'rev-parse', '--show-toplevel')).strip()).resolve()
    except GitCommandError as exc:
        # The shape is isolated but the Git linkage cannot be proven. Falling
        # back to the flat store would read the host's old registry, which is
        # exactly what this layout must never do.
        raise ScopeError(
            f'cannot prove the isolated layout: the AgentOrch root is not inside a '
            f'Git work tree ({exc})')
    if own_top == kit:
        # A flat checkout that simply lives in a directory named ``agentorch``.
        return _flat_roots(root)

    host = kit.parent
    if kit.is_symlink() or host.is_symlink():
        raise ScopeError('isolated layout refuses a symlinked AgentOrch or host root')
    if own_top != host:
        raise ScopeError(
            'cannot prove the isolated layout: the AgentOrch root is not directly '
            'inside the Git work-tree top level')
    if not (kit / 'scripts' / 'orchestrator.py').is_file():
        raise ScopeError(
            'cannot prove the isolated layout: the AgentOrch scheduler is missing')
    if not ((kit / 'VERSION.md').is_file() or (kit / 'MANIFEST.json').is_file()):
        raise ScopeError(
            'cannot prove the isolated layout: the AgentOrch version marker is missing')
    if root is not None:
        if not str(root).strip():
            raise ScopeError('--root must not be empty')
        given = Path(root)
        if given.is_symlink():
            raise ScopeError('--root must not be a symlink')
        try:
            resolved = given.resolve(strict=True)
        except OSError:
            raise ScopeError(f'--root does not exist: {root}')
        if not resolved.is_dir():
            raise ScopeError(f'--root is not a directory: {root}')
        if resolved != host:
            raise ScopeError(
                'isolated layout pins Git operations to the host work-tree top level; '
                '--root must be that directory')

    state = kit / ISOLATED_STATE_DIR[0] / ISOLATED_STATE_DIR[1]
    label = (f'{AGENTORCH_DIRNAME}/{ISOLATED_STATE_DIR[0]}/'
             f'{ISOLATED_STATE_DIR[1]}/{DB_NAME}')
    return ScopeRoots(LAYOUT_ISOLATED, host, kit, state / DB_NAME, label)


def require_commit(root, sha, source):
    try:
        _git(root, 'cat-file', '-e', f'{sha}^{{commit}}')
    except GitCommandError:
        raise ScopeError(f'base {sha} (from {source}) is not a commit in this '
                         'repository; pass a full 40-hex SHA of a commit that exists here')


def collect_changes(root, base):
    """{path: [sources]} for committed, worktree and untracked changes."""
    changes = {}

    def add(paths, source):
        for path in paths:
            sources = changes.setdefault(path, [])
            if source not in sources:
                sources.append(source)

    # Three-dot: only the commits on this branch, not the reverse of commits that
    # landed on the base branch after the fork point. External diff and textconv
    # are disabled: neither may run a repository-configured program.
    add(_z_paths(_git(root, 'diff', '--name-only', '--no-renames', '--no-ext-diff',
                       '--no-textconv', '-z', f'{base}...HEAD')), 'committed')
    # Staged + unstaged edits against HEAD. Deletions appear here as well.
    add(_z_paths(_git(root, 'diff', '--name-only', '--no-renames', '--no-ext-diff',
                       '--no-textconv', '-z', 'HEAD')), 'worktree')
    # Every untracked, non-ignored file counts: "no untracked baggage" is exactly
    # what G2 wants to keep visible.
    add(_z_paths(_git(root, 'ls-files', '--others', '--exclude-standard', '-z')), 'untracked')
    return changes


# --- report ------------------------------------------------------------------

def _empty_report(root):
    return {
        'tool': 'check_scope',
        'report_schema_version': REPORT_SCHEMA_VERSION,
        'root': str(root),
        'layout': None,
        'task_database': None,
        'status': STATUS_ERROR,
        'exit_code': EXIT_ERROR,
        'task': None,
        'base': {'sha': None, 'source': None},
        'changes': [],
        'in_scope': [],
        'out_of_scope': [],
        'summary': {'committed': 0, 'worktree': 0, 'untracked': 0,
                    'total': 0, 'in_scope': 0, 'out_of_scope': 0},
        'notes': [],
    }


def check_scope(root=None, task_id=None, base=None):
    """Return the stable report dict. Never writes anything."""
    roots = resolve_roots(root)
    tasks = load_tasks(roots)
    task, notes = select_task(tasks, task_id)
    base_sha, base_source = resolve_base(base, task)
    require_commit(roots.git_root, base_sha, base_source)
    changes = collect_changes(roots.git_root, base_sha)

    report = _empty_report(roots.git_root)
    report['layout'] = roots.layout
    report['task_database'] = roots.database_label
    report['task'] = {'id': task['id'], 'status': task['status'],
                      'title': task['title'], 'allowed_paths': task['allowed_paths']}
    report['base'] = {'sha': base_sha, 'source': base_source}

    entries = []
    for path in sorted(changes):
        in_scope = any(overlaps(path, allowed) for allowed in task['allowed_paths'])
        entries.append({'path': path, 'sources': changes[path], 'in_scope': in_scope})
        (report['in_scope'] if in_scope else report['out_of_scope']).append(path)
    report['changes'] = entries

    summary = report['summary']
    summary['total'] = len(entries)
    summary['in_scope'] = len(report['in_scope'])
    summary['out_of_scope'] = len(report['out_of_scope'])
    for entry in entries:
        for source in entry['sources']:
            summary[source] += 1

    out_of_scope = bool(report['out_of_scope'])
    report['status'] = STATUS_OUT_OF_SCOPE if out_of_scope else STATUS_IN_SCOPE
    report['exit_code'] = EXIT_OUT_OF_SCOPE if out_of_scope else EXIT_IN_SCOPE
    report['notes'] = notes
    if not entries:
        report['notes'].append('no changes against the base: nothing to judge')
    return report


def error_report(root, message):
    try:
        resolved = str(Path(root).resolve())
    except OSError:
        resolved = str(root)
    report = _empty_report(resolved)
    report['error'] = message
    return report


def render_report(report, stream):
    """Human-readable evidence: base SHA, task, changed paths, verdict."""
    print(f"status: {report['status']} (exit {report['exit_code']})", file=stream)
    if report.get('error'):
        print(f"error: {report['error']}", file=stream)
    if report.get('layout'):
        print(f"layout: {report['layout']}", file=stream)
    if report.get('task_database'):
        print(f"task database: {report['task_database']}", file=stream)
    task = report['task']
    if task:
        print(f"task: {task['id']} ({task['status']}), allowed: "
              + (', '.join(task['allowed_paths']) or '(none)'), file=stream)
    base = report['base']
    if base['sha']:
        print(f"base: {base['sha']} (source: {base['source']})", file=stream)
    summary = report['summary']
    print(f"changes: {summary['total']} (committed {summary['committed']}, "
          f"worktree {summary['worktree']}, untracked {summary['untracked']}); "
          f"in scope {summary['in_scope']}, out of scope {summary['out_of_scope']}",
          file=stream)
    for entry in report['changes']:
        label = 'in-scope' if entry['in_scope'] else 'out-of-scope'
        print(f"  [{label}] {entry['path']} ({', '.join(entry['sources'])})", file=stream)
    for note in report['notes']:
        print(f'note: {note}', file=stream)


# --- CLI ---------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse would exit 2; here 2 means "out of scope", so a bad invocation
        # must not be mistaken for an out-of-scope verdict.
        raise ScopeError(f'{message} (see --help)')


def build_parser():
    parser = _Parser(
        description='Read-only G2 scope check: changed paths vs one task\'s allowed paths.',
        epilog='Exit codes: 0 in scope, 2 out of scope, 1 invalid input or undeterminable.')
    parser.add_argument('--root', default=None,
                        help='repository root; flat default: current directory; an '
                             'isolated install pins Git to the host work-tree top level')
    parser.add_argument('--task', default=None,
                        help='task id to read allowed paths from; required when more '
                             'than one task is active')
    parser.add_argument('--base', default=None,
                        help='40-hex commit SHA to diff against; defaults to the '
                             'task approved_sha, then its head_sha')
    parser.add_argument('--json', action='store_true',
                        help='print one JSON document on stdout; human summary goes to stderr')
    return parser


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except ScopeError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return EXIT_ERROR

    try:
        report = check_scope(args.root, args.task, args.base)
    except ScopeError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        if args.json:
            print(json.dumps(error_report('.' if args.root is None else args.root, str(exc)),
                             indent=2, sort_keys=True, ensure_ascii=False))
        return EXIT_ERROR

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
        render_report(report, sys.stderr)
    else:
        render_report(report, sys.stdout)
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
