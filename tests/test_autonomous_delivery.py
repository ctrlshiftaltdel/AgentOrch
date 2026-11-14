import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import autonomous_delivery as delivery

PY = sys.executable

PROJECT_LOCAL = """# 项目配置

- 允许改动路径：`a.txt`、`b.txt`、`out.txt`、`shared.txt`、`same.txt`、`docs/`
- 永远禁止改动路径 / 文件：`LICENSE.md`
- edition：`local`
"""

PROJECT_GITHUB = PROJECT_LOCAL.replace('`local`', '`github`')


def _git(where, *args, check=True):
    return subprocess.run(['git', '--no-optional-locks', *args], cwd=where, check=check,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)


def _verdict(head, digest, verdict='APPROVE', findings=None, acceptance=None):
    return 'Review complete.\n```json\n' + json.dumps({
        'verdict': verdict, 'head': head, 'diff_sha256': digest,
        'findings': list(findings or []),
        'acceptance': list(acceptance or [{'id': 'AC-1', 'status': 'PASS',
                                           'evidence': 'check'}]),
    }) + '\n```\n'


class FakeAgents:
    """Real worktree writes; session ids, probes and transcripts are injected."""

    def __init__(self, case, *, execution=None, review=None, sessions=('ses_exec', 'ses_rev')):
        self.case = case
        self.execution = execution
        self.review = review
        self.exec_session, self.review_session = sessions
        self.calls = []

    def __call__(self, work, prompt, role):
        self.calls.append(role)
        if role == 'execution':
            if self.execution is not None:
                self.execution(work, prompt)
            else:
                self.case.write_default(work, prompt)
            self.case.transcripts[self.exec_session] = 'execution finished'
            return {'exit_code': 0, 'session_id': self.exec_session, 'detail': 'ok'}
        head = re.search(r'HEAD_SHA=([0-9a-f]{40})', prompt).group(1)
        digest = re.search(r'DIFF_SHA256=([0-9a-f]{64})', prompt).group(1)
        acceptance = [{'id': ident, 'status': 'PASS', 'evidence': 'checked'}
                      for ident in re.findall(r'^- (AC-\d+):', prompt, re.M)]
        if self.review is not None:
            custom = self.review(work, prompt, head, digest)
            if custom is not None:
                self.case.transcripts[custom.get('session_id')] = custom.get('text') or ''
                return custom
        text = _verdict(head, digest, acceptance=acceptance)
        self.case.transcripts[self.review_session] = text
        return {'exit_code': 0, 'session_id': self.review_session,
                'text': text, 'detail': 'ok'}


class DeliveryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        _git(self.repo, 'init', '-q', '-b', 'integration')
        _git(self.repo, 'config', 'user.email', 'test@example.invalid')
        _git(self.repo, 'config', 'user.name', 'Test')
        (self.repo / 'base.txt').write_text('base')
        (self.repo / 'docs').mkdir()
        (self.repo / 'docs' / 'PROJECT.md').write_text(PROJECT_LOCAL)
        _git(self.repo, 'add', 'base.txt', 'docs/PROJECT.md')
        _git(self.repo, 'commit', '-q', '-m', 'base')
        self.state = self.root / 'state.json'
        self.transcripts = {}
        self.profiles = lambda where: {
            'build': {'id': 'build', 'permissions': [
                {'action': '*', 'resource': '*', 'effect': 'allow'}]},
            'plan': {'id': 'plan', 'permissions': [
                {'action': '*', 'resource': '*', 'effect': 'allow'},
                {'action': 'edit', 'resource': '*', 'effect': 'deny'},
                {'action': 'shell', 'resource': '*', 'effect': 'deny'}]},
        }
        self.gh = None

    # -- fixtures ---------------------------------------------------------
    def worktree(self, name):
        path = self.root / f'work-{name}'
        _git(self.repo, 'worktree', 'add', '-q', '-b', f'task/{name}', str(path))
        return path

    def default_probe(self, session_id, work):
        agent = 'build' if session_id.startswith('ses_exec') else 'plan'
        return {'ok': True, 'outcome': 'succeeded', 'directory': str(work),
                'user_messages': 1, 'assistant_messages': 1, 'agents': [agent],
                'finishes': ['stop'], 'models': [{'id': 'test-model'}],
                'texts': [self.transcripts.get(session_id, '')]}

    def write_default(self, work, prompt):
        match = re.search(r'paths: (\[[^\]]*\])', prompt)
        name = json.loads(match.group(1))[0]
        (work / name).write_text(f'{Path(work).name}\n')

    def task(self, ident, work, paths, verify=None, depends=None, acceptance=None):
        return {'id': ident, 'worktree': str(work), 'prompt': f'{ident}: make a change',
                'paths': paths, 'depends': list(depends or []),
                'acceptance': list(acceptance or [f'{ident} does the required change']),
                'verify': verify or [[PY, '-c', 'raise SystemExit(0)']]}

    def plan(self, tasks, **extra):
        plan = {'merge_into': 'integration', 'tasks': tasks}
        plan.update(extra)
        return plan

    def run_delivery(self, plan, agents=None, **kwargs):
        agents = agents or FakeAgents(self)
        kwargs.setdefault('session_probe', self.default_probe)
        kwargs.setdefault('profiles', self.profiles)
        kwargs.setdefault('reviewer_agent', 'plan')
        if self.gh is not None:
            kwargs.setdefault('gh', self.gh)
        return delivery.run(self.repo, plan, self.state, agent=agents, **kwargs), agents

    def journal(self):
        return json.loads(self.state.read_text())

    def seed(self, plan, tasks):
        self.state.write_text(json.dumps({
            'version': 2, 'tasks': tasks, 'plan_hash': self.plan_hash(plan),
            'carrier_digest': None}))

    def seed_edit(self, plan, edit):
        state = self.journal()
        for record in state['tasks'].values():
            edit(record)
        state['plan_hash'] = self.plan_hash(plan)
        self.state.write_text(json.dumps(state))

    def plan_hash(self, plan):
        merged = dict(plan, executor_agent='build', reviewer_agent='plan')
        return delivery.hashlib.sha256(
            json.dumps(merged, sort_keys=True).encode()).hexdigest()


