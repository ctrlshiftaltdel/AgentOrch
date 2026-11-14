import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
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


def _verdict(prompt, verdict='APPROVE', findings=None, criteria=None, paths=None):
    head = re.search(r'HEAD_SHA=([0-9a-f]{40})', prompt).group(1)
    digest = re.search(r'DIFF_SHA256=([0-9a-f]{64})', prompt).group(1)
    listed = json.loads(re.search(r'Allowed paths: (\[[^\]]*\])', prompt).group(1))
    acceptance = criteria
    if acceptance is None:
        acceptance = [{'id': ident, 'criterion': text, 'status': 'PASS', 'evidence': 'checked'}
                      for ident, text in re.findall(r'^- (AC-\d+): (.*)$', prompt, re.M)]
    return 'Review complete.\n```json\n' + json.dumps({
        'verdict': verdict, 'head': head, 'diff_sha256': digest, 'mode': 'prepare-only',
        'paths': listed if paths is None else paths,
        'acceptance': acceptance, 'findings': list(findings or [])}) + '\n```\n'


class FakeAgents:
    """Real worktree writes; session ids, probes and transcripts are injected."""

    counter = 0

    def __init__(self, case, *, execution=None, review=None, sessions=None):
        self.case = case
        self.execution = execution
        self.review = review
        self.calls = []
        self.sessions = sessions

    def _ids(self):
        if self.sessions is not None:
            return self.sessions
        FakeAgents.counter += 1
        return (f'ses_exec{FakeAgents.counter}', f'ses_rev{FakeAgents.counter}')

    def __call__(self, work, prompt, role):
        self.exec_session, self.review_session = self._ids()
        self.calls.append(role)
        if role == 'execution':
            if self.execution is not None:
                self.execution(work, prompt)
            else:
                self.case.write_default(work, prompt)
            self.case.transcripts[self.exec_session] = 'execution finished'
            return {'exit_code': 0, 'session_id': self.exec_session, 'detail': 'ok'}
        if self.review is not None:
            custom = self.review(work, prompt)
            if custom is not None:
                self.case.transcripts[custom.get('session_id')] = custom.get('text') or ''
                return custom
        text = _verdict(prompt)
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
            'scout': {'id': 'scout', 'permissions': [
                {'action': '*', 'resource': '*', 'effect': 'allow'},
                {'action': 'edit', 'resource': '*', 'effect': 'deny'},
                {'action': 'shell', 'resource': '*', 'effect': 'deny'},
                {'action': 'browser', 'resource': '*', 'effect': 'deny'}]},
        }
        self.gh = None

    # -- fixtures ---------------------------------------------------------
    def worktree(self, name):
        path = self.root / f'work-{name}'
        _git(self.repo, 'worktree', 'add', '-q', '-b', f'task/{name}', str(path))
        return path

    def default_probe(self, session_id, work):
        agent = 'build' if session_id.startswith('ses_exec') else 'scout'
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
        plan = {'merge': False, 'tasks': tasks}
        plan.update(extra)
        return plan

    def run_delivery(self, plan, agents=None, **kwargs):
        agents = agents or FakeAgents(self)
        kwargs.setdefault('session_probe', self.default_probe)
        kwargs.setdefault('profiles', self.profiles)
        kwargs.setdefault('reviewer_agent', 'scout')
        if self.gh is not None:
            kwargs.setdefault('gh', self.gh)
        return delivery.run(self.repo, plan, self.state, agent=agents, **kwargs), agents

    def journal(self):
        return json.loads(self.state.read_text())

    def seed(self, plan, tasks):
        self.state.write_text(json.dumps({
            'version': 3, 'tasks': tasks, 'plan_hash': self.plan_hash(plan),
            'carrier_digest': None, 'mode': delivery.MODE}))

    def seed_edit(self, plan, edit):
        state = self.journal()
        for record in state['tasks'].values():
            edit(record)
        state['plan_hash'] = self.plan_hash(plan)
        self.state.write_text(json.dumps(state))

    def plan_hash(self, plan):
        merged = dict(plan, executor_agent='build', reviewer_agent='scout')
        return delivery.hashlib.sha256(
            json.dumps(merged, sort_keys=True).encode()).hexdigest()


