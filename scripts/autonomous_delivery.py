"""Opt-in, single-host delivery runner: execute, verify, review, merge, resume.

The operator supplies an authorized repository and already-created task
worktrees, plus either a trusted JSON plan or a live GitHub Issue. The runner
starts a real OpenCode execution session, verifies the diff with the plan's
commands, starts a *different* session running a declared read-only agent for
review, and merges only when every gate below is backed by observed evidence:

* the plan is authorized by the project's own configuration (edition, allowed
  and forbidden paths); a `github` edition never merges locally (that stays the
  Master's PR + CI flow) and an unprovable edition never merges either;
* the task starts exactly at the target baseline and only the reviewed, scoped
  diff is merged -- the merge uses the immutable reviewed head SHA, never the
  mutable branch ref;
* every acceptance criterion has a PASS verdict in the reviewer's own session
  transcript, not just in a stream or the journal;
* verification commands exited 0 at the recorded head, and the head and diff
  digest did not move while the checks ran;
* the review session is not the execution session, its transcript exists in the
  task worktree, its agent is a different, edit- and shell-denied agent, its
  assistant turn finished with ``stop``, and its verdict binds this exact head,
  diff digest and acceptance list;
* the target branch is still at the recorded baseline, the merged result is
  content-identical to the reviewed head for the reviewed paths, and post-merge
  verification neither fails nor moves the target head.

Nothing here is a sandbox. Worktrees, prompts, path checks and ``--standalone``
do not restrict OpenCode's configured permissions: V2 can reach the host
filesystem, shell, network and credentials. Only tool-level ``edit`` / ``shell``
denials are checked; OS isolation is not proven. Do not point this at untrusted
plans or repositories whose current OpenCode authority exceeds the user's
authorization. Only the standard library is used. Model text is not approval and
no state is trusted from the journal alone: every recorded success is re-proved
from Git, the session transcripts and re-run verification before it can unlock a
dependent task or produce exit code 0.
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

STATE_VERSION = 2
TERMINAL_OK = 'VERIFIED'
ACTIVE = ('RUNNING', 'APPROVED', 'MERGED')
STOPPED = ('FAILED', 'REVIEW_REJECTED', 'BLOCKED', 'UNKNOWN')
BRANCH_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')
SHA_RE = re.compile(r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
DIGEST_RE = re.compile(r'^[0-9a-f]{64}$')
VERDICT_KEYS = {'verdict', 'head', 'diff_sha256', 'findings', 'acceptance'}
REQUIRED_DENIED = ('edit', 'shell')
MUTATING_ACTIONS = ('edit', 'write', 'patch', 'shell', 'bash')


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


def _changes_between(where, base, head):
    raw = git(where, 'diff', '--no-ext-diff', '--no-renames', '--name-only', '-z', base, head, '--')
    return sorted(os.fsdecode(item) for item in raw.split(b'\0') if item)


def _tree_modes(where, head, paths):
    """Git modes for the given paths at ``head`` (120000/160000 are unsafe)."""
    raw = git(where, 'ls-tree', '-z', head, '--', *paths)
    modes = {}
    for entry in raw.split(b'\0'):
        if not entry:
            continue
        meta, _, name = entry.partition(b'\t')
        fields = meta.split()
        if fields:
            modes[os.fsdecode(name)] = os.fsdecode(fields[0])
    return modes


# ------------------------------------------------------------------ project config

def project_config(repo):
    """Read edition and path authorization from the project's own config."""
    path = Path(repo) / 'docs' / 'PROJECT.md'
    if not path.is_file():
        return {'edition': None, 'allowed': None, 'forbidden': []}
    text = path.read_text(encoding='utf-8', errors='replace')
    match = re.search(r'^\s*[-*]\s*`?edition`?\s*[:：]\s*`?([A-Za-z0-9_-]+)`?', text, re.M)
    return {'edition': match.group(1) if match else None,
            'allowed': _policy_paths(text, '允许改动路径'),
            'forbidden': _policy_paths(text, '禁止改动路径')}


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


def _authorize_paths(paths, config):
    """Refuse a plan that is outside the project's own written authorization."""
    if config['allowed'] is None:
        raise ValueError('cannot prove project authorization: docs/PROJECT.md has no '
                         'backticked 允许改动路径 list')
    for name in paths:
        for forbidden in config['forbidden']:
            if name == forbidden or name.startswith(forbidden + '/'):
                raise ValueError(f'path {name!r} is forbidden by docs/PROJECT.md')
        if not any(name == allowed or name.startswith(allowed + '/')
                   for allowed in config['allowed']):
            raise ValueError(f'path {name!r} is not authorized by docs/PROJECT.md')


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


