"""Batch delivery coordinator: execute, verify, review, integrate and merge.

A batch plan is a JSON document naming several tasks, one integration worktree
and one delivery finalization. The coordinator runs each task in its own real
Git worktree with one real ``opencode run`` session, verifies it with the
plan's commands, merges it into one batch branch, then performs one independent
review, one pull request, one check gate and one merge for the whole batch.
``INTEGRATED`` is an internal milestone only; ``VERIFIED`` requires a merge, a
post-merge verification run and a recorded carrier update.

Everything is fail-closed. Unknown task or worktree state, changes outside a
task's declared paths, a review verdict that does not bind the exact base/head/
diff, a head or diff that moved after review, a missing required check, a plan
hash mismatch, a journal inside the repository, overlapping task paths,
configured commit hooks or clean filters, and a second merge attempt all stop
the command with a non-zero exit.

Limitations, stated honestly:

* this is **not a sandbox**. A worktree, a prompt and path checks do not limit
  OpenCode's configured permissions; a session can reach the host filesystem,
  shell, network and credentials. The reviewer profile's ``edit`` denial is a
  configuration check, not OS isolation.
* the reviewer transcript and the execution session export are writable files.
  Consistency between the stream, the export and the journal is checked, but it
  is not tamper-proof; nothing here signs a transcript.
* ``git commit`` runs clean/process filters and commit hooks inside its own
  process. Git exposes no switch that keeps a clean filter from running, so the
  coordinator refuses to start a commit while any of them can execute instead
  of claiming to bypass them.
* external project changes are not authorized. The plan's paths are checked
  against ``docs/PROJECT.md`` when its policy sections exist; a missing section
  is recorded as ``project_paths: unknown`` and is never claimed as checked.
* the journal, lock and local task library are writable by the same user. The
  coordinator narrows recovery windows; it cannot make them atomic.

Only the Python standard library is used. No shell, no network in any test
path; ``gh`` and OpenCode are optional executables reached through
``BATCH_GH``/``BATCH_OPENCODE`` when the caller opts in.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

try:  # run as a script (scripts/ on sys.path) or imported as scripts.batch_delivery
    from delivery_risk import RiskError, classify as classify_risk
except ImportError:  # pragma: no cover - exercised by the module import path
    from scripts.delivery_risk import RiskError, classify as classify_risk

JOURNAL_SCHEMA = 'batch-delivery-journal/1'
PLAN_VERSION = 1
TASK_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$')
BRANCH_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')
SHA_RE = re.compile(r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
DIGEST_RE = re.compile(r'^[0-9a-f]{64}$')
MAX_DIFF_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT = 1800
DEFAULT_CHECKS_TIMEOUT = 3600
TERMINAL_BAD = ('FAILED', 'BLOCKED', 'SKIPPED')
INTEGRATED = ('INTEGRATED', 'VERIFIED')
STATUSES = ('PENDING', 'RUNNING', 'VERIFIED', 'INTEGRATED', 'FAILED', 'BLOCKED',
            'SKIPPED')
PHASES = ('tasks', 'review', 'pr', 'merge', 'post_merge', 'verified', 'blocked')
CHAIN = ('PLANNED', 'READY', 'DISPATCHED', 'IN_PROGRESS', 'REVIEW_READY',
         'MASTER_REVIEW', 'APPROVED', 'MERGED', 'VERIFIED')
FAILED_CHECKS = ('FAILURE', 'CANCELLED', 'TIMED_OUT', 'ACTION_REQUIRED', 'ERROR')
REGISTRY_STATUSES = ('READY', 'DISPATCHED', 'IN_PROGRESS', 'PR_OPEN',
                     'REVIEW_READY', 'MASTER_REVIEW', 'APPROVED', 'MERGED',
                     'VERIFIED', 'BLOCKED', 'NEEDS_HUMAN_DECISION', 'FAILED',
                     'CANCELED')
RELEASED_REGISTRY_STATUSES = ('VERIFIED', 'CANCELED')
MERGE_METHODS = ('merge', 'squash', 'rebase')
VALID_EDITIONS = ('local', 'github')
SECRET_PATTERNS = ('.env', '.env.*', '*secret*', '*token*', '*.pem', '*.key',
                   'credentials*')
SECRET_EXCEPTIONS = ('.env.example',)
# A declaration is a bullet whose first token is `edition`, with any mix of
# backticks or Markdown emphasis around it. Value parsing stays line-exact.
EDITION_RE = re.compile(
    r'^[ \t]*[-*+][ \t]+(?P<form>[*_`]*edition[*_`]*)'
    r'(?=[ \t:：]|$)(?P<rest>[^\n]*)$', re.M)
VERDICT_KEYS = {'verdict', 'base', 'head', 'diff_sha256', 'risk', 'tasks',
                'findings'}


class InputError(Exception):
    """Invalid plan, journal or CLI input; the CLI exits 2."""


class BatchError(Exception):
    """A task or batch gate failed; the CLI exits 1.

    ``status`` is the task status to record, ``halt`` stops all later tasks
    (used when a timed-out process tree could not be proved stopped), and
    ``conflicts`` carries merge-conflict paths for the summary.
    """

    def __init__(self, message, status='BLOCKED', *, halt=False, conflicts=None,
                 verify=None):
        super().__init__(message)
        self.status = status
        self.halt = halt
        self.conflicts = list(conflicts or [])
        self.verify = list(verify or [])

    @property
    def record_status(self):
        return self.status if self.status in STATUSES else 'BLOCKED'


# --------------------------------------------------------------------------- git

def _git(where, *args, timeout=120, binary=False):
    return subprocess.run(['git', '--no-optional-locks', *args], cwd=str(where),
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout,
                          text=not binary)


def _git_text(where, *args, timeout=120):
    result = _git(where, *args, timeout=timeout)
    if result.returncode != 0:
        raise BatchError(f'git {" ".join(args)} failed: '
                         f'{(result.stderr or "").strip()[:400]}')
    return (result.stdout or '').strip()


def _git_ok(where, *args, timeout=120):
    return _git(where, *args, timeout=timeout).returncode == 0


def _repo_root(path):
    result = _git(path, 'rev-parse', '--show-toplevel')
    if result.returncode != 0:
        raise InputError(f'not a Git worktree: {path}')
    return Path((result.stdout or '').strip()).resolve()


def _head(where):
    return _git_text(where, 'rev-parse', 'HEAD')


def _branch(where):
    return _git_text(where, 'branch', '--show-current')


def _clean(where):
    result = _git(where, 'status', '--porcelain')
    return result.returncode == 0 and not (result.stdout or '').strip()


def _ancestor(where, ancestor, descendant):
    return _git_ok(where, 'merge-base', '--is-ancestor', ancestor, descendant)


def _resolve_commit(where, ref):
    result = _git(where, 'rev-parse', '--verify', '--quiet', ref + '^{commit}')
    value = (result.stdout or '').strip() if result.returncode == 0 else ''
    if not value or not SHA_RE.match(value):
        raise InputError(f'unknown ref {ref!r} in {where}')
    return value


def _branch_exists(repo, branch):
    return _git_ok(repo, 'show-ref', '--verify', '--quiet', f'refs/heads/{branch}')


def _nul_names(where, *args):
    """Paths from one Git command with ``-z``, decoded byte-for-byte."""
    result = _git(where, *args, binary=True)
    if result.returncode != 0:
        raise BatchError(f'git {" ".join(args)} failed')
    return sorted({part.decode('utf-8', 'surrogateescape')
                   for part in (result.stdout or b'').split(b'\0') if part})


def _nul_status_paths(where, *args):
    """Parse ``--name-status -z`` output into ``(status, path)`` pairs."""
    result = _git(where, *args, binary=True)
    if result.returncode != 0:
        raise BatchError(f'git {" ".join(args)} failed')
    fields = (result.stdout or b'').split(b'\0')
    pairs = []
    index = 0
    while index + 1 < len(fields):
        status = fields[index].decode('ascii', 'replace')[:1]
        path = fields[index + 1].decode('utf-8', 'surrogateescape')
        index += 2
        if status and path:
            pairs.append((status, path))
    return pairs


def _changed_paths(work, base):
    """Tracked and untracked paths that differ from ``base`` in the worktree."""
    tracked = _nul_names(work, 'diff', '--no-ext-diff', '--no-renames',
                         '--name-only', '-z', base, '--')
    untracked = _nul_names(work, 'ls-files', '--others', '--exclude-standard',
                           '-z')
    return sorted(set(tracked) | set(untracked))


def _diff_names(work, base, head):
    pairs = _nul_status_paths(work, 'diff', '--no-ext-diff', '--no-renames',
                              '--name-status', '-z', f'{base}..{head}')
    return sorted({path for _, path in pairs})


def _changed_paths_range(work, base, head):
    """Paths the head adds relative to its merge base with ``base``."""
    return _nul_names(work, 'diff', '--no-ext-diff', '--no-renames',
                      '--name-only', '-z', f'{base}...{head}')


# -------------------------------------------------------------------- path rules

def _safe_rel(name):
    """Reject every relative task path that is ambiguous across platforms."""
    if not isinstance(name, str) or not name:
        raise ValueError('empty path')
    if '\x00' in name:
        raise ValueError('path contains a NUL byte')
    if '\\' in name:
        raise ValueError(f'path {name!r} contains a backslash separator')
    if re.match(r'^[A-Za-z]:', name) or name.startswith('/'):
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
    if any(part in ('.git', '.orchestration', '.local') for part in parts):
        raise ValueError(f'path {name!r} is a Git, orchestration or local-state path')
    return name


def _overlaps(left, right):
    return (left == right or left.startswith(right.rstrip('/') + '/')
            or right.startswith(left.rstrip('/') + '/'))


def _under(name, prefixes):
    return any(name == prefix or name.startswith(prefix.rstrip('/') + '/')
               for prefix in prefixes)


def _secret_path(name, patterns):
    basename = name.rsplit('/', 1)[-1]
    if basename in SECRET_EXCEPTIONS:
        return False
    return any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(basename, pattern)
               for pattern in patterns)


def _is_within(path, root):
    try:
        return Path(path).resolve() == Path(root).resolve() or \
            Path(path).resolve().is_relative_to(Path(root).resolve())
    except (OSError, RuntimeError):
        return False


# ----------------------------------------------------------------- project config

def project_config(repo):
    """Read the edition and path policy from ``docs/PROJECT.md``.

    The edition rule is the kit's strict one-line rule: exactly one declaration
    whose value is ``local`` or ``github``. A missing, duplicate, empty or
    invalid declaration is a preflight error, never a default.
    """
    path = Path(repo) / 'docs' / 'PROJECT.md'
    if not path.is_file():
        raise InputError('docs/PROJECT.md is required to choose an edition')
    text = path.read_text(encoding='utf-8', errors='replace')
    values = [_edition_value(form, rest) for form, rest in EDITION_RE.findall(text)]
    return {'edition': values[0] if len(values) == 1 else None,
            'edition_count': len(values), 'edition_values': values,
            'allowed': _policy_paths(text, '允许改动路径'),
            'forbidden': _policy_paths(text, '禁止改动路径') or [],
            'secrets': _secret_patterns(text),
            'verification': _project_verification(text)}


def _section_text(text, heading):
    match = re.search(r'^##+\s+' + re.escape(heading) + r'\s*$', text, re.M)
    if not match:
        return None
    body = text[match.end():]
    stop = re.search(r'^##+\s+', body, re.M)
    return body[:stop.start()] if stop else body


def _norm_argv(argv):
    argv = list(argv)
    if len(argv) >= 2 and argv[0] == 'uv' and argv[1] == 'run':
        argv = argv[2:]
    if argv:
        base = os.path.basename(argv[0])
        if base in ('python', 'python3') or argv[0] == sys.executable:
            argv[0] = '<python>'
    return tuple(argv)


def _project_verification(text):
    """Staged commands declared in `docs/PROJECT.md` under `## 验证命令`.

    Only the canonical command labels are read. The section also carries
    explanatory prose with backticked placeholders (for example a
    ``verification_profile`` command with ``--evidence <json>``); treating every
    backtick as a required command would make the plan impossible to satisfy.
    A labeled line whose commands cannot be interpreted fails closed.
    """
    section = _section_text(text, '验证命令')
    if section is None:
        return None
    stages = (('快速测试', 'post'), ('完整回归', 'post'),
              ('静态检查', 'post'), ('合并后必须执行', 'post'))
    known = ('python', 'python3', 'uv', 'pytest', 'npm', 'yarn', 'pnpm', 'go',
             'cargo', 'make', 'bash', 'sh', 'dotnet')
    requirements = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if line.startswith(('- ', '* ')):
            line = line[2:].strip()
        stage = None
        for label, candidate in stages:
            if line.startswith(label):
                stage = candidate
                break
        if stage is None:
            continue
        commands = []
        for token in re.findall(r'`([^`]+)`', line):
            if '<' in token or '>' in token:
                continue  # placeholder, not a runnable command
            try:
                argv = shlex.split(token)
            except ValueError as exc:
                raise InputError(
                    f'docs/PROJECT.md verification line for {line.split("：", 1)[0]} '
                    f'has an uninterpretable command {token!r}: {exc}') from None
            if not argv:
                continue
            first = os.path.basename(argv[0])
            if (first not in known and argv[0] != sys.executable
                    and not first.endswith(('.py', '.sh'))):
                continue
            commands.append(_norm_argv(argv))
        if not commands:
            raise InputError(
                'docs/PROJECT.md verification line for '
                f'{line.split("：", 1)[0]} declares no interpretable command; '
                'refusing to silently drop an ambiguous requirement')
        requirements.extend((stage, command) for command in commands)
    return requirements


def _edition_value(form, raw):
    if form not in ('edition', '`edition`'):
        return ''
    value = raw.strip()
    if not value or value[0] not in ':：':
        return ''
    value = value[1:].strip()
    if len(value) >= 2 and value.startswith('`') and value.endswith('`'):
        value = value[1:-1]
    return value


def _policy_paths(text, needle):
    for line in text.splitlines():
        if needle in line:
            paths = []
            for token in re.findall(r'`([^`]+)`', line):
                try:
                    paths.append(_safe_rel(token.strip().rstrip('/')))
                except ValueError:
                    continue
            return paths or None
    return None


def _secret_patterns(text):
    for line in text.splitlines():
        if '秘密与隐私文件模式' in line:
            return [token.strip() for token in re.findall(r'`([^`]+)`', line)
                    if token.strip()]
    return []


def _require_edition(config, expected=None):
    if config.get('edition_count') != 1 or config.get('edition') not in VALID_EDITIONS:
        raise InputError(
            'docs/PROJECT.md must declare exactly one `edition` of `local` or '
            f'`github`; found {config.get("edition_count")} declaration(s), values '
            f'{config.get("edition_values")!r}')
    if expected is not None and config['edition'] != expected:
        raise InputError(f'docs/PROJECT.md switched edition from {expected!r} to '
                         f'{config["edition"]!r}')


# ------------------------------------------------------------------- plan input

def _parse_argv_list(value, what):
    if (not isinstance(value, list) or not value
            or any(not isinstance(token, str) or not token for token in value)):
        raise InputError(f'{what} must be a nonempty argv list')
    return list(value)


def _parse_commands(value, what):
    if not isinstance(value, list) or not value:
        raise InputError(f'{what} must be a nonempty list of argv lists')
    return [_parse_argv_list(command, what) for command in value]


def _required_string(raw, key, what):
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InputError(f'{what}.{key} is required')
    return value.strip()


def _valid_branch(value, what):
    if not isinstance(value, str) or not BRANCH_RE.match(value):
        raise InputError(f'{what} is not a valid branch name: {value!r}')
    if value in ('main', 'master'):
        raise InputError(f'{what} must not be {value!r}')
    if '..' in value or value.endswith(('.lock', '.', '/')) or value.startswith('/'):
        raise InputError(f'{what} is not a valid branch name: {value!r}')
    return value


def _absolute(value, what):
    if not isinstance(value, str) or not value:
        raise InputError(f'{what} is required')
    path = Path(value)
    if not path.is_absolute():
        raise InputError(f'{what} must be an absolute path: {value!r}')
    return str(path.resolve())


def _task_spec(raw, index, known_ids, earlier_ids, batch_branch, config):
    if not isinstance(raw, dict):
        raise InputError('a task must be a mapping')
    ident = raw.get('id')
    if not isinstance(ident, str) or not TASK_ID_RE.match(ident):
        raise InputError(f'task id is invalid: {ident!r}')
    if ident in known_ids:
        raise InputError(f'task id is not unique: {ident}')
    title = _required_string(raw, 'title', f'task {ident}')
    prompt = _required_string(raw, 'prompt', f'task {ident}')
    worktree = _absolute(raw.get('worktree'), f'task {ident} worktree')
    branch = _valid_branch(raw.get('branch'), f'task {ident} branch')
    if branch == batch_branch:
        raise InputError(f'task {ident} branch equals the batch branch')
    paths_raw = raw.get('paths')
    if not isinstance(paths_raw, list) or not paths_raw:
        raise InputError(f'task {ident} paths must be a nonempty list')
    paths = []
    for value in paths_raw:
        try:
            paths.append(_safe_rel(value.rstrip('/') if isinstance(value, str) else value))
        except ValueError as exc:
            raise InputError(f'task {ident}: {exc}') from None
        if _secret_path(paths[-1], SECRET_PATTERNS):
            raise InputError(f'task {ident}: path {paths[-1]!r} is secret-shaped')
    acceptance = raw.get('acceptance')
    if (not isinstance(acceptance, list) or not acceptance
            or any(not isinstance(item, str) or not item.strip()
                   for item in acceptance)):
        raise InputError(f'task {ident} acceptance criteria are required')
    acceptance = [item.strip() for item in acceptance]
    acceptance_checks = raw.get('acceptance_checks')
    if acceptance_checks is not None:
        if (not isinstance(acceptance_checks, list)
                or len(acceptance_checks) != len(acceptance)):
            raise InputError(f'task {ident} acceptance_checks must be a list with '
                             'one argv list per acceptance criterion')
        checks = []
        for command in acceptance_checks:
            if (not isinstance(command, list) or not command
                    or any(not isinstance(token, str) or not token
                           for token in command)):
                raise InputError(f'task {ident} acceptance_checks entries must be '
                                 'nonempty argv lists')
            checks.append(list(command))
    else:
        checks = None
    decisions_raw = raw.get('acceptance_decisions')
    if decisions_raw is not None:
        if (not isinstance(decisions_raw, list)
                or len(decisions_raw) != len(acceptance)):
            raise InputError(f'task {ident} acceptance_decisions must be a list '
                             'with one decision per acceptance criterion')
        decisions = []
        for index, item in enumerate(decisions_raw, start=1):
            if not isinstance(item, dict):
                raise InputError(f'task {ident} acceptance_decision {index} must '
                                 'be an object')
            if item.get('id') != f'AC-{index}':
                raise InputError(f'task {ident} acceptance_decision {index} must '
                                 f'have id AC-{index}')
            if item.get('status') not in ('PASS', 'FAIL', 'NOT_RUN'):
                raise InputError(f'task {ident} AC-{index} decision status must be '
                                 'PASS, FAIL or NOT_RUN')
            if not isinstance(item.get('evidence', ''), str):
                raise InputError(f'task {ident} AC-{index} decision evidence must '
                                 'be a string')
            decisions.append({'id': f'AC-{index}', 'status': item['status'],
                              'evidence': item.get('evidence', '')})
    else:
        decisions = None
    verify = _parse_commands(raw.get('verify'), f'task {ident} verify')
    depends = raw.get('depends', [])
    if not isinstance(depends, list) or any(not isinstance(dep, str) for dep in depends):
        raise InputError(f'task {ident} depends must be a list of task ids')
    for dep in depends:
        if dep not in earlier_ids:
            raise InputError(f'task {ident} depends on {dep!r}, which is not an '
                             'earlier task in the batch')
    if config['allowed'] is not None:
        for name in paths:
            if not _under(name, config['allowed']):
                raise InputError(f'task {ident} path {name!r} is not authorized by '
                                 'docs/PROJECT.md')
    for name in paths:
        if _overlaps(name, 'docs/PROJECT.md'):
            raise InputError(
                f'task {ident} path {name!r} changes docs/PROJECT.md, which a batch '
                'may not do: the project config is bound by digest across run and '
                'finalize. Deliver project-config changes as a single task or '
                'outside the batch')
        for forbidden in config['forbidden']:
            if _overlaps(name, forbidden):
                raise InputError(f'task {ident} path {name!r} overlaps the forbidden '
                                 f'path {forbidden!r}')
        if _secret_path(name, config.get('secrets') or []):
            raise InputError(f'task {ident} path {name!r} matches a project secret '
                             'pattern')
    return {'id': ident, 'index': index, 'title': title, 'prompt': prompt,
            'worktree': worktree, 'branch': branch, 'paths': paths,
            'acceptance': acceptance, 'acceptance_checks': checks,
            'acceptance_decisions': decisions,
            'verify': verify, 'depends': list(depends)}


def load_plan(path):
    try:
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise InputError(f'cannot read the batch plan {path}: {exc}') from None
    if not isinstance(raw, dict):
        raise InputError('the batch plan must be a JSON object')
    return raw


def validate_plan(raw, repo_override=None):
    """Validate a plan and return a normalized, importable representation."""
    if raw.get('version') != PLAN_VERSION:
        raise InputError(f'plan version must be {PLAN_VERSION}')
    batch = raw.get('batch')
    if not isinstance(batch, dict):
        raise InputError('plan.batch is required')
    ident = _required_string(batch, 'id', 'batch')
    title = _required_string(batch, 'title', 'batch')
    summary = batch.get('summary') or ''
    if not isinstance(summary, str):
        raise InputError('batch.summary must be a string')
    repo_value = repo_override or batch.get('repo') or os.getcwd()
    repo = _repo_root(repo_value)
    base = _required_string(batch, 'base', 'batch')
    branch = _valid_branch(batch.get('branch'), 'batch.branch')
    if branch == base:
        raise InputError('batch.branch must differ from batch.base')
    worktree = _absolute(batch.get('worktree'), 'batch.worktree')
    if Path(worktree) == repo:
        raise InputError('batch.worktree must not be the repository root')
    issue = batch.get('issue')
    if issue is not None and (not isinstance(issue, int) or issue < 1):
        raise InputError('batch.issue must be a positive integer')
    issue_repo = batch.get('issue_repo')
    if issue_repo is not None and (not isinstance(issue_repo, str) or not issue_repo):
        raise InputError('batch.issue_repo must be a nonempty string')
    executor = _required_string(raw, 'executor_agent', 'plan')
    reviewer = _required_string(raw, 'reviewer_agent', 'plan')
    if executor == reviewer:
        raise InputError('the reviewer agent must differ from the executor agent')
    config = project_config(repo)
    _require_edition(config)
    tasks_raw = raw.get('tasks')
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise InputError('plan.tasks must be a nonempty list')
    tasks, known, identities = [], set(), []
    for index, item in enumerate(tasks_raw):
        spec = _task_spec(item, index, known, known, branch, config)
        known.add(spec['id'])
        tasks.append(spec)
        identities.append(Path(spec['worktree']).resolve())
    branches = [spec['branch'] for spec in tasks]
    if len(set(branches)) != len(branches):
        raise InputError('task branches must be unique')
    batch_identity = Path(worktree).resolve()
    for identity in identities:
        if identity == repo:
            raise InputError('a task worktree must not be the repository root')
        if identity == batch_identity:
            raise InputError('a task worktree must not be the batch worktree')
    if len(set(identities)) != len(identities):
        raise InputError('two tasks share one worktree')
    all_worktrees = identities + [batch_identity]
    for index, left in enumerate(all_worktrees):
        for right in all_worktrees[index + 1:]:
            if _is_within(left, right) or _is_within(right, left):
                raise InputError(f'worktrees must not be inside one another: '
                                 f'{left} / {right}')
    for index, left in enumerate(tasks):
        for right in tasks[index + 1:]:
            for one in left['paths']:
                for two in right['paths']:
                    if _overlaps(one, two):
                        raise InputError(f'tasks {left["id"]} and {right["id"]} have '
                                         f'overlapping paths: {one} / {two}')
    finalize_raw = raw.get('finalize')
    if not isinstance(finalize_raw, dict):
        raise InputError('plan.finalize is required')
    verify = _parse_commands(finalize_raw.get('verify'), 'finalize.verify')
    post_verify = _parse_commands(finalize_raw.get('post_merge_verify'),
                                  'finalize.post_merge_verify')
    declared = config.get('verification')
    project_verification = 'absent' if declared is None else 'bound'
    if declared is not None:
        verify_set = {_norm_argv(command) for command in verify}
        post_set = {_norm_argv(command) for command in post_verify}
        missing = []
        for stage, command in declared:
            pool = post_set if stage == 'post' else verify_set | post_set
            if command not in pool:
                missing.append((stage, command))
        if missing:
            rendered = ', '.join(
                f'{"post-merge" if stage == "post" else "pre/post"}: '
                + ' '.join(command) for stage, command in missing)
            raise InputError(
                'docs/PROJECT.md declares staged project verification command(s) '
                f'that the plan does not cover: {rendered}. Every declared '
                'quick/static/full-regression/merge-after command must be in '
                'finalize.post_merge_verify; it may also appear in verify')
    checks = finalize_raw.get('required_checks') or []
    if not isinstance(checks, list) or any(not isinstance(name, str) or not name
                                           for name in checks):
        raise InputError('finalize.required_checks must be a list of names')
    merge_method = finalize_raw.get('merge_method') or 'merge'
    if merge_method not in MERGE_METHODS:
        raise InputError('finalize.merge_method must be merge, squash or rebase')
    if config['edition'] == 'github' and not checks:
        raise InputError('a github batch needs at least one required_check')
    authorization = finalize_raw.get('authorization') or ''
    if not isinstance(authorization, str):
        raise InputError('finalize.authorization must be a string')
    if not authorization.strip():
        raise InputError('finalize.authorization must be nonempty: every merge '
                         'needs an explicit owner authorization text')
    next_plan = finalize_raw.get('next_plan')
    if next_plan is not None and (not isinstance(next_plan, str) or not next_plan):
        raise InputError('finalize.next_plan must be a nonempty path')
    return {'version': PLAN_VERSION, 'raw': raw,
            'batch': {'id': ident, 'title': title, 'summary': summary,
                      'repo': str(repo), 'base': base, 'branch': branch,
                      'worktree': worktree, 'issue': issue,
                      'issue_repo': issue_repo},
            'executor_agent': executor, 'reviewer_agent': reviewer,
            'tasks': tasks, 'config': config,
            'edition': config['edition'],
            'project_paths': 'known' if config['allowed'] is not None else 'unknown',
            'project_verification': project_verification,
            'finalize': {'verify': verify, 'post_merge_verify': post_verify,
                         'required_checks': checks, 'merge_method': merge_method,
                         'authorization': authorization, 'next_plan': next_plan}}


def plan_hash(raw):
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


# --------------------------------------------------------------------- journal

def _atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.batch-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _journal_lock_path(journal):
    journal = Path(journal).resolve()
    digest = hashlib.sha256(str(journal).encode()).hexdigest()[:24]
    return journal.parent / f'.batch-delivery-{digest}.lock'


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


class _Lock:
    """Exclusive journal lock; the OS lock is the authority, the PID is a hint."""

    def __init__(self, journal):
        self.path = _journal_lock_path(journal)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, 'a+')
        try:
            if os.name == 'nt':  # pragma: no cover - POSIX CI path
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.seek(0)
            raw = self.handle.read().strip()
            self.handle.close()
            self.handle = None
            pid = int(raw) if raw.isdigit() else None
            alive = _pid_alive(pid) if pid else None
            if alive is False:
                raise BatchError(
                    f'the journal lock is held by process {pid}, which is not '
                    'alive; inspect the lock file and remove it') from None
            where = f' by process {pid}' if pid else ''
            raise BatchError(f'the journal is locked{where}; another batch '
                             'command is running') from None
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()))
        self.handle.flush()
        return self

    def __exit__(self, *exc):
        if self.handle is not None:
            try:
                if os.name == 'nt':  # pragma: no cover
                    import msvcrt
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        return False


def _resolve_journal(value, plan, new=True):
    journal = Path(value).expanduser() if value else _default_journal(plan)
    journal = journal.absolute()
    if journal.parent.exists() is False and new:
        journal.parent.mkdir(parents=True, exist_ok=True)
    if not journal.parent.is_dir():
        raise InputError(f'journal directory does not exist: {journal.parent}')
    journal = journal.resolve()
    roots = [plan['batch']['repo'], plan['batch']['worktree']]
    roots += [task['worktree'] for task in plan['tasks']]
    for root in roots:
        if _is_within(journal, root):
            raise InputError('the journal must be outside the repository and every '
                             f'worktree; {journal} is inside {root}')
    return journal


def _default_journal(plan):
    return Path.home() / '.cache' / 'agentorch' / f'batch-{plan["batch"]["id"]}.json'


def _load_journal(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise InputError(f'cannot read the batch journal {path}: {exc}') from None
    if not isinstance(data, dict) or data.get('schema') != JOURNAL_SCHEMA:
        raise InputError(f'{path} is not a {JOURNAL_SCHEMA} journal')
    return data


def _project_config_digest(repo):
    path = Path(repo) / 'docs' / 'PROJECT.md'
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _new_journal(plan, base_sha):
    return {
        'schema': JOURNAL_SCHEMA, 'batch_id': plan['batch']['id'],
        'plan_sha256': plan_hash(plan['raw']), 'repo': plan['batch']['repo'],
        'edition': plan['edition'], 'base_ref': plan['batch']['base'],
        'base_sha': base_sha, 'batch_branch': plan['batch']['branch'],
        'project_config_sha256': _project_config_digest(plan['batch']['repo']),
        'phase': 'tasks', 'tasks': {}, 'review': None, 'pr': None,
        'merge': {'state': 'none', 'commit': None, 'authorization': None,
                  'post_verify': []},
        'dispatch': None, 'batch_verify': [], 'next_plan': None,
        'carrier': None, 'error': None, 'decision': None,
        'project_paths': plan['project_paths'],
        'project_verification': plan.get('project_verification'),
    }


def _check_journal_plan(state, plan):
    if state.get('batch_id') != plan['batch']['id']:
        raise InputError('the journal belongs to a different batch')
    if state.get('plan_sha256') != plan_hash(plan['raw']):
        raise InputError('plan changed; reconcile manually')
    if state.get('repo') != plan['batch']['repo']:
        raise InputError('the journal belongs to a different repository')
    current = _project_config_digest(plan['batch']['repo'])
    if state.get('project_config_sha256') is None:
        state['project_config_sha256'] = current
    elif current != state['project_config_sha256']:
        raise InputError(
            'docs/PROJECT.md changed after the journal was written; the project '
            'path/edition binding is stale. Reconcile the plan and start a new '
            'batch rather than reusing this journal')


def _task_record(spec, executor_agent):
    return {'status': 'PENDING', 'attempts': 0, 'branch': spec['branch'],
            'worktree': spec['worktree'], 'base': None, 'head': None,
            'changed_paths': [], 'verify': [], 'execution_session': None,
            'executor_agent': executor_agent, 'integration_sha': None,
            'error': None, 'pid': None}


# ------------------------------------------------------------------- process I/O

def _binary_argv(env, default):
    """Resolve an executable override to an argv prefix.

    ``BATCH_OPENCODE`` / ``BATCH_GH`` accept either one executable path or a
    JSON argv list. The list form keeps the test suite portable to platforms
    whose process launcher cannot execute a shebang script directly (Windows
    uses the interpreter plus script path), without enabling a shell.
    """
    raw = os.environ.get(env)
    if not raw:
        return [default]
    if raw.lstrip().startswith('['):
        try:
            value = json.loads(raw)
        except ValueError:
            raise BatchError(f'{env} is not a readable JSON argv list') from None
        if (not isinstance(value, list) or not value
                or any(not isinstance(token, str) or not token for token in value)):
            raise BatchError(f'{env} must be a nonempty JSON argv list')
        return value
    return [raw]


def _opencode_binary():
    return _binary_argv('BATCH_OPENCODE', 'opencode')


def _gh_binary():
    return _binary_argv('BATCH_GH', 'gh')


def _classify_stream(returncode, output):
    """Accept only a single-session stream that ends with one terminal stop."""
    unknown = {'exit_code': None, 'session_id': None, 'text': '',
               'detail': 'stream is not a completed single-session run'}
    if returncode != 0:
        return dict(unknown, detail=f'OpenCode exit code {returncode}')
    if not (output or '').strip():
        return dict(unknown, detail='OpenCode produced no events')
    session, text = None, []
    started = terminal = in_step = False
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


KILL_GRACE = 15


def _kill_group(process):
    if os.name == 'nt':  # pragma: no cover - POSIX CI path
        issued = False
        try:
            issued = subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL).returncode == 0
        except OSError:
            pass
        try:
            process.kill()
        except OSError:
            pass
        return issued, None
    try:
        pgid = os.getpgid(process.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, AttributeError):
        return False, None
    return True, pgid


def _prove_stopped(process, issued, pgid):
    try:
        process.communicate(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        return False
    if not issued or os.name == 'nt' or pgid is None:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _stop_timed_out(process):
    issued, pgid = _kill_group(process)
    if _prove_stopped(process, issued, pgid):
        return True, 'timed out; process group killed'
    return False, 'timed out; could not prove the process group was terminated'


def _run_process(argv, cwd, timeout, *, env=None, input_text=None):
    """Run one argv without a shell; return exit, tail and timeout facts."""
    environment = dict(os.environ, PWD=str(cwd))
    if env:
        environment.update(env)
    try:
        process = subprocess.Popen(
            argv, cwd=str(cwd), stdin=subprocess.DEVNULL if input_text is None
            else subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=environment, start_new_session=(os.name == 'posix'))
    except OSError as exc:
        return {'exit': None, 'tail': f'spawn failed: {exc}', 'timeout': False,
                'stopped': None}
    if input_text is not None:
        try:
            stdout, stderr = process.communicate(input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            proven, detail = _stop_timed_out(process)
            return {'exit': None, 'tail': detail, 'timeout': True, 'stopped': proven}
    else:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proven, detail = _stop_timed_out(process)
            return {'exit': None, 'tail': detail, 'timeout': True, 'stopped': proven}
    return {'exit': process.returncode,
            'tail': ((stdout or '') + (stderr or ''))[-2000:],
            'timeout': False, 'stopped': None}


def _agent(work, prompt, agent_name, timeout, *, title=None):
    argv = [*_opencode_binary(), 'run', '--standalone', '--format', 'json']
    if agent_name:
        argv += ['--agent', agent_name]
    if title:
        argv += ['--title', title]
    argv.append(prompt)
    environment = dict(os.environ, PWD=str(work))
    try:
        process = subprocess.Popen(
            argv, cwd=str(work), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=environment,
            start_new_session=(os.name == 'posix'))
    except OSError as exc:
        return {'exit_code': None, 'session_id': None, 'text': '',
                'detail': f'spawn failed: {exc}'}
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proven, detail = _stop_timed_out(process)
        return {'exit_code': None, 'session_id': None, 'text': '', 'timeout': True,
                'stopped': proven, 'detail': detail}
    return _classify_stream(process.returncode, output or '')


def _session_export(session, work, timeout=120):
    try:
        full = subprocess.run(
            [*_opencode_binary(), 'session', 'export', session], cwd=str(work),
            stdin=subprocess.DEVNULL, env=dict(os.environ, PWD=str(work)),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'ok': False, 'reason': f'session export unavailable: {exc}'}
    if full.returncode != 0:
        return {'ok': False, 'reason': f'session export exit {full.returncode}'}
    try:
        data = json.loads(full.stdout)
        info, messages = data['info'], data['messages']
    except (ValueError, KeyError, TypeError):
        return {'ok': False, 'reason': 'session export is not readable JSON'}
    if not isinstance(info, dict) or not isinstance(messages, list):
        return {'ok': False, 'reason': 'session export has an unexpected shape'}
    texts = []
    agents = []
    for message in messages:
        if isinstance(message, dict) and message.get('type') == 'assistant':
            agents.append(message.get('agent'))
            for part in message.get('content') or []:
                if isinstance(part, dict) and part.get('type') == 'text':
                    texts.append(part.get('text') or '')
    return {'ok': True, 'session': info.get('id'), 'outcome': info.get('outcome'),
            'directory': (info.get('location') or {}).get('directory'),
            'agents': agents, 'texts': texts}


def _agent_profiles(work, timeout=120):
    try:
        result = subprocess.run([*_opencode_binary(), 'debug', 'agents'], cwd=str(work),
                                stdin=subprocess.DEVNULL,
                                env=dict(os.environ, PWD=str(work)),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BatchError(f'agent profiles unavailable: {exc}') from None
    if result.returncode != 0:
        raise BatchError(f'agent profile query exit code {result.returncode}')
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise BatchError(f'agent profile query is not JSON: {exc}') from None
    if not isinstance(data, list):
        raise BatchError('agent profile query returned an unexpected shape')
    return {item.get('id'): item for item in data if isinstance(item, dict)}


def _rule_matches(rule, action):
    pattern = rule.get('action')
    if not isinstance(pattern, str):
        return False
    return pattern == '*' or fnmatch.fnmatchcase(action, pattern) or pattern == action


def _profile_gate(profile):
    """Fail closed unless the reviewer profile's effective rule denies edit."""
    rules = (profile or {}).get('permissions') or []
    if not isinstance(rules, list):
        return False, 'profile permissions are not a list'
    effect = None
    for rule in rules:
        if (isinstance(rule, dict) and _rule_matches(rule, 'edit')
                and rule.get('resource') == '*'):
            effect = rule.get('effect')
    if effect != 'deny':
        return False, 'the effective rule does not deny edit'
    return True, ''