class PrepareOnlyTests(DeliveryCase):
    def test_prepare_only_approves_and_leaves_merge_to_a_human(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])])
        state, agents = self.run_delivery(plan)
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'APPROVED')
        self.assertEqual(record['phase'], 'done')
        self.assertEqual(agents.calls, ['execution', 'review'])
        self.assertEqual(record['verification'][0]['exit_code'], 0)
        self.assertEqual(record['review_verdict']['mode'], 'prepare-only')
        # The repository is untouched: no merge, no file in the target worktree.
        self.assertEqual(_git(self.repo, 'log', '--oneline').stdout.count(b'\n'), 1)
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_dependent_task_waits_and_independent_task_still_runs(self):
        one, two, three = (self.worktree(name) for name in ('one', 'two', 'three'))
        plan = self.plan([self.task('one', one, ['a.txt']),
                          self.task('two', two, ['b.txt'], depends=['one']),
                          self.task('three', three, ['out.txt'])])
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        self.assertEqual(state['tasks']['two']['status'], 'PENDING')
        self.assertEqual(state['tasks']['three']['status'], 'APPROVED')
        self.assertEqual(agents.calls, ['execution', 'review', 'execution', 'review'])

    def test_automatic_merge_is_refused_before_anything_runs(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])], merge=True,
                         merge_into='integration')
        with self.assertRaises(delivery.DeliveryError) as caught:
            self.run_delivery(plan)
        self.assertIn('merge is not implemented', str(caught.exception))
        self.assertFalse(self.state.exists())
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_cli_refuses_merge_into(self):
        work = self.worktree('one')
        planfile = self.root / 'plan.json'
        planfile.write_text(json.dumps(self.plan([self.task('one', work, ['a.txt'])])))
        args = ['delivery', '--repo', str(self.repo), '--plan', str(planfile),
                '--state', str(self.state), '--execute', '--merge-into', 'integration']
        with mock.patch('sys.argv', args), self.assertRaises(SystemExit) as caught:
            delivery.main()
        self.assertEqual(caught.exception.code, 2)


