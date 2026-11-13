"""Opt-in, single-process delivery runner; merge and carrier updates remain blocked.

The operator supplies a trusted JSON plan, repository and already-created task
worktrees. OpenCode retains its existing permissions: worktrees are not OS
sandboxes. Never point this tool at repositories or commands not authorized by
the user. Model text is not approval. Only standard library is required.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


def git(where, *args):
    result = subprocess.run(['git', '--no-optional-locks', *args], cwd=where,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return result.stdout


def _root(path):
    path = Path(path).resolve(strict=True)
    if Path(os.fsdecode(git(path, 'rev-parse', '--show-toplevel')).strip()).resolve() != path:
        raise ValueError('path must be a Git worktree root')
    return path


def _common(path):
    return Path(os.fsdecode(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir')).strip()).resolve()


def _head(path):
    return os.fsdecode(git(path, 'rev-parse', 'HEAD')).strip()


def _changes(path, base):
    tracked = git(path, 'diff', '--no-ext-diff', '--no-renames', '--name-only', '-z', base, '--')
    tracked += git(path, 'diff', '--cached', '--no-ext-diff', '--no-renames', '--name-only', '-z', base, '--')
    untracked = git(path, 'ls-files', '--others', '--exclude-standard', '-z')
    return sorted(set(os.fsdecode(x) for x in (tracked + untracked).split(b'\0') if x))


def _fingerprint(path, base):
    digest = hashlib.sha256()
    digest.update(git(path, 'diff', '--no-ext-diff', '--binary', '--no-renames', base, '--'))
    digest.update(git(path, 'diff', '--cached', '--no-ext-diff', '--binary', '--no-renames', base, '--'))
    for name in _changes(path, base):
        file = path / name
        digest.update(os.fsencode(name) + b'\0')
        if file.is_symlink():
            digest.update(os.fsencode(os.readlink(file)))
        elif file.is_file():
            digest.update(file.read_bytes())
    return digest.hexdigest()


def _allowed(name, paths):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or not p.parts or any(x in ('.git', '.orchestration') for x in p.parts):
        return False
    return any(name == allowed or name.startswith(allowed.rstrip('/') + '/') for allowed in paths)


def _save(path, state):
    fd, tmp = tempfile.mkstemp(prefix='.delivery-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def opencode(worktree, prompt, role):
    """Invoke a new real session; neither --auto nor session continuation is used."""
    try:
        result = subprocess.run(['opencode', 'run', '--standalone', '--format', 'json', prompt],
                                cwd=worktree, stdin=subprocess.DEVNULL,
                                env=dict(os.environ, PWD=str(worktree)),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired):
        return {'exit_code': None, 'session_id': None}
    session = None
    finish = False
    text = []
    try:
        for line in result.stdout.splitlines():
            event = json.loads(line)
            sid = event.get('sessionID')
            if sid:
                if session and session != sid:
                    return {'exit_code': None, 'session_id': None}
                session = sid
            if event.get('type') == 'error':
                return {'exit_code': result.returncode or 1, 'session_id': session}
            if event.get('type') == 'step_finish':
                finish = event.get('part', {}).get('reason') == 'stop'
            if event.get('type') == 'text':
                text.append(event.get('part', {}).get('text', ''))
    except (ValueError, AttributeError):
        return {'exit_code': None, 'session_id': session}
    return {'exit_code': result.returncode if finish and session else None, 'session_id': session,
            'text': ''.join(text)}


def _validate_plan(plan):
    tasks = plan.get('tasks') if isinstance(plan, dict) else None
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('plan needs a nonempty tasks list')
    ids = set()
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get('id'), str) or not task['id'] or task['id'] in ids:
            raise ValueError('task ids must be nonempty and unique')
        ids.add(task['id'])
        if not isinstance(task.get('prompt'), str) or not task['prompt'].strip():
            raise ValueError('task prompt required')
        paths = task.get('paths')
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p or not _allowed(p, [p]) or p in ('.', '/') for p in paths):
            raise ValueError('task requires safe relative paths')
        commands = task.get('verify')
        if not isinstance(commands, list) or not commands or any(not isinstance(c, list) or not c or any(not isinstance(x, str) or not x for x in c) for c in commands):
            raise ValueError('verification must be nonempty argv lists')
        if not isinstance(task.get('depends'), list) or any(not isinstance(x, str) for x in task['depends']):
            raise ValueError('depends must be a list of ids')
    if any(dep not in ids or dep == t['id'] for t in tasks for dep in t['depends']):
        raise ValueError('unknown or self dependency')
    return tasks


def run(repo, plan, state_path, *, agent=opencode):
    repo = _root(repo)
    tasks = _validate_plan(plan)
    state_path = Path(state_path).absolute()
    if state_path.is_symlink() or not state_path.parent.is_dir() or state_path.resolve().is_relative_to(repo):
        raise ValueError('state must be outside the repository and not a symlink')
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    lock_path = state_path.with_name(state_path.name + '.lock')
    with open(lock_path, 'a', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(state_path.read_text()) if state_path.exists() else {'version': 1, 'tasks': {}}
        if state.get('version') != 1 or not isinstance(state.get('tasks'), dict):
            raise ValueError('invalid state')
        if state.get('plan_hash') not in (None, plan_hash):
            raise ValueError('plan changed; reconcile existing state manually')
        state['plan_hash'] = plan_hash
        for task in tasks:
            ident = task['id']
            previous = state['tasks'].get(ident)
            if previous:
                if previous.get('status') == 'RUNNING':
                    previous.update(status='UNKNOWN', reason='interrupted; reconcile manually')
                    _save(state_path, state)
                continue
            if any(state['tasks'].get(dep, {}).get('status') != 'VERIFIED' for dep in task['depends']):
                continue
            record = {'status': 'RUNNING'}
            state['tasks'][ident] = record
            _save(state_path, state)
            try:
                work = _root(task['worktree'])
                if work == repo or _common(work) != _common(repo):
                    raise ValueError('task worktree must belong to the specified repository and differ from it')
                if state_path.resolve().is_relative_to(work):
                    raise ValueError('state must be outside the task worktree')
                branch = os.fsdecode(git(work, 'branch', '--show-current')).strip()
                repo_branch = os.fsdecode(git(repo, 'branch', '--show-current')).strip()
                if not branch or branch in ('main', 'master', repo_branch):
                    raise ValueError('task worktree must be on a non-main branch')
                if git(work, 'status', '--porcelain', '-z'):
                    raise ValueError('task worktree is not clean')
                base = _head(work)
                record.update(base=base, worktree=str(work))
                _save(state_path, state)
                packet = ('You are an execution agent. Work only within these paths: '
                          + json.dumps(task['paths']) + '. Do not push, merge, approve, or modify other repositories.\n'
                          + task['prompt'])
                execution = agent(work, packet, 'execution')
                record['execution_session'] = execution.get('session_id')
                record['execution_text'] = execution.get('text', '')
                if execution.get('exit_code') != 0 or not execution.get('session_id'):
                    raise ValueError('execution failed or terminal state unknown')
                changed = _changes(work, base)
                record.update(head=_head(work), changed_paths=changed)
                if not changed or any(not _allowed(p, task['paths']) for p in changed):
                    raise ValueError('scope violation or no changes')
                for name in changed:
                    current = work
                    for part in PurePosixPath(name).parts:
                        current = current / part
                        if current.is_symlink():
                            raise ValueError('symlink change cannot be safely checked')
                fingerprint = _fingerprint(work, base)
                checks = []
                record['verification'] = checks
                for command in task['verify']:
                    try:
                        check = subprocess.run(command, cwd=work, stdin=subprocess.DEVNULL,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               timeout=1800)
                        code = check.returncode
                    except (OSError, subprocess.TimeoutExpired):
                        code = None
                    checks.append({'argv': command, 'exit_code': code})
                    _save(state_path, state)
                    if code != 0:
                        raise ValueError('verification failed or timed out')
                if _head(work) != record['head'] or _fingerprint(work, base) != fingerprint:
                    raise ValueError('head or diff changed during verification')
                record['diff_sha256'] = fingerprint
                _save(state_path, state)
                review = agent(work, 'Independently review the current task changes for defects. '
                               'Do not modify files, approve, merge, or push. Task: ' + task['prompt'], 'review')
                record['review_session'] = review.get('session_id')
                record['review_text'] = review.get('text', '')
                if review.get('exit_code') != 0 or not review.get('session_id') or review.get('session_id') == record['execution_session']:
                    raise ValueError('independent review session failed or unknown')
                if _head(work) != record['head'] or _fingerprint(work, base) != fingerprint:
                    raise ValueError('head or diff changed during review')
                record.update(status='BLOCKED', reason='independent approval and merge not implemented')
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                record.update(status='BLOCKED', reason=str(exc))
            _save(state_path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--execute', action='store_true', help='explicitly opt into execution')
    args = parser.parse_args()
    if not args.execute:
        parser.error('explicit --execute is required')
    try:
        plan = json.loads(Path(args.plan).read_text())
        state = run(args.repo, plan, args.state)
        safe = dict(state, tasks={key: {k: v for k, v in record.items()
                                       if k not in ('execution_text', 'review_text')}
                                  for key, record in state['tasks'].items()})
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 0 if all(state['tasks'].get(t['id'], {}).get('status') == 'VERIFIED'
                        for t in _validate_plan(plan)) else 1
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
