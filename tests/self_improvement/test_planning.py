"""AC-3 / AC-4: evidence, conflict and risk gates; complete task packets only.

Every refusal path is exercised because "no task" is the correct answer more
often than "create a task". The positive control at the end proves the gates did
not simply reject everything.
"""
import unittest

from . import loop_support, support
from agentorch_self_improvement import planning  # noqa: E402


def plan_one(record, spec=None, **kwargs):
    kwargs.setdefault('edition', 'local')
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


class RiskGateTests(unittest.TestCase):
    def test_state_machine_wording_alone_is_an_investigation_not_an_owner_decision(self):
        spec = loop_support.spec(goal='修改任务状态机以允许跳过审查。')
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertTrue(proposal['required_evidence'])

    def test_real_state_machine_code_change_is_high(self):
        spec = loop_support.spec(
            goal='修改任务状态机以允许跳过审查。',
            allowed_paths=['scripts/orchestrator.py'],
            change_evidence=loop_support.change_evidence(
                'scripts/orchestrator.py',
                old="if review_required: block('REVIEW')",
                new="if review_required: allow('REVIEW')"))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')
        self.assertEqual(proposal['risk_level'], 'HIGH')

    def test_high_risk_wins_over_an_incomplete_packet(self):
        spec = loop_support.spec(
            goal='修改任务状态机以允许跳过审查。',
            allowed_paths=['scripts/orchestrator.py'],
            change_evidence=loop_support.change_evidence(
                'scripts/orchestrator.py',
                old='return deny()',
                new='return allow()'))
        spec.pop('validation_commands')
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')

    def test_contract_schema_code_change_maps_to_hd13(self):
        spec = loop_support.spec(
            goal='改变 lesson schema 的枚举取值。',
            allowed_paths=['docs/contracts/lesson.schema.json'],
            change_evidence=loop_support.change_evidence(
                'docs/contracts/lesson.schema.json',
                old='"enum": ["OBSERVED"]',
                new='"enum": ["OBSERVED", "GENERALIZED"]'))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-13')