class ReviewGateTests(DeliveryCase):
    def test_review_session_equal_to_execution_blocks_approval(self):
        work = self.worktree('one')
        agents = FakeAgents(self, sessions=('ses_exec_same', 'ses_exec_same'))
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]), agents)
        self.assertEqual(state['tasks']['one']['status'], 'REVIEW_REJECTED')
        self.assertIn('equals execution session', state['tasks']['one']['reason'])

    def test_unverifiable_review_session_blocks_approval(self):
        work = self.worktree('one')

        def probe(session_id, where):
            if session_id.startswith('ses_rev'):
                return {'ok': False, 'reason': 'session export exit code 1'}
            return self.default_probe(session_id, where)

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     session_probe=probe)
        self.assertIn('unverifiable', state['tasks']['one']['reason'])

    def test_review_wrong_directory_or_agent_blocks_approval(self):
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

        def no_verdict(where, prompt):
            return {'exit_code': 0, 'session_id': 'ses_rev', 'text': 'looks good to me'}

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, review=no_verdict))
        self.assertEqual(state['tasks']['one']['status'], 'REVIEW_REJECTED')
        self.assertIn('no machine-readable verdict', state['tasks']['one']['reason'])

    def test_reject_or_stale_head_blocks_approval(self):
        work = self.worktree('one')

        def reject(where, prompt):
            return {'exit_code': 0, 'session_id': 'ses_rev',
                    'text': _verdict(prompt, 'REJECT', [{'defect': 'bug'}])}

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, review=reject))
        self.assertIn('not APPROVE', state['tasks']['one']['reason'])

        head = re.search(r'HEAD_SHA=([0-9a-f]{40})', 'HEAD_SHA=' + '0' * 40).group(1)

        def stale(where, prompt):
            text = _verdict(prompt).replace(
                re.search(r'HEAD_SHA=([0-9a-f]{40})', prompt).group(1), head)
            return {'exit_code': 0, 'session_id': 'ses_rev', 'text': text}

        self.state = self.root / 'state-stale.json'
        stale_work = self.worktree('stale')
        state, _ = self.run_delivery(self.plan([self.task('stale', stale_work, ['a.txt'])]),
                                     FakeAgents(self, review=stale))
        self.assertIn('different head', state['tasks']['stale']['reason'])

    def test_verdict_must_quote_each_criterion_and_the_path_set(self):
        work = self.worktree('one')

        def wrong_text(where, prompt):
            verdict = json.loads(re.search(r'```json\n(.*?)\n```', _verdict(prompt),
                                           re.S).group(1))
            verdict['acceptance'][0]['criterion'] = 'some other criterion'
            return {'exit_code': 0, 'session_id': 'ses_rev',
                    'text': '```json\n' + json.dumps(verdict) + '\n```\n'}

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, review=wrong_text))
        self.assertEqual(state['tasks']['one']['status'], 'REVIEW_REJECTED')
        self.assertIn('criterion', state['tasks']['one']['reason'])

        self.state = self.root / 'state-paths.json'
        path_work = self.worktree('badpaths')

        def wrong_paths(where, prompt):
            return {'exit_code': 0, 'session_id': 'ses_rev',
                    'text': _verdict(prompt, paths=['b.txt'])}

        state, _ = self.run_delivery(self.plan([self.task('badpaths', path_work, ['a.txt'])]),
                                     FakeAgents(self, review=wrong_paths))
        self.assertIn('path set', state['tasks']['badpaths']['reason'])

    def test_not_every_acceptance_criterion_passing_blocks_approval(self):
        work = self.worktree('one')

        def partial(where, prompt):
            verdict = json.loads(re.search(r'```json\n(.*?)\n```', _verdict(prompt),
                                           re.S).group(1))
            verdict['acceptance'][-1]['status'] = 'NOT_RUN'
            return {'exit_code': 0, 'session_id': 'ses_rev',
                    'text': '```json\n' + json.dumps(verdict) + '\n```\n'}

        task = self.task('one', work, ['a.txt'], acceptance=['first', 'second'])
        state, _ = self.run_delivery(self.plan([task]), FakeAgents(self, review=partial))
        self.assertIn('AC-2', state['tasks']['one']['reason'])

    def test_reviewer_profile_must_deny_edits_and_shell_tool(self):
        work = self.worktree('one')
        weak = lambda where: {'scout': {'permissions': [
            {'action': '*', 'resource': '*', 'effect': 'allow'}]}}
        state, agents = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                          profiles=weak)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('not provably read-only', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_scoped_allow_after_a_wildcard_deny_is_not_read_only(self):
        profile = {'permissions': [
            {'action': '*', 'resource': '*', 'effect': 'allow'},
            {'action': 'shell', 'resource': '*', 'effect': 'deny'},
            {'action': 'edit', 'resource': '*', 'effect': 'deny'},
            {'action': 'shell', 'resource': 'rm *', 'effect': 'allow'}]}
        gate = delivery.profile_gate(profile)
        self.assertFalse(gate['ready'])
        self.assertTrue(gate['scoped_allows'])

    def test_profile_is_checked_in_every_task_worktree(self):
        one, two = self.worktree('one'), self.worktree('two')

        def profiles(where):
            rules = [{'action': '*', 'resource': '*', 'effect': 'allow'},
                     {'action': 'edit', 'resource': '*', 'effect': 'deny'}]
            if Path(where) != two:
                rules.append({'action': 'shell', 'resource': '*', 'effect': 'deny'})
            return {'scout': {'permissions': rules}}

        state, agents = self.run_delivery(
            self.plan([self.task('one', one, ['a.txt']),
                       self.task('two', two, ['b.txt'])]), profiles=profiles)
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        self.assertEqual(state['tasks']['two']['status'], 'BLOCKED')
        self.assertIn('not provably read-only', state['tasks']['two']['reason'])
        self.assertEqual(agents.calls, ['execution', 'review'])


class ResumeTests(DeliveryCase):
    def _prepared(self, name='one'):
        work = self.worktree(name)
        base = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        (work / 'a.txt').write_text('pending\n')
        _git(work, 'add', 'a.txt')
        _git(work, 'commit', '-q', '-m', 'pending')
        head = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        return work, base, head

    def _seed_review(self, work, base, head, plan, **extra):
        record = {'status': 'RUNNING', 'phase': 'review', 'base': base, 'head': head,
                  'worktree': str(work), 'diff_sha256': delivery._fingerprint(work, base),
                  'execution_session': 'ses_exec', 'pid': 999999,
                  'executor_agent': 'build', 'reviewer_agent': 'scout'}
        record.update(extra)
        self.seed(plan, {'one': record})

    def test_interrupted_task_becomes_unknown_and_is_not_redispatched(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self._seed_review(work, base, head, plan)
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'UNKNOWN')
        self.assertEqual(agents.calls, [])

    def test_resume_re_runs_verification_before_review(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'],
                                    verify=[[PY, '-c', 'raise SystemExit(73)']])])
        self._seed_review(work, base, head, plan)
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'FAILED')
        self.assertIn('resuming', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_resume_never_merges_when_verification_fails_even_if_the_target_has_the_head(self):
        work, base, head = self._prepared()
        # The target branch already contains the task head, as in the reported
        # repro. There is no merge code, but the regression check asserts that a
        # failing verification cannot produce an approved/merged outcome.
        _git(self.repo, 'merge', '--no-ff', '--no-edit', 'task/one')
        plan = self.plan([self.task('one', work, ['a.txt'],
                                    verify=[[PY, '-c', 'raise SystemExit(73)']])])
        self._seed_review(work, base, head, plan)
        before = _git(self.repo, 'rev-parse', 'HEAD').stdout
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'FAILED')
        self.assertEqual(agents.calls, [])
        self.assertEqual(_git(self.repo, 'rev-parse', 'HEAD').stdout, before)

    def test_resume_continues_only_after_reproving_everything(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self._seed_review(work, base, head, plan)
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        self.assertEqual(agents.calls, ['review'])

    def test_resume_refuses_a_worktree_from_another_repository(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        digest = delivery._fingerprint(work, base)
        _git(self.repo, 'worktree', 'remove', '--force', str(work))
        work.mkdir()
        _git(work, 'init', '-q', '-b', 'task/one')
        _git(work, 'config', 'user.email', 'test@example.invalid')
        _git(work, 'config', 'user.name', 'Test')
        (work / 'a.txt').write_text('pending\n')
        _git(work, 'add', 'a.txt')
        _git(work, 'commit', '-q', '-m', 'pending')
        same = _head_of(work)
        self.seed(plan, {'one': {'status': 'RUNNING', 'phase': 'review', 'base': base,
                                 'head': same, 'worktree': str(work),
                                 'diff_sha256': digest, 'execution_session': 'ses_exec',
                                 'pid': 999999, 'executor_agent': 'build',
                                 'reviewer_agent': 'scout'}})
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('does not belong', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_resume_refuses_an_execution_session_with_the_wrong_agent(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self._seed_review(work, base, head, plan)

        def probe(session_id, where):
            result = self.default_probe(session_id, where)
            if session_id == 'ses_exec':
                result['agents'] = ['scout']
            return result

        state, agents = self.run_delivery(plan, resume=True, session_probe=probe)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('not the declared build', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_resume_refuses_an_unproven_reviewer_profile_before_running_checks(self):
        work, base, head = self._prepared()
        marker = work / 'check-ran'
        verify = [[PY, '-c', f'import pathlib; pathlib.Path("{marker}").write_text("x")']]
        plan = self.plan([self.task('one', work, ['a.txt'], verify=verify)])
        self._seed_review(work, base, head, plan)
        weak = lambda where: {'scout': {'permissions': [
            {'action': '*', 'resource': '*', 'effect': 'allow'}]}}
        state, agents = self.run_delivery(plan, resume=True, profiles=weak)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('not provably read-only', state['tasks']['one']['reason'])
        self.assertFalse(marker.exists())

    def test_resume_of_a_merge_checkpoint_is_refused(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self._seed_review(work, base, head, plan, status='APPROVED', phase='merge')
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('merge is not implemented', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_unknown_can_be_resumed_and_approved(self):
        work, base, head = self._prepared()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self._seed_review(work, base, head, plan)
        first, agents = self.run_delivery(plan)
        self.assertEqual(first['tasks']['one']['status'], 'UNKNOWN')
        self.assertEqual(agents.calls, [])
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        self.assertEqual(agents.calls, ['review'])


class AuthenticateTests(DeliveryCase):
    def test_forged_approval_proves_sessions_before_running_checks(self):
        work = self.worktree('one')
        marker = work / 'check-ran'
        (work / 'a.txt').write_text('x\n')
        _git(work, 'add', 'a.txt')
        _git(work, 'commit', '-q', '-m', 'change')
        base = _git(self.repo, 'rev-parse', 'HEAD').stdout.decode().strip()
        head = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        verify = [[PY, '-c', f'import pathlib; pathlib.Path("{marker}").write_text("x")']]
        plan = self.plan([self.task('one', work, ['a.txt'], verify=verify)])
        self.seed(plan, {'one': {
            'status': 'APPROVED', 'phase': 'done', 'base': base, 'head': head,
            'worktree': str(work), 'branch': 'task/one',
            'diff_sha256': delivery._fingerprint(work, base),
            'execution_session': 'ses_missing', 'review_session': 'ses_missing_review',
            'verification': [{'program': PY, 'argv_sha256': '0' * 64, 'exit_code': 0}],
            'attempt': 1, 'executor_agent': 'build', 'reviewer_agent': 'scout'}})
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertFalse(marker.exists())
        self.assertEqual(agents.calls, [])

    def test_changing_the_acceptance_text_invalidates_the_old_transcript(self):
        work = self.worktree('one')
        task = self.task('one', work, ['a.txt'], acceptance=['the original criterion'])
        plan = self.plan([task])
        state, _ = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        task['acceptance'] = ['a different criterion']
        state, agents = self.run_delivery(plan, plan_override=None) if False else (None, None)
        # The plan hash changes, so the same journal is refused outright.
        with self.assertRaises(ValueError):
            self.run_delivery(plan)

    def test_verdict_paths_must_match_the_task(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])])
        state, _ = self.run_delivery(plan)
        record = state['tasks']['one']
        verdict = dict(record['review_verdict'])
        verdict['paths'] = ['b.txt']
        wrong = '```json\n' + json.dumps(verdict) + '\n```\n'
        self.transcripts[record['review_session']] = wrong
        state = self.journal()
        state['tasks']['one']['review_verdict'] = verdict
        self.state.write_text(json.dumps(state))
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('path set', state['tasks']['one']['reason'])

    def test_a_complete_forged_journal_is_reproved_from_the_transcript(self):
        work = self.worktree('one')
        (work / 'a.txt').write_text('x\n')
        _git(work, 'add', 'a.txt')
        _git(work, 'commit', '-q', '-m', 'change')
        base = _git(self.repo, 'rev-parse', 'HEAD').stdout.decode().strip()
        head = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        digest = delivery._fingerprint(work, base)
        plan = self.plan([self.task('one', work, ['a.txt'])])
        prompt = (f'HEAD_SHA={head}\nDIFF_SHA256={digest}\nMODE=prepare-only\n'
                  'Allowed paths: ["a.txt"]\nAcceptance criteria:\n'
                  '- AC-1: one does the required change\n')
        verdict = json.loads(re.search(r'```json\n(.*?)\n```', _verdict(prompt), re.S).group(1))
        self.transcripts['ses_rev'] = '```json\n' + json.dumps(verdict) + '\n```\n'
        self.transcripts['ses_exec'] = 'execution finished'
        self.seed(plan, {'one': {
            'status': 'APPROVED', 'phase': 'done', 'base': base, 'head': head,
            'worktree': str(work), 'branch': 'task/one', 'diff_sha256': digest,
            'changed_paths': ['a.txt'],
            'execution_session': 'ses_exec', 'review_session': 'ses_rev',
            'review_verdict': verdict, 'reviewer_agent': 'scout', 'executor_agent': 'build',
            'attempt': 1}})
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        self.assertEqual(agents.calls, [])


class ScopeTests(DeliveryCase):
    def test_forbidden_path_cannot_be_reached_through_an_allowed_ancestor(self):
        (self.repo / 'docs' / 'secret.txt').write_text('secret')
        text = (self.repo / 'docs' / 'PROJECT.md').read_text().replace(
            '- 永远禁止改动路径 / 文件：`LICENSE.md`',
            '- 永远禁止改动路径 / 文件：`LICENSE.md`、`docs/secret.txt`')
        (self.repo / 'docs' / 'PROJECT.md').write_text(text)
        _git(self.repo, 'add', '-A')
        _git(self.repo, 'commit', '-q', '-m', 'forbid a nested path')
        work = self.worktree('one')
        task = self.task('one', work, ['docs'])
        with self.assertRaises(ValueError) as caught:
            self.run_delivery(self.plan([task]))
        self.assertIn('overlaps the forbidden path', str(caught.exception))
        self.assertFalse(self.state.exists())

    def test_runtime_writes_into_a_forbidden_subpath_are_blocked_end_to_end(self):
        (self.repo / 'docs' / 'secret.txt').write_text('secret')
        text = (self.repo / 'docs' / 'PROJECT.md').read_text().replace(
            '- 永远禁止改动路径 / 文件：`LICENSE.md`',
            '- 永远禁止改动路径 / 文件：`LICENSE.md`、`docs/secret.txt`')
        (self.repo / 'docs' / 'PROJECT.md').write_text(text)
        _git(self.repo, 'add', '-A')
        _git(self.repo, 'commit', '-q', '-m', 'forbid a nested path')
        work = self.worktree('one')

        def write_secret(where, prompt):
            (where / 'public.txt').write_text('allowed\n')
            (where / 'docs').mkdir(exist_ok=True)
            (where / 'docs' / 'secret.txt').write_text('leaked\n')

        # The declared path does not overlap the forbidden file, so the plan is
        # accepted; the actual out-of-policy write must still fail closed.
        task = self.task('one', work, ['docs/public.txt'],
                         acceptance=['only allowed files change'])
        state, _ = self.run_delivery(self.plan([task]),
                                     FakeAgents(self, execution=write_secret))
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertIn('forbidden', record['reason'])
        self.assertNotIn('review_session', record)
        # Nothing reached the target repository.
        self.assertFalse((self.repo / 'public.txt').exists())
        self.assertEqual((self.repo / 'docs' / 'secret.txt').read_text(), 'secret')

    def test_project_policy_is_re_read_before_the_commit(self):
        work = self.worktree('one')

        def write_and_tighten(where, prompt):
            (where / 'docs').mkdir(exist_ok=True)
            (where / 'docs' / 'public.txt').write_text('allowed\n')
            (where / 'docs' / 'secret.txt').write_text('leaked\n')
            project = self.repo / 'docs' / 'PROJECT.md'
            project.write_text(project.read_text().replace(
                '- 永远禁止改动路径 / 文件：`LICENSE.md`',
                '- 永远禁止改动路径 / 文件：`LICENSE.md`、`docs/secret.txt`'))
            _git(self.repo, 'add', 'docs/PROJECT.md')
            _git(self.repo, 'commit', '-q', '-m', 'tighten policy mid-run')

        task = self.task('one', work, ['docs/public.txt'])
        state, _ = self.run_delivery(self.plan([task]),
                                     FakeAgents(self, execution=write_and_tighten))
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertIn('forbidden', record['reason'])
        self.assertNotIn('review_session', record)
        # The blocked files stay in the task worktree (nothing is reset).
        self.assertEqual((work / 'docs' / 'secret.txt').read_text(), 'leaked\n')

    def test_every_actual_changed_path_is_checked_against_project_forbidden(self):
        work = self.worktree('one')
        (work / 'docs').mkdir(exist_ok=True)
        (work / 'docs' / 'secret.txt').write_text('x')
        config = {'edition': 'local', 'allowed': ['docs'], 'forbidden': ['docs/secret.txt']}
        with self.assertRaises(delivery.DeliveryError) as caught:
            delivery._check_changed_path(work, 'docs/secret.txt', ['docs'], config)
        self.assertIn('forbidden', str(caught.exception))
        delivery._check_changed_path(work, 'docs/other.txt', ['docs'], config)

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
                                 profiles=self.profiles, reviewer_agent='scout')
        self.assertFalse((self.root / 's.json').exists())

    def test_execution_outside_the_declared_paths_is_blocked(self):
        work = self.worktree('one')
        agents = FakeAgents(self, execution=lambda where, prompt: (
            (where / 'bad.txt').write_text('outside\n')))
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]), agents)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('scope violation', state['tasks']['one']['reason'])

    def test_symlink_change_is_blocked(self):
        work = self.worktree('one')

        def link(where, prompt):
            (where / 'a.txt').symlink_to(self.repo / 'base.txt')

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, execution=link))
        self.assertIn('symlink', state['tasks']['one']['reason'])

    def test_staged_change_hidden_by_a_worktree_revert_is_still_out_of_scope(self):
        work = self.worktree('one')

        def stage(where, prompt):
            (where / 'a.txt').write_text('allowed\n')
            (where / 'base.txt').write_text('staged forbidden\n')
            _git(where, 'add', 'base.txt')
            (where / 'base.txt').write_text('base')

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, execution=stage))
        self.assertIn('scope violation', state['tasks']['one']['reason'])

    def test_fingerprint_distinguishes_a_symlink_from_identical_bytes(self):
        work = self.worktree('one')
        base = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        (work / 'same.txt').write_text('same\n')
        regular = delivery._fingerprint(work, base)
        (work / 'same.txt').unlink()
        (work / 'same.txt').symlink_to(work / 'base.txt')
        (work / 'base.txt').write_text('same\n')
        self.assertNotEqual(regular, delivery._fingerprint(work, base))


