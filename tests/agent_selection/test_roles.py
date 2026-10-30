"""Role matching / report tests (#121, design §7).

The fixture matrix covers the required negatives: no adapter, C1 fail, context
not isolated, empty result, missing Execution / Reviewer, a same-model
Reviewer, unknown model identity / family, insufficient permission, insufficient
Master modality, stale evidence and no candidates at all. No test reaches a
real model, the network or a real ``.local`` directory.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent_selection import discovery, roles, runtime_adapter  # noqa: E402
from tests.agent_selection import support  # noqa: E402

NOW = support.NOW


def replace_capability(candidate, capability_id, verdict, *, limits=''):
    record = support.capability_record(
        runtime_id=candidate.runtime_id,
        runtime_version=candidate.runtime_version,
        overrides={capability_id: support.capability(
            capability_id, verdict, evidence_ref='', limits=limits)})
    data = candidate.as_dict()
    return support.candidate(
        candidate.agent_id, model_id=candidate.model_id,
        model_family=candidate.model_family,
        runtime_id=candidate.runtime_id,
        runtime_version=candidate.runtime_version,
        modalities=candidate.modalities, tools=candidate.tools,
        permission=candidate.permissions,
        capability_record_value=record, source=candidate.source,
        source_class=candidate.source_class,
        evidence_time=candidate.evidence_time,
        context_tokens=data['context_tokens'],
        cost_per_mtok=data['cost_per_mtok'], latency_ms=data['latency_ms'],
        role_evidence=candidate.role_evidence)


class SuccessfulSelectionTests(unittest.TestCase):
    def setUp(self):
        self.master, self.execution, self.reviewer = \
            support.standard_candidates()
        self.report = support.report_with(
            (self.master, self.execution, self.reviewer))

    def test_all_three_roles_are_selected(self):
        self.assertTrue(self.report.automatic_dispatch_allowed)
        for role in roles.ROLES:
            with self.subTest(role=role):
                selection = self.report.role(role)
                self.assertTrue(selection.auto_allowed)
                self.assertFalse(selection.manual_handoff)
                self.assertIsNotNone(selection.selected)
                self.assertIsNone(selection.reminder)
        self.assertEqual(self.report.degradations, ())

    def test_selection_is_not_a_dispatch(self):
        self.assertFalse(self.report.executed)
        self.assertIn('does not spawn', ' '.join(self.report.notes))

    def test_report_carries_candidates_filters_reasons_and_evidence_time(self):
        payload = self.report.as_dict()
        self.assertEqual(payload['discovery']['outcome'], 'RUNTIME')
        execution = payload['selections']['execution']
        self.assertEqual(execution['selected']['agent_id'], 'exec-1')
        self.assertEqual(execution['selected']['model_id'], 'model-a')
        self.assertEqual(execution['selected']['evidence_time'], NOW)
        filter_ids = [item['filter'] for item in
                      execution['selected']['filters']]
        self.assertEqual(filter_ids, list(roles._FILTER_ORDER))
        self.assertTrue(execution['reasons'])
        self.assertIn('evidence_time', execution['selected'])
        self.assertIn('alternatives', execution)
        self.assertIn('excluded', execution)

    def test_ranking_reason_names_the_signal(self):
        reasons = ' '.join(self.report.role('execution').reasons)
        self.assertIn('coding=STRONG', reasons)
        self.assertIn('project', reasons)

    def test_capability_pass_never_widens_the_task_paths(self):
        requirement = self.report.role('execution').requirement
        self.assertEqual(requirement.as_dict()['task_write_paths'], ['src'])
        payload = json.dumps(self.report.as_dict())
        self.assertNotIn('docs', json.dumps(
            self.report.role('execution').requirement.as_dict()))


class DegradedDiscoveryTests(unittest.TestCase):
    def test_no_adapter_requires_manual_handoff(self):
        discovered = discovery.discover_candidates()
        self.assertEqual(discovered.outcome, discovery.DISCOVERY_NO_ADAPTER)
        report = roles.select_roles(discovered, support.task_requirements(),
                                    now=NOW)
        self.assertFalse(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)
        for role in roles.ROLES:
            with self.subTest(role=role):
                selection = report.role(role)
                self.assertIsNone(selection.selected)
                self.assertTrue(selection.manual_handoff)
                self.assertTrue(selection.reminder)
        self.assertIn('manual handoff', ' '.join(report.degradations))

    def test_no_candidates_at_all_lists_the_gap(self):
        discovered = discovery.discover_candidates(adapter=support.FixtureAdapter(
            runtime_adapter.DiscoveryResult(outcome='EMPTY', mode='runtime')))
        report = roles.select_roles(discovered, support.task_requirements(),
                                    now=NOW)
        self.assertFalse(report.automatic_dispatch_allowed)
        for role in roles.ROLES:
            selection = report.role(role)
            self.assertIn('discovery returned no candidate',
                          ' '.join(selection.missing))


class CapabilityFailureTests(unittest.TestCase):
    def run_with_execution(self, execution):
        master, _, reviewer = support.standard_candidates()
        report = support.report_with((master, execution, reviewer))
        return report.role('execution'), report

    def assert_blocked(self, execution, capability_id):
        selection, report = self.run_with_execution(execution)
        self.assertIsNone(selection.selected)
        self.assertFalse(selection.auto_allowed)
        self.assertTrue(selection.manual_handoff)
        self.assertIn(capability_id, ' '.join(selection.missing))
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_c1_spawn_failure_blocks_execution(self):
        _, execution, _ = support.standard_candidates()
        self.assert_blocked(replace_capability(execution, 'C1', 'FAIL'), 'C1')

    def test_c2_context_not_isolated_blocks_execution(self):
        _, execution, _ = support.standard_candidates()
        self.assert_blocked(replace_capability(execution, 'C2', 'FAIL'), 'C2')

    def test_c3_empty_result_blocks_execution(self):
        _, execution, _ = support.standard_candidates()
        self.assert_blocked(
            replace_capability(execution, 'C3', 'FAIL',
                               limits='wait returned an empty result'),
            'C3')

    def test_c4_unknown_terminal_state_blocks_execution(self):
        _, execution, _ = support.standard_candidates()
        self.assert_blocked(
            replace_capability(execution, 'C4', 'UNKNOWN'), 'C4')

    def test_stale_probe_blocks_execution(self):
        _, execution, _ = support.standard_candidates()
        stale = support.candidate(
            'exec-1', model_id='model-a', model_family='family-a',
            capability_record_value=support.capability_record(
                probe_date=support.OLD_PROBE),
            role_evidence=execution.role_evidence)
        selection, report = self.run_with_execution(stale)
        self.assertIsNone(selection.selected)
        self.assertIn('STALE', ' '.join(selection.missing))
        self.assertFalse(report.automatic_dispatch_allowed)


class CapabilityRowFreshnessTests(unittest.TestCase):
    """#205: a re-dated record header never qualifies a stale gating row."""

    def run_with_execution(self, execution):
        master, _, reviewer = support.standard_candidates()
        report = support.report_with((master, execution, reviewer))
        return report.role('execution'), report

    def execution_with_row(self, capability_id, probe_date, *, header=NOW):
        _, execution, _ = support.standard_candidates()
        record = support.capability_record(
            probe_date=header,
            overrides={capability_id: support.capability(
                capability_id, probe_date=probe_date)})
        return support.candidate(
            execution.agent_id, model_id=execution.model_id,
            model_family=execution.model_family,
            modalities=execution.modalities, tools=execution.tools,
            permission=execution.permissions,
            capability_record_value=record,
            role_evidence=execution.role_evidence)

    def test_fresh_header_with_a_stale_row_keeps_execution_unqualified(self):
        for capability_id in support.PASS_VERDICTS:
            with self.subTest(capability_id=capability_id):
                selection, report = self.run_with_execution(
                    self.execution_with_row(capability_id, support.OLD_PROBE))
                self.assertIsNone(selection.selected)
                self.assertFalse(selection.auto_allowed)
                self.assertTrue(selection.manual_handoff)
                self.assertFalse(report.automatic_dispatch_allowed)
                self.assertTrue(selection.excluded)
                callable_filter = next(
                    item for item in selection.excluded[0].filters
                    if item.filter_id == 'callable')
                self.assertEqual(callable_filter.status, 'FAIL')
                self.assertIn(capability_id, callable_filter.detail)
                self.assertIn('re-probe', callable_filter.detail)
                self.assertIn('STALE', callable_filter.detail)

    def test_fresh_header_and_fresh_rows_still_qualify(self):
        selection, report = self.run_with_execution(
            self.execution_with_row('C2', support.PROBE))
        self.assertIsNotNone(selection.selected)
        self.assertTrue(report.automatic_dispatch_allowed)

    def test_future_row_is_reported_as_unverified_not_stale(self):
        selection, report = self.run_with_execution(
            self.execution_with_row('C3', '2026-10-28T12:00:00Z'))
        self.assertIsNone(selection.selected)
        self.assertFalse(report.automatic_dispatch_allowed)
        self.assertTrue(selection.unverified)
        callable_filter = next(
            item for item in selection.unverified[0].filters
            if item.filter_id == 'callable')
        self.assertEqual(callable_filter.status, 'UNKNOWN')
        self.assertIn('C3', callable_filter.detail)
        self.assertIn('future', callable_filter.detail)

    def test_explicit_max_age_days_applies_to_rows_in_the_report(self):
        candidate = self.execution_with_row('C4', '2026-10-17T12:00:00Z')
        at_limit = support.report_with(
            (support.standard_candidates()[0], candidate,
             support.standard_candidates()[2]))
        self.assertTrue(at_limit.automatic_dispatch_allowed)
        master, _, reviewer = support.standard_candidates()
        stricter = roles.select_roles(
            discovery.discover_candidates(
                adapter=support.FixtureAdapter(
                    support.ok_result((master, candidate, reviewer)))),
            support.task_requirements(), now=NOW, max_age_days=9)
        self.assertFalse(stricter.automatic_dispatch_allowed)
        self.assertIsNone(stricter.role('execution').selected)


