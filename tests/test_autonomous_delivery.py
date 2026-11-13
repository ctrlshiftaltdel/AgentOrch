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


def _git(where, *args, check=True):
    return subprocess.run(['git', '--no-optional-locks', *args], cwd=where, check=check,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)


def _verdict(head, digest, verdict='APPROVE', findings=None):
    return 'Review complete.\n```json\n' + json.dumps({
        'verdict': verdict, 'head': head, 'diff_sha256': digest,
        'findings': list(findings or [])}) + '\n```\n'


class FakeAgents:
    """Real worktree writes; session ids and probes are injected, not real ones."""

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
            return {'exit_code': 0, 'session_id': self.exec_session}
        head = re.search(r'HEAD_SHA=([0-9a-f]{40})', prompt).group(1)
        digest = re.search(r'DIFF_SHA256=([0-9a-f]{64})', prompt).group(1)
        if self.review is not None:
            custom = self.review(work, prompt, head, digest)
            if custom is not None:
                return custom
        return {'exit_code': 0, 'session_id': self.review_session,
                'text': _verdict(head, digest)}


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
        _git(self.repo, 'add', 'base.txt')
        _git(self.repo, 'commit', '-q', '-m', 'base')
        self.state = self.root / 'state.json'
        self.probe = self.default_probe
        self.profiles = lambda where: {
            'build': {'id': 'build', 'permissions': [{'action': '*', 'resource': '*',
                                                      'effect': 'allow'}]},
            'plan': {'id': 'plan', 'permissions': [
                {'action': '*', 'resource': '*', 'effect': 'allow'},
                {'action': 'edit', 'resource': '*', 'effect': 'deny'}]},
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
                'finishes': ['stop'], 'models': [{'id': 'test-model'}]}

    def write_default(self, work, prompt):
        match = re.search(r'paths: (\[[^\]]*\])', prompt)
        name = json.loads(match.group(1))[0]
        (work / name).write_text(f'{Path(work).name}\n')

    def task(self, ident, work, paths, verify=None, depends=None):
        return {'id': ident, 'worktree': str(work), 'prompt': f'{ident}: make a change',
                'paths': paths, 'depends': list(depends or []),
                'verify': verify or [[PY, '-c', 'raise SystemExit(0)']]}

    def plan(self, tasks, **extra):
        plan = {'merge_into': 'integration', 'tasks': tasks}
        plan.update(extra)
        return plan

    def run_delivery(self, plan, agents=None, **kwargs):
        agents = agents or FakeAgents(self)
        kwargs.setdefault('session_probe', self.probe)
        kwargs.setdefault('profiles', self.profiles)
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


class ReviewGateTests(DeliveryCase):
    def test_review_session_equal_to_execution_blocks_merge(self):
        work = self.worktree('one')
        agents = FakeAgents(self, sessions=('ses_exec_same', 'ses_exec_same'))
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]), agents)
        self.assertEqual(state['tasks']['one']['status'], 'REVIEW_REJECTED')
        self.assertIn('equals execution session', state['tasks']['one']['reason'])
        self.assertFalse((self.repo / 'a.txt').exists())

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
        agents = FakeAgents(self, review=lambda *args: {
            'exit_code': 0, 'session_id': 'ses_rev', 'text': 'looks good to me'})
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]), agents)
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
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_reviewer_that_changes_the_worktree_blocks_merge(self):
        work = self.worktree('one')

        def meddle(where, prompt, head, digest):
            (where / 'a.txt').write_text('reviewer wrote this')
            return None

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, review=meddle))
        self.assertIn('changed the worktree', state['tasks']['one']['reason'])
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_reviewer_agent_profile_must_deny_edits(self):
        work = self.worktree('one')
        profiles = lambda where: {'plan': {'permissions': [
            {'action': '*', 'resource': '*', 'effect': 'allow'}]}}
        with self.assertRaises(delivery.DeliveryError) as caught:
            self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                              profiles=profiles)
        self.assertIn('does not deny edits', str(caught.exception))
        self.assertFalse(self.state.exists())


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