class NoDestructiveTests(DeliveryCase):
    def test_a_worktree_behind_the_baseline_is_refused_without_touching_ignored_data(self):
        (self.repo / '.gitignore').write_text('private.txt\n')
        _git(self.repo, 'add', '.gitignore')
        _git(self.repo, 'commit', '-q', '-m', 'ignore private data')
        work = self.worktree('one')
        (work / 'private.txt').write_text('user data\n')
        (self.repo / 'later.txt').write_text('advance\n')
        _git(self.repo, 'add', 'later.txt')
        _git(self.repo, 'commit', '-q', '-m', 'advance baseline')
        state, agents = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]))
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('not exactly at the target baseline', state['tasks']['one']['reason'])
        self.assertEqual((work / 'private.txt').read_text(), 'user data\n')
        self.assertEqual(_git(work, 'status', '--porcelain', '--ignored').stdout,
                         b'!! private.txt\n')
        self.assertEqual(agents.calls, [])

    def test_behind_baseline_worktree_never_overwrites_an_ignored_file(self):
        (self.repo / '.gitignore').write_text('ignored.txt\n')
        _git(self.repo, 'add', '.gitignore')
        _git(self.repo, 'commit', '-q', '-m', 'ignore it')
        work = self.worktree('one')
        (work / 'ignored.txt').write_text('OWN_FIXTURE_ORIGINAL')
        self.assertTrue(_git(work, 'status', '--porcelain', '--ignored').stdout)
        # Advance the target with a *tracked* file of the same name.
        (self.repo / 'ignored.txt').write_text('tracked baseline')
        _git(self.repo, 'add', '-f', 'ignored.txt')
        _git(self.repo, 'commit', '-q', '-m', 'tracked baseline file')
        state, agents = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]))
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('not exactly at the target baseline', state['tasks']['one']['reason'])
        self.assertEqual((work / 'ignored.txt').read_text(), 'OWN_FIXTURE_ORIGINAL')
        self.assertEqual(agents.calls, [])

    def test_retry_refuses_and_preserves_uncommitted_work(self):
        work = self.worktree('one')
        verify = [[PY, '-c',
                   'import pathlib; pathlib.Path("a.txt").write_text("modified"); '
                   'raise SystemExit(1)']]
        plan = self.plan([self.task('one', work, ['a.txt'], verify=verify)])
        state, _ = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'FAILED')
        head = _git(work, 'rev-parse', 'HEAD').stdout
        state, agents = self.run_delivery(plan, retry=['one'])
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'BLOCKED')
        self.assertIn('retry refused', record['reason'])
        self.assertEqual((work / 'a.txt').read_text(), 'modified')
        self.assertEqual(_git(work, 'rev-parse', 'HEAD').stdout, head)
        self.assertEqual(agents.calls, [])
        self.assertNotIn('stash', record)

    def test_retry_reruns_review_on_the_same_committed_head(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])])

        def reject(where, prompt):
            return {'exit_code': 0, 'session_id': 'ses_rev',
                    'text': _verdict(prompt, 'REJECT', [{'defect': 'typo'}])}

        state, _ = self.run_delivery(plan, FakeAgents(self, review=reject))
        self.assertEqual(state['tasks']['one']['status'], 'REVIEW_REJECTED')
        head = state['tasks']['one']['head']
        state, agents = self.run_delivery(plan, retry=['one'])
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'APPROVED')
        self.assertEqual(record['head'], head)
        self.assertEqual(record['attempt'], 2)
        self.assertNotIn('stash', record)
        self.assertEqual(agents.calls, ['review'])