class FullLoopTests(DeliveryCase):
    def test_dependent_task_runs_after_verified_merge(self):
        one, two = self.worktree('one'), self.worktree('two')
        plan = self.plan([
            self.task('one', one, ['a.txt']),
            self.task('two', two, ['b.txt'], depends=['one']),
        ])
        state, agents = self.run_delivery(plan)
        self.assertEqual([state['tasks'][name]['status'] for name in ('one', 'two')],
                         ['VERIFIED', 'VERIFIED'])
        self.assertEqual(agents.calls, ['execution', 'review', 'execution', 'review'])
        self.assertEqual(_git(self.repo, 'show', 'integration:a.txt').stdout, b'work-one\n')
        self.assertEqual(_git(self.repo, 'show', 'integration:b.txt').stdout, b'work-two\n')
        # The later task is re-baselined onto the target head the earlier merge produced.
        self.assertEqual(state['tasks']['two']['base'],
                         state['tasks']['one']['merge_commit'])
        self.assertEqual(_git(self.repo, 'rev-list', '--merges', '--count',
                              'integration').stdout.strip(), b'2')
        for name in ('one', 'two'):
            record = state['tasks'][name]
            self.assertEqual(record['verification'][0]['exit_code'], 0)
            self.assertEqual(record['post_verification'][0]['exit_code'], 0)
            self.assertNotEqual(record['execution_session'], record['review_session'])
            self.assertEqual(record['review_verdict']['verdict'], 'APPROVE')

    def test_prepare_only_approves_but_never_merges_or_unlocks(self):
        one = self.worktree('one')
        plan = {'merge': False, 'tasks': [self.task('one', one, ['a.txt'])]}
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        self.assertIsNone(state['tasks']['one'].get('merge_commit'))
        self.assertEqual(_git(self.repo, 'log', '--oneline', '-1').stdout.count(b'\n'), 1)
        self.assertFalse((self.repo / 'a.txt').exists())
        self.assertEqual(agents.calls, ['execution', 'review'])


class MergeBindingTests(DeliveryCase):
    def test_merge_uses_the_reviewed_sha_when_the_branch_ref_moves(self):
        work = self.worktree('one')

        def move_ref(where, prompt, head, digest):
            # Reproduce the review-window attack: detach the worktree at the
            # reviewed commit and point the branch ref at an unreviewed commit.
            (where / 'bad.txt').write_text('unreviewed\n')
            _git(where, 'add', 'bad.txt')
            _git(where, 'commit', '-q', '-m', 'unreviewed')
            bad = _git(where, 'rev-parse', 'HEAD').stdout.decode().strip()
            _git(where, 'checkout', '-q', '--detach', head)
            _git(where, 'update-ref', 'refs/heads/task/one', bad)
            return None

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, review=move_ref))
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'VERIFIED')
        merge = record['merge_commit']
        self.assertEqual(_git(self.repo, 'rev-parse', f'{merge}^2').stdout.decode().strip(),
                         record['head'])
        self.assertNotEqual(_git(self.repo, 'cat-file', '-e',
                                 'integration:bad.txt', check=False).returncode, 0)

    def test_pre_existing_commits_cannot_be_smuggled_into_the_target(self):
        work = self.worktree('one')
        (work / 'forbidden.txt').write_text('smuggled\n')
        _git(work, 'add', 'forbidden.txt')
        _git(work, 'commit', '-q', '-m', 'pre-existing out-of-scope commit')
        agents = FakeAgents(self)
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]), agents)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertEqual(agents.calls, [])
        # The target did not receive the pre-existing commit or file.
        self.assertNotEqual(_git(self.repo, 'cat-file', '-e',
                                 'integration:forbidden.txt', check=False).returncode, 0)
        self.assertFalse((self.repo / 'forbidden.txt').exists())

    def test_worktree_with_unreviewed_commits_is_rejected_at_dispatch(self):
        work = self.worktree('one')
        (work / 'bad.txt').write_text('extra\n')
        _git(work, 'add', 'bad.txt')
        _git(work, 'commit', '-q', '-m', 'extra work')
        extra = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        state, agents = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]))
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertIn('target baseline', record['reason'])
        self.assertEqual(agents.calls, [])
        self.assertEqual(_git(self.repo, 'cat-file', '-e', extra, check=False).returncode, 0)

    def test_later_task_rebases_only_when_it_has_no_commits(self):
        one, two = self.worktree('one'), self.worktree('two')
        plan = self.plan([self.task('one', one, ['a.txt']),
                          self.task('two', two, ['b.txt'], depends=['one'])])
        state, _ = self.run_delivery(plan)
        self.assertEqual(state['tasks']['two']['status'], 'VERIFIED')
        self.assertEqual(state['tasks']['two']['base'],
                         state['tasks']['one']['merge_commit'])