class RecoveryTests(DeliveryCase):
    def test_interrupted_task_becomes_unknown_and_is_not_redispatched(self):
        work = self.worktree('one')
        base = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        (work / 'a.txt').write_text('pending\n')
        _git(work, 'add', 'a.txt')
        _git(work, 'commit', '-q', '-m', 'pending')
        head = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        target_base = _git(self.repo, 'rev-parse', 'HEAD').stdout.decode().strip()
        self.seed(plan, {'one': {'status': 'RUNNING', 'phase': 'verify', 'base': base,
                                 'target_base': target_base, 'head': head,
                                 'worktree': str(work),
                                 'diff_sha256': delivery._fingerprint(work, base),
                                 'pid': 999999}})
        state, agents = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'UNKNOWN')
        self.assertEqual(agents.calls, [])
        self.assertFalse((self.repo / 'a.txt').exists())

    def test_resume_continues_from_reproven_facts_without_redispatch(self):
        work = self.worktree('one')
        base = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        (work / 'a.txt').write_text('pending\n')
        _git(work, 'add', 'a.txt')
        _git(work, 'commit', '-q', '-m', 'pending')
        head = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        target_base = _git(self.repo, 'rev-parse', 'HEAD').stdout.decode().strip()
        self.seed(plan, {'one': {'status': 'RUNNING', 'phase': 'verify', 'base': base,
                                 'target_base': target_base, 'head': head,
                                 'worktree': str(work),
                                 'diff_sha256': delivery._fingerprint(work, base),
                                 'pid': 999999}})
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'VERIFIED')
        self.assertEqual(agents.calls, ['review'])
        self.assertEqual(_git(self.repo, 'show', 'integration:a.txt').stdout, b'pending\n')

    def test_resume_refuses_when_the_diff_moved(self):
        work = self.worktree('one')
        base = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        (work / 'a.txt').write_text('pending\n')
        _git(work, 'add', 'a.txt')
        _git(work, 'commit', '-q', '-m', 'pending')
        head = _git(work, 'rev-parse', 'HEAD').stdout.decode().strip()
        plan = self.plan([self.task('one', work, ['a.txt'])])
        self.seed(plan, {'one': {'status': 'RUNNING', 'phase': 'verify', 'base': base,
                                 'head': head, 'worktree': str(work),
                                 'diff_sha256': '0' * 64, 'pid': 999999}})
        state, agents = self.run_delivery(plan, resume=True)
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('diff changed after interruption', state['tasks']['one']['reason'])
        self.assertEqual(agents.calls, [])

    def test_retry_resets_with_a_recoverable_backup_and_stash(self):
        work = self.worktree('one')
        task = self.task('one', work, ['a.txt'],
                         verify=[[PY, '-c',
                                  'import pathlib; pathlib.Path("dirty.txt").write_text("x");'
                                  ' raise SystemExit(1)']])
        plan = self.plan([task])
        state, _ = self.run_delivery(plan)
        self.assertEqual(state['tasks']['one']['status'], 'FAILED')
        failed_head = state['tasks']['one']['head']
        # A retry with a passing check must keep the failed attempt recoverable.
        task['verify'] = [[PY, '-c', 'raise SystemExit(0)']]
        self.seed_edit(plan, lambda record: None)
        state, agents = self.run_delivery(plan, retry=['one'])
        record = state['tasks']['one']
        self.assertEqual(record['status'], 'VERIFIED')
        self.assertEqual(record['attempt'], 2)
        self.assertTrue(record.get('backup_ref'))
        self.assertTrue(record.get('stash'))
        self.assertEqual(_git(work, 'cat-file', '-e', failed_head, check=False).returncode, 0)
        self.assertEqual(_git(work, 'cat-file', '-e', record['stash'], check=False).returncode, 0)
        self.assertEqual(agents.calls, ['execution', 'review'])

class MergeTests(DeliveryCase):
    def test_merge_is_idempotent_after_a_crash_between_merge_and_journal(self):
        work = self.worktree('one')
        task = self.task('one', work, ['a.txt'])
        plan = self.plan([task])
        state, _ = self.run_delivery(plan)
        record = dict(state['tasks']['one'])
        # Rewind the journal to "approved, merge in flight" and keep the real facts.
        record.update(status='APPROVED', phase='merge', post_verification=None)
        self.seed(plan, {'one': record})
        before = _git(self.repo, 'rev-list', '--merges', '--count',
                      'integration').stdout.strip()
        state, agents = self.run_delivery(plan)
        after = _git(self.repo, 'rev-list', '--merges', '--count',
                     'integration').stdout.strip()
        self.assertEqual(before, after)
        self.assertEqual(state['tasks']['one']['status'], 'VERIFIED')
        self.assertEqual(agents.calls, [])

    def test_target_branch_moved_blocks_the_merge(self):
        work = self.worktree('one')
        marker = self.root / 'reviewer-note'

        def move_target(where, prompt, head, digest):
            if marker.exists():
                return None
            marker.write_text('moved')
            (self.repo / 'other.txt').write_text('concurrent\n')
            _git(self.repo, 'add', 'other.txt')
            _git(self.repo, 'commit', '-q', '-m', 'concurrent change')
            return None

        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'])]),
                                     FakeAgents(self, review=move_target))
        self.assertEqual(state['tasks']['one']['status'], 'BLOCKED')
        self.assertIn('target branch moved', state['tasks']['one']['reason'])
        self.assertFalse((self.repo / 'a.txt').exists())
        self.assertTrue((self.repo / 'other.txt').exists())

    def test_merge_conflict_is_aborted_and_target_is_unchanged(self):
        one, two = self.worktree('one'), self.worktree('two')
        agents = FakeAgents(self, execution=lambda work, prompt: (
            (work / 'shared.txt').write_text(Path(work).name + '\n')))
        plan = self.plan([
            self.task('one', one, ['shared.txt']),
            self.task('two', two, ['shared.txt']),
        ])
        state, _ = self.run_delivery(plan, agents)
        self.assertEqual(state['tasks']['one']['status'], 'VERIFIED')
        self.assertEqual(state['tasks']['two']['status'], 'BLOCKED')
        self.assertIn('merge conflict', state['tasks']['two']['reason'])
        self.assertEqual(_git(self.repo, 'show', 'integration:shared.txt').stdout,
                         b'work-one\n')
        self.assertEqual(_git(self.repo, 'status', '--porcelain').stdout, b'')


