"""End-to-end composition tests for the Phase D role-selection pipeline (#124, S5).

S1-S4 are unit-tested on their own. This module answers the question the single
modules cannot: **do they still hold when they are wired together in the order the
session entry prescribes**, and does a successful selection ever read as a
dispatch?

Every test composes at least two real modules and uses synthetic facts only:

* the store lives under a temporary project root (no real ``.local``);
* discovery is fed by an in-memory runtime adapter or by the synthetic
  ``agent-registry.md`` records of the temporary scope;
* model data is a fixed in-memory ``SnapshotBatch`` (no network, no API key);
* no test imports a model provider, opens a socket or reads personal data.

The safety properties asserted here are the ones the design freezes in §10-§13:
``UNKNOWN`` / ``STALE`` / ``FAIL`` never become ``PASS``, a Reviewer is not the
Execution model, a failed source or write is reported honestly, and
``executed=False`` / "selection is not dispatch" survives the composition.
"""
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent_selection import (  # noqa: E402
    discovery,
    feedback,
    local_store,
    model_selection,
    roles,
    scope,
)
from scripts.agent_selection.model_sources import snapshot  # noqa: E402
from tests.agent_selection import support  # noqa: E402

NOW = support.NOW
PROBE = support.PROBE
OLD_PROBE = support.OLD_PROBE
SCOPE_A = 'e2e-scope-a'
SCOPE_B = 'e2e-scope-b'


# --------------------------------------------------------------------------- #
# Synthetic fixtures: a temporary project root, a scope and a model batch
# --------------------------------------------------------------------------- #
def registry_details(*, agent_id, model_id, model_family, modalities, tools,
                     capability_ref='cap-1', **extra):
    details = {
        'runtime_id': 'fixture-runtime', 'runtime_version': '1.0.0',
        'agent_id': agent_id, 'model_id': model_id,
        'model_family': model_family, 'modalities': modalities, 'tools': tools,
        'capability_ref': capability_ref, 'evidence_time': NOW,
        'source_class': 'OBSERVED',
    }
    details.update(extra)
    # The Markdown store refuses empty detail values; an absent key means the
    # same thing to the registry parser and keeps the record honest.
    return {key: value for key, value in details.items() if value != ''}


def model_snapshot(slug, index, *, coding, intelligence, agentic=50.0):
    return snapshot.ModelSnapshot(
        canonical_id=f'id-{index:02d}', name=f'Fixture {slug}', slug=slug,
        source_id='artificial-analysis',
        source_url='https://artificialanalysis.ai/api/v2/language/models',
        fetched_at=NOW,
        evaluations=(
            snapshot.ModelEvaluation('artificial_analysis_coding_index', coding),
            snapshot.ModelEvaluation(
                'artificial_analysis_intelligence_index', intelligence),
            snapshot.ModelEvaluation(
                'artificial_analysis_agentic_index', agentic),
        ),
        pricing=snapshot.ModelPricing(input_per_mtok=1.0),
        freshness=snapshot.FRESHNESS_FRESH, source_tier='pro')


def fresh_batch():
    """Eight fresh models: a real cohort for the percentile grading."""
    models = [
        model_snapshot('fixture-exec', 0, coding=95.0, intelligence=60.0),
        model_snapshot('fixture-review', 1, coding=20.0, intelligence=99.0),
        model_snapshot('fixture-master', 2, coding=70.0, intelligence=90.0),
    ]
    for index in range(3, 9):
        models.append(model_snapshot(
            f'fixture-filler-{index}', index,
            coding=20.0 + index * 4, intelligence=40.0 + index * 5))
    fetch = snapshot.FetchRecord(
        source_id='artificial-analysis',
        source_url='https://artificialanalysis.ai/api/v2/language/models',
        status='OK', fetched_at=NOW, tier='pro', index_version='4.3',
        models_returned=len(models), requests=1)
    return snapshot.SnapshotBatch(
        source_id='artificial-analysis', fetch=fetch, models=tuple(models))


