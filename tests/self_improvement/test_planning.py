"""AC-3 / AC-4: evidence, conflict and risk gates; complete task packets only.

The risk tests use real throw-away Git repositories. A caller-supplied diff,
SHA, `changed_paths` list or authorization is never evidence: only the
repository's own commit range and clean worktree can decide.
"""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import loop_support, support
from agentorch_self_improvement import planning  # noqa: E402


class _ShaLike:
    """A non-string object whose ``__str__`` would look like a valid SHA.

    ``_git_evidence_state`` used to call ``str()`` on the required task-carrier
    SHA, so an object like this one reached Git as a seemingly valid commit id.
    """

    def __init__(self, value):
        self._value = value

    def __str__(self):
        return self._value


def plan_one(record, spec=None, **kwargs):
    kwargs.setdefault('edition', 'local')
    kwargs.setdefault('repo_root', loop_support.DEFAULT_REPO_ROOT)
    kwargs.setdefault('trusted_task', loop_support.state()['task'])
    kwargs.setdefault('required_base_sha', loop_support.DEFAULT_BASE_SHA)
    kwargs.setdefault('required_head_sha', loop_support.DEFAULT_HEAD_SHA)
    return planning.plan_tasks(
        lesson_records=[record], specs={record['lesson_id']: spec if spec is not None
                                        else loop_support.spec()},
        **kwargs)


class EligibilityGateTests(unittest.TestCase):
    def test_confirmed_fact_alone_cannot_become_a_task(self):
        record = support.verified_lesson()
        plan = plan_one(record, loop_support.spec())
        self.assertEqual(plan.proposals[0]['status'], 'NO_TASK')
        self.assertIn('GENERAL_LESSON', plan.proposals[0]['status_reason'])

    def test_unverified_assumption_never_becomes_a_task(self):
        record = support.lesson(content_class='UNVERIFIED_ASSUMPTION',
                                evidence_level='E1_SOURCE_ARTIFACT',
                                status='NEEDS_VERIFICATION')
        proposal = plan_one(record, loop_support.spec()).proposals[0]
        self.assertEqual(proposal['status'], 'NO_TASK')
        self.assertIsNone(proposal['required_human_decision'])

    def test_duplicate_lesson_does_not_get_a_second_task(self):
        record = loop_support.general_lesson(duplicate_of='LSN-TALKDND-000')
        proposal = plan_one(record, loop_support.spec()).proposals[0]
        self.assertEqual(proposal['status'], 'NO_TASK')
        self.assertIn('duplicate', proposal['status_reason'])

    def test_open_conflict_requires_hd04_even_with_a_complete_packet(self):
        record = loop_support.open_conflict_lesson()
        proposal = plan_one(record, loop_support.spec()).proposals[0]
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-04')
        self.assertIn('OPEN conflict', proposal['status_reason'])

    def test_already_covered_lesson_creates_no_duplicate_rule(self):
        record = loop_support.general_lesson()
        coverage = {record['lesson_id']: {'status': 'COVERED',
                                          'evidence': 'docs/orchestration.md 4.1'}}
        proposal = plan_one(record, loop_support.spec(), coverage=coverage).proposals[0]
        self.assertEqual(proposal['status'], 'NO_TASK')
        self.assertIn('already covers', proposal['status_reason'])

    def test_existing_task_is_reused_instead_of_duplicated(self):
        record = loop_support.general_lesson()
        proposal = plan_one(
            record, loop_support.spec(),
            existing_tasks={record['lesson_id']: 'TASK-0007'}).proposals[0]
        self.assertEqual(proposal['status'], 'ALREADY_REGISTERED')
        self.assertIn('TASK-0007', proposal['status_reason'])


