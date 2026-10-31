"""Role-specific model selection tests (#122, S3).

The tests prove the required order and negatives with fixed fixtures:

* S2's hard filters still decide qualification (a high external score cannot
  rescue a missing modality, a budget overrun or a same-model Reviewer);
* external data only orders the survivors;
* the Reviewer is never scored by a general intelligence index;
* no qualified candidate, a failed source, an expired cache and a policy that
  requires fresh external data all stop or ask instead of inventing a result.

No test reaches the network, a real key or a real ``.local`` directory.
"""
import json
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent_selection import (  # noqa: E402
    discovery,
    feedback,
    model_selection,
    peer_review,
    roles,
)
from scripts.agent_selection.model_sources import (  # noqa: E402
    artificial_analysis,
    snapshot,
)
from tests.agent_selection import support  # noqa: E402
from tests.agent_selection.model_sources import fixtures  # noqa: E402

NOW = support.NOW
KEY_ENV = artificial_analysis.DEFAULT_API_KEY_ENV


def scored_row(slug, index, *, coding, intelligence, agentic, ifbench,
               gpqa=0.5):
    return fixtures.model_row(index, slug=slug, evaluations={
        'artificial_analysis_coding_index': coding,
        'artificial_analysis_agentic_index': agentic,
        'artificial_analysis_intelligence_index': intelligence,
        'ifbench': ifbench,
        'gpqa_diamond': gpqa,
    })


def scored_payload():
    rows = [
        scored_row('fixture-exec', 0, coding=95, intelligence=60, agentic=95,
                   ifbench=0.90),
        scored_row('fixture-review', 1, coding=20, intelligence=99, agentic=30,
                   ifbench=0.90),
        scored_row('fixture-master', 2, coding=70, intelligence=90, agentic=80,
                   ifbench=0.95),
        scored_row('fixture-weak', 3, coding=12, intelligence=40, agentic=25,
                   ifbench=0.40),
    ]
    for index in range(4, 10):
        rows.append(scored_row(
            f'fixture-filler-{index}', index, coding=30 + index * 4,
            intelligence=45 + index * 3, agentic=35 + index * 4,
            ifbench=0.45 + index * 0.03))
    return fixtures.payload(rows=rows, tier='pro')


def fetch_batch(*, response=None, cache=None, now=NOW, key=fixtures.API_KEY):
    transport = fixtures.FakeTransport([
        response if response is not None
        else fixtures.json_response(scored_payload())])
    source = fixtures.source(transport=transport, cache=cache)
    with mock.patch.dict(os.environ, {KEY_ENV: key}, clear=False):
        return source.fetch(now=now)


def default_links():
    return {
        'model-master': 'fixture-master',
        'model-a': 'fixture-exec',
        'model-b': 'fixture-review',
        'model-c': 'fixture-weak',
    }


def make_discovery(candidates):
    # The fixture scope registry declares the identities; a runtime claim alone
    # would be discarded at the discovery boundary (#170).
    return support.discovery_with(candidates)


def select(candidates=None, *, batch=None, links=None, policy=None,
           requirements=None, now=NOW, **extra):
    candidates = support.standard_candidates() if candidates is None else candidates
    batch = fetch_batch() if batch is None else batch
    return model_selection.select_models(
        batch, make_discovery(candidates),
        requirements or support.task_requirements(),
        links=default_links() if links is None else links,
        policy=policy, now=now, **extra)


# --- local ETR / credibility fixtures (#157) --------------------------------
LOCAL_SCOPE = 'model-selection-scope-157'
LOCAL_RUNTIME = 'fixture-runtime'
LOCAL_RUNTIME_VERSION = '1.0.0'
LOCAL_CATEGORY = 'code-change'