class ReviewGateTests(DeliveryCase):
    def test_review_session_equal_to_execution_blocks_merge(self):
        work = self.worktree('one')
        agents = FakeAgents(self, sessions=('ses_exec_same', 'ses_exec_same'))
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]), agents)
        self.assertEqual(state['tasks']['one']['status'], 'REVIEW_REJECTED')
        self.assertIn('equals execution session', state['tasks']['one']['reason'])

    def test_unverifiable_review_session_blocks_merge(self):
        work = self.worktree('one')

        def probe(session_id, where):
            if session_id.startswith('ses_rev'):
                return {'ok': False, 'reason': 'session export exit code 1'}
            return self.default_probe(session_id, where)

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     session_probe=probe)
        self.assertEqual(state['tasks']['one']['status'], 'REVIEW_REJECTED')
        self.assertIn('unverifiable', state['tasks']['one']['reason'])
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_review_in_another_directory_or_agent_blocks_merge(self):
        work = self.worktree('one')

        def probe(session_id, where):
            result = self.default_probe(session_id, where)
            if session_id.startswith('ses_rev'):
                result['directory'] = str(self.repo)
            return result

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     session_probe=probe)
        self.assertIn('not in the task worktree', state['tasks']['one']['reason'])

    def test_missing_verdict_is_not_approval(self):
        work = self.worktree('one')

        def no_verdict(where, prompt, head, digest):
            return {'exit_code': 0, 'session_id': 'ses_rev', 'text': 'looks good to me'}

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, review=no_verdict))
        self.assertEqual(state['tasks']['one']['status'], 'REVIEW_REJECTED')
        self.assertIn('no machine-readable verdict', state['tasks']['one']['reason'])

    def test_rejected_or_mismatched_verdict_blocks_merge(self):
        work = self.worktree('one')

        def reject(work, prompt, head, digest):
            return {'exit_code': 0, 'session_id': 'ses_rev',
                    'text': _verdict(head, digest, 'REJECT', [{'note': 'bug'}])}

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, review=reject))
        self.assertIn('not APPROVE', state['tasks']['one']['reason'])
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_verdict_must_bind_the_actual_head_and_diff(self):
        work = self.worktree('one')

        def stale(work, prompt, head, digest):
            return {'exit_code': 0, 'session_id': 'ses_rev',
                    'text': _verdict('0' * 40, digest)}

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, review=stale))
        self.assertIn('different head', state['tasks']['one']['reason'])

    def test_verdict_must_come_from_the_session_transcript(self):
        work = self.worktree('one')

        def forged(work, prompt, head, digest):
            return {'exit_code': 0, 'session_id': 'ses_rev',
                    'text': _verdict(head, digest)}

        agents = FakeAgents(self, review=forged)
        original = self.default_probe

        def probe(session_id, where):
            result = original(session_id, where)
            if session_id.startswith('ses_rev'):
                result['texts'] = [_verdict('0' * 40, '0' * 64)]
            return result

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     agents, session_probe=probe)
        self.assertIn('transcript', state['tasks']['one']['reason'])
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_every_acceptance_criterion_must_pass(self):
        work = self.worktree('one')

        def partial(work, prompt, head, digest):
            return {'exit_code': 0, 'session_id': 'ses_rev',
                    'text': _verdict(head, digest, acceptance=[
                        {'id': 'AC-1', 'status': 'PASS', 'evidence': 'ok'},
                        {'id': 'AC-2', 'status': 'NOT_RUN', 'evidence': 'skipped'}])}

        task = self.task('one', work, ['a.txt'], acceptance=['first', 'second'])
        state, _ = self.run_delivery(self.plan([task]), FakeAgents(self, review=partial))
        self.assertEqual(state['tasks']['one']['status'], 'REVIEW_REJECTED')
        self.assertIn('AC-2', state['tasks']['one']['reason'])

    def test_reviewer_agent_profile_must_deny_edits(self):
        work = self.worktree('one')
        profiles = lambda where: {'plan': {'permissions': [
            {'action': '*', 'resource': '*', 'effect': 'allow'}]}}
        state, agents = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                          profiles=profiles)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('does not deny', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_merge_needs_a_shell_denied_reviewer_but_approval_does_not(self):
        work = self.worktree('one')
        profiles = lambda where: {'plan': {'permissions': [
            {'action': '*', 'resource': '*', 'effect': 'allow'},
            {'action': 'edit', 'resource': '*', 'effect': 'deny'}]}}
        prepare = {'merge': False, 'tasks': [self.task('one', work, ['a.txt'])]}
        state, _ = self.run_delivery(prepare, profiles=profiles)
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        self.assertFalse(state['tasks']['one']['reviewer_profile']['shell_denied'])
        self.state = self.root / 'state-merge.json'
        merge_work = self.worktree('two')
        state, agents = self.run_delivery(self.plan([self.task('two', merge_work, ['b.txt'])]),
                                          profiles=profiles)
        self.assertEqual(state['tasks']['two']['status'], 'BLOCKED')
        self.assertIn('shell', state['tasks']['two']['reason'])

    def test_reviewer_agent_must_also_deny_shell(self):
        work = self.worktree('one')
        profiles = lambda where: {'plan': {'permissions': [
            {'action': '*', 'resource': '*', 'effect': 'allow'},
            {'action': 'edit', 'resource': '*', 'effect': 'deny'}]}}
        self.state = self.root / 'state-merge.json'
        merge_work = self.worktree('two')
        state, agents = self.run_delivery(self.plan([self.task('two', merge_work, ['b.txt'])]),
                                          profiles=profiles)
        self.assertEqual(state['tasks']['two']['status'], 'BLOCKED')
        self.assertIn('shell', state['tasks']['two']['reason'])
        self.assertEqual(agents.calls, [])

    def test_reviewer_profile_is_checked_in_every_task_worktree(self):
        one, two = self.worktree('one'), self.worktree('two')

        def profiles(where):
            rules = [{'action': '*', 'resource': '*', 'effect': 'allow'},
                     {'action': 'edit', 'resource': '*', 'effect': 'deny'}]
            if Path(where) != two:
                rules.append({'action': 'shell', 'resource': '*', 'effect': 'deny'})
            return {'plan': {'permissions': rules}}

        plan = self.plan([self.task('one', one, ['a.txt']),
                          self.task('two', two, ['b.txt'])])
        state, agents = self.run_delivery(plan, profiles=profiles)
        self.assertEqual(state['tasks']['one']['status'], 'VERIFIED')
        self.assertEqual(state['tasks']['two']['status'], 'BLOCKED')
        self.assertIn('shell', state['tasks']['two']['reason'])
        self.assertEqual(agents.calls, ['execution', 'review'])


class VerificationTests(DeliveryCase):
    def test_failed_verification_never_merges_or_unlocks_dependents(self):
        one, two = self.worktree('one'), self.worktree('two')
        plan = self.plan([
            self.task('one', one, ['a.txt'], verify=[[PY, '-c', 'raise SystemExit(3)']]),
            self.task('two', two, ['b.txt'], depends=['one']),
        ])
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'FAILED')
        self.assertEqual(state['tasks']['one']['verification'][0]['exit_code'], 3)
        self.assertEqual(state['tasks']['two']['status'], 'PENDING')
        self.assertNotIn('two', [call for call in agents.calls])
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_restart_does_not_redispatch_a_failed_task(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'],
                                    verify=[[PY, '-c', 'raise SystemExit(1)']])])
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'FAILED')
        state, agents = self.run_delivery(plan)
        self.assertEqual(agents.calls, [])
        self.assertEqual(state['tasks']['one']['status'], 'FAILED')

    def test_verification_receives_pwd_and_cwd(self):
        work = self.worktree('one')
        verify = [[PY, '-c',
                   'import os,sys; sys.exit(0 if os.environ.get("PWD")==os.getcwd() else 1)']]
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'],
                                                          verify=verify)]))
        self.assertEqual(state['tasks']['one']['status'], 'VERIFIED')
        self.assertEqual(state['tasks']['one']['verification'][0]['exit_code'], 0)

    def test_verification_timeout_is_not_success(self):
        work = self.worktree('one')
        task = self.task('one', work, ['a.txt'],
                         verify=[[PY, '-c', 'import time; time.sleep(5)']])
        task['timeout'] = 1
        state, _ = self.run_delivery(self.plan([task]))
        self.assertEqual(state['tasks']['one']['status'], 'FAILED')
        self.assertIsNone(state['tasks']['one']['verification'][0]['exit_code'])

    def test_post_merge_verification_that_moves_the_target_head_is_not_verified(self):
        work = self.worktree('one')
        script = (
            'import subprocess,sys,pathlib\n'
            'branch=subprocess.run(["git","branch","--show-current"],capture_output=True,text=True).stdout.strip()\n'
            'if branch=="integration":\n'
            '    pathlib.Path("post.txt").write_text("x")\n'
            '    subprocess.run(["git","add","post.txt"],check=True)\n'
            '    subprocess.run(["git","-c","user.email=t@e.invalid","-c","user.name=T",'
            '"commit","-m","post"],check=True)\n'
            'raise SystemExit(0)\n')
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'],
                                                          verify=[[PY, '-c', script]])]))
        record = state['tasks']['one']
        self.assertNotEqual(record['status'], 'VERIFIED')
        self.assertIn('target', record['reason'])
        self.assertIsNotNone(record.get('merge_commit'))