# ------------------------------------------------------------------- preflight

def _inert_reasons(where):
    """Configuration that would execute code inside ``git add``/``commit``.

    Only configuration keys are reported. A filter or fsmonitor value is an
    arbitrary command line and may carry a secret, so it is never copied into
    the journal, the CLI output or a carrier comment.
    """
    reasons = []
    hooks_path = _git(where, 'config', '--get', 'core.hooksPath')
    if hooks_path.returncode == 0 and (hooks_path.stdout or '').strip():
        reasons.append('core.hooksPath is configured')
    result = _git(where, 'rev-parse', '--git-path', 'hooks')
    if result.returncode == 0:
        directory = Path((result.stdout or '').strip())
        if not directory.is_absolute():
            directory = Path(where) / directory
        if directory.is_dir():
            for entry in sorted(directory.iterdir()):
                if entry.name.endswith('.sample') or not entry.is_file():
                    continue
                if os.access(entry, os.X_OK):
                    reasons.append(f'commit hook {entry.name}')
    filters = _git(where, 'config', '--null', '--name-only', '--get-regexp',
                   r'^filter\..*\.(clean|process)$')
    if filters.returncode not in (0, 1):
        raise BatchError('cannot enumerate Git clean filters')
    if filters.returncode == 0:
        names = [name.strip() for name in (filters.stdout or '').split('\0')
                 if name.strip()]
        if names:
            reasons.append('clean/process filter(s): ' + ', '.join(sorted(set(names))))
    fsmonitor = _git(where, 'config', '--get', 'core.fsmonitor')
    if fsmonitor.returncode == 0:
        value = (fsmonitor.stdout or '').strip()
        if value not in ('', 'false', 'no', 'off', '0'):
            reasons.append('core.fsmonitor is configured')
    return reasons