def local_outcome(model_id, index, contribution, *, total_tokens=1000):
    return feedback.ExecutionOutcome(
        task_id=f'{model_id.replace("/", "-")}-{index}',
        category=LOCAL_CATEGORY,
        identity=feedback.ModelIdentity(
            model_id, LOCAL_RUNTIME, LOCAL_RUNTIME_VERSION),
        observed_at=NOW,
        evidence=(feedback.EvidenceRef(
            'task_record', f'issue:{model_id}-{index}', NOW),),
        first_acceptance='PASS',
        token_usage=feedback.TokenUsage(
            total_tokens=total_tokens, source='runtime:fixture',
            complete=True),
        contribution=contribution,
        scope_id=LOCAL_SCOPE)


def local_report(models, *, total=20):
    """One feedback report per model id; ``models`` maps id -> effective count."""
    outcomes = []
    for model_id, effective in models.items():
        for index in range(total):
            contribution = 'EFFECTIVE' if index < effective else 'INEFFECTIVE'
            outcomes.append(local_outcome(model_id, index, contribution))
    store = SimpleNamespace(scope_id=LOCAL_SCOPE)
    return feedback.build_report(store, now=NOW, outcomes=outcomes)


def review_event(evaluator, subject_model, *, category=LOCAL_CATEGORY,
                 task='issue:157', dimension='goal_conformance'):
    return peer_review.PeerReview(
        direction='master->execution', task_id=task, category=category,
        dimension=dimension, score=peer_review.POSITIVE,
        evaluator=feedback.ModelIdentity(
            evaluator, LOCAL_RUNTIME, LOCAL_RUNTIME_VERSION),
        subject=feedback.ModelIdentity(
            subject_model, LOCAL_RUNTIME, LOCAL_RUNTIME_VERSION),
        observed_at=NOW,
        evidence=(feedback.EvidenceRef(
            'master_review', f'review:{task}', NOW),),
        reason='fixture independent review', task_version='v1',
        evidence_scope=LOCAL_SCOPE, identity_verified=True, blind=True,
        scope_id=LOCAL_SCOPE)


def corroborated(subject_model):
    """Two independent, blind, verified evaluations of the same fact."""
    return (review_event('evaluator-a', subject_model),
            review_event('evaluator-b', subject_model))


def single_review(subject_model):
    return (review_event('evaluator-a', subject_model),)


def select_with_local(candidates=None, *, report, reviews=(), requirements=None):
    return select(
        candidates, requirements=requirements, feedback_report=report,
        task_category=LOCAL_CATEGORY, scope_id=LOCAL_SCOPE,
        peer_reviews=reviews)


class SuccessfulSelectionTests(unittest.TestCase):
    def setUp(self):
        self.report = select()

    def test_s2_selection_is_reused_and_extended(self):
        self.assertIsInstance(self.report.role_selection, roles.SelectionReport)
        self.assertTrue(self.report.automatic_allowed)
        execution = self.report.role('execution')
        self.assertEqual(execution.selected.candidate.candidate_id, 'fixture-runtime:exec-1')
        external = [item for item in execution.selected.candidate.role_evidence
                    if item.origin == 'external-reference' and item.grade != 'UNKNOWN']
        self.assertTrue(external)
        self.assertFalse(self.report.executed)

    def test_execution_gets_coding_and_agentic_evidence(self):
        assessment = self.report.selected_assessment('execution')
        self.assertIsNotNone(assessment)
        self.assertEqual(assessment.status, 'MATCHED')
        self.assertEqual(assessment.snapshot_slug, 'fixture-exec')
        by_dimension = {signal.dimension: signal for signal in assessment.signals}
        self.assertEqual(by_dimension['coding'].grade, 'STRONG')
        self.assertEqual(by_dimension['coding'].evaluation_name,
                         'artificial_analysis_coding_index')
        self.assertGreaterEqual(by_dimension['coding'].percentile, 0.8)
        self.assertEqual(by_dimension['tool-use'].grade, 'STRONG')

    def test_source_provenance_is_in_the_report(self):
        payload = self.report.as_dict()
        fetch = payload['source']['fetch']
        self.assertEqual(fetch['status'], 'OK')
        self.assertTrue(fetch['live'])
        self.assertEqual(fetch['fetched_at'], NOW)
        self.assertEqual(fetch['index_version'], '4.3')
        self.assertEqual(fetch['tier'], 'pro')
        self.assertEqual(fetch['models_returned'], 10)
        # JSON serialised without an UNKNOWN sentinel leaking a raw None where
        # the contract says UNKNOWN.
        text = json.dumps(payload)
        self.assertIn('artificial_analysis_coding_index', text)
        self.assertIn('fixture-exec', text)

    def test_report_lines_are_human_readable(self):
        lines = '\n'.join(self.report.render_lines())
        self.assertIn('source: artificial-analysis status=OK', lines)
        self.assertIn('execution: fixture-runtime:exec-1', lines)
        self.assertIn('signal: coding=STRONG', lines)