def _check_changed_path(work, name, allowed):
    """Reject a changed path that is not a regular file or a deletion."""
    _safe_rel(name)
    if not _allowed(name, allowed):
        raise DeliveryError(f'scope violation: {name} is outside the task paths')
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
    attempts = raw.get('attempts', 1)
    if not isinstance(attempts, int) or attempts < 1:
        errors.append('attempts must be a positive integer')
        attempts = 1
    return {'id': ident, 'raw': raw, 'worktree': worktree if isinstance(worktree, str) else '',
            'prompt': prompt if isinstance(prompt, str) else '',
            'paths': allowed, 'verify': commands, 'depends': depends,
            'acceptance': criteria, 'attempts': attempts, 'errors': errors}


def _plan_tasks(plan):
    if not isinstance(plan, dict):
        raise ValueError('plan must be a mapping')
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
    # Dependency cycles are a plan-level error: no partial run can satisfy them.
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


def load_issue_plan(number, repo, *, worktree, verify, acceptance, allows=(), merge_into=None,
                    instruction='', runner=gh_run):
    """Build a plan from the live Issue, not from a hand-copied snapshot."""
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
    plan = {'carrier': carrier, 'tasks': [{
        'id': f'issue-{number}', 'worktree': worktree, 'prompt': prompt,
        'paths': paths, 'verify': commands, 'acceptance': list(acceptance), 'depends': []}]}
    if merge_into:
        plan['merge_into'] = merge_into
    else:
        plan['merge'] = False
    return plan


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


def _lock_targets(repo, state_path, specs):
    """Every worktree and journal the run writes must be exclusively ours."""
    targets = {('repo', str(_common(repo))), ('journal', str(Path(state_path).absolute()))}
    for spec in specs:
        if not spec.get('worktree'):
            continue
        try:
            targets.add(('worktree', str(Path(spec['worktree']).absolute().resolve())))
        except OSError:
            continue
    return sorted(targets)


def _acquire_locks(targets):
    handles = []
    try:
        for kind, value in targets:
            key = hashlib.sha256((kind + '\0' + value).encode()).hexdigest()[:24]
            path = Path(tempfile.gettempdir()) / f'agentorch-delivery-{key}.lock'
            handles.append(_lock(path))
    except BaseException:
        for handle in handles:
            handle.close()
        raise
    return handles


def _clean_worktree(path):
    try:
        return not _status(_root(path))
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


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

    The export is *not* sanitized because sanitizing also redacts the working
    directory and the assistant text, which are the identity and verdict facts
    this gate needs. Only metadata is persisted; the transcript stays in memory.
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


def _profile_rules(profile):
    rules = (profile or {}).get('permissions') or []
    denied = {rule.get('action') for rule in rules
              if rule.get('resource') == '*' and rule.get('effect') == 'deny'}
    allowed = {rule.get('action') for rule in rules
               if rule.get('resource') == '*' and rule.get('effect') == 'allow'}
    return denied, allowed


def profile_gate(profile, require_shell):
    """Tool-level write gate for a reviewer agent (never an OS isolation claim).

    Approving a change only needs the edit tool denied. Merging additionally
    requires the shell tool denied: without that, a reviewer can still write
    through the shell, so tool-level read-only is not established.
    """
    denied, allowed = _profile_rules(profile)
    required = set(REQUIRED_DENIED) if require_shell else {'edit'}
    missing = sorted(required - denied)
    explicit_write_allow = sorted((allowed & set(MUTATING_ACTIONS)) - denied)
    return (not missing and not explicit_write_allow), missing, explicit_write_allow


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
    if not isinstance(entries, list) or len(entries) != len(criteria):
        return False, 'acceptance verdict does not cover every criterion'
    ids = {}
    for entry in entries:
        if not isinstance(entry, dict):
            return False, 'acceptance verdict entry is malformed'
        ident, state = entry.get('id'), entry.get('status')
        if ident not in [f'AC-{index}' for index in range(1, len(criteria) + 1)]:
            return False, f'acceptance verdict names unknown criterion {ident!r}'
        if ident in ids:
            return False, f'acceptance verdict repeats {ident}'
        if state != 'PASS':
            return False, f'{ident} is {state!r}, not PASS'
        if not str(entry.get('evidence') or '').strip():
            return False, f'{ident} has no evidence'
        ids[ident] = state
    return True, ''


# --------------------------------------------------------------------- runner