class EditionTests(DeliveryCase):
    def _set(self, text):
        (self.repo / 'docs' / 'PROJECT.md').write_text(text)
        _git(self.repo, 'add', 'docs/PROJECT.md')
        _git(self.repo, 'commit', '-q', '-m', 'project config')

    def test_missing_or_invalid_edition_refuses_dispatch_in_both_modes(self):
        for index, text in enumerate((PROJECT_LOCAL.replace('- edition：`local`', ''),
                                      PROJECT_LOCAL.replace('`local`', '`cloud`'))):
            with self.subTest(text=text[-40:]):
                self._set(text)
                work = self.worktree(f'one{index}')
                plan = self.plan([self.task('one', work, ['a.txt'])])
                with self.assertRaises(ValueError):
                    self.run_delivery(plan)
                self.assertFalse(self.state.exists())

    def test_github_edition_allows_prepare_only(self):
        self._set(PROJECT_GITHUB)
        work = self.worktree('one')
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]))
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        self.assertEqual(state['project_edition'], 'github')
        self.assertFalse((self.repo / 'a.txt').exists())


class CheckRunnerTests(DeliveryCase):
    def test_timeout_kills_the_whole_process_tree(self):
        work = self.worktree('one')
        child = ('import time,pathlib; time.sleep(2); '
                 'pathlib.Path("child-marker").write_text("x")')
        parent = ('import subprocess,sys,time; '
                  f'subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(10)')
        checks = delivery._run_checks([[PY, '-c', parent]], work, timeout=1)
        self.assertIsNone(checks[0]['exit_code'])
        time.sleep(3)
        self.assertFalse((work / 'child-marker').exists())

    def test_checks_receive_pwd_and_cwd(self):
        work = self.worktree('one')
        checks = delivery._run_checks(
            [[PY, '-c',
              'import os,sys; sys.exit(0 if os.environ.get("PWD")==os.getcwd() else 1)']],
            work, timeout=60)
        self.assertEqual(checks[0]['exit_code'], 0)