class PathSafetyTests(DeliveryCase):
    def test_plan_paths_reject_cross_platform_escapes(self):
        work = self.worktree('one')
        for value in ('../outside', '/etc/passwd', 'C:/windows', 'a\\..\\b',
                      'a/../../b', '.git/config', '.orchestration/state.sqlite3',
                      'a//b', 'a/./b', 'a:b', ''):
            with self.subTest(value=value):
                task = self.task('one', work, ['a.txt'])
                task['paths'] = [value]
                with self.assertRaises(ValueError):
                    delivery.run(self.repo, self.plan([task]), self.root / 's.json',
                                 agent=FakeAgents(self), session_probe=self.probe,
                                 profiles=self.profiles)

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

    def test_state_path_inside_repository_is_refused_before_writing(self):
        work = self.worktree('one')
        inside = self.repo / 'delivery-state.json'
        with self.assertRaises(ValueError):
            delivery.run(self.repo, self.plan([self.task('one', work, ['a.txt'])]), inside,
                         agent=FakeAgents(self), session_probe=self.probe,
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

    def test_verification_arguments_are_not_written_to_the_journal(self):
        work = self.worktree('one')
        secret = 'ghp_notARealTokenForTests'
        verify = [[PY, '-c', 'raise SystemExit(0)', secret]]
        state, _ = self.run_delivery(self.plan([self.task('one', work, ['a.txt'],
                                                          verify=verify)]))
        self.assertEqual(state['tasks']['one']['status'], 'VERIFIED')
        self.assertNotIn(secret, self.state.read_text())
        self.assertNotIn(secret, json.dumps(delivery._summary(state)))

    def test_interruption_does_not_leave_partial_json(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])])
        with self.assertRaises(delivery.DeliveryError):
            self.run_delivery(plan, profiles=lambda where: {'plan': {'permissions': []}})
        self.assertFalse(self.state.exists())


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
                                        allows=['docs/alpha.md'], runner=self.fake_gh())
        task = plan['tasks'][0]
        self.assertEqual(task['paths'], ['docs/alpha.md'])
        self.assertEqual(task['verify'], [['python3', '-c', 'raise SystemExit(0)']])
        self.assertEqual(plan['carrier']['digest'],
                         delivery.hashlib.sha256(self.BODY.encode()).hexdigest())
        self.assertFalse(plan['merge'])

    def test_issue_plan_refuses_scope_widening_and_closed_issues(self):
        with self.assertRaises(delivery.DeliveryError):
            delivery.load_issue_plan(230, 'owner/repo', worktree='/tmp/wt',
                                     verify=['["true"]'], allows=['README.md'],
                                     runner=self.fake_gh())
        with self.assertRaises(delivery.DeliveryError):
            delivery.load_issue_plan(230, 'owner/repo', worktree='/tmp/wt',
                                     verify=['["true"]'], runner=self.fake_gh(state='CLOSED'))

    def test_carrier_digest_change_blocks_the_run(self):
        work = self.worktree('one')
        plan = self.plan([self.task('one', work, ['a.txt'])],
                         carrier={'kind': 'issue', 'repo': 'owner/repo', 'number': 230,
                                  'digest': '1' * 64})
        with self.assertRaises(delivery.DeliveryError):
            self.run_delivery(plan, gh=self.fake_gh())
        # A stale carrier fails closed before any journal or agent activity.
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
                   'messages': [{'type': 'user'}, {'type': 'assistant', 'agent': 'plan',
                                                   'finish': 'stop'}]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload), '')
        with mock.patch.object(delivery.subprocess, 'run', return_value=completed) as run:
            result = delivery.session_export('ses_x', self.repo)
        self.assertEqual(run.call_args.args[0][:3],
                         ['opencode', 'session', 'export'])
        self.assertNotIn('--sanitize', run.call_args.args[0])
        self.assertTrue(result['ok'])
        self.assertEqual(result['agents'], ['plan'])


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
