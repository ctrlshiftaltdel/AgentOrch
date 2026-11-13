import json
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import autonomous_delivery as delivery


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git(self.repo, 'init', '-b', 'main')
        self.git(self.repo, 'config', 'user.email', 'test@example.invalid')
        self.git(self.repo, 'config', 'user.name', 'Test')
        (self.repo / 'base').write_text('base')
        self.git(self.repo, 'add', 'base')
        self.git(self.repo, 'commit', '-m', 'base')
        self.work = self.root / 'work'
        self.git(self.repo, 'worktree', 'add', '-b', 'task/one', str(self.work))
        self.plan = {'tasks': [{'id': 'one', 'worktree': str(self.work),
                                'prompt': 'make a change', 'paths': ['out.txt'],
                                'verify': [['true']], 'depends': []}]}
        self.state = self.root / 'state.json'

    @staticmethod
    def git(where, *args):
        return subprocess.run(['git', *args], cwd=where, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_real_diff_verification_and_independent_review_are_not_approval(self):
        calls = []
        def agent(worktree, prompt, role):
            calls.append(role)
            if role == 'execution':
                (worktree / 'out.txt').write_text('changed')
            return {'exit_code': 0, 'session_id': 'ses_' + role, 'text': 'approved'}
        result = delivery.run(self.repo, self.plan, self.state, agent=agent)
        task = result['tasks']['one']
        self.assertEqual(calls, ['execution', 'review'])
        self.assertEqual(task['status'], 'BLOCKED')
        self.assertEqual(task['reason'], 'independent approval and merge not implemented')
        self.assertEqual(task['verification'][0]['exit_code'], 0)
        self.assertEqual(task['changed_paths'], ['out.txt'])
        self.assertEqual(len(task['head']), 40)

    def test_interrupted_task_is_not_restarted(self):
        self.state.write_text(json.dumps({'version': 1, 'tasks': {'one': {'status': 'RUNNING'}}}))
        result = delivery.run(self.repo, self.plan, self.state,
                              agent=lambda *args: self.fail('must not restart'))
        self.assertEqual(result['tasks']['one']['status'], 'UNKNOWN')

    def test_outside_path_blocks_and_other_task_continues(self):
        other = self.root / 'other'
        self.git(self.repo, 'worktree', 'add', '-b', 'task/two', str(other))
        self.plan['tasks'].append({'id': 'two', 'worktree': str(other),
                                   'prompt': 'change', 'paths': ['out.txt'],
                                   'verify': [['true']], 'depends': []})
        def agent(worktree, prompt, role):
            if role == 'execution':
                (worktree / ('bad.txt' if worktree == self.work else 'out.txt')).write_text('x')
            return {'exit_code': 0, 'session_id': 'ses_' + role, 'text': 'done'}
        result = delivery.run(self.repo, self.plan, self.state, agent=agent)
        self.assertIn('scope', result['tasks']['one']['reason'])
        self.assertEqual(result['tasks']['two']['reason'], 'independent approval and merge not implemented')

    def test_staged_change_hidden_by_worktree_revert_is_out_of_scope(self):
        def agent(worktree, prompt, role):
            (worktree / 'out.txt').write_text('allowed')
            (worktree / 'base').write_text('staged forbidden')
            self.git(worktree, 'add', 'base')
            (worktree / 'base').write_text('base')
            return {'exit_code': 0, 'session_id': 'ses_execution'}
        result = delivery.run(self.repo, self.plan, self.state, agent=agent)
        self.assertIn('scope', result['tasks']['one']['reason'])

    def test_opencode_uses_private_server_for_worktree_location(self):
        events = '\n'.join(map(json.dumps, [
            {'type': 'text', 'sessionID': 'ses_test', 'part': {'text': 'done'}},
            {'type': 'step_finish', 'sessionID': 'ses_test', 'part': {'reason': 'stop'}},
        ]))
        with mock.patch.object(delivery.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, events, '')) as run:
            delivery.opencode(self.work, 'task', 'execution')
        self.assertIn('--standalone', run.call_args.args[0])
        self.assertNotIn('--auto', run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs['cwd'], self.work)
        self.assertEqual(run.call_args.kwargs['env']['PWD'], str(self.work))

    def test_failed_verification_is_not_retried_on_restart(self):
        self.plan['tasks'][0]['verify'] = [['false']]
        calls = []
        def agent(worktree, prompt, role):
            calls.append(role)
            (worktree / 'out.txt').write_text('changed')
            return {'exit_code': 0, 'session_id': 'ses_execution'}
        first = delivery.run(self.repo, self.plan, self.state, agent=agent)
        self.assertEqual(first['tasks']['one']['verification'][0]['exit_code'], 1)
        delivery.run(self.repo, self.plan, self.state, agent=agent)
        self.assertEqual(calls, ['execution'])

    def test_symlink_change_is_blocked(self):
        def agent(worktree, prompt, role):
            (worktree / 'out.txt').symlink_to(self.root / 'outside')
            return {'exit_code': 0, 'session_id': 'ses_execution'}
        result = delivery.run(self.repo, self.plan, self.state, agent=agent)
        self.assertIn('symlink', result['tasks']['one']['reason'])

    def test_cli_does_not_succeed_with_missing_tasks_or_print_model_text(self):
        planfile = self.root / 'plan.json'
        planfile.write_text(json.dumps(self.plan))
        output = io.StringIO()
        args = ['delivery', '--repo', str(self.repo), '--plan', str(planfile),
                '--state', str(self.state), '--execute']
        state = {'tasks': {'other': {'status': 'VERIFIED', 'review_text': 'private'}}}
        with mock.patch('sys.argv', args), mock.patch.object(delivery, 'run', return_value=state), contextlib.redirect_stdout(output):
            self.assertEqual(delivery.main(), 1)
        self.assertNotIn('private', output.getvalue())


if __name__ == '__main__':
    unittest.main()