class MissingRoleTests(unittest.TestCase):
    def test_missing_execution_gives_an_actionable_reminder(self):
        master, _, reviewer = support.standard_candidates()
        report = support.report_with((master, reviewer))
        selection = report.role('execution')
        self.assertIsNone(selection.selected)
        self.assertIn('No qualified Execution Agent', selection.reminder)
        self.assertIn('agent-registry.md', selection.reminder)
        self.assertIn('does not create', selection.reminder)
        self.assertIn('undispatched', selection.reminder)
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_missing_execution_blocks_reviewer_independence(self):
        master, _, reviewer = support.standard_candidates()
        report = support.report_with((master, reviewer))
        selection = report.role('reviewer')
        self.assertIsNone(selection.selected)
        self.assertIn('no Execution model was selected',
                      ' '.join(selection.missing))
        self.assertIn('Reviewer independence', ' '.join(report.degradations))
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_missing_reviewer_keeps_the_task_unreviewed(self):
        master, execution, _ = support.standard_candidates()
        report = support.report_with((master, execution))
        selection = report.role('reviewer')
        self.assertIsNone(selection.selected)
        self.assertIn('self-review', selection.reminder)
        self.assertIn('read-only', selection.reminder)
        self.assertIn('unreviewed', ' '.join(report.degradations))

    def test_execution_cannot_act_as_its_own_reviewer(self):
        master, execution, _ = support.standard_candidates()
        report = support.report_with((master, execution))
        self.assertIsNone(report.role('reviewer').selected)
        self.assertFalse(report.automatic_dispatch_allowed)


