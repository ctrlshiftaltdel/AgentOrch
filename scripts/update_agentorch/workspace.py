"""Project workspace inspection, staging, backup and rollback.

Everything in this module is read-only until :func:`atomic_write` is called. The
engine only calls the writers after the package is verified and a backup has been
created and verified. ``git stash`` / ``git checkout --`` / ``git clean`` are
never executed: uncommitted work is either left alone in an isolated worktree or
the run stops.
"""
from __future__ import annotations

import difflib
import errno
import hashlib
import json
import os
import posixpath
import re
import secrets
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

SKIP_DIRS = frozenset({
    '.git', '.orchestration', '.local', '.worktrees', '.workbuddy', 'release',
    '__pycache__', 'node_modules', '.venv', '.idea', '.vscode',
    '.mypy_cache', '.pytest_cache', '.ruff_cache',
})


class UnsafePath(Exception):
    """A target path would leave the project or traverse a symlink."""


def utc_stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# --- byte helpers -----------------------------------------------------------

def is_text(data: bytes) -> bool:
    if b'\x00' in data:
        return False
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        return False
    return True


def normalize_line_endings(data: bytes) -> bytes:
    """Same folding rule the release builder uses before hashing."""
    if not is_text(data):
        return data
    return data.replace(b'\r\n', b'\n')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(normalize_line_endings(data)).hexdigest()


# --- git state --------------------------------------------------------------

@dataclass
class GitState:
    is_repo: bool = False
    head_sha: str | None = None
    branch: str | None = None
    dirty_paths: list[str] = field(default_factory=list)
    isolated: bool = False
    commands: list[dict] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.dirty_paths)


def _run_git(root: Path, args: list[str], commands: list[dict], now: datetime) -> subprocess.CompletedProcess:
    commands.append({'command': ' '.join(args), 'exit_code': 0, 'ran_at': utc_stamp(now)})
    try:
        proc = subprocess.run(args, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, encoding='utf-8', errors='replace',
                              env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    except OSError:
        commands[-1]['exit_code'] = 127
        raise
    commands[-1]['exit_code'] = proc.returncode
    return proc


def _porcelain_paths(output: str) -> list[str]:
    paths: list[str] = []
    records = output.split('\0')
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        status = record[:2]
        path = record[3:]
        if path:
            paths.append(path)
        if status[:1] in ('R', 'C') and index < len(records) and records[index]:
            paths.append(records[index])
            index += 1
    return sorted(set(paths))


def inspect_git(root: Path, now: datetime) -> GitState:
    """Read head / branch / uncommitted paths. Never mutates the repository."""
    root = Path(root)
    state = GitState()
    try:
        inside = _run_git(root, ['git', 'rev-parse', '--is-inside-work-tree'], state.commands, now)
    except OSError:
        return state
    if inside.returncode != 0 or inside.stdout.strip() != 'true':
        return state
    state.is_repo = True

    head = _run_git(root, ['git', 'rev-parse', 'HEAD'], state.commands, now)
    if head.returncode == 0:
        state.head_sha = head.stdout.strip() or None
    branch = _run_git(root, ['git', 'rev-parse', '--abbrev-ref', 'HEAD'], state.commands, now)
    if branch.returncode == 0:
        state.branch = branch.stdout.strip() or None
    status = _run_git(root, ['git', 'status', '--porcelain=v1', '-z', '--untracked-files=all'],
                      state.commands, now)
    if status.returncode == 0:
        state.dirty_paths = _porcelain_paths(status.stdout)

    git_dir = _run_git(root, ['git', 'rev-parse', '--git-dir'], state.commands, now)
    common_dir = _run_git(root, ['git', 'rev-parse', '--git-common-dir'], state.commands, now)
    if git_dir.returncode == 0 and common_dir.returncode == 0:
        def resolved(value: str) -> Path:
            candidate = Path(value.strip())
            return candidate if candidate.is_absolute() else (root / candidate)
        state.isolated = resolved(git_dir.stdout).resolve() != resolved(common_dir.stdout).resolve()
    return state


@dataclass
class Isolation:
    path: Path | None
    commands: list[dict] = field(default_factory=list)
    detail: str | None = None


def create_isolation_worktree(root: Path, name: str, now: datetime) -> Isolation:
    """Create a dedicated branch + worktree under ``.worktrees/`` for APPLY.

    The original working tree (including uncommitted changes) is not touched.
    """
    root = Path(root)
    relative = f'.worktrees/update-{name}'
    target = root / relative
    if target.exists():
        return Isolation(None, [], f'{relative} already exists')
    commands: list[dict] = []
    proc = _run_git(root, ['git', 'worktree', 'add', '-q', '-b', f'update/{name}', relative, 'HEAD'],
                    commands, now)
    if proc.returncode != 0:
        return Isolation(None, commands, 'git worktree add failed')
    return Isolation(target, commands, None)


# --- project scan -----------------------------------------------------------

def scan_project_files(root: Path) -> dict[str, str]:
    """Relative path -> normalized sha256 for every regular project file."""
    root = Path(root)
    found: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in filenames:
            if name == '.git':
                continue
            full = Path(dirpath) / name
            if full.is_symlink() or not full.is_file():
                continue
            rel = full.relative_to(root).as_posix()
            try:
                found[rel] = sha256_bytes(full.read_bytes())
            except OSError:
                continue
    return dict(sorted(found.items()))


# --- safe writes ------------------------------------------------------------

def safe_project_path(root: Path, relative: str) -> Path:
    """Return the write target, refusing symlinks and paths outside ``root``."""
    root = Path(root)
    candidate = root / relative
    resolved_root = root.resolve()
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise UnsafePath(f'target path traverses a symlink: {relative}')
    if candidate.is_symlink():
        raise UnsafePath(f'target path is a symlink: {relative}')
    parent = candidate.parent.resolve()
    if parent != resolved_root and resolved_root not in parent.parents:
        raise UnsafePath(f'target path escapes the project: {relative}')
    return candidate


def target_problems(root: Path, paths: list[str]) -> list[str]:
    """Preflight the write set: refuse symlinks, directories and paths that escape."""
    problems: list[str] = []
    for relative in sorted(set(paths)):
        try:
            target = safe_project_path(root, relative)
        except UnsafePath as exc:
            problems.append(str(exc))
            continue
        if target.exists() and not target.is_file():
            problems.append(f'{relative}: target exists but is not a regular file')
    return problems


def atomic_write(path: Path, data: bytes) -> list[Path]:
    """Write ``data`` via a sibling temp file + ``os.replace``; return created dirs."""
    created: list[Path] = []
    probe = path.parent
    while not probe.exists() and probe != probe.parent:
        created.append(probe)
        probe = probe.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    created = list(reversed(created))
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix='.agentorch-write-')
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return created


# --- backup / restore -------------------------------------------------------

@dataclass
class Backup:
    location: Path
    created_at: str
    verified: bool
    entries: list[dict]
    metadata: dict

    @property
    def files_dir(self) -> Path:
        return self.location / 'files'


def _entry_for(root: Path, relative: str) -> dict:
    target = root / relative
    if target.is_file() and not target.is_symlink():
        data = target.read_bytes()
        return {
            'path': relative,
            'present': True,
            'sha256': sha256_bytes(data),
            'size': len(data),
        }
    return {'path': relative, 'present': False, 'sha256': None, 'size': None}


def create_backup(root: Path, paths: list[str], *, now: datetime, metadata: dict) -> Backup:
    """Copy every path that may be rewritten into a project-external directory."""
    root = Path(root)
    location = Path(tempfile.mkdtemp(prefix='agentorch-update-backup-'))
    files_dir = location / 'files'
    files_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for relative in sorted(set(paths)):
        entry = _entry_for(root, relative)
        if entry['present']:
            target = files_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((root / relative).read_bytes())
        entries.append(entry)
    payload = {
        'backup_schema_version': 1,
        'created_at': utc_stamp(now),
        'files': entries,
        **metadata,
    }
    (location / 'backup.json').write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n',
                                          encoding='utf-8')
    verified = verify_backup(location, entries)
    return Backup(location=location, created_at=payload['created_at'], verified=verified,
                  entries=entries, metadata=payload)