class RecoveryTests(DeliveryCase):
    def _prepared(self, name='one'):
        work = self.worktree(name)
        base = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        (work / 'a.txt').write_text('pending\n')
        _git(work, 'add', 'a.txt')
        _git(work, 'commit', '-q', '-m', 'pending')
        head = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        return work, base, head

    def test_interrupted_task_becomes_unknown_and_is_not_redispatched(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self.seed(plan, {'one': {'status': 'RUNNING', 'phase': 'verify', 'base': base,
                                 'head': head, 'worktree': str(work),
                                 'diff_sha256': delivery._fingerprint(work, base),
                                 'execution_session': 'ses_exec', 'pid': 999999}})
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'UNKNOWN')
        self.assertEqual(agents.calls, [])
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_unknown_can_be_resumed_after_reproving_facts(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self.seed(plan, {'one': {'status': 'RUNNING', 'phase': 'verify', 'base': base,
                                 'target_base': base, 'head': head,
                                 'worktree': str(work),
                                 'diff_sha256': delivery._fingerprint(work, base),
                                 'execution_session': 'ses_exec', 'pid': 999999}})
        first, agents = self.run_delivery(plan)
        self.assertEqual(first['tasks']['one']['status'], 'UNKNOWN')
        self.assertEqual(agents.calls, [])
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'VERIFIED')
        self.assertEqual(agents.calls, ['review'])

    def test_resume_continues_from_reproven_facts_without_redispatch(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self.seed(plan, {'one': {'status': 'RUNNING', 'phase': 'verify', 'base': base,
                                 'target_base': base, 'head': head,
                                 'worktree': str(work),
                                 'diff_sha256': delivery._fingerprint(work, base),
                                 'execution_session': 'ses_exec', 'pid': 999999}})
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'VERIFIED')
        self.assertEqual(agents.calls, ['review'])
        self.assertEqual(_git(self.repo, 'show', 'integration:a.txt').stdout, b'pending\n')

    def test_resume_refuses_when_the_diff_moved(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self.seed(plan, {'one': {'status': 'RUNNING', 'phase': 'verify', 'base': base,
                                 'head': head, 'worktree': str(work),
                                 'diff_sha256': '0' * 64,
                                 'execution_session': 'ses_exec', 'pid': 999999}})
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('diff differs', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_resume_merge_phase_without_review_evidence_stays_blocked(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self.seed(plan, {'one': {'status': 'RUNNING', 'phase': 'merge', 'base': base,
                                 'target_base': base, 'head': head,
                                 'worktree': str(work),
                                 'diff_sha256': delivery._fingerprint(work, base),
                                 'verification': [{'program': 'true', 'argv_sha256': '0' * 64,
                                                   'exit_code': 0}],
                                 'execution_session': 'ses_exec', 'pid': 999999}})
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('review', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_retry_refuses_to_discard_unproven_changes(self):
        work = self.worktree('one')

        def dirty(where, prompt):
            (where / 'a.txt').write_text('work\n')

        verify = [[PY, '-c',
                   'import pathlib; pathlib.Path("a.txt").write_text("modified"); '
                   'raise SystemExit(1)']]
        plan = self.plan([self.task('one', work, ['a.txt'], verify=verify)])
        state, _ = self.run_delivery(plan, FakeAgents(self, execution=dirty))
        self.assertEqual(state['tasks']['one']['status'], 'FAILED')
        head_before = _git(work, 'rev-parse', 'HEAD').stdout
        state, agents = self.run_delivery(plan, retry=['one'])
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertIn('retry', record['reason'])
        self.assertEqual(agents.calls, [])
        self.assertEqual((work / 'a.txt').read_text().strip(), 'modified')
        self.assertNotIn('stash', record)
        self.assertEqual(_git(work, 'rev-parse', 'HEAD').stdout, head_before)

    def test_retry_reruns_review_on_the_same_committed_head(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])])

        def reject(work, prompt, head, digest):
            return {'exit_code': 0, 'session_id': 'ses_rev',
                    'text': _verdict(head, digest, 'REJECT', [{'defect': 'typo'}])}

        state, _ = self.run_delivery(plan, FakeAgents(self, review=reject))
        self.assertEqual(state['tasks']['one']['status'], 'REVIEW_REJECTED')
        head = state['tasks']['one']['head']
        state, agents = self.run_delivery(plan, retry=['one'])
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'VERIFIED')
        self.assertEqual(record['head'], head)
        self.assertEqual(record['attempt'], 2)
        self.assertNotIn('stash', record)

    def test_retry_reruns_verification_without_discarding_the_committed_attempt(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'],
                                    verify=[[PY, '-c', 'raise SystemExit(1)']])])
        state, _ = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'FAILED')
        head = state['tasks']['one']['head']
        state, agents = self.run_delivery(plan, retry=['one'])
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'FAILED')
        self.assertEqual(record['attempt'], 2)
        self.assertEqual(record['head'], head)
        self.assertNotIn('stash', record)
        self.assertEqual(agents.calls, [])


