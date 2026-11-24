"""Integration tests for the batch delivery coordinator.

Every test builds a real temporary Git repository with a bare origin, creates
fake ``opencode``/``gh`` executables and runs the CLI in a subprocess. The fake
OpenCode writes real files into real worktrees, the fake ``gh`` performs real
local Git merges into the bare origin, and the coordinator itself performs the
real worktree creation, commits, merges and verification. No network, no model
call, no third-party import.
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None
import hashlib

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'batch_delivery.py'
PY = sys.executable
sys.path.insert(0, str(SCRIPT.parents[1]))
from scripts import batch_delivery as delivery  # noqa: E402
AUTH = 'owner authorized this batch on 2026-11-16'
CHECKS = ['checks (Python 3.12)', 'checks (Python 3.13)']
CHECK_SUCCESS = [{'name': name, 'state': 'SUCCESS', 'link': 'https://ci.invalid/1'}
                 for name in CHECKS]


def check_runs(conclusions=None, app_id=123, status='completed'):
    conclusions = conclusions or {name: 'success' for name in CHECKS}
    return [{'name': name, 'app': {'id': app_id}, 'status': status,
             'conclusion': conclusion}
            for name, conclusion in conclusions.items()]

OPENCODE = r'''#!{python}
import json
import os
from pathlib import Path
import re
import subprocess
import sys

STATE = Path(os.environ['FAKE_OPENCODE_STATE'])
STATE.mkdir(parents=True, exist_ok=True)


def next_id(prefix):
    counter = STATE / 'counter'
    number = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(number))
    return f'ses_{{prefix}}{{number}}'


def remember(session, agent, text):
    payload = {{
        'info': {{'id': os.environ.get('FAKE_EXPORT_SESSION') or session,
                  'outcome': os.environ.get('FAKE_EXPORT_OUTCOME') or 'succeeded',
                  'location': {{'directory': os.environ.get('FAKE_EXPORT_DIR')
                                or os.getcwd()}}}},
        'messages': [
            {{'type': 'user', 'content': [{{'type': 'text', 'text': 'prompt'}}]}},
            {{'type': 'assistant',
              'agent': os.environ.get('FAKE_EXPORT_AGENT') or agent,
              'finish': 'stop', 'model': {{'id': 'fake'}},
              'content': [{{'type': 'text', 'text': text}}]}}],
    }}
    (STATE / f'{{session}}.json').write_text(json.dumps(payload))


def emit(session, text):
    print(json.dumps({{'type': 'step_start', 'sessionID': session}}))
    print(json.dumps({{'type': 'text', 'sessionID': session,
                       'part': {{'type': 'text', 'text': text,
                                 'sessionID': session}}}}))
    print(json.dumps({{'type': 'step_finish', 'sessionID': session,
                       'part': {{'reason': 'stop', 'sessionID': session}}}}))


args = sys.argv[1:]
with open(STATE / 'calls.jsonl', 'a') as log:
    log.write(json.dumps(args[:4]) + '\n')

if args[:1] == ['run']:
    prompt = args[-1]
    if os.environ.get('FAKE_PROMPT_FILE'):
        Path(os.environ['FAKE_PROMPT_FILE']).write_text(prompt)
    agent = args[args.index('--agent') + 1] if '--agent' in args else None
    if os.environ.get('FAKE_OPENCODE_FAIL') == '1':
        print('forced fake failure', file=sys.stderr)
        raise SystemExit(1)
    if 'ROLE=reviewer' in prompt:
        base = re.search(r'^BASE_SHA=([0-9a-f]+)$', prompt, re.M).group(1)
        head = re.search(r'^HEAD_SHA=([0-9a-f]+)$', prompt, re.M).group(1)
        digest = re.search(r'^DIFF_SHA256=([0-9a-f]+)$', prompt, re.M).group(1)
        risk = re.search(r'^RISK=(L[0-3])$', prompt, re.M).group(1)
        tasks = json.loads(re.search(r'^TASKS_JSON=(.*)$', prompt, re.M).group(1))
        verdict = os.environ.get('FAKE_REVIEW_VERDICT', 'APPROVE')
        wrong_head = os.environ.get('FAKE_REVIEW_WRONG_HEAD') == '1'
        bad_ac = os.environ.get('FAKE_REVIEW_BAD_AC') == '1'
        entries = []
        for task in tasks:
            acceptance = []
            for index, criterion in enumerate(task['acceptance'], start=1):
                status = 'FAIL' if bad_ac and index == 1 else 'PASS'
                acceptance.append({{'id': f'AC-{{index}}', 'criterion': criterion,
                                    'status': status, 'evidence': 'checked'}})
            entries.append({{'id': task['id'], 'acceptance': acceptance}})
        findings = [] if verdict == 'APPROVE' else [{{'severity': 'high',
                                                      'note': 'test rejection'}}]
        text = 'Review complete.\n```json\n' + json.dumps({{
            'verdict': verdict,
            'base': base,
            'head': ('0' * 40) if wrong_head else head,
            'diff_sha256': digest,
            'risk': risk,
            'tasks': entries,
            'findings': findings,
        }}) + '\n```\n'
        session = next_id('rev')
        remember(session, agent, text)
        if os.environ.get('FAKE_REVIEW_WRITE') == '1':
            Path('review-write.txt').write_text('x')
        emit(session, text)
        raise SystemExit(0)
    ident = re.search(r'^TASK_ID=(\S+)$', prompt, re.M).group(1)
    observed = os.environ.get('FAKE_OPENCODE_OBSERVE_JOURNAL')
    if observed:
        state = json.loads(Path(observed).read_text())
        task = state['tasks'][ident]
        Path(observed + '.attempt').write_text(json.dumps({{
            'attempts': task.get('attempts'),
            'history': task.get('attempt_history') or [],
        }}))
    paths = json.loads(re.search(r'^PATHS_JSON=(.*)$', prompt, re.M).group(1))
    for name in paths:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(os.environ.get('FAKE_EXECUTOR_CONTENT', ident) + '\n')
    extra = os.environ.get('FAKE_EXTRA_PATH')
    if extra:
        Path(extra).write_text('out of scope\n')
    if os.environ.get('FAKE_OPENCODE_COMMIT') == '1':
        subprocess.run(['git', 'add', '-A'], check=True)
        subprocess.run(['git', 'commit', '-q', '-m', f'agent {{ident}}'], check=True)
    text = f'executed {{ident}}'
    session = next_id('exec')
    remember(session, agent, text)
    emit(session, text)
    raise SystemExit(0)

if args[:2] == ['session', 'export']:
    path = STATE / f'{{args[2]}}.json'
    if not path.exists():
        raise SystemExit(1)
    print(path.read_text())
    raise SystemExit(0)

if args[:2] == ['debug', 'agents']:
    deny = os.environ.get('FAKE_REVIEW_NO_DENY') != '1'
    permissions = [{{'action': '*', 'resource': '*', 'effect': 'allow'}}]
    if deny:
        permissions.append({{'action': 'edit', 'resource': '*', 'effect': 'deny'}})
    print(json.dumps([
        {{'id': 'builder', 'permissions': [{{'action': '*', 'resource': '*',
                                            'effect': 'allow'}}]}},
        {{'id': 'reviewer', 'permissions': permissions}},
    ]))
    raise SystemExit(0)

raise SystemExit(2)
'''.format(python=PY)

GH = r'''#!{python}
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

STATE = Path(os.environ['FAKE_GH_STATE'])
STATE.parent.mkdir(parents=True, exist_ok=True)


def load():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {{'prs': [], 'issue': {{'number': 250, 'body': '', 'comments': [],
                                  'state': 'OPEN'}},
             'calls': []}}


def save(state):
    STATE.write_text(json.dumps(state))


def out(value):
    print(json.dumps(value))


def opt(name, default=None):
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except ValueError:
        return default


def git(*args, check=False):
    return subprocess.run(['git', '--no-optional-locks', *args], check=check,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)


def body_from_stdin():
    if opt('--body-file') == '-':
        return sys.stdin.read()
    return ''


def carrier(state, number):
    if 'carriers' in state:
        return state['carriers'].get(str(number))
    return state.setdefault('issue', {{}})


args = sys.argv[1:]
state = load()
state.setdefault('calls', []).append(' '.join(args))
save(state)


def pr_view(pr):
    if pr.get('state') == 'OPEN':
        head = git('rev-parse', pr['headRefName']).stdout.strip()
        if head:
            pr['headRefOid'] = head
    pr['baseRefName'] = pr.get('base')
    pr['mergeable'] = os.environ.get('FAKE_GH_MERGEABLE', 'MERGEABLE')
    return pr


if args[:2] == ['pr', 'list']:
    state = load()
    wanted = opt('--state', 'open')
    head = opt('--head')
    rows = []
    for pr in state.get('prs', []):
        if wanted == 'open' and pr.get('state') != 'OPEN':
            continue
        if wanted == 'all' and head and pr.get('headRefName') != head:
            continue
        if head and pr.get('headRefName') != head:
            continue
        item = dict(pr_view(pr))
        item['files'] = [{{'path': name}} for name in pr.get('files', [])]
        rows.append(item)
    extra = os.environ.get('FAKE_GH_OPEN_PR_FILES')
    if wanted == 'open' and not head and extra:
        rows.append({{'number': 90, 'headRefName': 'other',
                      'files': [{{'path': name}} for name in json.loads(extra)]}})
    save(state)
    out(rows)
    raise SystemExit(0)

if args[:2] == ['pr', 'create']:
    state = load()
    head = opt('--head')
    base = opt('--base')
    number = max([pr.get('number', 0) for pr in state.get('prs', [])] + [0]) + 1
    files = [line for line in git('diff', '--name-only', f'{{base}}..{{head}}')
             .stdout.splitlines() if line]
    pr = {{'number': number, 'url': f'https://example.invalid/pr/{{number}}',
          'base': base, 'headRefName': head,
          'headRefOid': git('rev-parse', head).stdout.strip(),
          'state': 'OPEN', 'mergeCommit': None, 'files': files,
          'body': body_from_stdin()}}
    state.setdefault('prs', []).append(pr)
    save(state)
    print(pr['url'])
    raise SystemExit(0)

if args[:1] == ['api']:
    path = ''
    for token in args[1:]:
        if not token.startswith('--'):
            path = token
    app_id = int(os.environ.get('FAKE_GH_RULE_APP_ID', '123'))
    if '/rules/branches/' in path:
        contexts = os.environ.get('FAKE_GH_RULE_CONTEXTS')
        contexts = json.loads(contexts) if contexts is not None else {check_names!r}
        out([{{'type': 'required_status_checks',
              'parameters': {{'required_status_checks': [
                  {{'context': name, 'integration_id': app_id}}
                  for name in contexts]}}}}])
        raise SystemExit(0)
    if '/check-runs' in path:
        if os.environ.get('FAKE_GH_ADVANCE_ON_CHECKS') == '1':
            marker = STATE.parent / 'base-advanced'
            if not marker.exists():
                marker.write_text('1')
                git('fetch', 'origin', 'main')
                directory = tempfile.mkdtemp(prefix='gh-advance-',
                                             dir=str(STATE.parent))
                git('worktree', 'add', '--detach', directory, 'origin/main')
                git('-C', directory, 'commit', '--allow-empty', '-m',
                    'advance base')
                git('-C', directory, 'push', 'origin', 'HEAD:refs/heads/main')
                git('worktree', 'remove', '--force', directory)
                shutil.rmtree(directory, ignore_errors=True)
        raw_runs = os.environ.get('FAKE_GH_CHECK_RUNS')
        if raw_runs is not None:
            runs = json.loads(raw_runs)
        else:
            runs = [{{'name': name, 'app': {{'id': app_id}},
                      'status': 'completed', 'conclusion': 'success'}}
                    for name in {check_names!r}]
        total = os.environ.get('FAKE_GH_CHECK_RUNS_TOTAL')
        out({{'total_count': int(total) if total is not None else len(runs),
              'check_runs': runs}})
        raise SystemExit(0)
    print('unsupported gh api path', file=sys.stderr)
    raise SystemExit(2)

if args[:2] == ['pr', 'checks']:
    checks = os.environ.get('FAKE_GH_CHECKS')
    out(json.loads(checks) if checks else {checks!r})
    raise SystemExit(0)

if args[:2] == ['pr', 'view']:
    state = load()
    number = int(args[2])
    for pr in state.get('prs', []):
        if pr.get('number') == number:
            pr_view(pr)
            save(state)
            out(pr)
            raise SystemExit(0)
    print('not found', file=sys.stderr)
    raise SystemExit(1)

if args[:2] == ['pr', 'merge']:
    state = load()
    number = int(args[2])
    match = opt('--match-head-commit')
    for pr in state.get('prs', []):
        if pr.get('number') != number:
            continue
        actual = git('rev-parse', pr['headRefName']).stdout.strip()
        if match and actual != match:
            print('head moved', file=sys.stderr)
            raise SystemExit(1)
        if pr.get('state') == 'MERGED':
            print('already merged')
            raise SystemExit(0)
        git('fetch', 'origin')
        directory = tempfile.mkdtemp(prefix='gh-merge-', dir=str(STATE.parent))
        added = git('worktree', 'add', '--detach', directory,
                    f"origin/{{pr['base']}}")
        if added.returncode != 0:
            print(added.stderr, file=sys.stderr)
            raise SystemExit(1)
        merged = git('-C', directory, 'merge', '--no-ff',
                     f"origin/{{pr['headRefName']}}", '-m',
                     f"merge {{pr['headRefName']}}")
        if merged.returncode != 0:
            git('-C', directory, 'merge', '--abort')
            git('worktree', 'remove', '--force', directory)
            print('merge conflict', file=sys.stderr)
            raise SystemExit(1)
        commit = git('-C', directory, 'rev-parse', 'HEAD').stdout.strip()
        pushed = git('-C', directory, 'push', 'origin',
                     f"HEAD:refs/heads/{{pr['base']}}")
        git('worktree', 'remove', '--force', directory)
        shutil.rmtree(directory, ignore_errors=True)
        if pushed.returncode != 0:
            print(pushed.stderr, file=sys.stderr)
            raise SystemExit(1)
        pr['state'] = 'MERGED'
        pr['mergeCommit'] = {{'oid': commit}}
        pr['headRefOid'] = actual
        save(state)
        print('merged')
        raise SystemExit(0)
    print('not found', file=sys.stderr)
    raise SystemExit(1)

if args[:2] == ['issue', 'list']:
    state = load()
    issues = (list(state['carriers'].values()) if 'carriers' in state
              else state.get('issues'))
    if issues is None:
        issues = [state.get('issue', {{}})]
    out([{{'number': item.get('number'), 'body': item.get('body') or ''}}
         for item in issues
         if str(item.get('state', 'OPEN')).upper() == 'OPEN'])
    raise SystemExit(0)

if args[:2] == ['issue', 'view']:
    state = load()
    issue = carrier(state, args[2])
    if issue is None:
        raise SystemExit(1)
    out(issue)
    raise SystemExit(0)

if args[:2] == ['issue', 'comment']:
    state = load()
    carrier(state, args[2]).setdefault('comments', []).append(body_from_stdin())
    save(state)
    print('commented')
    raise SystemExit(0)

if args[:2] == ['issue', 'edit']:
    if os.environ.get('FAKE_GH_FAIL_ISSUE_EDIT') == '1':
        print('issue edit failed by test', file=sys.stderr)
        raise SystemExit(1)
    state = load()
    carrier(state, args[2])['body'] = body_from_stdin()
    save(state)
    print('edited')
    raise SystemExit(0)

print('unsupported gh invocation', file=sys.stderr)
raise SystemExit(2)
'''.format(python=PY, checks=CHECK_SUCCESS, check_names=CHECKS)

ORCHESTRATOR = r'''#!{python}
import json
import os
import sys


def env_json(name, default):
    raw = os.environ.get(name)
    return json.loads(raw) if raw else default


with open(os.environ['FAKE_ORCH_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\n')

tasks = env_json('FAKE_ORCH_TASKS', {{}})
missing = {{name.strip() for name in os.environ.get('FAKE_ORCH_MISSING', '').split(',')
          if name.strip()}}
default_status = os.environ.get('FAKE_ORCH_STATUS', 'DISPATCHED')
status_map = env_json('FAKE_ORCH_STATUS_MAP', {{}})
paths_map = env_json('FAKE_ORCH_PATHS', {{}})
acceptance_map = env_json('FAKE_ORCH_ACCEPTANCE', {{}})
deps_map = env_json('FAKE_ORCH_DEPS', {{}})
locks_map = env_json('FAKE_ORCH_LOCKS', {{}})


def status_of(ident):
    return status_map.get(ident, default_status)


def paths_of(ident):
    return paths_map.get(
        ident, tasks.get(ident, {{}}).get('paths', []))


def acceptance_of(ident):
    return acceptance_map.get(
        ident, tasks.get(ident, {{}}).get('acceptance', []))


def deps_of(ident):
    return deps_map.get(
        ident, tasks.get(ident, {{}}).get('dependencies', []))


if sys.argv[1:3] == ['task', 'show']:
    ident = sys.argv[3]
    if ident in missing or (tasks and ident not in tasks):
        print('no such task', file=sys.stderr)
        raise SystemExit(1)
    print(f"# {{ident}} — test task")
    print()
    print(f"Status: {{status_of(ident)}}")
    print('Description: synthetic')
    print('Allowed paths:')
    paths = paths_of(ident)
    for path in paths or ['(none)']:
        print(f'- {{path}}')
    print()
    print('Dependencies:')
    for dependency in deps_of(ident) or ['None']:
        print(f'- {{dependency}}')
    print()
    print('Acceptance criteria:')
    for criterion in acceptance_of(ident) or ['(none)']:
        print(f'- [ ] {{criterion}}')
    raise SystemExit(0)

if sys.argv[1:3] == ['task', 'list']:
    for ident in tasks:
        print(f"{{ident}}\t{{status_of(ident)}}\ttest task")
        print('  paths: ' + ', '.join(paths_of(ident)))
        print('  locks: ' + ', '.join(locks_map.get(ident, paths_of(ident))))
        print('  retries: 0')
    raise SystemExit(0)

if sys.argv[1:3] in (['task', 'ready'], ['task', 'dispatch']):
    if os.environ.get('FAKE_ORCH_FAIL_TRANSITION') == '1':
        print('transition refused', file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)
raise SystemExit(0)
'''.format(python=PY)

PROJECT = """# Project configuration