def verify_backup(location: Path, entries: list[dict]) -> bool:
    location = Path(location)
    try:
        payload = json.loads((location / 'backup.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    recorded = {entry['path']: entry for entry in payload.get('files') or [] if isinstance(entry, dict)}
    if set(recorded) != {entry['path'] for entry in entries}:
        return False
    for entry in entries:
        backup_copy = location / 'files' / entry['path']
        if entry['present']:
            if not backup_copy.is_file():
                return False
            if sha256_bytes(backup_copy.read_bytes()) != entry['sha256']:
                return False
            if backup_copy.stat().st_size != entry['size']:
                return False
        elif backup_copy.exists():
            return False
    return True


def restore_backup(root: Path, backup: Backup) -> list[str]:
    """Restore every backed-up path; return human-readable problems (empty = clean)."""
    root = Path(root)
    problems: list[str] = []
    for entry in backup.entries:
        relative = entry['path']
        try:
            target = safe_project_path(root, relative)
        except UnsafePath as exc:
            problems.append(str(exc))
            continue
        try:
            if entry['present']:
                data = (backup.files_dir / relative).read_bytes()
                atomic_write(target, data)
            elif target.is_file() or target.is_symlink():
                target.unlink()
        except OSError:
            problems.append(f'cannot restore {relative}')
    return problems


def verify_restore(root: Path, backup: Backup) -> list[str]:
    """Re-read the restored paths and compare them with the backup record."""
    root = Path(root)
    problems: list[str] = []
    for entry in backup.entries:
        target = root / entry['path']
        exists = target.is_file() and not target.is_symlink()
        if exists != entry['present']:
            problems.append(f'{entry["path"]}: presence differs after rollback')
            continue
        if not entry['present']:
            continue
        try:
            digest = sha256_bytes(target.read_bytes())
        except OSError:
            problems.append(f'{entry["path"]}: unreadable after rollback')
            continue
        if digest != entry['sha256']:
            problems.append(f'{entry["path"]}: sha256 differs after rollback')
    return problems


# =========================================================================== #
# Isolated onboarding: an existing project keeps its files, AgentOrch lives
# under ``<host>/agentorch/`` and the host root AGENTS.md gains one managed
# block (Issue #153).
#
# Everything below is still fail closed: it never overwrites a host file, never
# writes a second ``agentorch/AGENTS.md`` and never deletes a legacy file whose
# baseline cannot be proven. All filesystem writes happen inside ``agentorch/``
# plus the controlled root ``AGENTS.md``.
# =========================================================================== #

AGENTORCH_DIRNAME = 'agentorch'
# Host files that are never part of the host-invariance snapshot.
ISOLATED_PRIVATE_DIRS = frozenset({'.orchestration', '.local', '.worktrees', '.workbuddy', '__pycache__',
                                   '.pytest_cache', '.mypy_cache', '.ruff_cache', '.git'})
MANAGED_BEGIN_PREFIX = '<!-- AGENTORCH:BEGIN'
MANAGED_BEGIN_RE = re.compile(r'^<!-- AGENTORCH:BEGIN(?: v(?P<version>[^ ]+))? -->$')
MANAGED_END = '<!-- AGENTORCH:END -->'
MANAGED_HASH_TEMPLATE = '<!-- agentorch:managed-body-sha256: {} -->'
MANAGED_HASH_RE = re.compile(r'^<!-- agentorch:managed-body-sha256: (?P<sha>[0-9a-f]{64}) -->$')

MANAGED_GUIDANCE = (
    '手工处理建议：先备份 AGENTS.md；确认 AgentOrch 受管区块的标记成对且唯一。'
    '如果区块内容被手工修改，请先在区块外记录差异；确认后重建或删除成对标记，'
    '再重新运行安装器。安装器不会覆盖或猜测未确认的内容。')

AGENTORCH_GITIGNORE = """# AgentOrch private state (isolated layout). Never commit.
.orchestration/
.local/
.worktrees/
.workbuddy/
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.tmp
.agentorch-write.lock
"""

MARKDOWN_LINK_RE = re.compile(r'\]\((?P<target>[^)\s]+)(?P<title>\s+"[^"]*")?\)')
SOURCE_PATH_RE = re.compile(r"REQUIRED\s*=\s*\[(.*?)\]", re.S)

# Pure lexical validation for every host-relative path this module may write.
# No normalization (which could turn an escape into a different path) is done.
MAX_REL_PATH = 512
REL_PATH_RE = re.compile(r'^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$')
DRIVE_RE = re.compile(r'^[A-Za-z]:')
ALLOWED_ROOT_ENTRY = 'AGENTS.md'

_SOURCE_BINDING_SEAL = object()


class OnboardingError(ValueError):
    """A source tree or request is not usable for isolated onboarding."""


class PlatformUnsupported(OnboardingError):
    """The platform lacks the primitives required for a safe write set."""


class TransactionConflict(OnboardingError):
    """A host path changed between planning and writing."""


class ManagedBlockError(ValueError):
    """The root AGENTS.md managed block is missing, duplicated or modified."""

    def __init__(self, problems, *, diff=None, guidance=MANAGED_GUIDANCE,
                 conflict_type='OTHER_CONFLICT'):
        super().__init__('; '.join(problems))
        self.problems = list(problems)
        self.diff = diff
        self.guidance = guidance
        self.conflict_type = conflict_type


# --- platform primitives ----------------------------------------------------
# The transactional writer prefers POSIX ``dir_fd``-relative opens. The open
# flags are read through ``getattr`` so importing this module never raises
# ``AttributeError`` on a platform that does not define them: a missing flag
# makes the ``dir_fd`` writer unavailable (see
# ``_platform_capability_problems``) and a raw open never silently follows a
# symlink in place of a kernel-level refusal. Where those primitives do not
# exist at all (Windows) the same write set runs through the inspect-then-open
# path writer in ``_PathWriteSession``; when neither can run the transaction
# reports ``BLOCKED`` and writes nothing.
_O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
_O_DIRECTORY = getattr(os, 'O_DIRECTORY', 0)
_O_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
# Windows opens in text mode unless told otherwise, and a text-mode descriptor
# translates newlines and treats \x1a as end-of-file: that would corrupt the
# byte-exact content this writer must preserve. The flag is ``0`` on POSIX, so
# the ``dir_fd`` flag sets above are unchanged.
_O_BINARY = getattr(os, 'O_BINARY', 0)
_MISSING_OPEN_FLAGS = tuple(
    name for name, value in (('O_NOFOLLOW', _O_NOFOLLOW),
                             ('O_DIRECTORY', _O_DIRECTORY),
                             ('O_CLOEXEC', _O_CLOEXEC)) if not value)


def _file_identity(info) -> tuple[int, int] | None:
    """``(st_dev, st_ino)`` when the platform reports a usable inode identity."""
    device = getattr(info, 'st_dev', None)
    inode = getattr(info, 'st_ino', None)
    if not isinstance(device, int) or not isinstance(inode, int) or inode == 0:
        return None
    return device, inode


def open_no_follow(path, flags: int, *, label: str) -> int:
    """Open *path* without following a symlink that appears at open time.

    Where ``O_NOFOLLOW`` exists the kernel refuses a link as part of the open.
    A platform without it (Windows) cannot ask the kernel, so the entry is
    ``lstat``-ed first, opened, and the opened descriptor's identity is compared
    with the inspected identity. When no identity can be compared the open fails
    closed: following a link that cannot be ruled out is exactly the behaviour
    this helper exists to prevent.
    """
    if _O_NOFOLLOW:
        return os.open(path, flags | _O_NOFOLLOW | _O_CLOEXEC)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise OnboardingError(f'{label} cannot be inspected: {exc}') from exc
    if stat.S_ISLNK(before.st_mode):
        raise OnboardingError(f'{label} is a symlink; refusing to open it')
    expected = _file_identity(before)
    if expected is None:
        raise OnboardingError(
            f'{label} has no verifiable identity on this platform; refusing to open it')
    descriptor = os.open(path, flags)
    try:
        opened = _file_identity(os.fstat(descriptor))
        if opened is None:
            raise OnboardingError(
                f'{label} identity cannot be verified after opening; refusing to use it')
        if opened != expected:
            raise OnboardingError(f'{label} changed while it was being opened')
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


@dataclass(frozen=True)
class SourceBinding:
    """Opaque-in-practice record that a plan came from a verified release package.

    The seal is intentionally not a hash supplied by the caller: a plan without a
    binding built from :func:`package.verify_package` output is not writable. A
    self-signed manifest or plan digest is never accepted as authorization.
    """
    asset_sha256: str
    manifest_sha256: str
    suite: str
    format_version: str
    source_sha256: dict[str, str]
    planned_sha256: dict[str, str]
    _seal: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class BaselineBinding:
    """An *authenticated* old baseline for an isolated upgrade.

    Like :class:`SourceBinding`, the seal only comes from
    :func:`package.verify_package` output. The local ``MANIFEST.json`` can only
    hint which official release to fetch; it never becomes this object and its
    self-recorded hashes are not authorization. ``installed_sha256`` is the
    projection of the verified old release onto the isolated layout, so an
    installed tree must match every byte before an upgrade may write.
    """
    release_tag: str
    asset_sha256: str
    manifest_sha256: str
    suite: str
    format_version: str
    source_sha256: dict[str, str]
    installed_sha256: dict[str, str]
    _seal: object = field(repr=False, compare=False)
    # ``ISOLATED`` projects the release onto ``agentorch/``; ``LEGACY_ROOT``
    # compares the flat old install at its original host-root paths.
    kind: str = 'ISOLATED'


@dataclass
class OnboardingPlan:
    host_root: Path
    agentorch_root: Path
    source_root: Path
    scenario: str
    mode: str
    status: str = 'PLANNED'
    files: dict[str, bytes] = field(default_factory=dict)
    manifest_bytes: bytes | None = None
    agents_text: str | None = None
    agents_action: str = 'unchanged'
    agents_created: bool = False
    legacy_removals: list[str] = field(default_factory=list)
    legacy_hashes: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    diff: str | None = None
    guidance: str | None = None
    version: str | None = None
    suite: str | None = None
    # Transaction preconditions captured at plan time. ``originals`` is the
    # in-process recovery source; a backup file is only an audit record.
    originals: dict[str, bytes | None] = field(default_factory=dict)
    host_identity: tuple[int, int] | None = None
    target_present: bool = False
    target_empty: bool = True
    source_binding: SourceBinding | None = None
    baseline_binding: BaselineBinding | None = None
    worktree_head: str | None = None
    exclusive_writer: str = 'NOT_DECLARED'
    managed_summary: dict = field(default_factory=dict)

    @property
    def writes(self) -> dict[str, bytes]:
        """Host-relative path -> bytes, including the controlled AGENTS.md."""
        payload = {f'{AGENTORCH_DIRNAME}/{rel}': data for rel, data in self.files.items()}
        if self.agents_text is not None:
            payload['AGENTS.md'] = self.agents_text.encode('utf-8')
        return payload


def agentorch_root() -> Path:
    """Stable AgentOrch Root derived from this file, never from the cwd."""
    return Path(__file__).resolve().parents[2]


def lexical_rel_path(rel) -> str:
    """Validate one host-relative path without touching the filesystem."""
    if not isinstance(rel, str) or not rel:
        raise OnboardingError('write path must be a non-empty string')
    if len(rel) > MAX_REL_PATH:
        raise OnboardingError(f'write path is longer than {MAX_REL_PATH} characters')
    if '\\' in rel or rel.startswith('/') or DRIVE_RE.match(rel):
        raise OnboardingError(f'write path must be a relative POSIX path: {rel!r}')
    if not REL_PATH_RE.fullmatch(rel):
        raise OnboardingError(f'write path has an illegal component: {rel!r}')
    if any(part in ('', '.', '..') for part in rel.split('/')):
        raise OnboardingError(f'write path must not contain "." or "..": {rel!r}')
    return rel


def write_set_problems(writes: dict[str, bytes]) -> list[str]:
    """The only legal write set: ``agentorch/**`` plus the root ``AGENTS.md``."""
    problems = []
    for rel, data in writes.items():
        try:
            lexical_rel_path(rel)
        except OnboardingError as exc:
            problems.append(str(exc))
            continue
        if not isinstance(data, (bytes, bytearray)):
            problems.append(f'{rel}: planned content is not bytes')
            continue
        if rel == ALLOWED_ROOT_ENTRY:
            continue
        if rel == f'{AGENTORCH_DIRNAME}/AGENTS.md':
            problems.append('the isolated layout must not contain agentorch/AGENTS.md')
        elif not rel.startswith(f'{AGENTORCH_DIRNAME}/'):
            problems.append(f'write path is outside agentorch/ and the root AGENTS.md: {rel}')
        elif rel == f'{AGENTORCH_DIRNAME}/':
            problems.append('empty agentorch/ write path')
    return problems


def _sha_map(files: dict[str, bytes]) -> dict[str, str]:
    return {rel: sha256_bytes(data) for rel, data in files.items()}


def bind_verified_source(result, plan: OnboardingPlan) -> SourceBinding:
    """Bind a *verified package result* to a concrete plan write set.

    ``result`` must be the return value of :func:`package.verify_package`; a
    local manifest, a plan digest or a caller-constructed hash is not a
    substitute. The resulting object is what :func:`apply_isolated_onboarding`
    requires before touching a host.
    """
    if result is None or not getattr(result, 'ok', False):
        raise OnboardingError('a verified release package result is required')
    checks = {item.get('check'): item for item in getattr(result, 'checks', [])}
    asset = checks.get('ASSET_SHA256') or {}
    if asset.get('result') != 'PASS':
        raise OnboardingError('the release asset digest check did not pass')
    detail = str(asset.get('detail') or '')
    match = re.search(r'sha256=([0-9a-f]{64})', detail)
    if not match:
        raise OnboardingError('the release asset digest is not recorded')
    manifest_bytes = getattr(result, 'manifest_bytes', None)
    if not isinstance(manifest_bytes, (bytes, bytearray)):
        raise OnboardingError('the verified package did not retain its manifest bytes')
    if not plan.version or not plan.suite:
        raise OnboardingError('the plan does not carry a suite / format version')
    planned = _sha_map(dict(plan.files))
    if plan.agents_text is not None:
        planned[ALLOWED_ROOT_ENTRY] = sha256_bytes(plan.agents_text.encode('utf-8'))
    return SourceBinding(
        asset_sha256=match.group(1),
        manifest_sha256=hashlib.sha256(bytes(manifest_bytes)).hexdigest(),
        suite=plan.suite,
        format_version=plan.version,
        source_sha256=_sha_map(dict(getattr(result, 'files', {}) or {})),
        planned_sha256=planned,
        _seal=_SOURCE_BINDING_SEAL,
    )


def _sealed_baseline(binding: BaselineBinding | None) -> bool:
    return isinstance(binding, BaselineBinding) and binding._seal is _SOURCE_BINDING_SEAL


def _binding_matches_plan(binding: SourceBinding | None, plan: OnboardingPlan) -> bool:
    if binding is None or binding._seal is not _SOURCE_BINDING_SEAL:
        return False
    if binding.suite != plan.suite or binding.format_version != plan.version:
        return False
    planned = _sha_map(dict(plan.files))
    if plan.agents_text is not None:
        planned[ALLOWED_ROOT_ENTRY] = sha256_bytes(plan.agents_text.encode('utf-8'))
    return binding.planned_sha256 == planned


def _capture_plan_preconditions(plan: OnboardingPlan) -> None:
    """Record the exact host state a later APPLY must still observe."""
    host = Path(plan.host_root)
    problems: list[str] = []
    try:
        root_stat = host.lstat()
    except OSError as exc:
        plan.status = 'STOPPED_CONFLICT'
        plan.problems = [f'host root cannot be inspected: {exc}']
        return
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        plan.status = 'STOPPED_CONFLICT'
        plan.problems = ['host root is not a regular directory']
        return
    plan.host_identity = (root_stat.st_dev, root_stat.st_ino)

    target = host / AGENTORCH_DIRNAME
    try:
        target_info = target.lstat()
    except FileNotFoundError:
        plan.target_present = False
        plan.target_empty = True
    except OSError as exc:
        problems.append(f'agentorch/ cannot be inspected: {exc}')
        target_info = None
    else:
        if stat.S_ISLNK(target_info.st_mode) or not stat.S_ISDIR(target_info.st_mode):
            problems.append('agentorch/ exists but is not a regular directory')
        else:
            plan.target_present = True
            try:
                plan.target_empty = not any(target.iterdir())
            except OSError as exc:
                problems.append(f'agentorch/ cannot be listed: {exc}')

    originals: dict[str, bytes | None] = {}
    for rel in plan.writes:
        try:
            target_path = safe_project_path(host, rel)
        except UnsafePath as exc:
            problems.append(str(exc))
            continue
        try:
            info = target_path.lstat()
        except FileNotFoundError:
            originals[rel] = None
            continue
        except OSError as exc:
            problems.append(f'{rel}: cannot be inspected: {exc}')
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            problems.append(f'{rel}: target exists but is not a regular file')
            continue
        try:
            originals[rel] = target_path.read_bytes()
        except OSError as exc:
            problems.append(f'{rel}: cannot be read: {exc}')
    plan.originals = originals
    if problems:
        plan.status = 'STOPPED_CONFLICT'
        plan.problems = problems
        plan.files = {}
        plan.agents_text = None


def _installed_matches_release(target: Path, expected: dict[str, bytes]) -> list[str]:
    """Read-only comparison of an installed tree with verified target bytes."""
    target = Path(target)
    problems = []
    expected_paths = set(expected)
    expected_dirs = set()
    for rel in expected_paths:
        parts = rel.split('/')[:-1]
        for index in range(1, len(parts) + 1):
            expected_dirs.add('/'.join(parts[:index]))
    for path in sorted(target.rglob('*')):
        rel = path.relative_to(target).as_posix()
        if any(part in ISOLATED_PRIVATE_DIRS for part in PurePosixPath(rel).parts):
            continue
        if path.is_symlink():
            problems.append(f'agentorch/{rel} is a symlink')
            continue
        if path.is_dir():
            if rel not in expected_dirs:
                problems.append(f'unknown directory inside agentorch/: {rel}')
            continue
        if not path.is_file():
            problems.append(f'unknown non-regular entry inside agentorch/: {rel}')
            continue
        if rel not in expected_paths:
            problems.append(f'unknown file inside agentorch/: {rel}')
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            problems.append(f'cannot read agentorch/{rel}: {exc}')
            continue
        if sha256_bytes(data) != sha256_bytes(expected[rel]):
            problems.append(f'installed file does not match the verified release: {rel}')
    for rel in sorted(expected_paths):
        if not (target / rel).is_file():
            problems.append(f'expected installed file is missing: {rel}')
    return problems


def _installed_projection(source_files: dict[str, bytes], suite: str, version: str,
                          host_root: Path) -> dict[str, bytes]:
    """Project verified release bytes onto the isolated ``agentorch/`` layout."""
    files: dict[str, bytes] = {}
    for rel, data in source_files.items():
        if rel == 'AGENTS.md':
            continue
        files[rel] = _rewrite_host_agents_links(rel, data, Path('.'), host_root)
    files['.gitignore'] = AGENTORCH_GITIGNORE.encode('utf-8')
    projection = dict(files)
    projection['MANIFEST.json'] = _manifest_bytes(files, suite, version)
    return projection


def _installed_baseline_hint(target: Path) -> tuple[dict | None, list[str]]:
    """Read the local manifest only as a *hint* about which release to fetch.

    Nothing returned here authorizes a write. An upgrade still has to fetch the
    official release and match every installed byte against it.
    """
    manifest, problem = _read_installed_manifest(target)
    if manifest is None:
        return None, [problem or _BASELINE_MISSING]
    entries = _manifest_map(manifest)
    format_version = manifest.get('format_version')
    suite = manifest.get('suite')
    problems = []
    if not entries:
        problems.append('the installed MANIFEST.json does not list any files')
    if not isinstance(format_version, str) or not format_version:
        problems.append('the installed MANIFEST.json has no usable format_version')
    if not isinstance(suite, str) or not suite:
        problems.append('the installed MANIFEST.json has no usable suite name')
    if problems:
        return None, problems
    return {'format_version': format_version, 'suite': suite}, []


def bind_verified_baseline(result, target: Path) -> BaselineBinding:
    """Seal a verified old release and its exact projected installed bytes."""
    if result is None or not getattr(result, 'ok', False):
        raise OnboardingError('a verified baseline release package is required')
    checks = {item.get('check'): item for item in getattr(result, 'checks', [])}
    asset = checks.get('ASSET_SHA256') or {}
    if asset.get('result') != 'PASS':
        raise OnboardingError('the baseline release asset digest check did not pass')
    match = re.search(r'sha256=([0-9a-f]{64})', str(asset.get('detail') or ''))
    if not match:
        raise OnboardingError('the baseline release asset digest is not recorded')
    manifest_bytes = getattr(result, 'manifest_bytes', None)
    if not isinstance(manifest_bytes, (bytes, bytearray)):
        raise OnboardingError('the verified baseline did not retain its manifest bytes')
    suite = getattr(result, 'suite', None)
    format_version = getattr(result, 'format_version', None)
    release_tag = getattr(result, 'release_tag', None)
    if not isinstance(suite, str) or not suite:
        raise OnboardingError('the verified baseline does not carry a suite')
    if not isinstance(format_version, str) or not format_version:
        raise OnboardingError('the verified baseline does not carry a format version')
    if not isinstance(release_tag, str) or not re.fullmatch(
            r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?', release_tag):
        raise OnboardingError('the verified baseline does not carry a release tag')
    projection = _installed_projection(dict(getattr(result, 'files', {}) or {}),
                                       suite, format_version, Path(target))
    return BaselineBinding(
        release_tag=release_tag,
        asset_sha256=match.group(1),
        manifest_sha256=hashlib.sha256(bytes(manifest_bytes)).hexdigest(),
        suite=suite,
        format_version=format_version,
        source_sha256=_sha_map(dict(getattr(result, 'files', {}) or {})),
        installed_sha256=_sha_map(projection),
        kind='ISOLATED',
        _seal=_SOURCE_BINDING_SEAL,
    )


def bind_verified_legacy_baseline(result) -> BaselineBinding:
    """Seal a verified historical Release as the flat old-root baseline.

    The old install copied the release members to the host root unchanged, so
    the expected bytes are the release members themselves (plus its exact
    ``MANIFEST.json``), not the isolated projection. As with
    :func:`bind_verified_baseline`, only a successful ``verify_package`` result
    can produce this object; a local MANIFEST.json is never enough.
    """
    if result is None or not getattr(result, 'ok', False):
        raise OnboardingError('a verified historical release package is required')
    checks = {item.get('check'): item for item in getattr(result, 'checks', [])}
    asset = checks.get('ASSET_SHA256') or {}
    if asset.get('result') != 'PASS':
        raise OnboardingError('the historical release asset digest check did not pass')
    match = re.search(r'sha256=([0-9a-f]{64})', str(asset.get('detail') or ''))
    if not match:
        raise OnboardingError('the historical release asset digest is not recorded')
    manifest_bytes = getattr(result, 'manifest_bytes', None)
    if not isinstance(manifest_bytes, (bytes, bytearray)):
        raise OnboardingError('the verified historical release did not retain its manifest bytes')
    suite = getattr(result, 'suite', None)
    format_version = getattr(result, 'format_version', None)
    release_tag = getattr(result, 'release_tag', None)
    if not isinstance(suite, str) or not suite:
        raise OnboardingError('the verified historical release does not carry a suite')
    if not isinstance(format_version, str) or not format_version:
        raise OnboardingError('the verified historical release does not carry a format version')
    if not isinstance(release_tag, str) or not re.fullmatch(
            r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?', release_tag):
        raise OnboardingError('the verified historical release does not carry a release tag')
    files = dict(getattr(result, 'files', {}) or {})
    if not files:
        raise OnboardingError('the verified historical release has no file members')
    # Byte-for-byte, not the line-ending-folded hash: the old install is copied
    # verbatim, so a CRLF rewrite is a local modification and must stop.
    installed = {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}
    installed['MANIFEST.json'] = hashlib.sha256(bytes(manifest_bytes)).hexdigest()
    return BaselineBinding(
        release_tag=release_tag,
        asset_sha256=match.group(1),
        manifest_sha256=hashlib.sha256(bytes(manifest_bytes)).hexdigest(),
        suite=suite,
        format_version=format_version,
        source_sha256=_sha_map(files),
        installed_sha256=installed,
        kind='LEGACY_ROOT',
        _seal=_SOURCE_BINDING_SEAL,
    )


def _baseline_binding_matches(first: BaselineBinding | None,
                              second: BaselineBinding | None) -> bool:
    if first is None or second is None:
        return False
    if first._seal is not _SOURCE_BINDING_SEAL or second._seal is not _SOURCE_BINDING_SEAL:
        return False
    return (first.release_tag == second.release_tag
            and first.asset_sha256 == second.asset_sha256
            and first.manifest_sha256 == second.manifest_sha256
            and first.suite == second.suite
            and first.format_version == second.format_version
            and first.source_sha256 == second.source_sha256
            and first.installed_sha256 == second.installed_sha256
            and first.kind == second.kind)


def installed_tree_matches_baseline(target: Path,
                                    binding: BaselineBinding) -> list[str]:
    """Read-only item-by-item match of ``agentorch/`` against a sealed baseline."""
    target = Path(target)
    expected = dict(binding.installed_sha256)
    problems: list[str] = []
    if binding._seal is not _SOURCE_BINDING_SEAL:
        return ['the baseline binding is not sealed by a verified package result']
    expected_dirs: set[str] = set()
    for rel in expected:
        parts = rel.split('/')[:-1]
        for index in range(1, len(parts) + 1):
            expected_dirs.add('/'.join(parts[:index]))
    for path in sorted(target.rglob('*')):
        rel = path.relative_to(target).as_posix()
        if _is_private_rel(rel):
            continue
        if path.is_symlink():
            problems.append(f'agentorch/{rel} is a symlink')
            continue
        if path.is_dir():
            if rel not in expected_dirs:
                problems.append(f'unknown directory inside agentorch/: {rel}')
            continue
        if not path.is_file():
            problems.append(f'unknown non-regular entry inside agentorch/: {rel}')
            continue
        if rel not in expected:
            problems.append(f'unknown file inside agentorch/: {rel}')
            continue
        try:
            digest = sha256_bytes(path.read_bytes())
        except OSError as exc:
            problems.append(f'cannot read agentorch/{rel}: {exc}')
            continue
        if digest != expected[rel]:
            problems.append(f'installed file does not match the authenticated baseline: {rel}')
    for rel in sorted(expected):
        if not (target / rel).is_file():
            problems.append(f'expected installed file is missing: {rel}')
    return problems


def legacy_root_matches_baseline(host_root, binding: BaselineBinding) -> list[str]:
    """Read-only byte match of the flat old root against a sealed baseline.

    Every release member must sit at its original host-root path with identical
    bytes, and the root ``MANIFEST.json`` must equal the release's manifest. Any
    symlink or non-regular entry is refused before it can be read.
    """
    if binding is None or binding.kind != 'LEGACY_ROOT':
        return ['the supplied baseline is not a sealed legacy-root release']
    if binding._seal is not _SOURCE_BINDING_SEAL:
        return ['the legacy baseline binding is not sealed by a verified package result']
    host = Path(host_root)
    try:
        root_fd = _open_planning_root_fd(host)
    except OnboardingError as exc:
        return [str(exc)]
    if root_fd is None:
        return ['the old root does not exist or is not a directory']
    expected = dict(binding.installed_sha256)
    problems: list[str] = []
    try:
        for rel in sorted(expected):
            try:
                lexical_rel_path(rel)
            except OnboardingError as exc:
                problems.append(f'the historical release lists an unsafe path: {exc}')
                continue
            try:
                data = _read_regular_file_at(root_fd, rel, f'old root {rel}')
            except OnboardingError as exc:
                problems.append(str(exc))
                continue
            if data is None:
                problems.append(f'baseline file is missing from the old install: {rel}')
                continue
            if hashlib.sha256(data).hexdigest() != expected[rel]:
                problems.append(f'local modification in old root install: {rel}')
    finally:
        os.close(root_fd)
    strict_prefixes = {'scripts', 'editions', 'templates'}
    owned = {PurePosixPath(rel).parts[0] for rel in expected
             if len(PurePosixPath(rel).parts) > 1}
    for prefix in sorted(owned & strict_prefixes):
        directory = host / prefix
        if not directory.exists() and not directory.is_symlink():
            continue
        if directory.is_symlink() or not directory.is_dir():
            problems.append(f'old root {prefix}/ is not a regular directory')
            continue
        for path in directory.rglob('*'):
            rel = path.relative_to(host).as_posix()
            if path.is_symlink():
                problems.append(f'symlink inside a kit-owned namespace: {rel}')
                continue
            if path.is_dir():
                continue
            if not path.is_file():
                problems.append(f'non-regular entry inside a kit-owned namespace: {rel}')
                continue
            if rel not in expected:
                problems.append(f'untracked file inside a kit-owned namespace: {rel}')
    return problems


def detect_install_scenario(host_root) -> str:
    """Classify a host directory without guessing: empty vs existing project."""
    root = Path(host_root)
    if not root.is_dir():
        raise OnboardingError(f'host root does not exist or is not a directory: {root}')
    entries = [entry for entry in root.iterdir() if entry.name != '.git']
    return 'new-project' if not entries else 'existing-project'


def looks_like_agentorch_root(path) -> bool:
    """True when *path* carries the kit markers (no scheduler import involved).

    Symlinked or non-regular markers do not qualify: the caller would otherwise
    treat an attacker-controlled link as an installed kit tree.
    """
    path = Path(path)
    return (_is_regular_path(path / 'VERSION.md')
            and _is_regular_path(path / 'docs' / 'orchestration.md'))


def managed_block(version: str) -> str:
    """The one managed block appended to the host root AGENTS.md."""
    body_lines = [
        '# AgentOrch 推进引擎（受管区块）',
        '',
        '本区块由 AgentOrch 安装 / 更新器维护。区块之外的宿主内容由项目所有者管理，',
        'AgentOrch 不覆盖；两者冲突时以宿主所有者的明文规则为准。',
        '',
        '- 职责：AgentOrch 作为本项目的任务 / 审查 / 证据推进引擎，不替代宿主规则。',
        '- AgentOrch Root：`agentorch/`；核心契约：`agentorch/docs/orchestration.md`。',
        '- 内部操作手册：`agentorch/editions/<edition>/master.md`。',
        '- 本机命令：在宿主根运行 `python agentorch/scripts/orchestrator.py --layout isolated '
        '<command>`；',
        '  隔离布局拒绝不带 `--layout isolated` 的裸调用。',
        '- 本机私有数据：`agentorch/.local/`；任务库、配置与备份在 '
        '`agentorch/.local/orchestration/`',
        '  （不入 Git、不入发行包）。',
        '- 升级：只替换本受管区块；重复安装幂等；标记缺失 / 重复 / 不成对或',
        '  区块被手工修改时失败关闭，不覆盖宿主内容。',
    ]
    body = '\n'.join(body_lines) + '\n'
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
    lines = [f'{MANAGED_BEGIN_PREFIX} v{version} -->', *body_lines,
             MANAGED_HASH_TEMPLATE.format(digest), MANAGED_END]
    return '\n'.join(lines) + '\n'


def managed_block_hashes(text: str) -> list[str]:
    return [match.group('sha') for line in text.splitlines()
            for match in [MANAGED_HASH_RE.match(line.strip())] if match]


def _created_host_entry(block: str) -> str:
    return (
        '# Project Agent Rules\n'
        '\n'
        '<!-- Host project rules: owner-managed. Keep them outside the AgentOrch block. -->\n'
        '\n'
        'Add the host project rules here. AgentOrch never rewrites this section.\n'
        '\n'
        f'{block}')


def _append_block(text: str, block: str) -> str:
    if text and not text.endswith('\n'):
        text += '\n'
    if text and not text.endswith('\n\n'):
        text += '\n'
    if not block.endswith('\n'):
        block += '\n'
    return text + block


def _block_diff(text: str, proposed: str) -> str:
    return '\n'.join(difflib.unified_diff(
        text.splitlines(), proposed.splitlines(),
        fromfile='current AGENTS.md', tofile='proposed AGENTS.md', lineterm=''))


def _analyze_agents(text: str) -> dict:
    lines = text.split('\n')
    begins, ends, hashes = [], [], []
    for index, line in enumerate(lines):
        stripped = line.strip()
        begin = MANAGED_BEGIN_RE.match(stripped)
        if begin:
            begins.append((index, begin.group('version')))
        if stripped == MANAGED_END:
            ends.append(index)
        digest = MANAGED_HASH_RE.match(stripped)
        if digest:
            hashes.append((index, digest.group('sha')))
    return {'lines': lines, 'begins': begins, 'ends': ends, 'hashes': hashes}


def _agents_update(text: str | None, block: str) -> dict:
    """Return {'text', 'action', 'created', 'diff'} or raise ManagedBlockError."""
    if text is None:
        proposed = _created_host_entry(block)
        return {'text': proposed, 'action': 'create', 'created': True,
                'diff': _block_diff('', proposed)}
    info = _analyze_agents(text)
    begins, ends, hashes = info['begins'], info['ends'], info['hashes']
    if not begins and not ends:
        proposed = _append_block(text, block)
        return {'text': proposed, 'action': 'extend', 'created': False,
                'diff': _block_diff(text, proposed)}
    if len(begins) > 1:
        proposed = _append_block(text, block)
        raise ManagedBlockError(
            ['root AGENTS.md contains more than one AgentOrch BEGIN marker'],
            diff=_block_diff(text, proposed), conflict_type='DUPLICATE_MARKER')
    if len(ends) > 1:
        proposed = _append_block(text, block)
        raise ManagedBlockError(
            ['root AGENTS.md contains more than one AgentOrch END marker'],
            diff=_block_diff(text, proposed), conflict_type='DUPLICATE_MARKER')
    if not begins:
        proposed = _append_block(text, block)
        raise ManagedBlockError(
            ['root AGENTS.md contains an AgentOrch END marker without a BEGIN marker'],
            diff=_block_diff(text, proposed), conflict_type='MISSING_MARKER')
    if not ends:
        proposed = _append_block(text, block)
        raise ManagedBlockError(
            ['root AGENTS.md AgentOrch BEGIN marker has no END marker'],
            diff=_block_diff(text, proposed), conflict_type='MISSING_MARKER')
    begin_index, version = begins[0]
    end_index = ends[0]
    if end_index < begin_index:
        proposed = _append_block(text, block)
        raise ManagedBlockError(
            ['root AGENTS.md AgentOrch END marker appears before its BEGIN marker'],
            diff=_block_diff(text, proposed), conflict_type='REVERSED_MARKER')
    inner = [item for item in hashes if begin_index < item[0] < end_index]
    if len(inner) != 1:
        proposed = _append_block(text, block)
        raise ManagedBlockError(
            ['root AGENTS.md AgentOrch block must carry exactly one recorded body hash'],
            diff=_block_diff(text, proposed), conflict_type='HASH_MARKER_MISMATCH')
    hash_index, recorded = inner[0]
    if hash_index + 1 != end_index:
        proposed = '\n'.join(info['lines'][:begin_index]
                             + block.rstrip('\n').split('\n')
                             + info['lines'][end_index + 1:])
        raise ManagedBlockError(['unexpected content between managed hash and END marker'],
                                diff=_block_diff(text, proposed),
                                conflict_type='UNEXPECTED_BLOCK_CONTENT')
    body_lines = info['lines'][begin_index + 1:hash_index]
    body = '\n'.join(body_lines) + ('\n' if body_lines else '')
    # A checkout with core.autocrlf may hand us CRLF; the recorded hash is over
    # the LF body, so normalize for the integrity check only (the bytes on disk
    # are left alone when the block is unchanged).
    body_normalized = body.replace('\r\n', '\n')
    actual = hashlib.sha256(body_normalized.encode('utf-8')).hexdigest()
    if actual != recorded:
        proposed = _append_block(text, block)
        raise ManagedBlockError(
            ['root AGENTS.md AgentOrch block was modified outside the installer '
             '(recorded body hash does not match)'],
            diff=_block_diff(text, proposed), conflict_type='BODY_HASH_MISMATCH')
    if not version or body_normalized != '\n'.join(managed_block(version).split('\n')[1:-3]) + '\n':
        proposed = '\n'.join(info['lines'][:begin_index]
                             + block.rstrip('\n').split('\n')
                             + info['lines'][end_index + 1:])
        raise ManagedBlockError(['managed block is not a known canonical block; '
                                 'its self-recorded hash does not prove ownership'],
                                diff=_block_diff(text, proposed),
                                conflict_type='NONCANONICAL_BODY')
    proposed_block = block
    desired_body = '\n'.join(proposed_block.split('\n')[1:-3]) + '\n'
    if body_normalized == desired_body and version == _block_version(proposed_block):
        return {'text': text, 'action': 'unchanged', 'created': False, 'diff': ''}
    new_lines = (info['lines'][:begin_index]
                 + proposed_block.rstrip('\n').split('\n')
                 + info['lines'][end_index + 1:])
    proposed = '\n'.join(new_lines)
    return {'text': proposed, 'action': 'replace', 'created': False,
            'diff': _block_diff(text, proposed)}


def _block_version(block: str) -> str | None:
    match = MANAGED_BEGIN_RE.match(block.splitlines()[0].strip())
    return match.group('version') if match else None


# --- source package ---------------------------------------------------------

def fetch_verified_onboarding_release(tag: str | None = None):
    """Fetch verified official release bytes; this does not authorize writes.

    Only the fixed official client supplies metadata and asset bytes. ``tag`` may
    select the official release to fetch (used to re-authenticate an installed
    baseline); it is a selection hint, never proof. Callers must still bind the
    returned bytes to a plan and validate its host and rollback preconditions
    before any future APPLY can be enabled. Transport failures stop immediately
    rather than retrying until a download succeeds.
    """
    from . import package, sources

    client = sources.GitHubReleaseSource()
    try:
        release = client.fetch_release(tag)
        asset = sources.select_asset(release)
        payload = client.download_asset(asset)
        result = package.verify_package(
            release, payload, expected_suite='Agent-Orchestration Starter Kit')
    except (sources.SourceError, OSError) as exc:
        raise OnboardingError(f'official release unavailable or unsafe: {exc}') from exc
    if not result.ok:
        raise OnboardingError(f'official release verification failed: '
                              f'{result.failed_check}: {result.detail}')
    return result


_BASELINE_TAG_CANDIDATES = ('{format}', '{format}.0')
_BASELINE_MISSING = (
    'the installed agentorch/ has no readable MANIFEST.json; an upgrade cannot '
    'establish an authenticated old baseline and writes nothing')


def _baseline_tag_candidates(format_version: str) -> list[str]:
    tags = []
    for template in _BASELINE_TAG_CANDIDATES:
        tag = 'v' + template.format(format=format_version)
        if tag not in tags:
            tags.append(tag)
    return tags


def fetch_verified_onboarding_baseline(host_root):
    """Find and verify the official release the installed tree came from.

    The installed ``MANIFEST.json`` is used only to suggest a release tag. The
    returned object is a *verified package result* whose bytes must match the
    installed tree exactly; a local manifest, self-signed hash or caller digest
    can never authenticate the baseline on its own. Patch release tags that do
    not equal ``v<format>`` / ``v<format>.0`` are not recoverable from the local
    manifest, so such baselines stop rather than guessing.
    """
    host = Path(host_root)
    target = host / AGENTORCH_DIRNAME
    hint, hint_problems = _installed_baseline_hint(target)
    if hint is None:
        raise OnboardingError('; '.join(hint_problems) or _BASELINE_MISSING)
    failures: list[str] = []
    for tag in _baseline_tag_candidates(hint['format_version']):
        try:
            result = fetch_verified_onboarding_release(tag)
        except OnboardingError as exc:
            failures.append(str(exc))
            continue
        if (result.suite != hint['suite']
                or result.format_version != hint['format_version']):
            failures.append(f'official release {tag} does not match the installed '
                            f'manifest suite / format')
            continue
        binding = bind_verified_baseline(result, target)
        mismatches = installed_tree_matches_baseline(target, binding)
        if mismatches:
            failures.extend(mismatches[:5])
            continue
        return result
    detail = failures[-1] if failures else _BASELINE_MISSING
    raise OnboardingError(f'the installed baseline cannot be authenticated: {detail}')


def _legacy_baseline_hint(host_root: Path) -> tuple[dict | None, list[str]]:
    """Root MANIFEST.json as a *hint* only, never as ownership proof."""
    manifest, problem = _read_installed_manifest(host_root)
    if manifest is None:
        return None, [problem or ('no root MANIFEST.json: the old install has no '
                                  'trusted baseline')]
    entries = _manifest_map(manifest)
    format_version = manifest.get('format_version')
    suite = manifest.get('suite')
    problems = []
    if not entries:
        problems.append('root MANIFEST.json does not list any files')
    if not isinstance(format_version, str) or not format_version:
        problems.append('root MANIFEST.json has no usable format_version')
    if not isinstance(suite, str) or not suite:
        problems.append('root MANIFEST.json has no usable suite name')
    if problems:
        return None, problems
    return {'format_version': format_version, 'suite': suite}, []


def fetch_verified_onboarding_legacy_baseline(host_root):
    """Find and verify the official Release the flat old root came from.

    The root ``MANIFEST.json`` only suggests candidate tags. The returned object
    must be a verified package whose members and manifest equal the old root
    byte for byte; a local manifest or hash never authenticates migration.
    """
    host = Path(host_root)
    hint, hint_problems = _legacy_baseline_hint(host)
    if hint is None:
        raise OnboardingError('; '.join(hint_problems))
    failures: list[str] = []
    for tag in _baseline_tag_candidates(hint['format_version']):
        try:
            result = fetch_verified_onboarding_release(tag)
        except OnboardingError as exc:
            failures.append(str(exc))
            continue
        if (result.suite != hint['suite']
                or result.format_version != hint['format_version']):
            failures.append(f'official release {tag} does not match the old root '
                            f'manifest suite / format')
            continue
        try:
            binding = bind_verified_legacy_baseline(result)
        except OnboardingError as exc:
            failures.append(str(exc))
            continue
        mismatches = legacy_root_matches_baseline(host, binding)
        if mismatches:
            failures.extend(mismatches[:5])
            continue
        return result
    detail = failures[-1] if failures else 'no candidate historical release matched'
    raise OnboardingError(f'the old root baseline cannot be authenticated: {detail}')


def _read_version(source_root: Path) -> tuple[str, str]:
    path = source_root / 'VERSION.md'
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise OnboardingError(f'cannot read VERSION.md: {exc}') from exc
    suite, version = '', ''
    for raw in text.splitlines():
        line = raw.strip().lstrip('#').strip()
        if line.startswith('- '):
            line = line[2:].strip()
        match = re.match(r'(?i)^format\s+version\s*[:：]\s*(\S+)$', line)
        if match:
            version = match.group(1)
        elif not suite and line and ':' not in line:
            suite = line
    if not suite or not version:
        raise OnboardingError('VERSION.md must declare the suite name and Format version')
    return suite, version


def _required_paths(source_root: Path) -> list[str]:
    validator = source_root / 'scripts' / 'validate_kit.py'
    try:
        text = validator.read_text(encoding='utf-8')
    except OSError as exc:
        raise OnboardingError(f'cannot read scripts/validate_kit.py: {exc}') from exc
    match = SOURCE_PATH_RE.search(text)
    if not match:
        raise OnboardingError('cannot locate the REQUIRED list in scripts/validate_kit.py')
    items = re.findall(r"'([^']+)'|\"([^\"]+)\"", match.group(1))
    paths = [single or double for single, double in items]
    if not paths:
        raise OnboardingError('the REQUIRED list is empty')
    return paths


def _category(path: str) -> str:
    if path.startswith('editions/'):
        return 'edition'
    if path.startswith('templates/'):
        return 'template'
    if path in ('ORCHESTRATION-PROJECT.md', 'docs/PROJECT.md', 'PLAN.md',
                'docs/PLAN.md', 'DESIGN.md', 'docs/DESIGN.md'):
        return 'project-profile'
    return 'core'


def _manifest_bytes(files: dict[str, bytes], suite: str, version: str) -> bytes:
    entries = []
    for path in sorted(files):
        data = normalize_line_endings(files[path])
        entries.append({
            'path': path,
            'category': _category(path),
            'sha256': sha256_bytes(data),
            'size': len(data),
        })
    payload = {'schema_version': 1, 'suite': suite, 'format_version': version,
               'files': entries}
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
            + '\n').encode('utf-8')


def _read_source(source_root: Path) -> tuple[dict[str, bytes], dict, str, str]:
    manifest_path = source_root / 'MANIFEST.json'
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise OnboardingError(f'source MANIFEST.json is unreadable: {exc}') from exc
        if not isinstance(manifest, dict):
            raise OnboardingError('source MANIFEST.json must be a JSON object')
        files = {}
        for entry in manifest.get('files') or []:
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
                raise OnboardingError('source MANIFEST.json has a malformed file entry')
            rel = entry['path']
            parts = rel.split('/')
            if (not rel or not re.fullmatch(r'[A-Za-z0-9._/-]+', rel)
                    or any(part in ('', '.', '..') for part in parts)):
                raise OnboardingError(f'unsafe source manifest path: {rel!r}')
            try:
                data = safe_project_path(source_root, rel).read_bytes()
            except UnsafePath as exc:
                raise OnboardingError(f'unsafe source manifest path: {rel!r}') from exc
            except OSError as exc:
                raise OnboardingError(f'source tree is missing {rel}: {exc}') from exc
            if sha256_bytes(data) != entry.get('sha256') or len(normalize_line_endings(data)) != entry.get('size'):
                raise OnboardingError(f'source tree bytes do not match MANIFEST.json: {rel}')
            files[rel] = data
        return (files, manifest, manifest['format_version'], manifest['suite'])
    suite, version = _read_version(source_root)
    files = {}
    for rel in _required_paths(source_root):
        try:
            files[rel] = (source_root / rel).read_bytes()
        except OSError as exc:
            raise OnboardingError(f'source tree is missing required file {rel}: {exc}') from exc
    return files, {'schema_version': 1, 'suite': suite, 'format_version': version}, version, suite


def _rewrite_host_agents_links(rel: str, data: bytes, source_root: Path,
                               host_root: Path) -> bytes:
    """Point links to the kit root AGENTS.md at the host root AGENTS.md.

    The isolated layout deliberately has no ``agentorch/AGENTS.md``, so every
    shipped link that used to resolve to the kit root contract must be recomputed
    from the installed file depth (design §6.3).
    """
    if not is_text(data):
        return data
    text = data.decode('utf-8')
    source_parent = PurePosixPath(rel).parent.as_posix()

    def replace(match):
        target = match.group('target')
        if target.startswith(('http://', 'https://', 'mailto:', '#', '/')):
            return match.group(0)
        path_part, separator, anchor = target.partition('#')
        if not path_part:
            return match.group(0)
        if posixpath.normpath(posixpath.join(source_parent, path_part)) != 'AGENTS.md':
            return match.group(0)
        new_target = posixpath.relpath('../AGENTS.md', start=source_parent)
        title = match.group('title') or ''
        return f']({new_target}{separator}{anchor}{title})'

    return MARKDOWN_LINK_RE.sub(replace, text).encode('utf-8')


# --- baseline checks --------------------------------------------------------

def _open_planning_root_fd(root: Path) -> int | None:
    """Open a planning root as a directory descriptor, or ``None`` if absent.

    A symlinked root is refused here: the descriptor is the only anchor the
    component walk below trusts. A platform without ``O_DIRECTORY`` /
    ``O_NOFOLLOW`` cannot give that anchor, so the read fails closed rather than
    walking a path that a concurrent swap could redirect.
    """
    path = Path(root)
    if _MISSING_OPEN_FLAGS:
        raise OnboardingError(
            f'{path.name} cannot be inspected safely: the platform lacks '
            f'{", ".join(_MISSING_OPEN_FLAGS)}')
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        return os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise OnboardingError(f'{path.name} is not a regular directory') from exc
        raise OnboardingError(f'{path.name} cannot be opened safely: {exc}') from exc


def _read_regular_file_at(root_fd: int, rel: str, label: str) -> bytes | None:
    """Read *rel* by opening each component below *root_fd* with ``O_NOFOLLOW``.

    A parent symlink is therefore rejected by the open itself, not by an earlier
    ``lstat`` pass over the whole path that a concurrent swap could invalidate.
    The leaf is opened non-blocking and proved regular with ``fstat`` before any
    bytes are read. ``O_NONBLOCK`` matters because ``open(O_RDONLY)`` on a FIFO
    waits for a writer: without it, a manifest-listed FIFO would stall planning
    before the type check could reject it. A missing path returns ``None``.
    """
    lexical_rel_path(rel)
    parts = rel.split('/')
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            try:
                next_fd = os.open(part, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW
                                  | _O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                return None
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise OnboardingError(
                        f'{label} passes through a symlink: {part}') from exc
                if exc.errno == errno.ENOTDIR:
                    raise OnboardingError(
                        f'{label} has a non-directory or symlinked parent: {part}') from exc
                raise OnboardingError(f'{label} cannot be inspected: {exc}') from exc
            os.close(fd)
            fd = next_fd
        try:
            descriptor = os.open(parts[-1], os.O_RDONLY | _O_NOFOLLOW
                                 | os.O_NONBLOCK | _O_CLOEXEC,
                                 dir_fd=fd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise OnboardingError(
                    f'{label} is not a regular file; refusing to read it') from exc
            raise OnboardingError(f'{label} cannot be opened safely: {exc}') from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise OnboardingError(f'{label} is not a regular file; refusing to read it')
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(descriptor)
        return b''.join(chunks)
    finally:
        os.close(fd)


def _read_regular_file(path, label: str) -> bytes | None:
    """Read a file only after proving it is a regular, non-symlink inode.

    Candidate baselines are attacker-controlled input. ``is_file()`` follows
    symlinks, so a link inside the host could make a diagnostic read an external
    file. This helper lstat's first and then opens without following a link: with
    ``O_NOFOLLOW`` the kernel refuses it, and without that flag the opened inode
    is compared with the inspected one (see :func:`open_no_follow`). A link or
    any non-regular entry raises :class:`OnboardingError` *before* any bytes are
    read. A missing path returns ``None``.
    """
    path = Path(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OnboardingError(f'{label} cannot be inspected: {exc}') from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OnboardingError(f'{label} is not a regular file; refusing to read it')
    try:
        descriptor = open_no_follow(path, os.O_RDONLY | _O_CLOEXEC, label=label)
    except OSError as exc:
        raise OnboardingError(f'{label} cannot be opened safely: {exc}') from exc
    chunks: list[bytes] = []
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode):
            raise OnboardingError(f'{label} is not a regular file; refusing to read it')
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return b''.join(chunks)


def _is_regular_path(path) -> bool:
    """True only for a regular file that is not reached through a symlink."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _is_directory_path(path) -> bool:
    """True only for a real directory that is not a symlink."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _read_installed_manifest_at(root_fd: int, root: Path) -> tuple[dict | None, str | None]:
    """Read one root's MANIFEST.json through an already-open directory descriptor."""
    root = Path(root)
    label = f'{root.name}/MANIFEST.json'
    try:
        data = _read_regular_file_at(root_fd, 'MANIFEST.json', label)
    except OnboardingError as exc:
        return None, str(exc)
    if data is None:
        return None, None
    try:
        payload = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None, None
    return (payload, None) if isinstance(payload, dict) else (None, None)


def _read_installed_manifest(root: Path) -> tuple[dict | None, str | None]:
    """Read the installed MANIFEST.json as an untrusted object.

    A missing file, unreadable bytes, invalid JSON and any non-object JSON value
    return ``(None, None)``: the caller must then treat the baseline as unknown
    and stop instead of calling ``.get`` on attacker-controlled data. A symlink
    or non-regular entry returns ``(None, <problem>)`` without reading it.
    """
    root = Path(root)
    try:
        root_fd = _open_planning_root_fd(root)
    except OnboardingError as exc:
        return None, str(exc)
    if root_fd is None:
        return None, None
    try:
        return _read_installed_manifest_at(root_fd, root)
    finally:
        os.close(root_fd)


def _manifest_map(manifest: dict | None) -> dict[str, dict]:
    """Path -> entry for a local manifest; malformed shapes yield no entries."""
    if not isinstance(manifest, dict):
        return {}
    entries = manifest.get('files')
    if not isinstance(entries, list):
        return {}
    result = {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get('path'), str):
            result[entry['path']] = entry
    return result


def _is_private_rel(rel: str) -> bool:
    return any(part in ISOLATED_PRIVATE_DIRS for part in PurePosixPath(rel).parts)


def _check_isolated_upgrade(target: Path) -> tuple[list[str], dict[str, bytes], set[str]]:
    """Read-only diagnostic for an installed tree; never an authorization.

    A local manifest never proves ownership or write rights. This helper is
    retained for report/diagnostic text; the upgrade write set is rebuilt from a
    *verified* official baseline and the exact installed bytes.
    """
    # Problems, current bytes (relative) and paths whose bytes match the local
    # manifest. The last set is not a write allow-list by itself.
    manifest, manifest_problem = _read_installed_manifest(target)
    if manifest is None:
        return ([manifest_problem or 'agentorch/MANIFEST.json is missing or unreadable; '
                 'the installed baseline is unknown'], {}, set())
    recorded = _manifest_map(manifest)
    if not recorded:
        return (['agentorch/MANIFEST.json does not list any files'], {}, set())
    problems: list[str] = []
    current: dict[str, bytes] = {}
    rewritable: set[str] = set()
    for rel, entry in recorded.items():
        try:
            lexical_rel_path(rel)
        except OnboardingError as exc:
            problems.append(f'installed MANIFEST.json lists an unsafe path: {exc}')
            continue
        path = target / rel
        try:
            data = _read_regular_file(path, f'agentorch/{rel}')
        except OnboardingError as exc:
            problems.append(str(exc))
            continue
        if data is None:
            problems.append(f'installed AgentOrch file is missing: {rel}')
            continue
        current[rel] = data
        if sha256_bytes(data) != entry.get('sha256'):
            problems.append(f'local modification in AgentOrch-managed file: {rel}')
        else:
            rewritable.add(rel)
    for path in target.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(target).as_posix()
        if rel == 'MANIFEST.json' or _is_private_rel(rel):
            continue
        if rel not in recorded:
            problems.append(f'unknown file inside agentorch/: {rel}')
    return problems, current, rewritable


def _looks_like_legacy_root(host_root: Path) -> bool:
    """True when the host root still carries an old flat AgentOrch install.

    Only real regular files / directories count as markers: a symlinked marker
    must not switch the host into legacy migration diagnostics, where the check
    would be tempted to follow attacker-controlled links.
    """
    root = Path(host_root)
    return (_is_regular_path(root / 'VERSION.md')
            and (_is_regular_path(root / 'docs' / 'orchestration.md')
                 or _is_regular_path(root / 'kit' / 'orchestration.md'))
            and (_is_directory_path(root / 'editions')
                 or _is_directory_path(root / 'templates')))


def _preserve_legacy_agents(plan: OnboardingPlan) -> None:
    """A preserved old root keeps its own AGENTS.md byte for byte.

    Migration installs ``agentorch/`` only. A later run sees that target and
    would otherwise enter the normal UPGRADE branch and append the managed
    block to the old-root AGENTS.md, silently changing a file the migration
    promised to preserve.
    """
    plan.agents_text = None
    plan.agents_action = 'unchanged'
    plan.agents_created = False
    plan.notes.append('a preserved legacy root is present; its root AGENTS.md '
                      'is never rewritten')


def _check_legacy_root(host_root: Path) -> tuple[list[str], dict[str, dict], list[str]]:
    """Problems, baseline entries and the proven kit-owned paths to remove."""
    root = Path(host_root)
    try:
        root_fd = _open_planning_root_fd(root)
    except OnboardingError as exc:
        return ([str(exc), 'the old install cannot be inspected; migration must stop '
                 'and be reviewed manually'], {}, [])
    if root_fd is None:
        return (['no root MANIFEST.json: the old install has no trusted '
                 'baseline; migration must stop and be reviewed manually'], {}, [])
    try:
        return _check_legacy_root_at(root_fd, root)
    finally:
        os.close(root_fd)


def _check_legacy_root_at(
        root_fd: int, host_root: Path) -> tuple[list[str], dict[str, dict], list[str]]:
    """Inspect an old root through its already-open directory descriptor."""
    baseline, manifest_problem = _read_installed_manifest_at(root_fd, host_root)
    if baseline is None:
        problems = [manifest_problem] if manifest_problem else []
        problems.append('no root MANIFEST.json: the old install has no trusted '
                        'baseline; migration must stop and be reviewed manually')
        return (problems, {}, [])
    entries = _manifest_map(baseline)
    if not entries:
        return (['root MANIFEST.json does not list any files; the baseline is unusable'], {}, [])
    problems: list[str] = []
    removals: list[str] = []
    for rel, entry in entries.items():
        try:
            lexical_rel_path(rel)
        except OnboardingError as exc:
            problems.append(f'root MANIFEST.json lists an unsafe path: {exc}')
            continue
        try:
            data = _read_regular_file_at(root_fd, rel, f'old root {rel}')
        except OnboardingError as exc:
            problems.append(str(exc))
            continue
        if data is None:
            problems.append(f'baseline file is missing from the old install: {rel}')
            continue
        if sha256_bytes(data) != entry.get('sha256'):
            problems.append(f'local modification in old root install: {rel}')
            continue
        if entry.get('category') in ('core', 'edition', 'template') \
                and rel not in ('AGENTS.md', 'MANIFEST.json'):
            removals.append(rel)
    # Unknown files under namespaces the manifest exclusively owns are ambiguous:
    # the installer cannot prove they belong to the kit, so it stops. `docs/` and
    # `tests/` are shared with the host project by design and are only read.
    strict_prefixes = {'scripts', 'editions', 'templates'}
    owned_prefixes = {PurePosixPath(rel).parts[0] for rel in entries
                      if len(PurePosixPath(rel).parts) > 1}
    for prefix in sorted(owned_prefixes & strict_prefixes):
        directory = host_root / prefix
        if not directory.exists() and not directory.is_symlink():
            continue
        if directory.is_symlink() or not directory.is_dir():
            problems.append(f'old root {prefix}/ is not a regular directory')
            continue
        for path in directory.rglob('*'):
            rel = path.relative_to(host_root).as_posix()
            if path.is_symlink():
                problems.append(f'symlink inside a kit-owned namespace: {rel}')
                continue
            if path.is_dir():
                continue
            if not path.is_file():
                problems.append(f'non-regular entry inside a kit-owned namespace: {rel}')
                continue
            if rel not in entries:
                problems.append(f'untracked file inside a kit-owned namespace: {rel}')
    return problems, entries, removals


# --- planning ---------------------------------------------------------------

def plan_isolated_onboarding(host_root, *, source_root=None, scenario=None,
                             now: datetime | None = None,
                             verified_source=None,
                             verified_baseline=None,
                             _source_data=None) -> OnboardingPlan:
    """Plan an isolated install, same-source upgrade or current-tree check. Never writes.

    ``verified_source`` is the only accepted origin for a writable plan; it must
    be a successful :func:`package.verify_package` result. ``verified_baseline``
    is the optional *authenticated* old release for an upgrade: it must also be a
    verified package result, and the installed tree must match it item by item.
    A local manifest or self-signed hash is never accepted in its place.
    ``_source_data`` is a read-only planning input kept for tests and diagnostics
    and never becomes write authorization.
    """
    host = Path(host_root)
    if not host.is_dir():
        raise OnboardingError(f'host root does not exist or is not a directory: {host}')
    host = host.resolve()
    source = Path(source_root) if source_root is not None else agentorch_root()
    source = source.resolve()
    if not source.is_dir():
        raise OnboardingError(f'source root does not exist or is not a directory: {source}')
    requested = scenario or 'isolated'
    if requested not in ('isolated', 'new-project'):
        raise OnboardingError(f'scenario must be isolated or new-project, got {requested!r}')

    if verified_source is not None:
        if not getattr(verified_source, 'ok', False):
            raise OnboardingError('the supplied source package is not verified')
        source_files = dict(verified_source.files)
        version = verified_source.format_version
        suite = verified_source.suite
        source_manifest = verified_source.manifest
    elif _source_data is not None:
        source_files, source_manifest, version, suite = _source_data
    else:
        source_files, source_manifest, version, suite = _read_source(source)
    source_files = dict(source_files)

    detected = detect_install_scenario(host)
    target = host / AGENTORCH_DIRNAME
    plan = OnboardingPlan(host_root=host, agentorch_root=target, source_root=source,
                          scenario=detected, mode='INSTALL')
    plan.version = version
    plan.suite = suite

    # Build the installed file set once, rewriting links to the kit root
    # AGENTS.md. This set is the only content a later APPLY may write.
    expected_installed = _installed_projection(source_files, suite, version, host)
    target_files = {rel: data for rel, data in expected_installed.items()
                    if rel != 'MANIFEST.json'}
    plan.manifest_bytes = expected_installed['MANIFEST.json']

    if requested == 'new-project':
        if detected == 'existing-project':
            plan.status = 'STOPPED_SCENARIO'
            plan.problems = ['this directory already contains host files; the empty/new '
                             'project install mode must not be applied to an existing project']
            plan.guidance = ('已有项目请使用隔离接入模式（--scenario isolated）；只有空目录才按'
                             '新项目发行包流程安装。安装器没有写入任何文件。')
            return plan
        plan.status = 'NEW_PROJECT_MODE'
        plan.notes.append('empty directory: keep using the existing new-project release '
                          'unzip workflow; the isolated installer did not write anything')
        return plan
    if detected == 'new-project':
        plan.status = 'STOPPED_SCENARIO'
        plan.problems = ['the host directory is empty, so it is not an existing project']
        plan.guidance = ('空目录请按新项目方式解压发行包；隔离接入模式只用于已有项目，'
                         '以免把旧项目布局误用到空目录。安装器没有写入任何文件。')
        return plan

    # Whether the host still carries a preserved flat old root. Once migration
    # installed agentorch/, a later run enters UPGRADE, but the old-root
    # AGENTS.md must stay byte-identical: migration never owns that file.
    legacy_root_present = _looks_like_legacy_root(host)

    if target.is_symlink() or (target.exists() and not target.is_dir()):
        plan.status = 'STOPPED_CONFLICT'
        plan.problems = ['agentorch/ exists but is not a regular directory; refusing to write']
        plan.guidance = MANAGED_GUIDANCE
        return plan

    target_entries = list(target.iterdir()) if target.is_dir() else []
    if target.is_dir() and target_entries:
        if not looks_like_agentorch_root(target):
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = [f'agentorch/ already exists and is not an AgentOrch install; '
                             f'conflicting entries: {", ".join(sorted(p.name for p in target_entries)[:8])}']
            plan.guidance = ('默认不覆盖已有目录。请先人工确认 agentorch/ 内是否为用户数据，'
                             '备份或移走后重新运行；安装器没有写入任何文件。')
            return plan
        plan.mode = 'UPGRADE'
        # A tree that already equals the verified target still needs a decision
        # when the host managed block is stale. A pure no-op needs no baseline
        # authorization; a block-only write is authorized by the same verified
        # release bytes the installed tree already matches, otherwise PLAN would
        # say PLANNED while APPLY refused for a missing baseline.
        if not _installed_matches_release(target, expected_installed):
            plan.files = {}
            plan.notes.append('agentorch/ already matches the verified release')
            if legacy_root_present:
                _preserve_legacy_agents(plan)
            else:
                update = _agents_update_for_host(host, plan, managed_block(version))
                if isinstance(update, ManagedBlockError):
                    plan.status = 'STOPPED_CONFLICT'
                    plan.problems = update.problems
                    plan.diff = update.diff
                    plan.guidance = update.guidance
                    return plan
                plan.agents_text = update['text'] if update['action'] != 'unchanged' else None
                plan.agents_action = update['action']
                plan.agents_created = update['created']
                plan.diff = update['diff'] or None
                if plan.agents_text is None:
                    plan.notes.append('the root AGENTS.md managed block is already current')
                elif verified_source is not None:
                    try:
                        binding = bind_verified_baseline(verified_source, target)
                        obstacles = installed_tree_matches_baseline(target, binding)
                        if obstacles:
                            raise OnboardingError('; '.join(obstacles[:3]))
                    except OnboardingError as exc:
                        plan.status = 'STOPPED_CONFLICT'
                        plan.problems = [str(exc), 'nothing was written']
                        plan.agents_text = None
                        return plan
                    plan.baseline_binding = binding
                    plan.notes.append('the installed tree matches the verified release, so the same '
                                      'verified bytes authenticate the block-only baseline')
                else:
                    plan.notes.append('the managed block is a preview only: no verified release was '
                                      'supplied, so no write is authorized')
            plan.status = 'PLANNED'
            _finish_plan(plan, verified_source)
            return plan

        # Not a no-op. Nothing may be written unless an authenticated old
        # baseline was supplied and the installed tree matches it item by item.
        diagnostic, _current, _rewritable = _check_isolated_upgrade(target)
        hint, hint_problems = _installed_baseline_hint(target)
        if verified_baseline is None:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = (list(diagnostic) + list(hint_problems) + [
                'no authenticated old baseline was supplied; a local MANIFEST.json or '
                'self-signed hash cannot authorize an upgrade, and nothing was written'])
            plan.guidance = ('升级必须先由安装器自行获取并校验旧版官方 Release，再与已安装文件'
                             '逐项比对；本地清单与哈希不能单独授权写入。安装器没有写入任何文件。')
            return plan
        if not getattr(verified_baseline, 'ok', False):
            raise OnboardingError('the supplied baseline package is not verified')
        try:
            baseline_binding = bind_verified_baseline(verified_baseline, target)
        except OnboardingError as exc:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = [str(exc), 'nothing was written']
            return plan
        if hint is None:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = (list(hint_problems) + list(diagnostic)[:4] + [
                'the installed manifest cannot identify a baseline to verify; nothing was written'])
            plan.guidance = MANAGED_GUIDANCE
            return plan
        if (hint['suite'] != baseline_binding.suite
                or hint['format_version'] != baseline_binding.format_version):
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = (list(diagnostic)[:4] + [
                'the installed MANIFEST.json does not match the authenticated baseline; '
                'a local manifest cannot authorize an upgrade, and nothing was written'])
            plan.guidance = MANAGED_GUIDANCE
            return plan
        baseline_mismatch = installed_tree_matches_baseline(target, baseline_binding)
        if baseline_mismatch:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = baseline_mismatch[:8]
            plan.guidance = ('已安装的 agentorch/ 与已认证的旧版官方 Release 不能逐文件对上；'
                             '本地修改或清单伪造一律停止，安装器没有写入任何文件。')
            return plan

        from . import versions
        target_release = getattr(verified_source, 'version', None) if verified_source else None
        baseline_release = getattr(verified_baseline, 'version', None)
        target_key = versions.parse_version(target_release or version)
        baseline_key = versions.parse_version(baseline_release or baseline_binding.format_version)
        if target_key is None or baseline_key is None:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = ['the target or baseline version is not comparable; nothing was written']
            return plan
        if target_key < baseline_key:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = [f'downgrade is not supported: target {target_key} < '
                             f'installed baseline {baseline_key}; nothing was written']
            return plan
        if target_key == baseline_key:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = ['the target release is not newer than the authenticated '
                             'baseline but its bytes differ; refusing an ambiguous rewrite']
            plan.guidance = MANAGED_GUIDANCE
            return plan
        if suite != baseline_binding.suite:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = ['target suite differs from the authenticated baseline; '
                             'nothing was written']
            return plan
        removed = sorted(set(baseline_binding.installed_sha256) - set(expected_installed))
        if removed:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = ([f'the target release would remove installed files: {rel}'
                              for rel in removed[:8]] + [
                'deletion is not authorized by this upgrade slice; nothing was written'])
            plan.guidance = ('本轮升级不支持删除已安装文件；请由项目所有者人工核对后处理，'
                             '安装器没有写入或删除任何文件。')
            return plan

        if legacy_root_present:
            _preserve_legacy_agents(plan)
        else:
            update = _agents_update_for_host(host, plan, managed_block(version))
            if isinstance(update, ManagedBlockError):
                plan.status = 'STOPPED_CONFLICT'
                plan.problems = update.problems
                plan.diff = update.diff
                plan.guidance = update.guidance
                return plan
            plan.agents_text = update['text'] if update['action'] != 'unchanged' else None
            plan.agents_action = update['action']
            plan.agents_created = update['created']
            plan.diff = update['diff'] or None
        plan.files = {}
        for rel, data in expected_installed.items():
            path = target / rel
            current = path.read_bytes() if path.is_file() else None
            if current is None or sha256_bytes(current) != sha256_bytes(data):
                plan.files[rel] = data
        plan.manifest_bytes = expected_installed['MANIFEST.json']
        plan.baseline_binding = baseline_binding
        plan.notes.append('upgrade authorized only by a re-verified official baseline '
                          'and the exact installed tree')
        if not plan.files:
            plan.notes.append('all installed bytes already equal the verified release')
        plan.status = 'PLANNED'
        _finish_plan(plan, verified_source)
        return plan

    if _looks_like_legacy_root(host):
        plan.mode = 'MIGRATE'
        # Diagnosis only. A local MANIFEST and matching hashes cannot establish
        # ownership, so nothing here ever schedules a deletion or move.
        problems, _entries, _removals = _check_legacy_root(host)
        plan.legacy_removals = []
        if verified_baseline is None:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = list(problems) + [
                'legacy root files are kept; their ownership cannot be independently verified '
                'and no authenticated historical release was supplied']
            plan.guidance = ('请保留所有旧根文件与宿主内容，交由项目所有者依据官方历史 Release '
                             '人工核对；安装器没有写入或删除任何文件。')
            return plan
        if not getattr(verified_baseline, 'ok', False):
            raise OnboardingError('the supplied historical baseline package is not verified')
        try:
            legacy_binding = bind_verified_legacy_baseline(verified_baseline)
        except OnboardingError as exc:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = [str(exc), 'nothing was written']
            return plan
        mismatch = legacy_root_matches_baseline(host, legacy_binding)
        if mismatch:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = mismatch[:8] + [
                'the old root does not match the re-verified historical release '
                'byte for byte; nothing was written']
            plan.guidance = ('旧根文件必须与官方历史 Release 逐字节一致才允许安装新布局；'
                             '本地修改或缺失一律停止，安装器没有写入、移动或删除任何文件。')
            return plan
        if suite != legacy_binding.suite:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = ['the target suite differs from the historical release; '
                             'nothing was written']
            return plan
        from . import versions
        target_release = getattr(verified_source, 'version', None) if verified_source else None
        target_key = versions.parse_version(target_release or version)
        baseline_key = versions.parse_version(
            getattr(verified_baseline, 'version', None) or legacy_binding.format_version)
        if target_key is None or baseline_key is None:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = ['the target or historical version is not comparable; '
                             'nothing was written']
            return plan
        if target_key < baseline_key:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = [f'downgrade is not supported: target {target_key} < '
                             f'historical baseline {baseline_key}; nothing was written']
            return plan
        # The old root is preserved literally: only agentorch/ is added, and the
        # root AGENTS.md is left untouched because it is itself an old-root file.
        plan.files = dict(target_files)
        plan.files['MANIFEST.json'] = plan.manifest_bytes
        plan.agents_text = None
        plan.agents_action = 'unchanged'
        plan.agents_created = False
        plan.baseline_binding = legacy_binding
        plan.notes.append('migration installs agentorch/ only; every old-root path and '
                          'byte is preserved, including the root AGENTS.md')
        plan.status = 'PLANNED'
        _finish_plan(plan, verified_source)
        return plan

    update = _agents_update_for_host(host, plan, managed_block(version))
    if isinstance(update, ManagedBlockError):
        plan.status = 'STOPPED_CONFLICT'
        plan.problems = update.problems
        plan.diff = update.diff
        plan.guidance = update.guidance
        return plan
    plan.agents_text = update['text'] if update['action'] != 'unchanged' else None
    plan.agents_action = update['action']
    plan.agents_created = update['created']
    plan.diff = update['diff'] or None
    plan.files = dict(target_files)
    plan.files['MANIFEST.json'] = plan.manifest_bytes
    plan.status = 'PLANNED'
    _finish_plan(plan, verified_source)
    return plan


def _read_host_agents(host: Path, plan: OnboardingPlan):
    """Current root AGENTS.md text, or None; records a conflict on unsafe shapes."""
    agents_path = host / 'AGENTS.md'
    if not agents_path.exists() and not agents_path.is_symlink():
        return None
    if agents_path.is_symlink() or not agents_path.is_file():
        raise ManagedBlockError(['root AGENTS.md is not a regular file; refusing to write'],
                                guidance=MANAGED_GUIDANCE)
    try:
        return agents_path.read_bytes().decode('utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        raise ManagedBlockError([f'root AGENTS.md cannot be read as UTF-8: {exc}'],
                                guidance=MANAGED_GUIDANCE) from exc


def _agents_update_for_host(host: Path, plan: OnboardingPlan, block: str):
    try:
        text = _read_host_agents(host, plan)
        if text is not None:
            info = _analyze_agents(text)
            plan.managed_summary = {'begin_count': len(info['begins']),
                                    'end_count': len(info['ends']),
                                    'hash_count': len(info['hashes'])}
        else:
            plan.managed_summary = {'begin_count': 0, 'end_count': 0, 'hash_count': 0}
        result = _agents_update(text, block)
        plan.managed_summary['conflict_type'] = 'NONE'
        return result
    except ManagedBlockError as exc:
        plan.managed_summary['conflict_type'] = exc.conflict_type
        return exc


def _finish_plan(plan: OnboardingPlan, verified_source) -> None:
    """Record preconditions and, when a verified package was supplied, bind it."""
    if plan.status != 'PLANNED':
        return
    _capture_plan_preconditions(plan)
    if plan.status != 'PLANNED':
        return
    if verified_source is not None:
        plan.source_binding = bind_verified_source(verified_source, plan)


# --- apply / rollback -------------------------------------------------------

MAX_WRITE_BYTES = 64 * 1024 * 1024


def _invoke_hook(fault_injector, name: str, *args) -> None:
    hook = getattr(fault_injector, name, None) if fault_injector is not None else None
    if callable(hook):
        hook(*args)


def _platform_capability_problems() -> list[str]:
    """Refuse to write unless the platform exposes the required safe primitives."""
    problems = []
    if os.name != 'posix':
        problems.append('POSIX dir_fd / O_NOFOLLOW primitives are unavailable on this platform')
        return problems
    for flag in _MISSING_OPEN_FLAGS:
        problems.append(f'os.{flag} is unavailable')
    for function, label in ((os.open, 'open'), (os.mkdir, 'mkdir'),
                             (os.unlink, 'unlink'), (os.rmdir, 'rmdir'),
                             (os.stat, 'stat'), (os.rename, 'rename'),
                             (os.link, 'link')):
        if function not in os.supports_dir_fd:
            problems.append(f'os.{label} does not support dir_fd')
    supports_fd = getattr(os, 'supports_fd', set())
    if os.listdir not in supports_fd:
        problems.append('os.listdir does not accept a directory fd')
    if not hasattr(os, 'scandir') or os.scandir not in supports_fd:
        problems.append('os.scandir does not accept a directory fd')
    for function, label in ((os.ftruncate, 'ftruncate'), (os.fsync, 'fsync')):
        if not callable(function):
            problems.append(f'os.{label} is unavailable')
    return problems


def _path_backend_problems() -> list[str]:
    """Refuse the path + identity writer when its primitives are missing.

    This probe describes the *fallback* writer only. It is consulted when the
    ``dir_fd`` writer cannot run, and a platform that can anchor descriptors
    never reaches it.
    """
    problems = []
    for label in ('lstat', 'stat', 'fstat', 'open', 'close', 'read', 'write',
                  'lseek', 'ftruncate', 'fsync', 'mkdir', 'rmdir', 'unlink',
                  'replace', 'link', 'scandir', 'listdir'):
        if not callable(getattr(os, label, None)):
            problems.append(f'os.{label} is unavailable for a path-based write set')
    return problems


def _open_host_fd(host_root, expected_identity) -> int:
    host = Path(host_root)
    try:
        info = host.lstat()
    except OSError as exc:
        raise TransactionConflict(f'host root cannot be inspected: {exc}') from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransactionConflict('host root is not a regular directory')
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        fd = os.open(host, flags)
    except OSError as exc:
        raise PlatformUnsupported(f'cannot open host root with O_NOFOLLOW: {exc}') from exc
    opened = os.fstat(fd)
    if expected_identity is not None and (opened.st_dev, opened.st_ino) != expected_identity:
        os.close(fd)
        raise TransactionConflict('host root identity changed since planning')
    return fd


def _read_all(fd: int, *, limit: int = MAX_WRITE_BYTES) -> bytes:
    chunks, total = [], 0
    while True:
        chunk = os.read(fd, min(64 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise TransactionConflict('target file exceeds the safe read limit')
        chunks.append(chunk)
    return b''.join(chunks)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = os.write(fd, view[offset:])
        if written <= 0:
            raise OSError('short write')
        offset += written


def _split_rel(rel: str) -> list[str]:
    lexical_rel_path(rel)
    return rel.split('/')


def _open_dir_at(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise TransactionConflict(f'{name}: not a regular directory') from exc
        raise


def _walk_parent_fd(root_fd: int, rel: str, *, create: bool,
                    created_dirs: list[dict] | None = None):
    """Return ``(parent_fd, leaf_name)`` or ``(None, leaf)`` when absent.

    Every component is opened relative to the previous directory descriptor with
    ``O_NOFOLLOW``; a symlink or non-directory component is a conflict.
    """
    parts = _split_rel(rel)
    fd = os.dup(root_fd)
    try:
        prefix = ''
        for part in parts[:-1]:
            prefix = f'{prefix}/{part}' if prefix else part
            try:
                next_fd = _open_dir_at(fd, part)
            except FileNotFoundError:
                if not create:
                    os.close(fd)
                    return None, parts[-1]
                os.mkdir(part, 0o755, dir_fd=fd)
                created_record = {'rel': prefix, 'identity': None}
                if created_dirs is not None:
                    created_dirs.append(created_record)
                next_fd = _open_dir_at(fd, part)
                created_info = os.fstat(next_fd)
                created_record['identity'] = (created_info.st_dev, created_info.st_ino)
            os.close(fd)
            fd = next_fd
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _read_file_at(root_fd: int, rel: str):
    parent, name = _walk_parent_fd(root_fd, rel, create=False)
    if parent is None:
        return None, None
    fd = None
    try:
        try:
            fd = os.open(name, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC,
                         dir_fd=parent)
        except FileNotFoundError:
            return None, None
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise TransactionConflict(f'{rel}: target is not a regular file')
        return _read_all(fd), info
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def _write_new_file(root_fd: int, rel: str, data: bytes, *, mode: int = 0o644,
                    created_dirs=None, record=None) -> None:
    """Create a complete file exclusively; a partial file never reaches *rel*.

    The bytes are written to an exclusive temp in the same directory and linked
    into place with ``os.link`` (which fails with EEXIST), so the destination is
    either absent or the full planned content.
    """
    parent, name = _walk_parent_fd(root_fd, rel, create=True,
                                   created_dirs=created_dirs)
    temp = f'.agentorch-write-{os.getpid()}-{secrets.token_hex(8)}'
    fd = None
    temp_identity = None
    failure = None
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW
                     | _O_CLOEXEC, mode, dir_fd=parent)
        if record is not None:
            record['temp_creation_unverified'] = True
        created = os.fstat(fd)
        temp_identity = (created.st_dev, created.st_ino)
        if record is not None:
            record['temp_identity'] = temp_identity
            record['temp_rel'] = '/'.join(rel.split('/')[:-1] + [temp])
            record['temp_creation_unverified'] = False
        _write_all(fd, data)
        os.fsync(fd)
        info = os.fstat(fd)
        os.close(fd)
        fd = None
        try:
            os.link(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
        except FileExistsError as exc:
            raise TransactionConflict(f'{rel}: target appeared before write') from exc
        if record is not None:
            record['created_identity'] = (info.st_dev, info.st_ino)
        os.fsync(parent)
    except BaseException as exc:  # preserve the original failure if cleanup also fails
        failure = exc
    finally:
        if fd is not None:
            os.close(fd)
        cleanup_error = None
        if temp_identity is not None:
            try:
                current = os.stat(temp, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != temp_identity:
                    raise TransactionConflict('temporary file identity changed')
                os.unlink(temp, dir_fd=parent)
                if record is not None:
                    record['temp_removed'] = True
                os.fsync(parent)
            except (OSError, TransactionConflict) as exc:
                cleanup_error = exc
                if record is not None:
                    record['temp_cleanup_failed'] = True
                    if record.get('temp_removed'):
                        record['temp_sync_failed'] = True
        os.close(parent)
    if failure is not None:
        raise failure
    if cleanup_error is not None:
        raise TransactionConflict('temporary file cleanup failed') from cleanup_error


# Extended attributes that the kernel reports as "this filesystem cannot store
# xattrs" are proof that there is nothing to preserve. Any other failure means
# the metadata state is unknown, so the caller must fail closed.
_XATTR_UNSUPPORTED = frozenset({errno.ENOTSUP, getattr(errno, 'EOPNOTSUPP', errno.ENOTSUP)})

# ``listxattr``/``flistxattr`` take a fourth ``options`` argument on macOS; this
# one refuses to follow a symlink, so a path swapped after the lstat check is
# reported as an error instead of enumerating the link target.
_XATTR_NOFOLLOW = 0x0001
_LIBC = None


def _libc():
    """The libc handle used only when the stdlib does not bind xattr calls."""
    global _LIBC
    if _LIBC is None:
        import ctypes
        try:
            _LIBC = ctypes.CDLL(None, use_errno=True)
        except (OSError, TypeError) as exc:
            # Windows raises TypeError for ``CDLL(None)``; there is no libc
            # xattr symbol to reach, so the metadata state stays unproven.
            raise TransactionConflict(
                'extended attributes cannot be enumerated on this platform') from exc
    return _LIBC


def _libc_listxattr(path=None, *, fd: int | None = None) -> list[str]:
    """Enumerate xattrs through the platform libc symbol.

    Only used where ``os.listxattr`` is missing (macOS). The descriptor form uses
    ``flistxattr`` so no ``/proc`` mount or magic symlink is needed; the path
    form uses ``listxattr`` with ``XATTR_NOFOLLOW``. A missing symbol or a failed
    call raises, so the caller keeps failing closed: only a completed call that
    reports no names - or a filesystem that answers that it cannot store xattrs -
    proves there is nothing to preserve.
    """
    import ctypes

    libc = _libc()
    if fd is not None:
        symbol = 'flistxattr'
        argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char),
                    ctypes.c_size_t, ctypes.c_int]
    else:
        symbol = 'listxattr'
        argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_char),
                    ctypes.c_size_t, ctypes.c_int]
    function = getattr(libc, symbol, None)
    if function is None:
        raise TransactionConflict(
            'extended attributes cannot be enumerated on this platform')
    function.argtypes = argtypes
    function.restype = ctypes.c_ssize_t

    def call(buffer, size):
        if fd is not None:
            return function(fd, buffer, size, 0)
        return function(os.fsencode(path), buffer, size, _XATTR_NOFOLLOW)

    def checked(size, *, allow_unsupported=False):
        if size >= 0:
            return size
        code = ctypes.get_errno()
        if allow_unsupported and code in _XATTR_UNSUPPORTED:
            return None
        raise TransactionConflict(
            f'extended attributes cannot be inspected: {os.strerror(code)}')

    size = checked(call(None, 0), allow_unsupported=True)
    if not size:
        return []
    buffer = ctypes.create_string_buffer(size)
    written = checked(call(buffer, size))
    return sorted(os.fsdecode(name) for name in buffer.raw[:written].split(b'\x00') if name)


def _xattr_names(path=None, *, fd: int | None = None) -> list[str]:
    """Return the extended-attribute names on *path* or an open *fd*.

    ``os.listxattr`` accepts an open descriptor directly, so no ``/proc`` mount
    or magic symlink is required. Where the stdlib does not bind the xattr calls
    (macOS), the same libc symbol is reached through ``ctypes``. When neither
    route can enumerate attributes the metadata state is unproven and this
    raises; a filesystem that reports xattrs as unsupported simply has none to
    preserve.
    """
    if not hasattr(os, 'listxattr'):
        return _libc_listxattr(path, fd=fd)
    target = fd if fd is not None else path
    try:
        return sorted(os.listxattr(target))
    except OSError as exc:
        if exc.errno in _XATTR_UNSUPPORTED:
            return []
        raise TransactionConflict(
            f'extended attributes cannot be inspected: {exc}') from exc
    except (TypeError, ValueError) as exc:
        raise TransactionConflict(
            'extended attributes cannot be enumerated for an open descriptor') from exc


def _metadata_preservation_problems_for_stat(info, rel: str) -> list[str]:
    """Metadata an in-place append provably keeps; everything else blocks."""
    problems: list[str] = []
    if not stat.S_ISREG(info.st_mode):
        problems.append(f'{rel}: target is not a regular file')
        return problems
    if info.st_nlink != 1:
        problems.append(
            f'{rel}: target has {info.st_nlink} hard links; an in-place append would '
            'change bytes outside the allowed write set')
    if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
        problems.append(
            f'{rel}: setuid/setgid mode bits are not provably preserved by an append')
    return problems


def _target_metadata_problems(host: Path, rel: str) -> list[str]:
    """Plan/apply-time check of an existing write target without writing."""
    try:
        path = safe_project_path(host, rel)
    except UnsafePath as exc:
        return [str(exc)]
    try:
        info = path.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        return [f'{rel}: cannot inspect metadata: {exc}']
    if stat.S_ISLNK(info.st_mode):
        return [f'{rel}: target is a symlink whose metadata cannot be preserved']
    problems = _metadata_preservation_problems_for_stat(info, rel)
    if problems:
        return problems
    try:
        names = _xattr_names(path)
    except TransactionConflict as exc:
        return [f'{rel}: {exc}']
    if names:
        problems.append(
            f'{rel}: target carries extended attributes or ACLs ({", ".join(names)}); '
            'an in-place append cannot prove they survive')
    return problems


def _restore_appended_prefix(fd: int, original: bytes, expected: bytes) -> bool:
    """Undo a partial in-place append without replacing the inode.

    Only truncates while the current bytes are still a prefix of *expected* that
    begins with *original*; foreign content is left for the transaction's
    conflict report. Returns whether the original bytes were restored.
    """
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        current = _read_all(fd)
        if current == original:
            return True
        if not (current.startswith(original) and expected.startswith(current)):
            return False
        os.ftruncate(fd, len(original))
        os.fsync(fd)
        return True
    except (OSError, TransactionConflict):
        return False


def _append_file(root_fd: int, rel: str, original: bytes, expected: bytes) -> None:
    """Append *expected* to an existing regular file in place.

    The original bytes stay the byte-exact prefix (CRLF and a missing trailing
    newline included) and the inode, device, mode, owner and group are kept.
    Metadata whose survival cannot be proven (extended attributes / ACLs,
    hard links, setuid/setgid) fails closed instead of being claimed as preserved.
    """
    if not expected.startswith(original):
        raise TransactionConflict(f'{rel}: planned append does not preserve the original bytes')
    parent, name = _walk_parent_fd(root_fd, rel, create=False)
    if parent is None:
        raise TransactionConflict(f'{rel}: target disappeared before append')
    fd = None
    try:
        fd = os.open(name, os.O_RDWR | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=parent)
        _append_open_file(fd, rel, original, expected)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def _append_open_file(descriptor: int, rel: str, original: bytes,
                      expected: bytes) -> None:
    """The in-place append body shared by both writers; the caller owns ``descriptor``."""
    info = os.fstat(descriptor)
    problems = _metadata_preservation_problems_for_stat(info, rel)
    if problems:
        raise TransactionConflict('; '.join(problems))
    names = _xattr_names(fd=descriptor)
    if names:
        raise TransactionConflict(
            f'{rel}: target carries extended attributes or ACLs ({", ".join(names)}); '
            'an in-place append cannot prove they survive')
    os.lseek(descriptor, 0, os.SEEK_SET)
    current = _read_all(descriptor)
    if current != original:
        raise TransactionConflict(f'{rel}: target changed before append')
    try:
        os.lseek(descriptor, len(original), os.SEEK_SET)
        _write_all(descriptor, expected[len(original):])
        # Verify before truncating: a concurrent writer may have replaced the
        # bytes, and truncating to our expected size would then corrupt them.
        os.lseek(descriptor, 0, os.SEEK_SET)
        written = _read_all(descriptor)
        if written != expected:
            raise TransactionConflict(f'{rel}: target changed during append')
        os.ftruncate(descriptor, len(expected))
        os.fsync(descriptor)
    except BaseException:
        _restore_appended_prefix(descriptor, original, expected)
        raise


def _overwrite_fd(fd: int, data: bytes) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    _write_all(fd, data)
    os.ftruncate(fd, len(data))
    os.fsync(fd)


def _restore_fd_in_place(fd: int, expected: bytes, original: bytes) -> bool:
    """Best-effort restore of *original* into the same inode after a rewrite."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        current = _read_all(fd)
        if current == original:
            return True
        if current != expected:
            return False
        _overwrite_fd(fd, original)
        return True
    except (OSError, TransactionConflict):
        return False


def _rewrite_file_in_place(root_fd: int, rel: str, original: bytes,
                           expected: bytes) -> None:
    """Replace the bytes of an existing regular file without replacing the inode.

    Used for the managed AGENTS.md block version update: the host prefix and the
    descriptor (device / inode / mode / owner) are preserved, and the current
    bytes must still equal the planned original before anything changes.
    """
    parent, name = _walk_parent_fd(root_fd, rel, create=False)
    if parent is None:
        raise TransactionConflict(f'{rel}: target disappeared before rewrite')
    fd = None
    try:
        fd = os.open(name, os.O_RDWR | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=parent)
        _rewrite_open_file(fd, rel, original, expected)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def _rewrite_open_file(descriptor: int, rel: str, original: bytes,
                       expected: bytes) -> None:
    """The in-place rewrite body shared by both writers; the caller owns ``descriptor``."""
    info = os.fstat(descriptor)
    problems = _metadata_preservation_problems_for_stat(info, rel)
    if problems:
        raise TransactionConflict('; '.join(problems))
    names = _xattr_names(fd=descriptor)
    if names:
        raise TransactionConflict(
            f'{rel}: target carries extended attributes or ACLs ({", ".join(names)}); '
            'an in-place rewrite cannot prove they survive')
    os.lseek(descriptor, 0, os.SEEK_SET)
    current = _read_all(descriptor)
    if current != original:
        raise TransactionConflict(f'{rel}: target changed before rewrite')
    try:
        _overwrite_fd(descriptor, expected)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if _read_all(descriptor) != expected:
            raise TransactionConflict(f'{rel}: target changed during rewrite')
    except BaseException:
        _restore_fd_in_place(descriptor, expected, original)
        raise


def _restore_in_place_at(root_fd: int, rel: str, expected: bytes,
                         original: bytes) -> None:
    parent, name = _walk_parent_fd(root_fd, rel, create=False)
    if parent is None:
        raise TransactionConflict(f'{rel}: target disappeared during recovery')
    fd = None
    try:
        fd = os.open(name, os.O_RDWR | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=parent)
        _restore_in_place_open(fd, rel, expected, original)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def _restore_in_place_open(descriptor: int, rel: str, expected: bytes,
                           original: bytes) -> None:
    """Shared in-place recovery body; the caller owns ``descriptor``."""
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise TransactionConflict(f'{rel}: target is not a regular file')
    os.lseek(descriptor, 0, os.SEEK_SET)
    current = _read_all(descriptor)
    if current == original:
        return
    if current != expected:
        raise TransactionConflict(f'{rel}: current content no longer matches this operation')
    _overwrite_fd(descriptor, original)


def _replace_file_at(root_fd: int, rel: str, expected: bytes, original: bytes) -> None:
    """Replace *rel* with *original* after confirming the current bytes match."""
    if rel == ALLOWED_ROOT_ENTRY:
        raise TransactionConflict(
            'root AGENTS.md recovery disabled: metadata preservation is not established')
    current, info = _read_file_at(root_fd, rel)
    if current is None or current != expected or info is None:
        raise TransactionConflict(f'{rel}: current content no longer matches this operation')
    parent, name = _walk_parent_fd(root_fd, rel, create=False)
    if parent is None:
        raise TransactionConflict(f'{rel}: target disappeared during recovery')
    temp = f'.agentorch-restore-{os.getpid()}-{secrets.token_hex(8)}'
    fd = None
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW
                     | _O_CLOEXEC, stat.S_IMODE(info.st_mode), dir_fd=parent)
        _write_all(fd, original)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.rename(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temp, dir_fd=parent)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        os.close(parent)


def _truncate_at(root_fd: int, rel: str, expected: bytes, length: int) -> None:
    parent, name = _walk_parent_fd(root_fd, rel, create=False)
    if parent is None:
        raise TransactionConflict(f'{rel}: target disappeared during recovery')
    fd = None
    try:
        fd = os.open(name, os.O_RDWR | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=parent)
        _truncate_open(fd, rel, expected, length)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def _truncate_open(descriptor: int, rel: str, expected: bytes, length: int) -> None:
    """Shared truncate body; the caller owns ``descriptor``."""
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise TransactionConflict(f'{rel}: target is not a regular file')
    if _read_all(descriptor) != expected:
        raise TransactionConflict(f'{rel}: current content no longer matches this operation')
    os.ftruncate(descriptor, length)
    os.fsync(descriptor)


def _unlink_at(root_fd: int, rel: str) -> None:
    parent, name = _walk_parent_fd(root_fd, rel, create=False)
    if parent is None:
        return
    try:
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def _remove_dir_at(root_fd: int, rel: str, expected_identity=None) -> None:
    parent, name = _walk_parent_fd(root_fd, rel, create=False)
    if parent is None:
        return
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if expected_identity is None or (info.st_dev, info.st_ino) != expected_identity:
            raise TransactionConflict(f'{rel}: created directory identity changed')
        os.rmdir(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def _ensure_agentorch_target(root_fd: int, plan: OnboardingPlan) -> None:
    try:
        fd = _open_dir_at(root_fd, AGENTORCH_DIRNAME)
    except FileNotFoundError:
        if plan.target_present:
            raise TransactionConflict('agentorch/ disappeared since planning')
        return
    try:
        entries = os.listdir(fd)
    finally:
        os.close(fd)
    if not plan.target_present:
        raise TransactionConflict('agentorch/ appeared since planning')
    if plan.mode == 'UPGRADE':
        # The upgrade's per-file originals are verified separately; the directory
        # itself must simply still exist and be a regular directory.
        return
    if entries:
        raise TransactionConflict('agentorch/ is no longer empty')


def _verify_originals(root_fd: int, plan: OnboardingPlan) -> None:
    for rel, original in plan.originals.items():
        current, _ = _read_file_at(root_fd, rel)
        if original is None:
            if current is not None:
                raise TransactionConflict(f'{rel}: appeared since planning')
        elif current != original:
            raise TransactionConflict(f'{rel}: changed since planning')


def _tree_files_at(root_fd: int, rel: str = AGENTORCH_DIRNAME) -> set[str]:
    parent, name = _walk_parent_fd(root_fd, rel, create=False)
    if parent is None:
        return set()
    files: set[str] = set()
    try:
        fd = _open_dir_at(parent, name)
    finally:
        os.close(parent)
    stack = [(fd, '')]
    while stack:
        directory_fd, prefix = stack.pop()
        try:
            entries = list(os.scandir(directory_fd))
            for entry in entries:
                entry_rel = f'{prefix}/{entry.name}' if prefix else entry.name
                if entry.is_symlink():
                    files.add(f'{rel}/{entry_rel}')
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in ISOLATED_PRIVATE_DIRS:
                        continue
                    child = _open_dir_at(directory_fd, entry.name)
                    stack.append((child, entry_rel))
                else:
                    files.add(f'{rel}/{entry_rel}')
        finally:
            os.close(directory_fd)
    return files


def _inspect_no_link(path, *, label: str):
    """``lstat`` *path* and refuse a symlink or an entry with no identity.

    Platforms without ``O_NOFOLLOW`` cannot delegate the refusal to the kernel,
    so the entry is inspected first and its identity is required: an identity
    that cannot be read means the caller cannot later prove it opened the entry
    it inspected, and that is a stop, not a warning.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise TransactionConflict(f'{label} cannot be inspected: {exc}') from exc
    if stat.S_ISLNK(info.st_mode):
        raise TransactionConflict(f'{label} is a symlink; refusing to use it')
    identity = _file_identity(info)
    if identity is None:
        raise TransactionConflict(
            f'{label} has no verifiable identity on this platform; refusing to use it')
    return info, identity


def _open_verified(path, flags: int, *, label: str, expected_identity=None,
                   inspected=None):
    """Open *path* and require the descriptor to be the inspected inode.

    The inspect-then-open order alone would leave a swap window, so the opened
    descriptor's identity must equal the inspected one. A mismatch, or an
    identity the platform cannot report, closes the descriptor and raises: no
    caller ever writes through a descriptor that is not provably the inspected
    entry.
    """
    if inspected is None:
        inspected, inspected_identity = _inspect_no_link(path, label=label)
        if expected_identity is None:
            expected_identity = inspected_identity
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TransactionConflict(f'{label} cannot be opened safely: {exc}') from exc
    try:
        opened = os.fstat(descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise TransactionConflict(
            f'{label} cannot be inspected after opening: {exc}') from exc
    opened_identity = _file_identity(opened)
    if opened_identity is None:
        os.close(descriptor)
        raise TransactionConflict(
            f'{label} identity cannot be verified after opening; refusing to use it')
    if opened_identity != expected_identity:
        os.close(descriptor)
        raise TransactionConflict(f'{label} changed while it was being opened')
    return descriptor, opened


class _FdWriteSession:
    """The ``dir_fd`` writer: every operation is anchored to one descriptor.

    Each method is a thin delegation to the module-level ``dir_fd`` helper, so a
    caller that already holds a host descriptor keeps using exactly that code.
    """

    kind = 'DIR_FD'

    def __init__(self, root_fd: int, *, owns_root: bool = False):
        self.root = root_fd
        self._owns_root = owns_root

    def close(self) -> None:
        if self._owns_root:
            os.close(self.root)

    def read_file(self, rel):
        return _read_file_at(self.root, rel)

    def write_new_file(self, rel, data, *, mode=0o644, created_dirs=None,
                       record=None) -> None:
        _write_new_file(self.root, rel, data, mode=mode, created_dirs=created_dirs,
                        record=record)

    def append(self, rel, original, expected) -> None:
        _append_file(self.root, rel, original, expected)

    def rewrite(self, rel, original, expected) -> None:
        _rewrite_file_in_place(self.root, rel, original, expected)

    def replace_file(self, rel, data, original) -> None:
        _replace_file_at_for_write(self.root, rel, data, original)

    def unlink(self, rel) -> None:
        _unlink_at(self.root, rel)

    def truncate(self, rel, expected, length) -> None:
        _truncate_at(self.root, rel, expected, length)

    def restore_in_place(self, rel, expected, original) -> None:
        _restore_in_place_at(self.root, rel, expected, original)

    def restore_replace(self, rel, expected, original) -> None:
        _replace_file_at(self.root, rel, expected, original)

    def remove_dir(self, rel, identity=None) -> None:
        _remove_dir_at(self.root, rel, identity)

    def tree_files(self):
        return _tree_files_at(self.root)

    def ensure_target(self, plan) -> None:
        _ensure_agentorch_target(self.root, plan)

    def verify_originals(self, plan) -> None:
        _verify_originals(self.root, plan)


class _PathWriteSession:
    """Path + identity writer for platforms that have no ``dir_fd``.

    Every operation descends from the host root and proves each step: the root
    identity is the one planning captured, each intermediate component must be a
    real directory, a leaf that is a link or the wrong kind of entry is refused
    before it is opened, and every opened or created file is matched against the
    identity of the entry that was inspected. Unknown identity, a changed
    identity, a broken parent chain or a temporary file that cannot be accounted
    for all raise, and the transaction then reports ``BLOCKED`` with the host
    left as it was.

    Directory ``fsync`` has no equivalent here (a directory cannot be opened on
    these platforms), so the durability of the entry-producing rename/link is
    not claimed. Recovery verifies content and identity instead of durability.
    """

    kind = 'PATH_IDENTITY'

    def __init__(self, root: Path, identity):
        self.root = root
        self.identity = identity

    @classmethod
    def open(cls, host_root, expected_identity):
        host = Path(host_root)
        try:
            inspected = os.lstat(host)
        except OSError as exc:
            raise TransactionConflict(f'host root cannot be inspected: {exc}') from exc
        if stat.S_ISLNK(inspected.st_mode) or not stat.S_ISDIR(inspected.st_mode):
            raise TransactionConflict('host root is not a regular directory')
        identity = _file_identity(inspected)
        if identity is None:
            raise TransactionConflict(
                'host root has no verifiable identity on this platform; refusing to write')
        if expected_identity is not None and identity != expected_identity:
            raise TransactionConflict('host root identity changed since planning')
        return cls(host, identity)

    def close(self) -> None:
        return None

    def _verify_root(self) -> None:
        try:
            info = os.lstat(self.root)
        except FileNotFoundError:
            raise TransactionConflict('host root disappeared during the transaction') from None
        except OSError as exc:
            raise TransactionConflict(f'host root cannot be inspected: {exc}') from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TransactionConflict('host root is no longer a regular directory')
        if _file_identity(info) != self.identity:
            raise TransactionConflict('host root identity changed during the transaction')

    def _descend(self, parts, *, create: bool, created_dirs=None) -> Path:
        """Return the directory holding the leaf, proving every step below root."""
        self._verify_root()
        current = self.root
        prefix = ''
        for part in parts:
            prefix = f'{prefix}/{part}' if prefix else part
            candidate = current / part
            created = False
            try:
                info = os.lstat(candidate)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(candidate, 0o755)
                except FileExistsError:
                    info = os.lstat(candidate)
                else:
                    info = os.lstat(candidate)
                    created = True
            except OSError as exc:
                raise TransactionConflict(f'{prefix} cannot be inspected: {exc}') from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise TransactionConflict(f'{prefix}: not a regular directory')
            if created:
                identity = _file_identity(info)
                if identity is None:
                    raise TransactionConflict(
                        f'{prefix}: created directory identity is unverifiable')
                if created_dirs is not None:
                    created_dirs.append({'rel': prefix, 'identity': identity})
            current = candidate
        return current

    def _leaf(self, rel: str, *, create: bool, created_dirs=None):
        parts = _split_rel(rel)
        parent = self._descend(parts[:-1], create=create, created_dirs=created_dirs)
        return parent, parts[-1]

    def read_file(self, rel):
        parts = _split_rel(rel)
        try:
            parent = self._descend(parts[:-1], create=False)
        except FileNotFoundError:
            return None, None
        path = parent / parts[-1]
        try:
            inspected, identity = _inspect_no_link(path, label=rel)
        except FileNotFoundError:
            return None, None
        if not stat.S_ISREG(inspected.st_mode):
            raise TransactionConflict(f'{rel}: target is not a regular file')
        descriptor, info = _open_verified(
            path, os.O_RDONLY | _O_BINARY, label=rel, expected_identity=identity,
            inspected=inspected)
        try:
            return _read_all(descriptor), info
        finally:
            os.close(descriptor)

    def ensure_target(self, plan) -> None:
        self._verify_root()
        target = self.root / AGENTORCH_DIRNAME
        try:
            info = os.lstat(target)
        except FileNotFoundError:
            if plan.target_present:
                raise TransactionConflict('agentorch/ disappeared since planning') from None
            return
        except OSError as exc:
            raise TransactionConflict(f'agentorch/ cannot be inspected: {exc}') from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TransactionConflict('agentorch/: not a regular directory')
        if not plan.target_present:
            raise TransactionConflict('agentorch/ appeared since planning')
        if plan.mode == 'UPGRADE':
            # The upgrade's per-file originals are verified separately; the
            # directory itself must simply still exist and be a regular directory.
            return
        try:
            entries = os.listdir(target)
        except OSError as exc:
            raise TransactionConflict(f'agentorch/ cannot be listed: {exc}') from exc
        if entries:
            raise TransactionConflict('agentorch/ is no longer empty')

    def verify_originals(self, plan) -> None:
        for rel, original in plan.originals.items():
            current, _ = self.read_file(rel)
            if original is None:
                if current is not None:
                    raise TransactionConflict(f'{rel}: appeared since planning')
            elif current != original:
                raise TransactionConflict(f'{rel}: changed since planning')

    def tree_files(self):
        self._verify_root()
        target = self.root / AGENTORCH_DIRNAME
        try:
            info = os.lstat(target)
        except FileNotFoundError:
            return set()
        except OSError as exc:
            raise TransactionConflict(
                f'{AGENTORCH_DIRNAME}/ cannot be inspected: {exc}') from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TransactionConflict(f'{AGENTORCH_DIRNAME}/: not a regular directory')
        files: set[str] = set()
        stack = [(target, '')]
        while stack:
            directory, prefix = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                raise TransactionConflict(
                    f'{AGENTORCH_DIRNAME}/ cannot be enumerated: {exc}') from exc
            for entry in entries:
                entry_rel = f'{prefix}/{entry.name}' if prefix else entry.name
                if entry.is_symlink():
                    files.add(f'{AGENTORCH_DIRNAME}/{entry_rel}')
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in ISOLATED_PRIVATE_DIRS:
                        continue
                    stack.append((Path(entry.path), entry_rel))
                else:
                    files.add(f'{AGENTORCH_DIRNAME}/{entry_rel}')
        return files

    def _unlink_verified(self, path: Path, identity, label: str) -> None:
        """Remove *path* only while it is still the inode this transaction made."""
        try:
            current = os.lstat(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise TransactionConflict(f'{label} cannot be inspected: {exc}') from exc
        if stat.S_ISLNK(current.st_mode):
            raise TransactionConflict(f'{label} is a symlink; refusing to remove it')
        if _file_identity(current) != identity:
            raise TransactionConflict(f'{label} identity changed')
        os.unlink(path)

    def write_new_file(self, rel, data, *, mode=0o644, created_dirs=None,
                       record=None) -> None:
        """Create a complete file exclusively; a partial file never reaches *rel*.

        The bytes go to an exclusive temporary entry in the same directory and
        are linked into place, so the destination is either absent or complete.
        """
        parent, name = self._leaf(rel, create=True, created_dirs=created_dirs)
        temp = f'.agentorch-write-{os.getpid()}-{secrets.token_hex(8)}'
        temp_path = parent / temp
        fd = None
        temp_identity = None
        failure = None
        cleanup_error = None
        try:
            try:
                fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | _O_BINARY, mode)
            except OSError as exc:
                raise TransactionConflict(
                    f'{rel}: temporary file cannot be created: {exc}') from exc
            if record is not None:
                record['temp_creation_unverified'] = True
            created = os.fstat(fd)
            temp_identity = _file_identity(created)
            if temp_identity is None:
                raise TransactionConflict(f'{rel}: temporary file identity is unverified')
            if record is not None:
                record['temp_identity'] = temp_identity
                record['temp_rel'] = '/'.join(rel.split('/')[:-1] + [temp])
                record['temp_creation_unverified'] = False
            _write_all(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = None
            try:
                os.link(temp_path, parent / name)
            except FileExistsError as exc:
                raise TransactionConflict(f'{rel}: target appeared before write') from exc
            except OSError as exc:
                raise TransactionConflict(
                    f'{rel}: target cannot be placed atomically: {exc}') from exc
            if record is not None:
                record['created_identity'] = temp_identity
        except BaseException as exc:  # preserve the original failure if cleanup also fails
            failure = exc
        finally:
            if fd is not None:
                os.close(fd)
            if temp_identity is not None:
                try:
                    self._unlink_verified(temp_path, temp_identity, rel)
                    if record is not None:
                        record['temp_removed'] = True
                except (OSError, TransactionConflict) as exc:
                    cleanup_error = exc
                    if record is not None:
                        record['temp_cleanup_failed'] = True
        if failure is not None:
            raise failure
        if cleanup_error is not None:
            raise TransactionConflict('temporary file cleanup failed') from cleanup_error

    def _open_existing(self, rel: str):
        parent, name = self._leaf(rel, create=False)
        path = parent / name
        try:
            inspected, identity = _inspect_no_link(path, label=rel)
        except FileNotFoundError:
            raise TransactionConflict(f'{rel}: target disappeared') from None
        if not stat.S_ISREG(inspected.st_mode):
            raise TransactionConflict(f'{rel}: target is not a regular file')
        return _open_verified(path, os.O_RDWR | _O_BINARY, label=rel,
                              expected_identity=identity, inspected=inspected)

    def append(self, rel, original, expected) -> None:
        if not expected.startswith(original):
            raise TransactionConflict(
                f'{rel}: planned append does not preserve the original bytes')
        descriptor, _info = self._open_existing(rel)
        try:
            _append_open_file(descriptor, rel, original, expected)
        finally:
            os.close(descriptor)

    def rewrite(self, rel, original, expected) -> None:
        descriptor, _info = self._open_existing(rel)
        try:
            _rewrite_open_file(descriptor, rel, original, expected)
        finally:
            os.close(descriptor)

    def replace_file(self, rel, data, original) -> None:
        if rel == ALLOWED_ROOT_ENTRY:
            raise TransactionConflict(
                'root AGENTS.md replacement disabled: metadata preservation is not established')
        current, info = self.read_file(rel)
        if current is None or current != original or info is None:
            raise TransactionConflict(f'{rel}: changed before write')
        parent, name = self._leaf(rel, create=False)
        path = parent / name
        temp = f'.agentorch-write-{os.getpid()}-{secrets.token_hex(8)}'
        temp_path = parent / temp
        fd = None
        try:
            fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | _O_BINARY, stat.S_IMODE(info.st_mode))
            _write_all(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = None
            # The entry is proved to be the inode whose bytes were verified
            # immediately before it is replaced.
            inspected, identity = _inspect_no_link(path, label=rel)
            if not stat.S_ISREG(inspected.st_mode):
                raise TransactionConflict(f'{rel}: target is not a regular file')
            if identity != _file_identity(info):
                raise TransactionConflict(f'{rel}: target changed before write')
            os.replace(temp_path, path)
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def unlink(self, rel) -> None:
        parts = _split_rel(rel)
        try:
            parent = self._descend(parts[:-1], create=False)
        except FileNotFoundError:
            return
        path = parent / parts[-1]
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise TransactionConflict(f'{rel} cannot be inspected: {exc}') from exc
        if stat.S_ISLNK(info.st_mode):
            raise TransactionConflict(f'{rel} is a symlink; refusing to remove it')
        os.unlink(path)

    def truncate(self, rel, expected, length) -> None:
        descriptor, _info = self._open_existing(rel)
        try:
            _truncate_open(descriptor, rel, expected, length)
        finally:
            os.close(descriptor)

    def restore_in_place(self, rel, expected, original) -> None:
        descriptor, _info = self._open_existing(rel)
        try:
            _restore_in_place_open(descriptor, rel, expected, original)
        finally:
            os.close(descriptor)

    def restore_replace(self, rel, expected, original) -> None:
        if rel == ALLOWED_ROOT_ENTRY:
            raise TransactionConflict(
                'root AGENTS.md recovery disabled: metadata preservation is not established')
        current, info = self.read_file(rel)
        if current is None or current != expected or info is None:
            raise TransactionConflict(
                f'{rel}: current content no longer matches this operation')
        parent, name = self._leaf(rel, create=False)
        path = parent / name
        temp = f'.agentorch-restore-{os.getpid()}-{secrets.token_hex(8)}'
        temp_path = parent / temp
        fd = None
        try:
            fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | _O_BINARY, stat.S_IMODE(info.st_mode))
            _write_all(fd, original)
            os.fsync(fd)
            os.close(fd)
            fd = None
            inspected, identity = _inspect_no_link(path, label=rel)
            if not stat.S_ISREG(inspected.st_mode):
                raise TransactionConflict(f'{rel}: target is not a regular file')
            if identity != _file_identity(info):
                raise TransactionConflict(f'{rel}: target changed during recovery')
            os.replace(temp_path, path)
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def remove_dir(self, rel, identity=None) -> None:
        parts = _split_rel(rel)
        try:
            parent = self._descend(parts[:-1], create=False)
        except FileNotFoundError:
            return
        path = parent / parts[-1]
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise TransactionConflict(f'{rel} cannot be inspected: {exc}') from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TransactionConflict(f'{rel}: created directory changed')
        if identity is None or _file_identity(info) != identity:
            raise TransactionConflict(f'{rel}: created directory identity changed')
        # A non-empty directory raises here and is reported as a conflict by the
        # caller instead of deleting someone else's content.
        os.rmdir(path)


def _write_backend_problems() -> list[str]:
    """All reasons no writer can run here; empty when at least one can.

    A platform that can anchor descriptors never reports the path writer's
    limits, and a platform that cannot keeps the ``dir_fd`` reason visible next
    to the path reason instead of hiding the stronger anchor's absence.
    """
    dir_fd_problems = _platform_capability_problems()
    if not dir_fd_problems:
        return []
    path_problems = _path_backend_problems()
    if not path_problems:
        return []
    return list(dir_fd_problems) + list(path_problems)


def _write_session(plan: OnboardingPlan):
    """Return the writer for this plan as ``(session, problems)``.

    The ``dir_fd`` writer is used whenever the platform has the primitives: it is
    the stronger anchor and is never replaced by the weaker one. Only a platform
    that cannot anchor a descriptor at all falls through to the path + identity
    writer, and when neither writer is available the caller reports ``BLOCKED``.
    """
    dir_fd_problems = _platform_capability_problems()
    if not dir_fd_problems:
        try:
            return _FdWriteSession(_open_host_fd(plan.host_root, plan.host_identity),
                                   owns_root=True), []
        except (OSError, OnboardingError) as exc:
            return None, [str(exc)]
    path_problems = _path_backend_problems()
    if path_problems:
        return None, list(dir_fd_problems) + list(path_problems)
    try:
        return _PathWriteSession.open(plan.host_root, plan.host_identity), []
    except (OSError, OnboardingError) as exc:
        return None, [str(exc)]


def _rollback_transaction(root_fd: int, records: list[dict],
                          created_dirs: list[dict], fault_injector) -> dict:
    """Roll back a transaction through a directory descriptor.

    Kept as the descriptor-shaped entry point for callers that already hold one;
    the shared recovery below runs against either writer.
    """
    return _rollback_records(_FdWriteSession(root_fd), records, created_dirs,
                             fault_injector)


def _rollback_records(session, records: list[dict],
                      created_dirs: list[dict], fault_injector) -> dict:
    conflicts, failures = [], []
    for record in reversed(records):
        rel = record['rel']
        kind = record['kind']
        original = record['original']
        if record.get('temp_creation_unverified'):
            failures.append('temporary file identity is unverified')
        if record.get('temp_sync_failed'):
            failures.append('temporary file cleanup durability is unverified')
        try:
            current, info = session.read_file(rel)
            if kind == 'create':
                if current is None:
                    pass  # never created, or already removed; still inspect temp
                elif (record.get('created_identity') is None or info is None
                        or (info.st_dev, info.st_ino) != record['created_identity']
                        or current != record['expected']):
                    conflicts.append(rel)
                else:
                    session.unlink(rel)
            elif kind == 'append':
                if current == original:
                    pass  # append never happened
                elif current != record['expected']:
                    conflicts.append(rel)
                else:
                    # Truncate in place: recovery must keep the original inode,
                    # mode and owner, not replace them with a new file.
                    session.truncate(rel, record['expected'], len(original or b''))
            elif kind == 'inplace':
                if current == original:
                    pass  # rewrite never happened
                elif current != record['expected']:
                    conflicts.append(rel)
                else:
                    session.restore_in_place(rel, record['expected'],
                                             original or b'')
            else:
                if current == original:
                    pass  # replacement never happened
                elif current != record['expected']:
                    conflicts.append(rel)
                else:
                    session.restore_replace(rel, record['expected'],
                                            original or b'')
        except (OSError, TransactionConflict) as exc:
            failures.append(f'{rel}: {exc}')
        temp_rel = record.get('temp_rel')
        if temp_rel and record.get('temp_removed'):
            try:
                current, _ = session.read_file(temp_rel)
                if current is not None:
                    conflicts.append(temp_rel)
            except (OSError, TransactionConflict) as exc:
                failures.append(f'temporary file verification failed: {type(exc).__name__}')
        if temp_rel and not record.get('temp_removed'):
            try:
                current, info = session.read_file(temp_rel)
                if (info is None or current is None
                        or (info.st_dev, info.st_ino) != record.get('temp_identity')
                        or not record['expected'].startswith(current)):
                    conflicts.append(temp_rel)
                else:
                    session.unlink(temp_rel)
                    after, _ = session.read_file(temp_rel)
                    if after is not None:
                        conflicts.append(temp_rel)
                    else:
                        record['temp_removed'] = True
            except (OSError, TransactionConflict) as exc:
                failures.append(f'temporary file cleanup failed: {type(exc).__name__}')
    leftovers = []
    for record in records:
        rel = record['rel']
        try:
            current, _ = session.read_file(rel)
        except (OSError, TransactionConflict) as exc:
            failures.append(f'{rel}: cannot verify recovery: {exc}')
            continue
        original = record['original']
        if original is None:
            if current is not None:
                leftovers.append(rel)
        elif current != original:
            leftovers.append(rel)
    for directory in sorted(created_dirs, key=lambda item: item['rel'].count('/'), reverse=True):
        rel = directory['rel']
        try:
            session.remove_dir(rel, directory.get('identity'))
        except (OSError, TransactionConflict):
            # A non-empty directory means a concurrent writer left content:
            # preserve the scene instead of deleting someone else's file.
            conflicts.append(rel)
    if failures or conflicts or leftovers:
        return {'result': 'FAILED', 'conflicts': sorted(set(conflicts)),
                'failures': failures, 'unrestored': sorted(set(leftovers))}
    return {'result': 'SUCCEEDED'}


def _raw_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def apply_isolated_onboarding(plan: OnboardingPlan, *, now: datetime | None = None,
                              fault_injector=None, precondition=None,
                              declared_exclusive=False, expected_head=None) -> dict:
    """Apply a verified isolated onboarding inside one guarded transaction.

    Only ``agentorch/**`` and the root ``AGENTS.md`` are written. On a platform
    with ``dir_fd`` every path is opened relative to a directory descriptor with
    ``O_NOFOLLOW``; where those primitives do not exist the same write set runs
    through the path + identity writer (:class:`_PathWriteSession`), which refuses
    links before opening and re-checks the opened identity afterwards. New files
    use exclusive creation either way. A failure restores, in reverse order, only
    entries this call wrote whose current bytes still match; anything else is
    left in place as a conflict. Explicit cross-process rollback deliberately
    remains blocked.
    """
    now = now or datetime.now(timezone.utc)
    host = Path(plan.host_root)
    result = {
        'status': plan.status,
        'mode': plan.mode,
        'host_root': str(host),
        'agentorch_root': str(plan.agentorch_root),
        'written': [],
        'removed': [],
        'agents_action': plan.agents_action,
        'agents_created': plan.agents_created,
        'backup_location': None,
        'problems': list(plan.problems),
        'rollback': {'required': False, 'attempted': False, 'result': 'NOT_NEEDED'},
        'diff': plan.diff,
        'exclusive_writer': plan.exclusive_writer,
        'worktree_head': plan.worktree_head,
    }
    if plan.status != 'PLANNED':
        return result
    if plan.legacy_removals:
        result['status'] = 'BLOCKED'
        result['problems'].append('legacy file deletion is not authorized; old roots are kept')
        return result
    # Nothing to write means nothing to authorize: report the idempotent state
    # without demanding a fresh worktree declaration for a no-op re-run.
    if not plan.writes:
        result['status'] = 'PLANNED'
        return result
    # The proposal must be sealed by a verified package result and its shape
    # must already be one of the two legal write roots. Both checks run before
    # the worktree/fetch boundary so a forged or malformed plan is refused for
    # its own reason instead of an unrelated precondition error.
    if not _binding_matches_plan(plan.source_binding, plan):
        result['status'] = 'BLOCKED'
        result['problems'].append(
            'the plan is not bound to the verified official release it proposes; refusing to write')
        return result
    if plan.mode in ('UPGRADE', 'MIGRATE') and not _sealed_baseline(plan.baseline_binding):
        result['status'] = 'BLOCKED'
        result['problems'].append(
            'the plan is not bound to an authenticated old baseline; refusing to write')
        return result
    if plan.mode == 'MIGRATE' and plan.baseline_binding.kind != 'LEGACY_ROOT':
        result['status'] = 'BLOCKED'
        result['problems'].append(
            'the migration plan is not bound to a legacy-root release; refusing to write')
        return result
    if plan.mode == 'UPGRADE' and plan.baseline_binding.kind != 'ISOLATED':
        result['status'] = 'BLOCKED'
        result['problems'].append(
            'the upgrade plan is not bound to an isolated-layout release; refusing to write')
        return result
    problems = write_set_problems(plan.writes)
    if problems:
        result['status'] = 'BLOCKED'
        result['problems'] = list(result['problems']) + problems
        return result
    # The caller's plan is only a proposal, never source or write authority.
    # Verify the host before fetching, then rebuild the write set from the
    # fixed official client's verified bytes within this write boundary.
    from . import entry
    platform_problems = _write_backend_problems()
    if platform_problems:
        result['status'] = 'BLOCKED'
        result['problems'].extend(platform_problems)
        return result
    preflight = entry._onboarding_worktree_preflight(
        host, declared_exclusive=declared_exclusive, expected_head=expected_head)
    if preflight.get('status') != 'OBSERVED':
        result['status'] = 'BLOCKED'
        result['problems'].append('dedicated worktree, HEAD or exclusivity declaration invalid')
        return result
    head = preflight['head_sha']
    try:
        before = entry._onboarding_host_observation(host)
        verified = fetch_verified_onboarding_release()
        baseline = None
        if plan.mode in ('UPGRADE', 'MIGRATE'):
            # Re-fetch the authenticated old release through the fixed client
            # inside this write boundary: the caller's plan is not authority.
            baseline = fetch_verified_onboarding_release(
                plan.baseline_binding.release_tag)
            if (baseline.suite != plan.baseline_binding.suite
                    or baseline.format_version != plan.baseline_binding.format_version):
                raise OnboardingError('re-fetched baseline does not match the plan')
        rebuilt = plan_isolated_onboarding(
            host, scenario='isolated', verified_source=verified,
            verified_baseline=baseline)
        after = entry._onboarding_host_observation(host)
        again = entry._onboarding_worktree_preflight(
            host, declared_exclusive=declared_exclusive, expected_head=head)
        if rebuilt.status != 'PLANNED':
            raise OnboardingError(
                '; '.join(rebuilt.problems) or 'the verified write set is no longer writable')
        if (rebuilt.mode != plan.mode or before != after
                or again.get('status') != 'OBSERVED'
                or plan.writes != rebuilt.writes):
            raise OnboardingError('official write set or host preconditions changed')
        if plan.mode in ('UPGRADE', 'MIGRATE') and not _baseline_binding_matches(
                plan.baseline_binding, rebuilt.baseline_binding):
            raise OnboardingError('authenticated baseline changed between planning and apply')
    except (OSError, OnboardingError, ValueError) as exc:
        result['status'] = 'BLOCKED'
        result['problems'].append(str(exc))
        return result
    plan = rebuilt
    plan.worktree_head = head
    plan.exclusive_writer = 'DECLARED_NOT_VERIFIED'
    result['worktree_head'] = head
    result['exclusive_writer'] = plan.exclusive_writer

    # Freeze the write set before opening anything, so later mutations of the
    # mutable plan object cannot add paths after validation.
    writes = {rel: bytes(data) for rel, data in plan.writes.items()}
    problems = write_set_problems(writes)
    if problems:
        result['status'] = 'BLOCKED'
        result['problems'] = list(result['problems']) + problems
        return result
    if not writes:
        result['status'] = 'PLANNED'
        return result
    # Metadata that cannot be proven to survive an in-place append stays
    # blocked before the transaction opens any path or takes a backup.
    metadata_problems: list[str] = []
    for rel in sorted(writes):
        if plan.originals.get(rel) is None:
            continue
        metadata_problems.extend(_target_metadata_problems(host, rel))
    if metadata_problems:
        result['status'] = 'BLOCKED'
        result['problems'] = list(result['problems']) + metadata_problems
        return result

    if precondition is not None:
        try:
            accepted = precondition()
        except Exception as exc:  # noqa: BLE001 - external precondition
            result['status'] = 'BLOCKED'
            result['problems'].append(f'precondition could not be evaluated: {type(exc).__name__}')
            return result
        if accepted is not True:
            result['status'] = 'BLOCKED'
            result['problems'].append('host preconditions changed before writing')
            return result

    try:
        session, session_problems = _write_session(plan)
    except (OSError, OnboardingError) as exc:
        result['status'] = 'BLOCKED'
        result['problems'].append(str(exc))
        return result
    if session is None:
        result['status'] = 'BLOCKED'
        result['problems'].extend(session_problems)
        return result

    records: list[dict] = []
    created_dirs: list[dict] = []
    failure: BaseException | None = None
    try:
        try:
            session.ensure_target(plan)
            session.verify_originals(plan)
            _invoke_hook(fault_injector, 'before_backup')
            backup = create_backup(
                host, sorted(writes), now=now,
                metadata={'kind': 'isolated_onboarding', 'mode': plan.mode,
                          'target_version': plan.version})
            result['backup_location'] = str(backup.location)
            if not backup.verified:
                raise TransactionConflict('backup verification failed')
            # Re-check immediately before the first write. A backup record is an
            # audit trail only; it never authorizes the recovery below.
            final_preflight = entry._onboarding_worktree_preflight(
                host, declared_exclusive=declared_exclusive, expected_head=head)
            if (final_preflight.get('status') != 'OBSERVED'
                    or entry._onboarding_host_observation(host) != before):
                raise TransactionConflict('worktree or host changed before writing')
            if precondition is not None and precondition() is not True:
                raise TransactionConflict('host preconditions changed before writing')
            session.ensure_target(plan)
            session.verify_originals(plan)
        except BaseException as exc:  # noqa: BLE001 - no write has happened yet
            failure = exc
            result['status'] = 'BLOCKED'
            result['problems'].append(str(exc))
            result['rollback'] = {'required': False, 'attempted': False,
                                  'result': 'NOT_NEEDED'}
            return result

        ordered = sorted(rel for rel in writes if rel != ALLOWED_ROOT_ENTRY)
        if ALLOWED_ROOT_ENTRY in writes:
            ordered.append(ALLOWED_ROOT_ENTRY)

        failure_phase = 'WRITE'
        try:
            for rel in ordered:
                _invoke_hook(fault_injector, 'before_write', rel)
                data = writes[rel]
                original = plan.originals.get(rel)
                if original is None:
                    kind = 'create'
                elif rel == ALLOWED_ROOT_ENTRY:
                    kind = 'append' if data.startswith(original) else 'inplace'
                else:
                    kind = 'replace'
                # Record intent before touching the path so a failure in the
                # middle of the first write is still recoverable.
                records.append({'rel': rel, 'kind': kind, 'expected': data,
                                'original': original})
                if kind == 'create':
                    session.write_new_file(rel, data, created_dirs=created_dirs,
                                           record=records[-1])
                elif kind == 'append':
                    session.append(rel, original, data)
                elif kind == 'inplace':
                    session.rewrite(rel, original, data)
                else:
                    session.replace_file(rel, data, original)
                result['written'].append(rel)
                _invoke_hook(fault_injector, 'after_write', rel)

            failure_phase = 'FINALIZE'
            _invoke_hook(fault_injector, 'before_finalize')
            for record in records:
                temp_rel = record.get('temp_rel')
                if temp_rel:
                    current, _ = session.read_file(temp_rel)
                    if current is not None:
                        raise TransactionConflict('temporary file reappeared before finalization')
            for rel, data in writes.items():
                current, _ = session.read_file(rel)
                if current is None or _raw_sha(current) != _raw_sha(data):
                    raise TransactionConflict(f'post-write hash mismatch: {rel}')
            expected_tree = {rel for rel in writes if rel.startswith(f'{AGENTORCH_DIRNAME}/')}
            if plan.mode == 'UPGRADE' and plan.baseline_binding is not None:
                # An upgrade writes only the differing files; every authenticated
                # baseline file legitimately remains in place.
                expected_tree |= {f'{AGENTORCH_DIRNAME}/{rel}'
                                  for rel in plan.baseline_binding.installed_sha256}
            if expected_tree:
                actual_tree = session.tree_files()
                unexpected = sorted(actual_tree - expected_tree)
                if unexpected:
                    raise TransactionConflict(
                        'unexpected files appeared under agentorch/: '
                        + ', '.join(unexpected[:5]))
                missing = sorted(expected_tree - actual_tree)
                if missing:
                    raise TransactionConflict(
                        'expected files are missing under agentorch/: '
                        + ', '.join(missing[:5]))
        except BaseException as exc:  # noqa: BLE001 - every failure must roll back
            failure = exc
            result['failure'] = {'phase': failure_phase, 'kind': type(exc).__name__,
                                 'errno': exc.errno if isinstance(exc, OSError) else None}
    finally:
        try:
            if failure is not None and not records and result['status'] == 'PLANNED':
                result['status'] = 'BLOCKED'
                result['problems'].append(str(failure))
                result['rollback'] = {'required': False, 'attempted': False,
                                      'result': 'NOT_NEEDED'}
            if failure is not None and records:
                rollback = _rollback_records(session, records, created_dirs,
                                             fault_injector)
                _invoke_hook(fault_injector, 'after_restore', host)
                result['rollback'] = {'required': True, 'attempted': True, **rollback}
                if rollback.get('result') == 'SUCCEEDED':
                    result['status'] = 'ROLLED_BACK'
                    result['written'] = []
                    result['removed'] = []
                else:
                    result['status'] = 'BLOCKED'
                    result['problems'] = list(result['problems']) + [
                        f'rollback conflict preserved: {item}'
                        for item in (rollback.get('conflicts') or [])[:5]]
                    result['problems'] = list(result['problems']) + [
                        f'rollback failure: {item}'
                        for item in (rollback.get('failures') or [])[:5]]
                result['created_dirs'] = sorted({item['rel'] for item in created_dirs})
        finally:
            session.close()

    if failure is not None:
        if not isinstance(failure, Exception):
            raise failure
        return result

    result['status'] = 'APPLIED'
    result['created_dirs'] = sorted({item['rel'] for item in created_dirs})
    return result


def _replace_file_at_for_write(root_fd: int, rel: str, data: bytes,
                               original: bytes) -> None:
    """Replace an existing regular file only after confirming its old bytes."""
    if rel == ALLOWED_ROOT_ENTRY:
        raise TransactionConflict(
            'root AGENTS.md replacement disabled: metadata preservation is not established')
    current, info = _read_file_at(root_fd, rel)
    if current is None or current != original or info is None:
        raise TransactionConflict(f'{rel}: changed before write')
    parent, name = _walk_parent_fd(root_fd, rel, create=False)
    if parent is None:
        raise TransactionConflict(f'{rel}: target disappeared before write')
    temp = f'.agentorch-write-{os.getpid()}-{secrets.token_hex(8)}'
    fd = None
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW
                     | _O_CLOEXEC, stat.S_IMODE(info.st_mode), dir_fd=parent)
        _write_all(fd, data)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.rename(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temp, dir_fd=parent)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        os.close(parent)


def rollback_isolated_onboarding(result: dict) -> dict:
    """Explicit cross-process rollback is not authorized yet.

    A caller-supplied ``backup.json`` (including self-recorded hashes) is not
    bound to this host or to the operation that wrote the files. Treating it as
    authority could delete or overwrite host data, so the operation stays
    ``BLOCKED`` until an independently verifiable binding exists. In-process
    failure recovery uses :func:`apply_isolated_onboarding`'s recorded originals
    instead.
    """
    return {'status': 'BLOCKED', 'problems': [
        'cross-process rollback backup ownership and host binding cannot be '
        'independently verified; the operation is not authorized']}