def _require_inert(where):
    reasons = _inert_reasons(where)
    if reasons:
        raise BatchError(
            'creating a commit is not implemented while Git can run code inside '
            'it: ' + '; '.join(reasons) + '. The change is left for a human')


def _prepare_batch_worktree(plan, base_sha, execute):
    batch = plan['batch']
    path, branch = Path(batch['worktree']), batch['branch']
    if path.exists():
        if _repo_root(path) != path.resolve():
            raise BatchError(f'batch worktree is not a Git worktree root: {path}')
        if _branch(path) != branch:
            raise BatchError(f'batch worktree is on {_branch(path)!r}, not {branch!r}')
        if not _clean(path):
            raise BatchError('the batch worktree is not clean')
        head = _head(path)
        if not _ancestor(plan['batch']['repo'], base_sha, head):
            raise BatchError('the base ref is not an ancestor of the batch head')
        return head
    if not execute:
        return base_sha
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if _branch_exists(plan['batch']['repo'], branch):
        result = _git(plan['batch']['repo'], 'worktree', 'add', str(path), branch)
    else:
        result = _git(plan['batch']['repo'], 'worktree', 'add', '-b', branch,
                      str(path), base_sha)
    if result.returncode != 0:
        raise BatchError('cannot create the batch worktree: '
                         f'{(result.stderr or "").strip()[:400]}')
    return _head(path)