class ReviewerIndependenceTests(unittest.TestCase):
    def test_same_underlying_model_id_is_excluded(self):
        master, execution, reviewer = support.standard_candidates()
        same_model = support.candidate(
            'review-1', model_id='model-a', model_family='family-a',
            modalities=('text',), tools=('file-read',),
            permission=support.permissions(read_only=True),
            role_evidence=reviewer.role_evidence)
        report = support.report_with((master, execution, same_model))
        selection = report.role('reviewer')
        self.assertIsNone(selection.selected)
        self.assertIn('same as the selected Execution model',
                      ' '.join(selection.missing))
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_custom_reviewer_requirement_still_rejects_the_execution_model(self):
        master, execution, reviewer = support.standard_candidates()
        same_model = support.candidate(
            'review-1', model_id='model-a', model_family='family-a',
            modalities=('text',), tools=('file-read',),
            permission=support.permissions(read_only=True),
            role_evidence=reviewer.role_evidence)
        requirements = support.task_requirements()
        # Deliberately omit excluded_model_ids / reference fields: the selector
        # must derive independence from the selected Execution model.
        requirements[roles.ROLE_REVIEWER] = roles.RoleRequirement(
            role=roles.ROLE_REVIEWER, required_modalities=('text',),
            required_tools=('file-read',), read_paths=('src',),
            require_read_only=True)
        report = support.report_with((master, execution, same_model),
                                     requirements=requirements)
        selection = report.role('reviewer')
        self.assertIsNone(selection.selected)
        self.assertIn('same as the selected Execution model',
                      ' '.join(selection.missing))
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_unknown_model_family_is_reported_as_unknown(self):
        master, execution, reviewer = support.standard_candidates()
        unknown_family = support.candidate(
            'review-1', model_id='model-b', model_family=None,
            modalities=('text',), tools=('file-read',),
            permission=support.permissions(read_only=True),
            role_evidence=reviewer.role_evidence)
        report = support.report_with((master, execution, unknown_family))
        selection = report.role('reviewer')
        self.assertIsNotNone(selection.selected)
        independence = next(
            item for item in selection.selected.filters
            if item.filter_id == 'independence')
        self.assertEqual(independence.status, 'PASS')
        self.assertIn('family UNKNOWN', independence.detail)
        self.assertNotIn('family-level independence is established',
                         ' '.join(selection.reasons))

    def test_unknown_model_identity_is_not_a_qualified_role(self):
        master, execution, reviewer = support.standard_candidates()
        unidentified = support.candidate(
            'review-1', model_id=None, model_family=None,
            modalities=('text',), tools=('file-read',),
            permission=support.permissions(read_only=True),
            role_evidence=reviewer.role_evidence)
        report = support.report_with((master, execution, unidentified))
        selection = report.role('reviewer')
        self.assertIsNone(selection.selected)
        self.assertIn('model identity is UNKNOWN', ' '.join(selection.missing))

    def test_write_capable_reviewer_is_excluded_by_default(self):
        master, execution, reviewer = support.standard_candidates()
        writer = support.candidate(
            'review-1', model_id='model-b', model_family='family-b',
            modalities=('text',), tools=('file-read',),
            permission=support.permissions(write_paths=('src',)),
            role_evidence=reviewer.role_evidence)
        report = support.report_with((master, execution, writer))
        selection = report.role('reviewer')
        self.assertIsNone(selection.selected)
        self.assertIn('read-only', ' '.join(selection.missing))