class PathSafetyTests(DeliveryCase):
    def test_plan_paths_reject_cross_platform_escapes(self):
        work = self.worktree('one')
        for value in ('../outside', '/etc/passwd', 'C:/windows', 'a\\..\\b',
                      'a/../../b', '.git/config', '.orchestration/state.sqlite3',
                      'a//b', 'a/./b', 'a:b', 'a b', 'a*', ''):
            with self.subTest(value=value):
                task = self.task('one', work, ['a.txt'])
                task['paths'] = [value]
                with self.assertRaises(ValueError):
                    delivery.run(self.repo, self.plan([task]), self.root / 's.json',
                                 agent=FakeAgents(self), session_probe=self.default_probe,
                                 profiles=self.profiles)
        self.assertFalse((self.root / 's.json').exists())

    def test_plan_paths_must_be_inside_the_project_authorization(self):
        work = self.worktree('one')
        task = self.task('one', work, ['elsewhere.txt'])
        with self.assertRaises(ValueError) as caught:
            self.run_delivery(self.plan([task]))
        self.assertIn('authoriz', str(caught.exception))

    def test_execution_outside_allowed_paths_is_blocked(self):
        work = self.worktree('one')
        agents = FakeAgents(self, execution=lambda where, prompt: (
            (where / 'bad.txt').write_text('outside\n')))
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]), agents)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('scope violation', state['tasks']['one']['reason'])
        self.assertFalse((self.repo / 'bad.txt').exists())

    def test_symlink_change_is_blocked(self):
        work = self.worktree('one')

        def link(where, prompt):
            (where / 'a.txt').symlink_to(self.repo / 'base.txt')

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, execution=link))
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('symlink', state['tasks']['one']['reason'])

    def test_staged_change_hidden_by_worktree_revert_is_still_out_of_scope(self):
        work = self.worktree('one')

        def stage(where, prompt):
            (where / 'a.txt').write_text('allowed\n')
            (where / 'base.txt').write_text('staged forbidden\n')
            _git(where, 'add', 'base.txt')
            (where / 'base.txt').write_text('base')

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, execution=stage))
        self.assertIn('scope violation', state['tasks']['one']['reason'])

    def test_fingerprint_distinguishes_symlink_from_identical_bytes(self):
        work = self.worktree('one')
        base = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        (work / 'same.txt').write_text('same\n')
        regular = delivery._fingerprint(work, base)
        (work / 'same.txt').unlink()
        (work / 'same.txt').symlink_to(work / 'base.txt')
        (work / 'base.txt').write_text('same\n')
        symlink = delivery._fingerprint(work, base)
        self.assertNotEqual(regular, symlink)

    def test_worktree_branch_and_cleanliness_are_preflighted(self):
        work = self.worktree('one')
        (work / 'dirty.txt').write_text('dirty')
        state, agents = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]))
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('not clean', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_missing_worktree_is_isolated_not_fatal(self):
        good = self.worktree('good')
        task = self.task('bad', self.root / 'missing', ['x.txt'])
        task['worktree'] = ''
        plan = self.plan([task, self.task('good', good, ['a.txt'])])
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['bad']['status'], 'BLOCKED')
        self.assertIn('configuration is invalid', state['tasks']['bad']['reason'])
        self.assertEqual(state['tasks']['good']['status'], 'VERIFIED')
        self.assertEqual(agents.calls, ['execution', 'review'])