def _gh(args, cwd, *, input_text=None, timeout=300):
    argv = [*_gh_binary(), *args]
    kwargs = {
        'cwd': str(cwd),
        'env': dict(os.environ, GIT_TERMINAL_PROMPT='0'),
        'stdout': subprocess.PIPE,
        'stderr': subprocess.PIPE,
        'text': True,
        'timeout': timeout,
    }
    if input_text is None:
        kwargs['stdin'] = subprocess.DEVNULL
    else:
        kwargs['input'] = input_text
    try:
        return subprocess.run(argv, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BatchError(f'gh {" ".join(args)} unavailable: {exc}') from None


def _gh_json(args, cwd, *, input_text=None, timeout=300):
    result = _gh(args, cwd, input_text=input_text, timeout=timeout)
    if result.returncode != 0:
        raise BatchError(f'gh {" ".join(args)} failed with exit code '
                         f'{result.returncode}: {(result.stderr or "").strip()[:300]}')
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise BatchError(f'gh {" ".join(args)} returned unreadable JSON: {exc}') from None


def _open_pr_conflicts(plan, batch_branch):
    if plan['edition'] != 'github':
        return {}
    repo = plan['batch']['repo']
    payload = _gh_json(['pr', 'list', '--state', 'open', '--limit', '1000',
                        '--json', 'number,headRefName,files'], repo)
    if not isinstance(payload, list):
        raise BatchError('gh pr list returned an unexpected payload')
    if len(payload) >= 1000:
        raise BatchError('the open PR enumeration reached its 1000-item limit; '
                         'the active lock set cannot be proven complete',
                         status='BLOCKED')
    union = {name for spec in plan['tasks'] for name in spec['paths']}
    conflicts = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        head = item.get('headRefName')
        if head == batch_branch:
            continue
        files = []
        for entry in item.get('files') or []:
            if isinstance(entry, dict):
                files.append(entry.get('path') or '')
            elif isinstance(entry, str):
                files.append(entry)
        for spec in plan['tasks']:
            for name in spec['paths']:
                if any(_overlaps(name, other) for other in files if other):
                    conflicts[spec['id']] = (f'open PR #{item.get("number")} '
                                             f'({head}) touches {name}')
                    break
    issues = _gh_json(['issue', 'list', '--state', 'open', '--limit', '1000',
                       '--json', 'number,body'], repo)
    if not isinstance(issues, list):
        raise BatchError('gh issue list returned an unexpected payload')
    if len(issues) >= 1000:
        raise BatchError('the active Issue enumeration reached its 1000-item '
                         'limit; the registry lock set cannot be proven complete',
                         status='BLOCKED')
    current_issue = plan['batch'].get('issue')
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        number = issue.get('number')
        if current_issue is not None and number == current_issue:
            continue
        blocks = _registry_blocks(issue.get('body') or '')
        if not blocks:
            continue
        if len(blocks) != 1:
            conflicts[f'issue-{number}'] = (
                f'issue #{number} has {len(blocks)} ORCH-TASK-REGISTRY blocks; '
                'the active lock state is ambiguous')
            continue
        inner = blocks[0]
        status = _registry_value(inner, 'status')
        locked_raw = _registry_value(inner, 'locked_paths')
        if not status or locked_raw is None:
            conflicts[f'issue-{number}'] = (
                f'issue #{number} has an unreadable ORCH-TASK-REGISTRY block; '
                'the active lock state cannot be proven')
            continue
        if status not in REGISTRY_STATUSES:
            conflicts[f'issue-{number}'] = (
                f'issue #{number} has unknown ORCH-TASK-REGISTRY status '
                f'{status!r}; the active lock state is ambiguous')
            continue
        if status in RELEASED_REGISTRY_STATUSES:
            continue
        locked = [item.strip().rstrip('/') for item in locked_raw.split(',')
                  if item.strip() and item.strip() != '(none)']
        overlap = sorted({one for one in union for two in locked
                          if _overlaps(one, two)})
        if overlap:
            conflicts[f'issue-{number}'] = (
                f'active issue #{number} registry locks overlap the batch: '
                + ', '.join(overlap))
    return conflicts


def _task_status(text):
    match = re.search(r'^Status:\s*([A-Za-z_]+)\s*$', text or '', re.M)
    return match.group(1) if match else None


def _parse_task_show(text):
    record = {'status': _task_status(text), 'paths': [], 'dependencies': [],
              'acceptance': []}
    section = None
    for line in (text or '').splitlines():
        stripped = line.strip()
        if stripped.startswith('Allowed paths:'):
            section = 'paths'
            continue
        if stripped.startswith('Dependencies:'):
            section = 'dependencies'
            continue
        if stripped.startswith('Acceptance criteria:'):
            section = 'acceptance'
            continue
        if stripped.startswith(('Status:', 'Description:', 'Branch:', 'Worktree:',
                                'Head SHA:', 'Approved SHA:', 'Merge SHA:',
                                'Retries:', 'Last note:')):
            section = None
            continue
        if section and stripped.startswith('- '):
            item = stripped[2:].strip()
            if item.lower() in ('none', '(none)'):
                continue
            if section == 'acceptance':
                item = re.sub(r'^\[[ xX]\]\s*', '', item)
            record[section].append(item)
    return record


def _parse_task_list(text):
    records = {}
    current = None
    for line in (text or '').splitlines():
        if line and not line.startswith((' ', '\t')) and '\t' in line:
            fields = line.split('\t')
            if len(fields) >= 2:
                current = {'status': fields[1].strip(), 'paths': [], 'locks': []}
                records[fields[0].strip()] = current
            continue
        if current is None:
            continue
        stripped = line.strip()
        for key in ('paths', 'locks'):
            if stripped.startswith(key + ':'):
                value = stripped.split(':', 1)[1].strip()
                if value and value not in ('(none)', 'None'):
                    current[key] = [item.strip() for item in value.split(',')
                                    if item.strip()]
    return records


def _dispatch_local(plan, state, journal):
    repo = plan['batch']['repo']
    script = Path(repo) / 'scripts' / 'orchestrator.py'
    if not script.is_file():
        raise BatchError(
            'local batches require scripts/orchestrator.py and a task record for '
            'every task before an agent may start; the local carrier is absent. '
            'Create the task records, then rerun. Any locks already taken keep '
            'their contract and must be released through the normal CLI',
            status='BLOCKED')
    previous = state.get('dispatch') or {}
    now = _now()
    calls = list(previous.get('calls') or [])
    statuses = dict(previous.get('tasks') or {})
    retained = ('DISPATCHED', 'IN_PROGRESS', 'FAILED', 'BLOCKED', 'RUNNING',
                'UNKNOWN')
    task_ids = {spec['id'] for spec in plan['tasks']}
    shown = {}
    for spec in plan['tasks']:
        ident = spec['id']
        show = _run_orchestrator(repo, ['task', 'show', ident])
        calls.append({'argv': ['task', 'show', ident], 'exit': show.returncode,
                      'at': _now()})
        if show.returncode != 0:
            raise BatchError(
                f'task {ident} is not in the local task library, so dispatch is '
                'not authoritative; no agent was started. Any locks already taken '
                'stay in force; inspect with `task show` and release only through '
                'the normal CLI before retrying', status='BLOCKED')
        record = _parse_task_show(show.stdout)
        if record['status'] is None:
            raise BatchError(f'task {ident} has no readable Status line',
                             status='BLOCKED')
        if sorted(record['paths']) != sorted(spec['paths']):
            raise BatchError(
                f'task {ident} registered allowed paths {record["paths"]!r} do not '
                f'match the plan paths {spec["paths"]!r}; dispatch is not '
                'authoritative', status='BLOCKED')
        if record['acceptance'] != spec['acceptance']:
            raise BatchError(
                f'task {ident} registered acceptance criteria do not match the plan '
                'exactly; dispatch is not authoritative', status='BLOCKED')
        internal = sorted(set(record['dependencies']) & task_ids)
        if internal:
            raise BatchError(
                f'task {ident} records in-batch dependency {", ".join(internal)} in '
                'the local task library. This coordinator does not support local '
                'batch-internal dependencies recorded in the task library. Keep the '
                'task record intact; deliver the dependent task through a '
                'single-task/manual workflow or wait for a batch-aware local '
                'dispatch. The plan DAG does not replace task records',
                status='BLOCKED')
        if (record['status'] in ('VERIFIED', 'MERGED', 'CANCELED')
                and (state['tasks'].get(ident) or {}).get('status') != 'VERIFIED'):
            raise BatchError(
                f'task {ident} is {record["status"]} in the local library while the '
                'batch journal still expects work; refusing to treat a terminal '
                'task as dispatchable', status='BLOCKED')
        shown[ident] = record
    for spec in plan['tasks']:
        ident = spec['id']
        current = shown[ident]['status']
        if ident in statuses:
            statuses[ident] = statuses[ident] or 'DISPATCHED'
            continue
        if (current in retained
                and (state['tasks'].get(ident) or {}).get('status')
                in ('RUNNING', 'FAILED', 'BLOCKED', 'INTEGRATED', 'VERIFIED')):
            statuses[ident] = current
            continue
        if current in ('VERIFIED', 'MERGED', 'CANCELED'):
            statuses[ident] = current
            continue
        if current == 'PLANNED':
            ready = _run_orchestrator(repo, ['task', 'ready', ident])
            calls.append({'argv': ['task', 'ready', ident],
                          'exit': ready.returncode, 'at': _now()})
            if ready.returncode != 0:
                raise BatchError(
                    f'task {ident} could not move PLANNED -> READY; no agent was '
                    'started and any locks stay in force', status='BLOCKED')
            current = 'READY'
        if current == 'READY':
            dispatch = _run_orchestrator(repo, ['task', 'dispatch', ident])
            calls.append({'argv': ['task', 'dispatch', ident],
                          'exit': dispatch.returncode, 'at': _now()})
            if dispatch.returncode != 0:
                raise BatchError(
                    f'task {ident} could not be dispatched (possibly a batch '
                    'dependency the local CLI refuses); no agent was started and '
                    'any locks stay in force', status='BLOCKED')
            current = 'DISPATCHED'
        if current not in ('DISPATCHED', 'IN_PROGRESS'):
            raise BatchError(f'task {ident} is {current!r}, which is not an active '
                             'dispatch state and has no retained lock recorded; '
                             'refusing to start an agent', status='BLOCKED')
        statuses[ident] = current
    listed = _run_orchestrator(repo, ['task', 'list'])
    calls.append({'argv': ['task', 'list'], 'exit': listed.returncode,
                  'at': _now()})
    if listed.returncode != 0:
        raise BatchError('task list failed; the local path locks cannot be proved',
                         status='BLOCKED')
    locks = _parse_task_list(listed.stdout)
    for spec in plan['tasks']:
        ident = spec['id']
        entry = locks.get(ident)
        if entry is None:
            raise BatchError(f'task {ident} is missing from task list; its path '
                             'locks cannot be proved', status='BLOCKED')
        if sorted(set(entry['locks'])) != sorted(set(spec['paths'])):
            raise BatchError(
                f'task {ident} currently holds locks {entry["locks"]!r}, not the '
                f'plan paths {spec["paths"]!r}; the authoritative path lock is not '
                'held, so no agent may start', status='BLOCKED')
    state['dispatch'] = {'edition': 'local', 'at': previous.get('at') or now,
                         'calls': calls, 'tasks': statuses}
    _atomic_json(journal, state)


def _dispatch_github(plan, state, journal):
    batch = plan['batch']
    if not batch.get('issue') or not batch.get('issue_repo'):
        raise BatchError(
            'github batches require batch.issue and batch.issue_repo so the '
            'ORCH-TASK-REGISTRY dispatch record is authoritative; no agent was '
            'started', status='BLOCKED')
    repo = batch['issue_repo']
    number = str(batch['issue'])
    view = _gh_json(['issue', 'view', number, '--repo', repo, '--json',
                     'body,state'], plan['batch']['repo'])
    if not isinstance(view, dict):
        raise BatchError('gh issue view returned an unexpected payload',
                         status='BLOCKED')
    if view.get('state') not in (None, 'OPEN'):
        raise BatchError(f'carrier issue #{number} is {view.get("state")!r}, not '
                         'OPEN; refusing to dispatch', status='BLOCKED')
    body = view.get('body') or ''
    inner = _registry_inner(body)
    status = _registry_value(inner, 'status')
    branch = _registry_value(inner, 'branch')
    now = _now()
    if status in ('DISPATCHED', 'IN_PROGRESS') and branch == batch['branch'] \
            and _registry_value(inner, 'mode') == 'batch':
        locked_raw = _registry_value(inner, 'locked_paths') or ''
        locked = sorted({item.strip().rstrip('/') for item in locked_raw.split(',')
                         if item.strip() and item.strip() != '(none)'})
        planned = sorted({name for spec in plan['tasks'] for name in spec['paths']})
        if locked != planned:
            raise BatchError(
                'registry locks do not match the batch plan: the existing '
                f'ORCH-TASK-REGISTRY locks {locked!r}, but the plan union is '
                f'{planned!r}; no agent was started', status='BLOCKED')
        state['dispatch'] = {'edition': 'github', 'at': _now(), 'issue': number,
                             'issue_repo': repo, 'updated': False,
                             'registry_status': status}
        _atomic_json(journal, state)
        return
    if status in ('MERGED', 'VERIFIED', 'CANCELED'):
        raise BatchError(f'the carrier registry is already {status}; refusing to '
                         're-dispatch this batch', status='BLOCKED')
    dispatched = _registry_value(inner, 'dispatched_at') or now
    block = _registry_text(_registry_dispatch_fields(plan, dispatched, now))
    updated = _replace_registry(body, block)
    result = _gh(['issue', 'edit', number, '--repo', repo, '--body-file', '-'],
                 plan['batch']['repo'], input_text=updated)
    if result.returncode != 0:
        raise BatchError('the ORCH-TASK-REGISTRY dispatch block could not be '
                         f'written: {(result.stderr or "")[:300]}', status='BLOCKED')
    state['dispatch'] = {'edition': 'github', 'at': now, 'issue': number,
                         'issue_repo': repo, 'updated': True,
                         'registry_status': 'DISPATCHED'}
    _atomic_json(journal, state)


def _dispatch_batch(plan, state, journal):
    if plan['edition'] == 'github':
        _dispatch_github(plan, state, journal)
    else:
        _dispatch_local(plan, state, journal)


# ------------------------------------------------------------- execution stage

def _prompt(spec, plan, base):
    lines = [
        f'TASK_ID={spec["id"]}',
        'ROLE=executor',
        f'BATCH={plan["batch"]["id"]}',
        f'TITLE={spec["title"]}',
        f'BASE_SHA={base}',
        f'PATHS_JSON={json.dumps(spec["paths"])}',
        f'ACCEPTANCE_JSON={json.dumps(spec["acceptance"])}',
        f'VERIFY_JSON={json.dumps(spec["verify"])}',
        '',
        spec['prompt'],
        '',
        'Allowed paths (relative to this worktree): ' + ', '.join(spec['paths']),
        'Acceptance criteria:',
    ]
    lines += [f'- AC-{index}: {text}'
              for index, text in enumerate(spec['acceptance'], start=1)]
    lines += ['Verification commands:']
    lines += ['- ' + ' '.join(command) for command in spec['verify']]
    lines += [
        '',
        'Rules: do not touch paths outside the allowed list, do not push, do not '
        'merge, do not rewrite history, and leave the worktree clean. Commit your '
        'own work if you prefer, but uncommitted changes are also acceptable.',
    ]
    return '\n'.join(lines)


def _scope_check(names, spec, plan, *, where='changed'):
    config = plan['config']
    for name in names:
        try:
            _safe_rel(name)
        except ValueError as exc:
            raise BatchError(f'{where} path is unsafe: {exc}', status='FAILED') from None
        if not _under(name, spec['paths']):
            raise BatchError(f'scope violation: {name} is outside the task paths',
                             status='FAILED')
        for forbidden in config['forbidden']:
            if _overlaps(name, forbidden):
                raise BatchError(f'forbidden path: {name} overlaps {forbidden}',
                                 status='FAILED')
        if _secret_path(name, config.get('secrets') or SECRET_PATTERNS):
            raise BatchError(f'privacy boundary: {name} is secret-shaped',
                             status='FAILED')
        if config['allowed'] is not None and not _under(name, config['allowed']):
            raise BatchError(f'project scope violation: {name} is not authorized '
                             'by docs/PROJECT.md', status='FAILED')


def _prepare_task_worktree(plan, spec, base, record):
    repo = plan['batch']['repo']
    path = Path(spec['worktree'])
    allow = record.get('head') if record.get('head') else None
    if path.exists():
        if _repo_root(path) != path.resolve():
            raise BatchError(f'task worktree is not a Git worktree root: {path}',
                             status='FAILED')
        if _branch(path) != spec['branch']:
            raise BatchError(f'task worktree is on {_branch(path)!r}, not '
                             f'{spec["branch"]!r}', status='FAILED')
        if not _clean(path):
            raise BatchError('the task worktree is not clean', status='FAILED')
        head = _head(path)
        if head != base and head != allow:
            raise BatchError('the task worktree is not at the expected base',
                             status='BLOCKED')
        return path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if _branch_exists(repo, spec['branch']):
        result = _git(repo, 'worktree', 'add', str(path), spec['branch'])
    else:
        result = _git(repo, 'worktree', 'add', '-b', spec['branch'], str(path), base)
    if result.returncode != 0:
        raise BatchError('cannot create the task worktree: '
                         f'{(result.stderr or "").strip()[:400]}', status='FAILED')
    head = _head(path)
    if head != base and head != allow:
        raise BatchError('the task worktree is not at the expected base',
                         status='BLOCKED')
    return path


def _commit_if_dirty(work, base, spec, plan):
    if _clean(work):
        return
    names = _changed_paths(work, base)
    _scope_check(names, spec, plan)
    _require_inert(work)
    add = _git(work, 'add', '-A')
    if add.returncode != 0:
        raise BatchError('cannot stage the task changes', status='FAILED')
    staged = _nul_names(work, 'diff', '--cached', '--no-ext-diff',
                        '--no-renames', '--name-only', '-z', base, '--')
    _scope_check(staged, spec, plan)
    message = f'{plan["batch"]["id"]}/{spec["id"]}: {spec["title"]}'
    commit = _git(work, 'commit', '--no-gpg-sign', '--no-verify', '-m', message)
    if commit.returncode != 0:
        raise BatchError('cannot commit the task changes: '
                         f'{(commit.stderr or "").strip()[:400]}', status='FAILED')


def _verify_task(work, commands, timeout):
    entries = []
    for command in commands:
        result = _run_process(command, work, timeout)
        record = {'argv_sha256': hashlib.sha256(
                      json.dumps(command).encode()).hexdigest(),
                  'exit': result['exit'], 'tail': result['tail'],
                  'timeout': bool(result['timeout'])}
        entries.append(record)
        if result['timeout']:
            raise BatchError(
                f'verification timed out: {" ".join(command)}; '
                f'{result["tail"]}', status='FAILED',
                halt=not result['stopped'], verify=entries)
        if result['exit'] != 0:
            raise BatchError(
                f'verification failed with exit {result["exit"]}: '
                f'{" ".join(command)}\n{result["tail"]}', status='FAILED',
                verify=entries)
    return entries


def _integrate(plan, spec):
    batch = Path(plan['batch']['worktree'])
    if not _clean(batch):
        raise BatchError('the batch worktree is not clean before integration')
    if _branch(batch) != plan['batch']['branch']:
        raise BatchError('the batch worktree is not on the batch branch')
    message = f'batch {plan["batch"]["id"]}: integrate {spec["id"]}'
    result = _git(batch, 'merge', '--no-ff', spec['branch'], '-m', message)
    if result.returncode != 0:
        unmerged = _nul_names(batch, 'diff', '--name-only', '-z',
                              '--diff-filter=U')
        conflicts = sorted(unmerged)
        _git(batch, 'merge', '--abort')
        raise BatchError('integration conflict for ' + spec['id'] + ': '
                         + ', '.join(conflicts), status='BLOCKED',
                         conflicts=conflicts)
    return _head(batch)


def _execute_task(plan, spec, record, base, timeout, save):
    work = _prepare_task_worktree(plan, spec, base, record)
    title = f'batch-{plan["batch"]["id"]}-{spec["id"]}'
    result = _agent(work, _prompt(spec, plan, base), plan['executor_agent'], timeout,
                    title=title)
    if result.get('timeout'):
        raise BatchError(f'execution timed out; {result["detail"]}', status='FAILED',
                         halt=not result.get('stopped'))
    if result.get('exit_code') != 0 or not result.get('session_id'):
        raise BatchError(f'execution session failed: {result.get("detail")}',
                         status='FAILED')
    record['execution_session'] = result['session_id']
    _commit_if_dirty(work, base, spec, plan)
    head = _head(work)
    if head == base:
        raise BatchError('execution produced no changes', status='FAILED')
    names = _diff_names(work, base, head)
    _scope_check(names, spec, plan)
    record.update(head=head, changed_paths=names)
    save()
    record['verify'] = _verify_task(work, spec['verify'], timeout)
    after = _head(work)
    if after != head:
        raise BatchError('a verification command changed the task head',
                         status='FAILED', verify=record['verify'])
    if not _clean(work):
        raise BatchError('a verification command left the worktree dirty',
                         status='FAILED', verify=record['verify'])
    record['integration_sha'] = _integrate(plan, spec)
    record['status'] = 'INTEGRATED'
    record['error'] = None
    save()


def _apply_retry(state, plan, ids):
    current_batch = _head(Path(plan['batch']['worktree']))
    for ident in ids:
        spec = next((task for task in plan['tasks'] if task['id'] == ident), None)
        if spec is None:
            raise InputError(f'--retry names unknown task {ident!r}')
        record = state['tasks'].get(ident)
        if record is None:
            raise InputError(f'--retry names a task with no journal record: {ident}')
        if record.get('status') != 'FAILED':
            raise InputError(f'--retry only accepts FAILED tasks; {ident} is '
                             f'{record.get("status")}')
        if record.get('integration_sha'):
            raise InputError(f'{ident} is already integrated and cannot be retried')
        branch = spec['branch']
        if _branch_exists(plan['batch']['repo'], branch):
            head = _git_text(plan['batch']['repo'], 'rev-parse',
                             f'refs/heads/{branch}')
            if record.get('head'):
                allowed = {record['head']}
            else:
                # A failure before any commit is retryable when the branch is
                # still exactly at the attempt base or the current batch head.
                allowed = {record.get('base'), current_batch} - {None}
            if head not in allowed:
                raise InputError(f'{ident} branch has commits not represented in '
                                 'the journal')
        record.update(status='PENDING', error=None)
    _reset_dependents(state, plan, ids)


def _reset_dependents(state, plan, ids):
    """Re-open transitive SKIPPED dependents of a retried task."""
    pending = list(ids)
    seen = set(ids)
    while pending:
        ident = pending.pop()
        for spec in plan['tasks']:
            if ident not in spec['depends'] or spec['id'] in seen:
                continue
            seen.add(spec['id'])
            record = state['tasks'].get(spec['id'])
            if record is not None and record.get('status') == 'SKIPPED':
                record.update(status='PENDING', error=None)
                pending.append(spec['id'])


def _normalize_interrupted(state, resume):
    for ident in sorted(state.get('tasks') or {}):
        record = state['tasks'][ident]
        if record.get('status') != 'RUNNING':
            continue
        if record.get('head'):
            record.update(status='BLOCKED', error=(
                'an interrupted attempt left a commit; inspect the worktree '
                'manually and use --retry after reconciling the branch'))
        elif resume:
            record.update(status='PENDING', error=None)
        else:
            record.update(status='BLOCKED', error=(
                'the attempt was interrupted before a commit; rerun with --resume'))


def _run_summary(state, plan):
    print(f'batch {state["batch_id"]}: phase {state["phase"]}')
    print(f'{"task":6} {"status":11} {"head":12} {"integration":12} error')
    for spec in plan['tasks']:
        record = state['tasks'].get(spec['id'], {})
        print(f'{spec["id"]:6} {record.get("status", "-"):11} '
              f'{(record.get("head") or "-")[:12]:12} '
              f'{(record.get("integration_sha") or "-")[:12]:12} '
              f'{record.get("error") or ""}')


def command_run(args):
    raw = load_plan(args.plan)
    plan = validate_plan(raw, args.repo)
    journal = _resolve_journal(args.journal, plan)
    if not args.execute:
        print(f'batch {plan["batch"]["id"]}: {len(plan["tasks"])} task(s), '
              f'edition {plan["edition"]}, project_paths {plan["project_paths"]}')
        for spec in plan['tasks']:
            deps = ', '.join(spec['depends']) or '-'
            print(f'would run {spec["id"]}: {spec["title"]} (depends: {deps})')
        print('dry run: pass --execute to create worktrees and start agents')
        return 0
    only = None
    if args.only:
        only = {value.strip() for value in args.only.split(',') if value.strip()}
        unknown = only - {spec['id'] for spec in plan['tasks']}
        if unknown:
            raise InputError(f'--only names unknown task(s): {sorted(unknown)}')
    with _Lock(journal):
        state = _load_journal(journal) if journal.exists() else None
        if state is None:
            base_sha = _resolve_commit(plan['batch']['repo'], plan['batch']['base'])
            state = _new_journal(plan, base_sha)
            for spec in plan['tasks']:
                state['tasks'][spec['id']] = _task_record(spec, plan['executor_agent'])
            _atomic_json(journal, state)
        else:
            _check_journal_plan(state, plan)
            if state.get('edition') != plan['edition']:
                raise InputError('the journal edition does not match the project')
            if (state.get('tasks')
                    and all(record.get('status') == 'VERIFIED'
                            for record in state['tasks'].values())):
                print(f'batch {state["batch_id"]}: all tasks are already VERIFIED')
                return 0
        base_sha = state['base_sha']
        try:
            if not _clean(plan['batch']['repo']):
                raise BatchError('the repository worktree is not clean')
            reasons = _inert_reasons(plan['batch']['repo'])
            if reasons:
                raise BatchError('refusing to run while Git can execute code during '
                                 'commit: ' + '; '.join(reasons))
            try:
                conflicts = _open_pr_conflicts(plan, plan['batch']['branch'])
            except BatchError as exc:
                for spec in plan['tasks']:
                    state['tasks'][spec['id']].update(status='BLOCKED',
                                                      error=str(exc))
                raise
            if conflicts:
                detail = ('active lock conflict blocks the whole batch: '
                          + '; '.join(f'{ident}: {reason}'
                                      for ident, reason in sorted(conflicts.items())))
                for spec in plan['tasks']:
                    state['tasks'][spec['id']].update(status='BLOCKED', error=detail)
                raise BatchError(detail)
            _dispatch_batch(plan, state, journal)
            _prepare_batch_worktree(plan, base_sha, args.execute)
        except BatchError as exc:
            state['phase'] = 'blocked'
            state['error'] = str(exc)
            _atomic_json(journal, state)
            print(f'error: {exc}', file=sys.stderr)
            return 1
        if args.retry:
            _apply_retry(state, plan, [value.strip() for value in args.retry.split(',')
                                       if value.strip()])
        _normalize_interrupted(state, args.resume)
        state['phase'] = 'tasks'
        state['error'] = None
        _atomic_json(journal, state)

        def save():
            _atomic_json(journal, state)

        halt = False
        for spec in plan['tasks']:
            record = state['tasks'][spec['id']]
            if only is not None and spec['id'] not in only:
                continue
            if record.get('status') in INTEGRATED or record.get('status') in TERMINAL_BAD:
                continue
            if record.get('status') == 'RUNNING':
                continue
            if any(state['tasks'][dep]['status'] not in INTEGRATED
                   for dep in spec['depends']):
                record.update(status='SKIPPED', error='dependency failed')
                save()
                continue
            if spec['id'] in conflicts:
                record.update(status='BLOCKED', error=conflicts[spec['id']])
                save()
                continue
            if halt:
                record.update(status='BLOCKED',
                              error='the run stopped after an unproven process kill')
                save()
                continue
            base = _head(Path(plan['batch']['worktree']))
            record.update(status='RUNNING', base=base, error=None,
                          attempts=int(record.get('attempts') or 0) + 1,
                          pid=os.getpid())
            save()
            try:
                _execute_task(plan, spec, record, base, args.timeout, save)
            except BatchError as exc:
                record.update(status=exc.record_status, error=str(exc),
                              verify=exc.verify or record.get('verify') or [])
                save()
                if exc.halt:
                    halt = True
            save()
        if halt:
            state['phase'] = 'blocked'
            for spec in plan['tasks']:
                record = state['tasks'][spec['id']]
                if record.get('status') == 'PENDING':
                    record.update(status='BLOCKED',
                                  error='the run stopped after an unproven process kill')
        else:
            state['phase'] = 'tasks'
        state['error'] = None if not halt else state.get('error')
        _atomic_json(journal, state)
        _run_summary(state, plan)
        pending = [record for record in state['tasks'].values()
                   if record.get('status') not in INTEGRATED]
        return 0 if not pending else 1


# -------------------------------------------------------------- batch review

def _diff_for_review(work, base, head):
    """Return the full textual diff and its SHA-256, refusing binary/oversize."""
    result = _git(work, 'diff', '--no-ext-diff', '--no-color', '--no-renames',
                  f'{base}...{head}', binary=True)
    if result.returncode != 0:
        raise BatchError(f'cannot compute the batch diff {base}..{head}')
    raw = result.stdout or b''
    if len(raw) > MAX_DIFF_BYTES:
        raise BatchError('the batch diff is larger than 2 MiB; a reviewer must '
                         'see it whole, so this batch cannot be reviewed')
    if b'Binary files' in raw or b'\x00' in raw:
        raise BatchError('the batch diff contains binary content; refusing an '
                         'incomplete review')
    return raw.decode('utf-8', 'replace'), hashlib.sha256(raw).hexdigest()


def _review_prompt(plan, state, base, head, digest, risk, diff):
    tasks = []
    for spec in plan['tasks']:
        record = state['tasks'].get(spec['id'], {})
        tasks.append({'id': spec['id'], 'acceptance': spec['acceptance'],
                      'acceptance_checks': spec.get('acceptance_checks'),
                      'acceptance_decisions': spec.get('acceptance_decisions'),
                      'paths': spec['paths'],
                      'verify': record.get('verify') or []})
    lines = [
        'ROLE=reviewer',
        f'BATCH={plan["batch"]["id"]}',
        f'BASE_SHA={base}',
        f'HEAD_SHA={head}',
        f'DIFF_SHA256={digest}',
        f'RISK={risk["level"]}',
        f'RISK_REASONS_JSON={json.dumps(risk["reasons"])}',
        f'TASKS_JSON={json.dumps(tasks)}',
        '',
        'Each task entry carries its acceptance_checks (argv lists) and the '
        'already-recorded verify argv hashes, exits and tails. Judge whether the '
        'diff and evidence actually satisfy every criterion; do not infer a PASS '
        'from a command name alone.',
        'Review the complete diff below. Answer with exactly one fenced ```json '
        'block using this shape:',
        '{"verdict":"APPROVE|REJECT","base":"<sha>","head":"<sha>",'
        '"diff_sha256":"<sha>","risk":"L0|L1|L2|L3","tasks":[{"id":"T1",'
        '"acceptance":[{"id":"AC-1","criterion":"<exact>",'
        '"status":"PASS|FAIL|NOT_RUN","evidence":"..."}]}],"findings":[]}',
        'Every acceptance criterion must be PASS with evidence, or the verdict '
        'must be REJECT with findings. Do not edit files.',
        'DIFF_BEGIN',
        diff,
        'DIFF_END',
    ]
    return '\n'.join(lines)


def _parse_verdict(text):
    blocks = re.findall(r'```json\s*\n(.*?)```', text or '', re.DOTALL)
    for block in reversed(blocks):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and VERDICT_KEYS.issubset(data):
            return data
    return None


def _verdict_problem(verdict, plan, base, head, digest, risk):
    if verdict.get('verdict') != 'APPROVE':
        return f'review verdict is {verdict.get("verdict")!r}, not APPROVE'
    if verdict.get('base') != base or verdict.get('head') != head:
        return 'the review verdict binds a different base or head'
    if verdict.get('diff_sha256') != digest:
        return 'the review verdict binds a different diff'
    if verdict.get('risk') != risk['level']:
        return 'the review verdict binds a different risk level'
    entries = verdict.get('tasks')
    expected = {spec['id']: spec for spec in plan['tasks']}
    if not isinstance(entries, list) or len(entries) != len(expected):
        return 'the review verdict does not cover every task'
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return 'a task verdict is malformed'
        ident = entry.get('id')
        if ident not in expected or ident in seen:
            return f'the review verdict names an unknown or repeated task {ident!r}'
        seen.add(ident)
        spec = expected[ident]
        acceptance = entry.get('acceptance')
        if (not isinstance(acceptance, list)
                or len(acceptance) != len(spec['acceptance'])):
            return f'{ident} does not cover every acceptance criterion'
        for index, item in enumerate(acceptance, start=1):
            if not isinstance(item, dict):
                return f'{ident} has a malformed acceptance entry'
            if item.get('id') != f'AC-{index}':
                return f'{ident} has an unexpected acceptance id {item.get("id")!r}'
            if item.get('criterion') != spec['acceptance'][index - 1]:
                return f'{ident} AC-{index} does not quote the criterion it was given'
            if item.get('status') != 'PASS':
                return f'{ident} AC-{index} is {item.get("status")!r}, not PASS'
            if not str(item.get('evidence') or '').strip():
                return f'{ident} AC-{index} has no evidence'
    if verdict.get('findings'):
        return 'the review approved while listing findings'
    return None


def _acceptance_evidence(plan, state, work, head, digest, timeout):
    """Run per-AC checks on the reviewed head; all must pass."""
    evidence = {}
    for spec in plan['tasks']:
        checks = spec.get('acceptance_checks')
        if not checks:
            raise BatchError(f'task {spec["id"]} has no acceptance_checks',
                             status='BLOCKED')
        entries = []
        for index, command in enumerate(checks, start=1):
            result = _run_process(command, work, timeout)
            entry = {'id': f'AC-{index}',
                     'argv_sha256': hashlib.sha256(
                         json.dumps(command).encode()).hexdigest(),
                     'exit': result['exit'], 'tail': result['tail'],
                     'timeout': bool(result['timeout']),
                     'status': 'PASS' if result['exit'] == 0
                               and not result['timeout'] else 'FAIL'}
            entries.append(entry)
            if _head(work) != head:
                raise BatchError(f'acceptance check for {spec["id"]} AC-{index} '
                                 'moved HEAD', status='REVIEW_REJECTED')
            if not _clean(work):
                raise BatchError(f'acceptance check for {spec["id"]} AC-{index} '
                                 'left the worktree dirty', status='REVIEW_REJECTED')
            if entry['status'] != 'PASS':
                evidence[spec['id']] = entries
                state['review'] = {
                    'head': head, 'diff_sha256': digest, 'risk': 'L0',
                    'verdict': 'REJECT', 'session': None, 'reviewer_agent': None,
                    'reason': f'L0 acceptance check {spec["id"]} AC-{index} failed',
                    'acceptance_evidence': evidence}
                raise BatchError(
                    f'L0 acceptance check {spec["id"]} AC-{index} failed; the '
                    'batch cannot be merged without proven per-AC evidence',
                    status='REVIEW_REJECTED')
        evidence[spec['id']] = entries
    return evidence


def _load_attestation(path, plan, head, digest):
    """Validate a post-execution Master attestation bound to the reviewed diff."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise BatchError(f'cannot read the acceptance attestation: {exc}',
                         status='BLOCKED') from None
    try:
        data = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BatchError(f'the acceptance attestation is not JSON: {exc}',
                         status='BLOCKED') from None
    if not isinstance(data, dict) or data.get('schema') != 'batch-acceptance/1':
        raise BatchError('the acceptance attestation schema must be '
                         'batch-acceptance/1', status='BLOCKED')
    if data.get('head') != head:
        raise BatchError('the acceptance attestation binds a different head',
                         status='REVIEW_REJECTED')
    if data.get('diff_sha256') != digest:
        raise BatchError('the acceptance attestation binds a different diff',
                         status='REVIEW_REJECTED')
    tasks = data.get('tasks')
    expected = {spec['id'] for spec in plan['tasks']}
    if not isinstance(tasks, dict) or set(tasks) != expected:
        raise BatchError('the acceptance attestation does not cover exactly the '
                         'batch task set', status='REVIEW_REJECTED')
    decisions = {}
    for spec in plan['tasks']:
        entries = tasks.get(spec['id'])
        if not isinstance(entries, list) or len(entries) != len(spec['acceptance']):
            raise BatchError(f'the acceptance attestation for {spec["id"]} does not '
                             'cover every criterion', status='REVIEW_REJECTED')
        for index, item in enumerate(entries, start=1):
            if not isinstance(item, dict) or item.get('id') != f'AC-{index}':
                raise BatchError(f'the acceptance attestation for {spec["id"]} '
                                 f'AC-{index} is malformed', status='REVIEW_REJECTED')
            if item.get('status') != 'PASS':
                raise BatchError(f'the acceptance attestation for {spec["id"]} '
                                 f'AC-{index} is not PASS', status='REVIEW_REJECTED')
            if not str(item.get('evidence') or '').strip():
                raise BatchError(f'the acceptance attestation for {spec["id"]} '
                                 f'AC-{index} has no evidence',
                                 status='REVIEW_REJECTED')
        decisions[spec['id']] = entries
    return decisions, hashlib.sha256(raw).hexdigest()


def _review(plan, state, journal, args, base, head, digest, risk):
    _prove_task_integration(plan, state, base, head)
    existing = state.get('review') or {}
    if (existing.get('head') == head
            and existing.get('diff_sha256') == digest
            and existing.get('verdict') in ('APPROVE', 'NOT_REQUIRED_L0')):
        return existing
    if existing.get('head') and existing['head'] != head:
        raise BatchError('the head changed after review; the existing approval is '
                         'invalid. Reconcile the branch and review again.',
                         status='BLOCKED')
    if risk['level'] == 'L0':
        checks_complete = all(spec.get('acceptance_checks')
                              for spec in plan['tasks'])
        attestation = getattr(args, 'acceptance_attestation', None)
        if checks_complete and attestation:
            work = Path(plan['batch']['worktree'])
            evidence = _acceptance_evidence(plan, state, work, head, digest,
                                            args.timeout)
            decisions, attestation_digest = _load_attestation(
                attestation, plan, head, digest)
            plan_decisions = {spec['id']: spec.get('acceptance_decisions')
                              for spec in plan['tasks']
                              if spec.get('acceptance_decisions')}
            record = {'head': head, 'diff_sha256': digest, 'risk': risk['level'],
                      'verdict': 'NOT_REQUIRED_L0', 'session': None,
                      'reviewer_agent': None,
                      'reason': 'docs-only diff with per-AC checks and a '
                                'post-execution Master attestation',
                      'acceptance_evidence': evidence,
                      'acceptance_decisions': decisions,
                      'acceptance_attestation_sha256': attestation_digest,
                      'at': _now()}
            if plan_decisions:
                record['plan_acceptance_decisions'] = plan_decisions
            state['review'] = record
            return record
        if not args.review:
            raise BatchError(
                'L0 needs complete acceptance_checks and a matching '
                '--acceptance-attestation for every task/AC, or an independent '
                'review; neither gate is present, so the batch cannot be '
                'auto-merged', status='BLOCKED')
        # Fall through to the independent reviewer below.
    if risk['level'] == 'L3':
        state['review'] = {'head': head, 'diff_sha256': digest,
                           'risk': risk['level'], 'verdict': 'NEEDS_HUMAN_DECISION',
                           'session': None, 'reviewer_agent': None,
                           'reason': 'a secret-shaped or human-only path changed'}
        state['decision'] = 'NEEDS_HUMAN_DECISION'
        raise BatchError('risk L3 requires human authorization; no review, push '
                         'or merge was attempted', status='BLOCKED')
    if not args.review:
        raise BatchError('risk L1/L2 requires an independent review; rerun '
                         'finalize with --review', status='BLOCKED')
    diff, _ = _diff_for_review(plan['batch']['repo'], base, head)
    work = Path(plan['batch']['worktree'])
    before_head, before_clean = _head(work), _clean(work)
    profiles_before = _agent_profiles(work)
    profile = profiles_before.get(plan['reviewer_agent'])
    if profile is None:
        raise BatchError('the reviewer profile is not queryable', status='BLOCKED')
    ready, why = _profile_gate(profile)
    if not ready:
        raise BatchError(f'the reviewer profile may edit: {why}', status='BLOCKED')
    result = _agent(work, _review_prompt(plan, state, base, head, digest, risk, diff),
                    plan['reviewer_agent'], args.timeout,
                    title=f'batch-{plan["batch"]["id"]}-review')
    if result.get('timeout'):
        raise BatchError(f'reviewer timed out; {result["detail"]}', status='BLOCKED',
                         halt=not result.get('stopped'))
    if result.get('exit_code') != 0 or not result.get('session_id'):
        raise BatchError(f'reviewer session failed: {result.get("detail")}',
                         status='BLOCKED')
    session = result['session_id']
    for record in state['tasks'].values():
        if record.get('execution_session') == session:
            raise BatchError('the reviewer session is an execution session',
                             status='BLOCKED')
    exported = _session_export(session, work)
    if not exported.get('ok'):
        raise BatchError(f'reviewer transcript unavailable: {exported.get("reason")}',
                         status='BLOCKED')
    if exported.get('session') != session:
        raise BatchError('the session export does not name the requested session',
                         status='REVIEW_REJECTED')
    if exported.get('outcome') != 'succeeded':
        raise BatchError('the reviewer session did not succeed: '
                         f'{exported.get("outcome")!r}', status='REVIEW_REJECTED')
    directory = exported.get('directory')
    if not directory or Path(directory).resolve() != work.resolve():
        raise BatchError('the reviewer session ran in a different directory',
                         status='REVIEW_REJECTED')
    if plan['reviewer_agent'] not in (exported.get('agents') or []):
        raise BatchError('the reviewer transcript names a different agent',
                         status='REVIEW_REJECTED')
    profiles_after = _agent_profiles(work)
    after = profiles_after.get(plan['reviewer_agent'])
    if after is None:
        raise BatchError('the reviewer profile disappeared during review',
                         status='BLOCKED')
    ready, why = _profile_gate(after)
    if not ready:
        raise BatchError(f'the reviewer profile changed and may edit: {why}',
                         status='BLOCKED')
    stream = _parse_verdict(result.get('text') or '')
    transcript = _parse_verdict('\n'.join(exported.get('texts') or []))
    if stream is None or transcript is None or stream != transcript:
        raise BatchError('the reviewer stream and transcript do not contain the '
                         'same fenced verdict', status='REVIEW_REJECTED')
    if _head(work) != before_head or _clean(work) != before_clean:
        raise BatchError('the reviewer changed the batch worktree', status='REVIEW_REJECTED')
    problem = _verdict_problem(stream, plan, base, head, digest, risk)
    if problem:
        state['review'] = {'head': head, 'diff_sha256': digest,
                           'risk': risk['level'], 'verdict': stream.get('verdict'),
                           'session': session,
                           'reviewer_agent': plan['reviewer_agent'],
                           'reason': problem}
        raise BatchError(f'review rejected: {problem}', status='REVIEW_REJECTED')
    _prove_task_integration(plan, state, base, head)
    record = {'head': head, 'diff_sha256': digest, 'risk': risk['level'],
              'verdict': 'APPROVE', 'session': session,
              'reviewer_agent': plan['reviewer_agent'],
              'reason': 'approve', 'at': _now()}
    state['review'] = record
    return record


def _prove_task_integration(plan, state, base, head):
    """Every INTEGRATED task must still be present in the reviewed head."""
    repo = plan['batch']['repo']
    batch_paths = set(_changed_paths_range(repo, base, head))
    for spec in plan['tasks']:
        record = state['tasks'].get(spec['id'], {})
        if record.get('status') not in INTEGRATED:
            raise BatchError(f'{spec["id"]} is not integrated before review',
                             status='BLOCKED')
        integration = record.get('integration_sha')
        if not integration or not _ancestor(repo, integration, head):
            raise BatchError(f'{spec["id"]} integration commit is not an ancestor '
                             'of the reviewed head; the batch branch was rewritten',
                             status='REVIEW_REJECTED')
        changed = set(record.get('changed_paths') or [])
        if not changed:
            raise BatchError(f'{spec["id"]} has no recorded changed paths',
                             status='REVIEW_REJECTED')
        if not changed.issubset(batch_paths):
            missing = sorted(changed - batch_paths)
            raise BatchError(f'{spec["id"]} is marked INTEGRATED but is missing from '
                             f'the batch diff: {", ".join(missing)}',
                             status='REVIEW_REJECTED')


# --------------------------------------------------------------------- carriers

def _batch_report(state, plan, reason):
    return {'schema': 'batch-delivery-report/1',
            'batch_id': plan['batch']['id'], 'reason': reason,
            'tasks': state['tasks'], 'merge': state['merge'],
            'review': state.get('review'), 'risk': state.get('risk'),
            'carrier': {'state': 'not_applicable', 'reason': reason}}


def _run_orchestrator(repo, arguments):
    script = Path(repo) / 'scripts' / 'orchestrator.py'
    argv = [sys.executable, str(script), *arguments]
    result = subprocess.run(argv, cwd=str(repo), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=300)
    return result


def _current_task_status(repo, ident):
    result = _run_orchestrator(repo, ['task', 'show', ident])
    if result.returncode != 0:
        return None
    match = re.search(r'^Status:\s*([A-Za-z_]+)\s*$', result.stdout, re.M)
    return match.group(1) if match else None


def _carrier_local(state, plan, journal, merge_commit):
    repo = plan['batch']['repo']
    script = Path(repo) / 'scripts' / 'orchestrator.py'
    calls = []
    if not script.is_file():
        reason = 'no local task library (scripts/orchestrator.py is absent)'
        report = _batch_report(state, plan, reason)
        report['carrier'] = {'state': 'blocked', 'reason': reason}
        _atomic_json(journal.parent / 'batch-report.json', report)
        state['carrier'] = {'state': 'blocked', 'reason': reason}
        raise BatchError(
            'the local carrier is absent after merge, so delivery cannot be '
            'recorded: restore scripts/orchestrator.py and the task records, then '
            'rerun finalize. The merge and post-merge evidence stay in the journal; '
            'tasks are not VERIFIED', status='BLOCKED')
    for spec in plan['tasks']:
        current = _current_task_status(repo, spec['id'])
        if current is None:
            reason = (f'task {spec["id"]} is not in the local task library; a '
                      'batch report was written instead of claiming a SQLite update')
            report = _batch_report(state, plan, reason)
            report['carrier'] = {'state': 'blocked', 'reason': reason}
            _atomic_json(journal.parent / 'batch-report.json', report)
            state['carrier'] = {'state': 'blocked', 'reason': reason}
            raise BatchError(
                f'task {spec["id"]} is missing from the local library, so the '
                'carrier update cannot be proven; tasks are not VERIFIED. Recreate '
                'the task record and rerun finalize', status='BLOCKED')
        if current not in CHAIN:
            raise BatchError(f'task {spec["id"]} has unrecognized status {current!r}')
        start = CHAIN.index(current)
        for target in CHAIN[start + 1:]:
            if target == 'READY':
                arguments = ['task', 'ready', spec['id']]
            elif target == 'DISPATCHED':
                arguments = ['task', 'dispatch', spec['id']]
            else:
                arguments = ['task', 'status', spec['id'], target,
                             '--note', f'batch {plan["batch"]["id"]}']
                if target == 'IN_PROGRESS' and state['tasks'][spec['id']].get('head'):
                    arguments += ['--head-sha', state['tasks'][spec['id']]['head']]
                if target == 'MERGED':
                    arguments += ['--merge-sha', merge_commit]
            result = _run_orchestrator(repo, arguments)
            calls.append({'argv': arguments, 'exit': result.returncode})
            if result.returncode != 0:
                raise BatchError(
                    f'local carrier update failed for {spec["id"]} at {target}: '
                    f'{(result.stderr or "")[:300]}')
    state['carrier'] = {'state': 'updated', 'calls': calls}


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _registry_value(inner, name):
    match = re.search(r'^[ \t]*' + re.escape(name) + r'[ \t]*:[ \t]*(.*?)[ \t]*$',
                      inner or '', re.M)
    return match.group(1) if match else None


def _registry_text(fields):
    lines = ['<!-- ORCH-TASK-REGISTRY']
    lines += [f'{name}: {value}' for name, value in fields]
    lines.append('-->')
    return '\n'.join(lines)


def _registry_verified_fields(state, plan, merge_commit, dispatched_at,
                              updated_at):
    locked = sorted({name for spec in plan['tasks'] for name in spec['paths']})
    return [
        ('status', 'VERIFIED'), ('mode', 'batch'),
        ('locked_paths', ', '.join(locked)),
        ('branch', plan['batch']['branch']),
        ('pr', state['pr']['number']),
        ('runner', plan['executor_agent']),
        ('dispatched_at', dispatched_at), ('updated_at', updated_at),
        ('head_sha', merge_commit), ('blocked_reason', '(none)'),
    ]


def _registry_dispatch_fields(plan, dispatched_at, updated_at):
    locked = sorted({name for spec in plan['tasks'] for name in spec['paths']})
    return [
        ('status', 'DISPATCHED'), ('mode', 'batch'),
        ('locked_paths', ', '.join(locked)),
        ('branch', plan['batch']['branch']), ('pr', '(none)'),
        ('runner', plan['executor_agent']),
        ('dispatched_at', dispatched_at), ('updated_at', updated_at),
        ('head_sha', '(none)'), ('blocked_reason', '(none)'),
    ]


_HTML_REGISTRY = re.compile(r'<!--\s*ORCH-TASK-REGISTRY\b(?P<inner>.*?)-->', re.S)
_FENCED_REGISTRY = re.compile(
    r'```[A-Za-z0-9_-]*\n(?P<inner>[^`]*ORCH-TASK-REGISTRY[^`]*)\n```')


def _replace_registry(body, block):
    """Replace every ORCH-TASK-REGISTRY block with exactly one ``block``."""
    body = body or ''
    html_matches = list(_HTML_REGISTRY.finditer(body))
    fenced_matches = list(_FENCED_REGISTRY.finditer(body))
    existing = html_matches or fenced_matches
    if not existing:
        body = body.rstrip()
        return (body + '\n\n' if body else '') + block + '\n'
    anchor = existing[0].start()
    removals = sorted(html_matches + fenced_matches, key=lambda item: item.start())
    before = sum(len(body[item.start():item.end()]) for item in removals
                 if item.end() <= anchor)
    for item in reversed(removals):
        body = body[:item.start()] + body[item.end():]
    index = anchor - before
    return (body[:index] + block + body[index:]).strip('\n') + '\n'


def _registry_blocks(body):
    return ([match.group('inner') for match in _HTML_REGISTRY.finditer(body or '')]
            + [match.group('inner') for match in _FENCED_REGISTRY.finditer(body or '')])


def _registry_inner(body):
    blocks = _registry_blocks(body)
    return blocks[0] if blocks else ''


def _update_registry(body, state, plan, merge_commit):
    """Replace the single ORCH-TASK-REGISTRY block in place.

    The authoritative carrier form is an HTML comment. A legacy fenced block is
    upgraded rather than duplicated, and any duplicate ORCH-TASK-REGISTRY block
    is removed so the Issue keeps exactly one.
    """
    now = _now()
    dispatched = _registry_value(_registry_inner(body), 'dispatched_at') or now
    block = _registry_text(_registry_verified_fields(
        state, plan, merge_commit, dispatched, now))
    return _replace_registry(body, block)


def _comment(state, plan, head_or_merge, merged=False):
    if merged:
        conclusion = ('Batch verified: merged into the base branch and post-merge '
                      'verification passed.')
        delivery = f'Merge commit: `{head_or_merge}`.'
    else:
        conclusion = ('Batch review complete; required checks are pending. No merge '
                      'has been performed and post-merge verification has not run.')
        delivery = (f'Reviewed head: `{head_or_merge}` (not a merge commit). '
                    'The merge commit is recorded only after merge and post-merge '
                    'verification.')
    lines = [
        f'## Batch {plan["batch"]["id"]}: {plan["batch"]["title"]}',
        '',
        '### Conclusion',
        conclusion,
        '',
        '### User-visible change',
        plan['batch']['summary'] or plan['batch']['title'],
        '',
        '### Acceptance results',
        '| Task | Acceptance | Verification | Head |',
        '|---|---|---|---|',
    ]
    for spec in plan['tasks']:
        record = state['tasks'][spec['id']]
        criteria = '<br>'.join(f'AC-{index}: {text}'
                               for index, text in enumerate(spec['acceptance'], 1))
        checks = '<br>'.join(f'{entry["argv_sha256"][:12]}: exit {entry["exit"]}'
                             for entry in record.get('verify') or [])
        lines.append(f'| {spec["id"]} | {criteria} | {checks} | '
                     f'`{(record.get("head") or "")[:12]}` |')
    lines += [
        '',
        '### Risks and next step',
        f'Risk level: {state.get("risk", {}).get("level", "unknown")}. '
        f'Review: {state.get("review", {}).get("verdict", "unknown")}. '
        'No per-task PRs; one batch PR.',
        delivery,
    ]
    return '\n'.join(lines) + '\n'


def _carrier_github(state, plan, merge_commit):
    batch = plan['batch']
    if not batch.get('issue') or not batch.get('issue_repo'):
        state['carrier'] = {'state': 'missing', 'reason': 'no issue configured'}
        raise BatchError(
            'github delivery cannot be recorded without batch.issue and '
            'batch.issue_repo; tasks are not VERIFIED. Add the carrier and rerun '
            'finalize; the merge evidence is already in the journal',
            status='BLOCKED')
    repo = batch.get('issue_repo') or batch['repo']
    number = str(batch['issue'])
    comment = _comment(state, plan, merge_commit, merged=True)
    result = _gh(['issue', 'comment', number, '--repo', repo, '--body-file', '-'],
                 plan['batch']['repo'], input_text=comment)
    if result.returncode != 0:
        raise BatchError('the carrier comment could not be posted: '
                         f'{(result.stderr or "")[:300]}')
    viewed = _gh_json(['issue', 'view', number, '--repo', repo, '--json', 'body'],
                      plan['batch']['repo'])
    body = viewed.get('body') if isinstance(viewed, dict) else ''
    updated = _update_registry(body or '', state, plan, merge_commit)
    edited = _gh(['issue', 'edit', number, '--repo', repo, '--body-file', '-'],
                 plan['batch']['repo'], input_text=updated)
    if edited.returncode != 0:
        raise BatchError('the ORCH-TASK-REGISTRY block could not be updated: '
                         f'{(edited.stderr or "")[:300]}')
    state['carrier'] = {'state': 'updated', 'issue': batch['issue'],
                        'registry': 'VERIFIED'}


def _verify_guarded(work, commands, timeout, *, expected_head, label,
                    expected_branch=None, merge_commit=None):
    """Run commands while proving they cannot move or dirty the reviewed tree."""
    if _head(work) != expected_head:
        raise BatchError(f'{label} started on the wrong head: expected '
                         f'{expected_head}', status='BLOCKED')
    if expected_branch is not None and _branch(work) != expected_branch:
        raise BatchError(f'{label} started on the wrong branch', status='BLOCKED')
    if not _clean(work):
        raise BatchError(f'{label} started with a dirty worktree', status='BLOCKED')
    entries = []
    for command in commands:
        result = _run_process(command, work, timeout)
        entry = {'argv_sha256': hashlib.sha256(
                     json.dumps(command).encode()).hexdigest(),
                 'exit': result['exit'], 'tail': result['tail'],
                 'timeout': bool(result['timeout'])}
        entries.append(entry)
        if result['timeout'] or result['exit'] != 0:
            raise BatchError(
                f'{label} failed: {" ".join(command)} '
                f'(exit {result["exit"]}): {result["tail"]}', status='BLOCKED',
                verify=entries)
        after = _head(work)
        if after != expected_head:
            raise BatchError(
                f'{label} moved HEAD from {expected_head} to {after}; the reviewed '
                'content is no longer the content that would be delivered',
                status='BLOCKED', verify=entries)
        if expected_branch is not None and _branch(work) != expected_branch:
            raise BatchError(f'{label} left the base branch', status='BLOCKED',
                             verify=entries)
        if not _clean(work):
            raise BatchError(f'{label} left the worktree dirty', status='BLOCKED',
                             verify=entries)
        if merge_commit is not None and not _ancestor(work, merge_commit, after):
            raise BatchError(f'{label} removed the merge commit from the base '
                             'branch', status='BLOCKED', verify=entries)
    return entries


def _verify_list(work, commands, timeout):
    """Unproven command run for callers that are not a finalization gate."""
    entries = []
    for command in commands:
        result = _run_process(command, work, timeout)
        entry = {'argv_sha256': hashlib.sha256(
                     json.dumps(command).encode()).hexdigest(),
                 'exit': result['exit'], 'tail': result['tail'],
                 'timeout': bool(result['timeout'])}
        entries.append(entry)
        if result['timeout'] or result['exit'] != 0:
            raise BatchError(
                f'batch verification failed: {" ".join(command)} '
                f'(exit {result["exit"]}): {result["tail"]}', status='BLOCKED',
                verify=entries)
    return entries


def _prove_review_current(state, plan, head, digest):
    review = state.get('review') or {}
    if review.get('head') != head or review.get('diff_sha256') != digest:
        raise BatchError('the head or diff changed after review; the approval no '
                         'longer applies', status='BLOCKED')
    if review.get('verdict') not in ('APPROVE', 'NOT_REQUIRED_L0'):
        raise BatchError('there is no valid review for this head', status='BLOCKED')


# ------------------------------------------------------------------ finalize

def _require_tasks_integrated(state, plan):
    bad = []
    for spec in plan['tasks']:
        record = state['tasks'].get(spec['id'], {})
        if record.get('status') not in INTEGRATED:
            bad.append(f'{spec["id"]}={record.get("status")}')
    if bad:
        raise BatchError('not every task is integrated: ' + ', '.join(bad))


def _pr_from_item(plan, item, head):
    base = item.get('baseRefName')
    if base != plan['batch']['base']:
        raise BatchError(
            f'the pull request base is {base!r}, not {plan["batch"]["base"]!r}; '
            'refusing to reuse or merge it', status='BLOCKED')
    return {'number': item.get('number'), 'url': item.get('url'),
            'head': item.get('headRefOid') or head, 'base': base}


def _github_pr(plan, state, head):
    repo = plan['batch']['repo']
    branch = plan['batch']['branch']
    fields = 'number,url,headRefOid,state,baseRefName'
    payload = _gh_json(['pr', 'list', '--state', 'open', '--head', branch, '--json',
                        fields], repo)
    if not isinstance(payload, list):
        raise BatchError('gh pr list returned an unexpected payload')
    if payload:
        return _pr_from_item(plan, payload[0], head)
    body = _comment(state, plan, state['merge'].get('commit') or head)
    result = _gh(['pr', 'create', '--base', plan['batch']['base'], '--head', branch,
                  '--title', f'batch {plan["batch"]["id"]}: '
                             f'{plan["batch"]["title"]}', '--body-file', '-'],
                 repo, input_text=body)
    if result.returncode != 0:
        raise BatchError('gh pr create failed: '
                         f'{(result.stderr or "").strip()[:300]}')
    payload = _gh_json(['pr', 'list', '--state', 'open', '--head', branch, '--json',
                        fields], repo)
    if not isinstance(payload, list) or not payload:
        raise BatchError('the created pull request cannot be found')
    return _pr_from_item(plan, payload[0], head)


def _authoritative_checks(plan):
    """Required contexts and app identities from effective branch rules."""
    batch = plan['batch']
    if not batch.get('issue_repo'):
        raise BatchError('cannot read repository rules without batch.issue_repo',
                         status='BLOCKED')
    repo = plan['batch']['repo']
    slug = batch['issue_repo']
    payload = _gh_json(['api',
                        f'repos/{slug}/rules/branches/{batch["base"]}'], repo)
    if not isinstance(payload, list) or not payload:
        raise BatchError('the effective branch rules are empty or unreadable; '
                         'refusing to invent a required-check gate',
                         status='BLOCKED')
    required = {}
    for rule in payload:
        if not isinstance(rule, dict) or rule.get('type') != 'required_status_checks':
            continue
        params = rule.get('parameters') or {}
        for check in params.get('required_status_checks') or []:
            if not isinstance(check, dict):
                continue
            context = check.get('context')
            if not isinstance(context, str) or not context:
                continue
            app = check.get('integration_id')
            if context in required and required[context] != app:
                raise BatchError(
                    f'effective rules require context {context!r} with conflicting '
                    'app identities; refusing an ambiguous gate', status='BLOCKED')
            required[context] = app
    if not required:
        raise BatchError('the effective branch rules yield no required status '
                         'contexts; refusing to treat an empty set as pass',
                         status='BLOCKED')
    planned = set(plan['finalize']['required_checks'])
    missing = sorted(planned - set(required))
    if missing:
        raise BatchError('the plan lists required check(s) the effective rules do '
                         'not require: ' + ', '.join(missing), status='BLOCKED')
    return [{'context': context, 'integration_id': required[context]}
            for context in sorted(required)]


def _recheck_pr(plan, pr, head):
    repo = plan['batch']['repo']
    view = _gh_json(['pr', 'view', str(pr['number']), '--json',
                     'headRefOid,baseRefName,mergeable,state'], repo)
    if not isinstance(view, dict):
        raise BatchError('gh pr view returned an unexpected payload')
    if view.get('headRefOid') != head:
        raise BatchError('the pull request head moved after review')
    if view.get('baseRefName') != plan['batch']['base']:
        raise BatchError('the pull request base is not the batch base')
    if view.get('mergeable') != 'MERGEABLE':
        raise BatchError(f'the pull request mergeability is '
                         f'{view.get("mergeable")!r}, not MERGEABLE')
    if view.get('state') not in ('OPEN', 'MERGED'):
        raise BatchError(f'the pull request is {view.get("state")!r}')
    return view


def _check_run_order_key(run):
    timestamp = run.get('completed_at') or run.get('started_at') or ''
    ident = run.get('id')
    if not timestamp and not isinstance(ident, int):
        return None
    return (timestamp if isinstance(timestamp, str) else '',
            ident if isinstance(ident, int) else -1)


def _wait_checks(plan, state, pr, head, timeout, required):
    repo = plan['batch']['repo']
    slug = plan['batch']['issue_repo']
    deadline = time.monotonic() + timeout
    while True:
        payload = _gh_json(['api',
                            f'repos/{slug}/commits/{head}/check-runs?per_page=100'],
                           repo)
        if not isinstance(payload, dict):
            raise BatchError('the check-run response is not a JSON object',
                             status='BLOCKED')
        runs = payload.get('check_runs')
        total = payload.get('total_count')
        if not isinstance(runs, list) or not isinstance(total, int):
            raise BatchError('the check-run response is unreadable',
                             status='BLOCKED')
        if total > len(runs):
            raise BatchError(
                f'the check-run list is truncated ({len(runs)} of {total}); '
                'refusing to guess about missing required contexts',
                status='BLOCKED')
        grouped = {}
        for run in runs:
            if isinstance(run, dict) and isinstance(run.get('name'), str):
                grouped.setdefault(run['name'], []).append(run)
        missing = []
        pending = []
        for item in required:
            context = item['context']
            candidates = grouped.get(context) or []
            if not candidates:
                missing.append(context)
                continue
            expected_app = item.get('integration_id')
            completed_success = []
            context_pending = False
            for run in candidates:
                app = run.get('app') if isinstance(run.get('app'), dict) else {}
                app_id = app.get('id')
                if expected_app is not None and app_id != expected_app:
                    raise BatchError(
                        f'required check {context!r} has a run under app '
                        f'{app_id!r}, not the configured integration '
                        f'{expected_app!r}; the run identity is ambiguous',
                        status='BLOCKED')
                if run.get('status') != 'completed':
                    context_pending = True
                    continue
                if run.get('conclusion') != 'success':
                    raise BatchError(
                        f'required check {context!r} has a completed run with '
                        f'conclusion {run.get("conclusion")!r}, not success',
                        status='BLOCKED')
                completed_success.append(run)
            if context_pending:
                pending.append(context)
                continue
            if len(completed_success) > 1:
                keys = [_check_run_order_key(run) for run in completed_success]
                if any(key is None for key in keys):
                    raise BatchError(
                        f'required check {context!r} has multiple successful runs '
                        'that cannot be ordered unambiguously', status='BLOCKED')
                if len(set(keys)) != len(keys):
                    raise BatchError(
                        f'required check {context!r} has duplicate successful runs '
                        'with the same ordering identity; refusing to choose',
                        status='BLOCKED')
                _authoritative = max(zip(keys, completed_success),
                                     key=lambda pair: pair[0])
        if not missing and not pending:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if missing:
                raise BatchError('missing required check run(s): '
                                 + ', '.join(missing)
                                 + ' (NOT_RUN is never treated as pass)',
                                 status='BLOCKED')
            raise BatchError('required check run(s) did not complete in time: '
                             + ', '.join(pending), status='BLOCKED')
        time.sleep(min(10, remaining))
    _recheck_pr(plan, pr, head)


def _reconcile_github_merge(plan, state):
    """Adopt a merge the journal missed, so a crash cannot cause a second one."""
    repo = plan['batch']['repo']
    pr = state.get('pr')
    view = None
    if pr:
        try:
            view = _gh_json(['pr', 'view', str(pr['number']), '--json',
                             'state,mergeCommit,url,headRefOid'], repo)
        except BatchError:
            view = None
    else:
        try:
            payload = _gh_json(['pr', 'list', '--state', 'all', '--head',
                                plan['batch']['branch'], '--json',
                                'number,url,state,mergeCommit,headRefOid'], repo)
        except BatchError:
            payload = []
        if isinstance(payload, list):
            merged = [item for item in payload
                      if isinstance(item, dict) and item.get('state') == 'MERGED']
            if merged:
                view = merged[0]
                state['pr'] = {'number': view.get('number'),
                               'url': view.get('url'),
                               'head': view.get('headRefOid')}
    if not isinstance(view, dict) or view.get('state') != 'MERGED':
        return False
    merge = view.get('mergeCommit') or {}
    commit = merge.get('oid') if isinstance(merge, dict) else merge
    if not commit:
        raise BatchError('the pull request is MERGED but names no merge commit')
    state['merge'].update(state='merged', commit=commit)
    state['phase'] = 'post_merge'
    return True


def _merge_github(plan, state, pr, head, authorization, timeout):
    repo = plan['batch']['repo']
    if state['merge'].get('state') == 'merged':
        return state['merge']['commit']
    method = plan['finalize']['merge_method']
    args = ['pr', 'merge', str(pr['number']), f'--{method}', '--delete-branch',
            '--match-head-commit', head]
    result = _gh(args, repo)
    if result.returncode != 0:
        try:
            view = _gh_json(['pr', 'view', str(pr['number']), '--json',
                             'state,mergeCommit,url,headRefOid'], repo)
        except BatchError:
            view = None
        if isinstance(view, dict) and view.get('state') == 'MERGED':
            merge = view.get('mergeCommit') or {}
            commit = merge.get('oid') if isinstance(merge, dict) else merge
            state['merge'].update(state='merged', commit=commit,
                                  authorization=authorization)
            return commit
        raise BatchError('gh pr merge failed: '
                         f'{(result.stderr or "").strip()[:300]}')
    view = _gh_json(['pr', 'view', str(pr['number']), '--json',
                     'state,mergeCommit,url,headRefOid'], repo)
    merge = (view or {}).get('mergeCommit') or {}
    commit = merge.get('oid') if isinstance(merge, dict) else merge
    if not commit:
        raise BatchError('the merge succeeded but no merge commit was reported')
    state['merge'].update(state='merged', commit=commit,
                          authorization=authorization)
    return commit


def _block_tasks(state, plan):
    for spec in plan['tasks']:
        state['tasks'][spec['id']]['status'] = 'BLOCKED'


def _post_merge_github(plan, state, merge_commit, timeout):
    repo = plan['batch']['repo']
    base = plan['batch']['base']
    fetched = _git(repo, 'fetch', 'origin', base, timeout=300)
    if fetched.returncode != 0:
        state['merge']['state'] = 'merged_but_post_verify_failed'
        _block_tasks(state, plan)
        raise BatchError('git fetch origin failed: '
                         f'{(fetched.stderr or "").strip()[:300]}')
    origin = f'origin/{base}'
    if not _ancestor(repo, merge_commit, origin):
        state['merge']['state'] = 'merged_but_post_verify_failed'
        _block_tasks(state, plan)
        raise BatchError('the merge commit is not an ancestor of the base branch; '
                         'post-merge verification cannot be trusted')
    if _branch(repo) != base:
        state['merge']['state'] = 'merged_but_post_verify_failed'
        _block_tasks(state, plan)
        raise BatchError(f'the repository is not on {base!r}; refusing to update it')
    if not _clean(repo):
        state['merge']['state'] = 'merged_but_post_verify_failed'
        _block_tasks(state, plan)
        raise BatchError('the repository worktree is not clean; refusing to pull')
    pulled = _git(repo, 'pull', '--ff-only', timeout=300)
    if pulled.returncode != 0:
        state['merge']['state'] = 'merged_but_post_verify_failed'
        _block_tasks(state, plan)
        raise BatchError('git pull --ff-only failed: '
                         f'{(pulled.stderr or "").strip()[:300]}')
    try:
        head = _head(repo)
        if not _ancestor(repo, merge_commit, head):
            raise BatchError('the merge commit is not an ancestor of the base HEAD')
        state['merge']['post_verify'] = _verify_guarded(
            repo, plan['finalize']['post_merge_verify'], timeout,
            expected_head=head, label='post-merge verification',
            expected_branch=base, merge_commit=merge_commit)
        state['merge']['verified_sha'] = _head(repo)
    except BatchError:
        state['merge']['state'] = 'merged_but_post_verify_failed'
        _block_tasks(state, plan)
        raise


def _post_merge_local(plan, state, merge_commit, timeout):
    repo = plan['batch']['repo']
    if _branch(repo) != plan['batch']['base']:
        state['merge']['state'] = 'merged_but_post_verify_failed'
        _block_tasks(state, plan)
        raise BatchError('the repository is not on the base branch')
    if not _clean(repo):
        state['merge']['state'] = 'merged_but_post_verify_failed'
        _block_tasks(state, plan)
        raise BatchError('the repository worktree is not clean')
    try:
        head = _head(repo)
        state['merge']['post_verify'] = _verify_guarded(
            repo, plan['finalize']['post_merge_verify'], timeout,
            expected_head=head, label='post-merge verification',
            expected_branch=plan['batch']['base'], merge_commit=merge_commit)
        state['merge']['verified_sha'] = _head(repo)
    except BatchError:
        state['merge']['state'] = 'merged_but_post_verify_failed'
        _block_tasks(state, plan)
        raise


def _github_flow(plan, state, journal, args, base, head, digest, risk):
    repo = plan['batch']['repo']
    fetched = _git(repo, 'fetch', 'origin', plan['batch']['base'], timeout=300)
    if fetched.returncode != 0:
        raise BatchError('git fetch origin failed: '
                         f'{(fetched.stderr or "").strip()[:300]}')
    origin = f'origin/{plan["batch"]["base"]}'
    if not _ancestor(repo, origin, head):
        raise BatchError('the batch branch does not contain the base branch; a '
                         'rebase is required and is never automatic')
    _prove_review_current(state, plan, head, digest)
    if state['merge'].get('state') == 'merged':
        commit = state['merge']['commit']
        state['phase'] = 'post_merge'
        _atomic_json(journal, state)
        _post_merge_github(plan, state, commit, args.timeout)
        _carrier_github(state, plan, commit)
        if (state.get('carrier') or {}).get('state') != 'updated':
            raise BatchError('the github carrier did not record VERIFIED; tasks are '
                             'not VERIFIED', status='BLOCKED')
        for spec in plan['tasks']:
            state['tasks'][spec['id']]['status'] = 'VERIFIED'
        state['phase'] = 'verified'
        _atomic_json(journal, state)
        return
    do_push = args.push or args.merge
    if not do_push:
        state['phase'] = 'review'
        _atomic_json(journal, state)
        return
    pushed = _git(repo, 'push', '-u', 'origin',
                  f'{head}:refs/heads/{plan["batch"]["branch"]}', timeout=600)
    if pushed.returncode != 0:
        raise BatchError('git push failed: '
                         f'{(pushed.stderr or "").strip()[:300]}')
    local_head = _git_text(repo, 'rev-parse',
                           f'refs/heads/{plan["batch"]["branch"]}')
    if local_head != head:
        raise BatchError('the local batch branch moved after review; refusing to '
                         'open a pull request for unreviewed content',
                         status='BLOCKED')
    pr = _github_pr(plan, state, head)
    state['pr'] = pr
    state['phase'] = 'pr'
    _atomic_json(journal, state)
    required = _authoritative_checks(plan)
    _wait_checks(plan, state, pr, head, args.checks_timeout, required)
    if not args.merge:
        state['phase'] = 'pr'
        return
    _prove_review_current(state, plan, head, digest)
    fetched = _git(repo, 'fetch', 'origin', plan['batch']['base'], timeout=300)
    if fetched.returncode != 0:
        raise BatchError('git fetch origin failed immediately before merge: '
                         f'{(fetched.stderr or "").strip()[:300]}',
                         status='BLOCKED')
    origin_sha = _git_text(repo, 'rev-parse',
                           f'origin/{plan["batch"]["base"]}')
    if not _ancestor(repo, origin_sha, head):
        raise BatchError(
            'the base branch advanced during the check wait; the reviewed head no '
            'longer contains origin/' + plan['batch']['base']
            + '. Re-integrate and review on the new base', status='BLOCKED')
    state['merge']['base_sha_at_merge'] = origin_sha
    _recheck_pr(plan, pr, head)
    commit = _merge_github(plan, state, pr, head, args.authorization, args.timeout)
    state['phase'] = 'merge'
    _atomic_json(journal, state)
    _post_merge_github(plan, state, commit, args.timeout)
    state['phase'] = 'post_merge'
    _atomic_json(journal, state)
    _carrier_github(state, plan, commit)
    if (state.get('carrier') or {}).get('state') != 'updated':
        raise BatchError('the github carrier did not record VERIFIED; tasks are '
                         'not VERIFIED', status='BLOCKED')
    for spec in plan['tasks']:
        state['tasks'][spec['id']]['status'] = 'VERIFIED'
    state['phase'] = 'verified'
    _atomic_json(journal, state)


def _local_flow(plan, state, journal, args, base, head, digest, risk):
    repo = plan['batch']['repo']
    if _branch(repo) != plan['batch']['base']:
        raise BatchError(f'the repository is not on {plan["batch"]["base"]!r}')
    if not _clean(repo):
        raise BatchError('the repository worktree is not clean')
    if not _ancestor(repo, base, head):
        raise BatchError('the base is not an ancestor of the batch head')
    _prove_review_current(state, plan, head, digest)
    if not args.merge:
        state['phase'] = 'review'
        _atomic_json(journal, state)
        return
    expected = plan['finalize']['authorization']
    if not expected or args.authorization != expected:
        raise InputError('--authorization must exactly equal the nonempty '
                         'finalize.authorization before a local merge')
    if state['merge'].get('state') == 'merged':
        commit = state['merge']['commit']
    else:
        if _head(repo) != state['base_sha']:
            raise BatchError('base moved; integrate, verify and review on the new '
                             'base before merging', status='BLOCKED')
        result = _git(repo, 'merge', '--no-ff', head, '-m',
                      f'batch {plan["batch"]["id"]}: {plan["batch"]["title"]}')
        if result.returncode != 0:
            _git(repo, 'merge', '--abort')
            raise BatchError('the local merge failed: '
                             f'{(result.stderr or "").strip()[:300]}')
        commit = _head(repo)
        state['merge'].update(state='merged', commit=commit,
                              authorization=args.authorization)
    state['phase'] = 'merge'
    _atomic_json(journal, state)
    _post_merge_local(plan, state, commit, args.timeout)
    state['phase'] = 'post_merge'
    _atomic_json(journal, state)
    _carrier_local(state, plan, journal, commit)
    if (state.get('carrier') or {}).get('state') != 'updated':
        raise BatchError('the local carrier did not record VERIFIED; tasks are not '
                         'VERIFIED', status='BLOCKED')
    for spec in plan['tasks']:
        state['tasks'][spec['id']]['status'] = 'VERIFIED'
    state['phase'] = 'verified'
    _atomic_json(journal, state)


def _spawn_next(plan, args, journal):
    next_path = args.next_plan or plan['finalize'].get('next_plan')
    if not next_path:
        return None
    next_raw = load_plan(next_path)
    next_plan = validate_plan(next_raw, plan['batch']['repo'])
    if next_plan['finalize'].get('next_plan'):
        raise InputError('a next plan may not itself name a next plan')
    next_journal = _default_journal(next_plan)
    script = Path(__file__).resolve()
    run = subprocess.run([sys.executable, str(script), 'run', '--plan', next_path,
                          '--journal', str(next_journal), '--repo',
                          plan['batch']['repo'], '--execute'], cwd=str(Path.cwd()))
    if run.returncode != 0:
        return {'plan': next_path, 'run_exit': run.returncode}
    finalize_args = [sys.executable, str(script), 'finalize', '--plan', next_path,
                     '--journal', str(next_journal), '--repo', plan['batch']['repo'],
                     '--review']
    if next_plan['edition'] == 'github':
        finalize_args += ['--push', '--merge', '--authorization',
                          next_plan['finalize'].get('authorization') or '']
    else:
        finalize_args += ['--merge', '--authorization',
                          next_plan['finalize'].get('authorization') or '']
    result = subprocess.run(finalize_args, cwd=str(Path.cwd()))
    return {'plan': next_path, 'finalize_exit': result.returncode}


def _finalize_body(plan, state, journal, args):
    batch = Path(plan['batch']['worktree'])
    if not batch.is_dir() or _repo_root(batch) != batch.resolve():
        raise BatchError('the batch worktree is missing')
    if not _clean(batch):
        raise BatchError('the batch worktree is not clean')
    if _branch(batch) != plan['batch']['branch']:
        raise BatchError('the batch worktree is not on the batch branch')
    head = _head(batch)
    _require_tasks_integrated(state, plan)
    if plan['edition'] == 'github':
        _reconcile_github_merge(plan, state)
    elif state['merge'].get('state') != 'merged' and _ancestor(
            plan['batch']['repo'], head,
            _resolve_commit(plan['batch']['repo'], plan['batch']['base'])):
        state['merge'].update(state='merged', commit=_head(plan['batch']['repo']))
    merge_done = state['merge'].get('state') == 'merged'
    if not merge_done:
        state['batch_verify'] = _verify_guarded(
            batch, plan['finalize']['verify'], args.timeout,
            expected_head=head, label='batch verification',
            expected_branch=plan['batch']['branch'])
        after_verify = _head(batch)
        if after_verify != head:
            raise BatchError('batch verification moved the reviewed head',
                             status='BLOCKED')
    try:
        _, digest = _diff_for_review(plan['batch']['repo'], state['base_sha'], head)
        risk = classify_risk(plan['batch']['repo'], state['base_sha'], head)
    except RiskError as exc:
        raise InputError(f'cannot classify the batch diff: {exc}') from None
    review = state.get('review') or {}
    if not merge_done and review.get('head'):
        if (review['head'] != head
                or review.get('diff_sha256') != digest):
            raise BatchError('the reviewed head or diff changed before delivery; '
                             'a fresh review is required', status='BLOCKED')
    if not merge_done:
        state['risk'] = risk
        _atomic_json(journal, state)
        _review(plan, state, journal, args, state['base_sha'], head, digest, risk)
        _atomic_json(journal, state)
    else:
        _prove_task_integration(plan, state, state['base_sha'], head)
        _prove_review_current(state, plan, head, digest)
    if plan['edition'] == 'github':
        _github_flow(plan, state, journal, args, state['base_sha'], head, digest,
                     risk)
    else:
        _local_flow(plan, state, journal, args, state['base_sha'], head, digest,
                    risk)
    state['next_plan'] = args.next_plan or plan['finalize'].get('next_plan')
    _atomic_json(journal, state)


def _is_post_merge_state(state):
    merge = state.get('merge') or {}
    return (merge.get('state') in ('merged', 'merged_but_post_verify_failed')
            or state.get('phase') in ('merge', 'post_merge'))


def _resume_after_merge(plan, state, journal, args):
    """Re-enter after a merge without redoing integration or review."""
    repo = plan['batch']['repo']
    merge = state['merge']
    if plan['edition'] == 'github':
        if merge.get('state') != 'merged':
            if not _reconcile_github_merge(plan, state):
                raise BatchError('there is no merged pull request to resume',
                                 status='BLOCKED')
        commit = merge.get('commit')
        if not commit:
            raise BatchError('the journal records a merge without a commit',
                             status='BLOCKED')
        _post_merge_github(plan, state, commit, args.timeout)
    else:
        if merge.get('state') != 'merged':
            batch_head = _head(Path(plan['batch']['worktree']))
            base_head = _head(repo)
            if not _ancestor(repo, batch_head, base_head):
                raise BatchError('there is no local merge to resume',
                                 status='BLOCKED')
            merge.update(state='merged', commit=base_head)
        commit = merge.get('commit')
        if not commit:
            raise BatchError('the journal records a merge without a commit',
                             status='BLOCKED')
        _post_merge_local(plan, state, commit, args.timeout)
    state['phase'] = 'post_merge'
    _atomic_json(journal, state)
    if (state.get('carrier') or {}).get('state') != 'updated':
        if plan['edition'] == 'github':
            _carrier_github(state, plan, commit)
        else:
            _carrier_local(state, plan, journal, commit)
    if (state.get('carrier') or {}).get('state') != 'updated':
        raise BatchError('the carrier update did not complete; tasks remain '
                         'unverified', status='BLOCKED')
    for spec in plan['tasks']:
        state['tasks'][spec['id']]['status'] = 'VERIFIED'
    state['phase'] = 'verified'
    state['error'] = None
    _atomic_json(journal, state)


def command_finalize(args):
    raw = load_plan(args.plan)
    plan = validate_plan(raw, args.repo)
    journal = _resolve_journal(args.journal, plan)
    if args.merge:
        expected = plan['finalize']['authorization']
        if not expected or args.authorization != expected:
            raise InputError('--authorization must exactly equal the nonempty '
                             'finalize.authorization in the plan before any push '
                             'or merge')
    if not journal.exists():
        raise InputError(f'no batch journal at {journal}; run the batch first')
    with _Lock(journal):
        state = _load_journal(journal)
        _check_journal_plan(state, plan)
        if state.get('edition') != plan['edition']:
            raise InputError('the journal edition does not match the project')
        if state.get('phase') == 'verified':
            print(f'batch {state["batch_id"]} is already verified at '
                  f'{(state.get("merge") or {}).get("commit")}')
            return 0
        try:
            if _is_post_merge_state(state):
                _resume_after_merge(plan, state, journal, args)
            else:
                _finalize_body(plan, state, journal, args)
        except BatchError as exc:
            if state.get('phase') != 'verified':
                state['phase'] = 'blocked'
            state['error'] = str(exc)
            state.setdefault('merge', {})
            if state.get('merge', {}).get('state') != 'merged_but_post_verify_failed':
                if not state['merge'].get('state'):
                    state['merge']['state'] = 'none'
            _atomic_json(journal, state)
            raise
        state['error'] = None
        _atomic_json(journal, state)
        print(f'batch {state["batch_id"]}: {state.get("phase")}; merge '
              f'{(state.get("merge") or {}).get("commit")}')
    if state.get('next_plan'):
        result = _spawn_next(plan, args, journal)
        if result and (result.get('run_exit') or result.get('finalize_exit')):
            state['next_plan_result'] = result
            _atomic_json(journal, state)
            print(f'next batch failed: {result}')
            return 1
    return 0


def command_check(args):
    raw = load_plan(args.plan)
    plan = validate_plan(raw, args.repo)
    print(f'batch {plan["batch"]["id"]}: {plan["batch"]["title"]}')
    print(f'edition: {plan["edition"]}; project_paths: {plan["project_paths"]}')
    print(f'base: {plan["batch"]["base"]}; branch: {plan["batch"]["branch"]}')
    print('order: ' + ' -> '.join(spec['id'] for spec in plan['tasks']))
    for spec in plan['tasks']:
        print(f'{spec["id"]}: {spec["title"]} '
              f'(paths: {", ".join(spec["paths"])}; '
              f'depends: {", ".join(spec["depends"]) or "-"})')
    return 0


def command_status(args):
    journal = Path(args.journal).expanduser().absolute()
    if not journal.is_file():
        raise InputError(f'no batch journal at {journal}')
    state = _load_journal(journal)
    if args.json:
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        return 0
    print(f'batch {state.get("batch_id")} phase {state.get("phase")}')
    for ident in sorted(state.get('tasks') or {}):
        record = state['tasks'][ident]
        merge = state.get('merge') or {}
        print(f'{ident}: {record.get("status")} head={record.get("head") or "-"} '
              f'integration={record.get("integration_sha") or "-"} '
              f'pr={(state.get("pr") or {}).get("number") or "-"} '
              f'merge={merge.get("state") or "-"}')
    return 0


# ------------------------------------------------------------------------ CLI

def _parser():
    parser = argparse.ArgumentParser(
        description='Batch delivery coordinator (execute, verify, review, merge).')
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check', help='validate a batch plan; change nothing')
    check.add_argument('--plan', required=True)
    check.add_argument('--repo')
    check.set_defaults(func=command_check)
    run = sub.add_parser('run', help='execute, verify and integrate ready tasks')
    run.add_argument('--plan', required=True)
    run.add_argument('--journal')
    run.add_argument('--repo')
    run.add_argument('--execute', action='store_true')
    run.add_argument('--only')
    run.add_argument('--retry')
    run.add_argument('--resume', action='store_true')
    run.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    run.set_defaults(func=command_run)
    finalize = sub.add_parser('finalize', help='review, push, merge and verify')
    finalize.add_argument('--plan', required=True)
    finalize.add_argument('--journal')
    finalize.add_argument('--repo')
    finalize.add_argument('--review', action='store_true')
    finalize.add_argument('--acceptance-attestation', default=None)
    finalize.add_argument('--push', action='store_true')
    finalize.add_argument('--merge', action='store_true')
    finalize.add_argument('--authorization', default=None)
    finalize.add_argument('--resume', action='store_true')
    finalize.add_argument('--checks-timeout', type=int, default=DEFAULT_CHECKS_TIMEOUT)
    finalize.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    finalize.add_argument('--next-plan')
    finalize.set_defaults(func=command_finalize)
    status = sub.add_parser('status', help='read the journal; change nothing')
    status.add_argument('--journal', required=True)
    status.add_argument('--json', action='store_true')
    status.set_defaults(func=command_status)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except InputError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    except BatchError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('error: interrupted', file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