class PermissionAndModalityTests(unittest.TestCase):
    def test_write_scope_shortfall_excludes_execution(self):
        master, execution, reviewer = support.standard_candidates()
        limited = support.candidate(
            'exec-1', model_id='model-a', model_family='family-a',
            permission=support.permissions(read_paths=('src',),
                                           write_paths=('docs',)),
            role_evidence=execution.role_evidence)
        report = support.report_with((master, limited, reviewer))
        selection = report.role('execution')
        self.assertIsNone(selection.selected)
        self.assertIn('write: NOT_COVERED (src)', ' '.join(selection.missing))
        self.assertEqual(
            selection.requirement.as_dict()['task_write_paths'], ['src'])

    def test_read_scope_shortfall_excludes_candidate(self):
        master, execution, reviewer = support.standard_candidates()
        requirement = support.task_requirements()
        requirement[roles.ROLE_EXECUTION] = roles.execution_requirement(
            read_paths=('src', 'docs'), write_paths=('src',))
        report = support.report_with((master, execution, reviewer),
                                     requirements=requirement)
        selection = report.role('execution')
        self.assertIsNone(selection.selected)
        self.assertIn('read: NOT_COVERED (docs)',
                      ' '.join(selection.missing))

    def test_execution_without_command_permission_is_excluded(self):
        master, execution, reviewer = support.standard_candidates()
        no_commands = support.candidate(
            'exec-1', model_id='model-a', model_family='family-a',
            permission=support.permissions(read_paths=('src',),
                                           write_paths=('src',),
                                           commands=()),
            role_evidence=execution.role_evidence)
        report = support.report_with((master, no_commands, reviewer))
        selection = report.role('execution')
        self.assertIsNone(selection.selected)
        self.assertIn('commands: UNOBSERVED', ' '.join(selection.missing))
        self.assertFalse(report.automatic_dispatch_allowed)
        evaluation = selection.unverified[0]
        permissions = next(item for item in evaluation.filters
                           if item.filter_id == 'permissions')
        self.assertEqual(permissions.status, 'UNKNOWN')
        self.assertNotIn('covers the task paths', permissions.detail)

    def test_required_command_must_be_observed(self):
        master, execution, reviewer = support.standard_candidates()
        requirement = support.task_requirements()
        requirement[roles.ROLE_EXECUTION] = roles.execution_requirement(
            read_paths=('src',), write_paths=('src',),
            required_commands=('python3',))
        wrong_command = support.candidate(
            'exec-1', model_id='model-a', model_family='family-a',
            permission=support.permissions(read_paths=('src',),
                                           write_paths=('src',),
                                           commands=('python',)),
            role_evidence=execution.role_evidence)
        report = support.report_with((master, wrong_command, reviewer),
                                     requirements=requirement)
        selection = report.role('execution')
        self.assertIsNone(selection.selected)
        self.assertIn('commands: NOT_COVERED (python3)',
                      ' '.join(selection.missing))

    def test_master_permissions_are_reported_as_not_required(self):
        report = support.report_with(support.standard_candidates())
        evaluation = report.role('master').selected
        permissions = next(item for item in evaluation.filters
                           if item.filter_id == 'permissions')
        self.assertEqual(permissions.status, 'NOT_APPLICABLE')
        self.assertIn('read: NOT_REQUIRED', permissions.detail)
        self.assertIn('not an observed grant', permissions.detail)

    def test_master_without_image_input_is_excluded(self):
        master, execution, reviewer = support.standard_candidates()
        text_only = support.candidate(
            'master-1', model_id='model-master', model_family='family-master',
            modalities=('text',), tools=('file-read',),
            permission=support.permissions(), capability_record_value=None)
        report = support.report_with((text_only, execution, reviewer))
        selection = report.role('master')
        self.assertIsNone(selection.selected)
        self.assertIn('image', ' '.join(selection.missing))
        self.assertIn('text + image', selection.reminder)
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_budget_limit_excludes_candidate(self):
        master, execution, reviewer = support.standard_candidates()
        requirement = support.task_requirements()
        requirement[roles.ROLE_EXECUTION] = roles.execution_requirement(
            read_paths=('src',), write_paths=('src',), max_cost_per_mtok=10)
        expensive = support.candidate(
            'exec-1', model_id='model-a', model_family='family-a',
            cost_per_mtok=50, role_evidence=execution.role_evidence)
        report = support.report_with((master, expensive, reviewer),
                                     requirements=requirement)
        selection = report.role('execution')
        self.assertIsNone(selection.selected)
        self.assertIn('exceeds the limit', ' '.join(selection.missing))

    def test_unreported_cost_with_a_limit_is_unverified_not_selected(self):
        master, execution, reviewer = support.standard_candidates()
        requirement = support.task_requirements()
        requirement[roles.ROLE_EXECUTION] = roles.execution_requirement(
            read_paths=('src',), write_paths=('src',), max_cost_per_mtok=10)
        unknown_cost = support.candidate(
            'exec-1', model_id='model-a', model_family='family-a',
            cost_per_mtok=None, role_evidence=execution.role_evidence)
        report = support.report_with((master, unknown_cost, reviewer),
                                     requirements=requirement)
        selection = report.role('execution')
        self.assertIsNone(selection.selected)
        self.assertEqual(len(selection.unverified), 1)
        self.assertIn('not reported', ' '.join(selection.missing))