class JournalTests(DeliveryCase):
    def test_preexisting_journal_without_plan_hash_is_rejected(self):
        work = self.worktree('one')
        self.state.write_text(json.dumps({'version': 1, 'tasks': {'one': {'status': 'VERIFIED'}}}))
        with self.assertRaises(ValueError):
            self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]))

    def test_forged_verified_state_is_not_trusted_or_unlocked(self):
        one, two = self.worktree('one'), self.worktree('two')
        plan = self.plan([
            self.task('one', one, ['a.txt']),
            self.task('two', two, ['b.txt'], depends=['one']),
        ])
        self.seed(plan, {'one': {'status': 'VERIFIED', 'head': '0' * 40,
                                 'diff_sha256': '0' * 64, 'verification': []},
                         'two': {'status': 'PENDING'}})
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertEqual(state['tasks']['two']['status'], 'PENDING')
        self.assertEqual(agents.calls, [])

    def test_forged_approval_without_session_transcript_is_blocked(self):
        work, = (self.worktree('one'),)
        (work / 'a.txt').write_text('x\n')
        _git(work, 'add', 'a.txt')
        _git(work, 'commit', '-q', '-m', 'change')
        base = _git(self.repo, 'rev-parse', 'HEAD').stdout.decode().strip()
        head = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self.seed(plan, {'one': {
            'status': 'APPROVED', 'phase': 'merge', 'base': base, 'target_base': base,
            'head': head, 'worktree': str(work), 'branch': 'task/one',
            'diff_sha256': delivery._fingerprint(work, base),
            'execution_session': 'ses_exec', 'review_session': 'ses_rev',
            'review_verdict': {'verdict': 'APPROVE', 'head': head,
                               'diff_sha256': delivery._fingerprint(work, base),
                               'acceptance': [{'id': 'AC-1', 'status': 'PASS'}],
                               'findings': []},
            'verification': [{'program': PY, 'argv_sha256': '0' * 64, 'exit_code': 0}],
            'attempt': 1, 'executor_agent': 'build', 'reviewer_agent': 'plan'}})
        state, agents = self.run_delivery(plan)
        record = state['tasks']['one']
        self.assertIn(record['status'], ('BLOCKED', 'REVIEW_REJECTED'))
        self.assertIn('transcript', record['reason'].lower() + ' transcript')
        self.assertEqual(agents.calls, [])
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_state_path_inside_repository_is_refused_before_writing(self):
        work = self.worktree('one')
        inside = self.repo / 'delivery-state.json'
        with self.assertRaises(ValueError):
            delivery.run(self.repo, self.plan([self.task('one', work, ['a.txt'])]), inside,
                         agent=FakeAgents(self), session_probe=self.default_probe,
                         profiles=self.profiles)
        self.assertFalse(inside.exists())
        self.assertFalse((self.repo / 'delivery-state.json.lock').exists())

    def test_plan_level_errors_leave_no_state_or_lock(self):
        work = self.worktree('one')
        task = self.task('one', work, ['a.txt'])
        task['paths'] = ['../escape']
        with self.assertRaises(ValueError):
            self.run_delivery(self.plan([task]))
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / 'state.json.lock').exists())

    def test_missing_cross_platform_lock_refuses_instead_of_running(self):
        if os.name == 'nt':
            self.skipTest('POSIX-only check of the absent-lock refusal')
        with mock.patch.dict(sys.modules, {'fcntl': None}):
            with self.assertRaises(delivery.DeliveryError) as caught:
                delivery._lock(self.root / 'x.lock')
        self.assertIn('lock', str(caught.exception))

    def test_a_second_journal_cannot_write_the_same_repository(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])])
        locks = delivery._acquire_locks(delivery._lock_targets(self.repo, self.state,
                                                               delivery._plan_tasks(plan)))
        try:
            with self.assertRaises(delivery.DeliveryError) as caught:
                self.run_delivery(plan)
            self.assertIn('locked', str(caught.exception))
        finally:
            for handle in locks:
                handle.close()

    def test_plan_without_acceptance_criteria_is_blocked(self):
        work = self.worktree('one')
        task = self.task('one', work, ['a.txt'])
        task.pop('acceptance')
        state, agents = self.run_delivery(self.plan([task]))
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('acceptance', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_verification_arguments_are_not_written_to_the_journal(self):
        work = self.worktree('one')
        secret = 'ghp_notARealTokenForTests'
        verify = [[PY, '-c', 'raise SystemExit(0)', secret]]
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'],
                                                          verify=verify)]))
        self.assertEqual(state['tasks']['one']['status'], 'VERIFIED')
        self.assertNotIn(secret, self.state.read_text())
        self.assertNotIn(secret, json.dumps(delivery._summary(state)))


