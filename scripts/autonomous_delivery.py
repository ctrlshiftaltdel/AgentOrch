"""Prepare-only delivery runner: execute, verify and independently review.

This slice deliberately has **no automatic merge**. It runs one real OpenCode
execution session per task, verifies the diff with the plan's commands, starts a
different session with a declared read-only agent for review, and leaves the
task in ``PREPARED`` for a human or the Master to review and merge. Every write path is
fail-closed:

* a plan may not ask for a merge; ``merge``/``--merge-into`` is refused before
  any agent runs, because automatic merge, its authoritative task lock, atomic
  recovery and independent-authorization binding are not implemented here;
* pending approvals are re-proved from Git and from the reviewer's own session
  transcript before they are trusted; the private journal and its plan hash are
  not authority (they are copyable and recomputable);
* resume/retry re-prove the repository identity, the task packet, project
  authorization (including every *actual* changed path), the execution session,
  the reviewer profile and the full verification set before any side effect;
  the runner also appends a binding trailer commit that ties the session to the
  task packet, base, head and diff, so a hand-made commit cannot be resumed;
* no ``reset``, ``stash`` or delete is ever performed; a worktree that is not
  exactly at the target baseline, or that has uncommitted/committed leftovers,
  is refused and left untouched;
* verification runs in its own process group so a timeout kills the whole tree.

Nothing here is a sandbox. Worktrees, prompts, path checks and ``--standalone``
do not restrict OpenCode's configured permissions: V2 can reach the host
filesystem, shell, network and credentials. Tool-level denials are checked, but
OS isolation, MCP/plugin channels and model independence are not proven. Only
the standard library is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import tempfile

STATE_VERSION = 4
TERMINAL_OK = 'PREPARED'
# A dependency is only satisfied by an integrated result. This prepare-only
# slice never produces one, so dependent tasks stay PENDING instead of building
# on a target that does not contain the dependency's change.
INTEGRATED = ('VERIFIED',)
ACTIVE = ('RUNNING', 'PREPARED')
STOPPED = ('FAILED', 'REVIEW_REJECTED', 'BLOCKED', 'UNKNOWN')
BRANCH_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')
SHA_RE = re.compile(r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
DIGEST_RE = re.compile(r'^[0-9a-f]{64}$')
VERDICT_KEYS = {'verdict', 'head', 'base', 'diff_sha256', 'diff_digest', 'task_digest',
                'paths', 'mode', 'acceptance', 'findings'}
MAX_DIFF_BYTES = 65536
MODE = 'prepare-only'
# The runtime's write-capable tool actions we can name. MCP/plugin channels are
# not enumerable here, so this gate is never presented as an OS read-only proof.
MUTATING_ACTIONS = ('edit', 'shell')
VALID_EDITIONS = ('local', 'github')
EDITION_RE = re.compile(r'^\s*[-*]\s*`?edition`?\s*[:：]\s*(.*?)\s*$', re.M)
BINDING_KEYS = ('AgentOrch-Session', 'AgentOrch-Base', 'AgentOrch-Diff',
                'AgentOrch-Task')
MERGE_REFUSAL = ('automatic merge is not implemented in this slice: it has no authoritative '
                 'task lock, atomic recovery or independent authorization binding. Run with '
                 'merge disabled and let the Master review the PREPARED head')


class DeliveryError(Exception):
    """A gate failed; ``status`` is the state the task is left in."""

    def __init__(self, message, status='BLOCKED'):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- git

def git(where, *args):
    result = subprocess.run(['git', '--no-optional-locks', *args], cwd=where,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=True)
    return result.stdout


def _text(where, *args):
    return os.fsdecode(git(where, *args)).strip()


def _root(path):
    path = Path(path).resolve(strict=True)
    if Path(os.fsdecode(git(path, 'rev-parse', '--show-toplevel')).strip()).resolve() != path:
        raise ValueError('path must be a Git worktree root')
    return path


def _common(path):
    return Path(os.fsdecode(git(path, 'rev-parse', '--path-format=absolute',
                                '--git-common-dir')).strip()).resolve()


def _head(path):
    return _text(path, 'rev-parse', 'HEAD')


def _branch(path):
    return _text(path, 'branch', '--show-current')


def _status(where):
    return os.fsdecode(git(where, 'status', '--porcelain', '-z'))


def _is_ancestor(ancestor, descendant, where):
    result = subprocess.run(['git', '--no-optional-locks', 'merge-base', '--is-ancestor',
                             ancestor, descendant], cwd=where, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


# ------------------------------------------------------------------ project config

def project_config(repo):
    """Read edition and path authorization from the project's own config."""
    path = Path(repo) / 'docs' / 'PROJECT.md'
    if not path.is_file():
        return {'edition': None, 'edition_count': 0, 'edition_values': [],
                'allowed': None, 'forbidden': [], 'secrets': []}
    text = path.read_text(encoding='utf-8', errors='replace')
    values = [_edition_value(match) for match in EDITION_RE.findall(text)]
    return {'edition': values[0] if len(values) == 1 else None,
            'edition_count': len(values), 'edition_values': values,
            'allowed': _policy_paths(text, '允许改动路径'),
            'forbidden': _policy_paths(text, '禁止改动路径'),
            'secrets': _secret_patterns(text)}


def _edition_value(raw):
    """Take the *whole* declared value; a prefix or trailing text is invalid.

    Only a value wrapped in a matching pair of backticks is unwrapped. Anything
    else (``github/invalid``, ``???``, an empty value, a closed backtick followed
    by prose) stays as-is and is rejected by the callers' exact-value check.
    """
    value = raw.strip()
    if len(value) >= 2 and value.startswith('`') and value.endswith('`'):
        value = value[1:-1]
    return value


def _policy_paths(text, needle):
    for line in text.splitlines():
        if needle in line:
            paths = []
            for token in re.findall(r'`([^`]+)`', line):
                candidate = token.strip().rstrip('/')
                try:
                    paths.append(_safe_rel(candidate))
                except ValueError:
                    continue
            return paths or None
    return None


def _secret_patterns(text):
    for line in text.splitlines():
        if '秘密与隐私文件模式' in line:
            return [token.strip() for token in re.findall(r'`([^`]+)`', line) if token.strip()]
    return []


def _secret_path(name, patterns):
    import fnmatch
    basename = name.rsplit('/', 1)[-1]
    return any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(basename, pattern)
               for pattern in patterns)


def _overlaps(left, right):
    return (left == right or left.startswith(right.rstrip('/') + '/')
            or right.startswith(left.rstrip('/') + '/'))


def _authorize_paths(paths, config):
    """Refuse a plan outside the project's own written authorization.

    A task path may not sit on, above or below a forbidden path: allowing
    ``docs`` while ``docs/secret.txt`` is forbidden would authorize the secret
    file by ancestor.
    """
    if config['allowed'] is None:
        raise ValueError('cannot prove project authorization: docs/PROJECT.md has no '
                         'backticked 允许改动路径 list')
    for name in paths:
        if _secret_path(name, config.get('secrets') or []):
            raise ValueError(f'path {name!r} matches a project secret/private pattern')
        for forbidden in config['forbidden']:
            if _overlaps(name, forbidden):
                raise ValueError(f'path {name!r} overlaps the forbidden path {forbidden!r} '
                                 'in docs/PROJECT.md')
        if not any(name == allowed or name.startswith(allowed + '/')
                   for allowed in config['allowed']):
            raise ValueError(f'path {name!r} is not authorized by docs/PROJECT.md')


def _authorize_changed(name, allowed_paths, config):
    """Every actual changed path is checked, not only the declared prefixes."""
    if _secret_path(name, config.get('secrets') or []):
        raise DeliveryError(f'privacy boundary: {name} matches a project secret/private '
                            'pattern and cannot be reviewed or committed')
    _check_changed_path_lexical(name, allowed_paths)
    for forbidden in config['forbidden']:
        if _overlaps(name, forbidden):
            raise DeliveryError(f'scope violation: {name} overlaps the project-forbidden '
                                f'path {forbidden}')


# ------------------------------------------------------------------- path safety

def _safe_rel(name):
    """Reject every relative path that is ambiguous across platforms."""
    if not isinstance(name, str) or not name:
        raise ValueError('empty path')
    if '\x00' in name:
        raise ValueError('path contains a NUL byte')
    if '\\' in name:
        raise ValueError(f'path {name!r} contains a backslash separator')
    if re.match(r'^[A-Za-z]:', name):
        raise ValueError(f'path {name!r} is an absolute drive path')
    if name.startswith('/'):
        raise ValueError(f'path {name!r} is absolute')
    if ':' in name:
        raise ValueError(f'path {name!r} contains an ambiguous colon')
    if any(char.isspace() for char in name):
        raise ValueError(f'path {name!r} contains whitespace')
    if any(char in name for char in '*?['):
        raise ValueError(f'path {name!r} contains a glob character')
    parts = name.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError(f'path {name!r} contains an empty, "." or ".." component')
    if any(part in ('.git', '.orchestration') for part in parts):
        raise ValueError(f'path {name!r} is a Git or orchestration-state path')
    return name


