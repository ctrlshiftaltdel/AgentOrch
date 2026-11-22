"""Integration tests for the batch delivery coordinator.

Every test builds a real temporary Git repository with a bare origin, creates
fake ``opencode``/``gh`` executables and runs the CLI in a subprocess. The fake
OpenCode writes real files into real worktrees, the fake ``gh`` performs real
local Git merges into the bare origin, and the coordinator itself performs the
real worktree creation, commits, merges and verification. No network, no model
call, no third-party import.
"""
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
        path.write_text(f'{{ident}}\n')
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
    issues = state.get('issues')
    if issues is None:
        issues = [state.get('issue', {{}})]
    out([{{'number': item.get('number'), 'body': item.get('body') or ''}}
         for item in issues
         if str(item.get('state', 'OPEN')).upper() == 'OPEN'])
    raise SystemExit(0)

if args[:2] == ['issue', 'view']:
    state = load()
    out(state.get('issue', {{}}))
    raise SystemExit(0)

if args[:2] == ['issue', 'comment']:
    state = load()
    state.setdefault('issue', {{}}).setdefault('comments', []).append(
        body_from_stdin())
    save(state)
    print('commented')
    raise SystemExit(0)

if args[:2] == ['issue', 'edit']:
    if os.environ.get('FAKE_GH_FAIL_ISSUE_EDIT') == '1':
        print('issue edit failed by test', file=sys.stderr)
        raise SystemExit(1)
    state = load()
    state.setdefault('issue', {{}})['body'] = body_from_stdin()
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
        if os.name == 'nt':
            # Owner decision A: no Windows reuse before a Job Object (HD-2 B).
            self.assertEqual(second.returncode, 1, second.stderr)
            self.assertEqual(state['tasks']['T1']['status'], 'FAILED')
            self.assertEqual(state['tasks']['T1']['attempts'], 1)
            self.assertIn('retry_blocked', state['tasks']['T1'])
            self.assertEqual(state['tasks']['T2']['status'], 'SKIPPED')
            return
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
        self.assertIn(('post', ('<python>', 'scripts/orchestrator.py', 'backup')),
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
        if os.name == 'nt':
            # Owner decision A: Windows reuse waits for a Job Object (HD-2 B).
            self.assertEqual(second.returncode, 1, second.stderr)
            self.assertEqual(state['tasks']['T1']['status'], 'FAILED')
            self.assertEqual(state['tasks']['T1']['attempts'], 1)
            self.assertIn('retry_blocked', state['tasks']['T1'])
            self.assertEqual(state['tasks']['T2']['status'], 'SKIPPED')
            return
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
        if os.name == 'nt':
            # Owner decision A: an unknown liveness blocks Windows resume.
            self.assertEqual(second.returncode, 1, second.stderr)
            self.assertEqual(state['tasks']['T1']['status'], 'BLOCKED')
            self.assertEqual(state['tasks']['T1']['attempts'], 1)
            self.assertIn('cannot reuse', state['tasks']['T1']['error'])
            return
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
        # The fake executor read the journal at spawn time: the attempt record
        # (and its unique id) was already on disk, before the worktree was used.
        seen = json.loads(
            Path(str(self.journal) + '.attempt').read_text(encoding='utf-8'))
        self.assertEqual(seen['attempts'], 1)
        self.assertEqual(seen['history'][0]['state'], 'STARTED')
        self.assertTrue(seen['history'][0]['id'].startswith('attempt-'))
        self.assertEqual(seen['history'][0]['verifiers'], [])

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
        if os.name == 'nt':
            # Owner decision A: no Windows retry before a Job Object (HD-2 B).
            self.assertEqual(second.returncode, 1, second.stderr)
            self.assertEqual(record['attempts'], 1)
            self.assertEqual(record['attempt_history'][0]['id'], old_id)
            self.assertEqual(json.dumps(record['attempt_history'][0],
                                        sort_keys=True), snapshot)
            self.assertIn('retry_blocked', record)
            return
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
        if os.name == 'nt':
            # Owner decision A: the reuse guard blocks before the dirty
            # worktree is inspected; no new attempt is appended.
            self.assertEqual(record['attempts'], 1)
            self.assertIn('retry_blocked', record)
            return
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
        if os.name == 'nt':
            # R-F1: the root is gone and an orphaned intermediate process cannot
            # be excluded without a Job Object, so Windows refuses to resume.
            self.assertEqual(resumed.returncode, 1, resumed.stderr)
            self.assertEqual(record['status'], 'BLOCKED')
            self.assertEqual(record['attempts'], 1)
            self.assertIn('cannot reuse', record['error'])
            return
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
            # Owner decision A: a gone Windows root is unproven, even with a
            # recorded proof, until a Job Object (HD-2 B) exists. The exact
            # message depends on whether the pid was reused, but reuse is
            # always refused.
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
        if os.name == 'nt':
            # Owner decision A: no Windows retry before a Job Object (HD-2 B).
            self.assertEqual(retry.returncode, 1, retry.stderr)
            self.assertFalse((self.root / 'wt-t1').exists())
            self.assertEqual(record['attempts'], 1)
            self.assertEqual(record['status'], 'FAILED')
            self.assertIn('retry_blocked', record)
            return
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
            'artifacts': [{'path': str(artifact),
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
            'workdir': workdir or str(self.root / 'wt-batch'),
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
        self.assertEqual(request['worktree'], str(self.root / 'wt-t1'))
        self.assertEqual(request['branch'], 'task/batch-t1')
        self.assertEqual(request['paths'], ['a.txt'])
        self.assertIn('TASK_ID=T1', request['prompt'])
        self.assertIn('PATHS_JSON=["a.txt"]', request['prompt'])
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
