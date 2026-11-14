"""AC-3 / AC-4: evidence, conflict and risk gates; complete task packets only.

The risk tests use real throw-away Git repositories. A caller-supplied diff,
SHA, `changed_paths` list or authorization is never evidence: only the
repository's own commit range and clean worktree can decide.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from . import loop_support, support
from agentorch_self_improvement import planning  # noqa: E402


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
        self.assertTrue(any('caller-supplied authorization' in item
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
        """The exact reviewer case: real base/head, caller only names one path."""
        root = Path(support.ROOT)
        base = '7e74fd0aa59285fa4970606e9ebe231eee82a07a'
        head = 'c88b33db728ddfe9fa9ca24cb3f3ad6c84becbfe'
        for sha in (base, head):
            if subprocess.run(['git', '-C', str(root), 'cat-file', '-e',
                               f'{sha}^{{commit}}'], capture_output=True).returncode != 0:
                self.skipTest(f'repository history lacks {sha}')
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
            allowed_paths=['docs/self-improvement/entrypoints-and-decisions.md'],
            git_evidence=loop_support.git_evidence(base, head),
            change_evidence={'base_sha': base, 'head_sha': head,
                             'diff': '--- a/docs/self-improvement/entrypoints-and-decisions.md\n'
                                     '+++ b/docs/self-improvement/entrypoints-and-decisions.md\n'
                                     '@@ -1 +1 @@\n-old\n+new\n'})
        details = planning.assess_risk_details(
            {'allowed_paths': ['docs/self-improvement/entrypoints-and-decisions.md'],
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

    def test_docs_change_with_trusted_review_is_ready(self):
        proposal = plan_one(loop_support.general_lesson(),
                            loop_support.spec()).proposals[0]
        self.assertEqual(proposal['status'], 'READY')
        self.assertEqual(proposal['risk_level'], 'LOW')

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
        base, head = self.commit_range(
            {'docs/help.md': 'No permisssion changes.\n'},
            {'docs/help.md': 'No permission changes.\n'})
        proposal = self.plan(
            self.spec(['docs/help.md'], base, head, goal='修正文档错字。'),
            review=True)
        self.assertEqual(proposal['status'], 'READY')
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

    def test_docs_typo_with_trusted_review_is_ready(self):
        base, head = self.commit_range(
            {'docs/help.md': 'No permisssion changes.\n'},
            {'docs/help.md': 'No permission changes.\n'})
        proposal = self.plan(
            self.spec(['docs/help.md'], base, head, goal='修正文档错字。'),
            review=True)
        self.assertEqual(proposal['status'], 'READY')
        self.assertEqual(proposal['risk_level'], 'LOW')
        self.assertEqual(proposal['review_status'], 'APPROVED')

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
        self.assertEqual(proposal['status'], 'READY')

    def test_unresolved_dependency_blocks_readiness(self):
        spec = loop_support.spec(dependencies=['TASK-0002'])
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertIn('TASK-0002', proposal['status_reason'])

    def test_verified_dependency_is_allowed(self):
        spec = loop_support.spec(dependencies=['TASK-0002'])
        proposal = plan_one(loop_support.general_lesson(), spec,
                            dependency_state={'TASK-0002': 'VERIFIED'}).proposals[0]
        self.assertEqual(proposal['status'], 'READY')
        self.assertEqual(proposal['dependencies'], ['TASK-0002'])

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
    def test_ready_proposal_carries_every_required_field(self):
        proposal = plan_one(loop_support.general_lesson(),
                            loop_support.spec()).proposals[0]
        self.assertEqual(proposal['status'], 'READY')
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