class GitRiskTests(unittest.TestCase):
    """Risk evidence comes from Git, never from the caller's strings."""

    def setUp(self):
        self.repo = loop_support.GitRepo()
        self.addCleanup(self.repo.close)

    def task_for(self, base, head, scope, *, review=True, role='reviewer',
                 repo_root=None):
        task = {'task_id': 'TASK-RISK', 'base_sha': base, 'head_sha': head,
                'executor': 'executor-1'}
        if review:
            task['review'] = loop_support.review_receipt(
                repo_root or self.repo.root, base, head, scope, role=role)
        return task

    def plan(self, spec, *, review=True, role='reviewer', repo_root=None):
        repo_root = repo_root or self.repo.root
        evidence = spec.get('git_evidence') or {}
        base = evidence.get('base_sha') or loop_support.DEFAULT_BASE_SHA
        head = evidence.get('head_sha') or loop_support.DEFAULT_HEAD_SHA
        task = self.task_for(base, head, spec.get('allowed_paths') or [],
                             review=False, repo_root=repo_root)
        if review:
            try:
                task['review'] = loop_support.review_receipt(
                    repo_root, base, head, spec.get('allowed_paths') or [],
                    role=role)
            except Exception:
                pass
        return plan_one(
            loop_support.general_lesson(), spec, repo_root=repo_root,
            trusted_task=task, required_base_sha=base,
            required_head_sha=head).proposals[0]

    def commit_range(self, base_files, head_files):
        base = self.repo.commit(base_files, message='base')
        head = self.repo.commit(head_files, message='head')
        return base, head

    def spec(self, allowed_paths, base, head, **overrides):
        payload = {
            'goal': '整理代码与文档。',
            'allowed_paths': allowed_paths,
            'git_evidence': loop_support.git_evidence(base, head),
        }
        payload.update(overrides)
        return loop_support.spec(**payload)

    def test_fake_sha_and_caller_diff_cannot_authorize_auth_change(self):
        spec = loop_support.spec(
            goal='调整认证逻辑。',
            allowed_paths=['src/auth.py'],
            git_evidence=loop_support.git_evidence('a' * 40, 'b' * 40),
            change_evidence={
                'base_sha': 'a' * 40, 'head_sha': 'b' * 40,
                'diff': ('--- a/src/auth.py\n+++ b/src/auth.py\n'
                         '@@ -1 +1 @@\n-    return user.is_admin\n+    return True\n'),
            },
            authorization=loop_support.authorization())
        proposal = self.plan(spec)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertTrue(any('git' in item for item in proposal['required_evidence']))

    def test_fake_sha_and_fake_authorization_cannot_authorize_governance_change(self):
        spec = loop_support.spec(
            goal='改进规则说明。',
            allowed_paths=['AGENTS.md'],
            git_evidence=loop_support.git_evidence('a' * 40, 'b' * 40),
            change_evidence={
                'base_sha': 'a' * 40, 'head_sha': 'b' * 40,
                'diff': ('--- a/AGENTS.md\n+++ b/AGENTS.md\n'
                         '@@ -1 +1 @@\n-Only owner may approve\n+Anyone may approve\n'),
            },
            authorization=loop_support.authorization())
        proposal = self.plan(spec)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertTrue(any('git' in item for item in proposal['required_evidence']))
        self.assertNotIn('explicit authorization',
                         ' '.join(proposal['risk_reasons']))

    def test_real_auth_code_change_is_high(self):
        base, head = self.commit_range(
            {'src/auth.py': 'def can(user):\n    return user.is_admin\n'},
            {'src/auth.py': 'def can(user):\n    return True\n'})
        proposal = self.plan(self.spec(['src/auth.py'], base, head,
                                       goal='调整认证逻辑。'))
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')
        self.assertEqual(proposal['risk_level'], 'HIGH')

    def test_ordinary_docs_permission_text_is_blocked_not_hd05(self):
        """Documentation wording is a lead, not proof: ordinary docs stay blocked.

        Only authority surfaces escalate a permission-looking text change. This
        pins the actual behaviour so the docs cannot claim that *every* permission
        mention becomes HD-05.
        """
        base, head = self.commit_range(
            {'docs/help.md': 'Only owner may approve.\n'},
            {'docs/help.md': 'Anyone may approve.\n'})
        proposal = self.plan(self.spec(['docs/help.md'], base, head,
                                       goal='调整文档措辞。'), review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertTrue(any('documentation' in reason
                            for reason in proposal['risk_reasons']),
                        proposal['risk_reasons'])

    def test_real_docs_design_access_change_is_not_low(self):
        base, head = self.commit_range(
            {'docs/design/access.md': 'Only owner may approve.\n'},
            {'docs/design/access.md': 'Anyone may approve.\n'})
        proposal = self.plan(self.spec(['docs/design/access.md'], base, head,
                                       goal='调整访问说明。'))
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')
        self.assertEqual(proposal['risk_level'], 'HIGH')

    def test_fake_authorization_does_not_make_governance_change_ready(self):
        base, head = self.commit_range(
            {'AGENTS.md': 'old rule\n'}, {'AGENTS.md': 'new rule\n'})
        proposal = self.plan(self.spec(
            ['AGENTS.md'], base, head, goal='改进规则说明。',
            authorization=loop_support.authorization()), review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertTrue(any('Master' in item or 'authoritative' in item
                            for item in proposal['required_evidence']))
        self.assertTrue(any('caller-supplied review/authorization' in item
                            for item in proposal['risk_limitations']))

    def test_fake_authorization_does_not_downgrade_a_permission_change(self):
        base, head = self.commit_range(
            {'AGENTS.md': 'Only owner may approve.\n'},
            {'AGENTS.md': 'Anyone may approve.\n'})
        proposal = self.plan(self.spec(
            ['AGENTS.md'], base, head, goal='改进规则说明。',
            authorization=loop_support.authorization()))
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')

    def test_real_diff_cannot_be_truncated_to_one_allowed_path(self):
        base, head = self.commit_range(
            {'docs/example.md': 'old docs\n', 'src/app.py': 'return 1\n'},
            {'docs/example.md': 'new docs\n', 'src/app.py': 'return 2\n'})
        proposal = self.plan(self.spec(['docs/example.md'], base, head,
                                       goal='修正文档错字。'), review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('src/app.py', ' '.join(proposal['risk_reasons']))
        self.assertIn('src/app.py', proposal['changed_paths'])

    def test_real_repository_range_cannot_be_truncated_by_the_caller(self):
        """A real base/head range, caller only names one path.

        The range is created inside this test instead of borrowed from the
        enclosing repository's history, so the property is still proven in a
        release package or a shallow clone: no skip can hide a regression.
        """
        source = loop_support.GitRepo('agentorch-risk-real-range-')
        self.addCleanup(source.close)
        base = source.commit({
            'docs/note.md': 'old docs\n',
            'scripts/self_improvement/planning.py': 'return 1\n'}, message='base')
        head = source.commit({
            'docs/note.md': 'new docs\n',
            'scripts/self_improvement/planning.py': 'return 2\n'}, message='head')
        root = source.root
        tmp = Path(tempfile.mkdtemp(prefix='agentorch-risk-worktree-',
                                    dir=os.environ.get('TMPDIR') or None))
        self.addCleanup(shutil.rmtree, tmp, True)
        add = subprocess.run(['git', '-C', str(root), 'worktree', 'add',
                              '--detach', str(tmp), head], capture_output=True)
        self.assertEqual(add.returncode, 0, add.stderr.decode('utf-8', 'replace'))
        self.addCleanup(subprocess.run,
                        ['git', '-C', str(root), 'worktree', 'remove', '--force', str(tmp)],
                        capture_output=True)
        spec = loop_support.spec(
            goal='修正文档错字。',
            allowed_paths=['docs/note.md'],
            git_evidence=loop_support.git_evidence(base, head),
            change_evidence={'base_sha': base, 'head_sha': head,
                             'diff': '--- a/docs/note.md\n'
                                     '+++ b/docs/note.md\n'
                                     '@@ -1 +1 @@\n-old\n+new\n'})
        details = planning.assess_risk_details(
            {'allowed_paths': ['docs/note.md'],
             'git_evidence': loop_support.git_evidence(base, head)},
            {}, repo_root=tmp)
        self.assertNotEqual(details['level'], 'LOW')
        self.assertIn('scripts/self_improvement/planning.py',
                      details['changed_paths'])
        task = {'task_id': 'TASK-REAL', 'base_sha': base, 'head_sha': head,
                'executor': 'executor-1'}
        proposal = plan_one(
            loop_support.general_lesson(), spec, repo_root=tmp,
            trusted_task=task, required_base_sha=base,
            required_head_sha=head).proposals[0]
        self.assertIn(proposal['status'], ('BLOCKED', 'NEEDS_HUMAN_DECISION'))
        self.assertIn(proposal['risk_level'], ('NEEDS_EVIDENCE', 'HIGH'))
        self.assertNotIn(proposal['status'], ('READY', 'NO_TASK'))
        self.assertIn('scripts/self_improvement/planning.py',
                      ' '.join(proposal['risk_reasons']))
        self.assertIn('scripts/self_improvement/planning.py',
                      proposal['changed_paths'])

    def test_illegal_allowed_paths_reject_before_any_diff_can_help(self):
        spec = loop_support.spec(
            allowed_paths=['docs/', '../other-project'],
            git_evidence=loop_support.git_evidence(
                loop_support.DEFAULT_BASE_SHA, loop_support.DEFAULT_HEAD_SHA))
        proposal = self.plan(spec)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertIn('invalid allowed_paths', proposal['status_reason'])
        self.assertNotEqual(proposal['status'], 'READY')

    def test_absolute_backslash_and_parent_paths_are_rejected(self):
        for path in ('/tmp/x', 'C:\\x', '..\\x', 'docs/../x', '../x'):
            with self.subTest(path=path):
                errors = planning.validate_task_packet(
                    loop_support.spec(allowed_paths=[path]))
                self.assertTrue(errors, f'{path} was accepted')

    def test_mixed_text_and_binary_blocks(self):
        base, head = self.commit_range(
            {'docs/example.md': 'old\n', 'assets/logo.bin': b'\x00old'},
            {'docs/example.md': 'new\n', 'assets/logo.bin': b'\x00new'})
        proposal = self.plan(self.spec(
            ['docs/example.md', 'assets/logo.bin'], base, head))
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('binary', ' '.join(proposal['risk_reasons']))

    def test_rename_blocks(self):
        base = self.repo.commit({'docs/old.md': 'same\n'}, message='base')
        self.repo.mv('docs/old.md', 'docs/new.md')
        head = self.repo.commit({}, message='rename')
        proposal = self.plan(self.spec(
            ['docs/old.md', 'docs/new.md'], base, head))
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('rename', ' '.join(proposal['risk_reasons']))

    def test_symlink_blocks(self):
        base = self.repo.commit({'docs/target.md': 'target\n'}, message='base')
        self.repo.symlink('docs/link.md', 'target.md')
        head = self.repo.commit({}, message='symlink')
        proposal = self.plan(self.spec(['docs/link.md', 'docs/target.md'], base, head))
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('symlink', ' '.join(proposal['risk_reasons']))

    def test_submodule_blocks(self):
        sub = loop_support.GitRepo('agentorch-risk-sub-')
        self.addCleanup(sub.close)
        sub_sha = sub.commit({'sub.md': 'sub\n'}, message='sub base')
        base = self.repo.commit({'README.md': 'base\n'}, message='base')
        self.repo._git('-c', 'protocol.file.allow=always', 'submodule', 'add',
                       str(sub.root), 'sub')
        self.repo._git('config', 'diff.ignoreSubmodules', 'all')
        head = self.repo.commit({}, message='submodule')
        proposal = self.plan(self.spec(['README.md', 'sub', '.gitmodules'], base, head))
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('submodule', ' '.join(proposal['risk_reasons']))
        self.assertEqual(self.repo.head(), head)
        self.assertFalse(sub_sha == '')

    def test_uncommitted_worktree_blocks(self):
        base, head = self.commit_range(
            {'docs/example.md': 'old line\n'}, {'docs/example.md': 'old line  \n'})
        self.repo.write('docs/example.md', 'dirty\n')
        proposal = self.plan(self.spec(['docs/example.md'], base, head))
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('working tree', ' '.join(proposal['risk_reasons']))

    def test_untracked_file_blocks(self):
        base, head = self.commit_range(
            {'docs/example.md': 'old line\n'}, {'docs/example.md': 'old line  \n'})
        self.repo.write('notes.txt', 'untracked\n')
        proposal = self.plan(self.spec(['docs/example.md'], base, head))
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('untracked', ' '.join(proposal['risk_reasons']))

    def test_fake_sha_does_not_exist(self):
        spec = loop_support.spec(
            allowed_paths=['docs/example.md'],
            git_evidence=loop_support.git_evidence('a' * 40, 'b' * 40))
        proposal = self.plan(spec)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_head_must_be_checked_out(self):
        base = self.repo.commit({'docs/example.md': 'old\n'}, message='base')
        head = self.repo.commit({'docs/example.md': 'old  \n'}, message='head')
        self.repo.commit({'docs/example.md': 'after\n'}, message='after')
        proposal = self.plan(self.spec(['docs/example.md'], base, head))
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('HEAD', ' '.join(proposal['risk_reasons']))

    def test_base_must_be_an_ancestor_of_head(self):
        base = self.repo.commit({'docs/example.md': 'base\n'}, message='base')
        self.repo._git('checkout', '--orphan', 'other')
        self.repo._git('rm', '-rf', '.')
        head = self.repo.commit({'docs/other.md': 'head\n'}, message='orphan')
        proposal = self.plan(self.spec(
            ['docs/example.md', 'docs/other.md'], base, head))
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('ancestor', ' '.join(proposal['risk_reasons']))

    def test_shallow_repository_blocks(self):
        source = loop_support.GitRepo('agentorch-risk-source-')
        self.addCleanup(source.close)
        source.commit({'docs/example.md': 'one\n'}, message='one')
        base = source.commit({'docs/example.md': 'two\n'}, message='two')
        head = source.commit({'docs/example.md': 'three\n'}, message='three')
        clone = Path(tempfile.mkdtemp(prefix='agentorch-risk-shallow-',
                                      dir=os.environ.get('TMPDIR') or None))
        self.addCleanup(shutil.rmtree, clone, True)
        completed = subprocess.run(
            ['git', 'clone', '--depth', '2', 'file://' + str(source.root),
             str(clone)], capture_output=True)
        self.assertEqual(completed.returncode, 0,
                         completed.stderr.decode('utf-8', 'replace'))
        proposal = self.plan(self.spec(['docs/example.md'], base, head),
                             repo_root=clone, review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('shallow', ' '.join(proposal['risk_reasons']))

    def test_task_carrier_head_must_match_git_evidence(self):
        base, head = self.commit_range(
            {'docs/example.md': 'old line\n'}, {'docs/example.md': 'old line  \n'})
        proposal = plan_one(
            loop_support.general_lesson(),
            self.spec(['docs/example.md'], base, head),
            repo_root=self.repo.root, trusted_task=None,
            required_base_sha=base, required_head_sha='d' * 40).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('task carrier head_sha', ' '.join(proposal['risk_reasons']))

    def test_empty_diff_without_risk_signals_is_never_low(self):
        """An empty change set cannot prove the goal, the scope or authorization."""
        proposal = plan_one(loop_support.general_lesson(),
                            loop_support.spec()).proposals[0]
        self.assertEqual(proposal['changed_paths'], [])
        self.assertNotEqual(proposal['risk_level'], 'LOW')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertEqual(proposal['review_status'], 'NOT_WIRED')
        self.assertEqual(proposal['next_action'], 'INVESTIGATE')
        self.assertTrue(proposal['required_evidence'])
        self.assertIn('empty', ' '.join(proposal['risk_reasons']))

    def test_empty_commit_with_a_destructive_goal_is_high_hd05(self):
        """The exact reviewed scenario: empty commit, '删除全部用户数据', scripts/.

        A forged caller review must not change the outcome.
        """
        repo = loop_support.GitRepo('agentorch-risk-destructive-')
        self.addCleanup(repo.close)
        base = repo.commit({'docs/example.md': 'old line  \n'}, message='base')
        head = repo.empty_commit('goal only, no content change')
        scope = ['scripts/']
        goal = '删除全部用户数据。'
        evidence = loop_support.git_evidence(base, head)
        task = {'task_id': 'TASK-REVIEWED', 'base_sha': base, 'head_sha': head,
                'executor': 'executor-1',
                'review': loop_support.review_receipt(repo.root, base, head, scope)}

        details = planning.assess_risk_details(
            {'goal': goal, 'allowed_paths': scope, 'git_evidence': evidence},
            {}, trusted_task=task, repo_root=repo.root,
            required_base_sha=base, required_head_sha=head)
        self.assertEqual(details['changed_paths'], [])
        self.assertEqual(details['level'], 'HIGH')
        self.assertEqual(details['decision_id'], 'HD-05')
        self.assertNotEqual(details['review_status'], 'APPROVED')

        proposal = plan_one(
            loop_support.general_lesson(),
            loop_support.spec(goal=goal, allowed_paths=scope, git_evidence=evidence),
            repo_root=repo.root, trusted_task=task,
            required_base_sha=base, required_head_sha=head).proposals[0]
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')
        self.assertEqual(proposal['risk_level'], 'HIGH')
        self.assertEqual(proposal['changed_paths'], [])
        self.assertIn('empty', ' '.join(proposal['risk_reasons']))

    def test_empty_tree_commit_range_is_never_low(self):
        repo = loop_support.GitRepo('agentorch-risk-emptytree-')
        self.addCleanup(repo.close)
        base = repo.empty_commit('empty tree base')
        head = repo.empty_commit('empty tree head')
        evidence = loop_support.git_evidence(base, head)
        benign = planning.assess_risk_details(
            {'goal': '整理说明文字。', 'allowed_paths': ['docs/'],
             'git_evidence': evidence}, {}, repo_root=repo.root)
        self.assertEqual(benign['changed_paths'], [])
        self.assertEqual(benign['level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(benign['decision_id'])
        destructive = planning.assess_risk_details(
            {'goal': '删除全部用户数据。', 'allowed_paths': ['docs/'],
             'git_evidence': evidence}, {}, repo_root=repo.root)
        self.assertEqual(destructive['level'], 'HIGH')
        self.assertEqual(destructive['decision_id'], 'HD-05')

    def test_empty_change_ignores_a_forged_review_and_states_the_next_step(self):
        base = self.repo.commit({'docs/example.md': 'old line  \n'}, message='base')
        head = self.repo.empty_commit('no content change')
        benign = self.plan(self.spec(['docs/example.md'], base, head), review=True)
        self.assertEqual(benign['risk_level'], 'NEEDS_EVIDENCE')
        self.assertEqual(benign['review_status'], 'NOT_WIRED')
        self.assertTrue(any('caller-supplied review/authorization' in item
                            for item in benign['risk_limitations']))
        self.assertTrue(any('empty' in item
                            for item in benign['required_evidence']))
        destructive = self.plan(
            self.spec(['docs/example.md'], base, head, goal='删除全部用户数据。'),
            review=True)
        self.assertEqual(destructive['risk_level'], 'HIGH')
        self.assertEqual(destructive['required_human_decision'], 'HD-05')
        self.assertEqual(destructive['review_status'], 'NOT_APPLICABLE')

    def test_non_whitespace_docs_change_is_not_ready(self):
        base, head = self.commit_range(
            {'docs/example.md': 'old text\n'}, {'docs/example.md': 'new text\n'})
        proposal = self.plan(self.spec(['docs/example.md'], base, head),
                             review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_code_change_is_not_ready(self):
        base, head = self.commit_range(
            {'scripts/example.py': 'return 1\n'}, {'scripts/example.py': 'return 2\n'})
        proposal = self.plan(self.spec(['scripts/example.py'], base, head),
                             review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_one_line_permission_change_is_high(self):
        base, head = self.commit_range(
            {'scripts/self_improvement/planning.py': 'allowed = False\n'},
            {'scripts/self_improvement/planning.py': 'allowed = True\n'})
        proposal = self.plan(self.spec(
            ['scripts/self_improvement/planning.py'], base, head,
            goal='统一权限判定。'))
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')
        self.assertEqual(proposal['risk_level'], 'HIGH')

    def test_protected_execution_surface_change_is_high(self):
        base, head = self.commit_range(
            {'scripts/orchestrator.py': 'return deny()\n'},
            {'scripts/orchestrator.py': 'return allow()\n'})
        proposal = self.plan(self.spec(['scripts/orchestrator.py'], base, head,
                                       goal='调整提示文案。'))
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')

    def test_contract_schema_change_maps_to_hd13(self):
        base, head = self.commit_range(
            {'docs/contracts/lesson.schema.json': '{"enum":["A"]}\n'},
            {'docs/contracts/lesson.schema.json': '{"enum":["A","B"]}\n'})
        proposal = self.plan(self.spec(
            ['docs/contracts/lesson.schema.json'], base, head,
            goal='改变 lesson schema 的枚举取值。'))
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-13')

    def test_schema_named_documentation_typo_is_not_hd13(self):
        """A document whose file name contains "schema" is not a schema.

        ``docs/schema-notes.md`` only proves a documentation edit when its text
        changes. A basename substring match used to escalate a typo fix to
        HIGH / HD-13 even though no schema or contract changed.
        """
        base, head = self.commit_range(
            {'docs/schema-notes.md': 'schema notes typo\n'},
            {'docs/schema-notes.md': 'schema notes fixed\n'})
        proposal = self.plan(self.spec(['docs/schema-notes.md'], base, head,
                                       goal='修正文档错字。'), review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertFalse(any('contract/schema change' in reason
                             for reason in proposal['risk_reasons']),
                         proposal['risk_reasons'])
        self.assertTrue(any('documentation' in reason
                            for reason in proposal['risk_reasons']),
                        proposal['risk_reasons'])

    def test_schema_identity_is_not_a_filename_substring(self):
        self.assertTrue(planning._is_schema_path(
            'docs/contracts/schemas/lesson.schema.json'))
        self.assertTrue(planning._is_schema_path(
            'scripts/verification_planner/schema.json'))
        self.assertFalse(planning._is_schema_path('docs/schema-notes.md'))
        self.assertFalse(planning._is_schema_path('docs/schema.md'))
        self.assertFalse(planning._is_schema_path('src/schema_helpers.py'))

    def test_irreversible_code_change_is_high(self):
        base, head = self.commit_range(
            {'scripts/migrate.py': 'pass\n'},
            {'scripts/migrate.py': 'cursor.execute("DROP TABLE lessons")\n'})
        proposal = self.plan(self.spec(['scripts/migrate.py'], base, head,
                                       goal='清理旧数据。'))
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')

    def test_caller_flags_do_not_override_git_risk(self):
        base, head = self.commit_range(
            {'src/auth.py': 'def can(user):\n    return user.is_admin\n'},
            {'src/auth.py': 'def can(user):\n    return True\n'})
        proposal = self.plan(self.spec(
            ['src/auth.py'], base, head, goal='调整认证逻辑。',
            low_risk=True, approved=True))
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')

    def test_safe_docs_diff_does_not_prove_a_high_risk_task_scope(self):
        base, head = self.commit_range(
            {'docs/readme.md': 'old line\n'},
            {'docs/readme.md': 'old line  \n'})
        proposal = self.plan(self.spec(
            ['docs/', 'scripts/orchestrator.py'], base, head,
            goal='修改任务状态机以允许跳过审查。'), review=True)
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')
        self.assertEqual(proposal['risk_level'], 'HIGH')
        self.assertIn('not proven by the current diff',
                      ' '.join(proposal['risk_reasons']))

    def test_spec_cannot_choose_an_arbitrary_hidden_baseline(self):
        first = self.repo.commit(
            {'src/auth.py': 'def can(user):\n    return user.is_admin\n'},
            message='base')
        hidden = self.repo.commit(
            {'src/auth.py': 'def can(user):\n    return True\n'},
            message='hidden permission change')
        head = self.repo.commit(
            {'docs/example.md': 'old line  \n'}, message='docs whitespace')
        task = {'task_id': 'TASK-BASE', 'base_sha': first, 'head_sha': head,
                'executor': 'executor-1'}
        proposal = plan_one(
            loop_support.general_lesson(),
            self.spec(['docs/example.md'], hidden, head),
            repo_root=self.repo.root, trusted_task=task,
            required_base_sha=first, required_head_sha=head).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('base_sha', ' '.join(proposal['risk_reasons']))

        spec = loop_support.spec(
            allowed_paths=['docs/example.md'], goal='修正文档错字。')
        spec.pop('git_evidence')
        proposal = plan_one(
            loop_support.general_lesson(), spec, repo_root=self.repo.root,
            trusted_task=task, required_base_sha=first,
            required_head_sha=head).proposals[0]
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')

    def test_replace_ref_cannot_hide_an_auth_change(self):
        base = self.repo.commit(
            {'src/auth.py': 'def can(user):\n    return user.is_admin\n'},
            message='base')
        head = self.repo.commit(
            {'src/auth.py': 'def can(user):\n    return True\n'},
            message='head')
        self.repo._git('replace', base, head)
        proposal = self.plan(self.spec(['src/auth.py'], base, head),
                             review=False)
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')

    def test_assume_unchanged_cannot_hide_a_worktree_change(self):
        base, head = self.commit_range(
            {'docs/example.md': 'old line\n'},
            {'docs/example.md': 'old line  \n'})
        self.repo._git('update-index', '--assume-unchanged', 'docs/example.md')
        self.repo.write('docs/example.md', 'hidden permission change\n')
        proposal = self.plan(self.spec(['docs/example.md'], base, head),
                             review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('assume-unchanged', ' '.join(proposal['risk_reasons']))

    def test_skip_worktree_cannot_hide_a_worktree_change(self):
        base, head = self.commit_range(
            {'docs/example.md': 'old line\n'},
            {'docs/example.md': 'old line  \n'})
        self.repo._git('update-index', '--skip-worktree', 'docs/example.md')
        self.repo.write('docs/example.md', 'hidden permission change\n')
        proposal = self.plan(self.spec(['docs/example.md'], base, head),
                             review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('skip-worktree', ' '.join(proposal['risk_reasons']))

    def test_ignored_file_under_allowed_path_blocks(self):
        base = self.repo.commit(
            {'.gitignore': 'src/auth.py\n', 'docs/example.md': 'old line\n'},
            message='base')
        head = self.repo.commit(
            {'docs/example.md': 'old line  \n'}, message='head')
        self.repo.write('src/auth.py', 'def can(user):\n    return True\n')
        proposal = self.plan(
            self.spec(['docs/example.md', 'src/auth.py'], base, head),
            review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('ignored', ' '.join(proposal['risk_reasons']))

    def test_recheck_detects_a_worktree_change_after_planning(self):
        base = self.repo.commit({'docs/help.md': 'old line\n'}, message='base')
        head = self.repo.empty_commit()
        proposal = self.plan(self.spec(['docs/help.md'], base, head),
                             review=False)
        stable, why = planning.recheck_git_evidence(
            proposal, repo_root=self.repo.root,
            required_base_sha=base, required_head_sha=head)
        self.assertTrue(stable, why)
        self.repo.write('docs/help.md', 'changed after planning\n')
        stable, why = planning.recheck_git_evidence(
            proposal, repo_root=self.repo.root,
            required_base_sha=base, required_head_sha=head)
        self.assertFalse(stable)
        self.assertTrue(why)

    def test_whitespace_only_code_block_change_is_not_low(self):
        base, head = self.commit_range(
            {'docs/help.md': '```sh\nchmod 0 777 secret\n```\n'},
            {'docs/help.md': '```sh\nchmod 0777 secret\n```\n'})
        proposal = self.plan(
            self.spec(['docs/help.md'], base, head, goal='整理文档空白。'),
            review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_whitespace_only_shell_comment_change_is_not_low(self):
        base, head = self.commit_range(
            {'docs/help.md': '```sh\necho safe #; rm -rf /target\n```\n'},
            {'docs/help.md': '```sh\necho safe#; rm -rf /target\n```\n'})
        proposal = self.plan(
            self.spec(['docs/help.md'], base, head, goal='整理文档空白。'),
            review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_docs_typo_with_receipt_is_still_blocked(self):
        base, head = self.commit_range(
            {'docs/help.md': 'No permisssion changes.\n'},
            {'docs/help.md': 'No permission changes.\n'})
        proposal = self.plan(
            self.spec(['docs/help.md'], base, head, goal='修正文档错字。'),
            review=True)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertEqual(proposal['review_status'], 'NOT_WIRED')
        self.assertTrue(proposal['required_evidence'])

    def test_docs_typo_without_trusted_review_is_blocked(self):
        base, head = self.commit_range(
            {'docs/help.md': 'No permisssion changes.\n'},
            {'docs/help.md': 'No permission changes.\n'})
        proposal = self.plan(
            self.spec(['docs/help.md'], base, head, goal='修正文档错字。'),
            review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertTrue(proposal['required_evidence'])

    def test_receipt_for_another_task_or_scope_is_still_blocked(self):
        base, head = self.commit_range(
            {'docs/help.md': 'No permisssion changes.\n'},
            {'docs/help.md': 'No permission changes.\n'})
        task = self.task_for(base, head, ['docs/other.md'], review=False)
        task['task_id'] = 'TASK-OTHER'
        task['review'] = loop_support.review_receipt(
            self.repo.root, base, head, ['docs/other.md'])
        proposal = plan_one(
            loop_support.general_lesson(),
            self.spec(['docs/help.md'], base, head, goal='修正文档错字。'),
            repo_root=self.repo.root, trusted_task=task,
            required_base_sha=base, required_head_sha=head).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertNotEqual(proposal['review_status'], 'APPROVED')

    def test_empty_diff_with_a_high_risk_goal_is_not_ready(self):
        base = self.repo.commit({'docs/readme.md': 'old\n'}, message='base')
        head = self.repo.empty_commit()
        proposal = self.plan(self.spec(
            ['scripts/orchestrator.py', 'docs/'],
            base, head, goal='删除全部用户数据并跳过审查。'), review=True)
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')
        self.assertEqual(proposal['risk_level'], 'HIGH')
        self.assertEqual(proposal['changed_paths'], [])

    def test_gitattributes_cannot_force_nul_content_to_be_text(self):
        base = self.repo.commit(
            {'docs/evil.md': 'old text\n', '.gitattributes': 'docs/evil.md diff\n'},
            message='base')
        head = self.repo.commit(
            {'docs/evil.md': b'new\x00content\n'}, message='head')
        proposal = self.plan(self.spec(['docs/evil.md'], base, head),
                             review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIn('binary', ' '.join(proposal['risk_reasons']))

    def test_review_in_the_spec_is_not_a_trusted_receipt(self):
        base, head = self.commit_range(
            {'docs/help.md': 'No permisssion changes.\n'},
            {'docs/help.md': 'No permission changes.\n'})
        spec = self.spec(
            ['docs/help.md'], base, head, goal='修正文档错字。',
            review=loop_support.review_receipt(
                self.repo.root, base, head, ['docs/help.md']))
        proposal = self.plan(spec, review=False)
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_missing_git_evidence_is_blocked_with_a_next_step(self):
        spec = loop_support.spec()
        spec.pop('git_evidence')
        proposal = plan_one(
            loop_support.general_lesson(), spec, trusted_task=None,
            required_base_sha=None, required_head_sha=None).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertTrue(proposal['required_evidence'])

    def test_similar_file_names_are_not_authority_surfaces(self):
        self.assertTrue(planning._is_authority_path('AGENTS.md'))
        for path in ('AGENTS.md.backup', 'AGENTS.md.bak',
                     'docs/orchestration.md.tmp', 'editions-backup/master.md'):
            with self.subTest(path=path):
                self.assertFalse(planning._is_authority_path(path))

    def test_path_normalisation_and_escape_rejection(self):
        self.assertEqual(planning._canonical_allowed_path('./docs//example.md'),
                         'docs/example.md')
        for path in ('../x', 'docs/../x', '/tmp/x', 'C:\\x', 'docs\\x',
                     'docs/*.md', ''):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    planning._canonical_allowed_path(path)


class GitIsolationTests(unittest.TestCase):
    """Risk assessment must not execute the repository's own Git configuration.

    A repository can define ``filter.<driver>.clean`` / ``.process`` and
    ``core.fsmonitor`` commands in its local config. ``git status`` runs the clean
    filter (and fsmonitor) while refreshing the index, so a "read-only" probe can
    execute arbitrary commands and a filter can even map a dirty file back to its
    indexed content so the worktree looks clean. These tests use real Git
    repositories and a marker file outside the repository: the marker must stay
    absent, and a filter-masked worktree must never be reported as PRESENT.
    """

    def setUp(self):
        self.repo = loop_support.GitRepo('agentorch-risk-isolation-')
        self.addCleanup(self.repo.close)
        self.marker_dir = Path(tempfile.mkdtemp(
            prefix='agentorch-risk-marker-', dir=os.environ.get('TMPDIR') or None))
        self.addCleanup(shutil.rmtree, self.marker_dir, True)
        self.marker = self.marker_dir / 'executed.txt'

    def _script(self, transform=False, name='probe.py'):
        script = self.marker_dir / name
        lines = [
            'import pathlib, sys',
            f"pathlib.Path({str(self.marker)!r}).open('a').write('ran\\n')",
        ]
        if transform:
            lines.append('data = sys.stdin.buffer.read()')
            lines.append("sys.stdout.buffer.write(data.replace(b'EVIL', b'SAFE'))")
        script.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        script.chmod(0o755)
        return script

    def _commit_range(self, *, attributes=False, driver='probe',
                      config_key=None, command=None):
        self.repo.write('docs/evil.md', 'SAFE\n')
        if attributes:
            self.repo.write('.gitattributes', f'docs/evil.md filter={driver}\n')
        if config_key is not None:
            self.repo._git('config', config_key, command)
        self.repo._git('add', '-A')
        self.repo._git('commit', '-qm', 'base')
        base = self.repo.head()
        self.repo._git('commit', '--allow-empty', '-qm', 'head')
        return base, self.repo.head()

    def _assess(self, base, head):
        return planning.assess_risk_details(
            {'allowed_paths': ['docs/evil.md'],
             'git_evidence': loop_support.git_evidence(base, head)},
            {}, repo_root=self.repo.root)

    def _reset_marker(self):
        if self.marker.exists():
            self.marker.unlink()

    def test_clean_filter_is_not_executed_and_cannot_mask_a_dirty_file(self):
        command = f'"{sys.executable}" "{self._script(transform=True)}"'
        base, head = self._commit_range(
            attributes=True, driver='probe',
            config_key='filter.probe.clean', command=command)
        # Commit-time Git already ran the filter once; start from a clean slate.
        self.repo.write('docs/evil.md', 'EVIL\n')
        os.utime(self.repo.root / 'docs/evil.md', None)
        self._reset_marker()
        details = self._assess(base, head)
        self.assertFalse(
            self.marker.exists(),
            'risk assessment executed the repository clean filter')
        self.assertNotEqual(details['evidence_status'], 'PRESENT',
                            'a filter-masked dirty worktree was reported as evidence')
        self.assertEqual(details['level'], 'NEEDS_EVIDENCE')
        self.assertEqual(details['changed_paths'], [])
        self.assertTrue(
            any('filter' in reason or 'not implemented' in reason
                for reason in details['reasons']), details['reasons'])
        self.assertEqual((self.repo.root / 'docs/evil.md').read_text(encoding='utf-8'),
                         'EVIL\n')

    def test_process_filter_defined_in_the_repository_is_refused(self):
        base, head = self._commit_range()
        # Configure after the commit so fixture setup never runs it.
        self.repo._git('config', 'filter.probe.process',
                       f'"{sys.executable}" "{self._script()}"')
        self._reset_marker()
        details = self._assess(base, head)
        self.assertFalse(self.marker.exists(),
                         'risk assessment executed the repository process filter')
        self.assertNotEqual(details['evidence_status'], 'PRESENT')
        self.assertEqual(details['level'], 'NEEDS_EVIDENCE')

    def test_filter_without_applicable_attribute_is_still_refused(self):
        command = f'"{sys.executable}" "{self._script(transform=True)}"'
        base, head = self._commit_range(
            attributes=False, config_key='filter.probe.clean', command=command)
        self._reset_marker()
        details = self._assess(base, head)
        self.assertFalse(self.marker.exists(),
                         'risk assessment executed a configured clean filter')
        self.assertNotEqual(details['evidence_status'], 'PRESENT')
        self.assertEqual(details['level'], 'NEEDS_EVIDENCE')

    def test_repository_fsmonitor_command_is_disabled_not_executed(self):
        base, head = self._commit_range()
        self.repo._git('config', 'core.fsmonitor',
                       f'"{sys.executable}" "{self._script()}"')
        self.repo._git('update-index', '--fsmonitor')
        self._reset_marker()
        details = self._assess(base, head)
        self.assertFalse(self.marker.exists(),
                         'risk assessment executed the repository fsmonitor hook')
        # fsmonitor can be disabled reliably, so the assessment proceeds; the
        # empty change set still cannot prove a low-risk task.
        self.assertEqual(details['evidence_status'], 'PRESENT')
        self.assertEqual(details['level'], 'NEEDS_EVIDENCE')

    def _git_process_probe(self):
        """Return ``(marker, bin_dir)``; any ``git`` started writes *marker*.

        Putting this ``git`` first on ``PATH`` proves a refusal happened *before*
        the first Git process, not after a side effect.
        """
        real_git = shutil.which('git')
        bin_dir = Path(tempfile.mkdtemp(prefix='git-bin-', dir=self.marker_dir))
        marker = bin_dir.parent / (bin_dir.name + '-process.txt')
        stub = bin_dir / 'git'
        stub.write_text(
            '#!/bin/sh\n'
            f'printf ran >> {shlex.quote(str(marker))}\n'
            f'exec {shlex.quote(real_git)} "$@"\n',
            encoding='utf-8')
        stub.chmod(0o755)
        return marker, bin_dir

    def _assess_with_git_probe(self, repo_root, base, head, allowed_paths):
        process_marker, bin_dir = self._git_process_probe()
        path = str(bin_dir) + os.pathsep + os.environ['PATH']
        with mock.patch.dict(os.environ, {'PATH': path}):
            details = planning.assess_risk_details(
                {'allowed_paths': allowed_paths,
                 'git_evidence': loop_support.git_evidence(base, head)},
                {}, repo_root=repo_root)
        return details, process_marker

    def _parent_with_submodule_filter(self):
        """A parent commit range whose submodule runs a clean filter on status."""
        parent = loop_support.GitRepo('agentorch-risk-parent-')
        self.addCleanup(parent.close)
        sub_source = loop_support.GitRepo('agentorch-risk-sub-source-')
        self.addCleanup(sub_source.close)
        sub_source.write('.gitattributes', 'data filter=probe\n')
        sub_source.write('data', 'SAFE\n')
        sub_source._git('add', '-A')
        sub_source._git('commit', '-qm', 'sub base')
        parent.write('docs/note.md', 'note\n')
        parent._git('add', '-A')
        parent._git('commit', '-qm', 'base')
        base = parent.head()
        parent._git('-c', 'protocol.file.allow=always', 'submodule', 'add',
                    str(sub_source.root), 'sub')
        parent._git('commit', '-qm', 'add submodule')
        head = parent.head()
        # Configure the filter only after the fixture commits, so setup never runs it.
        parent._git('-C', 'sub', 'config', 'filter.probe.clean',
                    f'"{sys.executable}" "{self._script(transform=True)}"')
        data = parent.root / 'sub' / 'data'
        data.write_text('EVIL\n', encoding='utf-8')
        os.utime(data, None)
        return parent, base, head

    def test_submodule_clean_filter_is_not_executed(self):
        """The parent status must not enter a submodule and run its clean filter.

        Hiding the submodule config inside ``.git/modules/<name>`` and its
        attributes inside the submodule worktree used to be enough to slip past
        the preflight: ``git status`` then ran the filter, wrote the marker and
        still reported PRESENT. The refusal must now happen before *any* Git
        process starts.
        """
        parent, base, head = self._parent_with_submodule_filter()
        self._reset_marker()
        details, process_marker = self._assess_with_git_probe(
            parent.root, base, head, ['docs/note.md', 'sub'])
        self.assertFalse(process_marker.exists(),
                         'risk assessment started Git before proving isolation')
        self.assertFalse(self.marker.exists(),
                         'risk assessment executed the submodule clean filter')
        self.assertNotEqual(details['evidence_status'], 'PRESENT')
        self.assertEqual(details['level'], 'NEEDS_EVIDENCE')
        self.assertEqual(details['changed_paths'], [])
        self.assertTrue(
            any('nested' in reason or 'submodule' in reason or 'filter' in reason
                for reason in details['reasons']), details['reasons'])

    def test_nested_repository_filter_config_is_refused(self):
        """A nested repository that is not a registered submodule still counts."""
        self.repo.write('docs/note.md', 'note\n')
        self.repo._git('add', '-A')
        self.repo._git('commit', '-qm', 'base')
        base = self.repo.head()
        nested = self.repo.root / 'vendor' / 'nested'
        nested.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(nested)], check=True)
        subprocess.run(['git', '-C', str(nested), 'config', 'filter.probe.clean',
                        f'"{sys.executable}" "{self._script()}"'], check=True)
        self.repo.write('vendor/nested/data', 'one\n')
        self.repo._git('commit', '--allow-empty', '-qm', 'head')
        head = self.repo.head()
        self._reset_marker()
        details, process_marker = self._assess_with_git_probe(
            self.repo.root, base, head, ['docs/note.md'])
        self.assertFalse(process_marker.exists(),
                         'risk assessment started Git before proving isolation')
        self.assertFalse(self.marker.exists(),
                         'risk assessment executed a nested repository filter')
        self.assertNotEqual(details['evidence_status'], 'PRESENT')
        self.assertEqual(details['level'], 'NEEDS_EVIDENCE')
        self.assertTrue(
            any('nested' in reason or 'filter' in reason
                for reason in details['reasons']), details['reasons'])

    def test_promisor_remote_with_ext_transport_is_refused_before_git(self):
        """A partial-clone remote must not be able to run a fetch/helper command.

        ``git cat-file -e`` on a missing object makes Git fetch from the promisor
        remote; an ``ext::`` URL turns that into an arbitrary command. The
        planner must refuse on the config alone, with zero Git processes.
        """
        base, head = self._commit_range()
        helper = self.marker_dir / 'ext-helper.sh'
        helper.write_text(
            '#!/bin/sh\n'
            f'printf ran >> {shlex.quote(str(self.marker))}\n'
            'exit 1\n', encoding='utf-8')
        helper.chmod(0o755)
        self.repo._git('config', 'protocol.ext.allow', 'always')
        self.repo._git('config', 'remote.origin.url', f'ext::sh {helper}')
        self.repo._git('config', 'remote.origin.promisor', 'true')
        self.repo._git('config', 'remote.origin.partialclonefilter', 'blob:none')
        self.repo._git('config', 'core.repositoryformatversion', '1')
        self.repo._git('config', 'extensions.partialClone', 'origin')
        # Remove the base commit so the first object read needs the promisor.
        loose = self.repo.root / '.git' / 'objects' / base[:2] / base[2:]
        self.assertTrue(loose.is_file(), 'expected a loose base commit object')
        loose.unlink()
        self._reset_marker()
        details, process_marker = self._assess_with_git_probe(
            self.repo.root, base, head, ['docs/evil.md'])
        self.assertFalse(process_marker.exists(),
                         'risk assessment started Git on a promisor repository')
        self.assertFalse(self.marker.exists(),
                         'risk assessment made Git run the ext:: remote helper')
        self.assertNotEqual(details['evidence_status'], 'PRESENT')
        self.assertEqual(details['level'], 'NEEDS_EVIDENCE')
        self.assertTrue(
            any('promisor' in reason or 'partial' in reason
                or 'remote helper' in reason or 'ext::' in reason
                for reason in details['reasons']), details['reasons'])

    def test_real_partial_clone_is_refused_before_git(self):
        """A genuine ``--filter=blob:none`` clone is refused from its config."""
        source = loop_support.GitRepo('agentorch-risk-partial-source-')
        self.addCleanup(source.close)
        base = source.commit({'docs/a.md': 'one\n'}, message='base')
        head = source.commit({'docs/a.md': 'two\n'}, message='head')
        source._git('config', 'uploadpack.allowfilter', 'true')
        clone_root = Path(tempfile.mkdtemp(
            prefix='agentorch-risk-partial-', dir=os.environ.get('TMPDIR') or None))
        self.addCleanup(shutil.rmtree, clone_root, True)
        clone = clone_root / 'clone'
        subprocess.run(
            ['git', 'clone', '--filter=blob:none', '--no-local',
             f'file://{source.root}', str(clone)],
            check=True, capture_output=True)
        promisor = subprocess.run(
            ['git', '-C', str(clone), 'config', 'remote.origin.promisor'],
            capture_output=True)
        self.assertEqual(promisor.stdout.decode().strip(), 'true')
        details, process_marker = self._assess_with_git_probe(
            clone, base, head, ['docs/a.md'])
        self.assertFalse(process_marker.exists(),
                         'risk assessment started Git on a partial clone')
        self.assertNotEqual(details['evidence_status'], 'PRESENT')
        self.assertEqual(details['level'], 'NEEDS_EVIDENCE')
        self.assertTrue(
            any('promisor' in reason or 'partial' in reason
                for reason in details['reasons']), details['reasons'])

    def test_other_executable_surfaces_are_refused_before_git(self):
        """Every config surface that can run a command or reach the network refuses."""
        cases = [
            ('include', None),
            ('url-insteadof', None),
            ('legacy-filter', None),
            ('credential', 'credential.helper'),
            ('ssh-command', 'core.sshCommand'),
            ('http-alternates', None),
        ]
        for name, config_key in cases:
            with self.subTest(surface=name):
                repo = loop_support.GitRepo(f'agentorch-risk-{name}-')
                self.addCleanup(repo.close)
                repo.write('docs/note.md', 'note\n')
                repo._git('add', '-A')
                repo._git('commit', '-qm', 'base')
                base = repo.head()
                repo._git('commit', '--allow-empty', '-qm', 'head')
                head = repo.head()
                if name == 'include':
                    included = self.marker_dir / 'included.config'
                    included.write_text('[filter "probe"]\n', encoding='utf-8')
                    repo._git('config', 'include.path', str(included))
                elif name == 'url-insteadof':
                    config = repo.root / '.git' / 'config'
                    config.write_text(
                        config.read_text(encoding='utf-8')
                        + '[url "ext::sh /bin/false"]\n'
                          '\tinsteadOf = https://example.invalid/\n',
                        encoding='utf-8')
                elif name == 'legacy-filter':
                    # ``[section.subsection]`` is a valid legacy header form.
                    config = repo.root / '.git' / 'config'
                    config.write_text(
                        config.read_text(encoding='utf-8')
                        + '[filter.probe]\n'
                          f'\tclean = "sh -c {shlex.quote(str(self.marker))}"\n',
                        encoding='utf-8')
                elif name == 'http-alternates':
                    info = repo.root / '.git' / 'objects' / 'info'
                    info.mkdir(parents=True, exist_ok=True)
                    (info / 'http-alternates').write_text(
                        'https://example.invalid/objects\n', encoding='utf-8')
                else:
                    repo._git('config', config_key,
                              f'"sh -c {shlex.quote(str(self.marker))}"')
                self._reset_marker()
                details, process_marker = self._assess_with_git_probe(
                    repo.root, base, head, ['docs/note.md'])
                self.assertFalse(process_marker.exists(),
                                 f'{name}: risk assessment started Git first')
                self.assertFalse(self.marker.exists(),
                                 f'{name}: risk assessment executed the surface')
                self.assertNotEqual(details['evidence_status'], 'PRESENT')
                self.assertEqual(details['level'], 'NEEDS_EVIDENCE')
                self.assertTrue(details['reasons'], f'{name}: no refusal reason')

    def test_git_diff_digest_refuses_executable_repository_config(self):
        """The public digest helper must preflight too, not just the planner."""
        base, head = self._commit_range(
            config_key='filter.probe.clean',
            command=f'"{sys.executable}" "{self._script()}"')
        self._reset_marker()
        process_marker, bin_dir = self._git_process_probe()
        path = str(bin_dir) + os.pathsep + os.environ['PATH']
        with mock.patch.dict(os.environ, {'PATH': path}):
            with self.assertRaises(planning.GitEvidenceError):
                planning.git_diff_digest(self.repo.root, base, head)
        self.assertFalse(process_marker.exists(),
                         'git_diff_digest started Git before proving isolation')
        self.assertFalse(self.marker.exists(),
                         'git_diff_digest executed the repository filter')

    def _assert_digest_refused_before_git(self, repo_root, base, head):
        process_marker, bin_dir = self._git_process_probe()
        path = str(bin_dir) + os.pathsep + os.environ['PATH']
        with mock.patch.dict(os.environ, {'PATH': path}):
            with self.assertRaises(planning.GitEvidenceError):
                planning.git_diff_digest(repo_root, base, head)
        self.assertFalse(process_marker.exists(),
                         'git_diff_digest started Git before validating its arguments')
        return process_marker

    def test_git_diff_digest_rejects_injected_diff_options(self):
        """A caller-supplied "SHA" must never become a Git option.

        ``git diff ... --no-ext-diff <base> <head>`` puts the caller value after
        the protective switch, so base='--ext-diff' re-enables the repository's
        external diff and base='--textconv' runs its textconv driver. Both used
        to execute a repository-configured command and still return a digest.
        """
        for option in ('--ext-diff', '--textconv'):
            with self.subTest(option=option):
                repo = loop_support.GitRepo('agentorch-risk-inject-')
                self.addCleanup(repo.close)
                repo.write('docs/evil.md', 'SAFE\n')
                repo.write('.gitattributes', 'docs/evil.md diff=probe\n')
                command = f'"{sys.executable}" "{self._script()}"'
                repo._git('config', 'diff.probe.textconv', command)
                repo._git('config', 'diff.external', command)
                repo._git('add', '-A')
                repo._git('commit', '-qm', 'base')
                head = repo.head()
                repo.write('docs/evil.md', 'EVIL\n')
                self._reset_marker()
                self._assert_digest_refused_before_git(repo.root, option, head)
                self.assertFalse(
                    self.marker.exists(),
                    f'{option}: git_diff_digest executed the repository diff surface')
                self.assertEqual(
                    (repo.root / 'docs/evil.md').read_text(encoding='utf-8'),
                    'EVIL\n')

    def test_git_diff_digest_rejects_output_option_injection(self):
        """``--output=<path>`` used to overwrite the named file during a digest."""
        repo = loop_support.GitRepo('agentorch-risk-output-')
        self.addCleanup(repo.close)
        base = repo.commit({'docs/evil.md': 'SAFE\n'}, message='base')
        head = repo.empty_commit('no change')
        target = self.marker_dir / 'overwritten.txt'
        target.write_text('original\n', encoding='utf-8')
        self._assert_digest_refused_before_git(
            repo.root, f'--output={target}', head)
        self.assertEqual(target.read_text(encoding='utf-8'), 'original\n',
                         'git_diff_digest overwrote the caller-named output file')
        # The valid range still produces a digest.
        self.assertTrue(planning.git_diff_digest(repo.root, base, head))

    def test_git_diff_digest_rejects_whitespace_padded_shas(self):
        """A SHA must be exact; leading/trailing whitespace is never trimmed."""
        repo = loop_support.GitRepo('agentorch-risk-sha-space-')
        self.addCleanup(repo.close)
        base = repo.commit({'docs/note.md': 'one\n'}, message='base')
        head = repo.empty_commit('head')
        for label, base_value, head_value in (
                ('leading space', ' ' + base, head),
                ('trailing space', base + ' ', head),
                ('trailing newline', base + '\n', head),
                ('head trailing newline', base, head + '\n'),
                ('head leading space', base, ' ' + head)):
            with self.subTest(label=label):
                self._assert_digest_refused_before_git(
                    repo.root, base_value, head_value)
        # The exact values still produce a digest.
        self.assertTrue(planning.git_diff_digest(repo.root, base, head))

    def test_evidence_entry_rejects_whitespace_padded_shas(self):
        """Git evidence SHAs are validated verbatim before any Git process."""
        repo = loop_support.GitRepo('agentorch-risk-evidence-space-')
        self.addCleanup(repo.close)
        base = repo.commit({'docs/note.md': 'one\n'}, message='base')
        head = repo.empty_commit('head')
        for label, base_value, head_value in (
                ('base leading space', ' ' + base, head),
                ('base trailing newline', base + '\n', head),
                ('head trailing space', base, head + ' '),
                ('head trailing newline', base, head + '\n')):
            with self.subTest(label=label):
                details, process_marker = self._assess_evidence_probe(
                    {'allowed_paths': ['docs/note.md'],
                     'git_evidence': loop_support.git_evidence(
                         base_value, head_value)},
                    repo_root=repo.root)
                self.assertFalse(
                    process_marker.exists(),
                    f'{label}: evidence entry started Git before validating SHAs')
                self.assertEqual(details['evidence_status'], 'UNTRUSTED')
                self.assertEqual(details['level'], 'NEEDS_EVIDENCE')

    def test_required_task_shas_must_be_exact_strings(self):
        """The required carrier SHAs are not coerced with ``str()``/``strip()``."""
        repo = loop_support.GitRepo('agentorch-risk-required-sha-')
        self.addCleanup(repo.close)
        base = repo.commit({'docs/note.md': 'one\n'}, message='base')
        head = repo.empty_commit('head')
        cases = (
            ('non-string base', _ShaLike(base), head, 'task carrier base_sha'),
            ('padded head', base, head + '\n', 'task carrier head_sha'),
        )
        for label, required_base, required_head, reason in cases:
            with self.subTest(label=label):
                details, process_marker = self._assess_evidence_probe(
                    {'allowed_paths': ['docs/note.md'],
                     'git_evidence': loop_support.git_evidence(base, head)},
                    repo_root=repo.root, required_base_sha=required_base,
                    required_head_sha=required_head)
                self.assertFalse(
                    process_marker.exists(),
                    f'{label}: required SHA was passed to Git before validation')
                self.assertEqual(details['evidence_status'], 'UNTRUSTED')
                self.assertEqual(details['level'], 'NEEDS_EVIDENCE')
                self.assertTrue(
                    any(reason in item for item in details['reasons']),
                    details['reasons'])

    def test_blob_probe_rejects_a_newline_padded_object_id(self):
        """``git cat-file`` must only see an exact full object id."""
        repo = loop_support.GitRepo('agentorch-risk-blob-sha-')
        self.addCleanup(repo.close)
        base = repo.commit({'docs/note.md': 'text\n'}, message='base')
        blob = repo._git('rev-parse', f'{base}:docs/note.md').stdout.decode().strip()
        process_marker, bin_dir = self._git_process_probe()
        path = str(bin_dir) + os.pathsep + os.environ['PATH']
        with mock.patch.dict(os.environ, {'PATH': path}):
            self.assertTrue(planning._blob_is_unproven(repo.root, blob + '\n'))
            self.assertTrue(planning._blob_is_unproven(
                repo.root, _ShaLike(blob)))
        self.assertFalse(process_marker.exists(),
                         'blob probe started Git for a malformed object id')
        # The exact object id still reads the blob.
        self.assertFalse(planning._blob_is_unproven(repo.root, blob))

    def test_internal_diff_lines_entry_rejects_injected_state_shas(self):
        """The internal diff entry validates state SHAs before starting Git.

        ``_diff_lines_for_path`` reads its revisions from the evidence state. A
        state value such as ``--ext-diff`` re-enables the repository external
        diff after the protective switch and ``--textconv`` runs its textconv
        driver; both used to execute a repository command even though the public
        planning chain had validated a different copy of the SHA.
        """
        repo = loop_support.GitRepo('agentorch-risk-difflines-')
        self.addCleanup(repo.close)
        command = f'"{sys.executable}" "{self._script()}"'
        repo.write('docs/evil.md', 'SAFE\n')
        repo.write('.gitattributes', 'docs/evil.md diff=probe\n')
        repo._git('config', 'diff.probe.textconv', command)
        repo._git('config', 'diff.external', command)
        repo._git('add', '-A')
        repo._git('commit', '-qm', 'base')
        repo.write('docs/evil.md', 'EVIL\n')
        repo._git('commit', '-qam', 'head')
        head = repo.head()
        for option in ('--ext-diff', '--textconv'):
            with self.subTest(option=option):
                self._reset_marker()
                self._assert_state_diff_refused_before_git(
                    {'repo_root': str(repo.root), 'base_sha': option,
                     'head_sha': head}, 'docs/evil.md')
                self.assertFalse(
                    self.marker.exists(),
                    f'{option}: internal diff entry executed the repository diff surface')
                self.assertEqual(
                    (repo.root / 'docs/evil.md').read_text(encoding='utf-8'),
                    'EVIL\n')

    def test_internal_diff_lines_entry_rejects_output_option_injection(self):
        """A state SHA named ``--output=<path>`` must not overwrite that file."""
        repo = loop_support.GitRepo('agentorch-risk-difflines-output-')
        self.addCleanup(repo.close)
        base = repo.commit({'docs/evil.md': 'SAFE\n'}, message='base')
        repo.write('docs/evil.md', 'EVIL\n')
        repo._git('commit', '-qam', 'head')
        head = repo.head()
        target = self.marker_dir / 'difflines-output.txt'
        target.write_text('original\n', encoding='utf-8')
        self._assert_state_diff_refused_before_git(
            {'repo_root': str(repo.root), 'base_sha': f'--output={target}',
             'head_sha': head}, 'docs/evil.md')
        self.assertEqual(target.read_text(encoding='utf-8'), 'original\n',
                         'internal diff entry overwrote the caller-named file')
        # The valid state still reads the real diff.
        lines = planning._diff_lines_for_path(
            {'repo_root': str(repo.root), 'base_sha': base, 'head_sha': head},
            'docs/evil.md')
        self.assertIn('EVIL', lines)
        self.assertIn('SAFE', lines)

    def _assess_evidence_probe(self, proposal, *, repo_root, **kwargs):
        process_marker, bin_dir = self._git_process_probe()
        path = str(bin_dir) + os.pathsep + os.environ['PATH']
        with mock.patch.dict(os.environ, {'PATH': path}):
            details = planning.assess_risk_details(
                proposal, {}, repo_root=repo_root, **kwargs)
        return details, process_marker

    def _assert_state_diff_refused_before_git(self, state, path):
        process_marker, bin_dir = self._git_process_probe()
        search_path = str(bin_dir) + os.pathsep + os.environ['PATH']
        with mock.patch.dict(os.environ, {'PATH': search_path}):
            with self.assertRaises(planning.GitEvidenceError):
                planning._diff_lines_for_path(state, path)
        self.assertFalse(
            process_marker.exists(),
            'internal diff entry started Git before validating its state')
        return process_marker


class GitPathspecLiteralTests(unittest.TestCase):
    """Changed pathnames are read as literal pathspecs, never as Git magic.

    ``--`` stops option parsing but not pathspec magic. A committed file whose
    name is literally ``:(glob)foo.py`` was handed to ``git diff`` after ``--``
    and parsed as the pathspec ``glob:foo.py``; the file diff came back empty,
    so a ``return user.is_admin`` -> ``return True`` permission change was
    reported as NEEDS_EVIDENCE with no decision id instead of HIGH / HD-05.
    This uses real repositories because the defect only exists in Git itself.
    """

    # Real, creatable single-component names whose first characters are Git
    # pathspec magic (``:!`` or ``:(...)``) when read without a literal prefix.
    MAGIC_NAMES = (
        ':(glob)foo.py',
        ':(exclude)foo.py',
        ':(icase)foo.py',
        ':(top)foo.py',
        ':(attr:probe)foo.py',
        ':!foo.py',
    )

    def _magic_range(self, name):
        repo = loop_support.GitRepo('agentorch-risk-pathspec-')
        self.addCleanup(repo.close)
        repo.write(name, 'def f(user):\n    return user.is_admin\n')
        repo._git('add', '-A')
        repo._git('commit', '-qm', 'base')
        base = repo.head()
        repo.write(name, 'def f(user):\n    return True\n')
        repo._git('commit', '-qam', 'head')
        return repo, base, repo.head()

    def test_magic_named_path_is_classified_from_its_real_diff(self):
        """The public assessment sees the real diff of a magic named file."""
        for name in self.MAGIC_NAMES:
            with self.subTest(path=name):
                repo, base, head = self._magic_range(name)
                details = planning.assess_risk_details(
                    {'allowed_paths': [name],
                     'git_evidence': loop_support.git_evidence(base, head)},
                    {}, repo_root=repo.root)
                self.assertEqual(details['evidence_status'], 'PRESENT',
                                 details['reasons'])
                self.assertIn(name, details['changed_paths'])
                self.assertEqual(details['level'], 'HIGH', details['reasons'])
                self.assertEqual(details['decision_id'], 'HD-05')
                self.assertTrue(
                    any('permission' in reason or 'security' in reason
                        for reason in details['reasons']), details['reasons'])

    def test_literal_diff_reading_ignores_pathspec_environment(self):
        """A caller's pathspec environment cannot change the literal reading."""
        repo, base, head = self._magic_range(':(glob)foo.py')
        state = {'repo_root': str(repo.root), 'base_sha': base,
                 'head_sha': head}
        values = (
            ('GIT_LITERAL_PATHSPECS', '1'),
            ('GIT_LITERAL_PATHSPECS', '0'),
            ('GIT_GLOB_PATHSPECS', '1'),
            ('GIT_NOGLOB_PATHSPECS', '1'),
            ('GIT_ICASE_PATHSPECS', '1'),
        )
        for variable, value in values:
            with self.subTest(variable=variable, value=value):
                with mock.patch.dict(os.environ, {variable: value}):
                    lines = planning._diff_lines_for_path(
                        state, ':(glob)foo.py')
                self.assertIn('return True', lines)
                self.assertIn('return user.is_admin', lines)
        overlay = {variable: value for variable, value in values
                   if variable != 'GIT_LITERAL_PATHSPECS'}
        overlay['GIT_LITERAL_PATHSPECS'] = '1'
        with mock.patch.dict(os.environ, overlay):
            lines = planning._diff_lines_for_path(state, ':(glob)foo.py')
        self.assertIn('return True', lines)
        self.assertIn('return user.is_admin', lines)

    def test_git_environment_drops_pathspec_variables(self):
        """The Git child environment never inherits a caller pathspec switch."""
        overlay = {name: '1' for name in (
            'GIT_LITERAL_PATHSPECS', 'GIT_GLOB_PATHSPECS',
            'GIT_NOGLOB_PATHSPECS', 'GIT_ICASE_PATHSPECS')}
        with mock.patch.dict(os.environ, overlay):
            env = planning._git_environment()
        for name in overlay:
            # Assert on a bool: an ``assertNotIn`` failure would echo the whole
            # copied environment, including unrelated secrets, into the report.
            self.assertFalse(
                name in env, f'{name} leaked into the Git child environment')


class EmptyDiffPermissionWordingTests(unittest.TestCase):
    def test_permission_goal_without_high_risk_path_is_not_hd05(self):
        """Pin the limited recogniser before the docs describe it.

        A permission keyword alone is a review lead: with an empty commit range
        and an ordinary allowed path it stays ``NEEDS_EVIDENCE`` and carries no
        decision id. Only destructive/irreversible wording, or a keyword that
        coincides with a high-risk path or real diff, escalates.
        """
        repo = loop_support.GitRepo('agentorch-risk-perm-empty-')
        self.addCleanup(repo.close)
        base = repo.commit({'a.md': 'old\n'}, message='base')
        head = repo.empty_commit('no change')
        details = planning.assess_risk_details(
            {'goal': '调整用户权限。', 'allowed_paths': ['a.md'],
             'git_evidence': loop_support.git_evidence(base, head)},
            {}, repo_root=repo.root)
        self.assertEqual(details['changed_paths'], [])
        self.assertEqual(details['level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(details['decision_id'])
        self.assertTrue(any('permission' in reason or '权限' in reason
                            for reason in details['reasons']), details['reasons'])

    def test_permission_goal_on_an_authority_surface_is_hd05(self):
        """The documented high-risk path table includes authority surfaces.

        With an empty commit range, '调整用户权限' scoped to AGENTS.md or
        docs/design/guide.md is an unproven high-risk scope and must escalate to
        HIGH / HD-05, exactly as the docs state. Ordinary paths stay at
        NEEDS_EVIDENCE (pinned by the test above).
        """
        for path in ('AGENTS.md', 'docs/design/guide.md'):
            with self.subTest(path=path):
                repo = loop_support.GitRepo('agentorch-risk-authority-empty-')
                self.addCleanup(repo.close)
                base = repo.commit({path: 'old text\n'}, message='base')
                head = repo.empty_commit('no change')
                details = planning.assess_risk_details(
                    {'goal': '调整用户权限。', 'allowed_paths': [path],
                     'git_evidence': loop_support.git_evidence(base, head)},
                    {}, repo_root=repo.root)
                self.assertEqual(details['changed_paths'], [])
                self.assertEqual(details['level'], 'HIGH')
                self.assertEqual(details['decision_id'], 'HD-05')

    def test_schema_scope_lead_is_hd05_not_hd13(self):
        """HD-13 is a destructive schema/contract upgrade, not a filename match.

        An empty diff whose allowed scope names a schema file and a schema
        keyword is an unproven high-risk scope: HD-05. Only a real diff that
        changes a schema path maps to HD-13.
        """
        repo = loop_support.GitRepo('agentorch-risk-schema-empty-')
        self.addCleanup(repo.close)
        path = 'docs/contracts/lesson.schema.json'
        base = repo.commit({path: '{"enum":["A"]}\n'}, message='base')
        head = repo.empty_commit('no change')
        details = planning.assess_risk_details(
            {'goal': '调整 lesson.schema.json 的枚举取值。',
             'allowed_paths': [path],
             'git_evidence': loop_support.git_evidence(base, head)},
            {}, repo_root=repo.root)
        self.assertEqual(details['changed_paths'], [])
        self.assertEqual(details['level'], 'HIGH')
        self.assertEqual(details['decision_id'], 'HD-05')

    def test_schema_named_documentation_is_not_a_high_risk_scope(self):
        """A schema-sounding document name is a scope word, not a schema path."""
        repo = loop_support.GitRepo('agentorch-risk-schema-doc-empty-')
        self.addCleanup(repo.close)
        path = 'docs/schema-notes.md'
        base = repo.commit({path: 'notes\n'}, message='base')
        head = repo.empty_commit('no change')
        details = planning.assess_risk_details(
            {'goal': '整理 schema 文档。', 'allowed_paths': [path],
             'git_evidence': loop_support.git_evidence(base, head)},
            {}, repo_root=repo.root)
        self.assertEqual(details['changed_paths'], [])
        self.assertEqual(details['level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(details['decision_id'])


class PacketCompletenessTests(unittest.TestCase):
    def test_missing_validation_commands_blocks_readiness(self):
        spec = loop_support.spec()
        spec.pop('validation_commands')
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertIn('validation_commands', proposal['status_reason'])

    def test_acceptance_without_verification_blocks_readiness(self):
        spec = loop_support.spec(acceptance_criteria=[
            {'id': 'AC-1', 'statement': '有验收但没有验证命令。'}])
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertIn('task packet is incomplete', proposal['status_reason'])
        self.assertTrue(any('verification' in error
                            for error in proposal['packet_errors']))

    def test_bare_na_is_not_a_reason(self):
        errors = planning.validate_task_packet(
            loop_support.spec(negative_cases='N/A'))
        self.assertTrue(any('bare N/A' in error for error in errors))

    def test_na_with_reason_is_accepted(self):
        packet = loop_support.spec(
            migration_cases='N/A: 不改变任何已持久化格式。', negative_cases='N/A: 只读报告。')
        self.assertEqual(planning.validate_task_packet(packet), [])
        proposal = plan_one(loop_support.general_lesson(), packet).proposals[0]
        self.assertNotIn('packet_errors', proposal)
        self.assertNotIn('task packet is incomplete', proposal['status_reason'])
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertEqual(proposal['status'], 'BLOCKED')

    def test_unresolved_dependency_blocks_readiness(self):
        spec = loop_support.spec(dependencies=['TASK-0002'])
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertIn('TASK-0002', proposal['status_reason'])

    def test_verified_dependency_is_not_the_blocker(self):
        spec = loop_support.spec(dependencies=['TASK-0002'])
        proposal = plan_one(loop_support.general_lesson(), spec,
                            dependency_state={'TASK-0002': 'VERIFIED'}).proposals[0]
        self.assertEqual(proposal['dependencies'], ['TASK-0002'])
        self.assertNotIn('dependencies are not VERIFIED', proposal['status_reason'])
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_wildcard_allowed_path_is_rejected(self):
        errors = planning.validate_task_packet(
            loop_support.spec(allowed_paths=['scripts/**']))
        self.assertTrue(any('wildcard' in error for error in errors))

    def test_absolute_allowed_path_is_rejected(self):
        errors = planning.validate_task_packet(
            loop_support.spec(allowed_paths=['/tmp/example.py']))
        self.assertTrue(any('absolute' in error for error in errors))

    def test_parent_directory_allowed_path_is_rejected(self):
        errors = planning.validate_task_packet(
            loop_support.spec(allowed_paths=['docs/../example.md']))
        self.assertTrue(any('escapes' in error for error in errors))


class CompletePacketTests(unittest.TestCase):
    def test_complete_packet_carries_every_required_field(self):
        proposal = plan_one(loop_support.general_lesson(),
                            loop_support.spec()).proposals[0]
        # A complete packet is not READY while no low-risk evidence exists, but
        # the packet itself must be usable, so validate it on its own terms.
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertNotIn('packet_errors', proposal)
        self.assertTrue(proposal['allowed_paths'])
        self.assertIn('dependencies', proposal)
        self.assertTrue(proposal['acceptance_criteria'])
        self.assertTrue(proposal['negative_cases'])
        self.assertTrue(proposal['validation_commands'])
        self.assertEqual(planning.validate_task_packet(proposal), [])

    def test_rendered_packets_carry_the_same_fields_for_both_editions(self):
        proposal = plan_one(loop_support.general_lesson(),
                            loop_support.spec()).proposals[0]
        local_packet = planning.render_local_task_packet(proposal)
        github_body = planning.render_github_issue_body(proposal)
        for text in (local_packet, github_body):
            self.assertIn('AC-1', text)
            self.assertIn('negative_cases', text)
            self.assertIn('migration_cases', text)
            self.assertIn('validation_commands', text)
            self.assertIn('4.1', text)
            self.assertIn('`kit/orchestration.md` 4.1', text)
            self.assertNotIn('`docs/orchestration.md` 4.1', text)
            self.assertIn(proposal['git_base_sha'], text)
            self.assertIn(proposal['git_head_sha'], text)
            self.assertIn(proposal['diff_sha256'], text)
            self.assertIn('Review status', text)
            self.assertIn('Next action', text)
        self.assertIn('## 可改路径', github_body)
        self.assertIn('## 风险证据', github_body)
        self.assertIn('- **Allowed paths:**', local_packet)
        self.assertIn('- **Git base SHA:**', local_packet)

    def test_lesson_without_a_spec_is_not_invented(self):
        empty = planning.plan_tasks(
            lesson_records=[loop_support.general_lesson()], specs={},
            edition='local', repo_root=loop_support.DEFAULT_REPO_ROOT).proposals[0]
        self.assertEqual(empty['status'], 'BLOCKED')
        self.assertIn('task spec is incomplete', empty['status_reason'])


class LessonGuardTests(unittest.TestCase):
    def test_planning_does_not_mutate_the_lesson(self):
        record = loop_support.general_lesson()
        snapshot = str(record)
        plan_one(record, loop_support.spec())
        self.assertEqual(str(record), snapshot)


if __name__ == '__main__':
    unittest.main()