class LockTests(DeliveryCase):
    def test_locks_live_under_the_repository_common_dir(self):
        work = self.worktree('one')
        directory, _ = delivery.lock_targets(self.repo, self.state,
                                             delivery._plan_tasks(
                                                 self.plan([self.task('one', work,
                                                                     ['a.txt'])])))
        self.assertEqual(directory, delivery._common(self.repo) / 'agentorch-delivery-locks')

    def test_two_processes_with_different_tmpdir_cannot_lock_the_same_repository(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])])
        specs = delivery._plan_tasks(plan)
        holder = self.root / 'holder.py'
        holder.write_text(
            'import os,sys,time\n'
            'sys.path.insert(0, os.environ["PROJECT_ROOT"])\n'
            'from scripts import autonomous_delivery as d\n'
            'handles = d.acquire_locks(os.environ["REPO"], os.environ["STATE"],\n'
            '                           d._plan_tasks(__import__("json").loads(\n'
            '                               open(os.environ["PLAN"]).read())))\n'
            'print("held", flush=True)\n'
            'time.sleep(20)\n')
        project = Path(__file__).resolve().parents[1]
        planfile = self.root / 'plan.json'
        planfile.write_text(json.dumps(plan))
        env_a = dict(os.environ, TMPDIR=str(self.root / 'tmp-a'), PROJECT_ROOT=str(project),
                     REPO=str(self.repo), STATE=str(self.state), PLAN=str(planfile))
        env_b = dict(os.environ, TMPDIR=str(self.root / 'tmp-b'))
        child = subprocess.Popen([PY, str(holder)], env=env_a, stdout=subprocess.PIPE,
                                 text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'held')
            with mock.patch.dict(os.environ, {'TMPDIR': env_b['TMPDIR']}):
                with self.assertRaises(delivery.DeliveryError) as caught:
                    delivery.acquire_locks(self.repo, self.state, specs)
            self.assertIn('locked', str(caught.exception))
        finally:
            child.kill()
            child.wait(timeout=10)
            for handle in delivery.acquire_locks(self.repo, self.state, specs):
                handle.close()