class _Context:
    def __init__(self, repo, target, merge, state_path, plan, specs, state,
                 agent, session_probe, profiles, state_saver, config):
        self.repo = repo
        self.target = target
        self.merge = merge
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

    def reviewer_ready(self, work):
        key = (str(Path(work).resolve()), bool(self.merge))
        if key not in self._reviewer_ready:
            work_key = key[0]
            if work_key not in self._profiles:
                self._profiles[work_key] = self.profiles(work)
            profile = (self._profiles[work_key] or {}).get(self.plan['reviewer_agent']) or {}
            ready, missing, extra = profile_gate(profile, require_shell=self.merge)
            denied, _ = _profile_rules(profile)
            self._reviewer_ready[key] = {
                'ready': ready, 'missing': missing, 'extra': extra,
                'denied': sorted(denied), 'shell_denied': 'shell' in denied,
                'edit_denied': 'edit' in denied}
        return self._reviewer_ready[key]


def _check_probe(probe, work, expected_agent, role, status='BLOCKED', require_text=False):
    def fail(message):
        raise DeliveryError(message, status)

    if not probe.get('ok'):
        fail(f'{role} session is unverifiable: {probe.get("reason")}')
    if probe.get('outcome') != 'succeeded':
        fail(f'{role} session outcome is {probe.get("outcome")!r}, not succeeded')
    directory = probe.get('directory')
    if not directory or os.path.realpath(directory) != os.path.realpath(work):
        fail(f'{role} session ran in {directory!r}, not in the task worktree')
    if probe.get('user_messages', 0) < 1 or probe.get('assistant_messages', 0) < 1:
        fail(f'{role} session has no user/assistant turn')
    if 'stop' not in (probe.get('finishes') or []):
        fail(f'{role} session has no completed assistant turn')
    if expected_agent and any(name != expected_agent for name in probe.get('agents') or []):
        fail(f'{role} session used agent(s) {probe.get("agents")}, '
             f'not the declared {expected_agent}')
    if require_text and not ''.join(probe.get('texts') or []).strip():
        fail(f'{role} session transcript has no assistant text')


def _run_checks(commands, work, timeout):
    checks = []
    for command in commands:
        entry = {'program': command[0], 'argv_sha256':
                 hashlib.sha256(json.dumps(command).encode()).hexdigest()}
        try:
            result = subprocess.run(command, cwd=work, stdin=subprocess.DEVNULL,
                                    env=dict(os.environ, PWD=str(work)),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=timeout)
            entry.update(exit_code=result.returncode,
                         output_tail=(result.stdout or '')[-1000:])
        except (OSError, subprocess.TimeoutExpired) as exc:
            entry.update(exit_code=None, output_tail=f'not run: {exc}')
        checks.append(entry)
    return checks


def _all_zero(checks):
    return bool(checks) and all(check.get('exit_code') == 0 for check in checks)


def _criteria_prompt(spec):
    return '\n'.join(f'- AC-{index}: {text}'
                     for index, text in enumerate(spec['acceptance'], 1))


def _task_root(spec):
    if spec['errors'] and not spec['worktree']:
        raise DeliveryError('task configuration is invalid: ' + '; '.join(spec['errors']))
    return _root(spec['worktree'])


def _reviewer_gate(context, spec, work, record):
    gate = context.reviewer_ready(work)
    ready, missing, extra = gate['ready'], gate['missing'], gate['extra']
    record['reviewer_profile'] = {'agent': context.plan['reviewer_agent'],
                                  'tool_denied': gate['denied'],
                                  'edit_denied': gate['edit_denied'],
                                  'shell_denied': gate['shell_denied'],
                                  'missing_denies': missing,
                                  'explicit_write_allows': extra,
                                  'merge_requires_shell_deny': bool(context.merge),
                                  'ready_for_this_mode': ready}
    if not ready:
        detail = (f'does not deny {", ".join(missing)}' if missing
                  else f'explicitly allows {", ".join(extra)}')
        raise DeliveryError(f'reviewer agent profile {detail}; a reviewer that can write is '
                            'not independent and this runner cannot approve a merge')


def _execute(context, spec, record):
    work = _task_root(spec)
    if _status(work):
        raise DeliveryError('task worktree is not clean')
    base = _head(work)
    target_base = _head(context.repo) if context.merge else None
    record.update(base=base, target_base=target_base, branch=_branch(work),
                  worktree=str(work), pid=os.getpid(), phase='execute',
                  execution_session=None, head=None, diff_sha256=None, changed_paths=[])
    context.save()
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
    _check_probe(probe, work, record.get('executor_agent'), 'execution', 'FAILED')
    record['execution_evidence'] = {
        'session': execution['session_id'], 'outcome': probe.get('outcome'),
        'directory': probe.get('directory'), 'agents': probe.get('agents'),
        'models': probe.get('models')}
    changed = _changes(work, base)
    if not changed:
        raise DeliveryError('execution produced no changes', 'FAILED')
    for name in changed:
        _check_changed_path(work, name, spec['paths'])
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
    for name in committed:
        _check_changed_path(work, name, spec['paths'])
    record.update(head=head, changed_paths=committed, phase='verify')
    record['diff_sha256'] = _fingerprint(work, base)
    context.save()