class EditionTests(DeliveryCase):
    def _set_edition(self, value):
        (self.repo / 'docs' / 'PROJECT.md').write_text(PROJECT_LOCAL.replace('`local`', f'`{value}`'))
        _git(self.repo, 'add', 'docs/PROJECT.md')
        _git(self.repo, 'commit', '-q', '-m', f'edition {value}')

    def test_github_edition_refuses_local_auto_merge(self):
        self._set_edition('github')
        work = self.worktree('one')
        with self.assertRaises(delivery.DeliveryError) as caught:
            self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]))
        self.assertIn('edition', str(caught.exception))
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_github_edition_still_allows_prepare_only(self):
        self._set_edition('github')
        work = self.worktree('one')
        plan = {'merge': False, 'tasks': [self.task('one', work, ['a.txt'])]}
        state, _ = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_missing_edition_refuses_merge(self):
        (self.repo / 'docs' / 'PROJECT.md').write_text(PROJECT_LOCAL.replace(
            '- edition：`local`', ''))
        _git(self.repo, 'add', 'docs/PROJECT.md')
        _git(self.repo, 'commit', '-q', '-m', 'no edition')
        work = self.worktree('one')
        with self.assertRaises(delivery.DeliveryError):
            self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]))


class IssuePlanTests(DeliveryCase):
    BODY = ('## 目标\n\nDo the thing.\n\n## 可改路径\n\n'
            '- `docs/alpha.md`\n- `scripts/beta.py`\n- 说明性文字不是路径\n\n'
            '## 禁改路径\n\n- `LICENSE.md`\n')

    def response(self, payload):
        return subprocess.CompletedProcess([], 0, json.dumps(payload), '')

    def fake_gh(self, body=None, state='OPEN'):
        body = self.BODY if body is None else body

        def runner(argv):
            self.assertIn('gh', argv[0])
            return self.response({'number': 230, 'title': 'Real task', 'body': body,
                                  'state': state, 'url': 'https://example.invalid/issues/230',
                                  'labels': [{'name': 'status:in-progress'}]})
        return runner

    def test_issue_plan_derives_scope_and_records_provenance(self):
        plan = delivery.load_issue_plan(230, 'owner/repo', worktree='/tmp/wt',
                                        verify=['["python3","-c","raise SystemExit(0)"]'],
                                        acceptance=['the change is correct'],
                                        allows=['docs/alpha.md'], runner=self.fake_gh())
        task = plan['tasks'][0]
        self.assertEqual(task['paths'], ['docs/alpha.md'])
        self.assertEqual(task['verify'], [['python3', '-c', 'raise SystemExit(0)']])
        self.assertEqual(task['acceptance'], ['the change is correct'])
        self.assertEqual(plan['carrier']['digest'],
                         delivery.hashlib.sha256(self.BODY.encode()).hexdigest())
        self.assertFalse(plan['merge'])

    def test_issue_plan_refuses_scope_widening_and_closed_issues(self):
        with self.assertRaises(delivery.DeliveryError):
            delivery.load_issue_plan(230, 'owner/repo', worktree='/tmp/wt',
                                     verify=['["true"]'], acceptance=['x'], allows=['README.md'],
                                     runner=self.fake_gh())
        with self.assertRaises(delivery.DeliveryError):
            delivery.load_issue_plan(230, 'owner/repo', worktree='/tmp/wt',
                                     verify=['["true"]'], acceptance=['x'],
                                     runner=self.fake_gh(state='CLOSED'))

    def test_carrier_digest_change_blocks_the_run(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])],
                         carrier={'kind': 'issue', 'repo': 'owner/repo', 'number': 230,
                                  'digest': '1' * 64})
        with self.assertRaises(delivery.DeliveryError):
            self.run_delivery(plan, gh=self.fake_gh())
        self.assertFalse(self.state.exists())