def _allowed(name, paths):
    _safe_rel(name)
    return any(name == allowed or name.startswith(allowed.rstrip('/') + '/')
               for allowed in paths)


def _allowed_paths(values):
    return [_safe_rel(value.rstrip('/')) for value in values]


def _branch_name(value):
    if not isinstance(value, str) or not BRANCH_RE.match(value):
        raise ValueError(f'invalid branch name {value!r}')
    if '..' in value or value.endswith(('.lock', '.', '/')) or value.startswith('/'):
        raise ValueError(f'invalid branch name {value!r}')
    return value


def _lstat(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _check_changed_path_lexical(name, allowed):
    _safe_rel(name)
    if not _allowed(name, allowed):
        raise DeliveryError(f'scope violation: {name} is outside the task paths')


def _check_changed_path(work, name, allowed, config=None):
    """Reject a changed path that is not an allowed regular file or deletion.

    The project-forbidden rule is evaluated first and for the *actual* path, so
    a forbidden nested file is reported as forbidden even when a broader task
    prefix would otherwise allow it.
    """
    _safe_rel(name)
    if config is not None:
        if _secret_path(name, config.get('secrets') or []):
            raise DeliveryError(f'privacy boundary: {name} matches a project secret pattern')
        for forbidden in config['forbidden']:
            if _overlaps(name, forbidden):
                raise DeliveryError(f'forbidden path: {name} overlaps the project-forbidden '
                                    f'path {forbidden}')
    _check_changed_path_lexical(name, allowed)
    import stat as _stat
    current = work
    for part in PurePosixPath(name).parts:
        current = current / part
        info = _lstat(current)
        if info is None:
            return  # deletion, or a directory that no longer exists
        if _stat.S_ISLNK(info.st_mode):
            raise DeliveryError(f'symlink change cannot be safely checked: {name}')
        if current != work / name and not _stat.S_ISDIR(info.st_mode):
            return
    info = _lstat(work / name)
    if info is None:
        return
    if not (_stat.S_ISREG(info.st_mode) or _stat.S_ISDIR(info.st_mode)):
        raise DeliveryError(f'changed path is not a regular file: {name}')
    if config is not None:
        _authorize_changed(name, allowed, config)


def _changes(path, base):
    tracked = git(path, 'diff', '--no-ext-diff', '--no-renames', '--name-only', '-z', base, '--')
    tracked += git(path, 'diff', '--cached', '--no-ext-diff', '--no-renames',
                   '--name-only', '-z', base, '--')
    untracked = git(path, 'ls-files', '--others', '--exclude-standard', '-z')
    return sorted(set(os.fsdecode(x) for x in (tracked + untracked).split(b'\0') if x))


def _fingerprint(path, base):
    """Digest of the worktree state vs ``base``: content, type, mode and status."""
    import stat as _stat
    digest = hashlib.sha256()
    digest.update(git(path, 'status', '--porcelain=v1', '-z'))
    digest.update(git(path, 'diff', '--no-ext-diff', '--binary', '--no-renames', base, '--'))
    digest.update(git(path, 'diff', '--cached', '--no-ext-diff', '--binary',
                      '--no-renames', base, '--'))
    for name in _changes(path, base):
        file = path / name
        digest.update(os.fsencode(name) + b'\0')
        info = _lstat(file)
        if info is None:
            digest.update(b'M')
        elif _stat.S_ISLNK(info.st_mode):
            digest.update(b'L' + oct(info.st_mode).encode() + b'\0'
                          + os.fsencode(os.readlink(file)))
        elif _stat.S_ISREG(info.st_mode):
            digest.update(b'F' + oct(info.st_mode).encode() + b'\0' + file.read_bytes())
        else:
            digest.update(b'X' + oct(info.st_mode).encode())
    return digest.hexdigest()


def _bind_session(work, base, session, digest, packet):
    """Record the execution session on the branch as durable repository state.

    The journal is copyable and its hashes are recomputable, so a journal field
    alone cannot show that this session produced this head for this task packet.
    The runner writes an empty trailer commit *after* the work is committed;
    resume, retry and approval re-proof read it from Git. A hand-made commit, or
    a session bound to another packet, cannot carry it.
    """
    body = (f'AgentOrch-Session: {session}\nAgentOrch-Base: {base}\n'
            f'AgentOrch-Diff: {digest}\nAgentOrch-Task: {packet}\n')
    subprocess.run(['git', '--no-optional-locks', 'commit', '--allow-empty',
                    '--no-gpg-sign', '-m', 'AgentOrch: bind the execution session',
                    '-m', body], cwd=work, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)


def _session_binding(work, base, head, session, digest, packet):
    """Name the trailer field that does not match, or ``None`` when all match."""
    if not isinstance(session, str) or not session:
        return 'session'
    if not isinstance(base, str) or not base or not isinstance(head, str) or not head:
        return 'base/head'
    if not isinstance(digest, str) or not digest:
        return 'diff'
    if not isinstance(packet, str) or not packet:
        return 'task packet'
    try:
        body = os.fsdecode(git(work, 'log', '-1', '--format=%B', head))
    except subprocess.SubprocessError:
        return 'head'
    wanted = {'AgentOrch-Session': session, 'AgentOrch-Base': base,
              'AgentOrch-Diff': digest, 'AgentOrch-Task': packet}
    found = {}
    for line in body.splitlines():
        for key in BINDING_KEYS:
            if line.startswith(key + ':'):
                found[key] = line.split(':', 1)[1].strip()
    for key in BINDING_KEYS:
        if found.get(key) != wanted[key]:
            return 'task packet' if key == 'AgentOrch-Task' else key.rsplit('-', 1)[1].lower()
    return None


# ------------------------------------------------------------------- plan input

def _task_spec(raw, known_ids):
    """Validate one task; return a spec whose ``errors`` are isolated per task."""
    errors = []
    if not isinstance(raw, dict):
        raise ValueError('a task must be a mapping')
    ident = raw.get('id')
    if not isinstance(ident, str) or not ident:
        raise ValueError('task ids must be nonempty strings')
    worktree, prompt = raw.get('worktree'), raw.get('prompt')
    paths, verify, depends = raw.get('paths'), raw.get('verify'), raw.get('depends')
    if not isinstance(worktree, str) or not worktree:
        errors.append('task worktree required')
    if not isinstance(prompt, str) or not prompt.strip():
        errors.append('task prompt required')
    allowed = []
    if not isinstance(paths, list) or not paths:
        errors.append('task paths must be a nonempty list')
    else:
        for value in paths:
            allowed.append(_safe_rel(value.rstrip('/')))
    commands = []
    if not isinstance(verify, list) or not verify:
        errors.append('verification must be a nonempty list of argv lists')
    else:
        for command in verify:
            if (not isinstance(command, list) or not command
                    or any(not isinstance(token, str) or not token for token in command)):
                errors.append('verification entries must be nonempty argv lists')
            else:
                commands.append(list(command))
    acceptance = raw.get('acceptance')
    criteria = []
    if (not isinstance(acceptance, list) or not acceptance
            or any(not isinstance(item, str) or not item.strip() for item in acceptance)):
        errors.append('task acceptance criteria required (nonempty list of strings)')
    else:
        criteria = [item.strip() for item in acceptance]
    if not isinstance(depends, list) or any(not isinstance(dep, str) for dep in depends):
        errors.append('depends must be a list of task ids')
        depends = []
    for dep in depends:
        if dep not in known_ids or dep == ident:
            raise ValueError(f'task {ident} has an unknown or self dependency {dep}')
    return {'id': ident, 'raw': raw, 'worktree': worktree if isinstance(worktree, str) else '',
            'prompt': prompt if isinstance(prompt, str) else '',
            'paths': allowed, 'verify': commands, 'depends': depends,
            'acceptance': criteria, 'errors': errors}


def _plan_tasks(plan):
    if not isinstance(plan, dict):
        raise ValueError('plan must be a mapping')
    if plan.get('merge') or plan.get('merge_into'):
        raise DeliveryError(MERGE_REFUSAL)
    tasks = plan.get('tasks')
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('plan needs a nonempty tasks list')
    ids = set()
    for raw in tasks:
        ident = raw.get('id') if isinstance(raw, dict) else None
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError('task ids must be nonempty and unique')
        ids.add(ident)
    specs = [_task_spec(raw, ids) for raw in tasks]
    graph = {spec['id']: list(spec['depends']) for spec in specs}
    seen, stack = set(), set()

    def visit(node):
        if node in stack:
            raise ValueError(f'dependency cycle through {node}')
        if node in seen:
            return
        stack.add(node)
        for dep in graph[node]:
            visit(dep)
        stack.discard(node)
        seen.add(node)

    for node in graph:
        visit(node)
    absolute = [Path(spec['worktree']).absolute() for spec in specs if spec['worktree']]
    if len(set(absolute)) != len(absolute):
        raise ValueError('two tasks share one worktree')
    return specs


def _load_plan_file(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def gh_run(argv):
    """Default GitHub CLI runner: capture output, never a shell."""
    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=120)


def _gh_json(args, runner):
    result = runner(['gh', *args])
    if result.returncode != 0:
        raise DeliveryError(f'gh {" ".join(args)} failed with exit code {result.returncode}')
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise DeliveryError(f'gh returned unreadable JSON: {exc}') from exc
    if not isinstance(data, dict):
        raise DeliveryError('gh returned an unexpected payload')
    return data


def _section(text, heading):
    match = re.search(r'^##+\s+' + re.escape(heading) + r'\s*$', text, re.MULTILINE)
    if not match:
        return None
    body = text[match.end():]
    stop = re.search(r'^##+\s+', body, re.MULTILINE)
    return body[:stop.start()] if stop else body


def load_issue_plan(number, repo, *, worktree, verify, acceptance, allows=(), instruction='',
                    runner=gh_run):
    """Build a prepare-only plan from the live Issue, not a hand-copied snapshot."""
    if not isinstance(number, int) or number < 1:
        raise ValueError('issue number must be a positive integer')
    if not acceptance:
        raise ValueError('--acceptance is required: an issue plan needs stated criteria')
    data = _gh_json(['issue', 'view', str(number), '--repo', repo,
                     '--json', 'number,title,body,state,url,labels'], runner)
    if data.get('state') != 'OPEN':
        raise DeliveryError(f'carrier issue #{number} is {data.get("state")}, not OPEN')
    body = data.get('body') or ''
    section = _section(body, '可改路径')
    if section is None:
        raise DeliveryError(f'carrier issue #{number} has no 可改路径 section')
    candidates = []
    for token in re.findall(r'`([^`\n]+)`', section):
        try:
            candidates.append(_safe_rel(token.strip().rstrip('/')))
        except ValueError:
            continue  # prose, commands and idioms are not paths
    if not candidates:
        raise DeliveryError(f'carrier issue #{number} lists no usable paths')
    if allows:
        narrowed = _allowed_paths(allows)
        for allow in narrowed:
            if not any(allow == item or allow.startswith(item.rstrip('/') + '/')
                       for item in candidates):
                raise DeliveryError(f'narrowed path {allow} is not inside the issue scope')
        paths = narrowed
    else:
        paths = candidates
    commands = []
    for value in verify:
        command = json.loads(value)
        if (not isinstance(command, list) or not command
                or any(not isinstance(token, str) or not token for token in command)):
            raise ValueError('--verify must be a nonempty JSON argv list')
        commands.append(command)
    if not commands:
        raise ValueError('--verify is required: this tool never invents a check')
    prompt = f'Issue #{number}: {data.get("title", "")}\n\n{body}'
    if instruction:
        prompt += f'\n\nAdditional instruction from the operator:\n{instruction}'
    carrier = {'kind': 'issue', 'repo': repo, 'number': number,
               'url': data.get('url'), 'digest': hashlib.sha256(body.encode()).hexdigest(),
               'labels': sorted(label.get('name', '') for label in data.get('labels') or [])}
    return {'carrier': carrier, 'merge': False, 'tasks': [{
        'id': f'issue-{number}', 'worktree': worktree, 'prompt': prompt,
        'paths': paths, 'verify': commands, 'acceptance': list(acceptance), 'depends': []}]}


def verify_carrier(carrier, runner=gh_run):
    """Re-read the live carrier before work starts; a stale snapshot fails closed."""
    if not isinstance(carrier, dict) or carrier.get('kind') != 'issue':
        raise ValueError('carrier must be a mapping of kind "issue"')
    number, repo = carrier.get('number'), carrier.get('repo')
    digest = carrier.get('digest')
    if not isinstance(number, int) or not isinstance(repo, str) or not repo:
        raise ValueError('carrier needs an integer number and a repo')
    if not isinstance(digest, str) or not DIGEST_RE.match(digest):
        raise ValueError('carrier digest must be a sha256 hex digest')
    data = _gh_json(['issue', 'view', str(number), '--repo', repo,
                     '--json', 'state,body,url'], runner)
    if data.get('state') != 'OPEN':
        raise DeliveryError(f'carrier issue #{number} is not open')
    if hashlib.sha256((data.get('body') or '').encode()).hexdigest() != digest:
        raise DeliveryError('carrier issue changed after the plan was written')
    return data.get('url')


# ------------------------------------------------------------------- journal I/O

def _save(path, state):
    fd, tmp = tempfile.mkstemp(prefix='.delivery-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(state, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _lock(path):
    """Exclusive non-blocking lock; lazy import keeps Windows importable."""
    handle = open(path, 'a+b')
    try:
        try:
            import fcntl
        except ImportError:
            try:
                import msvcrt
            except ImportError:
                raise DeliveryError('no cross-process file lock on this platform; '
                                    'refusing to run') from None
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise DeliveryError(f'resource is locked by another runner: {exc}') from None
    except DeliveryError:
        handle.close()
        raise
    return handle


def _lock_dir(repo):
    """Stable per-repository lock directory, independent of TMPDIR."""
    directory = _common(repo) / 'agentorch-delivery-locks'
    directory.mkdir(exist_ok=True)
    return directory


def lock_targets(repo, state_path, specs):
    """Every worktree and journal the run writes must be exclusively ours."""
    directory = _lock_dir(repo)
    targets = {'repo', 'journal'}
    for spec in specs:
        if spec.get('worktree'):
            targets.add(str(Path(spec['worktree']).absolute()))
    return directory, sorted(targets)


def acquire_locks(repo, state_path, specs):
    directory, targets = lock_targets(repo, state_path, specs)
    journal = Path(state_path).absolute()
    journal_lock = journal.parent / (
        '.agentorch-delivery-' + hashlib.sha256(str(journal).encode()).hexdigest()[:24]
        + '.lock')
    keys = [('repo', str(_common(repo)))]
    for value in targets:
        if value not in ('repo', 'journal'):
            keys.append(('worktree', value))
    handles = []
    try:
        paths = [journal_lock]
        for kind, value in sorted(keys):
            key = hashlib.sha256((kind + '\0' + value).encode()).hexdigest()[:24]
            paths.append(directory / f'{key}.lock')
        for path in sorted(set(paths)):
            handles.append(_lock(path))
    except BaseException:
        for handle in handles:
            handle.close()
        raise
    return handles


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == 'nt':  # os.kill would terminate the process on Windows
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


# ---------------------------------------------------------------- OpenCode run

def _classify_stream(returncode, output):
    """Accept only a single-session stream that ends with a clean stop.

    Real tool-using runs contain several steps: an intermediate step_finish
    carries ``tool-calls`` and is followed by another step_start. Only a
    ``stop`` finish is terminal, and nothing may follow it.
    """
    unknown = {'exit_code': None, 'session_id': None, 'text': '',
               'detail': 'stream is not a completed single-session run'}
    if returncode != 0:
        return dict(unknown, detail=f'OpenCode exit code {returncode}')
    if not output.strip():
        return dict(unknown, detail='OpenCode produced no events')
    session, text = None, []
    started = terminal = False
    in_step = False
    try:
        for line in output.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or terminal:
                return dict(unknown, detail='event after the terminal stop')
            sid = event.get('sessionID')
            if not isinstance(sid, str) or not sid.startswith('ses_'):
                return dict(unknown, detail='event without a usable session id')
            if session is not None and session != sid:
                return dict(unknown, detail='more than one session in the stream')
            session = sid
            part = event.get('part')
            if part is not None and not isinstance(part, dict):
                return dict(unknown, detail='event part is not an object')
            part = part or {}
            if part.get('sessionID') not in (None, sid):
                return dict(unknown, detail='event part names another session')
            kind = event.get('type')
            if kind == 'step_start':
                if in_step:
                    return dict(unknown, detail='step_start inside a running step')
                in_step, started = True, True
            elif kind in ('text', 'tool_use', 'tool'):
                if not in_step:
                    return dict(unknown, detail='output event outside a step')
                if kind == 'text':
                    text.append(part.get('text') or '')
            elif kind == 'step_finish':
                if not in_step:
                    return dict(unknown, detail='step_finish without a running step')
                in_step = False
                reason = part.get('reason')
                if reason == 'stop':
                    terminal = True
                elif reason not in ('tool-calls', 'tool_calls'):
                    return dict(unknown, detail=f'unrecognized finish reason {reason!r}')
            else:
                return dict(unknown, detail=f'unexpected event type {kind!r}')
    except (ValueError, TypeError, AttributeError):
        return dict(unknown, detail='stream is not readable JSON events')
    if not started:
        return dict(unknown, detail='stream never started a step')
    if in_step:
        return dict(unknown, detail='stream ended inside a step')
    if not terminal:
        return dict(unknown, detail='stream ended without a stop finish')
    return {'exit_code': returncode, 'session_id': session, 'text': ''.join(text),
            'detail': 'ok'}


def _kill_group(process):
    try:
        if os.name == 'posix':
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (OSError, AttributeError):
        pass


def _popen(argv, **kwargs):
    return subprocess.Popen(argv, **kwargs)


def opencode(worktree, prompt, role, *, agent_name=None, timeout=1800):
    """Invoke one real session; no --auto, no session continuation."""
    argv = ['opencode', 'run', '--standalone', '--format', 'json']
    if agent_name:
        argv += ['--agent', agent_name]
    argv.append(prompt)
    environment = dict(os.environ, PWD=str(worktree))
    try:
        process = _popen(argv, cwd=worktree, stdin=subprocess.DEVNULL, env=environment,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                         start_new_session=(os.name == 'posix'))
    except OSError:
        return {'exit_code': None, 'session_id': None, 'text': '', 'detail': 'spawn failed'}
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        return {'exit_code': None, 'session_id': None, 'text': '', 'timeout': True,
                'detail': 'timed out; the process group was killed'}
    return _classify_stream(process.returncode, output or '')


def session_export(session_id, worktree, timeout=120):
    """Read a session's own terminal record; this is identity, not success proof.

    The export is not sanitized because sanitizing redacts the working directory
    and the assistant text, which are the identity and verdict facts this gate
    needs. Only metadata is persisted; the transcript stays in memory.
    """
    argv = ['opencode', 'session', 'export', session_id]
    try:
        result = subprocess.run(argv, cwd=worktree, stdin=subprocess.DEVNULL,
                                env=dict(os.environ, PWD=str(worktree)),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'ok': False, 'reason': f'session export unavailable: {exc}'}
    if result.returncode != 0:
        return {'ok': False, 'reason': f'session export exit code {result.returncode}'}
    try:
        data = json.loads(result.stdout)
        info = data['info']
        messages = data['messages']
    except (ValueError, KeyError, TypeError):
        return {'ok': False, 'reason': 'session export is not readable JSON'}
    if not isinstance(info, dict) or not isinstance(messages, list):
        return {'ok': False, 'reason': 'session export has an unexpected shape'}
    assistants = [message for message in messages
                  if isinstance(message, dict) and message.get('type') == 'assistant']
    texts = []
    for message in assistants:
        for part in message.get('content') or []:
            if isinstance(part, dict) and part.get('type') == 'text':
                texts.append(part.get('text') or '')
    directory = (info.get('location') or {}).get('directory')
    return {'ok': True, 'session': info.get('id'), 'outcome': info.get('outcome'),
            'directory': directory,
            'user_messages': sum(1 for message in messages
                                 if isinstance(message, dict) and message.get('type') == 'user'),
            'assistant_messages': len(assistants),
            'agents': [message.get('agent') for message in assistants],
            'finishes': [message.get('finish') for message in assistants],
            'models': [message.get('model') or {} for message in assistants],
            'texts': texts}


def agent_profiles(worktree, timeout=120):
    argv = ['opencode', 'debug', 'agents']
    try:
        result = subprocess.run(argv, cwd=worktree, stdin=subprocess.DEVNULL,
                                env=dict(os.environ, PWD=str(worktree)),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError(f'agent profiles unavailable: {exc}') from None
    if result.returncode != 0:
        raise DeliveryError(f'agent profile query exit code {result.returncode}')
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise DeliveryError(f'agent profile query is not JSON: {exc}') from None
    if not isinstance(data, list):
        raise DeliveryError('agent profile query returned an unexpected shape')
    return {item.get('id'): item for item in data if isinstance(item, dict)}


def _rule_matches(rule, action):
    """OpenCode matches action patterns as globs; '*' and 's*' both cover shell."""
    import fnmatch
    pattern = rule.get('action')
    if not isinstance(pattern, str):
        return False
    return pattern == '*' or fnmatch.fnmatchcase(action, pattern) or pattern == action


def _covers_mutating(rule):
    return any(_rule_matches(rule, action) for action in MUTATING_ACTIONS)


def profile_gate(profile):
    """Fail-closed tool-level read-only check for a reviewer profile.

    Rules are evaluated in order with the runtime's last-match semantics for the
    wildcard resource. Any allow rule for a mutating action on a scoped resource
    (even if an earlier wildcard deny exists) is unproven, because this runner
    cannot enumerate the resources that rule can reach.
    """
    rules = (profile or {}).get('permissions') or []
    if not isinstance(rules, list):
        return {'ready': False, 'missing': ['permissions'], 'scoped_allows': [],
                'effective': {}, 'reasons': ['profile permissions are not a list']}
    effective, scoped_allows = {}, []
    for action in MUTATING_ACTIONS:
        effect = None
        for rule in rules:
            if isinstance(rule, dict) and _rule_matches(rule, action) \
                    and rule.get('resource') == '*':
                effect = rule.get('effect')
        effective[action] = effect
    for rule in rules:
        if not isinstance(rule, dict) or rule.get('effect') != 'allow':
            continue
        resource = rule.get('resource')
        if resource in (None, '*'):
            continue
        # Any allow for a pattern that *could* cover edit/shell is unproven. A
        # later identical-scope deny does not make it safe: the runner cannot
        # enumerate what the glob reaches.
        if _covers_mutating(rule):
            scoped_allows.append({'action': rule.get('action'), 'resource': resource})
    missing = [action for action in MUTATING_ACTIONS if effective.get(action) != 'deny']
    reasons = []
    if missing:
        reasons.append('effective rule does not deny ' + ', '.join(missing))
    if scoped_allows:
        reasons.append('scoped allow rules cannot be proven read-only: '
                       + ', '.join(f"{item['action']}:{item['resource']}"
                                   for item in scoped_allows))
    return {'ready': not reasons, 'missing': missing, 'scoped_allows': scoped_allows,
            'effective': effective, 'reasons': reasons}


# ------------------------------------------------------------------ review gate

def _verdict(text):
    """Last ```json block with the exact verdict shape, or ``None``."""
    blocks = re.findall(r'```json\s*\n(.*?)```', text or '', re.DOTALL)
    for block in reversed(blocks):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and VERDICT_KEYS.issubset(data):
            return data
    return None


def _acceptance_ok(verdict, criteria):
    entries = verdict.get('acceptance')
    expected_ids = [f'AC-{index}' for index in range(1, len(criteria) + 1)]
    if not isinstance(entries, list) or len(entries) != len(criteria):
        return False, 'acceptance verdict does not cover every criterion'
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return False, 'acceptance verdict entry is malformed'
        ident, state = entry.get('id'), entry.get('status')
        if ident not in expected_ids or ident in ids:
            return False, f'acceptance verdict names an unknown or repeated criterion {ident!r}'
        ids.add(ident)
        if entry.get('criterion') != criteria[int(ident.split('-')[1]) - 1]:
            return False, f'{ident} does not quote the criterion text it was given'
        if state != 'PASS':
            return False, f'{ident} is {state!r}, not PASS'
        if not str(entry.get('evidence') or '').strip():
            return False, f'{ident} has no evidence'
    return True, ''


def _verdict_matches(verdict, record, spec, context):
    if verdict.get('verdict') != 'APPROVE':
        return f'review verdict is {verdict.get("verdict")!r}, not APPROVE'
    if verdict.get('head') != record['head'] or verdict.get('base') != record.get('base'):
        return 'review verdict binds a different base or head'
    if verdict.get('diff_sha256') != record['diff_sha256']:
        return 'review verdict binds a different worktree diff'
    if verdict.get('diff_digest') != record.get('diff_digest'):
        return 'review verdict binds a different reviewed diff'
    if verdict.get('task_digest') != _task_digest(context, spec, record):
        return 'review verdict binds a different task packet'
    if verdict.get('mode') != MODE:
        return f'review verdict mode is {verdict.get("mode")!r}, not {MODE!r}'
    if sorted(verdict.get('paths') or []) != sorted(spec['paths']):
        return 'review verdict binds a different allowed-path set'
    if verdict.get('findings'):
        return 'review approved while listing findings'
    accepted, why = _acceptance_ok(verdict, spec['acceptance'])
    if not accepted:
        return f'review did not pass every acceptance criterion: {why}'
    return None


# --------------------------------------------------------------------- runner

class _Context:
    def __init__(self, repo, plan, specs, state, agent, session_probe, profiles,
                 state_saver, config, state_path):
        self.repo = repo
        self.state_path = state_path
        self.plan = plan
        self.specs = specs
        self.state = state
        self.agent = agent
        self.session_probe = session_probe
        self.profiles = profiles
        self.save = state_saver
        self.config = config
        self.auth = {}
        self._profiles = {}
        self._reviewer_ready = {}
        self.order = {spec['id']: index for index, spec in enumerate(specs)}

    def record(self, ident):
        return self.state['tasks'][ident]

    def spec(self, ident):
        return next(spec for spec in self.specs if spec['id'] == ident)

    def reviewer_gate(self, work):
        key = str(Path(work).resolve())
        if key not in self._reviewer_ready:
            if key not in self._profiles:
                self._profiles[key] = self.profiles(work)
            profile = (self._profiles[key] or {}).get(self.plan['reviewer_agent']) or {}
            self._reviewer_ready[key] = profile_gate(profile)
        return self._reviewer_ready[key]


def _check_probe(probe, work, expected_agent, role, status='BLOCKED', require_text=False,
                 session=None):
    def fail(message):
        raise DeliveryError(message, status)

    if not expected_agent:
        fail(f'{role} agent identity is not defined by the current plan')
    if not probe.get('ok'):
        fail(f'{role} session is unverifiable: {probe.get("reason")}')
    if session is not None and probe.get('session') != session:
        fail(f'{role} export did not return the requested session id')
    if probe.get('outcome') != 'succeeded':
        fail(f'{role} session outcome is {probe.get("outcome")!r}, not succeeded')
    directory = probe.get('directory')
    if not directory or os.path.realpath(directory) != os.path.realpath(work):
        fail(f'{role} session ran in {directory!r}, not in the task worktree')
    if probe.get('user_messages', 0) < 1 or probe.get('assistant_messages', 0) < 1:
        fail(f'{role} session has no user/assistant turn')
    if 'stop' not in (probe.get('finishes') or []):
        fail(f'{role} session has no completed assistant turn')
    agents = probe.get('agents') or []
    if not agents:
        fail(f'{role} session export does not name an agent')
    if any(name != expected_agent for name in agents):
        fail(f'{role} session used agent(s) {agents}, not the declared {expected_agent}')
    if require_text and not ''.join(probe.get('texts') or []).strip():
        fail(f'{role} session transcript has no assistant text')


def _run_checks(commands, work, timeout):
    """Run checks in their own process group; a timeout kills the whole tree."""
    checks = []
    for command in commands:
        entry = {'program': command[0], 'argv_sha256':
                 hashlib.sha256(json.dumps(command).encode()).hexdigest()}
        try:
            process = _popen(command, cwd=work, stdin=subprocess.DEVNULL,
                             env=dict(os.environ, PWD=str(work)),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             start_new_session=(os.name == 'posix'))
        except OSError as exc:
            entry.update(exit_code=None, output_tail=f'not run: {exc}')
            checks.append(entry)
            continue
        try:
            output, _ = process.communicate(timeout=timeout)
            entry.update(exit_code=process.returncode,
                         output_tail=(output or '')[-1000:])
        except subprocess.TimeoutExpired:
            _kill_group(process)
            try:
                process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            entry.update(exit_code=None, timeout=True,
                         output_tail='timed out; process group killed')
        checks.append(entry)
    return checks


def _timed_out(record):
    return any(check.get('timeout') for check in (record.get('verification') or []))


def _all_zero(checks):
    return bool(checks) and all(check.get('exit_code') == 0 for check in checks)


def _criteria_prompt(spec):
    return '\n'.join(f'- AC-{index}: {text}'
                     for index, text in enumerate(spec['acceptance'], 1))


def _resolve_task(context, spec):
    """The one identity gate every phase, resume, retry and auth path must pass."""
    if spec['errors'] and not spec['worktree']:
        raise DeliveryError('task configuration is invalid: ' + '; '.join(spec['errors']))
    work = _root(spec['worktree'])
    if work == context.repo:
        raise DeliveryError('task worktree is the repository worktree itself; refusing to '
                            'operate on it')
    if _common(work) != _common(context.repo):
        raise DeliveryError('task worktree does not belong to the run repository')
    branch = _branch(work)
    if not branch or branch in ('main', 'master'):
        raise DeliveryError('task worktree must be on a non-main branch')
    if branch == _branch(context.repo):
        raise DeliveryError('task worktree is on the repository worktree branch; refusing to '
                            'commit there')
    if context.state_path.resolve().is_relative_to(work):
        raise DeliveryError('state journal is inside the task worktree')
    return work


def _reviewer_gate(context, spec, work, record):
    gate = context.reviewer_gate(work)
    record['reviewer_profile'] = {'agent': context.plan['reviewer_agent'],
                                  'effective': gate['effective'],
                                  'missing_denies': gate['missing'],
                                  'scoped_allows': gate['scoped_allows'],
                                  'unknown_channels_unproven': True,
                                  'tool_gate_passed': gate['ready']}
    if not gate['ready']:
        raise DeliveryError('reviewer agent profile is not provably read-only: '
                            + '; '.join(gate['reasons']))


def _prove_before_side_effect(context, spec, record, work, require_evidence):
    """The one re-proof shared by execute, verify, review, resume, retry and auth.

    It runs before any command or agent starts, so a failure stops the task with
    no verification or session side effect: the task identity and authorization
    are re-read, the reviewer profile is re-queried, and — once an attempt has
    produced a head — every *actual* changed path, the declared execution
    session's own Agent/directory/terminal state, and the session-to-head/diff
    binding are re-proven. Session metadata alone is not evidence that the
    session produced the recorded commit.
    """
    fresh = project_config(context.repo)
    _authorize_paths(spec['paths'], fresh)
    if _status(work):
        raise DeliveryError('worktree is not clean; refusing to continue')
    changed = []
    if require_evidence:
        base, head = record.get('base'), record.get('head')
        if not isinstance(base, str) or not base or not isinstance(head, str) or not head:
            raise DeliveryError('recorded state has no proven base/head')
        changed = _changes(work, base)
        if changed != sorted(record.get('changed_paths') or []):
            raise DeliveryError('actual changed paths do not match the recorded set')
        for name in changed:
            _check_changed_path(work, name, spec['paths'], fresh)
    _reviewer_gate(context, spec, work, record)
    if require_evidence:
        session = record.get('execution_session')
        if not session:
            raise DeliveryError('recorded state has no execution session')
        probe = context.session_probe(session, work)
        _check_probe(probe, work, context.plan['executor_agent'], 'execution',
                     session=session)
        problem = _session_binding(work, base, head, session, record.get('diff_sha256'),
                                   _packet_digest(context.plan, spec))
        if problem:
            raise DeliveryError(f'the execution session does not prove the recorded '
                                f'{problem}; refusing to trust this attempt')
    return changed


def _execute(context, spec, record):
    work = _resolve_task(context, spec)
    if _status(work):
        raise DeliveryError('task worktree is not clean')
    base = _head(work)
    record.update(base=base, branch=_branch(work), worktree=str(work), pid=os.getpid(),
                  phase='execute', execution_session=None, head=None, diff_sha256=None,
                  changed_paths=[])
    context.save()
    _prove_before_side_effect(context, spec, record, work, require_evidence=False)
    packet = ('You are an execution agent. Work only inside this task worktree and only '
              'within these repository-relative paths: ' + json.dumps(spec['paths'])
              + '. Do not push, merge, approve, change permissions, or modify any other '
              'repository. Leave the worktree clean of unrelated changes.\n'
              'Acceptance criteria:\n' + _criteria_prompt(spec)
              + '\n\n' + spec['prompt'])
    execution = context.agent(work, packet, 'execution')
    record['execution_session'] = execution.get('session_id')
    record['execution_detail'] = execution.get('detail')
    if execution.get('exit_code') != 0 or not execution.get('session_id'):
        raise DeliveryError('execution failed or its terminal state is unknown: '
                            + str(execution.get('detail') or 'no detail'), 'FAILED')
    probe = context.session_probe(execution['session_id'], work)
    _check_probe(probe, work, context.plan['executor_agent'], 'execution', 'FAILED',
                 session=execution['session_id'])
    record['execution_evidence'] = {
        'session': execution['session_id'], 'outcome': probe.get('outcome'),
        'directory': probe.get('directory'), 'agents': probe.get('agents'),
        'models': probe.get('models')}
    changed = _changes(work, base)
    if not changed:
        raise DeliveryError('execution produced no changes', 'FAILED')
    # Re-read the project's authorization immediately before committing: the
    # policy may have changed while the agent was running.
    fresh = project_config(context.repo)
    _authorize_paths(spec['paths'], fresh)
    for name in changed:
        _check_changed_path(work, name, spec['paths'], fresh)
    if _status(work):
        subprocess.run(['git', '--no-optional-locks', 'add', '-A', '--', '.'], cwd=work,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        message = spec['raw'].get('message') or f'{spec["id"]}: autonomous delivery'
        subprocess.run(['git', '--no-optional-locks', 'commit', '--no-gpg-sign',
                        '-m', str(message)], cwd=work, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    if _status(work):
        raise DeliveryError('task worktree still has uncommitted changes after commit')
    head = _head(work)
    if head == base:
        raise DeliveryError('execution committed nothing', 'FAILED')
    committed = _changes(work, base)
    fresh = project_config(context.repo)
    _authorize_paths(spec['paths'], fresh)
    for name in committed:
        _check_changed_path(work, name, spec['paths'], fresh)
    digest = _fingerprint(work, base)
    _bind_session(work, base, execution['session_id'], digest,
                  _packet_digest(context.plan, spec))
    head = _head(work)
    record.update(head=head, changed_paths=committed, phase='verify')
    record['diff_sha256'] = digest
    context.save()


def _verify(context, spec, record):
    work = _resolve_task(context, spec)
    if _head(work) != record['head'] or _fingerprint(work, record['base']) != record['diff_sha256']:
        raise DeliveryError('head or diff changed before verification')
    _prove_before_side_effect(context, spec, record, work, require_evidence=True)
    checks = _run_checks(spec['verify'], work, spec['raw'].get('timeout', 1800))
    record['verification'] = checks
    context.save()
    if any(check.get('timeout') for check in checks):
        raise DeliveryError('a verification process tree may still be running; refusing to '
                            'continue or retry automatically')
    if not _all_zero(checks):
        raise DeliveryError('verification failed', 'FAILED')
    if _head(work) != record['head'] or _fingerprint(work, record['base']) != record['diff_sha256']:
        raise DeliveryError('head or diff changed during verification')
    record['phase'] = 'review'
    context.save()


def _packet_digest(plan, spec):
    """Digest of the task packet, independent of the attempt's head/diff state."""
    payload = {
        'id': spec['id'], 'prompt': spec['prompt'], 'paths': list(spec['paths']),
        'acceptance': list(spec['acceptance']),
        'verify': [hashlib.sha256(json.dumps(command).encode()).hexdigest()
                   for command in spec['verify']],
        'executor_agent': plan.get('executor_agent'),
        'reviewer_agent': plan.get('reviewer_agent'),
        'carrier_digest': (plan.get('carrier') or {}).get('digest'),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _task_digest(context, spec, record):
    payload = {
        'id': spec['id'], 'prompt': spec['prompt'], 'paths': list(spec['paths']),
        'acceptance': list(spec['acceptance']),
        'verify': [hashlib.sha256(json.dumps(command).encode()).hexdigest()
                   for command in spec['verify']],
        'base': record.get('base'), 'head': record.get('head'),
        'diff_sha256': record.get('diff_sha256'), 'diff_digest': record.get('diff_digest'),
        'changed_paths': sorted(record.get('changed_paths') or []),
        'mode': MODE,
        'executor_agent': context.plan['executor_agent'],
        'reviewer_agent': context.plan['reviewer_agent'],
        'carrier_digest': (context.plan.get('carrier') or {}).get('digest'),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _review_diff(work, base, head, changed):
    """The complete textual diff a reviewer needs, or a fail-closed error."""
    if not changed:
        raise DeliveryError('there is no changed path to review')
    numstat = os.fsdecode(git(work, 'diff', '--numstat', '--no-renames', base, head, '--',
                              *changed))
    for line in numstat.splitlines():
        parts = line.split('\t')
        if len(parts) >= 2 and ('-' in parts[0] or '-' in parts[1]):
            raise DeliveryError('binary change cannot be presented completely; refusing to '
                                'start a review that could not see it')
    patch = os.fsdecode(git(work, 'diff', '--no-ext-diff', '--no-color', '--no-renames',
                            '--unified=3', base, head, '--', *changed))
    if 'Binary files' in patch:
        raise DeliveryError('binary change cannot be presented completely; refusing to review')
    if len(patch.encode()) > MAX_DIFF_BYTES:
        raise DeliveryError(f'diff is larger than {MAX_DIFF_BYTES} bytes; refusing to review '
                            'a change the reviewer cannot see completely')
    if not patch.strip():
        raise DeliveryError('the reviewed diff is empty; refusing to review')
    return patch, hashlib.sha256(patch.encode()).hexdigest()


def _review_prompt(spec, record, patch):
    return ('You are an independent reviewer in a new session. Do not modify, create or '
            'delete any file, do not commit, do not run commands that change the worktree, '
            'do not approve or merge anything, and do not push. Review the change described '
            'below against each acceptance criterion and answer with exactly one fenced '
            '```json block and nothing else inside it: {"verdict": "APPROVE" or "REJECT", '
            '"head": "<40-char head sha>", "base": "<40-char base sha>", '
            '"diff_sha256": "<64-char worktree digest>", "diff_digest": "<64-char digest of '
            'the shown diff>", "task_digest": "<64-char task binding>", '
            '"mode": "prepare-only", "paths": [<the exact allowed path list>], '
            '"acceptance": [{"id": "AC-1", "criterion": "<the exact criterion text>", '
            '"status": "PASS" or "FAIL" or "NOT_RUN", "evidence": "what you checked"}], '
            '"findings": []}.\n\n'
            'BASE_SHA=' + record['base'] + '\nHEAD_SHA=' + record['head']
            + '\nDIFF_SHA256=' + record['diff_sha256']
            + '\nDIFF_DIGEST=' + record['diff_digest']
            + '\nTASK_DIGEST=' + record['task_digest']
            + '\nMODE=' + MODE + '\nAllowed paths: ' + json.dumps(spec['paths'])
            + '\nChanged paths: ' + json.dumps(record.get('changed_paths') or [])
            + '\n\nThe complete diff produced by Git is below; review it directly.\n'
            '--- BEGIN DIFF ---\n' + patch + '\n--- END DIFF ---\n'
            + '\nAcceptance criteria:\n' + _criteria_prompt(spec)
            + '\n\nThe verdict must quote the allowed path list and every criterion text '
            'exactly and echo BASE_SHA, HEAD_SHA, DIFF_SHA256, DIFF_DIGEST and TASK_DIGEST. '
            'findings must be an empty list for APPROVE: put any non-defect note inside the '
            'acceptance evidence instead. Task:\n' + spec['prompt'])


def _review(context, spec, record):
    work = _resolve_task(context, spec)
    _prove_before_side_effect(context, spec, record, work, require_evidence=True)
    changed = _changes(work, record['base'])
    if changed != sorted(record.get('changed_paths') or []):
        raise DeliveryError('changed paths moved before review; refusing to review')
    patch, diff_digest = _review_diff(work, record['base'], record['head'], changed)
    record['changed_paths'] = changed
    record['diff_digest'] = diff_digest
    record['task_digest'] = _task_digest(context, spec, record)
    context.save()
    review = context.agent(work, _review_prompt(spec, record, patch), 'review')
    session = review.get('session_id')
    record['review_detail'] = review.get('detail')
    if review.get('exit_code') != 0 or not session:
        raise DeliveryError('review session failed or its terminal state is unknown: '
                            + str(review.get('detail') or 'no detail'), 'REVIEW_REJECTED')
    if session == record.get('execution_session'):
        raise DeliveryError('review session equals execution session; independence is not '
                            'established', 'REVIEW_REJECTED')
    probe = context.session_probe(session, work)
    _check_probe(probe, work, context.plan['reviewer_agent'], 'review', 'REVIEW_REJECTED',
                 require_text=True, session=session)
    record['review_session'] = session
    record['review_evidence'] = {
        'session': session, 'outcome': probe.get('outcome'),
        'directory': probe.get('directory'), 'agents': probe.get('agents'),
        'models': probe.get('models')}
    context.save()
    transcript = ''.join(probe.get('texts') or [])
    verdict = _verdict(transcript)
    if verdict is None:
        raise DeliveryError('review transcript has no machine-readable verdict',
                            'REVIEW_REJECTED')
    streamed = _verdict(review.get('text'))
    if streamed is None:
        raise DeliveryError('review stream has no machine-readable verdict',
                            'REVIEW_REJECTED')
    if json.dumps(streamed, sort_keys=True) != json.dumps(verdict, sort_keys=True):
        raise DeliveryError('review stream and session transcript disagree; refusing the '
                            'verdict', 'REVIEW_REJECTED')
    problem = _verdict_matches(verdict, record, spec, context)
    if problem:
        raise DeliveryError(problem, 'REVIEW_REJECTED')
    if _status(work) or _head(work) != record['head'] \
            or _fingerprint(work, record['base']) != record['diff_sha256']:
        raise DeliveryError('review session changed the worktree or its head/diff',
                            'REVIEW_REJECTED')
    record['review_verdict'] = verdict
    record['status'] = 'PREPARED'
    record['phase'] = 'done'
    context.save()


def _facts_match(record, work):
    if _status(work):
        return False, 'worktree is not clean'
    if _head(work) != record.get('head'):
        return False, 'head differs from the recorded head'
    if _fingerprint(work, record['base']) != record.get('diff_sha256'):
        return False, 'diff differs from the recorded digest'
    return True, ''


def _require_supported_state(record):
    if record.get('status') in ('MERGED', 'VERIFIED') or record.get('phase') in (
            'merge', 'post_verify', 'approved'):
        raise DeliveryError('this journal records a merge; automatic merge is not '
                            'implemented in this slice, so the record is not trusted')


def _prove_common(context, spec, record, work):
    """Everything that must hold before any side effect on a recorded approval."""
    work = _resolve_task(context, spec)
    ok, why = _facts_match(record, work)
    if not ok:
        raise DeliveryError(f'recorded state cannot be proven: {why}')
    changed = _prove_before_side_effect(context, spec, record, work,
                                        require_evidence=True)
    patch, diff_digest = _review_diff(work, record['base'], record['head'], changed)
    if diff_digest != record.get('diff_digest'):
        raise DeliveryError('the reviewed diff no longer matches the worktree')
    if _task_digest(context, spec, record) != record.get('task_digest'):
        raise DeliveryError('the current task packet no longer matches the reviewed one')
    session = record.get('execution_session')
    review = record.get('review_session')
    if not review or review == session:
        raise DeliveryError('recorded state has no independent review session')
    review_probe = context.session_probe(review, work)
    _check_probe(review_probe, work, context.plan['reviewer_agent'], 'review',
                 require_text=True, session=review)
    verdict = _verdict(''.join(review_probe.get('texts') or []))
    if verdict is None:
        raise DeliveryError('review transcript has no machine-readable verdict')
    problem = _verdict_matches(verdict, record, spec, context)
    if problem:
        raise DeliveryError('review transcript no longer supports the approval: ' + problem)
    recorded = record.get('review_verdict')
    if recorded is not None and json.dumps(recorded, sort_keys=True) != json.dumps(
            verdict, sort_keys=True):
        raise DeliveryError('recorded verdict does not match the session transcript')
    return changed


def _authenticate(record, context, spec):
    """Re-prove an approval from Git and the transcripts before trusting it."""
    _require_supported_state(record)
    if record.get('status') != 'PREPARED':
        return
    key = spec['id']
    if context.auth.get(key) == 'ok':
        return
    head, digest = record.get('head'), record.get('diff_sha256')
    if not isinstance(head, str) or not SHA_RE.match(head):
        raise DeliveryError('recorded approval has no usable head')
    if not isinstance(digest, str) or not DIGEST_RE.match(digest):
        raise DeliveryError('recorded approval has no diff digest')
    work = _resolve_task(context, spec)
    # Non-executing proof first: identity, scope, sessions and transcript.
    _prove_common(context, spec, record, work)
    # Only then run the verification commands (the only side effect here).
    checks = _run_checks(spec['verify'], work, spec['raw'].get('timeout', 1800))
    if any(check.get('timeout') for check in checks):
        raise DeliveryError('a verification process tree may still be running; refusing to '
                            'continue')
    if not _all_zero(checks):
        raise DeliveryError('recorded preparation does not pass verification when re-run')
    if _head(work) != head or _fingerprint(work, record['base']) != digest:
        raise DeliveryError('worktree moved while re-proving the approval')
    context.auth[key] = 'ok'


def _reconcile(context, spec, record):
    """Continue an interrupted task only after re-proving every gate."""
    if _pid_alive(record.get('pid')) is not False:
        raise DeliveryError('previous runner process may still be alive; refusing to resume')
    _require_supported_state(record)
    work = _resolve_task(context, spec)
    phase = record.get('phase')
    if phase in (None, 'execute'):
        if _status(work):
            raise DeliveryError('interrupted execution left uncommitted changes; inspect them '
                                'manually (this runner will not reset or stash)')
        if record.get('base') and _head(work) != record['base']:
            raise DeliveryError('interrupted execution left commits behind; inspect them '
                                'manually (this runner will not reset or stash)')
        _prove_before_side_effect(context, spec, record, work, require_evidence=False)
        record.update(status='PENDING', phase='execute', reason=None,
                      attempt=(record.get('attempt') or 1) + 1)
        return
    if phase not in ('verify', 'review'):
        raise DeliveryError(f'interrupted phase {phase!r} cannot be resumed: automatic merge '
                            'is not implemented and there is no merge checkpoint')
    if not record.get('base'):
        raise DeliveryError('interrupted record has no recorded baseline')
    ok, why = _facts_match(record, work)
    if not ok:
        raise DeliveryError(f'interrupted state cannot be proven: {why}')
    changed = _prove_before_side_effect(context, spec, record, work,
                                        require_evidence=True)
    # Re-run the full verification before handing the task to review again.
    checks = _run_checks(spec['verify'], work, spec['raw'].get('timeout', 1800))
    record['verification'] = checks
    record['changed_paths'] = changed
    if any(check.get('timeout') for check in checks):
        raise DeliveryError('a verification process tree may still be running; refusing to '
                            'continue')
    if not _all_zero(checks):
        raise DeliveryError('verification failed when resuming; not continuing to review',
                            'FAILED')
    record.update(status='RUNNING', phase='review',
                  attempt=record.get('attempt') or 1)
    context.save()


def _configure(context, spec, record):
    work = _resolve_task(context, spec)
    branch = _branch(work)
    if not branch or branch in ('main', 'master'):
        raise DeliveryError('task worktree must be on a non-main branch')
    baseline = _head(context.repo)
    if _head(work) != baseline:
        # Fail closed. Moving a branch with `reset` can overwrite ignored or
        # untracked user data, so this runner never does it.
        raise DeliveryError('task worktree is not exactly at the target baseline; refusing '
                            'to move it (this runner never resets or stashes)')
    if _status(work):
        raise DeliveryError('task worktree is not clean')
    record.update(worktree=str(work), branch=branch, base=baseline, target_base=None,
                  executor_agent=context.plan['executor_agent'],
                  reviewer_agent=context.plan['reviewer_agent'])


def _process(context, spec, record):
    if spec['errors']:
        raise DeliveryError('task configuration is invalid: ' + '; '.join(spec['errors']))
    status = record.get('status')
    if status == 'CANCELLED':
        return False
    _require_supported_state(record)
    if status in ('PENDING', 'RUNNING', 'PREPARED'):
        _resolve_task(context, spec)
    if status == 'PREPARED':
        _authenticate(record, context, spec)
        return False
    if status in STOPPED:
        return False
    if status in ('PENDING', 'RUNNING'):
        for dep in spec['depends']:
            dependency = context.record(dep)
            if dependency.get('status') not in INTEGRATED:
                return False
            _authenticate(dependency, context, context.spec(dep))
        for index, other in enumerate(context.specs):
            if other['id'] == spec['id'] or index >= context.order[spec['id']]:
                continue
            if _overlap(spec['paths'], other['paths']) and \
                    context.record(other['id']).get('status') != TERMINAL_OK:
                raise DeliveryError(f'path conflict with earlier task {other["id"]}: '
                                    'overlapping paths must be serialized')
    if record.get('status') not in ACTIVE:
        _configure(context, spec, record)
        work = _root(record['worktree'])
        _reviewer_gate(context, spec, work, record)
        record.update(status='RUNNING', phase='execute',
                      attempt=record.get('attempt') or 1,
                      reason=None, history=record.get('history') or [])
        context.save()
    if record.get('phase') == 'execute':
        _execute(context, spec, record)
    if record.get('phase') == 'verify':
        _verify(context, spec, record)
    if record.get('phase') == 'review':
        _review(context, spec, record)
    return True


def _overlap(left, right):
    return any(a == b or a.startswith(b.rstrip('/') + '/') or b.startswith(a.rstrip('/') + '/')
               for a in left for b in right)


def _attempt_retry(context, spec, record):
    """Re-run work only when nothing the user owns can be touched."""
    if spec['errors']:
        raise DeliveryError('task configuration is invalid: ' + '; '.join(spec['errors']))
    _require_supported_state(record)
    if record.get('status') not in STOPPED:
        raise DeliveryError(f'task {spec["id"]} is {record.get("status")}, not retryable')
    if record.get('phase') not in ('execute', 'verify', 'review'):
        raise DeliveryError(f'task {spec["id"]} cannot be retried from phase '
                            f'{record.get("phase")!r}')
    if record.get('status') == 'UNKNOWN' and _pid_alive(record.get('pid')) is not False:
        raise DeliveryError('retry refused: the previous runner process may still be alive')
    if _timed_out(record):
        raise DeliveryError('retry refused: a verification check timed out and its process '
                            'tree may still be running; inspect the host manually')
    work = _resolve_task(context, spec)
    if _status(work):
        raise DeliveryError('retry refused: the worktree has uncommitted changes. Inspect them '
                            'and clean up manually; this runner never stashes or resets')
    phase = record.get('phase')
    if phase in ('verify', 'review'):
        if _head(work) != record.get('head'):
            raise DeliveryError('retry refused: the worktree head is not the failed attempt')
    elif record.get('base') and _head(work) != record['base']:
        raise DeliveryError('retry refused: the worktree has commits from the failed attempt. '
                            'Inspect them and reset manually; this runner never resets')
    # Every retry re-proves identity, scope, every actual changed path, the
    # reviewer profile, authorization and the session-to-head/diff binding
    # before it can run a check or start a session.
    _prove_before_side_effect(context, spec, record, work,
                              require_evidence=phase in ('verify', 'review'))
    attempt = int(record.get('attempt') or 1)
    history = record.get('history') or []
    history.append({'attempt': attempt, 'status': record.get('status'),
                    'phase': record.get('phase'), 'reason': record.get('reason'),
                    'head': record.get('head')})
    if phase == 'execute':
        record.update(history=history, status='PENDING', phase='execute', attempt=attempt + 1,
                      reason=None, head=None, diff_sha256=None, changed_paths=[],
                      verification=None, review_verdict=None, review_session=None,
                      execution_session=None)
    else:
        record.update(history=history, status='RUNNING', phase='verify', attempt=attempt + 1,
                      reason=None, verification=None, review_verdict=None,
                      review_session=None, review_evidence=None, review_detail=None)
    context.save()


def run(repo, plan, state_path, *, agent=None, session_probe=session_export,
        profiles=agent_profiles, gh=gh_run, resume=False, retry=(),
        executor_agent=None, reviewer_agent=None, timeout=1800):
    repo = _root(repo)
    if plan.get('merge') or plan.get('merge_into'):
        raise DeliveryError(MERGE_REFUSAL)
    specs = _plan_tasks(plan)
    state_path = Path(state_path).absolute()
    if state_path.is_symlink() or not state_path.parent.is_dir():
        raise ValueError('state must be a regular file path in an existing directory')
    if state_path.resolve().is_relative_to(repo):
        raise ValueError('state must be outside the repository')

    executor_agent = executor_agent or plan.get('executor_agent') or 'build'
    reviewer_agent = reviewer_agent or plan.get('reviewer_agent') or 'scout'
    if reviewer_agent == executor_agent:
        raise ValueError('reviewer agent must differ from the executor agent')

    config = project_config(repo)
    if config.get('edition_count') != 1 or config.get('edition') not in VALID_EDITIONS:
        values = config.get('edition_values') or []
        raise ValueError('docs/PROJECT.md must declare exactly one `edition` of `local` or '
                         f'`github` before any dispatch; found {config.get("edition_count")} '
                         f'declaration(s), values {values!r} (edition parsed as '
                         f'{config.get("edition")!r})')
    if _status(repo):
        raise ValueError('repository worktree is not clean')
    for spec in specs:
        if spec['errors']:
            continue
        _authorize_paths(spec['paths'], config)

    carrier_url = verify_carrier(plan['carrier'], gh) if plan.get('carrier') else None
    peek = {}
    if state_path.exists():
        try:
            peek = json.loads(state_path.read_text(encoding='utf-8'))
        except ValueError as exc:
            raise ValueError(f'state journal is not readable JSON: {exc}') from None
        peek = peek.get('tasks') if isinstance(peek, dict) else None
        if not isinstance(peek, dict):
            raise ValueError('state journal has an unsupported shape; reconcile manually')
    for spec in specs:
        if spec['errors']:
            continue
        if (peek.get(spec['id']) or {}).get('status') not in (None, 'PENDING', 'UNKNOWN'):
            continue
        try:
            work = _root(spec['worktree'])
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            spec['errors'].append(f'task worktree is not usable: {exc}')
            continue
        if state_path.resolve().is_relative_to(work):
            raise ValueError('state must be outside every task worktree')
        try:
            if work == repo:
                raise ValueError('task worktree is the repository worktree itself')
            if _common(work) != _common(repo):
                raise ValueError('worktree does not belong to the repository')
            branch = _branch(work)
            if not branch or branch in ('main', 'master'):
                raise ValueError('worktree must be on a non-main branch')
            if _status(work):
                raise ValueError('worktree is not clean')
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            spec['errors'].append(f'task worktree is not usable: {exc}')

    plan = dict(plan, executor_agent=executor_agent, reviewer_agent=reviewer_agent)
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    carrier_digest = (plan.get('carrier') or {}).get('digest')

    locks = acquire_locks(repo, state_path, specs)
    try:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if (not isinstance(state, dict) or state.get('version') != STATE_VERSION
                    or not isinstance(state.get('tasks'), dict)):
                raise ValueError('state journal has an unsupported shape; reconcile manually')
            if state.get('plan_hash') != plan_hash:
                raise ValueError('plan changed since the journal was written; reconcile '
                                 'manually instead of reusing its state')
            if state.get('carrier_digest') != carrier_digest:
                raise ValueError('carrier changed since the journal was written; reconcile '
                                 'manually')
        else:
            state = {'version': STATE_VERSION, 'tasks': {}, 'plan_hash': plan_hash,
                     'carrier_digest': carrier_digest, 'carrier_url': carrier_url}
        state['plan_hash'] = plan_hash
        state['carrier_digest'] = carrier_digest
        state['carrier_url'] = carrier_url
        state['project_edition'] = config.get('edition')
        state['mode'] = MODE

        if agent is None:
            def agent(work, prompt, role):
                name = reviewer_agent if role == 'review' else executor_agent
                return opencode(work, prompt, role, agent_name=name, timeout=timeout)

        def saver():
            _save(state_path, state)

        context = _Context(repo, plan, specs, state, agent, session_probe, profiles,
                           saver, config, state_path)
        for spec in specs:
            state['tasks'].setdefault(spec['id'], {'status': 'PENDING'})
        for spec in specs:
            record = context.record(spec['id'])
            try:
                _require_supported_state(record)
            except DeliveryError as exc:
                record.update(status=exc.status, reason=str(exc))
                saver()
                continue
            if record.get('status') not in ('RUNNING', 'UNKNOWN'):
                continue
            if not resume:
                if record.get('status') == 'RUNNING':
                    record.update(status='UNKNOWN', reason='interrupted; rerun with --resume '
                                                          'to reconcile')
                    saver()
                continue
            try:
                _reconcile(context, spec, record)
                saver()
            except DeliveryError as exc:
                record.update(status=exc.status, reason=str(exc))
                saver()
            except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
                record.update(status='BLOCKED', reason=str(exc))
                saver()
        for ident in retry:
            spec = next((spec for spec in specs if spec['id'] == ident), None)
            if spec is None:
                raise ValueError(f'--retry names an unknown task {ident!r}')
            try:
                _attempt_retry(context, spec, context.record(ident))
            except DeliveryError as exc:
                context.record(ident).update(status=exc.status, reason=str(exc))
                saver()

        progress = True
        while progress:
            progress = False
            for spec in specs:
                record = state['tasks'][spec['id']]
                try:
                    progress = _process(context, spec, record) or progress
                except DeliveryError as exc:
                    record.update(status=exc.status, reason=str(exc))
                    saver()
                except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
                    record.update(status='BLOCKED', reason=str(exc))
                    saver()
        return state
    finally:
        for handle in locks:
            handle.close()


# ------------------------------------------------------------------------ CLI

def _summary(state):
    tasks = {}
    for ident, record in sorted((state.get('tasks') or {}).items()):
        verdict = record.get('review_verdict') or {}
        safe_verdict = ({'verdict': verdict.get('verdict'), 'head': verdict.get('head'),
                         'base': verdict.get('base'), 'diff_digest': verdict.get('diff_digest'),
                         'task_digest': verdict.get('task_digest'),
                         'paths': verdict.get('paths'), 'mode': verdict.get('mode'),
                         'acceptance': [{'id': item.get('id'), 'status': item.get('status')}
                                        for item in verdict.get('acceptance') or []]}
                        if verdict else None)
        tasks[ident] = {
            'status': record.get('status'), 'phase': record.get('phase'),
            'attempt': record.get('attempt'), 'head': record.get('head'),
            'diff_sha256': record.get('diff_sha256'), 'changed_paths': record.get('changed_paths'),
            'execution_session': record.get('execution_session'),
            'review_session': record.get('review_session'),
            'review_verdict': safe_verdict,
            'reviewer_profile': record.get('reviewer_profile'),
            'verification': [{'program': check.get('program'),
                              'argv_sha256': check.get('argv_sha256'),
                              'exit_code': check.get('exit_code')}
                             for check in record.get('verification') or []],
            'reason': record.get('reason')}
    return {'tasks': tasks, 'carrier_url': state.get('carrier_url'),
            'project_edition': state.get('project_edition'), 'mode': state.get('mode')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='authorized repository worktree')
    parser.add_argument('--plan', help='trusted JSON plan path')
    parser.add_argument('--issue', type=int, help='GitHub Issue number as the plan source')
    parser.add_argument('--issue-repo', help='owner/name of the issue repository')
    parser.add_argument('--worktree', help='task worktree when planning from an issue')
    parser.add_argument('--merge-into', help='refused: automatic merge is not implemented')
    parser.add_argument('--verify', action='append', default=[],
                        help='JSON argv list for an issue plan (repeatable)')
    parser.add_argument('--acceptance', action='append', default=[],
                        help='acceptance criterion for an issue plan (repeatable)')
    parser.add_argument('--allow', action='append', default=[],
                        help='narrow the issue scope to these paths (repeatable)')
    parser.add_argument('--instruction', default='', help='operator note for an issue plan')
    parser.add_argument('--state', required=True)
    parser.add_argument('--execute', action='store_true', help='explicitly opt into execution')
    parser.add_argument('--resume', action='store_true',
                        help='reconcile interrupted or unknown tasks from re-proven facts')
    parser.add_argument('--retry', action='append', default=[], metavar='ID')
    parser.add_argument('--executor-agent', default=None)
    parser.add_argument('--reviewer-agent', default=None)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error('explicit --execute is required')
    if bool(args.plan) == bool(args.issue):
        parser.error('provide exactly one of --plan or --issue')
    if args.merge_into:
        parser.exit(2, MERGE_REFUSAL + '\n')
    try:
        if args.issue is not None:
            if not args.issue_repo or not args.worktree:
                parser.error('--issue needs --issue-repo and --worktree')
            plan = load_issue_plan(args.issue, args.issue_repo, worktree=args.worktree,
                                   verify=args.verify, acceptance=args.acceptance,
                                   allows=args.allow, instruction=args.instruction)
        else:
            plan = _load_plan_file(args.plan)
        state = run(args.repo, plan, args.state, resume=args.resume, retry=args.retry,
                    executor_agent=args.executor_agent, reviewer_agent=args.reviewer_agent,
                    timeout=args.timeout)
    except (ValueError, OSError, subprocess.SubprocessError, DeliveryError) as exc:
        parser.exit(2, f'{exc}\n')
    print(json.dumps(_summary(state), ensure_ascii=False, indent=2, sort_keys=True))
    specs = _plan_tasks(plan)
    return 0 if all(state['tasks'].get(spec['id'], {}).get('status') == TERMINAL_OK
                    for spec in specs) else 1


if __name__ == '__main__':
    raise SystemExit(main())