def _verify(context, spec, record):
    work = _task_root(spec)
    if _head(work) != record['head'] or _fingerprint(work, record['base']) != record['diff_sha256']:
        raise DeliveryError('head or diff changed before verification')
    checks = _run_checks(spec['verify'], work, spec['raw'].get('timeout', 1800))
    record['verification'] = checks
    context.save()
    if not _all_zero(checks):
        raise DeliveryError('verification failed or timed out', 'FAILED')
    if _head(work) != record['head'] or _fingerprint(work, record['base']) != record['diff_sha256']:
        raise DeliveryError('head or diff changed during verification')
    record['phase'] = 'review'
    context.save()


def _review_prompt(spec, record):
    return ('You are an independent reviewer in a new session. Do not modify, create or '
            'delete any file, do not commit, do not run commands that change the worktree, '
            'do not approve or merge anything, and do not push. Review the change described '
            'below against each acceptance criterion and answer with exactly one fenced '
            '```json block and nothing else inside it: {"verdict": "APPROVE" or "REJECT", '
            '"head": "<40-char head sha>", "diff_sha256": "<64-char digest>", '
            '"acceptance": [{"id": "AC-1", "status": "PASS" or "FAIL" or "NOT_RUN", '
            '"evidence": "what you checked"}], "findings": []}.\n\n'
            'HEAD_SHA=' + record['head'] + '\nDIFF_SHA256=' + record['diff_sha256']
            + '\nAllowed paths: ' + json.dumps(spec['paths'])
            + '\nAcceptance criteria:\n' + _criteria_prompt(spec)
            + '\n\nThe verdict must bind this exact head and digest; list a finding for every '
            'defect and use REJECT when any criterion is not PASS. Task:\n' + spec['prompt'])


def _review(context, spec, record):
    work = _task_root(spec)
    review = context.agent(work, _review_prompt(spec, record), 'review')
    session = review.get('session_id')
    record['review_detail'] = review.get('detail')
    if review.get('exit_code') != 0 or not session:
        raise DeliveryError('review session failed or its terminal state is unknown: '
                            + str(review.get('detail') or 'no detail'), 'REVIEW_REJECTED')
    if session == record.get('execution_session'):
        raise DeliveryError('review session equals execution session; independence is not '
                            'established', 'REVIEW_REJECTED')
    probe = context.session_probe(session, work)
    _check_probe(probe, work, record.get('reviewer_agent'), 'review', 'REVIEW_REJECTED',
                 require_text=True)
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
    if verdict.get('verdict') != 'APPROVE':
        raise DeliveryError(f'review verdict is {verdict.get("verdict")!r}, not APPROVE',
                            'REVIEW_REJECTED')
    if verdict.get('head') != record['head']:
        raise DeliveryError('review verdict binds a different head', 'REVIEW_REJECTED')
    if verdict.get('diff_sha256') != record['diff_sha256']:
        raise DeliveryError('review verdict binds a different diff', 'REVIEW_REJECTED')
    if verdict.get('findings'):
        raise DeliveryError('review approved while listing findings', 'REVIEW_REJECTED')
    accepted, why = _acceptance_ok(verdict, spec['acceptance'])
    if not accepted:
        raise DeliveryError(f'review did not pass every acceptance criterion: {why}',
                            'REVIEW_REJECTED')
    if _status(work) or _head(work) != record['head'] \
            or _fingerprint(work, record['base']) != record['diff_sha256']:
        raise DeliveryError('review session changed the worktree or its head/diff',
                            'REVIEW_REJECTED')
    record['review_verdict'] = verdict
    record['phase'] = 'merge'
    record['status'] = 'APPROVED'
    context.save()


def _merge_commit_of(work, head, target):
    found = subprocess.run(['git', '--no-optional-locks', 'rev-list', '--max-count=1',
                            '--merges', '--ancestry-path', f'{head}..{target}', '--'],
                           cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True)
    if found.returncode == 0 and found.stdout.strip():
        return found.stdout.strip()
    return head  # fast-forwarded or already equal