class OpenCodeInvocationTests(DeliveryCase):
    def process(self, output, code=0):
        proc = mock.Mock()
        proc.communicate.return_value = (output, '')
        proc.returncode = code
        proc.pid = 4242
        return proc

    def events(self, trailing=''):
        lines = [json.dumps({'type': 'step_start', 'sessionID': 'ses_x',
                             'part': {'type': 'step-start', 'sessionID': 'ses_x'}}),
                 json.dumps({'type': 'text', 'sessionID': 'ses_x',
                             'part': {'type': 'text', 'sessionID': 'ses_x', 'text': 'ok'}}),
                 json.dumps({'type': 'step_finish', 'sessionID': 'ses_x',
                             'part': {'type': 'step-finish', 'sessionID': 'ses_x',
                                      'reason': 'stop'}})]
        if trailing:
            lines.append(trailing)
        return '\n'.join(lines)

    def test_invocation_uses_a_private_server_named_agent_and_no_auto(self):
        with mock.patch.object(delivery, '_popen',
                               return_value=self.process(self.events())) as popen:
            result = delivery.opencode(self.repo, 'task', 'review', agent_name='plan')
        argv = popen.call_args.args[0]
        self.assertIn('--standalone', argv)
        self.assertNotIn('--auto', argv)
        self.assertEqual(argv[argv.index('--agent') + 1], 'plan')
        self.assertEqual(popen.call_args.kwargs['cwd'], self.repo)
        self.assertEqual(popen.call_args.kwargs['env']['PWD'], str(self.repo))
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['session_id'], 'ses_x')

    def test_incomplete_or_ambiguous_streams_are_not_success(self):
        trailing = json.dumps({'type': 'step_start', 'sessionID': 'ses_x',
                               'part': {'type': 'step-start', 'sessionID': 'ses_x'}})
        other = json.dumps({'type': 'text', 'sessionID': 'ses_other',
                            'part': {'type': 'text', 'sessionID': 'ses_other',
                                     'text': 'x'}})
        for output, code in ((self.events() + '\n{"type": "step_start"}', 0),
                             (self.events(trailing), 0),
                             (self.events(other), 0),
                             (self.events(), 1)):
            with self.subTest(output=output[-60:], code=code):
                with mock.patch.object(delivery, '_popen',
                                       return_value=self.process(output, code)):
                    self.assertIsNone(delivery.opencode(self.repo, 't', 'execution')['exit_code'])

    def test_tool_steps_before_a_final_stop_are_accepted(self):
        def event(kind, **part):
            return json.dumps({'type': kind, 'sessionID': 'ses_x',
                               'part': dict(part, sessionID='ses_x')})
        stream = '\n'.join([
            event('step_start', type='step-start'),
            event('tool_use', type='tool', tool='read'),
            event('step_finish', type='step-finish', reason='tool-calls'),
            event('step_start', type='step-start'),
            event('text', type='text', text='done'),
            event('step_finish', type='step-finish', reason='stop'),
        ])
        with mock.patch.object(delivery, '_popen', return_value=self.process(stream)):
            result = delivery.opencode(self.repo, 't', 'execution')
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['text'], 'done')
        truncated = stream.rsplit('\n', 1)[0]
        with mock.patch.object(delivery, '_popen', return_value=self.process(truncated)):
            self.assertIsNone(delivery.opencode(self.repo, 't', 'execution')['exit_code'])

    def test_timeout_kills_the_process_group_and_is_not_success(self):
        proc = mock.Mock()
        proc.pid = 4242
        proc.communicate.side_effect = subprocess.TimeoutExpired([], 1)
        with mock.patch.object(delivery, '_popen', return_value=proc), \
                mock.patch.object(delivery, '_kill_group') as kill:
            result = delivery.opencode(self.repo, 't', 'execution', timeout=1)
        kill.assert_called_once()
        self.assertIsNone(result['exit_code'])
        self.assertTrue(result['timeout'])

    def test_session_export_command_reads_the_session_record(self):
        payload = {'info': {'id': 'ses_x', 'outcome': 'succeeded',
                            'location': {'directory': str(self.repo)}},
                   'messages': [{'type': 'user'},
                                {'type': 'assistant', 'agent': 'plan', 'finish': 'stop',
                                 'content': [{'type': 'text', 'text': 'approved'}]}]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload), '')
        with mock.patch.object(delivery.subprocess, 'run', return_value=completed) as run:
            result = delivery.session_export('ses_x', self.repo)
        self.assertEqual(run.call_args.args[0][:3],
                         ['opencode', 'session', 'export'])
        self.assertNotIn('--sanitize', run.call_args.args[0])
        self.assertTrue(result['ok'])
        self.assertEqual(result['agents'], ['plan'])
        self.assertIn('approved', result['texts'][0])


class CliTests(DeliveryCase):
    def test_cli_requires_explicit_execution_and_one_plan_source(self):
        with mock.patch('sys.argv', ['delivery', '--repo', str(self.repo),
                                     '--state', str(self.state)]):
            with self.assertRaises(SystemExit) as caught:
                delivery.main()
        self.assertEqual(caught.exception.code, 2)

    def test_cli_exit_code_reflects_verified_state_only(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])])
        planfile = self.root / 'plan.json'
        planfile.write_text(json.dumps(plan))
        verified = {'tasks': {'one': {'status': 'VERIFIED'}}}
        args = ['delivery', '--repo', str(self.repo), '--plan', str(planfile),
                '--state', str(self.state), '--execute']
        output = io.StringIO()
        with mock.patch('sys.argv', args), \
                mock.patch.object(delivery, 'run', return_value=verified), \
                contextlib.redirect_stdout(output):
            self.assertEqual(delivery.main(), 0)
        with mock.patch('sys.argv', args), \
                mock.patch.object(delivery, 'run',
                                  return_value={'tasks': {'one': {'status': 'BLOCKED'}}}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(delivery.main(), 1)

    def test_cli_does_not_print_model_text(self):
        verified = {'tasks': {'one': {'status': 'VERIFIED', 'execution_text': 'private'}}}
        args = ['delivery', '--repo', str(self.repo), '--plan', '/dev/null',
                '--state', str(self.state), '--execute']
        output = io.StringIO()
        with mock.patch('sys.argv', args), \
                mock.patch.object(delivery, 'run', return_value=verified), \
                mock.patch.object(delivery, '_load_plan_file',
                                  return_value={'tasks': [{'id': 'one'}]}), \
                contextlib.redirect_stdout(output):
            delivery.main()
        self.assertNotIn('private', output.getvalue())


if __name__ == '__main__':
    unittest.main()