class EvidenceRoutingTests(unittest.TestCase):
    """Keywords and directories are leads; only evidence decides the gate."""

    def test_permission_wording_typo_without_a_diff_needs_evidence(self):
        spec = loop_support.spec(goal='修正不修改权限的说明错字。',
                                 allowed_paths=['docs/example.md'])
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertTrue(any('权限' in lead for lead in proposal['risk_leads']))
        self.assertTrue(proposal['required_evidence'])

    def test_docs_design_text_change_without_a_diff_needs_evidence_not_an_owner(self):
        spec = loop_support.spec(goal='修复错字。',
                                 allowed_paths=['docs/design/example.md'])
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])

    def test_text_only_docs_diff_resolves_the_permission_wording(self):
        spec = loop_support.spec(
            goal='修正不修改权限的说明错字。',
            allowed_paths=['docs/example.md'],
            change_evidence=loop_support.change_evidence(
                'docs/example.md', old='本步骤不修改权限。', new='本步骤不改动权限。'))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'READY')
        self.assertEqual(proposal['risk_level'], 'LOW')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertTrue(any('caller-supplied' in item
                            for item in proposal['risk_limitations']))

    def test_real_one_line_permission_change_is_high(self):
        spec = loop_support.spec(
            goal='统一权限判定。',
            allowed_paths=['scripts/self_improvement/planning.py'],
            change_evidence=loop_support.change_evidence(
                'scripts/self_improvement/planning.py',
                old='allowed = False',
                new='allowed = True'))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')
        self.assertEqual(proposal['risk_level'], 'HIGH')

    def test_docs_permission_wording_does_not_make_a_separate_code_file_high(self):
        two_file_diff = (
            'diff --git a/docs/example.md b/docs/example.md\n'
            '--- a/docs/example.md\n'
            '+++ b/docs/example.md\n'
            '@@ -1 +1 @@\n'
            '-permission denied\n'
            '+permission check\n'
            'diff --git a/scripts/example.py b/scripts/example.py\n'
            '--- a/scripts/example.py\n'
            '+++ b/scripts/example.py\n'
            '@@ -1 +1 @@\n'
            '-return 1\n'
            '+return 2\n')
        spec = loop_support.spec(
            goal='修正权限说明错字。',
            allowed_paths=['docs/', 'scripts/'],
            change_evidence=loop_support.change_evidence(
                'docs/example.md', diff=two_file_diff))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])

    def test_protected_execution_surface_change_is_high_without_keywords(self):
        spec = loop_support.spec(
            goal='调整提示文案。',
            allowed_paths=['scripts/orchestrator.py'],
            change_evidence=loop_support.change_evidence(
                'scripts/orchestrator.py', old='return "old"', new='return "new"'))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')

    def test_governance_text_diff_without_authorization_needs_evidence(self):
        spec = loop_support.spec(
            goal='改进合并门说明。',
            allowed_paths=['AGENTS.md'],
            change_evidence=loop_support.change_evidence(
                'AGENTS.md', old='旧规则说明。', new='新规则说明。'))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertTrue(any('authorization' in item for item in proposal['required_evidence']))

    def test_authorized_governance_text_diff_is_master_review_not_an_owner_decision(self):
        spec = loop_support.spec(
            goal='改进合并门说明。',
            allowed_paths=['AGENTS.md'],
            change_evidence=loop_support.change_evidence(
                'AGENTS.md', old='旧规则说明。', new='新规则说明。'),
            authorization=loop_support.authorization())
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'READY')
        self.assertEqual(proposal['risk_level'], 'LOW')
        self.assertIsNone(proposal['required_human_decision'])
        self.assertTrue(any('authorization is caller-supplied' in item
                            for item in proposal['risk_limitations']))

    def test_governance_path_without_a_diff_needs_evidence(self):
        spec = loop_support.spec(goal='改进规则说明。',
                                 allowed_paths=['docs/orchestration.md'])
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertIsNone(proposal['required_human_decision'])

    def test_similar_file_names_are_not_the_governance_file(self):
        for path in ('AGENTS.md.backup', 'AGENTS.md.bak',
                     'docs/orchestration.md.tmp', 'editions-backup/master.md'):
            with self.subTest(path=path):
                spec = loop_support.spec(goal='整理文件。', allowed_paths=[path])
                proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
                self.assertEqual(proposal['status'], 'READY')
                self.assertEqual(proposal['risk_level'], 'LOW')

    def test_path_normalization_still_detects_the_real_sensitive_file(self):
        for path in ('./AGENTS.md', 'docs//orchestration.md',
                     'docs/design/../orchestration.md',
                     'docs\\design\\..\\orchestration.md'):
            with self.subTest(path=path):
                spec = loop_support.spec(goal='整理文件。', allowed_paths=[path])
                proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
                self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
                self.assertEqual(proposal['status'], 'BLOCKED')

    def test_absolute_or_escaping_paths_need_evidence(self):
        for path in ('/repo/AGENTS.md', '../AGENTS.md', 'C:\\repo\\AGENTS.md'):
            with self.subTest(path=path):
                spec = loop_support.spec(goal='整理文件。', allowed_paths=[path])
                proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
                self.assertEqual(proposal['status'], 'BLOCKED')
                self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_absolute_diff_path_is_not_trusted(self):
        spec = loop_support.spec(
            goal='修正文档错字。',
            allowed_paths=['docs/example.md'],
            change_evidence=loop_support.change_evidence(
                'docs/example.md',
                diff=('--- /tmp/example.md\n+++ /tmp/example.md\n'
                      '@@ -1 +1 @@\n-旧说明\n+新说明\n')))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_changed_paths_must_match_the_diff(self):
        two_file_diff = (
            'diff --git a/docs/example.md b/docs/example.md\n'
            '--- a/docs/example.md\n'
            '+++ b/docs/example.md\n'
            '@@ -1 +1 @@\n'
            '-旧说明\n'
            '+新说明\n'
            'diff --git a/docs/other.md b/docs/other.md\n'
            '--- a/docs/other.md\n'
            '+++ b/docs/other.md\n'
            '@@ -1 +1 @@\n'
            '-旧内容\n'
            '+新内容\n')
        evidence = loop_support.change_evidence(
            'docs/example.md', diff=two_file_diff,
            changed_paths=['docs/example.md'])
        spec = loop_support.spec(
            goal='修正文档错字。', allowed_paths=['docs/'],
            change_evidence=evidence)
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertTrue(any('changed_paths' in item
                            for item in proposal['required_evidence']))

    def test_diff_path_outside_allowed_paths_is_not_silent(self):
        spec = loop_support.spec(
            goal='修正文档错字。',
            allowed_paths=['docs/example.md'],
            change_evidence=loop_support.change_evidence(
                'docs/other.md', old='旧内容', new='新内容'))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')
        self.assertTrue(any('outside allowed paths' in item
                            for item in proposal['required_evidence']))

    def test_unbound_diff_is_not_trusted(self):
        evidence = loop_support.change_evidence(
            'docs/example.md', old='本步骤不修改权限。', new='本步骤不改动权限。')
        evidence.pop('head_sha')
        spec = loop_support.spec(
            goal='修正不修改权限的说明错字。',
            allowed_paths=['docs/example.md'], change_evidence=evidence)
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_caller_flags_do_not_downgrade_missing_evidence(self):
        spec = loop_support.spec(
            goal='修正不修改权限的说明错字。',
            allowed_paths=['docs/example.md'],
            low_risk=True, approved=True)
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'BLOCKED')
        self.assertEqual(proposal['risk_level'], 'NEEDS_EVIDENCE')

    def test_caller_flags_do_not_downgrade_a_real_permission_change(self):
        spec = loop_support.spec(
            goal='统一权限判定。',
            allowed_paths=['scripts/orchestrator.py'],
            low_risk=True, approved=True,
            change_evidence=loop_support.change_evidence(
                'scripts/orchestrator.py',
                old='allowed = False', new='allowed = True'))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')

    def test_old_format_docs_typo_without_evidence_keeps_the_low_risk_path(self):
        spec = loop_support.spec(goal='修正文档错字。',
                                 allowed_paths=['docs/example.md'])
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'READY')
        self.assertEqual(proposal['risk_level'], 'LOW')
        self.assertNotIn('change_evidence', proposal)

    def test_irreversible_code_change_is_high(self):
        spec = loop_support.spec(
            goal='清理旧数据。',
            allowed_paths=['scripts/migrate.py'],
            change_evidence=loop_support.change_evidence(
                'scripts/migrate.py',
                old='pass', new='cursor.execute("DROP TABLE lessons")'))
        proposal = plan_one(loop_support.general_lesson(), spec).proposals[0]
        self.assertEqual(proposal['status'], 'NEEDS_HUMAN_DECISION')
        self.assertEqual(proposal['required_human_decision'], 'HD-05')


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
            # The rendered field reference must point at the contract the
            # package actually ships, not at a project-owned path.
            self.assertIn('`kit/orchestration.md` 4.1', text)
            self.assertNotIn('`docs/orchestration.md` 4.1', text)
        self.assertIn('## 可改路径', github_body)
        self.assertIn('- **Allowed paths:**', local_packet)

    def test_lesson_without_a_spec_is_not_invented(self):
        empty = planning.plan_tasks(
            lesson_records=[loop_support.general_lesson()], specs={},
            edition='local').proposals[0]
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