- 允许改动路径：`a.txt`、`b.txt`、`c.txt`、`d.txt`、`docs/`、`tests/`、`scripts/`
- 永远禁止改动路径 / 文件：`LICENSE.md`
- 秘密与隐私文件模式：`.env*`、`*secret*`
- edition：`{edition}`
"""


def _git(where, *args, check=True):
    return subprocess.run(['git', '--no-optional-locks', *args], cwd=where,
                          check=check, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class BatchCase(unittest.TestCase):
    edition = 'github'
    orchestrator = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.fake_state = self.root / 'fake-state'
        self.fake_state.mkdir()
        self.opencode = self._executable('opencode', OPENCODE)
        self.gh = self._executable('gh', GH)
        self.gh_state = self.fake_state / 'gh.json'
        self.journal = self.root / 'journal.json'
        self.orch_log = self.root / 'orchestrator-calls.jsonl'
        self.origin = self.root / 'origin.git'
        self.repo = self.root / 'repo'
        self._repository()
        self.env = dict(os.environ)
        self.env.update({
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_CONFIG_SYSTEM': os.devnull,
            'HOME': str(self.home),
            'BATCH_OPENCODE': json.dumps([PY, '-S', str(self.opencode)]),
            'BATCH_GH': json.dumps([PY, '-S', str(self.gh)]),
            'FAKE_OPENCODE_STATE': str(self.fake_state),
            'FAKE_GH_STATE': str(self.gh_state),
            'FAKE_ORCH_LOG': str(self.orch_log),
        })
        for name in ('GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_DIR',
                     'GIT_WORK_TREE', 'GIT_COMMON_DIR'):
            self.env.pop(name, None)

    # -- fixtures ---------------------------------------------------------
    def _executable(self, name, text):
        path = self.bin / f'{name}.py'
        # The overrides below pass an explicit interpreter argv, so the fake
        # never needs a shebang or an executable bit. -S keeps interpreter
        # startup from dominating the suite; the standard library still works.
        path.write_text(text.replace(f'#!{PY}\n', '', 1))
        return path

    def _repository(self):
        _git(self.root, 'init', '-q', '--bare', '-b', 'main', str(self.origin))
        self.repo.mkdir()
        _git(self.repo, 'init', '-q', '-b', 'main')
        _git(self.repo, 'config', 'core.autocrlf', 'false')
        _git(self.repo, 'config', 'core.eol', 'lf')
        _git(self.repo, 'config', 'user.email', 'test@example.invalid')
        _git(self.repo, 'config', 'user.name', 'Test')
        (self.repo / 'docs').mkdir()
        (self.repo / 'docs' / 'PROJECT.md').write_text(
            PROJECT.format(edition=self.edition))
        (self.repo / 'base.txt').write_text('base\n')
        files = ['base.txt', 'docs/PROJECT.md']
        if self.orchestrator:
            (self.repo / 'scripts').mkdir()
            (self.repo / 'scripts' / 'orchestrator.py').write_text(ORCHESTRATOR)
            files.append('scripts/orchestrator.py')
        _git(self.repo, 'add', *files)
        _git(self.repo, 'commit', '-q', '-m', 'base')
        _git(self.repo, 'remote', 'add', 'origin', str(self.origin))
        _git(self.repo, 'push', '-q', '-u', 'origin', 'main')

    def seed_issue(self, body):
        self.gh_state.write_text(json.dumps({
            'prs': [], 'calls': [],
            'issue': {'number': 250, 'body': body, 'comments': [],
                      'state': 'OPEN'}}))

    def task(self, ident, name, depends=(), verify=None, title=None,
             acceptance_checks=None, acceptance_decisions=None):
        task = {
            'id': ident,
            'title': title or f'{ident} changes {name}',
            'prompt': f'Make the change required by {ident}.',
            'worktree': str(self.root / f'wt-{ident.lower()}'),
            'branch': f'task/batch-{ident.lower()}',
            'paths': [name],
            'acceptance': [f'{ident} writes {name}'],
            'verify': verify or [[PY, '-c', 'pass']],
            'depends': list(depends),
        }
        if acceptance_checks is not None:
            task['acceptance_checks'] = acceptance_checks
        if acceptance_decisions is not None:
            task['acceptance_decisions'] = acceptance_decisions
        return task

    def plan(self, tasks, *, risk_checks=None, next_plan=None):
        batch = {'id': 'batch-250', 'title': 'Batch 250',
                 'summary': 'deliver the batch on one branch',
                 'repo': str(self.repo), 'base': 'main',
                 'branch': 'task/batch-250',
                 'worktree': str(self.root / 'wt-batch'), 'issue': 250,
                 'issue_repo': 'owner/name'}
        finalize = {'verify': [[PY, '-c', 'pass']],
                    'post_merge_verify': [[PY, '-c', 'pass']],
                    'required_checks': risk_checks or CHECKS,
                    'merge_method': 'merge', 'authorization': AUTH,
                    'next_plan': next_plan}
        return {'version': 1, 'batch': batch, 'executor_agent': 'builder',
                'reviewer_agent': 'reviewer', 'tasks': tasks,
                'finalize': finalize}

    def write_plan(self, plan):
        path = self.root / 'plan.json'
        path.write_text(json.dumps(plan))
        self._plan_for_env = plan
        return path

    def _task_env(self):
        plan = getattr(self, '_plan_for_env', None)
        if not plan or plan.get('edition') == 'local' or 'tasks' not in plan:
            return {}
        mapping = {}
        for task in plan.get('tasks') or []:
            mapping[task['id']] = {'paths': task.get('paths') or [],
                                   'acceptance': task.get('acceptance') or [],
                                   'dependencies': []}
        return {'FAKE_ORCH_TASKS': json.dumps(mapping)}

    def cli(self, *args, extra=None):
        env = dict(self.env)
        env.update(self._task_env())
        if extra:
            env.update(extra)
        return subprocess.run([PY, str(SCRIPT), *args], cwd=self.root, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=180)

    def run_batch(self, plan, extra=None, *flags):
        path = self.write_plan(plan)
        return self.cli('run', '--plan', str(path), '--journal', str(self.journal),
                        '--execute', *flags, extra=extra)

    def finalize(self, plan, extra=None, *flags):
        path = self.write_plan(plan)
        return self.cli('finalize', '--plan', str(path), '--journal',
                        str(self.journal), *flags, extra=extra)

    def journal_data(self):
        return json.loads(self.journal.read_text())

    def gh_calls(self):
        if not self.gh_state.exists():
            return []
        return json.loads(self.gh_state.read_text()).get('calls', [])

    def gh_issue(self):
        return json.loads(self.gh_state.read_text()).get('issue', {})

    def opencode_calls(self, prefix='run'):
        path = self.fake_state / 'calls.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()
                if json.loads(line)[:1] == [prefix]]

    def session_files(self, prefix):
        return sorted(self.fake_state.glob(f'ses_{prefix}*.json'))

    def head(self, where, ref='HEAD'):
        return _git(where, 'rev-parse', ref).stdout.decode().strip()

    def empty_batch_merge_target(self):
        batch = self.root / 'wt-batch'
        return _git(batch, 'commit-tree', self.head(batch, 'HEAD^{tree}'),
                    '-p', self.head(batch, 'HEAD^2'), '-m',
                    'empty divergent commit').stdout.decode().strip()

    def count_commits(self, where, ref):
        return int(_git(where, 'rev-list', '--count', ref).stdout.decode().strip())

    def count_merges(self, where, ref):
        return int(_git(where, 'rev-list', '--merges', '--count',
                        ref).stdout.decode().strip())

    def lock_path(self):
        resolved = self.journal.resolve()
        digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:24]
        return resolved.parent / f'.batch-delivery-{digest}.lock'

    def write_attestation(self, plan, head=None, digest=None, status='PASS',
                          tasks=None):
        state = self.journal_data()
        real_head = self.head(self.root / 'wt-batch')
        head = head or real_head
        digest = digest or delivery._diff_for_review(
            self.repo, state['base_sha'], real_head)[1]
        if tasks is None:
            tasks = {}
            for spec in plan['tasks']:
                tasks[spec['id']] = [
                    {'id': f'AC-{index}', 'status': status,
                     'evidence': f'{spec["id"]} AC-{index} evidence'}
                    for index in range(1, len(spec['acceptance']) + 1)]
        path = self.root / 'attestation.json'
        path.write_text(json.dumps({'schema': 'batch-acceptance/1', 'head': head,
                                    'diff_sha256': digest, 'tasks': tasks}))
        return path

    def review_fails(self, extra, needle):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, extra, '--review', '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(needle, done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))
        return done, self.journal_data()


class GitHubBatchTests(BatchCase):
    def test_three_tasks_integrate_review_and_merge_once(self):
        self.seed_issue(
            '# 250\n\n<!-- ORCH-TASK-REGISTRY\nstatus: INTEGRATED\n'
            'locked_paths: old/path.txt\ndispatched_at: 2026-01-01T00:00:00Z\n'
            '-->\n')
        plan = self.plan([self.task('T1', 'a.txt'),
                          self.task('T2', 'b.txt', depends=['T1']),
                          self.task('T3', 'c.txt', depends=['T2'])])
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        state = self.journal_data()
        self.assertEqual(state['project_verification'], 'absent')
        for ident in ('T1', 'T2', 'T3'):
            self.assertEqual(state['tasks'][ident]['status'], 'INTEGRATED')
        self.assertEqual(state['tasks']['T2']['base'],
                         state['tasks']['T1']['integration_sha'])
        self.assertEqual(state['tasks']['T3']['base'],
                         state['tasks']['T2']['integration_sha'])
        for ident in ('T1', 'T2', 'T3'):
            self.assertTrue((self.root / f'wt-{ident.lower()}').is_dir())
        # One batch branch with three integration merges.
        self.assertEqual(self.count_merges(self.repo, 'task/batch-250'), 3)

        done = self.finalize(plan, None, '--review', '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        state = self.journal_data()
        self.assertEqual(state['phase'], 'verified')
        for ident in ('T1', 'T2', 'T3'):
            self.assertEqual(state['tasks'][ident]['status'], 'VERIFIED')
        self.assertEqual(state['merge']['state'], 'merged')
        merge_commit = state['merge']['commit']
        self.assertEqual(_git(self.repo, 'merge-base', '--is-ancestor',
                              merge_commit, 'origin/main').returncode, 0)
        calls = self.gh_calls()
        self.assertEqual(sum('pr create' in call for call in calls), 1)
        self.assertEqual(sum('pr merge' in call for call in calls), 1)
        # The PR body is written before the merge and must not claim success.
        pr_body = json.loads(self.gh_state.read_text())['prs'][0]['body']
        self.assertNotIn('merged into the base branch', pr_body)
        self.assertNotIn('post-merge verification passed', pr_body)
        self.assertIn('not a merge commit', pr_body)
        self.assertIn('Reviewed head:', pr_body)
        # The carrier comment and the registry block are recorded after merge.
        issue = self.gh_issue()
        self.assertTrue(issue['comments'])
        self.assertIn('merged into the base branch', issue['comments'][0])
        self.assertIn('post-merge verification passed', issue['comments'][0])
        self.assertEqual(issue['body'].count('ORCH-TASK-REGISTRY'), 1)
        self.assertNotIn('```text', issue['body'])
        self.assertIn('<!-- ORCH-TASK-REGISTRY', issue['body'])
        for line in ('status: VERIFIED', 'mode: batch',
                     'locked_paths: a.txt, b.txt, c.txt',
                     'branch: task/batch-250', 'runner: builder',
                     f'pr: {state["pr"]["number"]}',
                     'dispatched_at: 2026-01-01T00:00:00Z',
                     f'head_sha: {merge_commit}',
                     'blocked_reason: (none)'):
            self.assertIn(line, issue['body'])
        self.assertIn('updated_at: ', issue['body'])
        # A duplicate finalize adopts the existing state and merges once.
        again = self.finalize(plan, None, '--review', '--push', '--merge',
                              '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
        calls = self.gh_calls()
        self.assertEqual(sum('pr merge' in call for call in calls), 1)
        self.assertEqual(self.count_merges(self.repo, 'origin/main'), 4)

    def test_independent_failure_skips_only_dependents(self):
        plan = self.plan([
            self.task('T1', 'a.txt'),
            self.task('T2', 'b.txt', verify=[[PY, '-c', 'raise SystemExit(3)']]),
            self.task('T3', 'c.txt'),
            self.task('T4', 'd.txt', depends=['T2']),
        ])
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1, run.stdout)
        state = self.journal_data()
        self.assertEqual(state['tasks']['T1']['status'], 'INTEGRATED')
        self.assertEqual(state['tasks']['T2']['status'], 'FAILED')
        self.assertEqual(state['tasks']['T3']['status'], 'INTEGRATED')
        self.assertEqual(state['tasks']['T4']['status'], 'SKIPPED')
        self.assertIsNone(state['tasks']['T4']['execution_session'])

    def test_review_rejection_blocks_push_and_merge(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, {'FAKE_REVIEW_VERDICT': 'REJECT'}, '--review',
                             '--push', '--merge', '--authorization', AUTH,
                             '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))
        state = self.journal_data()
        self.assertEqual(state['review']['verdict'], 'REJECT')
        self.assertEqual(state['phase'], 'blocked')

    def test_failed_acceptance_rejects_the_review(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, {'FAKE_REVIEW_BAD_AC': '1'}, '--review',
                             '--push', '--merge', '--authorization', AUTH,
                             '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('not PASS', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_wrong_head_in_verdict_is_rejected(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, {'FAKE_REVIEW_WRONG_HEAD': '1'}, '--review',
                             '--push', '--merge', '--authorization', AUTH,
                             '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('different base or head', done.stderr)

    def test_reviewer_profile_that_can_edit_blocks(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, {'FAKE_REVIEW_NO_DENY': '1'}, '--review',
                             '--push', '--merge', '--authorization', AUTH,
                             '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('may edit', done.stderr)
        # The gate runs before the session: no reviewer transcript exists.
        self.assertEqual(self.session_files('rev'), [])
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_export_for_a_different_session_is_rejected(self):
        self.review_fails({'FAKE_EXPORT_SESSION': 'ses_other'}, 'requested session')

    def test_export_from_a_different_directory_is_rejected(self):
        self.review_fails({'FAKE_EXPORT_DIR': str(self.root / 'elsewhere')},
                          'different directory')

    def test_export_with_a_failed_outcome_is_rejected(self):
        self.review_fails({'FAKE_EXPORT_OUTCOME': 'failed'}, 'did not succeed')

    def test_export_naming_a_different_agent_is_rejected(self):
        self.review_fails({'FAKE_EXPORT_AGENT': 'builder'}, 'different agent')

    def test_rewritten_batch_branch_fails_the_integration_proof(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        batch = self.root / 'wt-batch'
        _git(batch, 'reset', '--hard', 'HEAD^')
        done = self.finalize(plan, None, '--review', '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('integration commit is not an ancestor', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_head_change_after_review_is_refused(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        first = self.finalize(plan, None, '--review')
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        batch = self.root / 'wt-batch'
        _git(batch, 'commit', '--allow-empty', '-m', 'move the head')
        again = self.finalize(plan, None, '--review', '--push', '--merge',
                              '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(again.returncode, 1)
        self.assertIn('changed before delivery', again.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_l0_reverted_batch_is_not_verified(self):
        plan = self.plan([self.task('T1', 'docs/note.md')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        batch = self.root / 'wt-batch'
        _git(batch, 'reset', '--hard', 'HEAD^')
        done = self.finalize(plan, None, '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('integration commit is not an ancestor', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))
        self.assertEqual(self.journal_data()['phase'], 'blocked')

    def test_l0_batch_needs_no_second_model(self):
        check = 'import pathlib; assert pathlib.Path("docs/note.md").exists()'
        plan = self.plan([self.task(
            'T1', 'docs/note.md', acceptance_checks=[[PY, '-c', check]])])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        attestation = self.write_attestation(plan)
        done = self.finalize(plan, None, '--acceptance-attestation',
                             str(attestation), '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        state = self.journal_data()
        self.assertEqual(state['review']['verdict'], 'NOT_REQUIRED_L0')
        self.assertIsNone(state['review']['session'])
        self.assertEqual(self.session_files('rev'), [])
        self.assertEqual(state['review']['acceptance_evidence']['T1'][0]['status'],
                         'PASS')
        self.assertEqual(
            state['review']['acceptance_decisions']['T1'][0]['status'], 'PASS')
        self.assertTrue(state['review']['acceptance_attestation_sha256'])
        self.assertEqual(state['phase'], 'verified')

    def test_l0_plan_decisions_alone_block(self):
        plan = self.plan([self.task(
            'T1', 'docs/note.md',
            acceptance_checks=[[PY, '-c', 'pass']],
            acceptance_decisions=[{'id': 'AC-1', 'status': 'PASS',
                                   'evidence': 'pre-run decision'}])])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, None, '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('--acceptance-attestation', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_l0_wrong_head_attestation_blocks(self):
        plan = self.plan([self.task(
            'T1', 'docs/note.md',
            acceptance_checks=[[PY, '-c', 'pass']])])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        attestation = self.write_attestation(plan, head='0' * 40)
        done = self.finalize(plan, None, '--acceptance-attestation',
                             str(attestation), '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('different head', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_l0_wrong_diff_attestation_blocks(self):
        plan = self.plan([self.task(
            'T1', 'docs/note.md',
            acceptance_checks=[[PY, '-c', 'pass']])])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        attestation = self.write_attestation(plan, digest='f' * 64)
        done = self.finalize(plan, None, '--acceptance-attestation',
                             str(attestation), '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('different diff', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_l0_attestation_with_unrelated_tasks_blocks(self):
        plan = self.plan([self.task(
            'T1', 'docs/note.md',
            acceptance_checks=[[PY, '-c', 'pass']])])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        attestation = self.write_attestation(
            plan, tasks={'T9': [{'id': 'AC-1', 'status': 'PASS',
                                 'evidence': 'unrelated'}]})
        done = self.finalize(plan, None, '--acceptance-attestation',
                             str(attestation), '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('exactly the batch task set', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_l0_without_acceptance_checks_is_blocked(self):
        plan = self.plan([self.task('T1', 'docs/note.md')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, None, '--push', '--merge', '--authorization',
                             AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('acceptance_checks and a matching --acceptance-attestation',
                      done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_l0_failed_master_decision_blocks_the_merge(self):
        plan = self.plan([self.task(
            'T1', 'docs/note.md',
            acceptance_checks=[[PY, '-c', 'pass']])])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        attestation = self.write_attestation(plan, status='FAIL')
        done = self.finalize(plan, None, '--acceptance-attestation',
                             str(attestation), '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('is not PASS', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_l0_without_checks_can_use_reviewer(self):
        plan = self.plan([self.task('T1', 'docs/note.md')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, None, '--review', '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        state = self.journal_data()
        self.assertEqual(state['review']['verdict'], 'APPROVE')
        self.assertTrue(state['review']['session'])

    def test_l0_failed_acceptance_check_blocks_the_merge(self):
        check = 'import pathlib; assert pathlib.Path("docs/note.md").read_text().strip() == "42"'
        plan = self.plan([self.task(
            'T1', 'docs/note.md', acceptance_checks=[[PY, '-c', check]])])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        attestation = self.write_attestation(plan)
        done = self.finalize(plan, None, '--acceptance-attestation',
                             str(attestation), '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('acceptance check T1 AC-1 failed', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_l2_batch_without_review_stops_before_push(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, None, '--push', '--merge', '--authorization',
                             AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('requires an independent review', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))
        self.assertTrue(all(record['status'] == 'INTEGRATED'
                            for record in self.journal_data()['tasks'].values()))

    def test_interrupted_committed_task_is_not_reexecuted(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        state = self.journal_data()
        state['tasks']['T1']['status'] = 'RUNNING'
        self.journal.write_text(json.dumps(state))
        before = len(self.opencode_calls())
        resumed = self.cli('run', '--plan', str(self.write_plan(plan)),
                           '--journal', str(self.journal), '--execute', '--resume')
        self.assertEqual(resumed.returncode, 1)
        self.assertEqual(len(self.opencode_calls()), before)
        self.assertEqual(self.journal_data()['tasks']['T1']['status'], 'BLOCKED')

    def test_retry_reopens_skipped_dependents(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([self.task('T1', 'a.txt', verify=[[PY, str(script)]]),
                          self.task('T2', 'b.txt', depends=['T1'])])
        first = self.run_batch(plan, {'FAIL_T1': '1'})
        self.assertEqual(first.returncode, 1)
        state = self.journal_data()
        self.assertEqual(state['tasks']['T1']['status'], 'FAILED')
        self.assertEqual(state['tasks']['T2']['status'], 'SKIPPED')
        second = self.cli('run', '--plan', str(self.write_plan(plan)), '--journal',
                          str(self.journal), '--execute', '--retry', 'T1')
        state = self.journal_data()
        # HD-2 B (Issue #267): the Windows Job Object proof makes the retry
        # path identical to POSIX instead of blocking on an unknown liveness.
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(state['tasks']['T1']['status'], 'INTEGRATED')
        self.assertEqual(state['tasks']['T2']['status'], 'INTEGRATED')
        self.assertEqual(state['tasks']['T2']['base'],
                         state['tasks']['T1']['integration_sha'])

    def test_base_moved_blocks_without_rebasing(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        other = self.root / 'other'
        _git(self.root, 'clone', '-q', str(self.origin), str(other))
        _git(other, 'config', 'user.email', 'test@example.invalid')
        _git(other, 'config', 'user.name', 'Test')
        _git(other, 'commit', '--allow-empty', '-m', 'move main')
        _git(other, 'push', '-q', 'origin', 'main')
        done = self.finalize(plan, None, '--review', '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('rebase is required', done.stderr)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))

    def test_wrong_task_worktree_state_fails_closed(self):
        work = self.root / 'wt-t1'
        _git(self.repo, 'worktree', 'add', '-q', '-b', 'wrong-branch', str(work),
             'main')
        plan = self.plan([self.task('T1', 'a.txt')])
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(self.journal_data()['tasks']['T1']['status'], 'FAILED')
        self.assertEqual(self.opencode_calls(), [])

    def test_configured_clean_filter_refuses_before_any_agent(self):
        _git(self.repo, 'config', 'filter.evil.clean', 'cat')
        plan = self.plan([self.task('T1', 'a.txt')])
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertIn('clean/process filter', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def test_configured_hook_refuses_before_any_agent(self):
        hook = self.repo / '.git' / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 0\n')
        hook.chmod(0o755)
        plan = self.plan([self.task('T1', 'a.txt')])
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertIn('commit hook pre-commit', run.stderr)
        self.assertEqual(self.opencode_calls(), [])
        self.assertEqual(self.journal_data()['phase'], 'blocked')

    def test_out_of_scope_change_fails_and_is_left_for_inspection(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        run = self.run_batch(plan, {'FAKE_EXTRA_PATH': 'evil.txt'})
        self.assertEqual(run.returncode, 1)
        self.assertEqual(self.journal_data()['tasks']['T1']['status'], 'FAILED')
        work = self.root / 'wt-t1'
        status = _git(work, 'status', '--porcelain').stdout.decode()
        self.assertIn('evil.txt', status)
        self.assertIn('scope violation', self.journal_data()['tasks']['T1']['error'])

    def test_agent_committed_changes_are_scope_checked(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        run = self.run_batch(plan, {'FAKE_OPENCODE_COMMIT': '1'})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.journal_data()['tasks']['T1']['status'], 'INTEGRATED')

    def test_failed_required_check_blocks_merge(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        runs = check_runs({CHECKS[0]: 'failure', CHECKS[1]: 'success'})
        done = self.finalize(plan, {'FAKE_GH_CHECK_RUNS': json.dumps(runs)},
                             '--review', '--push', '--merge', '--authorization',
                             AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('not success', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))
        self.assertEqual(self.journal_data()['phase'], 'blocked')

    def test_missing_required_check_is_not_a_pass(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        runs = check_runs({CHECKS[0]: 'success'})
        done = self.finalize(plan, {'FAKE_GH_CHECK_RUNS': json.dumps(runs)},
                             '--review', '--push', '--merge', '--authorization',
                             AUTH, '--checks-timeout', '1')
        self.assertEqual(done.returncode, 1)
        self.assertIn('missing required check run', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_wrong_check_app_id_blocks(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        runs = check_runs(app_id=999)
        done = self.finalize(plan, {'FAKE_GH_CHECK_RUNS': json.dumps(runs)},
                             '--review', '--push', '--merge', '--authorization',
                             AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('configured integration', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_truncated_check_run_list_blocks(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        runs = check_runs({CHECKS[0]: 'success'})
        done = self.finalize(
            plan, {'FAKE_GH_CHECK_RUNS': json.dumps(runs),
                   'FAKE_GH_CHECK_RUNS_TOTAL': '2'},
            '--review', '--push', '--merge', '--authorization', AUTH,
            '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('truncated', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_failure_then_success_same_context_blocks(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        runs = [
            {'name': CHECKS[0], 'app': {'id': 123}, 'status': 'completed',
             'conclusion': 'failure', 'id': 1,
             'completed_at': '2026-01-01T00:00:00Z'},
            {'name': CHECKS[0], 'app': {'id': 123}, 'status': 'completed',
             'conclusion': 'success', 'id': 2,
             'completed_at': '2026-01-01T00:01:00Z'},
            check_runs({CHECKS[1]: 'success'})[0],
        ]
        done = self.finalize(plan, {'FAKE_GH_CHECK_RUNS': json.dumps(runs)},
                             '--review', '--push', '--merge', '--authorization',
                             AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('completed run with conclusion', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_success_then_failure_same_context_blocks(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        runs = [
            {'name': CHECKS[0], 'app': {'id': 123}, 'status': 'completed',
             'conclusion': 'success', 'id': 1,
             'completed_at': '2026-01-01T00:01:00Z'},
            {'name': CHECKS[0], 'app': {'id': 123}, 'status': 'completed',
             'conclusion': 'failure', 'id': 2,
             'completed_at': '2026-01-01T00:00:00Z'},
            check_runs({CHECKS[1]: 'success'})[0],
        ]
        done = self.finalize(plan, {'FAKE_GH_CHECK_RUNS': json.dumps(runs)},
                             '--review', '--push', '--merge', '--authorization',
                             AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('completed run with conclusion', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_same_name_foreign_app_run_blocks(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        runs = [
            {'name': CHECKS[0], 'app': {'id': 123}, 'status': 'completed',
             'conclusion': 'success', 'id': 1},
            {'name': CHECKS[0], 'app': {'id': 999}, 'status': 'completed',
             'conclusion': 'success', 'id': 2},
            check_runs({CHECKS[1]: 'success'})[0],
        ]
        done = self.finalize(plan, {'FAKE_GH_CHECK_RUNS': json.dumps(runs)},
                             '--review', '--push', '--merge', '--authorization',
                             AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('run identity is ambiguous', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_retry_of_an_integrated_task_is_refused(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        retried = self.cli('run', '--plan', str(self.write_plan(plan)), '--journal',
                           str(self.journal), '--execute', '--retry', 'T1')
        self.assertEqual(retried.returncode, 2)
        self.assertIn('only accepts FAILED tasks', retried.stderr)

    def test_live_lock_is_refused_and_dead_pid_lock_is_taken(self):
        lock = self.lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text('999999')
        plan = self.plan([self.task('T1', 'a.txt')])
        first = self.run_batch(plan)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        lock.write_text(str(os.getpid()))
        if fcntl is None:
            return
        with open(lock, 'a+') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                second = self.run_batch(plan)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self.assertEqual(second.returncode, 1)
        self.assertIn('locked', second.stderr)


    def test_run_after_verified_is_a_noop(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, None, '--review', '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        before = len(self.gh_calls())
        again = self.cli('run', '--plan', str(self.write_plan(plan)),
                         '--journal', str(self.journal), '--execute', '--resume')
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
        self.assertEqual(len(self.gh_calls()), before)

    def test_github_without_issue_blocks_before_agents(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['batch']['issue'] = None
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertIn('batch.issue', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def test_open_pr_conflict_blocks_task_before_agents(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        run = self.run_batch(plan, {'FAKE_GH_OPEN_PR_FILES': '["a.txt"]'})
        self.assertEqual(run.returncode, 1)
        state = self.journal_data()
        self.assertEqual(state['tasks']['T1']['status'], 'BLOCKED')
        self.assertIn('open PR', state['tasks']['T1']['error'])
        self.assertEqual(self.opencode_calls(), [])

    def test_resume_does_not_rewrite_dispatch(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        body = self.gh_issue()['body']
        edits = sum('issue edit' in call for call in self.gh_calls())
        self.assertEqual(edits, 1)
        resumed = self.cli('run', '--plan', str(self.write_plan(plan)),
                           '--journal', str(self.journal), '--execute', '--resume')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(sum('issue edit' in call for call in self.gh_calls()), edits)
        self.assertEqual(self.gh_issue()['body'], body)

    # -- verification may not move the reviewed head ----------------------
    def _verify_script(self, actions):
        script = self.root / 'verify_action.py'
        script.write_text(
            'import os, pathlib, subprocess\n'
            'action = os.environ.get("VERIFY_ACTION")\n'
            + actions +
            'raise SystemExit(0)\n')
        return script

    def _finalize_with_verify_action(self, action):
        script = self._verify_script(
            'if action == "commit":\n'
            '    subprocess.run(["git", "commit", "--allow-empty", "-m", "drift"],'
            ' check=True)\n'
            'elif action == "reset":\n'
            '    subprocess.run(["git", "reset", "--hard", "HEAD^"], check=True)\n'
            'elif action == "dirty":\n'
            '    pathlib.Path("drift.txt").write_text("x")\n')
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['finalize']['verify'] = [[PY, str(script)]]
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, {'VERIFY_ACTION': action}, '--review',
                             '--push', '--merge', '--authorization', AUTH,
                             '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))
        self.assertEqual(self.journal_data()['phase'], 'blocked')
        return done

    def test_batch_verification_that_commits_is_refused(self):
        done = self._finalize_with_verify_action('commit')
        self.assertIn('moved HEAD', done.stderr)

    def test_batch_verification_that_resets_is_refused(self):
        done = self._finalize_with_verify_action('reset')
        self.assertIn('moved HEAD', done.stderr)

    def test_batch_verification_that_dirties_is_refused(self):
        done = self._finalize_with_verify_action('dirty')
        self.assertIn('dirty', done.stderr)

    # -- post-merge commands may not undo the merge -----------------------
    def _post_script(self):
        script = self.root / 'post_action.py'
        script.write_text(
            'import os, pathlib, subprocess\n'
            'action = os.environ.get("POST_ACTION")\n'
            'if action == "commit":\n'
            '    subprocess.run(["git", "commit", "--allow-empty", "-m", "drift"],'
            ' check=True)\n'
            'elif action == "reset":\n'
            '    subprocess.run(["git", "reset", "--hard", "HEAD^"], check=True)\n'
            'elif action == "dirty":\n'
            '    pathlib.Path("drift.txt").write_text("x")\n'
            'elif action == "branch":\n'
            '    subprocess.run(["git", "checkout", "-q", "-b", "other"], check=True)\n'
            'raise SystemExit(0)\n')
        return script

    def _finalize_with_post_action(self, action):
        script = self._post_script()
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['finalize']['post_merge_verify'] = [[PY, str(script)]]
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, {'POST_ACTION': action}, '--review', '--push',
                             '--merge', '--authorization', AUTH,
                             '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1, done.stdout)
        state = self.journal_data()
        self.assertEqual(state['merge']['state'], 'merged_but_post_verify_failed')
        self.assertTrue(all(record['status'] == 'BLOCKED'
                            for record in state['tasks'].values()))
        return done, plan

    def test_post_merge_commit_is_refused(self):
        done, _ = self._finalize_with_post_action('commit')
        self.assertIn('moved HEAD', done.stderr)

    def test_post_merge_reset_is_refused(self):
        done, _ = self._finalize_with_post_action('reset')
        self.assertIn('moved HEAD', done.stderr)

    def test_post_merge_dirty_tree_is_refused(self):
        done, _ = self._finalize_with_post_action('dirty')
        self.assertIn('dirty', done.stderr)

    def test_post_merge_branch_move_is_refused(self):
        done, _ = self._finalize_with_post_action('branch')
        self.assertIn('left the base branch', done.stderr)

    def test_post_merge_failure_resumes_on_second_finalize(self):
        done, plan = self._finalize_with_post_action('dirty')
        self.assertEqual(done.returncode, 1)
        (self.repo / 'drift.txt').unlink()
        again = self.finalize(plan, None, '--review', '--push', '--merge',
                              '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
        state = self.journal_data()
        self.assertEqual(state['phase'], 'verified')
        self.assertEqual(state['merge']['state'], 'merged')
        self.assertEqual(sum('pr merge' in call for call in self.gh_calls()), 1)

    def test_carrier_failure_resumes_on_second_finalize(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        first = self.finalize(plan, {'FAKE_GH_FAIL_ISSUE_EDIT': '1'}, '--review',
                              '--push', '--merge', '--authorization', AUTH,
                              '--checks-timeout', '5')
        self.assertEqual(first.returncode, 1)
        self.assertIn('ORCH-TASK-REGISTRY', first.stderr)
        self.assertEqual(self.journal_data()['merge']['state'], 'merged')
        self.assertNotEqual(self.journal_data()['phase'], 'verified')
        second = self.finalize(plan, None, '--review', '--push', '--merge',
                               '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        state = self.journal_data()
        self.assertEqual(state['phase'], 'verified')
        self.assertEqual(sum('pr merge' in call for call in self.gh_calls()), 1)

    def test_fetch_failure_after_merge_resumes_later(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        first = self.finalize(plan, {'FAKE_GH_FAIL_ISSUE_EDIT': '1'}, '--review',
                              '--push', '--merge', '--authorization', AUTH,
                              '--checks-timeout', '5')
        self.assertEqual(first.returncode, 1)
        _git(self.repo, 'remote', 'set-url', 'origin', str(self.root / 'missing'))
        broken = self.finalize(plan, None, '--review', '--push', '--merge',
                               '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(broken.returncode, 1)
        self.assertIn('git fetch origin failed', broken.stderr)
        self.assertEqual(self.journal_data()['merge']['state'],
                         'merged_but_post_verify_failed')
        _git(self.repo, 'remote', 'set-url', 'origin', str(self.origin))
        third = self.finalize(plan, None, '--review', '--push', '--merge',
                              '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(third.returncode, 0, third.stderr + third.stdout)
        self.assertEqual(self.journal_data()['phase'], 'verified')
        self.assertEqual(sum('pr merge' in call for call in self.gh_calls()), 1)

    # -- PR identity and authoritative checks -----------------------------
    def test_same_head_pr_on_wrong_base_is_refused(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        head = self.head(self.root / 'wt-batch')
        self.gh_state.write_text(json.dumps({
            'calls': [],
            'issue': {'number': 250, 'body': '', 'comments': [], 'state': 'OPEN'},
            'prs': [{'number': 5, 'url': 'https://example.invalid/pr/5',
                     'base': 'other', 'headRefName': 'task/batch-250',
                     'headRefOid': head, 'state': 'OPEN', 'mergeCommit': None,
                     'files': [], 'body': ''}]}))
        done = self.finalize(plan, None, '--review', '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('pull request base', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_unknown_mergeability_is_refused(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, {'FAKE_GH_MERGEABLE': 'UNKNOWN'}, '--review',
                             '--push', '--merge', '--authorization', AUTH,
                             '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('mergeability', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_authoritative_extra_check_still_gates_merge(self):
        plan = self.plan([self.task('T1', 'a.txt')], risk_checks=[CHECKS[0]])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        runs = check_runs({CHECKS[0]: 'success', CHECKS[1]: 'failure'})
        done = self.finalize(plan, {'FAKE_GH_CHECK_RUNS': json.dumps(runs)},
                             '--review', '--push', '--merge', '--authorization',
                             AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn(CHECKS[1], done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_empty_authoritative_checks_fail_closed(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, {'FAKE_GH_RULE_CONTEXTS': '[]'}, '--review',
                             '--push', '--merge', '--authorization', AUTH,
                             '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('yield no required status contexts', done.stderr)

    def test_project_config_change_blocks_resume(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        path = self.repo / 'docs' / 'PROJECT.md'
        path.write_text(path.read_text() + '\n- extra\n')
        resumed = self.cli('run', '--plan', str(self.write_plan(plan)),
                           '--journal', str(self.journal), '--execute', '--resume')
        self.assertEqual(resumed.returncode, 2)
        self.assertIn('docs/PROJECT.md changed', resumed.stderr)


    def test_truncated_pr_enumeration_blocks(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        prs = [{'number': 2000 + index, 'url': 'https://x/pr',
                'base': 'main', 'headRefName': f'other-{index}',
                'state': 'OPEN', 'mergeCommit': None, 'files': [], 'body': ''}
               for index in range(1000)]
        self.gh_state.write_text(json.dumps({
            'calls': [], 'prs': prs,
            'issue': {'number': 250, 'body': '', 'comments': [], 'state': 'OPEN'}}))
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertIn('PR enumeration reached its 1000-item limit', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def test_multiple_registry_blocks_in_an_issue_block(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        body = ('<!-- ORCH-TASK-REGISTRY\nstatus: DISPATCHED\n'
                'locked_paths: other.txt\nbranch: task/a\n-->\n'
                '<!-- ORCH-TASK-REGISTRY\nstatus: DISPATCHED\n'
                'locked_paths: other2.txt\nbranch: task/b\n-->\n')
        self.gh_state.write_text(json.dumps({
            'calls': [], 'prs': [],
            'issue': {'number': 250, 'body': '', 'comments': [], 'state': 'OPEN'},
            'issues': [{'number': 250, 'body': '', 'state': 'OPEN'},
                       {'number': 998, 'body': body, 'state': 'OPEN'}]}))
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertIn('ambiguous', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def _registry_status_run(self, status):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.gh_state.write_text(json.dumps({
            'calls': [], 'prs': [],
            'issue': {'number': 250, 'body': '', 'comments': [], 'state': 'OPEN'},
            'issues': [
                {'number': 250, 'body': '', 'state': 'OPEN'},
                {'number': 997, 'state': 'OPEN',
                 'body': f'<!-- ORCH-TASK-REGISTRY\nstatus: {status}\n'
                         'mode: batch\nlocked_paths: a.txt\n'
                         'branch: task/other\n-->\n'}]}))
        return self.run_batch(plan)

    def test_registry_lock_retaining_statuses_block(self):
        for status in ('PR_OPEN', 'MERGED', 'NOT_A_REGISTRY_STATUS'):
            with self.subTest(status=status):
                self.setUp()
                run = self._registry_status_run(status)
                self.assertEqual(run.returncode, 1, run.stderr)
                if status == 'NOT_A_REGISTRY_STATUS':
                    self.assertIn('unknown ORCH-TASK-REGISTRY status', run.stderr)
                else:
                    self.assertIn('active lock conflict', run.stderr)
                self.assertEqual(self.opencode_calls(), [])

    def test_registry_released_statuses_do_not_block(self):
        for status in ('VERIFIED', 'CANCELED'):
            with self.subTest(status=status):
                self.setUp()
                run = self._registry_status_run(status)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(
                    self.journal_data()['tasks']['T1']['status'], 'INTEGRATED')

    def test_active_issue_registry_overlap_blocks_without_prs(self):
        plan = self.plan([self.task('T1', 'a.txt'), self.task('T2', 'b.txt')])
        self.gh_state.write_text(json.dumps({
            'calls': [], 'prs': [],
            'issue': {'number': 250, 'body': '', 'comments': [],
                      'state': 'OPEN'},
            'issues': [
                {'number': 250, 'body': '', 'state': 'OPEN'},
                {'number': 999, 'state': 'OPEN',
                 'body': '<!-- ORCH-TASK-REGISTRY\nstatus: DISPATCHED\n'
                         'mode: batch\nlocked_paths: a.txt\n'
                         'branch: task/other\n-->\n'}]}))
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertIn('active lock conflict', run.stderr)
        self.assertEqual({record['status']
                          for record in self.journal_data()['tasks'].values()},
                         {'BLOCKED'})
        self.assertEqual(self.opencode_calls(), [])
        self.assertFalse(any('issue edit' in call for call in self.gh_calls()))

    def test_truncated_issue_enumeration_blocks(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        issues = [{'number': index, 'body': '', 'state': 'OPEN'}
                  for index in range(1, 1001)]
        self.gh_state.write_text(json.dumps({
            'calls': [], 'prs': [],
            'issue': {'number': 250, 'body': '', 'comments': [], 'state': 'OPEN'},
            'issues': issues}))
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertIn('Issue enumeration reached its 1000-item limit', run.stderr)
        self.assertEqual({record['status']
                          for record in self.journal_data()['tasks'].values()},
                         {'BLOCKED'})
        self.assertEqual(self.opencode_calls(), [])

    def test_many_open_prs_one_conflict_blocks_the_whole_batch(self):
        plan = self.plan([self.task('T1', 'a.txt'), self.task('T2', 'b.txt')])
        prs = []
        for index in range(35):
            files = ['a.txt'] if index == 31 else []
            prs.append({'number': 100 + index, 'url': f'https://x/pr/{index}',
                        'base': 'main', 'headRefName': f'other-{index}',
                        'state': 'OPEN', 'mergeCommit': None, 'files': files,
                        'body': ''})
        self.gh_state.write_text(json.dumps({
            'calls': [], 'prs': prs,
            'issue': {'number': 250, 'body': '', 'comments': [], 'state': 'OPEN'}}))
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertIn('blocks the whole batch', run.stderr)
        state = self.journal_data()
        self.assertEqual({record['status'] for record in state['tasks'].values()},
                         {'BLOCKED'})
        self.assertEqual(self.opencode_calls(), [])
        self.assertFalse(any('issue edit' in call for call in self.gh_calls()))

    def test_same_branch_registry_with_wrong_paths_blocks(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.seed_issue(
            '<!-- ORCH-TASK-REGISTRY\nstatus: DISPATCHED\nmode: batch\n'
            'locked_paths: other.txt\nbranch: task/batch-250\n'
            'dispatched_at: 2026-01-01T00:00:00Z\n-->\n')
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertIn('registry locks do not match the batch plan', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def test_configured_context_without_a_check_result_blocks(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        runs = check_runs({CHECKS[0]: 'success'})
        done = self.finalize(plan, {'FAKE_GH_CHECK_RUNS': json.dumps(runs)},
                             '--review', '--push', '--merge', '--authorization',
                             AUTH, '--checks-timeout', '1')
        self.assertEqual(done.returncode, 1)
        self.assertIn(CHECKS[1], done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_base_advanced_while_waiting_blocks_merge(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        done = self.finalize(plan, {'FAKE_GH_ADVANCE_ON_CHECKS': '1'},
                             '--review', '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1)
        self.assertIn('base branch advanced', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))


class InputErrorTests(BatchCase):
    def append_project(self, text):
        path = self.repo / 'docs' / 'PROJECT.md'
        path.write_text(path.read_text() + '\n' + text)

    def test_declared_project_verification_command_is_required(self):
        self.append_project('## 验证命令\n\n'
                            '- 快速测试：`python scripts/validate_kit.py`\n')
        plan = self.plan([self.task('T1', 'a.txt')])
        check = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(check.returncode, 2)
        self.assertIn('declares staged project verification command', check.stderr)

    def test_project_verification_python_names_are_normalized(self):
        self.append_project('## 验证命令\n\n'
                            '- 快速测试：`python scripts/validate_kit.py`\n')
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['finalize']['post_merge_verify'] = [
            [sys.executable, 'scripts/validate_kit.py']]
        check = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_quick_command_only_in_verify_blocks(self):
        self.append_project('## 验证命令\n\n'
                            '- 快速测试：`python scripts/validate_kit.py`\n')
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['finalize']['verify'] = [[PY, 'scripts/validate_kit.py']]
        check = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(check.returncode, 2)
        self.assertIn('post-merge', check.stderr)

    def test_project_verification_ignores_placeholder_backticks(self):
        text = ('# p\n\n## 验证命令\n\n'
                '- 快速测试：`python scripts/validate_kit.py`\n'
                '- 说明：`python scripts/verification_profile.py '
                '--evidence <json>`\n')
        commands = delivery._project_verification(text)
        self.assertEqual(commands,
                         [('post', ('<python>', 'scripts/validate_kit.py'))])

    def test_post_only_command_placed_in_verify_blocks(self):
        self.append_project(
            '## 验证命令\n\n'
            '- 完整回归：`python -m unittest discover -s tests`\n')
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['finalize']['verify'] = [[PY, '-m', 'unittest', 'discover',
                                       '-s', 'tests']]
        check = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(check.returncode, 2)
        self.assertIn('post-merge', check.stderr)

    def test_multiple_commands_on_merge_line_are_all_required(self):
        self.append_project(
            '## 验证命令\n\n'
            '- 合并后必须执行：`python a.py` + `python b.py`\n')
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['finalize']['post_merge_verify'] = [[PY, 'a.py']]
        check = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(check.returncode, 2)
        self.assertIn('b.py', check.stderr)

    def test_ambiguous_verification_line_blocks(self):
        self.append_project('## 验证命令\n\n- 快速测试：见上文\n')
        plan = self.plan([self.task('T1', 'a.txt')])
        check = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(check.returncode, 2)
        self.assertIn('no interpretable command', check.stderr)

    def test_repository_verification_stages_are_parsed(self):
        text = (Path('docs/PROJECT.md').read_text(encoding='utf-8'))
        commands = delivery._project_verification(text)
        self.assertIn(('post', ('<python>', 'scripts/validate_kit.py')),
                      commands)
        self.assertIn(('post', ('<python>', '-m', 'unittest', 'discover', '-s',
                                'tests')), commands)
        self.assertIn(('post', ('<python>', '-m', 'py_compile',
                                'scripts/orchestrator.py',
                                'scripts/validate_kit.py')), commands)
        self.assertNotIn(
            ('post', ('<python>', 'scripts/orchestrator.py', 'backup')),
            commands)
        self.assertTrue(all(stage == 'post' for stage, _ in commands))

    def test_all_four_default_commands_in_post_merge_passes(self):
        self.append_project(
            '## 验证命令\n\n'
            '- 快速测试：`python scripts/validate_kit.py`\n'
            '- 完整回归：`python -m unittest discover -s tests`\n'
            '- 静态检查：`python -m py_compile a.py b.py`\n'
            '- 合并后必须执行：`python scripts/orchestrator.py backup`\n')
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['finalize']['post_merge_verify'] = [
            [PY, 'scripts/validate_kit.py'],
            [PY, '-m', 'unittest', 'discover', '-s', 'tests'],
            [PY, '-m', 'py_compile', 'a.py', 'b.py'],
            [PY, 'scripts/orchestrator.py', 'backup']]
        check = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_project_config_path_is_refused_in_a_batch(self):
        plan = self.plan([self.task('T1', 'docs/PROJECT.md')])
        check = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(check.returncode, 2)
        self.assertIn('may not do', check.stderr)

    def test_journal_inside_repository_is_refused(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        run = self.cli('run', '--plan', str(path), '--journal',
                       str(self.repo / 'journal.json'), '--execute')
        self.assertEqual(run.returncode, 2)

    def test_journal_inside_task_worktree_is_refused(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        journal = self.root / 'wt-t1' / 'journal.json'
        run = self.cli('run', '--plan', str(path), '--journal', str(journal),
                       '--execute')
        self.assertEqual(run.returncode, 2)

    def test_path_overlap_is_refused(self):
        plan = self.plan([self.task('T1', 'a.txt'), self.task('T2', 'a.txt')])
        run = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(run.returncode, 2)
        self.assertIn('overlapping paths', run.stderr)

    def test_plan_hash_change_is_refused(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        plan['batch']['title'] = 'changed after the journal was written'
        resumed = self.cli('run', '--plan', str(self.write_plan(plan)),
                           '--journal', str(self.journal), '--execute', '--resume')
        self.assertEqual(resumed.returncode, 2)
        self.assertIn('plan changed', resumed.stderr)

    def test_dry_run_and_check_write_nothing(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        check = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(check.returncode, 0)
        self.assertIn('T1', check.stdout)
        dry = self.cli('run', '--plan', str(self.write_plan(plan)), '--journal',
                       str(self.journal))
        self.assertEqual(dry.returncode, 0)
        self.assertFalse(self.journal.exists())
        self.assertFalse((self.root / 'wt-t1').exists())

    def test_invalid_edition_is_refused(self):
        (self.repo / 'docs' / 'PROJECT.md').write_text(
            PROJECT.format(edition='nonsense'))
        _git(self.repo, 'add', 'docs/PROJECT.md')
        _git(self.repo, 'commit', '-q', '-m', 'bad edition')
        plan = self.plan([self.task('T1', 'a.txt')])
        check = self.cli('check', '--plan', str(self.write_plan(plan)))
        self.assertEqual(check.returncode, 2)
        self.assertIn('exactly one', check.stderr)

    def test_plan_requires_checks_authorization_and_distinct_branch(self):
        no_checks = self.plan([self.task('T1', 'a.txt')], risk_checks=[])
        no_checks['finalize']['required_checks'] = []
        rejected = self.cli('check', '--plan', str(self.write_plan(no_checks)))
        self.assertEqual(rejected.returncode, 2)
        self.assertIn('required_check', rejected.stderr)

        no_auth = self.plan([self.task('T1', 'a.txt')])
        no_auth['finalize']['authorization'] = ''
        rejected = self.cli('check', '--plan', str(self.write_plan(no_auth)))
        self.assertEqual(rejected.returncode, 2)
        self.assertIn('authorization', rejected.stderr)

        same_branch = self.plan([self.task('T1', 'a.txt')])
        same_branch['batch']['base'] = 'trunk'
        same_branch['batch']['branch'] = 'trunk'
        rejected = self.cli('check', '--plan', str(self.write_plan(same_branch)))
        self.assertEqual(rejected.returncode, 2)
        self.assertIn('must differ from batch.base', rejected.stderr)

    def test_status_reads_the_journal(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        status = self.cli('status', '--journal', str(self.journal), '--json')
        self.assertEqual(status.returncode, 0)
        self.assertEqual(json.loads(status.stdout)['batch_id'], 'batch-250')
        missing = self.cli('status', '--journal', str(self.root / 'nope.json'))
        self.assertEqual(missing.returncode, 2)


class LocalBatchTests(BatchCase):
    edition = 'local'
    orchestrator = True

    @staticmethod
    def decisions(count=1):
        return [{'id': f'AC-{index}', 'status': 'PASS',
                 'evidence': 'local test decision'} for index in range(1, count + 1)]

    def test_local_merge_post_verify_and_carrier_chain(self):
        def checks(name):
            return [[PY, '-c',
                     f'import pathlib; assert pathlib.Path("{name}").exists()']]
        plan = self.plan([
            self.task('T1', 'docs/one.md', acceptance_checks=checks('docs/one.md'),
                      acceptance_decisions=self.decisions()),
            self.task('T2', 'docs/two.md', depends=['T1'],
                      acceptance_checks=checks('docs/two.md'),
                      acceptance_decisions=self.decisions())])
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        done = self.finalize(plan, None, '--review', '--merge',
                             '--authorization', AUTH)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        state = self.journal_data()
        self.assertEqual(state['phase'], 'verified')
        self.assertEqual(state['merge']['state'], 'merged')
        self.assertTrue((self.repo / 'docs' / 'one.md').is_file())
        self.assertTrue((self.repo / 'docs' / 'two.md').is_file())
        self.assertEqual(self.count_merges(self.repo, 'HEAD'), 3)
        self.assertEqual(state['carrier']['state'], 'updated')
        calls = [json.loads(line) for line in self.orch_log.read_text().splitlines()]
        merged = [call for call in calls
                  if call[:4] == ['task', 'status', 'T1', 'MERGED']]
        self.assertTrue(merged)
        self.assertIn('--merge-sha', merged[0])
        self.assertIn(state['merge']['commit'], merged[0])
        self.assertTrue(any(call[:4] == ['task', 'status', 'T1', 'VERIFIED']
                            for call in calls))
        self.assertFalse(self.gh_state.exists())

    def test_absent_local_library_blocks_before_any_agent(self):
        (self.repo / 'scripts' / 'orchestrator.py').unlink()
        _git(self.repo, 'add', '-A')
        _git(self.repo, 'commit', '-q', '-m', 'remove orchestrator')
        plan = self.plan([self.task('T1', 'docs/one.md')])
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 1)
        self.assertIn('scripts/orchestrator.py', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def test_carrier_local_without_library_blocks(self):
        plan = delivery.validate_plan(
            self.plan([self.task('T1', 'docs/one.md')]), self.repo)
        state = delivery._new_journal(plan, self.head(self.repo, 'main'))
        for spec in plan['tasks']:
            state['tasks'][spec['id']] = delivery._task_record(
                spec, plan['executor_agent'])
        (self.repo / 'scripts' / 'orchestrator.py').unlink()
        with self.assertRaises(delivery.BatchError) as caught:
            delivery._carrier_local(state, plan, self.root / 'j.json', '0' * 40)
        self.assertIn('local carrier is absent', str(caught.exception))
        self.assertEqual(state['carrier']['state'], 'blocked')

    def test_local_missing_task_blocks_before_any_agent(self):
        plan = self.plan([self.task('T1', 'docs/one.md')])
        run = self.run_batch(plan, {'FAKE_ORCH_MISSING': 'T1'})
        self.assertEqual(run.returncode, 1)
        self.assertIn('local task library', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def test_local_registered_paths_mismatch_blocks(self):
        plan = self.plan([self.task('T1', 'docs/one.md')])
        run = self.run_batch(plan, {'FAKE_ORCH_PATHS': '{"T1": ["other.md"]}'})
        self.assertEqual(run.returncode, 1)
        self.assertIn('registered allowed paths', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def test_local_registered_acceptance_mismatch_blocks(self):
        plan = self.plan([self.task('T1', 'docs/one.md')])
        run = self.run_batch(
            plan, {'FAKE_ORCH_ACCEPTANCE': '{"T1": ["different criterion"]}'})
        self.assertEqual(run.returncode, 1)
        self.assertIn('acceptance criteria do not match', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def test_local_released_lock_blocks(self):
        plan = self.plan([self.task('T1', 'docs/one.md')])
        run = self.run_batch(plan, {'FAKE_ORCH_LOCKS': '{"T1": []}'})
        self.assertEqual(run.returncode, 1)
        self.assertIn('currently holds locks', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def test_local_terminal_task_with_pending_journal_blocks(self):
        plan = self.plan([self.task('T1', 'docs/one.md')])
        run = self.run_batch(plan, {'FAKE_ORCH_STATUS_MAP': '{"T1": "VERIFIED"}'})
        self.assertEqual(run.returncode, 1)
        self.assertIn('terminal task as dispatchable', run.stderr)
        self.assertEqual(self.opencode_calls(), [])

    def test_local_in_batch_dependency_is_unsupported(self):
        plan = self.plan([self.task('T1', 'docs/one.md'),
                          self.task('T2', 'docs/two.md', depends=['T1'])])
        path = self.write_plan(plan)
        before = path.read_text()
        run = self.run_batch(plan, {'FAKE_ORCH_DEPS': '{"T2": ["T1"]}'})
        self.assertEqual(run.returncode, 1)
        self.assertIn('does not support local batch-internal dependencies',
                      run.stderr)
        self.assertEqual(path.read_text(), before)
        self.assertEqual(self.opencode_calls(), [])
        calls = [json.loads(line) for line in self.orch_log.read_text().splitlines()]
        self.assertFalse(any(call[:2] in (['task', 'ready'],
                                          ['task', 'dispatch']) for call in calls))
        self.assertIsNone(self.journal_data()['dispatch'])

    def test_local_ready_task_is_dispatched(self):
        plan = self.plan([self.task('T1', 'docs/one.md')])
        run = self.run_batch(plan, {'FAKE_ORCH_STATUS': 'READY'})
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        calls = [json.loads(line) for line in self.orch_log.read_text().splitlines()]
        self.assertIn(['task', 'dispatch', 'T1'], calls)

    def test_local_base_moved_blocks_the_merge(self):
        plan = self.plan([self.task(
            'T1', 'docs/one.md',
            acceptance_checks=[[PY, '-c',
                                'import pathlib; assert pathlib.Path("docs/one.md").exists()']],
            acceptance_decisions=self.decisions())])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        _git(self.repo, 'commit', '--allow-empty', '-m', 'move base')
        done = self.finalize(plan, None, '--review', '--merge',
                             '--authorization', AUTH)
        self.assertEqual(done.returncode, 1)
        self.assertIn('base moved', done.stderr)
        self.assertNotEqual(self.journal_data()['merge']['state'], 'merged')

    def test_local_retry_does_not_dispatch_again(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([
            self.task('T1', 'docs/one.md', verify=[[PY, str(script)]]),
            self.task('T2', 'docs/two.md', depends=['T1'])])
        first = self.run_batch(plan, {'FAIL_T1': '1',
                                      'FAKE_ORCH_STATUS': 'READY'})
        self.assertEqual(first.returncode, 1)
        state = self.journal_data()
        self.assertEqual(state['tasks']['T1']['status'], 'FAILED')
        self.assertEqual(state['tasks']['T2']['status'], 'SKIPPED')
        self.assertIn('T1', state['dispatch']['tasks'])
        dispatches = sum(
            1 for line in self.orch_log.read_text().splitlines()
            if json.loads(line) == ['task', 'dispatch', 'T1'])
        second = self.cli('run', '--plan', str(self.write_plan(plan)), '--journal',
                          str(self.journal), '--execute', '--retry', 'T1')
        state = self.journal_data()
        # HD-2 B (Issue #267): Windows reuses the worktree exactly like POSIX
        # once the Job Object proof is recorded.
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(state['tasks']['T1']['status'], 'INTEGRATED')
        self.assertEqual(state['tasks']['T2']['status'], 'INTEGRATED')
        self.assertEqual(sum(
            1 for line in self.orch_log.read_text().splitlines()
            if json.loads(line) == ['task', 'dispatch', 'T1']), dispatches)

    def test_local_resume_running_without_head(self):
        plan = self.plan([self.task('T1', 'docs/one.md')])
        first = self.run_batch(plan, {'FAKE_OPENCODE_FAIL': '1',
                                      'FAKE_ORCH_STATUS': 'READY'})
        self.assertEqual(first.returncode, 1)
        state = self.journal_data()
        self.assertEqual(state['tasks']['T1']['status'], 'FAILED')
        self.assertIsNone(state['tasks']['T1']['head'])
        dispatches = sum(
            1 for line in self.orch_log.read_text().splitlines()
            if json.loads(line) == ['task', 'dispatch', 'T1'])
        self.assertEqual(dispatches, 1)
        state['tasks']['T1']['status'] = 'RUNNING'
        state['tasks']['T1']['error'] = None
        self.journal.write_text(json.dumps(state))
        second = self.cli('run', '--plan', str(self.write_plan(plan)), '--journal',
                          str(self.journal), '--execute', '--resume')
        state = self.journal_data()
        # HD-2 B (Issue #267): the recorded Job Object is gone after the
        # coordinator died, so kill-on-close already stopped the whole tree.
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(state['tasks']['T1']['status'], 'INTEGRATED')
        self.assertEqual(sum(
            1 for line in self.orch_log.read_text().splitlines()
            if json.loads(line) == ['task', 'dispatch', 'T1']), dispatches)

    def test_local_merge_requires_exact_authorization(self):
        plan = self.plan([self.task(
            'T1', 'docs/one.md',
            acceptance_checks=[[PY, '-c',
                                'import pathlib; assert pathlib.Path("docs/one.md").exists()']],
            acceptance_decisions=self.decisions())])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        missing = self.finalize(plan, None, '--merge')
        self.assertEqual(missing.returncode, 2)
        self.assertIn('authorization', missing.stderr)
        wrong = self.finalize(plan, None, '--merge', '--authorization', 'wrong')
        self.assertEqual(wrong.returncode, 2)
        self.assertIn('authorization', wrong.stderr)
        self.assertEqual(self.count_commits(self.repo, 'HEAD'), 1)
        self.assertEqual(self.journal_data()['merge']['state'], 'none')


class SuccessorCase(BatchCase):
    merge_flags = ('--review', '--merge', '--authorization', AUTH)
    crash_after_dispatch = """