class ReviewerSpecificTests(unittest.TestCase):
    def test_generic_intelligence_index_is_not_a_review_score(self):
        report = select()
        assessment = report.selected_assessment('reviewer')
        self.assertEqual(assessment.status, 'MATCHED')
        self.assertTrue(all(signal.grade == 'UNKNOWN'
                            for signal in assessment.signals))
        self.assertFalse(any(
            signal.evaluation_name == 'artificial_analysis_intelligence_index'
            for signal in assessment.signals))
        self.assertIn('not substituted',
                      ' '.join(signal.note for signal in assessment.signals))
        # The reviewer fixture deliberately has the top intelligence score.
        reviewer_model = report.source.model('fixture-review')
        self.assertEqual(
            reviewer_model.evaluation(
                'artificial_analysis_intelligence_index').value, 99.0)

    def test_same_model_reviewer_is_rejected_even_with_top_scores(self):
        master, execution, reviewer = support.standard_candidates()
        same = replace(reviewer, model_id='model-a', model_family='family-a')
        report = select((master, execution, same))
        selection = report.role('reviewer')
        self.assertIsNone(selection.selected)
        same_evaluation = next(
            item for item in selection.excluded
            if item.candidate.candidate_id == same.candidate_id)
        independence = next(
            item for item in same_evaluation.filters
            if item.filter_id == 'independence')
        self.assertEqual(independence.status, 'FAIL')
        self.assertFalse(report.automatic_allowed)

    def test_unknown_family_is_not_claimed_as_family_independence(self):
        master, execution, reviewer = support.standard_candidates()
        no_family = replace(reviewer, model_family=None)
        report = select((master, execution, no_family))
        selection = report.role('reviewer')
        independence = next(
            item for item in selection.selected.filters
            if item.filter_id == 'independence')
        self.assertEqual(independence.status, 'PASS')
        self.assertIn('UNKNOWN', independence.detail)
        assessment = report.selected_assessment('reviewer')
        self.assertIsNone(assessment.model_family)
        self.assertEqual(assessment.as_dict()['model_family'],
                         snapshot.UNKNOWN)

    def test_same_family_is_reported_without_claiming_family_independence(self):
        master, execution, reviewer = support.standard_candidates()
        same_family = replace(reviewer, model_family='family-a')
        report = select((master, execution, same_family))
        independence = next(
            item for item in report.role('reviewer').selected.filters
            if item.filter_id == 'independence')
        self.assertEqual(independence.status, 'PASS')
        self.assertIn('same model family', independence.detail)