class JournalTests(DeliveryCase):
    def test_journal_should_be_versioned_and_carrier_bound(self):
        work = self.worktree('one')
        self.state.write_text(json.dumps({'version': 2, 'tasks': {},
                                          'plan_hash': 'x'}))
        with self.assertRaises(ValueError):
            self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]))

    def test_merge_states_in_a_journal_are_refused(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self.seed(plan, {'one': {'status': 'VERIFIED', 'head': '0' * 40,
                                 'diff_sha256': '0' * 64}})
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('merge', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_state_path_inside_the_repository_is_refused(self):
        work = self.worktree('one')
        inside = self.repo / 'delivery-state.json'
        with self.assertRaises(ValueError):
            delivery.run(self.repo, self.plan([self.task('one', work, ['a.txt'])]), inside,
                         agent=FakeAgents(self), session_probe=self.default_probe,
                         profiles=self.profiles, reviewer_agent='scout')
        self.assertFalse(inside.exists())

    def test_verification_arguments_are_not_written_to_the_journal(self):
        work = self.worktree('one')
        secret = 'synthetic-' + 'argument-value'
        verify = [[PY, '-c', 'raise SystemExit(0)', secret]]
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'],
                                                          verify=verify)]))
        self.assertEqual(state['tasks']['one']['status'], 'APPROVED')
        self.assertNotIn(secret, self.state.read_text())
        self.assertNotIn(secret, json.dumps(delivery._summary(state)))

    def test_plan_without_acceptance_is_blocked(self):
        work = self.worktree('one')
        task = self.task('one', work, ['a.txt'])
        task.pop('acceptance')
        state, agents = self.run_delivery(self.plan([task]))
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('acceptance', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])