save = delivery._atomic_json
def interrupted_save(path, state):
    if state.get('batch_id') == 'batch-251' and state.get('dispatch'):
        os._exit(89)
    return save(path, state)
delivery._atomic_json = interrupted_save
"""

    def setUp(self):
        super().setUp()
        self.env['USERPROFILE'] = str(self.home)
        self.child_journal = self.home / '.cache' / 'agentorch' / 'batch-batch-251.json'
        self.child_path = self.root / 'successor.json'
        self.front = self.plan([self.task('T1', 'a.txt')])
        self.child = self.plan([self.task('T2', 'b.txt')])
        self.child['batch'].update(id='batch-251', branch='task/batch-251',
                                   worktree=str(self.root / 'wt-next'), issue=251)
        self.child['finalize']['authorization'] = 'authorization for batch 251'
        self.child_path.write_text(json.dumps(self.child), encoding='utf-8')

    def interrupt_finalize(self, hook, *args):
        code = ('import os, sys\n'
                'sys.path.insert(0, sys.argv[1])\n'
                'from scripts import batch_delivery as delivery\n'
                f'{hook}\n'
                'sys.exit(delivery.main(sys.argv[2:]))\n')
        result = subprocess.run(
            [PY, '-c', code, str(SCRIPT.parents[1]), 'finalize',
             '--plan', str(self.write_plan(self.front)), '--journal',
             str(self.journal), *args], cwd=self.root, env=self.env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=180)
        self.assertEqual(result.returncode, 89, result.stderr + result.stdout)

    def finish_front_then_interrupt(self):
        run = self.run_batch(self.front)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.interrupt_finalize('delivery._continue_successor = lambda *a: os._exit(89)',
                                *self.merge_flags, '--next-plan', str(self.child_path))
        self.assertEqual(self.journal_data()['phase'], 'verified')
        self.assertFalse(self.child_journal.exists())

    def read_child(self):
        return json.loads(self.child_journal.read_text(encoding='utf-8'))


class GitHubSuccessorHandoffTests(SuccessorCase):
    def setUp(self):
        super().setUp()
        self.gh_state.write_text(json.dumps({
            'prs': [], 'calls': [], 'carriers': {
                '250': {'number': 250, 'state': 'OPEN', 'body': ''},
                '251': {'number': 251, 'state': 'OPEN',
                        'labels': [{'name': 'status:ready'}],
                        'body': '## 依赖\n- #250\n'},
            }}), encoding='utf-8')

    def change_registry(self, number, status):
        state = json.loads(self.gh_state.read_text(encoding='utf-8'))
        issue = state['carriers'][str(number)]
        body = issue['body']
        if delivery._registry_blocks(body):
            previous = delivery._registry_value(delivery._registry_inner(body), 'status')
            issue['body'] = body.replace('status: ' + previous, 'status: ' + status)
        else:
            issue['body'] += '\n' + delivery._registry_text([('status', status)])
        self.gh_state.write_text(json.dumps(state), encoding='utf-8')

    def test_review_pr_and_failed_post_merge_do_not_dispatch_successor(self):
        self.front['finalize']['post_merge_verify'] = [[
            PY, '-c', 'import os; raise SystemExit(int(os.environ.get("POST_FAIL", 0)))']]
        self.front['finalize']['next_plan'] = str(self.child_path)
        run = self.run_batch(self.front)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        for flags, env, phase, code in (
                (('--review',), {}, 'review', 0),
                (('--push', '--checks-timeout', '0'),
                 {'FAKE_GH_CHECK_RUNS': '[]'}, 'blocked', 1),
                (('--push',), {}, 'pr', 0),
                (self.merge_flags, {'POST_FAIL': '1'}, 'blocked', 1)):
            with self.subTest(flags=flags, env=env):
                result = self.finalize(self.front, env, *flags)
                self.assertEqual(result.returncode, code, result.stderr + result.stdout)
                self.assertEqual(self.journal_data()['phase'], phase)
                self.assertFalse(self.child_journal.exists())
                self.assertFalse(any(call.startswith('issue edit 251')
                                     for call in self.gh_calls()))
        self.assertEqual(self.journal_data()['merge']['state'],
                         'merged_but_post_verify_failed')
        self.assertEqual(len(self.session_files('exec')), 1)

    def test_recovers_before_and_after_registration_and_after_remote_merge(self):
        self.finish_front_then_interrupt()
        self.assertNotIn('next_plan_result', self.journal_data())
        self.interrupt_finalize(self.crash_after_dispatch, '--resume')
        self.assertIsNone(self.read_child()['dispatch'])
        self.assertNotIn('next_plan_result', self.journal_data())
        self.interrupt_finalize("""