class HardFilterFirstTests(unittest.TestCase):
    def test_external_scores_cannot_rescue_a_missing_modality(self):
        master, execution, reviewer = support.standard_candidates()
        text_only = replace(master, modalities=('text',))
        report = select((text_only, execution, reviewer))
        self.assertIsNone(report.role('master').selected)
        modality = next(
            item for item in report.role('master').excluded[0].filters
            if item.filter_id == 'modality')
        self.assertEqual(modality.status, 'FAIL')
        self.assertFalse(report.automatic_allowed)
        # The strong external master row exists; it still did not qualify.
        self.assertEqual(
            report.source.model('fixture-master').evaluation(
                'artificial_analysis_intelligence_index').value, 90.0)

    def test_external_scores_cannot_rescue_a_budget_overrun(self):
        master, execution, reviewer = support.standard_candidates()
        strict = support.task_requirements()
        strict[roles.ROLE_EXECUTION] = roles.execution_requirement(
            read_paths=('src',), write_paths=('src',),
            max_cost_per_mtok=0.5)
        report = select((master, execution, reviewer), requirements=strict)
        self.assertIsNone(report.role('execution').selected)
        budget = next(
            item for item in report.role('execution').excluded[0].filters
            if item.filter_id == 'budget')
        self.assertEqual(budget.status, 'FAIL')
        self.assertFalse(report.automatic_allowed)

    def test_external_evidence_orders_survivors_after_the_filters(self):
        master, execution, reviewer = support.standard_candidates()
        # Same S2 project evidence and cost, different external scores.
        weak = replace(execution, agent_id='exec-2', model_id='model-c',
                       model_family='family-c')
        report = select((master, execution, weak, reviewer))
        self.assertEqual(
            report.role('execution').selected.candidate.agent_id, 'exec-1')
        assessments = {item.candidate_id: item
                       for item in report.assessments_for('execution')}
        self.assertEqual(
            next(signal.grade for signal in
                 assessments['fixture-runtime:exec-1'].signals
                 if signal.dimension == 'coding'), 'STRONG')
        self.assertEqual(
            next(signal.grade for signal in
                 assessments['fixture-runtime:exec-2'].signals
                 if signal.dimension == 'coding'), 'WEAK')


class DegradedSourceTests(unittest.TestCase):
    def test_missing_source_is_unknown_not_a_capability_absence(self):
        failed = fetch_batch(response=fixtures.json_response(
            {'error': 'invalid key'}, status=401))
        report = select(batch=failed)
        self.assertEqual(failed.fetch.status, snapshot.FETCH_AUTH_FAILED)
        # External data is optional by default: S2 selection still stands.
        self.assertTrue(report.automatic_allowed)
        self.assertTrue(any('AUTH_FAILED' in item
                            for item in report.degradations))
        for role in roles.ROLES:
            assessment = report.selected_assessment(role)
            self.assertEqual(assessment.status, 'UNLINKED')
            self.assertTrue(all(signal.grade == 'UNKNOWN'
                                for signal in assessment.signals))
        self.assertIn('authentication failed', failed.fetch.message)

    def test_policy_can_require_fresh_external_data(self):
        failed = fetch_batch(response=fixtures.json_response(
            {'error': 'rate limit'}, status=429,
            headers=(('Retry-After', '60'),)))
        policy = model_selection.AssessmentPolicy(require_fresh_source=True)
        report = select(batch=failed, policy=policy)
        self.assertFalse(report.automatic_allowed)
        self.assertTrue(report.decisions)
        self.assertIn('RATE_LIMITED', ' '.join(report.decisions))

    def test_expired_cache_is_stale_and_never_ranked(self):
        store = fixtures.cache(max_age_seconds=60)
        fetch_batch(cache=store, now=fixtures.NOW)
        stale = fetch_batch(
            response=fixtures.json_response({'error': 'rate limit'}, status=429),
            cache=store, now=fixtures.EXPIRED)
        self.assertEqual(stale.fetch.status, snapshot.FETCH_STALE_CACHE)
        report = select(batch=stale)
        for role in roles.ROLES:
            assessment = report.selected_assessment(role)
            self.assertEqual(assessment.status, 'STALE')
            self.assertTrue(all(signal.grade == 'UNKNOWN'
                                for signal in assessment.signals))
        strict = select(
            batch=stale,
            policy=model_selection.AssessmentPolicy(require_fresh_source=True))
        self.assertFalse(strict.automatic_allowed)
        self.assertIn('STALE_CACHE', ' '.join(strict.decisions))

    def test_fresh_cache_is_usable_but_not_live(self):
        store = fixtures.cache(max_age_seconds=24 * 60 * 60)
        fetch_batch(cache=store, now=fixtures.NOW)
        cached = fetch_batch(
            response=fixtures.json_response({'error': 'rate limit'}, status=429),
            cache=store, now=fixtures.LATER)
        report = select(batch=cached)
        self.assertEqual(cached.fetch.status, snapshot.FETCH_CACHED)
        self.assertTrue(cached.fetch.usable)
        self.assertFalse(cached.fetch.live)
        self.assertEqual(report.selected_assessment('execution').status,
                         'MATCHED')
        self.assertTrue(any('not a live' in note for note in report.notes))