class RankingTests(unittest.TestCase):
    def test_role_evidence_outranks_price(self):
        master, execution, reviewer = support.standard_candidates()
        strong = support.candidate(
            'exec-strong', model_id='model-a', model_family='family-a',
            cost_per_mtok=8,
            role_evidence=(support.evidence('execution', 'coding'),))
        cheap = support.candidate(
            'exec-cheap', model_id='model-c', model_family='family-c',
            cost_per_mtok=1, role_evidence=())
        report = support.report_with((master, strong, cheap, reviewer))
        self.assertEqual(report.role('execution').selected.candidate.agent_id,
                         'exec-strong')
        self.assertIn('coding=STRONG', ' '.join(
            report.role('execution').reasons))

    def test_equal_evidence_falls_back_to_cost(self):
        master, execution, reviewer = support.standard_candidates()
        pricey = support.candidate(
            'exec-pricey', model_id='model-c', model_family='family-c',
            cost_per_mtok=9,
            role_evidence=(support.evidence('execution', 'coding'),))
        cheaper = support.candidate(
            'exec-cheaper', model_id='model-a', model_family='family-a',
            cost_per_mtok=2,
            role_evidence=(support.evidence('execution', 'coding'),))
        report = support.report_with((master, pricey, cheaper, reviewer))
        self.assertEqual(report.role('execution').selected.candidate.agent_id,
                         'exec-cheaper')

    def test_small_sample_is_reported_but_not_counted(self):
        master, _, reviewer = support.standard_candidates()
        anecdote = support.candidate(
            'exec-anecdote', model_id='model-c', model_family='family-c',
            cost_per_mtok=1,
            role_evidence=(support.evidence('execution', 'coding', sample=1),))
        report = support.report_with((master, anecdote, reviewer))
        selection = report.role('execution')
        self.assertEqual(selection.selected.candidate.agent_id, 'exec-anecdote')
        self.assertIn('sample 1 is below', ' '.join(selection.reasons))
        # Counted project-strong evidence is 0, so cost / latency decided.
        self.assertEqual(selection.selected.rank_key[:3], (0, 0, 0))