class CliTests(DeliveryCase):
    def test_cli_exit_code_reflects_approved_state_only(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])])
        planfile = self.root / 'plan.json'
        planfile.write_text(json.dumps(plan))
        args = ['delivery', '--repo', str(self.repo), '--plan', str(planfile),
                '--state', str(self.state), '--execute']
        approved = {'tasks': {'one': {'status': 'APPROVED'}}}
        with mock.patch('sys.argv', args), \
                mock.patch.object(delivery, 'run', return_value=approved), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(delivery.main(), 0)
        blocked = {'tasks': {'one': {'status': 'BLOCKED'}}}
        with mock.patch('sys.argv', args), \
                mock.patch.object(delivery, 'run', return_value=blocked), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(delivery.main(), 1)

    def test_cli_does_not_print_model_text(self):
        approved = {'tasks': {'one': {'status': 'APPROVED', 'execution_text': 'private'}}}
        args = ['delivery', '--repo', str(self.repo), '--plan', '/dev/null',
                '--state', str(self.state), '--execute']
        output = io.StringIO()
        with mock.patch('sys.argv', args), \
                mock.patch.object(delivery, 'run', return_value=approved), \
                mock.patch.object(delivery, '_load_plan_file',
                                  return_value={'tasks': [{'id': 'one'}]}), \
                contextlib.redirect_stdout(output):
            delivery.main()
        self.assertNotIn('private', output.getvalue())


def _head_of(work):
    return _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()


if __name__ == '__main__':
    unittest.main()