class SelectionStopTests(unittest.TestCase):
    def test_no_candidate_stops_automatic_selection(self):
        empty = discovery.discover_candidates(
            adapter=support.FixtureAdapter(
                support.runtime_adapter.DiscoveryResult(
                    outcome='EMPTY', mode='runtime')))
        report = model_selection.select_models(
            fetch_batch(), empty, support.task_requirements(),
            links=default_links(), now=NOW)
        self.assertFalse(report.automatic_allowed)
        for role in roles.ROLES:
            selection = report.role(role)
            self.assertIsNone(selection.selected)
            self.assertTrue(selection.manual_handoff)
        self.assertTrue(any('no qualified' in item
                            for item in report.degradations))

    def test_unlinked_model_is_unknown_without_fuzzy_matching(self):
        report = select(links={})
        for role in roles.ROLES:
            assessment = report.selected_assessment(role)
            self.assertEqual(assessment.status, 'UNLINKED')
            self.assertTrue(all(signal.grade == 'UNKNOWN'
                                for signal in assessment.signals))
        self.assertIn('never inferred from a name',
                      ' '.join(report.selected_assessment(
                          roles.ROLE_EXECUTION).notes))
        # No snapshot's evaluation leaked into the S2 evidence.
        for role in roles.ROLES:
            selected = report.role(role).selected
            self.assertFalse(any(
                item.origin == 'external-reference' and item.grade != 'UNKNOWN'
                for item in selected.candidate.role_evidence))

    def test_policy_can_require_role_specific_evidence(self):
        policy = model_selection.AssessmentPolicy(require_role_evidence=True)
        report = select(policy=policy)
        self.assertFalse(report.automatic_allowed)
        self.assertTrue(any('reviewer' in item for item in report.decisions))

    def test_policy_can_require_a_snapshot_for_the_selected_model(self):
        policy = model_selection.AssessmentPolicy(
            require_snapshot_for_selected=True)
        report = select(links={}, policy=policy)
        self.assertFalse(report.automatic_allowed)
        self.assertTrue(report.decisions)

    def test_text_only_master_requirement_is_refused(self):
        requirements = support.task_requirements()
        requirements[roles.ROLE_MASTER] = roles.RoleRequirement(
            role=roles.ROLE_MASTER, required_modalities=('text',))
        with self.assertRaises(model_selection.ModelSelectionError):
            select(requirements=requirements)

    def test_duplicate_links_are_rejected(self):
        with self.assertRaises(model_selection.ModelSelectionError):
            model_selection.select_models(
                fetch_batch(), make_discovery(support.standard_candidates()),
                support.task_requirements(),
                links=(model_selection.ModelLink('model-a', 'fixture-exec'),
                       model_selection.ModelLink('model-a', 'fixture-weak')),
                now=NOW)

    def test_small_cohort_grade_stays_unknown(self):
        row = scored_row('fixture-exec', 0, coding=95, intelligence=60,
                         agentic=95, ifbench=0.9)
        batch = fetch_batch(response=fixtures.json_response(
            fixtures.payload(rows=[row], tier='pro')))
        report = select(
            batch=batch,
            policy=model_selection.AssessmentPolicy(min_cohort_size=99))
        assessment = report.selected_assessment('execution')
        coding = next(signal for signal in assessment.signals
                      if signal.dimension == 'coding')
        self.assertEqual(coding.grade, 'UNKNOWN')
        self.assertIsNotNone(coding.value)
        self.assertIn('cohort', coding.note)