class ReportBoundaryTests(unittest.TestCase):
    def test_selecting_without_a_master_requirement_is_refused(self):
        master, execution, reviewer = support.standard_candidates()
        discovered = discovery.discover_candidates(
            adapter=support.FixtureAdapter(
                support.ok_result((master, execution, reviewer))))
        requirements = support.task_requirements()
        del requirements[roles.ROLE_MASTER]
        with self.assertRaises(ValueError):
            roles.select_roles(discovered, requirements, now=NOW)

    def test_report_has_no_task_state_machine_fields(self):
        master, execution, reviewer = support.standard_candidates()
        report = support.report_with((master, execution, reviewer))
        payload = json.dumps(report.as_dict())
        for state in ('DISPATCHED', 'IN_PROGRESS', 'PR_OPEN', 'VERIFIED',
                      'APPROVED', 'MERGED'):
            self.assertNotIn(state, payload)
        roles_source = Path(roles.__file__).read_text(encoding='utf-8')
        self.assertNotIn('orchestrator', roles_source)

    def test_alternatives_are_listed_after_the_selected_candidate(self):
        master, execution, reviewer = support.standard_candidates()
        second = support.candidate(
            'exec-2', model_id='model-c', model_family='family-c',
            cost_per_mtok=2,
            role_evidence=(support.evidence('execution', 'coding',
                                            'MODERATE', sample=4),))
        report = support.report_with((master, execution, second, reviewer))
        selection = report.role('execution')
        self.assertEqual(selection.selected.candidate.agent_id, 'exec-1')
        self.assertEqual([item.candidate.agent_id for item in
                          selection.alternatives], ['exec-2'])
        self.assertIsNotNone(selection.alternatives[0].rank_key)

    def test_unverified_candidates_are_kept_for_human_decision(self):
        master, execution, reviewer = support.standard_candidates()
        requirement = support.task_requirements()
        requirement[roles.ROLE_EXECUTION] = roles.execution_requirement(
            read_paths=('src',), write_paths=('src',),
            min_context_tokens=400000)
        small_context = support.candidate(
            'exec-1', model_id='model-a', model_family='family-a',
            context_tokens=None, role_evidence=execution.role_evidence)
        report = support.report_with((master, small_context, reviewer),
                                     requirements=requirement)
        selection = report.role('execution')
        self.assertIsNone(selection.selected)
        self.assertEqual(len(selection.unverified), 1)
        self.assertIn('context capacity was not reported',
                      ' '.join(selection.missing))


if __name__ == '__main__':
    unittest.main()