def failed_batch(status='AUTH_FAILED', *, http_status=401,
                 message='authentication failed'):
    fetch = snapshot.FetchRecord(
        source_id='artificial-analysis',
        source_url='https://artificialanalysis.ai/api/v2/language/models',
        status=status, fetched_at=NOW, http_status=http_status,
        message=message, requests=1)
    return snapshot.SnapshotBatch(
        source_id='artificial-analysis', fetch=fetch, models=())


def stale_batch():
    return snapshot.stale_batch(
        fresh_batch(), status='STALE_CACHE',
        message='the live read failed and the cache is older than the window',
        now=NOW, cache_age_seconds=200000, max_age_seconds=86400,
        live_failure_status='RATE_LIMITED', http_status=429)


LINKS = {'model-a': 'fixture-exec', 'model-b': 'fixture-review',
         'model-master': 'fixture-master'}


class StoreCase(unittest.TestCase):
    """A temporary project root; every scope and document stays inside it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Resolve once: macOS `/var` and Windows short paths are symlinked or
        # re-cased by the store, and comparing an unresolved root would fail.
        self.root = Path(self._tmp.name).resolve()

    def make_scope(self, namespace=SCOPE_A, tty='/dev/pts/9'):
        resolution = scope.resolve_scope(
            self.root, env={'AGENTORCH_SCOPE_ID': namespace}, tty_name=tty)
        return local_store.LocalStore.from_resolution(self.root, resolution)

    def make_store(self, namespace=SCOPE_A, tty='/dev/pts/9'):
        store = self.make_scope(namespace, tty)
        store.initialize(now=NOW)
        return store

    def registry_store(self):
        """One scope whose ``agent-registry.md`` lists the three fixture roles."""
        store = self.make_store()
        entries = (
            registry_details(
                agent_id='master-1', model_id='model-master',
                model_family='family-master', modalities='text,image',
                tools='file-read', capability_ref=''),
            registry_details(
                agent_id='exec-1', model_id='model-a',
                model_family='family-a', modalities='text',
                tools='file-read,file-write,command', read_paths='src',
                write_paths='src', commands='python', context_tokens='200000',
                cost_per_mtok='1.0', latency_ms='1000'),
            registry_details(
                agent_id='review-1', model_id='model-b',
                model_family='family-b', modalities='text', tools='file-read',
                read_paths='src', read_only='true', cost_per_mtok='1.5',
                latency_ms='1200'),
        )
        for index, details in enumerate(entries, start=1):
            store.append_record(
                'agent-registry.md', summary=f'fixture agent {index}',
                source_class='OBSERVED', status='RECORDED', details=details,
                record_id=f'agent-{index}', now=NOW)
        return store

    def registry_discovery(self, store, capability_record=None):
        capability_record = capability_record or support.capability_record()
        registry = discovery.ExplicitRegistry.from_store(
            store, capability_records={'cap-1': capability_record})
        return discovery.discover_candidates(registry=registry)

    def select(self, discovered, *, batch=None, requirements=None, policy=None):
        return model_selection.select_models(
            fresh_batch() if batch is None else batch, discovered,
            requirements or support.task_requirements(), links=LINKS,
            policy=policy, now=NOW)

    def scope_directories(self):
        store_root = self.root / '.local' / 'agent-orch'
        return sorted(path.name for path in store_root.iterdir()
                      if path.is_dir() and not path.name.startswith('_'))


def execution_outcome(store, index=1, **overrides):
    values = dict(
        task_id=f'issue:{index}', category='code-change',
        identity=feedback.ModelIdentity('model-a', 'fixture-runtime', '1.0.0'),
        observed_at=NOW,
        evidence=(feedback.EvidenceRef('task_record', f'issue:{index}', NOW),),
        first_acceptance='PASS', rework_rounds=0,
        post_merge_regression_defects=0, scope_id=store.scope_id)
    values.update(overrides)
    return feedback.ExecutionOutcome(**values)


# --------------------------------------------------------------------------- #
# Positive control: the whole pipeline, still advisory
# --------------------------------------------------------------------------- #
class PositivePipelineTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.store = self.registry_store()
        self.capability = support.capability_record()
        self.discovered = self.registry_discovery(self.store, self.capability)
        self.report = self.select(self.discovered)

    def test_scope_and_store_are_isolated_under_the_project_root(self):
        self.assertTrue(self.store.scope_id.startswith(SCOPE_A))
        self.assertEqual(self.scope_directories(), [self.store.scope_id])
        self.assertTrue(self.store.directory.is_relative_to(self.root))

    def test_gate_passes_only_on_fresh_observed_c1_c4(self):
        gate = self.capability.gate(now=NOW)
        self.assertTrue(gate.passed)
        self.assertEqual(gate.status, 'PASS')
        self.assertFalse(gate.stale)

    def test_registry_discovery_returns_the_three_fixture_agents(self):
        self.assertEqual(self.discovered.outcome, discovery.DISCOVERY_REGISTRY)
        self.assertEqual(len(self.discovered.candidates), 3)
        self.assertEqual(
            {candidate.agent_id for candidate in self.discovered.candidates},
            {'master-1', 'exec-1', 'review-1'})

    def test_every_role_is_selected_and_the_reviewer_is_independent(self):
        self.assertTrue(self.report.automatic_allowed)
        execution = self.report.role(roles.ROLE_EXECUTION).selected
        reviewer = self.report.role(roles.ROLE_REVIEWER).selected
        self.assertIsNotNone(execution)
        self.assertIsNotNone(reviewer)
        self.assertEqual(execution.candidate.model_id, 'model-a')
        self.assertEqual(reviewer.candidate.model_id, 'model-b')
        self.assertNotEqual(execution.candidate.model_id,
                            reviewer.candidate.model_id)

    def test_selection_is_reported_as_not_executed(self):
        self.assertFalse(self.report.executed)
        self.assertIn('selection is a recommendation, not a dispatch',
                      ' '.join(self.report.notes))
        for module in (roles, discovery, model_selection):
            for name in ('spawn', 'start_agent', 'collect', 'wait_for'):
                self.assertFalse(hasattr(module, name),
                                 f'{module.__name__}.{name} would be a shadow dispatch')

    def test_external_evidence_is_attributed_and_role_specific(self):
        assessment = self.report.selected_assessment(roles.ROLE_EXECUTION)
        self.assertEqual(assessment.status, 'MATCHED')
        self.assertEqual(self.report.source.fetch.index_version, '4.3')
        coding = next(signal for signal in assessment.signals
                      if signal.dimension == 'coding')
        self.assertEqual(coding.evaluation_name,
                         'artificial_analysis_coding_index')
        self.assertEqual(coding.observed_at, NOW)
        reviewer = self.report.selected_assessment(roles.ROLE_REVIEWER)
        self.assertTrue(all(signal.grade == 'UNKNOWN'
                            for signal in reviewer.signals))

    def test_local_feedback_is_advisory_and_keeps_its_sample(self):
        for index in range(1, 4):
            feedback.record_outcome(
                self.store, execution_outcome(self.store, index), now=NOW,
                record_id=f'outcome-{index}')
        report = feedback.build_report(self.store, now=NOW)
        cohort = report.cohort('execution', 'code-change')
        metric = cohort.metric('first_acceptance_pass_rate')
        self.assertEqual(metric.status, 'OK')
        self.assertEqual(metric.value, 1.0)
        self.assertEqual(metric.sample_size, 3)
        self.assertEqual(metric.coverage, 1.0)
        self.assertFalse(report.applied_automatically)
        self.assertFalse(report.changes_configuration)
        self.assertEqual(report.scope_id, self.store.scope_id)

    def test_selection_history_appends_and_keeps_executed_false(self):
        for index in (1, 2):
            self.store.append_record(
                'selection-history.md', summary=f'selection {index}',
                source_class='OBSERVED', status='RECORDED',
                details={'execution': 'fixture-runtime:exec-1',
                         'executed': 'false'},
                record_id=f'selection-{index}', now=NOW)
        records = self.store.read_records('selection-history.md')
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].record_id, 'selection-1')
        self.assertEqual(records[0].details['executed'], 'false')
        self.assertEqual(records[1].record_id, 'selection-2')

    def test_selection_evidence_carries_every_required_field(self):
        """AC-3: the Markdown record names candidates, alternatives and limits."""
        report = self.report
        execution = report.role(roles.ROLE_EXECUTION)
        reviewer = report.role(roles.ROLE_REVIEWER)
        fetch = report.source.fetch
        self.store.append_record(
            'selection-history.md', summary='roles selected; not a dispatch',
            source_class='OBSERVED', status='RECORDED',
            details={
                'execution': execution.selected.candidate.candidate_id,
                'reviewer': reviewer.selected.candidate.candidate_id,
                'execution_model': execution.selected.candidate.model_id,
                'reviewer_model': reviewer.selected.candidate.model_id,
                'alternatives': ','.join(
                    item.candidate.candidate_id
                    for role in roles.ROLES
                    for item in report.role(role).alternatives) or '(none)',
                'hard_filter_failures': ','.join(
                    f'{item.candidate.candidate_id}:{entry.filter_id}'
                    for role in roles.ROLES
                    for item in report.role(role).excluded
                    for entry in item.filters if entry.status == 'FAIL')
                or '(none)',
                'source': f'{fetch.source_id} {fetch.status}',
                'fetched_at': fetch.fetched_at,
                'freshness': fetch.freshness,
                'degradations': '; '.join(report.degradations) or '(none)',
                'limitations': '; '.join(fetch.limitations) or '(none)',
                'executed': 'false',
                'scope': self.store.scope_id,
            },
            record_id='selection-evidence', now=NOW)
        path = self.store.directory / 'selection-history.md'
        self.assertTrue(path.is_relative_to(self.store.directory))
        text = path.read_text(encoding='utf-8')
        for key in ('execution', 'reviewer', 'alternatives',
                    'hard_filter_failures', 'source', 'fetched_at',
                    'freshness', 'degradations', 'limitations', 'executed',
                    'scope'):
            self.assertIn(f'{key}:', text)
        self.assertIn(self.store.scope_id, text)
        self.assertIn('model-a', text)
        self.assertIn('model-b', text)

    def test_external_scores_cannot_rescue_a_text_only_registry_master(self):
        text_only = self.make_store(SCOPE_B, '/dev/pts/8')
        text_only.append_record(
            'agent-registry.md', summary='text-only master',
            source_class='OBSERVED', status='RECORDED',
            details=registry_details(
                agent_id='master-1', model_id='model-master',
                model_family='family-master', modalities='text',
                tools='file-read', capability_ref=''),
            record_id='agent-1', now=NOW)
        discovered = self.registry_discovery(text_only)
        report = self.select(discovered)
        self.assertIsNone(report.role(roles.ROLE_MASTER).selected)
        self.assertFalse(report.automatic_allowed)
        # The top-scoring Master row exists; it still did not qualify.
        self.assertEqual(
            report.source.model('fixture-master').evaluation(
                'artificial_analysis_intelligence_index').value, 90.0)


# --------------------------------------------------------------------------- #
# C1-C4 gate
# --------------------------------------------------------------------------- #
class GateTests(unittest.TestCase):
    def report_for(self, capability_record):
        discovered = discovery.discover_candidates(
            adapter=support.FixtureAdapter(support.ok_result([
                support.candidate('master-1', model_id='model-master',
                                  model_family='family-master',
                                  modalities=('text', 'image'),
                                  tools=('file-read',),
                                  permission=support.permissions(),
                                  capability_record_value=None),
                support.candidate('exec-1', capability_record_value=capability_record),
                support.candidate('review-1', model_id='model-b',
                                  model_family='family-b', tools=('file-read',),
                                  permission=support.permissions(read_only=True),
                                  capability_record_value=capability_record),
            ])))
        return roles.select_roles(discovered, support.task_requirements(), now=NOW)

    def test_partial_record_keeps_the_task_undispatched(self):
        partial = support.capability_record(
            status='PARTIAL',
            overrides={'C1': support.capability('C1', 'UNKNOWN')})
        self.assertEqual(partial.gate(now=NOW).status, 'UNKNOWN')
        report = self.report_for(partial)
        self.assertFalse(report.executed)
        self.assertFalse(report.automatic_dispatch_allowed)
        selection = report.role(roles.ROLE_EXECUTION)
        self.assertIsNone(selection.selected)
        self.assertTrue(selection.manual_handoff)
        self.assertTrue(any('undispatched' in item
                            for item in report.degradations))

    def test_failed_gating_capability_is_blocked(self):
        failed = support.capability_record(
            overrides={'C4': support.capability('C4', 'FAIL')})
        self.assertEqual(failed.gate(now=NOW).status, 'BLOCKED')
        report = self.report_for(failed)
        self.assertIsNone(report.role(roles.ROLE_EXECUTION).selected)
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_stale_probe_is_blocked_and_not_reused(self):
        stale = support.capability_record(probe_date=OLD_PROBE)
        gate = stale.gate(now=NOW)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertTrue(gate.stale)
        report = self.report_for(stale)
        self.assertIsNone(report.role(roles.ROLE_EXECUTION).selected)
        self.assertFalse(report.automatic_dispatch_allowed)


    def test_refreshed_header_does_not_revive_an_expired_row(self):
        record = support.capability_record(
            probe_date=NOW,
            overrides={'C1': support.capability('C1', probe_date=OLD_PROBE)})
        gate = record.gate(now=NOW)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertTrue(gate.stale)
        self.assertIn('C1', ' '.join(gate.reasons))
        report = self.report_for(record)
        self.assertFalse(report.executed)
        self.assertFalse(report.automatic_dispatch_allowed)
        execution = report.role(roles.ROLE_EXECUTION)
        self.assertIsNone(execution.selected)
        self.assertTrue(execution.manual_handoff)
        # The stored evidence keeps its real dates: nothing is re-stamped.
        self.assertEqual(record.probe_date, NOW)
        self.assertEqual(record.get('C1').probe_date, OLD_PROBE)


# --------------------------------------------------------------------------- #
# Missing roles and empty discovery
# --------------------------------------------------------------------------- #
class RoleGapTests(unittest.TestCase):
    def test_missing_execution_hands_off_without_inventing_an_agent(self):
        discovered = support.FixtureAdapter(support.ok_result(
            support.standard_candidates()[:1]
            + support.standard_candidates()[2:]))
        report = roles.select_roles(
            discovery.discover_candidates(adapter=discovered),
            support.task_requirements(), now=NOW)
        selection = report.role(roles.ROLE_EXECUTION)
        self.assertIsNone(selection.selected)
        self.assertTrue(selection.manual_handoff)
        self.assertIn('No qualified Execution Agent', selection.reminder)
        self.assertIn('Register the agent', selection.reminder)
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_missing_reviewer_refuses_self_review(self):
        discovered = support.FixtureAdapter(support.ok_result(
            support.standard_candidates()[:2]))
        report = roles.select_roles(
            discovery.discover_candidates(adapter=discovered),
            support.task_requirements(), now=NOW)
        self.assertIsNotNone(report.role(roles.ROLE_EXECUTION).selected)
        reviewer = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(reviewer.selected)
        self.assertIn('self-review is not a substitute', reviewer.reminder)
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_zero_qualified_candidates_selects_nothing(self):
        junk = support.candidate(
            'junk-1', model_id='model-x', modalities=('text',), tools=(),
            permission=support.permissions(read_paths=(), write_paths=(),
                                           commands=()))
        report = roles.select_roles(
            discovery.discover_candidates(
                adapter=support.FixtureAdapter(support.ok_result([junk]))),
            support.task_requirements(), now=NOW)
        for role in roles.ROLES:
            selection = report.role(role)
            self.assertIsNone(selection.selected)
            self.assertTrue(selection.manual_handoff)
            self.assertIn('no candidate passed every hard filter',
                          selection.reasons[0])
        self.assertFalse(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)

    def test_empty_discovery_does_not_become_a_candidate(self):
        empty = discovery.discover_candidates(
            adapter=support.FixtureAdapter(support.runtime_adapter.DiscoveryResult(
                outcome='EMPTY', mode='runtime')))
        self.assertEqual(empty.outcome, discovery.DISCOVERY_EMPTY)
        report = roles.select_roles(empty, support.task_requirements(), now=NOW)
        self.assertTrue(all(selection.selected is None
                            for selection in report.selections.values()))
        self.assertFalse(report.automatic_dispatch_allowed)


# --------------------------------------------------------------------------- #
# Hard filters composed with external data
# --------------------------------------------------------------------------- #
class HardFilterCompositionTests(StoreCase):
    def discovered(self, candidates):
        return discovery.discover_candidates(
            adapter=support.FixtureAdapter(support.ok_result(candidates)))

    def test_same_model_reviewer_is_excluded_even_with_top_scores(self):
        master, execution, reviewer = support.standard_candidates()
        same = replace(reviewer, model_id='model-a', model_family='family-a')
        report = model_selection.select_models(
            fresh_batch(), self.discovered((master, execution, same)),
            support.task_requirements(), links=LINKS, now=NOW)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        entry = next(item for item in selection.excluded
                     if item.candidate.agent_id == 'review-1')
        independence = next(item for item in entry.filters
                            if item.filter_id == 'independence')
        self.assertEqual(independence.status, 'FAIL')
        self.assertFalse(report.automatic_allowed)
        assessment = next(item for item in report.assessments_for('reviewer')
                          if item.candidate_id == 'fixture-runtime:review-1')
        self.assertTrue(all(signal.grade == 'UNKNOWN'
                            for signal in assessment.signals))

    def test_budget_overrun_is_excluded_before_ranking(self):
        requirements = support.task_requirements()
        requirements[roles.ROLE_EXECUTION] = roles.execution_requirement(
            read_paths=('src',), write_paths=('src',), max_cost_per_mtok=0.5)
        report = model_selection.select_models(
            fresh_batch(),
            self.discovered(support.standard_candidates()), requirements,
            links=LINKS, now=NOW)
        self.assertIsNone(report.role(roles.ROLE_EXECUTION).selected)
        entry = report.role(roles.ROLE_EXECUTION).excluded[0]
        budget = next(item for item in entry.filters
                      if item.filter_id == 'budget')
        self.assertEqual(budget.status, 'FAIL')
        self.assertIn('exceeds the limit', budget.detail)
        self.assertFalse(report.automatic_allowed)

    def test_blank_runtime_paths_never_unlock_a_private_path_requirement(self):
        master, execution, reviewer = support.standard_candidates()
        blank = replace(execution, permissions=support.permissions(
            read_paths=('',), write_paths=('',), commands=('python',)))
        requirements = support.task_requirements()
        requirements[roles.ROLE_EXECUTION] = roles.execution_requirement(
            read_paths=('private/secret.py',),
            write_paths=('private/secret.py',),
            required_commands=('python',))
        report = model_selection.select_models(
            fresh_batch(), self.discovered((master, blank, reviewer)),
            requirements, links=LINKS, now=NOW)
        selection = report.role(roles.ROLE_EXECUTION)
        self.assertIsNone(selection.selected)
        self.assertFalse(report.automatic_allowed)
        self.assertFalse(report.executed)
        permissions = next(item for item in next(
            entry for entry in selection.excluded
            if entry.candidate.agent_id == 'exec-1').filters
            if item.filter_id == 'permissions')
        self.assertEqual(permissions.status, 'FAIL')
        self.assertIn('NOT_COVERED (private/secret.py)', permissions.detail)
        self.assertEqual(
            selection.requirement.as_dict()['task_write_paths'],
            ['private/secret.py'])

    def test_text_only_master_is_rejected_by_the_modality_filter(self):
        master, execution, reviewer = support.standard_candidates()
        text_only = replace(master, modalities=('text',))
        report = model_selection.select_models(
            fresh_batch(), self.discovered((text_only, execution, reviewer)),
            support.task_requirements(), links=LINKS, now=NOW)
        self.assertIsNone(report.role(roles.ROLE_MASTER).selected)
        modality = next(item for item in report.role(roles.ROLE_MASTER).excluded[0].filters
                        if item.filter_id == 'modality')
        self.assertEqual(modality.status, 'FAIL')
        self.assertIn('image', modality.detail)
        self.assertFalse(report.automatic_allowed)


# --------------------------------------------------------------------------- #
# Optional external model data
# --------------------------------------------------------------------------- #
class SourceTests(StoreCase):
    def discovered(self):
        return discovery.discover_candidates(
            adapter=support.FixtureAdapter(
                support.ok_result(support.standard_candidates())))

    def test_auth_failed_source_is_unlinked_and_never_blank(self):
        report = self.select(self.discovered(), batch=failed_batch())
        self.assertEqual(report.source.fetch.status, 'AUTH_FAILED')
        self.assertFalse(report.source.fetch.live)
        for role in roles.ROLES:
            assessment = report.selected_assessment(role)
            self.assertEqual(assessment.status, 'UNLINKED')
            self.assertTrue(all(signal.grade == 'UNKNOWN'
                                for signal in assessment.signals))
        # External data is optional: S2 selection still stands.
        self.assertIsNotNone(report.role(roles.ROLE_EXECUTION).selected)

    def test_a_fresh_source_policy_withholds_automatic_selection(self):
        policy = model_selection.AssessmentPolicy(require_fresh_source=True)
        report = self.select(self.discovered(), batch=failed_batch(),
                             policy=policy)
        self.assertFalse(report.automatic_allowed)
        self.assertTrue(any('AUTH_FAILED' in item for item in report.decisions))

    def test_expired_cache_is_stale_and_never_ranked(self):
        report = self.select(self.discovered(), batch=stale_batch())
        self.assertEqual(report.source.fetch.status, 'STALE_CACHE')
        self.assertEqual(report.source.fetch.freshness, 'STALE')
        self.assertFalse(report.source.fetch.usable)
        self.assertFalse(report.source.fetch.live)
        for role in roles.ROLES:
            assessment = report.selected_assessment(role)
            self.assertEqual(assessment.status, 'STALE')
            self.assertTrue(all(signal.grade == 'UNKNOWN'
                                for signal in assessment.signals))

    def test_stale_cache_with_a_fresh_policy_asks_for_a_decision(self):
        policy = model_selection.AssessmentPolicy(require_fresh_source=True)
        report = self.select(self.discovered(), batch=stale_batch(),
                             policy=policy)
        self.assertFalse(report.automatic_allowed)
        self.assertTrue(any('STALE_CACHE' in item for item in report.decisions))


# --------------------------------------------------------------------------- #
# Scope isolation and write honesty
# --------------------------------------------------------------------------- #
class ScopeAndWriteTests(StoreCase):
    def test_unresolved_scope_creates_no_shared_directory(self):
        with self.assertRaises(scope.ScopeIdentityError):
            local_store.open_store(self.root, env={}, tty_name=None)
        self.assertFalse((self.root / '.local').exists())

    def test_two_scopes_get_distinct_directories(self):
        store_a = self.make_store(SCOPE_A, '/dev/pts/1')
        store_b = self.make_store(SCOPE_B, '/dev/pts/2')
        self.assertNotEqual(store_a.scope_id, store_b.scope_id)
        self.assertEqual(self.scope_directories(),
                         sorted([store_a.scope_id, store_b.scope_id]))

    def test_cross_scope_record_is_refused_and_not_written(self):
        store_a = self.make_store(SCOPE_A, '/dev/pts/1')
        store_b = self.make_store(SCOPE_B, '/dev/pts/2')
        outcome = execution_outcome(store_a, scope_id=store_b.scope_id)
        with self.assertRaises(feedback.FeedbackScopeError):
            feedback.record_outcome(store_a, outcome, now=NOW)
        self.assertEqual(store_a.read_records(feedback.FEEDBACK_DOCUMENT), ())
        self.assertEqual(store_b.read_records(feedback.FEEDBACK_DOCUMENT), ())

    def test_write_failure_is_reported_and_leaves_no_record(self):
        store = self.make_store()
        with mock.patch.object(local_store.os, 'replace',
                               side_effect=PermissionError('read-only')):
            with self.assertRaises(feedback.FeedbackWriteError) as caught:
                feedback.record_outcome(
                    store, execution_outcome(store), now=NOW)
            with self.assertRaises(local_store.StoreWriteError):
                store.append_record('selection-history.md', summary='selection',
                                    source_class='OBSERVED', status='RECORDED',
                                    now=NOW)
        self.assertIn('was not written', str(caught.exception))
        self.assertEqual(store.read_records(feedback.FEEDBACK_DOCUMENT), ())
        self.assertEqual(store.read_records('selection-history.md'), ())


if __name__ == '__main__':
    unittest.main()