class LocalEvidenceSelectionTests(unittest.TestCase):
    """ETR / credibility are applied only after the existing hard filters."""

    def execution_filter(self, selection, candidate_id, filter_id):
        entry = next(item for item in selection.excluded
                     if item.candidate.candidate_id == candidate_id)
        return next(item for item in entry.filters
                    if item.filter_id == filter_id)

    def test_high_etr_cannot_offset_a_permission_failure(self):
        master, execution, reviewer = support.standard_candidates()
        denied = replace(execution, permissions=support.permissions(
            read_paths=('src',), write_paths=(), commands=('python',),
            read_only=True))
        report = local_report({'model-a': 18})
        selected = select_with_local(
            (master, denied, reviewer), report=report,
            reviews=corroborated('model-a'))
        execution_selection = selected.role(roles.ROLE_EXECUTION)
        self.assertIsNone(execution_selection.selected)
        permissions = self.execution_filter(
            execution_selection, 'fixture-runtime:exec-1', 'permissions')
        self.assertEqual(permissions.status, 'FAIL')
        self.assertFalse(selected.automatic_allowed)
        evidence = selected.local_evidence_for(
            roles.ROLE_EXECUTION, 'fixture-runtime:exec-1')
        self.assertEqual(evidence.gate_status, 'RAISE_PRIORITY')
        self.assertEqual(evidence.applied_action, 'NOT_APPLICABLE')
        self.assertIn('hard filter', ' '.join(evidence.reasons))

    def test_alias_of_the_execution_model_is_not_an_independent_reviewer(self):
        master, execution, reviewer = support.standard_candidates()
        shared = support.identity(
            'canonical-shared', aliases=('model-a', 'model-b'))
        execution = replace(execution, canonical_identity=shared)
        reviewer = replace(reviewer, canonical_identity=shared)
        report = local_report({'model-a': 18, 'model-b': 18})
        selected = select_with_local(
            (master, execution, reviewer), report=report,
            reviews=corroborated('model-a') + corroborated('model-b'))
        reviewer_selection = selected.role(roles.ROLE_REVIEWER)
        self.assertIsNone(reviewer_selection.selected)
        independence = self.execution_filter(
            reviewer_selection, 'fixture-runtime:review-1', 'independence')
        self.assertEqual(independence.status, 'FAIL')
        self.assertIn('verified canonical identity', independence.detail)
        self.assertFalse(selected.automatic_allowed)
        # Both aliases resolve through the registry to one underlying model,
        # so the report records that canonical identity instead of inventing
        # two independent models.
        reviewer_evidence = selected.local_evidence_for(
            roles.ROLE_REVIEWER, 'fixture-runtime:review-1')
        self.assertEqual(reviewer_evidence.canonical_model_id,
                         'canonical-shared')
        self.assertEqual(reviewer_evidence.identity_status, 'CONFIRMED')

    def test_high_etr_cannot_rescue_an_uncallable_candidate(self):
        master, execution, reviewer = support.standard_candidates()
        blocked = replace(execution, capability_record=support.capability_record(
            overrides={'C1': support.capability('C1', 'FAIL')}))
        report = local_report({'model-a': 18})
        selected = select_with_local(
            (master, blocked, reviewer), report=report,
            reviews=corroborated('model-a'))
        execution_selection = selected.role(roles.ROLE_EXECUTION)
        self.assertIsNone(execution_selection.selected)
        callable_filter = self.execution_filter(
            execution_selection, 'fixture-runtime:exec-1', 'callable')
        self.assertEqual(callable_filter.status, 'FAIL')
        evidence = selected.local_evidence_for(
            roles.ROLE_EXECUTION, 'fixture-runtime:exec-1')
        self.assertEqual(evidence.applied_action, 'NOT_APPLICABLE')
        self.assertFalse(selected.automatic_allowed)

    def test_low_sample_does_not_pause_automatic_selection(self):
        report = local_report({'model-a': 1}, total=5)
        selected = select_with_local(report=report,
                                     reviews=corroborated('model-a'))
        execution = selected.role(roles.ROLE_EXECUTION)
        self.assertIsNotNone(execution.selected)
        evidence = selected.local_evidence_for(
            roles.ROLE_EXECUTION, 'fixture-runtime:exec-1')
        self.assertEqual(evidence.etr_value_status, 'OK')
        self.assertLess(evidence.etr_value, 40.0)
        self.assertEqual(evidence.gate_status, 'PROVISIONAL')
        self.assertEqual(evidence.applied_action, 'PROVISIONAL')
        self.assertEqual(execution.withheld, ())
        self.assertTrue(selected.automatic_allowed)

    def test_below_threshold_with_evidence_pauses_automatic_selection(self):
        report = local_report({'model-a': 6})
        selected = select_with_local(report=report,
                                     reviews=corroborated('model-a'))
        execution = selected.role(roles.ROLE_EXECUTION)
        self.assertIsNone(execution.selected)
        self.assertEqual(len(execution.withheld), 1)
        self.assertEqual(execution.withheld[0].candidate.candidate_id,
                         'fixture-runtime:exec-1')
        evidence = selected.local_evidence_for(
            roles.ROLE_EXECUTION, 'fixture-runtime:exec-1')
        self.assertEqual(evidence.gate_status, 'PAUSE_AUTO_SELECTION')
        self.assertEqual(evidence.applied_action, 'PAUSE_AUTO_SELECTION')
        self.assertFalse(selected.automatic_allowed)
        self.assertTrue(execution.manual_handoff)
        self.assertFalse(selected.executed)

    def test_low_etr_with_low_credibility_is_provisional_not_paused(self):
        report = local_report({'model-a': 6})
        selected = select_with_local(report=report,
                                     reviews=single_review('model-a'))
        execution = selected.role(roles.ROLE_EXECUTION)
        self.assertIsNotNone(execution.selected)
        evidence = selected.local_evidence_for(
            roles.ROLE_EXECUTION, 'fixture-runtime:exec-1')
        self.assertEqual(evidence.credibility, 'LOW')
        self.assertEqual(evidence.gate_status, 'PAUSE_AUTO_SELECTION')
        self.assertEqual(evidence.applied_action, 'PROVISIONAL')
        self.assertIn('credibility', ' '.join(evidence.reasons))

    def test_above_high_line_raises_priority_over_cheaper_candidate(self):
        master, execution, reviewer = support.standard_candidates()
        cheap = replace(execution, agent_id='exec-2', model_id='model-c',
                        model_family='family-c', cost_per_mtok=0.1)
        expensive = replace(execution, cost_per_mtok=5.0)
        report = local_report({'model-a': 18})
        selected = select_with_local(
            (master, expensive, cheap, reviewer), report=report,
            reviews=corroborated('model-a'))
        self.assertEqual(
            selected.role(roles.ROLE_EXECUTION).selected.candidate.model_id,
            'model-a')
        evidence = selected.local_evidence_for(
            roles.ROLE_EXECUTION, 'fixture-runtime:exec-1')
        self.assertEqual(evidence.applied_action, 'RAISE_PRIORITY')

    def test_no_candidate_is_never_reported_as_dispatched(self):
        empty = discovery.discover_candidates(
            adapter=support.FixtureAdapter(
                support.runtime_adapter.DiscoveryResult(
                    outcome='EMPTY', mode='runtime')))
        report = model_selection.select_models(
            fetch_batch(), empty, support.task_requirements(),
            links=default_links(), now=NOW,
            feedback_report=local_report({'model-a': 18}),
            task_category=LOCAL_CATEGORY, scope_id=LOCAL_SCOPE,
            peer_reviews=corroborated('model-a'))
        self.assertEqual(report.local_evidence, ())
        self.assertFalse(report.automatic_allowed)
        self.assertFalse(report.executed)
        self.assertFalse(hasattr(report, 'dispatched'))
        for role in roles.ROLES:
            selection = report.role(role)
            self.assertIsNone(selection.selected)
            self.assertTrue(selection.manual_handoff)
            self.assertEqual(selection.withheld, ())


if __name__ == '__main__':
    unittest.main()