create = delivery._github_pr
def interrupted_pr(plan, *args):
    pr = create(plan, *args)
    if plan['batch']['id'] == 'batch-251':
        os._exit(89)
    return pr
delivery._github_pr = interrupted_pr
""", '--resume')
        self.assertIsNone(self.read_child()['pr'])
        github = json.loads(self.gh_state.read_text(encoding='utf-8'))
        github['prs'][1]['state'] = 'CLOSED'
        self.gh_state.write_text(json.dumps(github), encoding='utf-8')
        denied = self.finalize(self.front, None, '--resume')
        self.assertEqual(denied.returncode, 1)
        self.assertIn('does not match the recorded progress', denied.stderr)
        github = json.loads(self.gh_state.read_text(encoding='utf-8'))
        self.assertEqual(len(github['prs']), 2)
        github['prs'][1]['state'] = 'OPEN'
        self.gh_state.write_text(json.dumps(github), encoding='utf-8')
        self.interrupt_finalize("""
merge = delivery._merge_github
def interrupted_merge(plan, *args):
    commit = merge(plan, *args)
    if plan['batch']['id'] == 'batch-251':
        os._exit(89)
    return commit
delivery._merge_github = interrupted_merge
""", '--resume')
        self.assertEqual(self.read_child()['phase'], 'pr')
        for _ in range(2):
            done = self.finalize(self.front, None, '--resume')
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            self.assertEqual(self.read_child()['phase'], 'verified')
        calls = self.gh_calls()
        self.assertEqual(sum(call.startswith('pr create ') for call in calls), 2)
        self.assertEqual(sum(call.startswith('pr merge ') for call in calls), 2)
        self.assertTrue(all('--repo owner/name' in call for call in calls
                            if call.startswith(('pr create ', 'pr merge '))))
        self.assertEqual(sum(call.startswith('issue edit 251 ') for call in calls), 2)
        self.assertEqual(len(self.session_files('exec')), 2)
        self.assertEqual(len(self.session_files('rev')), 2)
        self.assertEqual(self.read_child()['tasks']['T2']['attempts'], 1)
        self.assertEqual(self.journal_data()['next_plan_result']['merge_commit'],
                         self.head(self.repo))

    def test_requires_post_merge_evidence_carrier_and_unchanged_authorization(self):
        self.finish_front_then_interrupt()
        verified_state = self.journal_data()
        missing = self.journal_data()
        missing['merge']['post_verify'] = []
        self.journal.write_text(json.dumps(missing), encoding='utf-8')
        done = self.finalize(self.front, None, '--resume')
        self.assertEqual(done.returncode, 1)
        self.assertIn('post-merge evidence', done.stderr)
        self.journal.write_text(json.dumps(verified_state), encoding='utf-8')
        self.change_registry(250, 'MERGED')
        done = self.finalize(self.front, None, '--resume')
        self.assertEqual(done.returncode, 1)
        self.assertIn('registry status does not match', done.stderr)
        self.change_registry(250, 'VERIFIED')
        github = json.loads(self.gh_state.read_text(encoding='utf-8'))
        github['carriers']['251']['labels'] = []
        self.gh_state.write_text(json.dumps(github), encoding='utf-8')
        done = self.finalize(self.front, None, '--resume')
        self.assertEqual(done.returncode, 1)
        self.assertIn('no authoritative READY status', done.stderr)
        self.change_registry(251, 'NEEDS_HUMAN_DECISION')
        done = self.finalize(self.front, None, '--resume')
        self.assertEqual(done.returncode, 1)
        self.assertIn('NEEDS_HUMAN_DECISION', done.stderr)
        self.child['finalize']['authorization'] = 'another authorization'
        self.child_path.write_text(json.dumps(self.child), encoding='utf-8')
        done = self.finalize(self.front, None, '--resume')
        self.assertEqual(done.returncode, 2)
        self.assertIn('authorization changed', done.stderr)
        self.assertFalse(self.child_journal.exists())
        self.assertEqual(self.journal_data()['phase'], 'verified')

    def test_legacy_start_proves_dependencies_and_preserves_conflict_locks(self):
        self.child['tasks'].append(self.task('T3', 'c.txt', depends=['T2']))
        self.child_path.write_text(json.dumps(self.child), encoding='utf-8')
        self.finish_front_then_interrupt()
        legacy = self.journal_data()
        del legacy['successor']
        self.journal.write_text(json.dumps(legacy), encoding='utf-8')
        done = self.finalize(self.front, None, '--resume')
        self.assertEqual(done.returncode, 1)
        self.assertIn('legacy journal lacks a successor plan digest', done.stderr)
        self.assertFalse(self.child_journal.exists())
        github = json.loads(self.gh_state.read_text(encoding='utf-8'))
        github['carriers']['251']['body'] = (
            '## Dependencies\n- #250\n'
            '- [Other prerequisite](https://github.com/owner/name/issues/252)\n'
            '- Both issues must pass verification on the main branch.\n'
            '## T3\n### 依赖\nT2（批内依赖）\n')
        github['carriers']['252'] = {
            'number': 252, 'state': 'OPEN',
            'body': delivery._registry_text([('status', 'MERGED'),
                                            ('locked_paths', 'b.txt')])}
        self.gh_state.write_text(json.dumps(github), encoding='utf-8')
        flags = ('--resume', '--next-plan', str(self.child_path))
        done = self.finalize(self.front, None, *flags)
        self.assertEqual(done.returncode, 1)
        self.assertIn('dependency #252 is not VERIFIED', done.stderr)
        self.assertFalse(self.child_journal.exists())
        self.change_registry(252, 'VERIFIED')
        done = self.finalize(self.front, {'FAKE_GH_OPEN_PR_FILES': '["b.txt"]'}, *flags)
        self.assertEqual(done.returncode, 1)
        self.assertIn('open PR #90', done.stderr)
        self.assertFalse(self.child_journal.exists())
        self.assertEqual(len(self.session_files('exec')), 1)
        done = self.finalize(self.front, None, '--resume')
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        child = self.read_child()
        self.assertEqual(child['phase'], 'verified')
        self.assertEqual({task['status'] for task in child['tasks'].values()},
                         {'VERIFIED'})

    def test_attached_successor_reuses_pending_request(self):
        self.child['executor_adapter'] = {
            'transport': 'attached', 'name': 'test-host',
            'capabilities': {f'C{index}': 'PASS' for index in range(1, 5)},
            'capability_evidence': 'https://example.invalid/probe'}
        self.child_path.write_text(json.dumps(self.child), encoding='utf-8')
        self.finish_front_then_interrupt()
        first = self.finalize(self.front, None, '--resume')
        self.assertEqual(first.returncode, 3, first.stderr + first.stdout)
        pending = self.read_child()['tasks']['T2']['awaiting_host']
        self.assertTrue(Path(pending['request_path']).is_file())
        second = self.finalize(self.front, None, '--resume')
        self.assertEqual(second.returncode, 3, second.stderr + second.stdout)
        self.assertEqual(self.read_child()['tasks']['T2']['awaiting_host'], pending)
        self.assertEqual(self.read_child()['tasks']['T2']['attempts'], 1)
        self.assertEqual(len(self.session_files('exec')), 1)
        self.assertEqual(sum(call.startswith('pr create ') for call in self.gh_calls()), 1)


class LocalSuccessorHandoffTests(SuccessorCase):
    edition = 'local'

    def _repository(self):
        super()._repository()
        (self.repo / 'scripts').mkdir()
        source = SCRIPT.with_name('orchestrator.py')
        (self.repo / 'scripts' / source.name).write_bytes(source.read_bytes())
        (self.repo / 'docs' / 'PLAN.md').write_text('# Fixture project plan\n')
        _git(self.repo, 'add', 'scripts/orchestrator.py', 'docs/PLAN.md')
        _git(self.repo, 'commit', '-q', '-m', 'add fixture task carrier')

    def orchestrate(self, *args):
        completed = subprocess.run(
            [PY, str(self.repo / 'scripts' / 'orchestrator.py'), *args],
            cwd=self.repo, env=self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        return completed.stdout

    def add_record(self, task, *deps):
        self.orchestrate('task', 'add', '--id', task['id'], '--title', task['title'],
                         '--paths', *task['paths'], '--acceptance', *task['acceptance'],
                         '--depends', *deps)

    def setUp(self):
        super().setUp()
        self.orchestrate('init')
        self.add_record(self.front['tasks'][0])
        self.add_record(self.child['tasks'][0], 'T1')

    def test_recovers_real_sqlite_dispatch_without_taking_lock_twice(self):
        other = self.task('T3', 'b.txt')
        self.add_record(other)
        self.orchestrate('task', 'ready', 'T3')
        self.orchestrate('task', 'dispatch', 'T3')
        self.finish_front_then_interrupt()
        done = self.finalize(self.front, None, '--resume')
        self.assertEqual(done.returncode, 1)
        self.assertIsNone(self.read_child()['dispatch'])
        self.assertIn('Status: PLANNED', self.orchestrate('task', 'show', 'T2'))
        self.assertIn('locks: b.txt', self.orchestrate('task', 'list'))
        self.orchestrate('task', 'status', 'T3', 'CANCELED',
                         '--note', 'release the fixture conflict')
        self.interrupt_finalize(self.crash_after_dispatch, '--resume')
        self.assertIn('Status: DISPATCHED', self.orchestrate('task', 'show', 'T2'))
        self.assertIsNone(self.read_child()['dispatch'])
        for _ in range(2):
            done = self.finalize(self.front, None, '--resume')
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        note = self.orchestrate('task', 'show', 'T2')
        self.assertIn('Status: VERIFIED', note)
        self.assertEqual(note.count('TASK_DISPATCHED'), 1)
        self.assertEqual(len(self.session_files('exec')), 2)
        self.assertEqual(self.count_merges(self.repo, 'main'), 4)
        self.assertFalse(self.gh_state.exists())

    def test_local_external_dependency_blocks_before_dispatch(self):
        self.add_record(self.task('T3', 'c.txt'))
        third = self.task('T4', 'd.txt')
        self.add_record(third, 'T3')
        self.child['tasks'].append(third)
        self.child_path.write_text(json.dumps(self.child), encoding='utf-8')
        self.finish_front_then_interrupt()
        done = self.finalize(self.front, None, '--resume')
        self.assertEqual(done.returncode, 1)
        self.assertIn('local dependency T3 is not VERIFIED', done.stderr)
        self.assertFalse(self.child_journal.exists())
        self.assertIn('Status: PLANNED', self.orchestrate('task', 'show', 'T2'))
        self.assertEqual(len(self.session_files('exec')), 1)


class ReviewDiffTests(BatchCase):
    def test_review_prompt_exposes_exact_verify_commands(self):
        plan = self.plan([self.task(
            'T1', 'a.txt', verify=[[PY, '-c', 'pass']])])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        prompt_file = self.root / 'review-prompt.txt'
        done = self.finalize(plan, {'FAKE_PROMPT_FILE': str(prompt_file)},
                             '--review')
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        prompt = prompt_file.read_text(encoding='utf-8')
        line = next(line for line in prompt.splitlines()
                    if line.startswith('TASKS_JSON='))
        tasks = json.loads(line.split('=', 1)[1])
        self.assertEqual(tasks[0]['verify_commands'], [[PY, '-c', 'pass']])

    def test_review_diff_uses_the_merge_base(self):
        _git(self.repo, 'checkout', '-q', '-b', 'head')
        (self.repo / 'batch.txt').write_text('batch\n')
        _git(self.repo, 'add', 'batch.txt')
        _git(self.repo, 'commit', '-q', '-m', 'batch change')
        head = self.head(self.repo)
        _git(self.repo, 'checkout', '-q', 'main')
        (self.repo / 'base-only.txt').write_text('base only\n')
        _git(self.repo, 'add', 'base-only.txt')
        _git(self.repo, 'commit', '-q', '-m', 'base moved')
        base = self.head(self.repo)
        diff, digest = delivery._diff_for_review(self.repo, base, head)
        self.assertIn('batch.txt', diff)
        self.assertNotIn('base-only.txt', diff)
        self.assertEqual(digest, hashlib.sha256(diff.encode()).hexdigest())


class ObservedIntegratedHeadTests(BatchCase):
    def verification(self, body, *args):
        return [[PY, '-c', body, *map(str, args)]]

    def task_record(self):
        return self.journal_data()['tasks']['T1']

    def test_observation_is_durable_before_verification_and_after_resumes(self):
        batch, work = self.root / 'wt-batch', self.root / 'wt-t1'
        verify = self.verification(
            'import json, pathlib, subprocess, sys\n'
            'row = json.loads(pathlib.Path(sys.argv[1]).read_text())["tasks"]["T1"]\n'
            'head = subprocess.check_output(["git", "rev-parse", "HEAD"], '
            'text=True).strip()\n'
            'assert row["observed_head"] == head\n'
            'assert row["observed_paths"] == ["a.txt"]\n'
            'assert row["integration_sha"] is None\n'
            'raise SystemExit(17)', self.journal)
        plan = self.plan([self.task('T1', 'a.txt', verify=verify)])
        first = self.run_batch(plan)
        self.assertEqual(first.returncode, 1, first.stderr + first.stdout)
        original = self.task_record()
        self.assertEqual(original['status'], 'FAILED')
        self.assertEqual(original['verify'][0]['exit'], 17)
        self.assertEqual(original['observed_head'], self.head(work))
        self.assertEqual(original['observed_paths'], ['a.txt'])
        self.assertIsNone(original['integration_sha'])
        self.assertEqual(self.head(batch), self.head(self.repo))

        for _ in range(2):
            resumed = self.run_batch(plan, None, '--resume')
            self.assertEqual(resumed.returncode, 1, resumed.stderr)
            self.assertEqual(self.task_record(), original)
            self.assertEqual(self.count_merges(batch, 'HEAD'), 0)
        self.assertEqual(len(self.opencode_calls()), 1)

    def test_retries_keep_the_original_base_after_an_independent_integration(self):
        verify = self.verification(
            'import os, sys; sys.exit(17 if os.getenv("FAIL_T1") else 0)')
        plan = self.plan([self.task('T1', 'a.txt', verify=verify),
                          self.task('T2', 'b.txt')])
        batch = self.root / 'wt-batch'
        base = self.head(self.repo)
        first = self.run_batch(plan, {'FAIL_T1': '1'})
        self.assertEqual(first.returncode, 1, first.stderr + first.stdout)
        original = self.task_record()
        candidate = original['observed_head']
        self.assertEqual(original['status'], 'FAILED')
        self.assertIsNotNone(candidate)
        t2 = self.journal_data()['tasks']['T2']
        self.assertEqual(t2['status'], 'INTEGRATED')
        self.assertEqual(self.head(batch), t2['integration_sha'])
        self.assertNotEqual(self.head(batch), base)

        for count in range(2, 4):
            failed = self.run_batch(plan, {'FAKE_OPENCODE_FAIL': '1'},
                                    '--retry', 'T1')
            self.assertEqual(failed.returncode, 1, failed.stderr + failed.stdout)
            record = self.task_record()
            self.assertEqual(record['status'], 'FAILED')
            self.assertEqual(record['attempts'], count)
            self.assertEqual(record['observed_head'], candidate)
            self.assertEqual(record['observed_paths'], ['a.txt'])
            self.assertIsNone(record['integration_sha'])
            self.assertEqual(record['base'], base)
            self.assertEqual(record['attempt_history'][0],
                             original['attempt_history'][0])
            self.assertNotIn('observation', record['attempt_history'][-1])
            self.assertEqual(self.head(batch), t2['integration_sha'])

        prompt = self.root / 'retry-prompt.txt'
        retried = self.run_batch(plan, {'FAKE_PROMPT_FILE': str(prompt),
                                       'FAKE_EXECUTOR_CONTENT': 'updated'},
                                 '--retry', 'T1')
        self.assertEqual(retried.returncode, 0, retried.stderr + retried.stdout)
        record = self.task_record()
        self.assertIn(f'BASE_SHA={base}\n', prompt.read_text())
        self.assertEqual(record['base'], base)
        self.assertEqual([entry['base'] for entry in record['attempt_history']],
                         [base] * 4)
        self.assertEqual(record['attempt_history'][0],
                         original['attempt_history'][0])
        self.assertEqual(record['observed_paths'], ['a.txt'])
        self.assertNotEqual(record['observed_head'], candidate)
        self.assertEqual(record['observed_head'], self.head(self.root / 'wt-t1'))
        self.assertEqual(record['integration_sha'], self.head(batch))
        self.assertEqual(self.head(batch, 'HEAD^1'), t2['integration_sha'])
        self.assertEqual(self.head(batch, 'HEAD^2'), record['observed_head'])
        self.assertEqual(self.count_merges(batch, 'HEAD'), 2)
        self.assertEqual((batch / 'a.txt').read_text().strip(), 'updated')
        self.assertEqual((batch / 'b.txt').read_text().strip(), 'T2')

        calls = self.opencode_calls()
        for _ in range(2):
            resumed = self.run_batch(plan, None, '--resume')
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(self.task_record(), record)
            self.assertEqual(self.count_merges(batch, 'HEAD'), 2)
        self.assertEqual(self.opencode_calls(), calls)

    def test_failed_git_merge_keeps_the_verified_observation_unintegrated(self):
        batch = self.root / 'wt-batch'
        verify = self.verification(
            'import pathlib, subprocess, sys\n'
            'name = subprocess.check_output(["git", "-C", sys.argv[1], '
            '"rev-parse", "--path-format=absolute", "--git-path", "index.lock"], '
            'text=True).strip()\n'
            'pathlib.Path(name).write_text("held index")', batch)
        plan = self.plan([self.task('T1', 'a.txt', verify=verify)])
        first = self.run_batch(plan)
        self.assertEqual(first.returncode, 1, first.stderr + first.stdout)
        record = self.task_record()
        self.assertIn('Git could not integrate T1', record['error'])
        self.assertIn('index.lock', record['error'])
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertEqual(record['verify'][0]['exit'], 0)
        self.assertEqual(record['observed_head'], self.head(self.root / 'wt-t1'))
        self.assertEqual(record['observed_paths'], ['a.txt'])
        self.assertIsNone(record['integration_sha'])
        self.assertEqual(self.head(batch), self.head(self.repo))
        self.assertEqual(self.journal_data()['phase'], 'tasks')
        self.assertNotIn('git merge --abort', record['error'])
        resumed = self.run_batch(plan, None, '--resume')
        self.assertEqual(resumed.returncode, 1, resumed.stderr)
        self.assertEqual(self.task_record(), record)
        self.assertEqual(self.count_merges(batch, 'HEAD'), 0)
        self.assertEqual(len(self.opencode_calls()), 1)

    def test_conflicting_merge_with_failed_abort_blocks_next_task_and_resume(self):
        verify = self.verification(
            'import os, sys; sys.exit(17 if os.getenv("FAIL_T1") else 0)')
        plan = self.plan([self.task('T1', 'a.txt', verify=verify),
                          self.task('T2', 'b.txt')])
        first = self.run_batch(plan, {'FAIL_T1': '1'}, '--only', 'T1')
        self.assertEqual(first.returncode, 1, first.stderr + first.stdout)
        before = self.task_record()
        self.assertEqual(before['status'], 'FAILED')
        candidate = before['observed_head']
        batch = self.root / 'wt-batch'
        # Seed a known, divergent batch base. The retry still uses the
        # earlier task base and reaches Git's real add/add conflict.
        (batch / 'a.txt').write_text('batch change\n')
        _git(batch, 'add', 'a.txt')
        _git(batch, 'commit', '-q', '-m', 'conflicting batch base')
        batch_head = self.head(batch)
        state = self.journal_data()
        state['base_sha'] = batch_head
        self.journal.write_text(json.dumps(state))
        lock = Path(_git(batch, 'rev-parse', '--path-format=absolute',
                         '--git-path', 'index.lock').stdout.decode().strip())
        git = delivery._git
        aborts = []

        def hold_index_for_abort(where, *args, **kwargs):
            if args == ('merge', '--abort') and Path(where).resolve() == batch.resolve():
                lock.write_text('held fixture index')
                result = git(where, *args, **kwargs)
                aborts.append(result)
                return result
            return git(where, *args, **kwargs)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {**self.env, **self._task_env()}, clear=True), \
                mock.patch.object(delivery, '_git', side_effect=hold_index_for_abort), \
                redirect_stdout(out), redirect_stderr(err):
            code = delivery.main(['run', '--plan', str(self.write_plan(plan)),
                                  '--journal', str(self.journal), '--execute',
                                  '--retry', 'T1'])
        self.assertEqual(code, 1, err.getvalue() + out.getvalue())
        self.assertEqual(len(aborts), 1)
        self.assertNotEqual(aborts[0].returncode, 0)
        self.assertIn('index.lock', aborts[0].stderr)
        record = self.task_record()
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertEqual(record['verify'][0]['exit'], 0)
        self.assertEqual(record['observed_head'], candidate)
        self.assertEqual(record['observed_paths'], ['a.txt'])
        self.assertIsNone(record['integration_sha'])
        self.assertIn('integration conflict for T1: a.txt', record['error'])
        self.assertIn(f'git merge --abort exited {aborts[0].returncode}',
                      record['error'])
        self.assertIn('index.lock', record['error'])
        self.assertIn('MERGE_HEAD remains present', record['error'])
        self.assertIn('unmerged paths remain (a.txt)', record['error'])
        self.assertEqual(self.head(batch), batch_head)
        self.assertEqual(self.head(batch, 'MERGE_HEAD'), candidate)
        self.assertEqual(_git(batch, 'diff', '--name-only', '--diff-filter=U')
                         .stdout.decode().splitlines(), ['a.txt'])

        state = self.journal_data()
        self.assertEqual(state['phase'], 'blocked')
        self.assertEqual(state['error'], record['error'])
        next_task = state['tasks']['T2']
        self.assertEqual(next_task['status'], 'BLOCKED')
        self.assertEqual(next_task['attempts'], 0)
        self.assertIn('batch merge reconciliation', next_task['error'])
        self.assertEqual(len(self.opencode_calls()), 2)
        self.assertFalse((self.root / 'wt-t2').exists())

        lock.unlink()
        _git(batch, 'checkout', '--ours', '--', 'a.txt')
        _git(batch, 'add', 'a.txt')
        self.assertEqual(_git(batch, 'status', '--porcelain').stdout, b'')
        self.assertEqual(self.head(batch, 'MERGE_HEAD'), candidate)
        resumed = self.run_batch(plan, None, '--resume')
        self.assertEqual(resumed.returncode, 1, resumed.stderr)
        self.assertIn('unfinished Git merge (MERGE_HEAD)', resumed.stderr)
        self.assertEqual(self.task_record(), record)
        self.assertEqual(self.journal_data()['tasks']['T2'], next_task)
        self.assertEqual(len(self.opencode_calls()), 2)

    def test_git_merge_without_its_journal_save_blocks_repeated_recovery(self):
        batch = self.root / 'wt-batch'
        snapshot = self.root / 'before-integration.json'
        verify = self.verification(
            'import pathlib, sys\n'
            'pathlib.Path(sys.argv[2]).write_bytes('
            'pathlib.Path(sys.argv[1]).read_bytes())', self.journal, snapshot)
        plan = self.plan([self.task('T1', 'a.txt', verify=verify)])
        first = self.run_batch(plan)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        actual_merge = self.head(batch)
        self.journal.write_bytes(snapshot.read_bytes())
        prior = self.task_record()
        self.assertEqual(self.head(batch, 'HEAD^2'), prior['observed_head'])
        self.assertIsNone(prior['integration_sha'])

        for _ in range(2):
            resumed = self.run_batch(plan, None, '--resume')
            self.assertEqual(resumed.returncode, 1, resumed.stderr)
            self.assertIn('does not match the recorded successful integrations',
                          resumed.stderr)
            record = self.task_record()
            self.assertEqual(record['status'], 'BLOCKED')
            self.assertEqual(record['observed_head'], prior['observed_head'])
            self.assertEqual(record['observed_paths'], prior['observed_paths'])
            self.assertIsNone(record['integration_sha'])
            self.assertEqual(self.head(batch), actual_merge)
        self.assertEqual(self.count_merges(batch, 'HEAD'), 1)
        self.assertEqual(len(self.opencode_calls()), 1)

    def test_failed_verification_cannot_prove_a_real_merge_commit(self):
        verify = self.verification('raise SystemExit(17)')
        plan = self.plan([self.task('T1', 'a.txt', verify=verify)])
        self.assertEqual(self.run_batch(plan).returncode, 1)
        state = self.journal_data()
        record = state['tasks']['T1']
        batch = self.root / 'wt-batch'
        _git(batch, 'merge', '--no-ff', record['observed_head'], '-m',
             'test unverified merge')
        record.update(status='INTEGRATED', integration_sha=self.head(batch))
        # An "ok" summary cannot override the real failing verification.
        record['attempt_history'][0]['result'] = 'ok'
        self.journal.write_text(json.dumps(state))
        resumed = self.run_batch(plan, None, '--resume')
        self.assertEqual(resumed.returncode, 1, resumed.stderr)
        self.assertIn('no successful verification', resumed.stderr)
        self.assertEqual(self.task_record(), record)
        self.assertEqual(self.count_merges(batch, 'HEAD'), 1)
        self.assertEqual(len(self.opencode_calls()), 1)

    def test_retry_rejects_branch_movement_or_unproven_legacy_observation(self):
        verify = self.verification('raise SystemExit(17)')
        plan = self.plan([self.task('T1', 'a.txt', verify=verify)])
        self.assertEqual(self.run_batch(plan).returncode, 1)
        initial = self.journal.read_bytes()
        observed = self.task_record()['observed_head']
        work = self.root / 'wt-t1'
        for case in ('branch moved', 'legacy missing observation',
                     'legacy lost attempt bases'):
            with self.subTest(case=case):
                self.journal.write_bytes(initial)
                state = self.journal_data()
                record = state['tasks']['T1']
                if case == 'branch moved':
                    _git(work, 'reset', '--hard', record['base'])
                    reason = 'branch head does not match'
                else:
                    _git(work, 'reset', '--hard', observed)
                    record.pop('observed_head')
                    record.pop('observed_paths')
                    attempt = record['attempt_history'][0]
                    attempt.pop('base')
                    attempt.pop('observation')
                    if case == 'legacy missing observation':
                        record.update(head=None, changed_paths=[])
                        reason = 'branch head does not match'
                    else:
                        second = dict(attempt, index=2, id='attempt-legacy-second')
                        record['attempt_history'].append(second)
                        record['attempts'] = 2
                        reason = 'inconsistent attempt base'
                self.journal.write_text(json.dumps(state))
                read = self.cli('status', '--journal', str(self.journal), '--json')
                self.assertEqual(read.returncode, 0, read.stderr)
                blocked = self.run_batch(plan, None, '--retry', 'T1')
                self.assertEqual(blocked.returncode, 1, blocked.stderr)
                self.assertIn(reason, blocked.stderr)
                after = self.task_record()
                for key in ('head', 'observed_head', 'observed_paths', 'base',
                            'attempt_history'):
                    self.assertEqual(after.get(key), record.get(key))
                self.assertEqual(after['status'], 'FAILED')
                self.assertIsNone(after['integration_sha'])
        self.assertEqual(len(self.opencode_calls()), 1)

    def test_retry_reuses_a_consistent_single_attempt_legacy_record(self):
        verify = self.verification(
            'import os, sys; sys.exit(17 if os.getenv("FAIL_T1") else 0)')
        plan = self.plan([self.task('T1', 'a.txt', verify=verify)])
        self.assertEqual(self.run_batch(plan, {'FAIL_T1': '1'}).returncode, 1)
        state = self.journal_data()
        record = state['tasks']['T1']
        record.pop('observed_head')
        record.pop('observed_paths')
        legacy = record['attempt_history'][0]
        legacy.pop('base')
        legacy.pop('observation')
        self.journal.write_text(json.dumps(state))
        retried = self.run_batch(plan, None, '--retry', 'T1')
        self.assertEqual(retried.returncode, 0, retried.stderr + retried.stdout)
        accepted = self.task_record()
        self.assertEqual(accepted['attempt_history'][0], legacy)
        self.assertEqual(accepted['attempt_history'][1]['base'], record['base'])
        self.assertEqual(accepted['observed_head'], record['head'])
        self.assertEqual(accepted['observed_paths'], record['changed_paths'])
        resumed = self.run_batch(plan, None, '--resume')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.count_merges(self.root / 'wt-batch', 'HEAD'), 1)

    def test_unobserved_branch_at_new_batch_head_is_not_a_retry_base(self):
        plan = self.plan([self.task('T1', 'a.txt'), self.task('T2', 'b.txt')])
        first = self.run_batch(plan, {'FAKE_OPENCODE_FAIL': '1'}, '--only', 'T1')
        self.assertEqual(first.returncode, 1, first.stderr + first.stdout)
        self.assertIsNone(self.task_record()['observed_head'])
        self.assertEqual(self.run_batch(plan, None, '--only', 'T2').returncode, 1)
        batch = self.root / 'wt-batch'
        _git(self.root / 'wt-t1', 'reset', '--hard', self.head(batch))
        calls = self.opencode_calls()
        retried = self.run_batch(plan, None, '--retry', 'T1')
        self.assertEqual(retried.returncode, 1, retried.stderr)
        self.assertIn('branch head does not match', retried.stderr)
        self.assertEqual(self.task_record()['base'], self.head(self.repo))
        self.assertEqual(self.opencode_calls(), calls)
        self.assertEqual(self.count_merges(batch, 'HEAD'), 1)

    def test_recovery_blocks_a_stale_approval_even_with_the_same_diff(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        reviewed = self.finalize(plan, None, '--review')
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        approved = self.journal_data()['review']
        batch = self.root / 'wt-batch'
        _git(batch, 'commit', '--allow-empty', '-m', 'change reviewed head')
        calls = self.opencode_calls()
        resumed = self.run_batch(plan, None, '--resume')
        self.assertEqual(resumed.returncode, 1, resumed.stderr)
        self.assertIn('the approval no longer applies', resumed.stderr)
        self.assertEqual(self.journal_data()['review'], approved)
        self.assertEqual(self.opencode_calls(), calls)
        self.assertEqual(self.count_merges(batch, 'HEAD'), 1)

    def test_recovery_rejects_a_missing_task_record(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        state = self.journal_data()
        missing = state['tasks'].pop('T1')
        self.journal.write_text(json.dumps(state))
        resumed = self.run_batch(plan, None, '--resume')
        self.assertEqual(resumed.returncode, 2, resumed.stderr)
        self.assertIn('missing or invalid task records', resumed.stderr)
        self.assertEqual(self.head(self.root / 'wt-batch'),
                         missing['integration_sha'])
        self.assertEqual(len(self.opencode_calls()), 1)

    def test_task_branch_move_during_verification_cannot_be_integrated(self):
        verify = self.verification(
            'import subprocess\n'
            'subprocess.run(["git", "switch", "-c", "other-task-branch"], check=True)')
        plan = self.plan([self.task('T1', 'a.txt', verify=verify)])
        failed = self.run_batch(plan)
        self.assertEqual(failed.returncode, 1, failed.stderr + failed.stdout)
        record = self.task_record()
        self.assertIn('changed the task branch', record['error'])
        self.assertEqual(record['observed_head'], self.head(self.root / 'wt-t1'))
        self.assertEqual(record['observed_paths'], ['a.txt'])
        self.assertIsNone(record['integration_sha'])
        self.assertEqual(self.count_merges(self.root / 'wt-batch', 'HEAD'), 0)

    def test_finalize_refuses_clean_worktree_with_unfinished_merge(self):
        called = self.root / 'batch-verify-called'
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['finalize']['verify'] = self.verification(
            'import pathlib, sys; pathlib.Path(sys.argv[1]).touch()', called)
        self.assertEqual(self.run_batch(plan).returncode, 0)
        batch = self.root / 'wt-batch'
        head = self.head(batch)
        target = self.empty_batch_merge_target()
        _git(batch, 'merge', '--no-ff', '--no-commit', target)
        self.assertEqual(_git(batch, 'status', '--porcelain').stdout, b'')
        self.assertEqual(self.head(batch, 'MERGE_HEAD'), target)
        calls = self.gh_calls()

        done = self.finalize(plan, None, '--review', '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('unfinished Git merge (MERGE_HEAD)', done.stderr)
        self.assertFalse(called.exists())
        self.assertEqual(self.gh_calls(), calls)
        self.assertEqual(self.head(batch), head)
        self.assertEqual(self.head(batch, 'MERGE_HEAD'), target)
        self.assertEqual(self.journal_data()['phase'], 'blocked')

    def test_finalize_stops_after_a_verifier_leaves_clean_pending_merge(self):
        target_file = self.root / 'merge-target'
        other_verify = self.root / 'other-verify-called'
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['finalize']['verify'] = self.verification(
            'import pathlib, subprocess, sys\n'
            'target = pathlib.Path(sys.argv[1]).read_text()\n'
            'subprocess.run(["git", "merge", "--no-ff", "--no-commit", target], '
            'check=True)', target_file) + self.verification(
                'import pathlib, sys; pathlib.Path(sys.argv[1]).touch()', other_verify)
        self.assertEqual(self.run_batch(plan).returncode, 0)
        batch = self.root / 'wt-batch'
        head = self.head(batch)
        target = self.empty_batch_merge_target()
        target_file.write_text(target)

        done = self.finalize(plan, None, '--review', '--push', '--merge',
                             '--authorization', AUTH, '--checks-timeout', '5')
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('unfinished Git merge (MERGE_HEAD)', done.stderr)
        self.assertEqual(_git(batch, 'status', '--porcelain').stdout, b'')
        self.assertEqual(self.head(batch), head)
        self.assertEqual(self.head(batch, 'MERGE_HEAD'), target)
        self.assertFalse(other_verify.exists())
        self.assertEqual(self.session_files('rev'), [])
        self.assertFalse(any('pr create' in call for call in self.gh_calls()))
        state = self.journal_data()
        self.assertEqual(state['phase'], 'blocked')
        self.assertEqual([entry['exit'] for entry in state['batch_verify']], [0])


class ExecutorLifecycleTests(BatchCase):
    """S1 rows of the Issue #265 state matrix: attempt history and stop proof.

    Rows M01-M20 are covered by name; see the matrix comment on Issue #265 for
    the state x guard x side-effect table these tests execute.
    """

    def _attempt(self, *, index=1, ident=None, state='ENDED', result='failed',
                 trigger='fresh', executor=None, verifiers=None):
        if executor is None:
            executor = {'state': 'ended', 'pid': 424242, 'pgid': None,
                        'exit': 0, 'stopped': True, 'session': 'ses_x'}
        return {
            'id': ident or 'attempt-' + f'{index:032x}',
            'index': index, 'task': 'T1', 'trigger': trigger, 'state': state,
            'started_at': '2026-11-20T00:00:00+00:00',
            'ended_at': ('2026-11-20T00:00:01+00:00'
                         if state == 'ENDED' else None),
            'result': result if state == 'ENDED' else None, 'error': None,
            'executor': executor,
            'verifiers': [] if verifiers is None else verifiers,
        }

    def _record(self, history, attempts=None):
        return {'status': 'FAILED',
                'attempts': len(history) if attempts is None else attempts,
                'attempt_history': history}

    def _problems(self, record, states=None):
        def probe(pid, pgid, token=None, grace=0.0):
            return (states or {}).get(pid, 'stopped')
        with mock.patch.object(delivery, '_proven_process_state', probe):
            return delivery._attempt_problems(record)

    def _fail_t1(self, plan, extra=None):
        return self.run_batch(plan, dict({'FAIL_T1': '1'}, **(extra or {})))

    # -- M01 / M02 / AC-1: the attempt exists before the executor starts ----
    def test_m01_m02_fresh_run_records_the_first_attempt(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        run = self.run_batch(
            plan, {'FAKE_OPENCODE_OBSERVE_JOURNAL': str(self.journal)})
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['attempts'], 1)
        attempt = record['attempt_history'][0]
        self.assertEqual(attempt['state'], 'ENDED')
        self.assertEqual(attempt['result'], 'ok')
        self.assertEqual(attempt['trigger'], 'fresh')
        self.assertTrue(attempt['id'].startswith('attempt-'))
        self.assertIsInstance(attempt['executor']['pid'], int)
        if os.name == 'posix':
            self.assertEqual(attempt['executor']['pgid'],
                             attempt['executor']['pid'])
        self.assertIs(attempt['executor']['stopped'], True)
        if os.name == 'nt':
            # AC-1: the Job id sits in the attempt record with the pid/token
            # and the recorded proof is a real Job query result.
            job = attempt['executor']['job']
            self.assertEqual(job['kind'], 'windows-job-object')
            self.assertTrue(job['id'].startswith('Local\\'))
            self.assertEqual(job['state'], 'ended')
            self.assertIs(job['stopped'], True)
            self.assertEqual(job['active'], 0)
            self.assertIsInstance(job['closed_at'], str)
        # The fake executor read the journal at spawn time: the attempt record
        # (and its unique id) was already on disk, before the worktree was used.
        seen = json.loads(
            Path(str(self.journal) + '.attempt').read_text(encoding='utf-8'))
        self.assertEqual(seen['attempts'], 1)
        self.assertEqual(seen['history'][0]['state'], 'STARTED')
        self.assertTrue(seen['history'][0]['id'].startswith('attempt-'))
        self.assertEqual(seen['history'][0]['verifiers'], [])
        if os.name == 'nt':
            # AC-1: at spawn time the journal already binds the attempt to a
            # live Job Object id, pid and token (state 'assigned').
            observed = seen['history'][0]['executor']
            self.assertEqual(observed['job']['kind'], 'windows-job-object')
            self.assertEqual(observed['job']['state'], 'assigned')
            self.assertTrue(observed['job']['id'].startswith('Local\\'))
            self.assertIsInstance(observed['pid'], int)
            self.assertIsInstance(observed['token'], int)

    def test_m02_fresh_run_reuses_a_clean_worktree(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        work = self.root / 'wt-t1'
        _git(self.repo, 'worktree', 'add', '-q', '-b', 'task/batch-t1',
             str(work), 'main')
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertEqual(len(self.opencode_calls()), 1)
        self.assertEqual(self.journal_data()['tasks']['T1']['attempts'], 1)
        self.assertEqual(self.journal_data()['tasks']['T1']['status'],
                         'INTEGRATED')

    # -- M03: missing history with recorded attempts ------------------------
    def test_m03_missing_history_with_attempts_fails_closed(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        state = self.journal_data()
        record = state['tasks']['T1']
        record.update(status='FAILED', integration_sha=None, attempts=1)
        record.pop('attempt_history')
        self.journal.write_text(json.dumps(state))
        before = len(self.opencode_calls())
        retry = self.cli('run', '--plan', str(self.write_plan(plan)),
                         '--journal', str(self.journal), '--execute',
                         '--retry', 'T1')
        self.assertEqual(retry.returncode, 1, retry.stderr)
        self.assertIn('attempt_history', retry.stderr)
        self.assertEqual(len(self.opencode_calls()), before)
        record = self.journal_data()['tasks']['T1']
        self.assertNotIn('attempt_history', record)
        self.assertEqual(record['attempts'], 1)
        self.assertIn('retry_blocked', record)

    # -- M04 / M19: integrated tasks keep their history --------------------
    def test_m04_m19_integrated_task_retry_is_refused_without_new_attempt(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        before = len(self.opencode_calls())
        retry = self.cli('run', '--plan', str(self.write_plan(plan)),
                         '--journal', str(self.journal), '--execute',
                         '--retry', 'T1')
        self.assertEqual(retry.returncode, 2, retry.stderr)
        self.assertIn('only accepts FAILED', retry.stderr)
        self.assertEqual(len(self.opencode_calls()), before)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['attempts'], 1)
        self.assertEqual(len(record['attempt_history']), 1)

    # -- M05: retry appends, never overwrites ------------------------------
    def test_m05_retry_appends_a_new_attempt_and_keeps_the_old_one(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([self.task('T1', 'a.txt', verify=[[PY, str(script)]])])
        first = self._fail_t1(plan)
        self.assertEqual(first.returncode, 1)
        history = self.journal_data()['tasks']['T1']['attempt_history']
        self.assertEqual(len(history), 1)
        snapshot = json.dumps(history[0], sort_keys=True)
        old_id = history[0]['id']
        second = self.cli('run', '--plan', str(self.write_plan(plan)),
                          '--journal', str(self.journal), '--execute',
                          '--retry', 'T1')
        record = self.journal_data()['tasks']['T1']
        # HD-2 B (Issue #267): a recorded Job Object makes Windows retry
        # behave exactly like POSIX instead of blocking on a decision-A halt.
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(record['attempts'], 2)
        history = record['attempt_history']
        self.assertEqual(len(history), 2)
        self.assertEqual([entry['trigger'] for entry in history],
                         ['fresh', 'retry'])
        self.assertNotEqual(history[0]['id'], history[1]['id'])
        self.assertEqual(history[0]['id'], old_id)
        self.assertEqual(json.dumps(history[0], sort_keys=True), snapshot)

    def test_attempt_history_append_is_additive(self):
        record = delivery._task_record(
            {'id': 'T1', 'branch': 'task/append',
             'worktree': str(self.root / 'wt-append')}, 'builder')
        saves = []
        first = delivery._start_attempt(record, {'id': 'T1'}, 'fresh',
                                        lambda: saves.append('first'))
        snapshot = json.dumps(first, sort_keys=True)
        second = delivery._start_attempt(record, {'id': 'T1'}, 'retry',
                                         lambda: saves.append('second'))
        self.assertEqual(record['attempts'], 2)
        self.assertEqual(len(record['attempt_history']), 2)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(json.dumps(record['attempt_history'][0],
                                    sort_keys=True), snapshot)
        self.assertEqual(saves, ['first', 'second'])

    # -- M20: every verifier has its own pid/pgid record -------------------
    def test_m20_each_verifier_is_recorded_with_its_own_pid(self):
        plan = self.plan([self.task(
            'T1', 'a.txt', verify=[[PY, '-c', 'pass'], [PY, '-c', 'pass']])])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        verifiers = self.journal_data()['tasks']['T1']['attempt_history'][0][
            'verifiers']
        self.assertEqual(len(verifiers), 2)
        for entry in verifiers:
            self.assertEqual(entry['state'], 'ended')
            self.assertIsInstance(entry['pid'], int)
            self.assertEqual(entry['exit'], 0)
            self.assertIs(entry['stopped'], True)
            if os.name == 'posix':
                self.assertEqual(entry['pgid'], entry['pid'])
        if os.name == 'nt':
            # Every verifier is bound to its own Job Object (AC-1).
            for entry in verifiers:
                self.assertEqual(entry['job']['kind'], 'windows-job-object')
                self.assertIs(entry['job']['stopped'], True)
            self.assertNotEqual(verifiers[0]['job']['id'],
                                verifiers[1]['job']['id'])

    # -- M06: --resume is not a retry --------------------------------------
    def test_m06_resume_does_not_reopen_a_finished_failure(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([self.task('T1', 'a.txt', verify=[[PY, str(script)]])])
        self.assertEqual(self._fail_t1(plan).returncode, 1)
        before = len(self.opencode_calls())
        resumed = self.cli('run', '--plan', str(self.write_plan(plan)),
                           '--journal', str(self.journal), '--execute',
                           '--resume')
        self.assertEqual(resumed.returncode, 1)
        self.assertEqual(len(self.opencode_calls()), before)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'FAILED')
        self.assertEqual(record['attempts'], 1)

    # -- M07: a dirty worktree is rejected before any new process ----------
    def test_m07_dirty_worktree_blocks_retry_before_spawn(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([self.task('T1', 'a.txt', verify=[[PY, str(script)]])])
        self.assertEqual(self._fail_t1(plan).returncode, 1)
        (self.root / 'wt-t1' / 'dirty.txt').write_text('x')
        before = len(self.opencode_calls())
        retry = self.cli('run', '--plan', str(self.write_plan(plan)),
                         '--journal', str(self.journal), '--execute',
                         '--retry', 'T1')
        self.assertEqual(retry.returncode, 1, retry.stderr)
        self.assertEqual(len(self.opencode_calls()), before)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'FAILED')
        # HD-2 B (Issue #267): the Job proof lets the guard pass, so the dirty
        # worktree is the failure on every platform; no new process spawned.
        self.assertIn('not clean', record['error'])
        self.assertEqual(record['attempts'], 2)
        self.assertEqual(record['attempt_history'][1]['result'], 'failed')
        self.assertEqual(len(record['attempt_history'][0]['verifiers']), 1)

    # -- M08 / M09: timeout attempts encode the stop proof -----------------
    def test_m08_m09_timeout_attempts_encode_the_stop_proof(self):
        proven = self._attempt(result='timeout', executor={
            'state': 'ended', 'pid': 111, 'pgid': 111, 'exit': None,
            'stopped': True, 'session': None})
        self.assertEqual(self._problems(self._record([proven])), [])
        for result, stopped in (('timeout', False), ('unproven_stop', False),
                                ('unproven_stop', None)):
            with self.subTest(result=result, stopped=stopped):
                unproven = self._attempt(result=result, executor={
                    'state': 'ended', 'pid': 112, 'pgid': 112, 'exit': None,
                    'stopped': stopped, 'session': None})
                problems = self._problems(self._record([unproven]),
                                          {112: 'stopped'})
                self.assertTrue(any('not proven stopped' in problem
                                    for problem in problems), problems)

    def test_m09_unproven_stop_blocks_retry_and_resume_without_spawn(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([self.task('T1', 'a.txt', verify=[[PY, str(script)]])])
        self.assertEqual(self._fail_t1(plan).returncode, 1)
        state = self.journal_data()
        attempt = state['tasks']['T1']['attempt_history'][0]
        attempt['result'] = 'unproven_stop'
        attempt['executor']['stopped'] = False
        self.journal.write_text(json.dumps(state))
        before = len(self.opencode_calls())
        retry = self.cli('run', '--plan', str(self.write_plan(plan)),
                         '--journal', str(self.journal), '--execute',
                         '--retry', 'T1')
        self.assertEqual(retry.returncode, 1, retry.stderr)
        self.assertEqual(len(self.opencode_calls()), before)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'FAILED')
        self.assertEqual(record['attempts'], 1)
        self.assertIn('retry_blocked', record)
        state = self.journal_data()
        state['tasks']['T1'].update(status='RUNNING', head=None, error=None)
        self.journal.write_text(json.dumps(state))
        resumed = self.cli('run', '--plan', str(self.write_plan(plan)),
                           '--journal', str(self.journal), '--execute',
                           '--resume')
        self.assertEqual(resumed.returncode, 1, resumed.stderr)
        self.assertEqual(len(self.opencode_calls()), before)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertEqual(record['attempts'], 1)

    # -- M10: a failed stream still records a proven, ended executor ------
    def test_failed_stream_records_an_ended_executor(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        run = self.run_batch(plan, {'FAKE_OPENCODE_FAIL': '1'})
        self.assertEqual(run.returncode, 1)
        attempt = self.journal_data()['tasks']['T1']['attempt_history'][0]
        self.assertEqual(attempt['result'], 'failed')
        self.assertEqual(attempt['executor']['state'], 'ended')
        self.assertIs(attempt['executor']['stopped'], True)
        if os.name == 'nt':
            self.assertIsInstance(attempt['executor']['token'], int)
        else:
            self.assertEqual(attempt['executor']['pgid'],
                             attempt['executor']['pid'])

    # -- M10: resume closes the interrupted attempt, then appends ----------
    def test_m10_resume_closes_the_interrupted_attempt_and_appends_one(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        first = self.run_batch(plan, {'FAKE_OPENCODE_FAIL': '1'})
        self.assertEqual(first.returncode, 1)
        state = self.journal_data()
        record = state['tasks']['T1']
        self.assertIsNone(record['head'])
        attempt = record['attempt_history'][0]
        attempt.update(state='STARTED', ended_at=None, result=None)
        record['status'] = 'RUNNING'
        record['error'] = None
        self.journal.write_text(json.dumps(state))
        resumed = self.cli('run', '--plan', str(self.write_plan(plan)),
                           '--journal', str(self.journal), '--execute',
                           '--resume')
        record = self.journal_data()['tasks']['T1']
        # HD-2 B (Issue #267): the interrupted attempt was bound to a
        # kill-on-close Job, so the vanished name proves the tree stopped.
        self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
        self.assertEqual(record['status'], 'INTEGRATED')
        self.assertEqual(record['attempts'], 2)
        history = record['attempt_history']
        self.assertEqual(history[0]['result'], 'interrupted')
        self.assertEqual(history[1]['result'], 'ok')
        self.assertNotEqual(history[0]['id'], history[1]['id'])

    # -- M11 / M12 / M13: live or unknown processes fail closed ------------
    def test_m11_m12_m13_live_or_unknown_process_blocks_reuse(self):
        stopped = {'state': 'ended', 'pid': 100, 'pgid': 100, 'exit': 0,
                   'stopped': True, 'session': None}
        cases = {
            'M11 executor running': (
                [self._attempt(executor={
                    'state': 'ended', 'pid': 101, 'pgid': 101, 'exit': 0,
                    'stopped': None, 'session': None})],
                {101: 'running'}, 'still running'),
            'M11 child exited but group lives': (
                [self._attempt(executor={
                    'state': 'ended', 'pid': 104, 'pgid': 104, 'exit': 0,
                    'stopped': False, 'session': None})],
                {104: 'running'}, 'still running'),
            'M12 verifier running': (
                [self._attempt(executor=dict(stopped), verifiers=[{
                    'state': 'ended', 'pid': 102, 'pgid': 102, 'exit': 0,
                    'stopped': None}])],
                {102: 'running'}, 'still running'),
            'M13 liveness unknown': (
                [self._attempt(executor={
                    'state': 'ended', 'pid': 103, 'pgid': 103, 'exit': 0,
                    'stopped': None, 'session': None})],
                {103: 'unknown'}, 'cannot be proven'),
            'M13 recorded proof with token': (
                [self._attempt(executor={
                    'state': 'ended', 'pid': 105, 'pgid': 105, 'token': 42,
                    'exit': 0, 'stopped': True, 'session': None})],
                {105: 'unknown'},
                # Owner decision A: Windows never lets the recorded proof
                # override an unknown liveness; POSIX keeps the exemption.
                'cannot be proven' if os.name == 'nt' else None),
        }
        for label, (history, states, needle) in cases.items():
            with self.subTest(label):
                record = self._record(history)
                problems = self._problems(record, states)
                if needle is None:
                    self.assertEqual(problems, [])
                    continue
                self.assertTrue(any(needle in problem for problem in problems),
                                problems)

                def probe(pid, pgid, token=None, grace=0.0, states=states):
                    return states.get(pid, 'stopped')
                with mock.patch.object(delivery, '_proven_process_state',
                                       probe):
                    with self.assertRaises(delivery.BatchError) as caught:
                        delivery._reuse_guard({'id': 'T1'}, record)
                self.assertEqual(caught.exception.status, 'BLOCKED')
                self.assertTrue(caught.exception.halt)
                self.assertIn('not proven stopped', str(caught.exception))

    # -- Owner decision A: Windows never trusts an unknown after a proof ----
    def test_owner_a_windows_unknown_beats_the_recorded_proof(self):
        attempt = self._attempt(executor={
            'state': 'ended', 'pid': 301, 'pgid': 301, 'token': 42,
            'exit': 0, 'stopped': True, 'session': None})

        def probe(pid, pgid, token=None, grace=0.0):
            return 'unknown'

        with mock.patch.object(delivery, '_proven_process_state', probe):
            with mock.patch.object(delivery, '_IS_WINDOWS', True):
                windows = delivery._attempt_problems(self._record([attempt]))
            with mock.patch.object(delivery, '_IS_WINDOWS', False):
                posix = delivery._attempt_problems(self._record([attempt]))
        self.assertTrue(any('cannot be proven' in problem
                            for problem in windows), windows)
        self.assertEqual(posix, [])

    # -- M14: an unfinished attempt without a pid --------------------------
    def test_m14_unfinished_attempt_without_pid_fails_closed(self):
        silent = self._attempt(state='STARTED', result=None, executor={
            'state': 'pending', 'pid': None, 'pgid': None, 'exit': None,
            'stopped': None, 'session': None})
        problems = self._problems(self._record([silent]))
        self.assertTrue(any('no executor pid' in problem
                            for problem in problems), problems)
        started = self._attempt(state='STARTED', result=None, executor={
            'state': 'ended', 'pid': 201, 'pgid': 201, 'exit': 0,
            'stopped': True, 'session': None}, verifiers=[{
                'state': 'starting', 'pid': None, 'pgid': None, 'exit': None,
                'timeout': False, 'stopped': None}])
        problems = self._problems(self._record([started]), {201: 'stopped'})
        self.assertTrue(any('verifier 1 is starting without a recorded pid'
                            in problem for problem in problems), problems)

    # -- F2: verifier pid evidence is checked for every attempt state ------
    def test_f2_verifier_without_pid_fails_closed_in_ended_attempts(self):
        executor = {'state': 'ended', 'pid': 424242, 'pgid': 424242, 'exit': 0,
                    'stopped': True, 'session': None}
        cases = {
            'starting without pid': ({'state': 'starting', 'pid': None,
                                      'pgid': None, 'exit': None,
                                      'timeout': False, 'stopped': None},
                                     'verifier 1 is starting without a '
                                     'recorded pid'),
            'running without pid': ({'state': 'running', 'pid': None,
                                     'pgid': None, 'exit': None,
                                     'timeout': False, 'stopped': None},
                                    'verifier 1 is running without a '
                                    'recorded pid'),
            'ended without pid': ({'state': 'ended', 'pid': None,
                                   'pgid': None, 'exit': 0, 'timeout': False,
                                   'stopped': True},
                                  'verifier 1 is ended without a recorded pid'),
            'unrecorded without pid and unproven': (
                {'state': 'unrecorded', 'pid': None, 'pgid': None, 'exit': None,
                 'timeout': False, 'stopped': False},
                'verifier 1 is unrecorded without a recorded pid'),
            'unrecorded without pid but claimed stopped': (
                {'state': 'unrecorded', 'pid': None, 'pgid': None, 'exit': None,
                 'timeout': False, 'stopped': True},
                'verifier 1 is unrecorded without a recorded pid'),
        }
        for label, (entry, needle) in cases.items():
            with self.subTest(label):
                attempt = self._attempt(executor=dict(executor),
                                        verifiers=[dict(entry)])
                problems = self._problems(self._record([attempt]))
                self.assertTrue(any(needle in problem for problem in problems),
                                (label, problems))
        safe = self._attempt(executor=dict(executor), verifiers=[{
            'state': 'spawn_failed', 'pid': None, 'pgid': None, 'exit': None,
            'timeout': False, 'stopped': None}])
        self.assertEqual(self._problems(self._record([safe])), [])

    # -- R-F2 / R-F3: spawn window and stop proofs without identity --------
    def test_rf2_rf3_spawn_window_and_unidentified_stop_proofs(self):
        base = {'index': 1, 'task': 'T1', 'trigger': 'fresh',
                'started_at': '2026-11-20T00:00:00+00:00',
                'ended_at': '2026-11-20T00:00:01+00:00', 'result': 'failed',
                'error': None, 'verifiers': []}

        def attempt(state, executor):
            return dict(base, id='attempt-' + 'a' * 32, state=state,
                        executor=executor)

        spawning = attempt('STARTED', {
            'state': 'spawning', 'pid': None, 'pgid': None, 'token': None,
            'exit': None, 'stopped': None, 'session': None})
        problems = self._problems(self._record([spawning], attempts=1))
        self.assertTrue(any('executor is spawning without a recorded pid'
                            in problem for problem in problems), problems)
        unrecorded = attempt('ENDED', {
            'state': 'unrecorded', 'pid': None, 'pgid': None, 'token': None,
            'exit': None, 'stopped': True, 'session': None})
        problems = self._problems(self._record([unrecorded]))
        self.assertTrue(any('executor is unrecorded without a recorded pid'
                            in problem for problem in problems), problems)
        no_identity = attempt('ENDED', {
            'state': 'ended', 'pid': 301, 'pgid': None, 'token': None,
            'exit': 0, 'stopped': True, 'session': None})
        problems = self._problems(self._record([no_identity]),
                                  {301: 'stopped'})
        self.assertTrue(any('without an identity to check' in problem
                            for problem in problems), problems)
        identified = attempt('ENDED', {
            'state': 'ended', 'pid': 301, 'pgid': 301, 'token': None,
            'exit': 0, 'stopped': True, 'session': None})
        self.assertEqual(self._problems(self._record([identified]),
                                        {301: 'stopped'}), [])

    # -- R-F4: a missing attempt count is not "zero attempts" --------------
    def test_rf4_missing_attempt_count_is_not_zero(self):
        cases = {
            'both fields missing': ({'status': 'FAILED'}, 'cannot be proven'),
            'attempts is None': ({'status': 'FAILED', 'attempts': None,
                                  'attempt_history': []}, 'cannot be proven'),
            'attempts is a bool': ({'status': 'FAILED', 'attempts': True,
                                    'attempt_history': []}, 'cannot be proven'),
        }
        for label, (record, needle) in cases.items():
            with self.subTest(label):
                problems = self._problems(record)
                self.assertTrue(any(needle in problem for problem in problems),
                                (label, problems))
        evidence = {'status': 'FAILED', 'attempts': 0, 'attempt_history': [],
                    'head': 'a' * 40}
        problems = self._problems(evidence)
        self.assertTrue(any('attempts is 0 but the record has head' in problem
                            for problem in problems), problems)
        clean = {'status': 'PENDING', 'attempts': 0, 'attempt_history': []}
        self.assertEqual(self._problems(clean), [])

    # -- R-F8: an unhashable id must be reported, not crash ----------------
    def test_rf8_unhashable_attempt_id_is_reported(self):
        for ident in ([1, 2], {'x': 1}):
            with self.subTest(ident=ident):
                attempt = self._attempt()
                attempt['id'] = ident
                problems = self._problems(self._record([attempt]))
                self.assertTrue(any('no readable id' in problem
                                    for problem in problems), problems)

    # -- R-F2: an identity failure after spawn terminates the child ---------
    def test_rf2_identity_failure_kills_the_spawned_executor(self):
        argv = json.dumps([PY, '-c', 'import time; time.sleep(2)'])
        work = self.root / 'spawn-work'
        work.mkdir()
        with mock.patch.dict(os.environ, {'BATCH_OPENCODE': argv}):
            with mock.patch.object(delivery, '_process_token',
                                   side_effect=RuntimeError('token boom')):
                result = delivery._agent(work, 'prompt', None, 30)
        self.assertIn('token boom', result.get('record_failed', ''))
        self.assertIsInstance(result.get('pid'), int)
        self.assertEqual(
            delivery._prove_child_stopped(result['pid'], result.get('pgid'),
                                          None, grace=5.0),
            'stopped')

    # -- M15: unreadable or inconsistent history ---------------------------
    def test_m15_corrupt_history_fails_closed(self):
        good = self._attempt()
        duplicate = self._attempt(index=2, ident=good['id'])
        cases = {
            'not a list': ({'status': 'FAILED', 'attempts': 1,
                            'attempt_history': 'x'}, 'not a list'),
            'count mismatch': (self._record([good], attempts=2),
                               'does not match'),
            'duplicate id': (self._record([good, duplicate]), 'used twice'),
            'invalid pid': (self._record([self._attempt(executor={
                'state': 'ended', 'pid': 'x', 'pgid': None, 'exit': 0,
                'stopped': True, 'session': None})]), 'pid is invalid'),
            'unknown state': (self._record([dict(good, state='BOGUS')]),
                              'unknown state'),
            'unknown result': (self._record([dict(good, result='BOGUS')]),
                               'unknown result'),
        }
        for label, (record, needle) in cases.items():
            with self.subTest(label):
                problems = self._problems(record)
                self.assertTrue(any(needle in problem for problem in problems),
                                (label, problems))

    # -- M16a / M16b / F1: dead, reused or still-live pids -----------------
    def test_m16a_dead_pid_proof_is_platform_specific(self):
        child = subprocess.Popen([PY, '-c', 'pass'], start_new_session=True)
        child.wait()
        attempt = self._attempt(executor={
            'state': 'ended', 'pid': child.pid,
            'pgid': child.pid if os.name == 'posix' else None, 'token': 42,
            'exit': 0, 'stopped': True, 'session': None})
        problems = delivery._attempt_problems(self._record([attempt]))
        if os.name == 'nt':
            # A pre-#267 record has no Job Object proof, so a gone Windows root
            # stays unproven and reuse is refused (HD-2 B replaced decision A
            # for new records; old records are never silently upgraded). The
            # message depends on whether the pid was reused.
            self.assertTrue(problems)
        else:
            self.assertEqual(problems, [])

    def test_f1_reused_windows_pid_still_proves_the_descendants(self):
        with mock.patch.object(delivery, '_windows_creation_time',
                               return_value=('live', 999)):
            for parents in ({500: 400, 501: 500}, {500: 400, 502: 401},
                            {601: 600}, None):
                with self.subTest(parents=parents):
                    with mock.patch.object(delivery, '_windows_parent_map',
                                           return_value=parents):
                        # A reused pid's children belong to another process, and
                        # orphaned grandchildren are not attributable here.
                        self.assertEqual(
                            delivery._windows_process_state(500, token=42),
                            'unknown')
        with mock.patch.object(delivery, '_windows_creation_time',
                               return_value=('gone', None)):
            with mock.patch.object(delivery, '_windows_parent_map',
                                   return_value={500: 400, 501: 500}):
                self.assertEqual(
                    delivery._windows_process_state(500, token=42), 'running')
            with mock.patch.object(delivery, '_windows_parent_map',
                                   return_value={601: 600}):
                self.assertEqual(
                    delivery._windows_process_state(500, token=42), 'unknown')
        with mock.patch.object(delivery, '_windows_creation_time',
                               return_value=('live', 42)):
            self.assertEqual(
                delivery._windows_process_state(500, token=42), 'running')

    def test_f1_reused_child_creation_time_is_not_attributed(self):
        parents = {500: 400, 501: 500}
        with mock.patch.object(delivery, '_windows_creation_time',
                               return_value=('live', 1000)):
            self.assertFalse(
                delivery._windows_children_before(parents, 500, 999))
        with mock.patch.object(delivery, '_windows_creation_time',
                               return_value=('live', 500)):
            self.assertTrue(
                delivery._windows_children_before(parents, 500, 999))
        with mock.patch.object(delivery, '_windows_creation_time',
                               return_value=('unknown', None)):
            self.assertIsNone(
                delivery._windows_children_before(parents, 500, 999))

    def test_completion_ignores_children_created_after_the_exit(self):
        parents = {500: 400, 501: 500}

        def created_after(pid):
            return ('gone', None) if pid == 500 else ('live', 2000)

        with mock.patch.object(delivery, '_IS_WINDOWS', True), \
                mock.patch.object(delivery, '_windows_parent_map',
                                  return_value=parents), \
                mock.patch.object(delivery, '_windows_creation_time',
                                  side_effect=created_after):
            self.assertEqual(
                delivery._prove_child_stopped(500, None, 42, exit_ticks=1000,
                                              grace=0.05),
                'stopped')

        def created_before(pid):
            return ('gone', None) if pid == 500 else ('live', 500)

        with mock.patch.object(delivery, '_IS_WINDOWS', True), \
                mock.patch.object(delivery, '_windows_parent_map',
                                  return_value=parents), \
                mock.patch.object(delivery, '_windows_creation_time',
                                  side_effect=created_before):
            self.assertEqual(
                delivery._prove_child_stopped(500, None, 42, exit_ticks=1000,
                                              grace=0.05),
                'running')

    def test_m16b_live_recorded_group_blocks_reuse(self):
        attempt = self._attempt(executor={
            'state': 'ended', 'pid': os.getpid(), 'pgid': None, 'exit': 0,
            'stopped': None, 'session': None})
        problems = delivery._attempt_problems(self._record([attempt]))
        self.assertTrue(any('still running' in problem for problem in problems),
                        problems)

    # -- M17: retry recreates a removed worktree ---------------------------
    def test_m17_retry_recreates_a_missing_worktree(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([self.task('T1', 'a.txt', verify=[[PY, str(script)]])])
        self.assertEqual(self._fail_t1(plan).returncode, 1)
        _git(self.repo, 'worktree', 'remove', '--force',
             str(self.root / 'wt-t1'))
        self.assertFalse((self.root / 'wt-t1').exists())
        retry = self.cli('run', '--plan', str(self.write_plan(plan)),
                         '--journal', str(self.journal), '--execute',
                         '--retry', 'T1')
        record = self.journal_data()['tasks']['T1']
        # HD-2 B (Issue #267): Windows retry recreates the worktree like POSIX.
        self.assertEqual(retry.returncode, 0, retry.stderr + retry.stdout)
        self.assertTrue((self.root / 'wt-t1').is_dir())
        self.assertEqual(record['attempts'], 2)
        self.assertEqual(record['status'], 'INTEGRATED')

    # -- M18: the attempt must be persisted before the process starts ------
    def test_m18_persistence_failure_stops_before_spawn(self):
        journal = self.root / 'persist.json'
        record = delivery._task_record(
            {'id': 'T1', 'branch': 'task/persist',
             'worktree': str(self.root / 'wt-persist')}, 'builder')
        journal.write_text(json.dumps({'tasks': {'T1': record}}))
        before = journal.read_bytes()

        def broken_save():
            delivery._atomic_json(journal, {'tasks': {'T1': record}})

        with mock.patch.object(delivery, '_atomic_json',
                               side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                delivery._start_attempt(record, {'id': 'T1'}, 'fresh',
                                        broken_save)
        # The disk journal is untouched and _start_attempt never returned an
        # attempt, so the caller cannot reach _prepare_task_worktree/_agent.
        self.assertEqual(journal.read_bytes(), before)
        persisted = json.loads(journal.read_text(encoding='utf-8'))['tasks']['T1']
        self.assertEqual(persisted['attempts'], 0)
        self.assertEqual(persisted['attempt_history'], [])

    # -- AC-2 end to end: retry and resume check the recorded processes ----
    def test_retry_refuses_when_the_recorded_executor_is_alive(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([self.task('T1', 'a.txt', verify=[[PY, str(script)]])])
        self.assertEqual(self._fail_t1(plan).returncode, 1)
        state = self.journal_data()
        state['tasks']['T1']['attempt_history'][0]['executor'].update(
            pid=os.getpid(), pgid=None, token=None, stopped=None)
        self.journal.write_text(json.dumps(state))
        before = len(self.opencode_calls())
        retry = self.cli('run', '--plan', str(self.write_plan(plan)),
                         '--journal', str(self.journal), '--execute',
                         '--retry', 'T1')
        self.assertEqual(retry.returncode, 1, retry.stderr)
        self.assertIn('cannot reuse', retry.stderr)
        self.assertEqual(len(self.opencode_calls()), before)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['attempts'], 1)
        self.assertEqual(record['status'], 'FAILED')
        self.assertIn('retry_blocked', record)

    def test_resume_refuses_when_the_recorded_verifier_is_alive(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([self.task('T1', 'a.txt', verify=[[PY, str(script)]])])
        self.assertEqual(self._fail_t1(plan).returncode, 1)
        state = self.journal_data()
        record = state['tasks']['T1']
        record['attempt_history'][0]['verifiers'] = [{
            'state': 'ended', 'pid': os.getpid(), 'pgid': None, 'exit': 0,
            'stopped': None}]
        record.update(status='RUNNING', head=None, error=None)
        self.journal.write_text(json.dumps(state))
        before = len(self.opencode_calls())
        resumed = self.cli('run', '--plan', str(self.write_plan(plan)),
                           '--journal', str(self.journal), '--execute',
                           '--resume')
        self.assertEqual(resumed.returncode, 1, resumed.stderr)
        self.assertEqual(len(self.opencode_calls()), before)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertIn('cannot reuse', record['error'])
        self.assertEqual(record['attempts'], 1)

    def test_retry_refuses_when_the_recorded_verifier_is_alive(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([self.task('T1', 'a.txt', verify=[[PY, str(script)]])])
        self.assertEqual(self._fail_t1(plan).returncode, 1)
        state = self.journal_data()
        state['tasks']['T1']['attempt_history'][0]['verifiers'] = [{
            'state': 'ended', 'pid': os.getpid(), 'pgid': None, 'exit': 0,
            'stopped': None}]
        self.journal.write_text(json.dumps(state))
        before = len(self.opencode_calls())
        retry = self.cli('run', '--plan', str(self.write_plan(plan)),
                         '--journal', str(self.journal), '--execute',
                         '--retry', 'T1')
        self.assertEqual(retry.returncode, 1, retry.stderr)
        self.assertIn('cannot reuse', retry.stderr)
        self.assertEqual(len(self.opencode_calls()), before)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'FAILED')
        self.assertEqual(record['attempts'], 1)
        self.assertIn('retry_blocked', record)


    # -- #267 AC-4: a journal without Job records is never upgraded ----------
    def test_attempt_without_a_job_record_blocks_windows_retry(self):
        script = self.root / 'verify_t1.py'
        script.write_text(
            'import os, sys\n'
            'raise SystemExit(1 if os.environ.get("FAIL_T1") == "1" else 0)\n')
        plan = self.plan([self.task('T1', 'a.txt', verify=[[PY, str(script)]])])
        self.assertEqual(self._fail_t1(plan).returncode, 1)
        state = self.journal_data()
        for attempt in state['tasks']['T1']['attempt_history']:
            attempt['executor'].pop('job', None)
            for entry in attempt['verifiers']:
                entry.pop('job', None)
        self.journal.write_text(json.dumps(state))
        before = len(self.opencode_calls())
        retry = self.cli('run', '--plan', str(self.write_plan(plan)),
                         '--journal', str(self.journal), '--execute',
                         '--retry', 'T1')
        record = self.journal_data()['tasks']['T1']
        if os.name == 'nt':
            # A pre-#267 journal has no Job proof and is not silently upgraded.
            self.assertEqual(retry.returncode, 1, retry.stderr)
            self.assertEqual(len(self.opencode_calls()), before)
            self.assertEqual(record['attempts'], 1)
            self.assertIn('retry_blocked', record)
            self.assertIn('Job Object', record['retry_blocked'])
        else:
            self.assertEqual(retry.returncode, 0,
                             retry.stderr + retry.stdout)
            self.assertEqual(record['attempts'], 2)

    # -- #267: attempt state x Job state x operation matrix (mocked rows) ---
    def _job_problems(self, job, verdict, liveness='unknown'):
        executor = {'state': 'ended', 'pid': 500, 'pgid': None, 'token': 42,
                    'exit': 0, 'stopped': True, 'session': None, 'job': job}
        record = self._record([self._attempt(executor=executor)])
        with mock.patch.object(delivery, '_IS_WINDOWS', True), \
                mock.patch.object(delivery, '_proven_process_state',
                                  lambda *args, **kwargs: liveness), \
                mock.patch.object(delivery, '_job_active_by_name',
                                  return_value=verdict):
            return delivery._attempt_problems(record)

    def test_windows_job_query_rows(self):
        empty = {'kind': 'windows-job-object', 'id': 'Local\\AgentOrch-x',
                 'state': 'ended', 'active': 0, 'stopped': True,
                 'created_at': 'x', 'proven_at': 'y', 'closed_at': 'z',
                 'detail': None}
        cases = {
            'row 7 absent after close': ('absent', None),
            'row 8 present and empty': ('present', 0),
        }
        for label, verdict in cases.items():
            with self.subTest(label):
                self.assertEqual(
                    self._job_problems(dict(empty), verdict), [], label)
        blocked = {
            'row 5 present with a live member': ('present', 1),
            'row 11 query failure': ('unknown', None),
        }
        for label, verdict in blocked.items():
            with self.subTest(label):
                problems = self._job_problems(dict(empty), verdict)
                self.assertTrue(problems, label)
        self.assertTrue(any('still running' in problem for problem in
                            self._job_problems(dict(empty), ('present', 1))))
        self.assertTrue(any('cannot be proven' in problem for problem in
                            self._job_problems(dict(empty), ('unknown', None))))

    def test_windows_job_record_rows_fail_closed(self):
        good = {'kind': 'windows-job-object', 'id': 'Local\\AgentOrch-x',
                'state': 'ended', 'active': 0, 'stopped': True,
                'created_at': 'x', 'proven_at': 'y', 'closed_at': 'z',
                'detail': None}
        corrupt = {
            'row 2 no Job record': None,
            'row 2 not an object': ['x'],
            'row 2 wrong kind': dict(good, kind='other-job'),
            'row 2 no id': dict(good, id=None),
            'row 2 bad state': dict(good, state='BOGUS'),
            'row 2 bad active': dict(good, active=True),
            'row 2 bad stopped': dict(good, stopped='yes'),
            'row 2 stopped while processes were active': dict(
                good, active=5, stopped=True),
            'row 2 live while the active count was 0': dict(
                good, active=0, stopped=False),
            'row 6 created but never bound': dict(
                good, state='created', stopped=None, proven_at=None),
            'row 14 created+stopped without a proof timestamp': dict(
                good, state='created', stopped=True, proven_at=None,
                detail='the child was never bound to the Job; the direct child '
                       'was terminated and reaped'),
            'row 14 created+stopped without detail markers': dict(
                good, state='created', stopped=True, proven_at='x',
                detail='terminated'),
        }
        for label, job in corrupt.items():
            with self.subTest(label):
                problems = self._job_problems(job, ('present', 0))
                self.assertTrue(problems, label)
                self.assertTrue(any('cannot be proven' in problem
                                    for problem in problems), (label, problems))

    def test_windows_live_job_proof_is_not_upgraded_by_a_vanished_name(self):
        job = {'kind': 'windows-job-object', 'id': 'Local\\AgentOrch-x',
               'state': 'ended', 'active': 3, 'stopped': False,
               'created_at': 'x', 'proven_at': None, 'closed_at': 'z',
               'detail': '3 process(es) still active in the Job'}
        problems = self._job_problems(job, ('absent', None))
        self.assertTrue(any('still running' in problem for problem in problems),
                        problems)

    def test_windows_unproven_job_is_not_upgraded_by_a_vanished_name(self):
        # Row 11: an explicit proof failed, so the vanished name may not turn
        # the recorded "unknown" into a reusable proof.
        job = {'kind': 'windows-job-object', 'id': 'Local\\AgentOrch-x',
               'state': 'ended', 'active': None, 'stopped': None,
               'created_at': 'x', 'proven_at': None, 'closed_at': 'z',
               'detail': 'QueryInformationJobObject failed (5)'}
        problems = self._job_problems(job, ('absent', None))
        self.assertTrue(any('cannot be proven' in problem
                            for problem in problems), problems)

    def test_windows_interrupted_job_is_proven_by_kill_on_close(self):
        # Row 3: the coordinator died mid-attempt. Its last handle closing made
        # the kernel terminate every member, which the absent name proves.
        job = {'kind': 'windows-job-object', 'id': 'Local\\AgentOrch-x',
               'state': 'assigned', 'active': 1, 'stopped': None,
               'created_at': 'x', 'proven_at': None, 'closed_at': None,
               'detail': None}
        self.assertEqual(self._job_problems(job, ('absent', None)), [])

    def test_posix_reuse_keeps_its_recorded_proof_and_ignores_job_rows(self):
        job = {'kind': 'windows-job-object', 'id': 'Local\\AgentOrch-x',
               'state': 'ended', 'active': 1, 'stopped': False,
               'created_at': 'x', 'proven_at': None, 'closed_at': None,
               'detail': None}
        executor = {'state': 'ended', 'pid': 500, 'pgid': None, 'token': 42,
                    'exit': 0, 'stopped': True, 'session': None, 'job': job}
        record = self._record([self._attempt(executor=executor)])
        with mock.patch.object(delivery, '_IS_WINDOWS', False), \
                mock.patch.object(delivery, '_proven_process_state',
                                  lambda *args, **kwargs: 'unknown'):
            self.assertEqual(delivery._attempt_problems(record), [])

    def test_unproven_verifier_blocks_reuse_like_the_executor(self):
        # Round-2 High: an explicit negative verifier stop proof is never
        # reusable on any platform, including a spawn_failed row without a pid.
        executor = {'state': 'ended', 'pid': 100, 'pgid': None, 'token': 42,
                    'exit': 0, 'stopped': True, 'session': None, 'job': None}
        for state in ('ended', 'spawn_failed'):
            with self.subTest(state=state):
                verifier = {'state': state, 'pid': None, 'pgid': None,
                            'token': None, 'exit': None, 'timeout': True,
                            'stopped': False,
                            'job': {'kind': 'windows-job-object',
                                    'id': 'Local\\AgentOrch-x',
                                    'state': 'created', 'active': None,
                                    'stopped': False, 'created_at': 'x',
                                    'proven_at': None, 'closed_at': 'x',
                                    'detail': 'never bound; not proven stopped'}}
                record = self._record([self._attempt(
                    executor=dict(executor), verifiers=[verifier])])
                problems = self._problems(record)
                self.assertTrue(any('verifier 1' in problem
                                    for problem in problems), problems)
                with self.assertRaises(delivery.BatchError):
                    delivery._reuse_guard({'id': 'T1'}, record)

    def test_posix_verifier_without_a_stop_proof_keeps_the_old_verdict(self):
        # Low: the Windows-only "no stop proof" verifier rule must not change
        # the historical POSIX verdict for an ended verifier with no proof.
        executor = {'state': 'ended', 'pid': 100, 'pgid': None, 'token': 42,
                    'exit': 0, 'stopped': True, 'session': None, 'job': None}
        verifier = {'state': 'ended', 'pid': 101, 'pgid': 101, 'token': None,
                    'exit': 0, 'timeout': False, 'stopped': None, 'job': None}
        record = self._record([self._attempt(executor=dict(executor),
                                             verifiers=[verifier])])
        with mock.patch.object(delivery, '_proven_process_state',
                               lambda *args, **kwargs: 'stopped'):
            with mock.patch.object(delivery, '_IS_WINDOWS', False):
                self.assertEqual(delivery._attempt_problems(record), [])
            with mock.patch.object(delivery, '_IS_WINDOWS', True):
                windows = delivery._attempt_problems(record)
        self.assertTrue(any('verifier 1 is ended without a stop proof' in
                            problem for problem in windows), windows)


class WindowsJobObjectTests(unittest.TestCase):
    """#267 AC-1/AC-3/AC-5: the real Job Object proof and its failure branches."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    # -- real Windows coverage ---------------------------------------------
    @unittest.skipUnless(os.name == 'nt', 'real Windows Job Object test')
    def test_real_job_binds_a_suspended_child_before_it_runs(self):
        marker = self.root / 'child-started.txt'
        seen = {}
        script = ('import pathlib, sys\n'
                  f'pathlib.Path({str(marker)!r}).write_text("ran")\n'
                  'print("child-ok")\n')

        def on_bound(pid, pgid, token, record):
            seen.update(pid=pid, token=token, record=dict(record),
                        premature=marker.exists(),
                        query=delivery._job_active_by_name(record['id']))

        spawn = delivery._spawn_bound(
            [PY, '-c', script],
            dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, text=True, env=dict(os.environ)),
            on_bound=on_bound)
        job = spawn['job']
        try:
            self.assertIsNone(spawn['error'], spawn['error'])
            self.assertIsNone(spawn['job_failed'], spawn['job_failed'])
            self.assertIsInstance(seen['pid'], int)
            self.assertIsInstance(seen['token'], int)
            self.assertEqual(seen['record']['kind'], 'windows-job-object')
            self.assertTrue(seen['record']['id'].startswith('Local\\'))
            self.assertEqual(seen['record']['state'], 'assigned')
            # Bound and persisted before the child may run: the Job already
            # counts it, but the child has not written its marker yet.
            self.assertEqual(seen['query'], ('present', 1))
            self.assertFalse(seen['premature'])
            out, err = spawn['process'].communicate(timeout=60)
            self.assertEqual(spawn['process'].returncode, 0, err)
            self.assertEqual(out.strip(), 'child-ok')
            self.assertTrue(marker.exists())
            self.assertEqual(delivery._job_active_by_name(job.name),
                             ('present', 0))
            self.assertEqual(delivery._settle_job(job, grace=5.0), 'stopped')
            self.assertIs(job.record['stopped'], True)
            self.assertEqual(job.record['active'], 0)
            self.assertEqual(job.record['state'], 'ended')
            self.assertIsInstance(job.record['closed_at'], str)
            self.assertEqual(delivery._job_active_by_name(job.name),
                             ('absent', None))
        finally:
            job.close()

    @unittest.skipUnless(os.name == 'nt', 'real Windows Job Object test')
    def test_real_job_tracks_and_terminates_an_orphan_grandchild(self):
        script = (
            'import subprocess, sys\n'
            'child = subprocess.Popen([sys.executable, "-c", '
            '"import time; time.sleep(120)"],\n'
            '    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n'
            '    stderr=subprocess.DEVNULL)\n'
            'print(child.pid, flush=True)\n')
        spawn = delivery._spawn_bound(
            [PY, '-c', script],
            dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, text=True, env=dict(os.environ)))
        job = spawn['job']
        try:
            self.assertIsNone(spawn['job_failed'], spawn['job_failed'])
            out, err = spawn['process'].communicate(timeout=60)
            self.assertEqual(spawn['process'].returncode, 0, err)
            grandchild = int(out.strip())
            # The direct child is gone; the Job still tracks its descendant,
            # which the Windows parent map could not attribute.
            self.assertEqual(delivery._job_active_by_name(job.name),
                             ('present', 1))
            self.assertEqual(job.prove_empty(grace=0.2), 'running')
            self.assertIs(job.record['stopped'], False)
            self.assertTrue(job.stop_and_prove(grace=10.0))
            self.assertEqual(job.active(), 0)
            self.assertIs(job.record['stopped'], True)
            self.assertEqual(delivery._windows_creation_time(grandchild)[0],
                             'gone')
        finally:
            job.close()

    @unittest.skipUnless(os.name == 'nt', 'real Windows Job Object test')
    def test_real_job_name_is_queryable_and_disappears_on_close(self):
        job = delivery._WindowsJob(delivery._new_job_name()).create()
        try:
            self.assertEqual(delivery._job_active_by_name(job.name),
                             ('present', 0))
        finally:
            job.close()
        self.assertIsInstance(job.record['closed_at'], str)
        self.assertEqual(delivery._job_active_by_name(job.name),
                         ('absent', None))
        self.assertEqual(delivery._job_active_by_name('Local\\no-such-job'),
                         ('absent', None))

    @unittest.skipUnless(os.name == 'nt', 'real Windows Job Object test')
    def test_real_timeout_terminates_the_job_tree_and_proves_it_empty(self):
        result = delivery._run_process(
            [PY, '-c', 'import time; time.sleep(120)'], self.root, 1)
        self.assertTrue(result['timeout'])
        self.assertIs(result['stopped'], True)
        self.assertEqual(result['job']['state'], 'ended')
        self.assertIs(result['job']['stopped'], True)
        self.assertEqual(result['job']['active'], 0)
        self.assertIsInstance(result['job']['closed_at'], str)
        self.assertEqual(delivery._job_active_by_name(result['job']['id']),
                         ('absent', None))

    @unittest.skipUnless(os.name == 'nt', 'real Windows Job Object test')
    def test_real_timeout_is_unproven_when_the_job_query_fails(self):
        with mock.patch.object(delivery, '_job_active',
                               side_effect=delivery._JobError('query boom')):
            result = delivery._run_process(
                [PY, '-c', 'import time; time.sleep(120)'], self.root, 1)
        self.assertTrue(result['timeout'])
        self.assertIs(result['stopped'], False)
        self.assertIs(result['job']['stopped'], None)
        self.assertIn('query boom', result['job']['detail'])

    # -- mocked failure and non-Windows branches (AC-5) --------------------
    def test_job_creation_failure_spawns_nothing(self):
        popen = mock.Mock()
        with mock.patch.object(delivery, '_IS_WINDOWS', True), \
                mock.patch.object(delivery, '_create_job_handle',
                                  side_effect=delivery._JobError('job boom')), \
                mock.patch.object(delivery.subprocess, 'Popen', popen):
            spawn = delivery._spawn_bound(
                [PY, '-c', 'pass'],
                dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True))
        self.assertIsNone(spawn['process'])
        self.assertIn('job boom', spawn['job_failed'])
        self.assertEqual(spawn['job_record']['state'], 'created')
        self.assertIsNone(spawn['job_record']['stopped'])
        popen.assert_not_called()

    def test_bind_failure_proves_only_the_reaped_child(self):
        killed = []

        class FakeProcess:
            pid = 4321
            _handle = 1

            def kill(self):
                killed.append(True)

            def communicate(self, timeout=None):
                return '', ''

            def poll(self):
                return -9

        def create(job):
            job.handle = 1
            return job

        with mock.patch.object(delivery, '_IS_WINDOWS', True), \
                mock.patch.object(delivery._WindowsJob, 'create', create), \
                mock.patch.object(delivery, '_assign_job_handle',
                                  side_effect=delivery._JobError('assign boom')), \
                mock.patch.object(delivery, '_process_token', return_value=9), \
                mock.patch.object(delivery._WindowsJob, 'stop_and_prove',
                                  side_effect=AssertionError(
                                      'an empty Job is not a proof for an '
                                      'unbound child')), \
                mock.patch.object(delivery._WindowsJob, 'close',
                                  lambda job: job.record.update(closed_at='x')), \
                mock.patch.object(delivery.subprocess, 'Popen',
                                  return_value=FakeProcess()):
            spawn = delivery._spawn_bound(
                [PY, '-c', 'pass'],
                dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True))
        self.assertEqual(spawn['process'].pid, 4321)
        self.assertEqual(spawn['token'], 9)
        self.assertIn('assign boom', spawn['job_failed'])
        self.assertIs(spawn['stopped'], True)
        self.assertEqual(killed, [True])
        # The record never claims the child was bound to the Job: the proof is
        # the direct child's reap, recorded with an explicit detail.
        self.assertEqual(spawn['job_record']['state'], 'created')
        self.assertIs(spawn['job_record']['stopped'], True)
        self.assertIn('never bound', spawn['job_record']['detail'])
        with mock.patch.object(delivery, '_job_active_by_name',
                               return_value=('absent', None)):
            self.assertEqual(
                delivery._job_proof_status(spawn['job_record'])[0], 'empty')

    def test_bind_failure_without_a_reap_proof_stays_unproven(self):
        class StuckProcess:
            pid = 4321
            _handle = 1

            def kill(self):
                raise OSError('kill refused')

            def communicate(self, timeout=None):
                raise subprocess.TimeoutExpired('cmd', timeout)

            def poll(self):
                return None

        def create(job):
            job.handle = 1
            return job

        with mock.patch.object(delivery, '_IS_WINDOWS', True), \
                mock.patch.object(delivery._WindowsJob, 'create', create), \
                mock.patch.object(delivery, '_assign_job_handle',
                                  side_effect=delivery._JobError('assign boom')), \
                mock.patch.object(delivery._WindowsJob, 'stop_and_prove',
                                  side_effect=AssertionError(
                                      'an empty Job is not a proof')), \
                mock.patch.object(delivery._WindowsJob, 'close',
                                  lambda job: job.record.update(closed_at='x')), \
                mock.patch.object(delivery.subprocess, 'Popen',
                                  return_value=StuckProcess()):
            spawn = delivery._spawn_bound(
                [PY, '-c', 'pass'],
                dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True))
        self.assertIsNone(spawn['process'])
        self.assertIn('assign boom', spawn['job_failed'])
        self.assertIs(spawn['stopped'], False)
        self.assertIs(spawn['job_record']['stopped'], False)

    def test_resume_failure_terminates_the_bound_child(self):
        killed = []
        bound = []

        class FakeProcess:
            pid = 4321
            _handle = 1

            def kill(self):
                killed.append(True)

            def communicate(self, timeout=None):
                return '', ''

        def create(job):
            job.handle = 1
            return job

        with mock.patch.object(delivery, '_IS_WINDOWS', True), \
                mock.patch.object(delivery._WindowsJob, 'create', create), \
                mock.patch.object(delivery, '_assign_job_handle',
                                  side_effect=lambda handle, process: None), \
                mock.patch.object(delivery, '_process_token', return_value=9), \
                mock.patch.object(delivery, '_resume_job_child',
                                  side_effect=delivery._JobError('resume boom')), \
                mock.patch.object(delivery._WindowsJob, 'stop_and_prove',
                                  return_value=True), \
                mock.patch.object(delivery._WindowsJob, 'close',
                                  lambda job: job.record.update(closed_at='x')), \
                mock.patch.object(delivery.subprocess, 'Popen',
                                  return_value=FakeProcess()):
            spawn = delivery._spawn_bound(
                [PY, '-c', 'pass'],
                dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True),
                on_bound=lambda pid, pgid, token, record: bound.append(
                    (pid, token, record['state'])))
        self.assertIsNone(spawn['process'])
        self.assertIn('resume boom', spawn['job_failed'])
        self.assertIs(spawn['stopped'], True)
        self.assertEqual(bound, [(4321, 9, 'assigned')])
        self.assertEqual(killed, [True])

    def test_unpersisted_job_proof_stops_the_child(self):
        class FakeProcess:
            pid = 4321
            _handle = 1

            def kill(self):
                pass

            def communicate(self, timeout=None):
                return '', ''

        def create(job):
            job.handle = 1
            return job

        def broken(pid, pgid, token, record):
            raise OSError('disk full')

        with mock.patch.object(delivery, '_IS_WINDOWS', True), \
                mock.patch.object(delivery._WindowsJob, 'create', create), \
                mock.patch.object(delivery, '_assign_job_handle',
                                  side_effect=lambda handle, process: None), \
                mock.patch.object(delivery, '_process_token', return_value=9), \
                mock.patch.object(delivery._WindowsJob, 'stop_and_prove',
                                  return_value=True), \
                mock.patch.object(delivery._WindowsJob, 'close',
                                  lambda job: job.record.update(closed_at='x')), \
                mock.patch.object(delivery.subprocess, 'Popen',
                                  return_value=FakeProcess()):
            spawn = delivery._spawn_bound(
                [PY, '-c', 'pass'],
                dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True),
                on_bound=broken)
        self.assertIn('disk full', spawn['record_failed'])
        self.assertIs(spawn['stopped'], True)
        self.assertEqual(spawn['process'].pid, 4321)

    def test_non_windows_spawn_creates_no_job(self):
        # Mocks the POSIX branch so the shared guard logic is exercised on
        # every platform as well.
        with mock.patch.object(delivery, '_IS_WINDOWS', False):
            spawn = delivery._spawn_bound(
                [PY, '-c', 'print("plain")'],
                dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True))
        self.assertIsNone(spawn['job'])
        self.assertIsNone(spawn['job_record'])
        self.assertIsNone(spawn['token'])
        out, _err = spawn['process'].communicate(timeout=60)
        self.assertEqual(out.strip(), 'plain')

    def test_unproven_termination_marks_the_job_record_unproven(self):
        # Medium 1: a timeout whose stop cannot be proven must not leave an
        # 'assigned'/None record, which is the interrupted-attempt proof.
        class SlowProcess:
            def communicate(self, timeout=None):
                raise subprocess.TimeoutExpired('cmd', timeout)

        job = delivery._WindowsJob('Local\\AgentOrch-x')
        job.handle = 1
        with mock.patch.object(delivery, '_kill_group',
                               return_value=(True, None)), \
                mock.patch.object(delivery._WindowsJob, 'close',
                                  lambda worker: worker.record.update(
                                      closed_at='x')):
            proven, _detail = delivery._stop_timed_out(SlowProcess(), job)
        self.assertFalse(proven)
        self.assertEqual(job.record['state'], 'ended')
        self.assertIsNone(job.record['stopped'])
        with mock.patch.object(delivery, '_job_active_by_name',
                               return_value=('absent', None)):
            verdict, detail = delivery._job_proof_status(job.record)
        self.assertEqual(verdict, 'unknown', detail)

    def test_inconsistent_job_record_is_never_treated_as_proven(self):
        # Low: stopped=True with a recorded active>0 is contradictory.
        record = {'kind': 'windows-job-object', 'id': 'Local\\AgentOrch-x',
                  'state': 'ended', 'active': 5, 'stopped': True,
                  'created_at': 'x', 'proven_at': 'y', 'closed_at': 'z',
                  'detail': None}
        with mock.patch.object(delivery, '_job_active_by_name',
                               return_value=('absent', None)):
            verdict, detail = delivery._job_proof_status(record)
        self.assertEqual(verdict, 'invalid', detail)
        live = dict(record, active=0, stopped=False)
        with mock.patch.object(delivery, '_job_active_by_name',
                               return_value=('absent', None)):
            self.assertEqual(delivery._job_proof_status(live)[0], 'invalid')