def _merge(context, spec, record):
    if not context.merge:
        record['status'] = 'APPROVED'
        record['phase'] = 'approved'
        context.save()
        return
    if context.config.get('edition') != 'local':
        raise DeliveryError(
            "local auto-merge is only permitted for docs/PROJECT.md edition 'local'; "
            f"found {context.config.get('edition')!r}. On the github edition the runner "
            'stops at APPROVED and the merge stays with the Master PR + CI flow')
    repo = context.repo
    target = context.target
    work = _task_root(spec)
    if _branch(repo) != target:
        raise DeliveryError('target worktree is not on the configured merge branch')
    if _status(repo):
        raise DeliveryError('target worktree is not clean')
    if _status(work) or _head(work) != record['head']:
        raise DeliveryError('task worktree changed before merge; refusing the reviewed head')
    if not record.get('target_base'):
        raise DeliveryError('no recorded target baseline for this task')
    if record.get('base') != record['target_base']:
        raise DeliveryError('task base is not the recorded target baseline')
    if not _is_ancestor(record['base'], record['head'], work):
        raise DeliveryError('reviewed head does not descend from the recorded baseline')
    reviewed = sorted(record.get('changed_paths') or [])
    at_head = _changes_between(work, record['base'], record['head'])
    if at_head != reviewed:
        raise DeliveryError('the reviewed path set no longer matches the branch history')
    for name in at_head:
        _check_changed_path(work, name, spec['paths'])
    modes = _tree_modes(work, record['head'], at_head)
    if any(mode in ('120000', '160000') for mode in modes.values()):
        raise DeliveryError('reviewed head contains a symlink or submodule')
    if _is_ancestor(record['head'], target, repo):
        merge_commit = record.get('merge_commit') or _merge_commit_of(
            repo, record['head'], target)
        if _changes_between(repo, record['target_base'], merge_commit) != reviewed:
            raise DeliveryError('target history between the baseline and the merge contains '
                                'changes outside the reviewed set')
        record['merge_commit'] = merge_commit
        record['already_merged'] = True
        record['phase'] = 'post_verify'
        context.save()
        return
    if _head(repo) != record['target_base']:
        raise DeliveryError('target branch moved after dispatch; baseline is no longer '
                            'verified')
    merged = subprocess.run(['git', '--no-optional-locks', 'merge', '--no-ff', '--no-edit',
                             record['head']], cwd=repo, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if merged.returncode != 0:
        abort = subprocess.run(['git', '--no-optional-locks', 'merge', '--abort'],
                               cwd=repo, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        detail = (merged.stderr or '').strip()[-500:]
        raise DeliveryError(f'merge conflict; aborted (exit {abort.returncode}): {detail}')
    merge_commit = _head(repo)
    second = subprocess.run(['git', '--no-optional-locks', 'rev-parse', f'{merge_commit}^2'],
                            cwd=repo, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    if second.returncode != 0 or second.stdout.strip() != record['head']:
        raise DeliveryError('merge did not bind the reviewed head as its second parent')
    content = subprocess.run(['git', '--no-optional-locks', 'diff', '--quiet',
                              merge_commit, record['head'], '--', *reviewed], cwd=repo,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    if content.returncode != 0:
        raise DeliveryError('merged result is not identical to the reviewed content')
    if _changes_between(repo, record['target_base'], merge_commit) != reviewed:
        raise DeliveryError('target history between the baseline and the merge contains '
                            'changes outside the reviewed set')
    record['merge_commit'] = merge_commit
    record['phase'] = 'post_verify'
    context.save()


def _post_verify(context, spec, record):
    if context.merge:
        merge_commit = record.get('merge_commit')
        if not merge_commit or not _is_ancestor(merge_commit, context.target, context.repo):
            raise DeliveryError('merge commit is not contained in the target branch')
        if _head(context.repo) != merge_commit:
            raise DeliveryError('target head moved before post-merge verification')
        if _changes_between(context.repo, record['target_base'], merge_commit) != sorted(
                record['changed_paths']):
            raise DeliveryError('merged target does not contain exactly the reviewed change set')
    target_head = _head(context.repo) if context.merge else None
    checks = _run_checks(spec['verify'], context.repo, spec['raw'].get('timeout', 1800))
    record['post_verification'] = checks
    if context.merge and (_head(context.repo) != target_head or _status(context.repo)):
        raise DeliveryError('target head or worktree changed during post-merge verification')
    if not _all_zero(checks):
        record['phase'] = 'post_verify_failed'
        raise DeliveryError('post-merge verification failed', 'MERGED')
    record['status'] = 'VERIFIED'
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


def _authenticate(record, context, spec):
    """Re-prove a recorded success from Git, transcripts and fresh checks."""
    status = record.get('status')
    if status not in ('APPROVED', 'MERGED', 'VERIFIED'):
        return
    key = spec['id']
    if context.auth.get(key) == 'ok':
        return
    head, digest = record.get('head'), record.get('diff_sha256')
    if not isinstance(head, str) or not SHA_RE.match(head):
        raise DeliveryError('recorded status has no usable head')
    if not isinstance(digest, str) or not DIGEST_RE.match(digest):
        raise DeliveryError('recorded status has no diff digest')
    work = _task_root(spec)
    ok, why = _facts_match(record, work)
    if not ok:
        raise DeliveryError(f'recorded status cannot be proven: {why}')
    if _changes(work, record['base']) != sorted(record.get('changed_paths') or []):
        raise DeliveryError('recorded path set does not match the worktree')
    for name in record.get('changed_paths') or []:
        _check_changed_path(work, name, spec['paths'])
    checks = _run_checks(spec['verify'], work, spec['raw'].get('timeout', 1800))
    if not _all_zero(checks):
        raise DeliveryError('recorded verification does not pass when re-run')
    session = record.get('execution_session')
    if not session:
        raise DeliveryError('recorded status has no execution session')
    probe = context.session_probe(session, work)
    _check_probe(probe, work, record.get('executor_agent'), 'execution')
    review = record.get('review_session')
    if not review or review == session:
        raise DeliveryError('recorded status has no independent review session')
    probe = context.session_probe(review, work)
    _check_probe(probe, work, record.get('reviewer_agent'), 'review', require_text=True)
    verdict = _verdict(''.join(probe.get('texts') or []))
    if verdict is None:
        raise DeliveryError('review transcript has no machine-readable verdict')
    recorded = record.get('review_verdict') or {}
    if json.dumps(verdict, sort_keys=True) != json.dumps(recorded, sort_keys=True):
        raise DeliveryError('recorded verdict does not match the session transcript')
    if verdict.get('verdict') != 'APPROVE' or verdict.get('head') != head \
            or verdict.get('diff_sha256') != digest:
        raise DeliveryError('review transcript does not approve this head and diff')
    accepted, why = _acceptance_ok(verdict, spec['acceptance'])
    if not accepted:
        raise DeliveryError(f'review transcript does not pass every criterion: {why}')
    if status in ('MERGED', 'VERIFIED'):
        if not context.merge:
            raise DeliveryError('recorded merge cannot be proven without a merge target')
        merge_commit = record.get('merge_commit')
        if not merge_commit or not _is_ancestor(merge_commit, context.target, context.repo):
            raise DeliveryError('recorded merge commit is not in the target branch')
        if not _is_ancestor(head, context.target, context.repo):
            raise DeliveryError('recorded head is not in the target branch')
        content = subprocess.run(['git', '--no-optional-locks', 'diff', '--quiet',
                                  head, merge_commit, '--',
                                  *sorted(record.get('changed_paths') or [])],
                                 cwd=context.repo, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if content.returncode != 0:
            raise DeliveryError('merged content is not identical to the reviewed head')
    if status == 'VERIFIED':
        recheck = _run_checks(spec['verify'], context.repo,
                              spec['raw'].get('timeout', 1800))
        if not _all_zero(recheck):
            raise DeliveryError('recorded VERIFIED does not pass when re-run on the target')
    context.auth[key] = 'ok'


def _reconcile(context, spec, record):
    """Continue an interrupted task only from re-proven facts."""
    if _pid_alive(record.get('pid')) is not False:
        raise DeliveryError('previous runner process may still be alive; refusing to resume')
    work = _task_root(spec)
    session = record.get('execution_session')
    if session:
        probe = context.session_probe(session, work)
        if not probe.get('ok') or probe.get('outcome') != 'succeeded':
            raise DeliveryError('interrupted execution session has no verifiable success')
    phase = record.get('phase')
    if phase in ('verify', 'review', 'merge', 'post_verify'):
        if not record.get('base'):
            raise DeliveryError('interrupted record has no recorded baseline')
        ok, why = _facts_match(record, work)
        if not ok:
            raise DeliveryError(f'interrupted state cannot be proven: {why}')
        changed = _changes(work, record['base'])
        for name in changed:
            _check_changed_path(work, name, spec['paths'])
        record['changed_paths'] = changed
    if phase in ('verify', 'review'):
        if not session:
            raise DeliveryError('interrupted execution has no verifiable session')
        record.update(status='RUNNING', phase=phase,
                      attempt=record.get('attempt') or 1)
        return
    if phase in ('merge', 'approved'):
        if not session:
            raise DeliveryError('interrupted approval has no verifiable execution session')
        record['status'] = 'APPROVED'
        record['phase'] = 'merge'
        _authenticate(record, context, spec)
        return
    if phase == 'post_verify':
        if not record.get('merge_commit') or not _is_ancestor(
                record['merge_commit'], context.target, context.repo):
            raise DeliveryError('interrupted merge has no merge commit in the target')
        record.update(status='APPROVED', phase='post_verify')
        return
    if phase == 'execute':
        if _status(work):
            raise DeliveryError('interrupted execution left uncommitted changes; inspect them '
                                'manually (this runner will not reset or stash)')
        base = record.get('base')
        if base and _head(work) != base:
            raise DeliveryError('interrupted execution left commits behind; inspect them '
                                'manually (this runner will not reset or stash)')
        record.update(status='PENDING', phase='execute', reason=None,
                      attempt=(record.get('attempt') or 1) + 1)
        return
    raise DeliveryError('interrupted record has no verifiable checkpoint')


def _configure(spec, record, repo, target, context):
    work = _root(spec['worktree'])
    if work == repo or _common(work) != _common(repo):
        raise DeliveryError('task worktree must belong to the specified repository')
    branch = _branch(work)
    if not branch or branch in ('main', 'master', target or ''):
        raise DeliveryError('task worktree must be on a non-main branch distinct from the '
                            'target branch')
    baseline = _head(repo)
    head = _head(work)
    if head != baseline:
        # A clean worktree whose head is already contained in the baseline has
        # no task content: moving the branch forward discards nothing. A head
        # with its own commits is refused, not reset.
        previous = head
        safe = not _status(work) and _is_ancestor(head, baseline, work)
        if not safe:
            raise DeliveryError('worktree is not at the target baseline and already has '
                                'commits; refusing to rebase or discard them')
        subprocess.run(['git', '--no-optional-locks', 'reset', '--hard', baseline],
                       cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        record['baseline_rebased_from'] = previous
        record['baseline_rebased_to'] = baseline
        head = baseline
    if _status(work):
        raise DeliveryError('task worktree is not clean')
    record.update(worktree=str(work), branch=branch, base=head,
                  target_base=baseline if context.merge else None,
                  executor_agent=context.plan['executor_agent'],
                  reviewer_agent=context.plan['reviewer_agent'])


def _process(context, spec, record):
    if spec['errors']:
        raise DeliveryError('task configuration is invalid: ' + '; '.join(spec['errors']))
    status = record.get('status')
    if status == 'CANCELLED':
        return False
    if status in ('VERIFIED', 'MERGED', 'APPROVED'):
        _authenticate(record, context, spec)
        if status in ('VERIFIED', 'MERGED'):
            return False
        if not context.merge or record.get('phase') == 'approved':
            return False
    elif status in STOPPED:
        return False
    if status in ('PENDING', 'RUNNING'):
        for dep in spec['depends']:
            dependency = context.record(dep)
            if dependency.get('status') != TERMINAL_OK:
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
        _configure(spec, record, context.repo, context.target, context)
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
    if record.get('phase') in ('merge', 'approved'):
        _merge(context, spec, record)
    if record.get('phase') == 'post_verify':
        _post_verify(context, spec, record)
    return True


def _overlap(left, right):
    return any(a == b or a.startswith(b.rstrip('/') + '/') or b.startswith(a.rstrip('/') + '/')
               for a in left for b in right)


def _attempt_retry(context, spec, record):
    """Retry only when no task content and no running agent can be at risk."""
    if spec['errors']:
        raise DeliveryError('task configuration is invalid: ' + '; '.join(spec['errors']))
    if record.get('status') not in STOPPED:
        raise DeliveryError(f'task {spec["id"]} is {record.get("status")}, not retryable')
    if record.get('phase') not in ('execute', 'verify', 'review'):
        raise DeliveryError(f'task {spec["id"]} cannot be retried from phase '
                            f'{record.get("phase")!r}')
    work = _task_root(spec)
    if _status(work):
        raise DeliveryError('retry refused: the worktree has uncommitted changes. Inspect them '
                            'and clean up manually; this runner will not stash or reset data')
    if record.get('base') and _head(work) != record['base']:
        raise DeliveryError('retry refused: the worktree has commits from the failed attempt. '
                            'Inspect them and reset manually; this runner will not discard them')
    if _pid_alive(record.get('pid')) is not False:
        raise DeliveryError('retry refused: the previous runner process may still be alive')
    session = record.get('execution_session')
    if session:
        probe = context.session_probe(session, spec['worktree'])
        if not probe.get('ok') or probe.get('outcome') not in ('succeeded', 'failed'):
            raise DeliveryError('retry refused: the previous agent session has no verifiable '
                                'terminal outcome')
    attempt = int(record.get('attempt') or 1)
    history = record.get('history') or []
    history.append({'attempt': attempt, 'status': record.get('status'),
                    'phase': record.get('phase'), 'reason': record.get('reason'),
                    'head': record.get('head')})
    record.update(history=history, status='PENDING', phase='execute', attempt=attempt + 1,
                  reason=None, head=None, diff_sha256=None, changed_paths=[],
                  verification=None, review_verdict=None, review_session=None,
                  execution_session=None, merge_commit=None, post_verification=None)
    context.save()


def run(repo, plan, state_path, *, agent=None, session_probe=session_export,
        profiles=agent_profiles, gh=gh_run, resume=False, retry=(),
        executor_agent=None, reviewer_agent=None, timeout=1800):
    repo = _root(repo)
    specs = _plan_tasks(plan)
    merge = plan.get('merge', True)
    target = None
    if merge:
        target = _branch_name(plan.get('merge_into'))
        if _branch(repo) != target:
            raise ValueError(f'repository worktree is on {_branch(repo)!r}, '
                             f'not the merge target {target!r}')
        if _status(repo):
            raise ValueError('merge target worktree is not clean')
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
    # Authorization is a plan-level property: an unauthorized task is a defect,
    # not something to isolate per task.
    for spec in specs:
        if spec['errors']:
            continue
        _authorize_paths(spec['paths'], config)
    if merge and config.get('edition') != 'local':
        raise DeliveryError(
            "local auto-merge is only permitted for docs/PROJECT.md edition 'local'; "
            f"found {config.get('edition')!r}. On the github edition the runner stays "
            'prepare-only (merge: false) and the merge belongs to the Master PR + CI flow')

    # Preflight every input *before* the first journal write. Only tasks that
    # have not started yet are (re)validated; a started task's recorded facts
    # drive recovery, and a new preflight error must not overwrite its status.
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
            if work == repo or _common(work) != _common(repo):
                raise ValueError('worktree does not belong to the repository')
            branch = _branch(work)
            if not branch or branch in ('main', 'master', target or ''):
                raise ValueError('worktree must be on a non-main branch distinct from the target')
            if _status(work):
                raise ValueError('worktree is not clean')
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            spec['errors'].append(f'task worktree is not usable: {exc}')

    plan = dict(plan, executor_agent=executor_agent, reviewer_agent=reviewer_agent)
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    carrier_digest = (plan.get('carrier') or {}).get('digest')

    locks = _acquire_locks(_lock_targets(repo, state_path, specs))
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

        if agent is None:
            def agent(work, prompt, role):
                name = reviewer_agent if role == 'review' else executor_agent
                return opencode(work, prompt, role, agent_name=name, timeout=timeout)

        def saver():
            _save(state_path, state)

        context = _Context(repo, target, merge, state_path, plan, specs, state,
                           agent, session_probe, profiles, saver, config)
        for spec in specs:
            state['tasks'].setdefault(spec['id'], {'status': 'PENDING'})
        for spec in specs:
            record = context.record(spec['id'])
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
        tasks[ident] = {
            'status': record.get('status'), 'phase': record.get('phase'),
            'attempt': record.get('attempt'), 'head': record.get('head'),
            'diff_sha256': record.get('diff_sha256'), 'changed_paths': record.get('changed_paths'),
            'execution_session': record.get('execution_session'),
            'review_session': record.get('review_session'),
            'review_verdict': record.get('review_verdict'),
            'merge_commit': record.get('merge_commit'),
            'verification': [{'program': check.get('program'),
                              'argv_sha256': check.get('argv_sha256'),
                              'exit_code': check.get('exit_code')}
                             for check in record.get('verification') or []],
            'post_verification': [{'program': check.get('program'),
                                   'argv_sha256': check.get('argv_sha256'),
                                   'exit_code': check.get('exit_code')}
                                  for check in record.get('post_verification') or []],
            'reason': record.get('reason')}
    return {'tasks': tasks, 'carrier_url': state.get('carrier_url'),
            'project_edition': state.get('project_edition')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='authorized target worktree')
    parser.add_argument('--plan', help='trusted JSON plan path')
    parser.add_argument('--issue', type=int, help='GitHub Issue number as the plan source')
    parser.add_argument('--issue-repo', help='owner/name of the issue repository')
    parser.add_argument('--worktree', help='task worktree when planning from an issue')
    parser.add_argument('--merge-into', help='target branch; omit for prepare-only runs')
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
    try:
        if args.issue is not None:
            if not args.issue_repo or not args.worktree:
                parser.error('--issue needs --issue-repo and --worktree')
            plan = load_issue_plan(args.issue, args.issue_repo, worktree=args.worktree,
                                   verify=args.verify, acceptance=args.acceptance,
                                   allows=args.allow, merge_into=args.merge_into,
                                   instruction=args.instruction)
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