class AdapterTransportTests(BatchCase):
    """Execution/review adapters: legacy default, generic command, attached."""

    CAPS = {'C1': 'PASS', 'C2': 'PASS', 'C3': 'PASS', 'C4': 'PASS'}

    def host_plan(self, tasks):
        plan = self.plan(tasks)
        plan['executor_adapter'] = {
            'transport': 'attached', 'name': 'zcode-host',
            'capabilities': dict(self.CAPS),
            'capability_evidence': 'https://example.invalid/probe/230'}
        return plan

    def _pending(self, ident='T1'):
        state = self.journal_data()
        pending = state['tasks'][ident].get('awaiting_host')
        self.assertIsInstance(pending, dict, state['tasks'][ident])
        path = Path(pending['request_path'])
        self.assertTrue(path.is_file(), path)
        return path, json.loads(path.read_text(encoding='utf-8'))

    def _execution_result(self, request_path, *, terminal='success',
                          exit_code=0, report='host report', session='host-exec-1',
                          head_override=None, write=True, commit=True,
                          extra=None, agent=None, workdir=None):
        request_path = Path(request_path)
        request = json.loads(request_path.read_text(encoding='utf-8'))
        work = Path(request['worktree'])
        base = request['base_sha']
        if write:
            for name in request['paths']:
                target = work / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('host execution\n', encoding='utf-8')
        if extra:
            target = work / extra
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('out of scope\n', encoding='utf-8')
        if commit:
            _git(work, 'add', '-A')
            _git(work, 'commit', '-q', '-m', f'host {request["task_id"]}')
        head = head_override or self.head(work)
        if head_override:
            paths, raw = [], b''
        else:
            paths = (delivery._diff_names(work, base, head)
                     if write or extra else [])
            raw = delivery._git(work, 'diff', '--no-ext-diff', '--no-color',
                                '--no-renames', base, head,
                                binary=True).stdout or b''
        adapter_dir = request_path.parent
        artifact = adapter_dir / 'execution-report.txt'
        artifact.write_text('artifact\n', encoding='utf-8')
        data = artifact.read_bytes()
        result = {
            'schema': delivery.EXECUTION_RESULT_SCHEMA, 'kind': 'execution',
            'request_sha256': hashlib.sha256(request_path.read_bytes()).hexdigest(),
            'batch_id': request['batch_id'], 'task_id': request['task_id'],
            'session_id': session,
            'executor_agent': agent or request['executor_agent'],
            'workdir': workdir or request['worktree'],
            'terminal': terminal, 'exit_code': exit_code, 'report': report,
            'artifacts': [{'path': str(artifact.resolve()),
                           'sha256': hashlib.sha256(data).hexdigest(),
                           'bytes': len(data)}],
            'head': head,
            'diff': {'base': base, 'head': head, 'paths': paths,
                     'sha256': hashlib.sha256(raw).hexdigest()},
            'ended_at': '2026-11-22T00:00:00+00:00',
        }
        path = Path(request['result_path'])
        path.write_text(json.dumps(result), encoding='utf-8')
        return path

    def _review_result(self, request_path, *, verdict='APPROVE', session='host-rev-1',
                       head=None, diff_sha256=None, risk=None, tasks=None,
                       edit_denied=True, declared=None, reviewer_agent=None,
                       workdir=None):
        request_path = Path(request_path)
        request = json.loads(request_path.read_text(encoding='utf-8'))
        state = self.journal_data()
        head = head or self.head(self.root / 'wt-batch')
        digest = diff_sha256 or delivery._diff_for_review(
            self.repo, state['base_sha'], head)[1]
        risk = risk or request['risk']
        if tasks is None:
            tasks = []
            for task in request['tasks']:
                tasks.append({'id': task['id'], 'acceptance': [
                    {'id': f'AC-{index}', 'criterion': criterion,
                     'status': 'PASS', 'evidence': f'checked {task["id"]} '
                                                   f'AC-{index}'}
                    for index, criterion in enumerate(task['acceptance'],
                                                      start=1)]})
        if declared is None:
            declared = [record['execution_session']
                        for record in state['tasks'].values()
                        if record.get('execution_session')]
        result = {
            'schema': delivery.REVIEW_RESULT_SCHEMA, 'kind': 'review',
            'request_sha256': hashlib.sha256(request_path.read_bytes()).hexdigest(),
            'batch_id': request['batch_id'], 'session_id': session,
            'reviewer_agent': reviewer_agent or request['reviewer_agent'],
            'workdir': workdir or str((self.root / 'wt-batch').resolve()),
            'edit_denied': edit_denied, 'declared_executor_sessions': declared,
            'base': state['base_sha'], 'head': head, 'diff_sha256': digest,
            'risk': risk, 'verdict': verdict, 'tasks': tasks,
            'findings': [] if verdict == 'APPROVE' else [
                {'severity': 'high', 'note': 'host reviewer rejection'}],
            'report': 'host review report',
            'ended_at': '2026-11-22T00:00:00+00:00',
        }
        path = Path(request['result_path'])
        path.write_text(json.dumps(result), encoding='utf-8')
        return path

    def _integrate_one(self, plan, path):
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, _ = self._pending()
        result = self._execution_result(request_path)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 0, attach.stderr)
        self.assertEqual(self.journal_data()['tasks']['T1']['status'],
                         'INTEGRATED')
        return request_path

    # -- legacy default -------------------------------------------------
    def test_default_plan_records_the_legacy_opencode_adapter(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        adapter = self.journal_data()['adapter']
        self.assertEqual(adapter['transport'], 'command')
        self.assertEqual(adapter['name'], 'opencode')
        self.assertEqual(adapter['source'], 'legacy-default')

    def test_read_only_entries_work_without_a_runtime(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        check = self.cli('check', '--plan', str(path))
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn('attached/zcode-host', check.stdout)
        dry = self.cli('run', '--plan', str(path), '--journal', str(self.journal))
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn('attached/zcode-host', dry.stdout)
        self.assertFalse((self.root / 'wt-t1').exists())

    # -- attached execution --------------------------------------------
    def test_attached_request_then_attach_integrates_and_records_evidence(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 3, run.stderr + run.stdout)
        self.assertIn('awaiting host execution request', run.stdout)
        self.assertEqual(self.opencode_calls(), [])
        state = self.journal_data()
        record = state['tasks']['T1']
        self.assertEqual(record['status'], 'RUNNING')
        self.assertEqual(record['attempts'], 1)
        request_path, request = self._pending()
        self.assertEqual(request['task_id'], 'T1')
        self.assertEqual(Path(request['worktree']).resolve(),
                         (self.root / 'wt-t1').resolve())
        self.assertEqual(request['branch'], 'task/batch-t1')
        self.assertEqual(request['paths'], ['a.txt'])
        self.assertIn('TASK_ID=T1', request['prompt'])
        self.assertIn('PATHS_JSON=["a.txt"]', request['prompt'])
        self.assertIn('Commit your changes and leave the worktree clean',
                      request['prompt'])
        self.assertNotIn('uncommitted changes are also acceptable',
                         request['prompt'])
        self.assertRegex(request['base_sha'], r'^[0-9a-f]{40}$')
        result = self._execution_result(request_path)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 0, attach.stderr)
        state = self.journal_data()
        record = state['tasks']['T1']
        self.assertEqual(record['status'], 'INTEGRATED')
        self.assertIsNone(record['awaiting_host'])
        self.assertEqual(record['execution_session'], 'host-exec-1')
        self.assertEqual(len(record['adapter_history']), 1)
        entry = record['adapter_history'][0]
        self.assertTrue(entry['accepted'])
        self.assertTrue(entry['integrated'])
        self.assertEqual(entry['terminal'], 'success')
        attempt = record['attempt_history'][0]
        self.assertEqual(attempt['state'], 'ENDED')
        self.assertEqual(attempt['result'], 'ok')
        self.assertEqual(attempt['adapter']['state'], 'accepted')
        self.assertEqual(attempt['adapter']['session'], 'host-exec-1')
        replay = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(replay.returncode, 2, replay.stderr)
        self.assertIn('no host request waiting', replay.stderr)

    def test_second_run_while_attached_is_idempotent(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        first = self.run_batch(plan)
        self.assertEqual(first.returncode, 3, first.stderr)
        first_request, _ = self._pending()
        second = self.cli('run', '--plan', str(self.write_plan(plan)), '--journal',
                          str(self.journal), '--execute')
        self.assertEqual(second.returncode, 3, second.stderr)
        self.assertIn(str(first_request), second.stdout)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['attempts'], 1)
        self.assertEqual(len(list(self.journal.parent.glob(
            f'{self.journal.stem}.adapter/execution-*.request.json'))), 1)

    def test_missing_result_is_rejected_without_integration(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        missing = self.root / 'no-such-result.json'
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(missing))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertIsNone(record['integration_sha'])
        self.assertEqual(record['attempt_history'][0]['result'],
                         'adapter_rejected')
        self.assertFalse(record.get('head'))

    def test_unknown_terminal_is_rejected(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, _ = self._pending()
        result = self._execution_result(request_path, terminal='unknown', write=False,
                                        commit=False)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('unknown terminal', attach.stderr)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertIsNone(record['integration_sha'])

    def test_head_mismatch_is_rejected(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, _ = self._pending()
        result = self._execution_result(request_path, head_override='0' * 40)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertEqual(record['attempt_history'][0]['result'],
                         'adapter_rejected')
        self.assertIsNone(record['integration_sha'])

    def test_out_of_scope_result_is_failed_without_integration(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, _ = self._pending()
        result = self._execution_result(request_path, extra='extra.txt')
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('scope violation', attach.stderr)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'FAILED')
        self.assertIsNone(record['integration_sha'])

    def test_attached_verification_failure_keeps_its_candidate_for_retry(self):
        verify = [[PY, '-c',
                   'import os, sys; sys.exit(17 if os.getenv("FAIL_T1") else 0)']]
        plan = self.host_plan([self.task('T1', 'a.txt', verify=verify)])
        self.assertEqual(self.run_batch(plan).returncode, 3)
        path = self.write_plan(plan)
        request, first_request = self._pending()
        result = self._execution_result(request)
        failed = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result),
                          extra={'FAIL_T1': '1'})
        self.assertEqual(failed.returncode, 1, failed.stderr)
        record = self.journal_data()['tasks']['T1']
        candidate = record['observed_head']
        self.assertEqual(record['status'], 'FAILED')
        self.assertEqual(record['observed_paths'], ['a.txt'])
        self.assertEqual(candidate, self.head(Path(first_request['worktree'])))
        self.assertEqual(record['verify'][0]['exit'], 17)
        self.assertEqual(record['attempt_history'][0]['result'], 'failed')
        self.assertTrue(record['adapter_history'][-1]['accepted'])
        self.assertFalse(record['adapter_history'][-1]['integrated'])
        self.assertIsNone(record['integration_sha'])

        retried = self.run_batch(plan, None, '--retry', 'T1')
        self.assertEqual(retried.returncode, 3, retried.stderr)
        request, next_request = self._pending()
        self.assertEqual(next_request['base_sha'], first_request['base_sha'])
        self.assertEqual(self.journal_data()['tasks']['T1']['observed_head'],
                         candidate)
        result = self._execution_result(request, commit=False,
                                        session='host-exec-2')
        accepted = self.cli('attach', '--plan', str(path), '--journal',
                            str(self.journal), '--result', str(result))
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        record = self.journal_data()['tasks']['T1']
        batch = self.root / 'wt-batch'
        self.assertEqual(record['status'], 'INTEGRATED')
        self.assertEqual(record['observed_head'], candidate)
        self.assertEqual(record['integration_sha'], self.head(batch))
        self.assertEqual(self.head(batch, 'HEAD^2'), candidate)
        self.assertEqual(self.count_merges(batch, 'HEAD'), 1)
        self.assertTrue(record['adapter_history'][-1]['integrated'])

    def test_dirty_attached_worktree_is_rejected(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, _ = self._pending()
        request = json.loads(request_path.read_text())
        (Path(request['worktree']) / 'a.txt').write_text('dirty\n')
        result = self._execution_result(request_path, write=False, commit=False)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('dirty', attach.stderr)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertIsNone(record['integration_sha'])

    def test_host_failure_terminal_marks_task_failed(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, _ = self._pending()
        result = self._execution_result(request_path, terminal='failure',
                                        exit_code=1, report='host failed',
                                        write=False, commit=False)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'FAILED')
        self.assertIsNone(record['integration_sha'])
        self.assertEqual(record['adapter_history'][0]['terminal'], 'failure')
        self.assertEqual(record['attempt_history'][0]['result'], 'failed')
        self.assertEqual(record['attempt_history'][0]['adapter']['state'],
                         'accepted')
        self.assertEqual(record['attempt_history'][0]['adapter']['terminal'],
                         'failure')

    # -- attached review ------------------------------------------------
    def test_attached_review_binds_head_diff_and_per_ac_evidence(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self._integrate_one(plan, path)
        done = self.finalize(plan, None, '--review')
        self.assertEqual(done.returncode, 3, done.stderr + done.stdout)
        self.assertIn('awaiting host review request', done.stdout)
        state = self.journal_data()
        self.assertEqual(state['review']['verdict'], 'AWAITING_HOST')
        request_path = Path(state['review']['request_path'])
        request = json.loads(request_path.read_text(encoding='utf-8'))
        head = self.head(self.root / 'wt-batch')
        self.assertEqual(request['head_sha'], head)
        self.assertEqual(request['diff_sha256'],
                         delivery._diff_for_review(self.repo, state['base_sha'],
                                                   head)[1])
        self.assertEqual(request['tasks'][0]['acceptance'],
                         plan['tasks'][0]['acceptance'])
        self.assertIn('DIFF_BEGIN', request['prompt'])
        self.assertIn('a.txt', request['diff'])
        self.assertIn(delivery.REVIEW_RESULT_SCHEMA, request['prompt'])
        self.assertIn('Do not only print a fenced JSON verdict', request['prompt'])
        self.assertNotIn('Answer with exactly one fenced', request['prompt'])
        before = request_path.read_bytes()
        again = self.finalize(plan, None, '--review')
        self.assertEqual(again.returncode, 3, again.stderr + again.stdout)
        self.assertEqual(request_path.read_bytes(), before)
        result = self._review_result(request_path)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 0, attach.stderr)
        state = self.journal_data()
        self.assertEqual(state['review']['verdict'], 'APPROVE')
        self.assertEqual(state['review']['session'], 'host-rev-1')
        self.assertEqual(len(state['review_history']), 1)
        self.assertTrue(state['review_history'][0]['accepted'])
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def _pending_review(self, plan):
        path = self.write_plan(plan)
        self._integrate_one(plan, path)
        done = self.finalize(plan, None, '--review')
        self.assertEqual(done.returncode, 3, done.stderr + done.stdout)
        review = self.journal_data()['review']
        return path, Path(review['request_path'])

    def test_attached_review_rejects_the_executor_session(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path, request_path = self._pending_review(plan)
        result = self._review_result(request_path, session='host-exec-1')
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('reviewer session is an execution session', attach.stderr)
        self.assertEqual(self.journal_data()['review']['verdict'], 'BLOCKED')

    def test_attached_review_rejects_missing_acceptance_evidence(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path, request_path = self._pending_review(plan)
        tasks = [{'id': 'T1', 'acceptance': [
            {'id': 'AC-1', 'criterion': plan['tasks'][0]['acceptance'][0],
             'status': 'PASS', 'evidence': ''}]}]
        result = self._review_result(request_path, tasks=tasks)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('has no evidence', attach.stderr)
        state = self.journal_data()
        self.assertEqual(state['review']['verdict'], 'BLOCKED')
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    def test_attached_review_rejects_a_different_diff(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path, request_path = self._pending_review(plan)
        result = self._review_result(request_path, diff_sha256='0' * 64)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('different diff', attach.stderr)
        self.assertEqual(self.journal_data()['review']['verdict'], 'BLOCKED')

    def test_attached_review_rejects_clean_worktree_with_unfinished_merge(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path, request_path = self._pending_review(plan)
        result = self._review_result(request_path)
        batch = self.root / 'wt-batch'
        head = self.head(batch)
        target = self.empty_batch_merge_target()
        _git(batch, 'merge', '--no-ff', '--no-commit', target)
        self.assertEqual(_git(batch, 'status', '--porcelain').stdout, b'')
        self.assertEqual(self.head(batch, 'MERGE_HEAD'), target)

        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('unfinished Git merge (MERGE_HEAD)', attach.stderr)
        state = self.journal_data()
        self.assertEqual(state['review']['verdict'], 'BLOCKED')
        self.assertFalse(state['review_history'][-1]['accepted'])
        self.assertEqual(self.head(batch), head)
        self.assertEqual(self.head(batch, 'MERGE_HEAD'), target)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    # -- I/O failure and attached invariants ---------------------------
    def test_agent_io_error_kills_and_reports_the_stop_proof(self):
        class BrokenProcess:
            pid = 4242
            returncode = None

            def communicate(self, input=None, timeout=None):
                raise UnicodeEncodeError('utf-8', '中', 0, 1, 'cannot encode')

        adapter = {'transport': 'command', 'name': 'generic-command',
                   'command': [PY, '-S', 'x'], 'source': 'plan'}
        # The spawn itself (including the Windows Job binding) is covered by
        # its own tests; this unit isolates the post-spawn I/O failure.
        spawn = {'process': BrokenProcess(), 'job': None, 'token': 7,
                 'job_record': None, 'error': None, 'job_failed': None,
                 'record_failed': None, 'stopped': None}
        with mock.patch.object(delivery, '_spawn_bound', return_value=spawn), \
                mock.patch.object(delivery, '_stop_timed_out',
                                  return_value=(True, 'timed out; process group '
                                                       'killed')):
            result = delivery._agent(self.root, '中文 prompt', 'builder', 5,
                                     adapter=adapter, role='executor')
        self.assertIn('UnicodeEncodeError', result['io_error'])
        self.assertIs(result['stopped'], True)
        self.assertIsNone(result['session_id'])
        self.assertIsNone(result['exit_code'])

    def test_prove_stopped_treats_a_decode_failure_as_unproven(self):
        class BrokenProcess:
            def communicate(self, timeout=None):
                raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'bad byte')

        self.assertFalse(delivery._prove_stopped(BrokenProcess(), True, 1234))

    def test_execute_task_io_error_ends_the_attempt_without_orphans(self):
        raw = self.plan([self.task('T1', 'a.txt')])
        plan = delivery.validate_plan(raw, str(self.repo))
        spec = plan['tasks'][0]
        base = self.head(self.repo)
        for stopped in (True, False):
            with self.subTest(stopped=stopped):
                record = delivery._task_record(spec, plan['executor_agent'])
                saves = []
                attempt = delivery._start_attempt(
                    record, spec, 'fresh', lambda: saves.append('start'))
                result = {'exit_code': None, 'session_id': None, 'text': '',
                          'pid': 4242, 'pgid': None, 'token': None,
                          'stopped': stopped, 'timeout': False,
                          'io_error': 'UnicodeEncodeError: cannot encode',
                          'detail': 'UnicodeEncodeError: cannot encode; killed'}
                with mock.patch.object(delivery, '_agent', return_value=result):
                    with self.assertRaises(delivery.BatchError) as caught:
                        delivery._execute_task(
                            plan, spec, record, base, 5,
                            lambda: saves.append('save'), attempt, self.journal)
                self.assertEqual(caught.exception.status, 'FAILED')
                self.assertIs(caught.exception.halt, stopped is not True)
                self.assertEqual(attempt['state'], 'ENDED')
                self.assertEqual(attempt['result'],
                                 'failed' if stopped else 'unproven_stop')
                self.assertEqual(attempt['executor']['state'], 'ended')
                self.assertIs(attempt['executor']['stopped'], stopped)

    def test_execute_task_job_failure_rows_halt_only_when_unproven(self):
        raw = self.plan([self.task('T1', 'a.txt')])
        plan = delivery.validate_plan(raw, str(self.repo))
        spec = plan['tasks'][0]
        base = self.head(self.repo)
        reap_detail = ('the child was never bound to the Job; the direct child '
                       'was terminated and reaped')
        lost_detail = ('the child was never bound to the Job; the direct child '
                       'was not proven stopped')
        cases = {
            # Matrix rows 13-16 with the exact production shapes. A resume
            # failure terminates the Job but has no direct-child pid, so the
            # guard over-blocks it (documented, fail closed).
            'row 13 job creation failed': {
                'detail': 'cannot create a Job Object: job boom',
                'pid': None, 'token': None, 'stopped': None,
                'job': {'state': 'created', 'stopped': None, 'proven_at': None,
                        'detail': 'cannot create a Job Object'},
                'halt': True, 'expect': 'not proven stopped'},
            'row 14a bind failed after the child was reaped': {
                'detail': 'cannot bind the child to its Job Object: assign boom',
                'pid': 424242, 'token': 7, 'stopped': True,
                'job': {'state': 'created', 'stopped': True,
                        'proven_at': 'x', 'detail': reap_detail},
                'halt': False, 'expect': None},
            'row 14b bind failed without a proof': {
                'detail': 'cannot bind the child to its Job Object: assign boom',
                'pid': None, 'token': None, 'stopped': False,
                'job': {'state': 'created', 'stopped': False,
                        'proven_at': None, 'detail': lost_detail},
                'halt': True, 'expect': 'not proven stopped'},
            'row 16a resume failed after the tree was proven empty': {
                'detail': 'cannot resume the Job-bound child: resume boom',
                'pid': None, 'token': None, 'stopped': True,
                'job': {'state': 'ended', 'stopped': True, 'proven_at': 'x',
                        'detail': None},
                'halt': False, 'expect': 'claims a stop proof without a pid'},
            'row 16b resume failed without a proof': {
                'detail': 'cannot resume the Job-bound child: resume boom',
                'pid': None, 'token': None, 'stopped': False,
                'job': {'state': 'ended', 'stopped': False, 'proven_at': None,
                        'detail': 'not proven'},
                'halt': True, 'expect': 'not proven stopped'},
        }
        for label, row in cases.items():
            with self.subTest(label):
                record = delivery._task_record(spec, plan['executor_agent'])
                saves = []
                attempt = delivery._start_attempt(
                    record, spec, 'fresh', lambda: saves.append('start'))
                result = {'exit_code': None, 'session_id': None, 'text': '',
                          'pid': row['pid'], 'pgid': None,
                          'token': row['token'], 'stopped': row['stopped'],
                          'timeout': False,
                          'job_failed': row['detail'], 'detail': row['detail'],
                          'job': dict(row['job'],
                                      kind='windows-job-object',
                                      id='Local\\AgentOrch-x', active=None,
                                      created_at='x', closed_at='x')}
                with mock.patch.object(delivery, '_agent', return_value=result):
                    with self.assertRaises(delivery.BatchError) as caught:
                        delivery._execute_task(
                            plan, spec, record, base, 5,
                            lambda: saves.append('save'), attempt, self.journal)
                self.assertIs(caught.exception.halt, row['halt'], label)
                self.assertEqual(
                    attempt['result'],
                    'failed' if row['stopped'] is True else 'unproven_stop')
                self.assertEqual(attempt['executor']['state'], 'spawn_failed')
                self.assertIs(attempt['executor']['stopped'], row['stopped'])
                with mock.patch.object(delivery, '_proven_process_state',
                                       lambda *args, **kwargs: 'stopped'):
                    problems = delivery._attempt_problems(record)
                if row['expect'] is None:
                    self.assertEqual(problems, [], label)
                else:
                    self.assertTrue(any(row['expect'] in problem
                                        for problem in problems),
                                    (label, problems))

    def _job_bound_attempt(self):
        return {
            'id': 'attempt-' + 'a' * 32, 'index': 1, 'task': 'T1',
            'trigger': 'fresh', 'state': 'STARTED',
            'started_at': '2026-11-20T00:00:00+00:00', 'ended_at': None,
            'result': None, 'error': None,
            'executor': {'state': 'ended', 'pid': 424242, 'pgid': None,
                         'token': 7, 'exit': 0, 'stopped': True,
                         'session': 'ses_x', 'job': None},
            'verifiers': []}

    def test_verify_task_unproven_job_failure_blocks_retry(self):
        # Round-2 High/Medium: reproduce the exact production shape of an
        # unproven verifier bind failure and prove the reuse guard refuses it.
        attempt = self._job_bound_attempt()
        detail = ('the child was never bound to the Job; the direct child was '
                  'not proven stopped')
        result = {'exit': None, 'tail': 'job boom', 'timeout': False,
                  'pid': None, 'pgid': None, 'token': None, 'stopped': False,
                  'job': {'kind': 'windows-job-object',
                          'id': 'Local\\AgentOrch-x', 'state': 'created',
                          'active': None, 'stopped': False,
                          'created_at': 'x', 'proven_at': None,
                          'closed_at': 'x', 'detail': detail},
                  'job_failed': 'cannot bind the child to its Job Object'}
        with mock.patch.object(delivery, '_run_process', return_value=result):
            with self.assertRaises(delivery.BatchError) as caught:
                delivery._verify_task(
                    self.repo, [[PY, '-c', 'pass']], 5,
                    attempt=attempt, save=lambda: None)
        self.assertIs(caught.exception.halt, True)
        entry = attempt['verifiers'][0]
        self.assertEqual(entry['state'], 'spawn_failed')
        self.assertIsNone(entry['pid'])
        self.assertIs(entry['stopped'], False)
        self.assertEqual(entry['job']['state'], 'created')
        self.assertIs(entry['job']['stopped'], False)
        self.assertEqual(attempt['result'], 'unproven_stop')
        record = {'status': 'FAILED', 'attempts': 1,
                  'attempt_history': [attempt]}
        with mock.patch.object(delivery, '_proven_process_state',
                               lambda *args, **kwargs: 'stopped'):
            problems = delivery._attempt_problems(record)
        self.assertTrue(any('verifier 1' in problem
                            and 'not proven stopped' in problem
                            for problem in problems), problems)
        with mock.patch.object(delivery, '_proven_process_state',
                               lambda *args, **kwargs: 'stopped'):
            with self.assertRaises(delivery.BatchError) as guarded:
                delivery._reuse_guard({'id': 'T1'}, record)
        self.assertEqual(guarded.exception.status, 'BLOCKED')
        self.assertTrue(guarded.exception.halt)

    def test_verify_task_proven_job_failure_stays_reusable(self):
        attempt = self._job_bound_attempt()
        detail = ('the child was never bound to the Job; the direct child was '
                  'terminated and reaped')
        result = {'exit': None, 'tail': '', 'timeout': False,
                  'pid': 424242, 'pgid': None, 'token': 7, 'stopped': True,
                  'job': {'kind': 'windows-job-object',
                          'id': 'Local\\AgentOrch-x', 'state': 'created',
                          'active': None, 'stopped': True,
                          'created_at': 'x', 'proven_at': 'x',
                          'closed_at': 'x', 'detail': detail},
                  'job_failed': 'cannot bind the child to its Job Object'}
        with mock.patch.object(delivery, '_run_process', return_value=result):
            with self.assertRaises(delivery.BatchError) as caught:
                delivery._verify_task(
                    self.repo, [[PY, '-c', 'pass']], 5,
                    attempt=attempt, save=lambda: None)
        self.assertIs(caught.exception.halt, False)
        self.assertEqual(attempt['verifiers'][0]['state'], 'spawn_failed')
        self.assertIs(attempt['verifiers'][0]['stopped'], True)
        record = {'status': 'FAILED', 'attempts': 1,
                  'attempt_history': [attempt]}
        with mock.patch.object(delivery, '_proven_process_state',
                               lambda *args, **kwargs: 'stopped'):
            self.assertEqual(delivery._attempt_problems(record), [])

    def test_attached_success_rejects_a_detached_worktree(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, request = self._pending()
        result = self._execution_result(request_path)
        _git(Path(request['worktree']), 'checkout', '--detach')
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('not on the task branch', attach.stderr)
        record = self.journal_data()['tasks']['T1']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertIsNone(record['integration_sha'])

    def test_attached_result_rejects_duplicate_keys(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, _ = self._pending()
        result = self._execution_result(request_path)
        text = result.read_text(encoding='utf-8')
        needle = '"task_id": "T1"'
        self.assertIn(needle, text)
        result.write_text(text.replace(
            needle, '"task_id": "T1", "task_id": "T1"', 1), encoding='utf-8')
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('duplicate key', attach.stderr)
        self.assertEqual(self.journal_data()['tasks']['T1']['status'],
                         'BLOCKED')

    def test_attached_result_rejects_unknown_fields(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, _ = self._pending()
        result = self._execution_result(request_path)
        data = json.loads(result.read_text(encoding='utf-8'))
        data['unexpected'] = 1
        result.write_text(json.dumps(data), encoding='utf-8')
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('unknown field', attach.stderr)
        self.assertEqual(self.journal_data()['tasks']['T1']['status'],
                         'BLOCKED')

    def test_attached_result_rejects_artifact_mismatch(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, _ = self._pending()
        result = self._execution_result(request_path)
        data = json.loads(result.read_text(encoding='utf-8'))
        data['artifacts'][0]['sha256'] = '0' * 64
        result.write_text(json.dumps(data), encoding='utf-8')
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('does not match its digest', attach.stderr)
        self.assertIsNone(self.journal_data()['tasks']['T1']['integration_sha'])

    def test_attached_result_rejects_a_rewritten_request(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self.assertEqual(self.run_batch(plan).returncode, 3)
        request_path, _ = self._pending()
        request_path.write_bytes(request_path.read_bytes() + b' ')
        result = self._execution_result(request_path)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('request changed', attach.stderr)
        self.assertEqual(self.journal_data()['tasks']['T1']['status'],
                         'BLOCKED')

    def test_review_rejects_a_session_from_an_earlier_failed_attempt(self):
        plan = self.host_plan([self.task('T1', 'a.txt')])
        path = self.write_plan(plan)
        self._integrate_one(plan, path)
        state = self.journal_data()
        record = state['tasks']['T1']
        record['adapter_history'].append({
            'kind': 'execution', 'accepted': True, 'terminal': 'failure',
            'session_id': 'old-failed-session', 'exit_code': 1,
            'result_sha256': '0' * 64, 'integrated': False,
            'at': '2026-11-22T00:00:00+00:00'})
        self.journal.write_text(json.dumps(state))
        done = self.finalize(plan, None, '--review')
        self.assertEqual(done.returncode, 3, done.stderr + done.stdout)
        review = self.journal_data()['review']
        request_path = Path(review['request_path'])
        declared = [record['execution_session'], 'old-failed-session']
        result = self._review_result(request_path,
                                     session='old-failed-session',
                                     declared=declared)
        attach = self.cli('attach', '--plan', str(path), '--journal',
                          str(self.journal), '--result', str(result))
        self.assertEqual(attach.returncode, 1, attach.stderr)
        self.assertIn('reviewer session is an execution session', attach.stderr)
        self.assertEqual(self.journal_data()['review']['verdict'], 'BLOCKED')

    def test_recorded_execution_sessions_cover_all_histories(self):
        state = {'tasks': {'T1': {
            'execution_session': 's1',
            'adapter_history': [{'session_id': 's2'}],
            'attempt_history': [
                {'executor': {'session': 's3'}, 'adapter': {'session': 's4'}},
                {'executor': {'session': None}, 'adapter': None}]}}}
        self.assertEqual(delivery._recorded_execution_sessions(state),
                         {'s1', 's2', 's3', 's4'})

    def test_old_journal_refuses_a_backfilled_explicit_adapter(self):
        explicit_raw = self.host_plan([self.task('T1', 'a.txt')])
        explicit = delivery.validate_plan(explicit_raw, str(self.repo))
        state = {'batch_id': explicit['batch']['id'],
                 'plan_sha256': delivery.plan_hash(explicit_raw),
                 'repo': explicit['batch']['repo'],
                 'project_config_sha256':
                     delivery._project_config_digest(self.repo)}
        with self.assertRaises(delivery.InputError) as caught:
            delivery._check_journal_plan(state, explicit)
        self.assertIn('predates executor adapters', str(caught.exception))
        legacy_raw = self.plan([self.task('T1', 'a.txt')])
        legacy = delivery.validate_plan(legacy_raw, str(self.repo))
        legacy_state = {'batch_id': legacy['batch']['id'],
                        'plan_sha256': delivery.plan_hash(legacy_raw),
                        'repo': legacy['batch']['repo'],
                        'project_config_sha256':
                            delivery._project_config_digest(self.repo)}
        delivery._check_journal_plan(legacy_state, legacy)
        self.assertEqual(legacy_state['adapter']['source'], 'legacy-default')

    # -- generic command transport -------------------------------------
    def _generic_plan(self):
        script = self.bin / 'generic.py'
        script.write_text(
            'import json, os, re, sys\n'
            'from pathlib import Path\n'
            'prompt = sys.stdin.read()\n'
            'Path(os.environ["GENERIC_MARKER"]).write_text(prompt)\n'
            'paths = json.loads(re.search(r"^PATHS_JSON=(.*)$", prompt, re.M).group(1))\n'
            'for name in paths:\n'
            '    path = Path(name); path.parent.mkdir(parents=True, exist_ok=True)\n'
            '    path.write_text("generic\\n")\n'
            'session = "generic-session-1"\n'
            'print(json.dumps({"type": "step_start", "sessionID": session}))\n'
            'print(json.dumps({"type": "text", "sessionID": session, '
            '"part": {"text": "done"}}))\n'
            'print(json.dumps({"type": "step_finish", "sessionID": session, '
            '"part": {"reason": "stop"}}))\n')
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['executor_adapter'] = {
            'transport': 'command', 'name': 'generic-command',
            'command': [PY, '-S', str(script)],
            'capabilities': dict(self.CAPS),
            'capability_evidence': 'https://example.invalid/probe/230'}
        return plan

    def test_generic_command_adapter_runs_a_contracted_stream(self):
        plan = self._generic_plan()
        marker = self.root / 'generic-prompt.txt'
        run = self.run_batch(plan, {'GENERIC_MARKER': str(marker)})
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertIn('TASK_ID=T1', marker.read_text(encoding='utf-8'))
        state = self.journal_data()
        self.assertEqual(state['adapter']['name'], 'generic-command')
        record = state['tasks']['T1']
        self.assertEqual(record['status'], 'INTEGRATED')
        self.assertEqual(record['execution_session'], 'generic-session-1')
        self.assertIsInstance(
            record['attempt_history'][0]['executor']['pid'], int)

    def test_generic_command_adapter_blocks_independent_review(self):
        plan = self._generic_plan()
        marker = self.root / 'generic-prompt.txt'
        run = self.run_batch(plan, {'GENERIC_MARKER': str(marker)})
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        done = self.finalize(plan, {'GENERIC_MARKER': str(marker)}, '--review')
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
        self.assertIn('no independent review evidence provider', done.stderr)
        self.assertFalse(any('pr merge' in call for call in self.gh_calls()))

    # -- selection and preflight ---------------------------------------
    def test_explicit_adapter_requires_capabilities(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['executor_adapter'] = {'transport': 'attached', 'name': 'host'}
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn('capabilities', run.stderr)
        self.assertFalse((self.root / 'wt-t1').exists())
        self.assertFalse(self.journal.exists())

    def test_missing_explicit_command_is_reported_before_worktrees(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        plan['executor_adapter'] = {
            'transport': 'command', 'name': 'generic-command',
            'command': [str(self.root / 'no-such-executable')],
            'capabilities': dict(self.CAPS),
            'capability_evidence': 'https://example.invalid/probe/230'}
        run = self.run_batch(plan)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn('not available', run.stderr)
        self.assertFalse((self.root / 'wt-t1').exists())
        self.assertFalse(self.journal.exists())

    def test_journal_refuses_a_mid_batch_adapter_switch(self):
        plan = self.plan([self.task('T1', 'a.txt')])
        self.assertEqual(self.run_batch(plan).returncode, 0)
        state = self.journal_data()
        state['adapter'] = {'transport': 'attached', 'name': 'other',
                            'source': 'plan'}
        self.journal.write_text(json.dumps(state))
        again = self.cli('run', '--plan', str(self.write_plan(plan)),
                         '--journal', str(self.journal), '--execute')
        self.assertEqual(again.returncode, 2, again.stderr)
        self.assertIn('different executor adapter', again.stderr)


if __name__ == '__main__':
    unittest.main()
