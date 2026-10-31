"""Project-internal role feedback and calibration tests (#123, S4).

Every fixture is synthetic and every store lives in a temporary project root:
no real ``.local`` directory, no personal terminal data, no network.

The tests pin the negatives the design requires (§9, §12.2):

* missing data stays ``UNKNOWN`` with a coverage figure, never ``0``;
* a partial failure is visible in the pooled rate, not averaged away;
* a new model id / runtime version does not inherit the old evaluation;
* a handful of samples produces no ranking;
* a Reviewer's missed-issue count needs an independent comparator;
* another scope's records are refused and never merged;
* a failed write is reported as "not written";
* chat memory, a self-assessment, a merge event or a green CI run alone is not
  evidence.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent_selection import feedback as fb  # noqa: E402
from scripts.agent_selection import local_store  # noqa: E402

NOW = '2026-10-27T12:00:00Z'
RECENT = '2026-10-20T00:00:00Z'
OLDER = '2026-10-10T00:00:00Z'
STALE = '2025-01-01T00:00:00Z'
SCOPE_A = 'a' * 32
SCOPE_B = 'b' * 32

EXECUTION_ID = fb.ModelIdentity('model-a', 'runtime-x', '1.0.0')
REVIEWER_ID = fb.ModelIdentity('model-b', 'runtime-x', '1.0.0')
MASTER_ID = fb.ModelIdentity('model-m', 'runtime-x', '1.0.0')


def task_evidence(ref='issue:1', at=RECENT):
    return fb.EvidenceRef('task_record', ref, at)


def review_evidence(ref='pr:1#c1', at=RECENT):
    return fb.EvidenceRef('review_conclusion', ref, at)


def usage(total=1000, *, input_tokens=None, output_tokens=None,
          cached_tokens=None, reasoning_tokens=None,
          source='runtime:fixture', complete=True):
    """One source-reported Token usage value; never an estimate."""
    return fb.TokenUsage(
        total_tokens=total, input_tokens=input_tokens,
        output_tokens=output_tokens, cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens, source=source, complete=complete)


def execution(index=1, **overrides):
    values = dict(
        task_id=f'issue:{index}', category='code-change', identity=EXECUTION_ID,
        observed_at=RECENT, evidence=(task_evidence(f'issue:{index}'),),
        first_acceptance='PASS', rework_rounds=0,
        post_merge_regression_defects=0, duration_seconds=None, cost=None,
        token_usage=None, contribution='UNRATED', agent_alias=fb.UNKNOWN)
    values.update(overrides)
    return fb.ExecutionOutcome(**values)


def reviewer(index=1, **overrides):
    values = dict(
        review_id=f'pr:{index}', category='code-change', identity=REVIEWER_ID,
        observed_at=RECENT, evidence=(review_evidence(f'pr:{index}#c1'),),
        confirmed_issues=1, false_positives=0, severity='MEDIUM',
        missed_issues=None, baseline=None, blocked_regression=None)
    values.update(overrides)
    return fb.ReviewerOutcome(**values)


def master(index=1, **overrides):
    values = dict(
        cycle_id=f'cycle:{index}', category='planning', identity=MASTER_ID,
        observed_at=RECENT,
        signals=(('planning_omissions', 1,
                  fb.EvidenceRef('master_review', f'issue:{index}', RECENT)),),
        evidence=(fb.EvidenceRef('master_review', f'issue:{index}', RECENT),))
    values.update(overrides)
    return fb.MasterOutcome(**values)


class StoreFixture(unittest.TestCase):
    """A temporary project root plus one initialized scope."""

    scope_id = SCOPE_A

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = self.make_store(self.scope_id)

    def make_store(self, scope_id):
        store = local_store.LocalStore(self.root, scope_id, history_allowed=True)
        store.initialize(now=RECENT)
        return store

    def record(self, outcome, *, record_id=None, store=None):
        return fb.record_outcome(store or self.store, outcome,
                                 now=RECENT, record_id=record_id)

    def document(self, store=None):
        path = (store or self.store).directory / fb.FEEDBACK_DOCUMENT
        return path.read_text(encoding='utf-8')

    def report(self, store=None, **kwargs):
        kwargs.setdefault('now', NOW)
        return fb.build_report(store or self.store, **kwargs)


class ExecutionFeedbackTests(StoreFixture):
    def test_three_passing_tasks_yield_a_rate_with_sample_window_and_coverage(self):
        for index in range(1, 4):
            self.record(execution(index, rework_rounds=index - 1))
        report = self.report()
        cohort = report.cohort('execution', 'code-change')
        self.assertIsNotNone(cohort)
        metric = cohort.metric('first_acceptance_pass_rate')
        self.assertEqual(metric.status, 'OK')
        self.assertEqual(metric.value, 1.0)
        self.assertEqual(metric.sample_size, 3)
        self.assertEqual(metric.missing, 0)
        self.assertEqual(metric.coverage, 1.0)
        self.assertEqual(metric.role, 'execution')
        self.assertEqual(metric.category, 'code-change')
        self.assertEqual(metric.window_start, '2026-07-29T12:00:00Z')
        self.assertEqual(metric.window_end, NOW)

    def test_a_partial_failure_is_visible_in_the_pooled_rate(self):
        self.record(execution(1, first_acceptance='PASS', rework_rounds=0))
        self.record(execution(2, first_acceptance='FAIL', rework_rounds=2))
        self.record(execution(3, first_acceptance='PASS', rework_rounds=1))
        metric = self.report().cohort('execution', 'code-change').metric(
            'first_acceptance_pass_rate')
        self.assertEqual(metric.status, 'OK')
        self.assertEqual(metric.value, 0.667)
        self.assertEqual(metric.sample_size, 3)

    def test_missing_values_are_unknown_with_coverage_not_zero(self):
        self.record(execution(1, duration_seconds=600))
        self.record(execution(2, duration_seconds=None))
        self.record(execution(3, duration_seconds=None))
        metric = self.report().cohort('execution', 'code-change').metric(
            'mean_delivery_seconds')
        self.assertEqual(metric.status, 'INSUFFICIENT_SAMPLE')
        self.assertEqual(metric.value, fb.UNKNOWN)
        self.assertEqual(metric.sample_size, 1)
        self.assertEqual(metric.missing, 2)
        self.assertEqual(metric.coverage, 0.333)

    def test_recorded_duration_and_regression_defects_are_used_when_present(self):
        self.record(execution(1, duration_seconds=600,
                              post_merge_regression_defects=0))
        self.record(execution(2, duration_seconds=1200,
                              post_merge_regression_defects=1))
        self.record(execution(3, duration_seconds=900,
                              post_merge_regression_defects=0))
        cohort = self.report().cohort('execution', 'code-change')
        duration = cohort.metric('mean_delivery_seconds')
        self.assertEqual(duration.status, 'OK')
        self.assertEqual(duration.value, 900.0)
        self.assertEqual(duration.unit, 'seconds/task')
        defects = cohort.metric('post_merge_regression_defect_rate')
        self.assertEqual(defects.status, 'OK')
        self.assertEqual(defects.value, 0.333)

    def test_cost_is_only_reported_with_its_currency_and_unit(self):
        for index in range(1, 4):
            self.record(execution(index, cost=fb.Cost(1.5, 'usd', 'per_task')))
        metric = self.report().cohort('execution', 'code-change').metric(
            'mean_cost')
        self.assertEqual(metric.status, 'OK')
        self.assertEqual(metric.value, 1.5)
        self.assertEqual(metric.unit, 'USD/per_task')

    def test_mixed_price_units_are_not_averaged(self):
        self.record(execution(1, cost=fb.Cost(1.5, 'USD', 'per_task')))
        self.record(execution(2, cost=fb.Cost(2.0, 'EUR', 'per_task')))
        self.record(execution(3, cost=fb.Cost(3.0, 'USD', 'per_mtok')))
        metric = self.report().cohort('execution', 'code-change').metric(
            'mean_cost')
        self.assertEqual(metric.status, 'NOT_COMPARABLE')
        self.assertEqual(metric.value, fb.UNKNOWN)
        self.assertIn('never averaged', metric.note)

    def test_different_task_categories_are_never_added_together(self):
        self.record(execution(1, category='code-change'))
        self.record(execution(2, category='code-change'))
        self.record(execution(3, category='docs-only', first_acceptance='FAIL'))
        report = self.report()
        code = report.cohort('execution', 'code-change')
        docs = report.cohort('execution', 'docs-only')
        self.assertEqual(code.metric('first_acceptance_pass_rate').sample_size, 2)
        self.assertEqual(docs.metric('first_acceptance_pass_rate').status,
                         'INSUFFICIENT_SAMPLE')


class ReviewerFeedbackTests(StoreFixture):
    def test_confirmed_and_false_positive_counts_are_pooled(self):
        self.record(reviewer(1, confirmed_issues=2, false_positives=1))
        self.record(reviewer(2, confirmed_issues=1, false_positives=0))
        self.record(reviewer(3, confirmed_issues=3, false_positives=1))
        cohort = self.report().cohort('reviewer', 'code-change')
        confirmed = cohort.metric('confirmed_issue_rate')
        false_positive = cohort.metric('false_positive_rate')
        self.assertEqual(confirmed.status, 'OK')
        self.assertEqual(confirmed.value, 0.806)
        self.assertEqual(confirmed.sample_size, 3)
        self.assertEqual(false_positive.status, 'OK')
        self.assertEqual(false_positive.value, 0.194)
        self.assertIn('pooled', confirmed.note)

    def test_a_review_without_counts_counts_as_missing(self):
        self.record(reviewer(1, confirmed_issues=2, false_positives=0))
        self.record(reviewer(2, confirmed_issues=None, false_positives=None))
        self.record(reviewer(3, confirmed_issues=1, false_positives=1))
        metric = self.report().cohort('reviewer', 'code-change').metric(
            'confirmed_issue_rate')
        self.assertEqual(metric.status, 'INSUFFICIENT_SAMPLE')
        self.assertEqual(metric.missing, 1)
        self.assertEqual(metric.coverage, 0.667)

    def test_missed_issues_need_an_independent_comparator(self):
        for index in range(1, 4):
            self.record(reviewer(index, missed_issues=None, baseline=None))
        metric = self.report().cohort('reviewer', 'code-change').metric(
            'mean_missed_issues_per_review')
        self.assertEqual(metric.status, 'NO_COMPARATOR')
        self.assertEqual(metric.value, fb.UNKNOWN)
        self.assertEqual(metric.sample_size, 0)
        self.assertEqual(metric.coverage, 0.0)
        self.assertIn('not computed', metric.note)

    def test_missed_issues_are_reported_once_a_comparator_exists(self):
        baseline = fb.EvidenceRef('comparison_baseline', 'baseline:run-1', RECENT)
        for index, missed in enumerate((1, 0, 2), start=1):
            self.record(reviewer(index, missed_issues=missed, baseline=baseline))
        metric = self.report().cohort('reviewer', 'code-change').metric(
            'mean_missed_issues_per_review')
        self.assertEqual(metric.status, 'OK')
        self.assertEqual(metric.value, 1.0)
        self.assertEqual(metric.sample_size, 3)
        self.assertIn('comparison baseline', metric.note)

    def test_missed_issues_without_a_baseline_is_rejected_at_construction(self):
        with self.assertRaises(fb.FeedbackError):
            reviewer(1, missed_issues=2)

    def test_severity_distribution_and_high_severity_share(self):
        self.record(reviewer(1, severity='HIGH', confirmed_issues=2,
                             false_positives=0))
        self.record(reviewer(2, severity='LOW', confirmed_issues=1,
                             false_positives=0))
        self.record(reviewer(3, severity='HIGH', confirmed_issues=1,
                             false_positives=0))
        cohort = self.report().cohort('reviewer', 'code-change')
        self.assertIn(('HIGH', 2), cohort.severity_counts)
        self.assertIn(('LOW', 1), cohort.severity_counts)
        share = cohort.metric('high_severity_share')
        self.assertEqual(share.status, 'OK')
        self.assertEqual(share.value, 0.667)

    def test_a_merge_or_a_green_ci_run_is_not_review_quality(self):
        for index in range(1, 4):
            self.record(reviewer(index, confirmed_issues=0,
                                 false_positives=0, severity='NONE'))
        metric = self.report().cohort('reviewer', 'code-change').metric(
            'confirmed_issue_rate')
        # A zero denominator is missing data, not a 100% or 0% confirmation.
        self.assertEqual(metric.status, 'INSUFFICIENT_SAMPLE')
        self.assertEqual(metric.sample_size, 0)
        self.assertEqual(metric.missing, 3)


class MasterFeedbackTests(StoreFixture):
    def test_recorded_signals_need_their_own_evidence(self):
        signal = ('planning_omissions', 2,
                  fb.EvidenceRef('master_review', 'issue:5', RECENT))
        self.record(master(1, signals=(signal,)))
        report = self.report()
        cohort = report.cohort('master', 'planning')
        self.assertIsNotNone(cohort)
        metric = cohort.metric('mean_planning_omissions')
        self.assertEqual(metric.status, 'INSUFFICIENT_SAMPLE')

    def test_three_cycles_make_a_signal_readable(self):
        for index, omissions in enumerate((1, 2, 0), start=1):
            signal = ('planning_omissions', omissions,
                      fb.EvidenceRef('master_review', f'issue:{index}', RECENT))
            corrections = ('decision_corrections', 0,
                           fb.EvidenceRef('master_review', f'issue:{index}', RECENT))
            self.record(master(index, signals=(signal, corrections)))
        metric = self.report().cohort('master', 'planning').metric(
            'mean_planning_omissions')
        self.assertEqual(metric.status, 'OK')
        self.assertEqual(metric.value, 1.0)
        self.assertIn('anecdote', metric.note)

    def test_one_cycle_never_becomes_a_stable_quality_claim(self):
        self.record(master(1))
        report = self.report()
        metric = report.cohort('master', 'planning').metric(
            'mean_planning_omissions')
        self.assertEqual(metric.status, 'INSUFFICIENT_SAMPLE')
        self.assertEqual(metric.value, fb.UNKNOWN)
        statements = ' '.join(item.statement for item in report.recommendations)
        self.assertIn('no stable quality', statements)

    def test_a_signal_without_evidence_is_rejected(self):
        with self.assertRaises(fb.FeedbackError):
            master(1, signals=(('planning_omissions', 1, None),))

    def test_an_unknown_signal_name_is_rejected(self):
        with self.assertRaises(fb.FeedbackError):
            master(1, signals=(('make_it_faster', 1,
                                fb.EvidenceRef('master_review', 'issue:1',
                                               RECENT)),))


class InsufficientEvidenceTests(StoreFixture):
    def test_chat_memory_alone_is_not_evidence(self):
        with self.assertRaises(fb.FeedbackEvidenceError):
            execution(1, evidence=(fb.EvidenceRef('chat_memory', 'chat:1',
                                                  RECENT),))
        self.assertEqual(fb.load_outcomes(self.store).outcomes, ())

    def test_a_merge_and_a_green_ci_run_are_not_evidence(self):
        with self.assertRaises(fb.FeedbackEvidenceError):
            execution(1, evidence=(
                fb.EvidenceRef('merge_record', 'pr:1', RECENT),
                fb.EvidenceRef('ci_result', 'run:1', RECENT)))

    def test_a_self_assessment_cannot_be_the_whole_basis(self):
        with self.assertRaises(fb.FeedbackEvidenceError):
            reviewer(1, evidence=(
                fb.EvidenceRef('self_assessment', 'self:1', RECENT),))

    def test_insufficient_evidence_may_accompany_a_task_record(self):
        outcome = execution(1, evidence=(
            task_evidence('issue:1'),
            fb.EvidenceRef('merge_record', 'pr:1', RECENT)))
        record = self.record(outcome)
        self.assertEqual(record.source_class, 'OBSERVED')
        self.assertEqual(record.status, 'RECORDED')

    def test_an_unknown_evidence_kind_is_rejected(self):
        with self.assertRaises(fb.FeedbackError):
            fb.EvidenceRef('vibes', 'x', RECENT)

    def test_an_evidence_locator_cannot_smuggle_a_separator(self):
        with self.assertRaises(fb.FeedbackError):
            fb.EvidenceRef('task_record', 'issue:1|extra', RECENT)


class IdentityAndVersionTests(StoreFixture):
    def test_a_runtime_version_change_does_not_inherit_the_old_evaluation(self):
        for index in range(1, 4):
            self.record(execution(index))
        upgraded = fb.ModelIdentity('model-a', 'runtime-x', '2.0.0')
        report = self.report(
            current_identities={'execution': upgraded})
        cohorts = report.cohorts_for('execution')
        self.assertEqual(len(cohorts), 1)
        metric = cohorts[0].metric('first_acceptance_pass_rate')
        self.assertEqual(metric.status, 'IDENTITY_CHANGED')
        self.assertEqual(metric.value, fb.UNKNOWN)
        self.assertIn('no evaluation is carried forward', metric.note)
        self.assertFalse(report.compare(
            'execution', 'code-change', 'first_acceptance_pass_rate').allowed)

    def test_two_runtime_versions_are_two_cohorts_never_one_sample(self):
        for index in range(1, 4):
            self.record(execution(index))
        for index in range(4, 7):
            self.record(execution(
                index, identity=fb.ModelIdentity('model-a', 'runtime-x', '2.0.0')))
        report = self.report()
        cohorts = report.cohorts_for('execution')
        self.assertEqual(len(cohorts), 2)
        self.assertEqual(
            sorted(cohort.identity.runtime_version for cohort in cohorts),
            ['1.0.0', '2.0.0'])

    def test_an_unknown_model_identity_cannot_be_attributed(self):
        unknown = fb.ModelIdentity()
        for index in range(1, 4):
            self.record(execution(index, identity=unknown))
        metric = self.report().cohort('execution', 'code-change').metric(
            'first_acceptance_pass_rate')
        self.assertEqual(metric.status, 'IDENTITY_CHANGED')
        self.assertIn('UNKNOWN', metric.note)


class SampleSizeTests(StoreFixture):
    def test_two_samples_produce_no_value_and_no_ranking(self):
        for index in range(1, 3):
            self.record(execution(index))
        report = self.report()
        metric = report.cohort('execution', 'code-change').metric(
            'first_acceptance_pass_rate')
        self.assertEqual(metric.status, 'INSUFFICIENT_SAMPLE')
        self.assertEqual(metric.value, fb.UNKNOWN)
        self.assertEqual(metric.sample_size, 2)
        comparison = report.compare(
            'execution', 'code-change', 'first_acceptance_pass_rate')
        self.assertFalse(comparison.allowed)
        self.assertEqual(comparison.rows, ())
        self.assertTrue(any('fewer than two' in reason
                            for reason in comparison.blocked_reasons))

    def test_policy_can_demand_a_larger_sample(self):
        for index in range(1, 4):
            self.record(execution(index))
        report = self.report(policy=fb.FeedbackPolicy(min_samples=5))
        metric = report.cohort('execution', 'code-change').metric(
            'first_acceptance_pass_rate')
        self.assertEqual(metric.status, 'INSUFFICIENT_SAMPLE')

    def test_a_ranking_requires_two_well_sampled_identities(self):
        other = fb.ModelIdentity('model-z', 'runtime-x', '1.0.0')
        for index in range(1, 4):
            self.record(execution(index, first_acceptance='PASS'))
        for index in range(4, 7):
            self.record(execution(index, identity=other,
                                  first_acceptance='FAIL'))
        comparison = self.report().compare(
            'execution', 'code-change', 'first_acceptance_pass_rate')
        self.assertTrue(comparison.allowed)
        self.assertEqual(comparison.rows[0].identity.model_id, 'model-a')
        self.assertEqual(comparison.rows[0].value, 1.0)
        self.assertEqual(comparison.rows[1].value, 0.0)
        self.assertIn('advisory only', comparison.note)

    def test_ranking_can_be_disabled_by_policy(self):
        other = fb.ModelIdentity('model-z', 'runtime-x', '1.0.0')
        for index in range(1, 4):
            self.record(execution(index))
            self.record(execution(index + 10, identity=other))
        report = self.report(policy=fb.FeedbackPolicy(allow_ranking=False))
        comparison = report.compare(
            'execution', 'code-change', 'first_acceptance_pass_rate')
        self.assertFalse(comparison.allowed)
        self.assertIn('ranking is disabled by policy', comparison.blocked_reasons)


class FreshnessTests(StoreFixture):
    def test_an_old_record_is_stale_and_not_current_evidence(self):
        self.record(execution(1, observed_at=STALE))
        report = self.report()
        self.assertEqual(report.stale_entries, 1)
        metric = report.cohort('execution', 'code-change').metric(
            'first_acceptance_pass_rate')
        self.assertEqual(metric.status, 'STALE')
        self.assertEqual(metric.value, fb.UNKNOWN)
        self.assertTrue(any('older than' in item for item in report.degradations))

    def test_stale_records_are_excluded_from_a_mixed_cohort(self):
        for index in range(1, 4):
            self.record(execution(index, observed_at=RECENT))
        self.record(execution(9, observed_at=STALE, first_acceptance='FAIL'))
        cohort = self.report().cohort('execution', 'code-change')
        self.assertEqual(cohort.stale_entries, 1)
        metric = cohort.metric('first_acceptance_pass_rate')
        self.assertEqual(metric.status, 'OK')
        self.assertEqual(metric.value, 1.0)
        self.assertEqual(metric.sample_size, 3)

    def test_an_empty_scope_reports_no_data_with_zero_coverage(self):
        report = self.report()
        self.assertEqual(report.cohorts, ())
        self.assertEqual(report.recommendations, ())
        self.assertTrue(any(item.startswith('no data')
                            for item in report.degradations))
        self.assertEqual(report.applied_automatically, False)


class ScopeIsolationTests(StoreFixture):
    def test_another_scope_cannot_be_written_into_this_one(self):
        other = self.make_store(SCOPE_B)
        with self.assertRaises(fb.FeedbackScopeError):
            self.record(execution(1, scope_id=other.scope_id), store=self.store)
        self.assertEqual(fb.load_outcomes(self.store).outcomes, ())

    def test_a_report_never_reads_another_scope(self):
        self.record(execution(1))
        other = self.make_store(SCOPE_B)
        report = self.report(store=other)
        self.assertEqual(report.scope_id, SCOPE_B)
        self.assertEqual(report.cohorts, ())
        self.assertEqual(fb.load_outcomes(other).outcomes, ())

    def test_synthetic_outcomes_from_another_scope_are_refused(self):
        other = self.make_store(SCOPE_B)
        with self.assertRaises(fb.FeedbackScopeError):
            self.report(store=self.store,
                        outcomes=(execution(1, scope_id=other.scope_id),))

    def test_the_scope_only_holds_markdown_documents(self):
        self.record(execution(1))
        names = sorted(path.name for path in self.store.directory.iterdir())
        self.assertEqual(names, sorted(local_store.DOCUMENTS))
        for path in self.store.directory.iterdir():
            self.assertTrue(path.name.endswith('.md'), path)
        self.assertFalse(
            (self.store.directory / 'observed-performance.json').exists())


class WriteFailureTests(StoreFixture):
    def test_a_failed_write_is_reported_and_nothing_is_claimed(self):
        with mock.patch.object(self.store, 'append_record',
                               side_effect=local_store.StoreWriteError('disk full')):
            with self.assertRaises(fb.FeedbackWriteError) as caught:
                self.record(execution(1))
        self.assertIn('was not written', str(caught.exception))
        self.assertNotIn('first_acceptance', self.document())
        self.assertEqual(fb.load_outcomes(self.store).outcomes, ())

    def test_a_conflict_is_also_a_write_failure(self):
        with mock.patch.object(
                self.store, 'append_record',
                side_effect=local_store.StoreConflictError('locked')):
            with self.assertRaises(fb.FeedbackWriteError):
                self.record(execution(1))

    def test_a_history_disabled_store_refuses_to_record(self):
        store = local_store.LocalStore(self.root, SCOPE_A, history_allowed=False)
        with self.assertRaises(fb.FeedbackWriteError):
            self.record(execution(1), store=store)


class MigrationAndDiagnosticsTests(StoreFixture):
    def test_an_older_schema_is_a_read_only_diagnostic(self):
        outcome = execution(1)
        details = dict(outcome.as_details())
        details['schema'] = 's4-feedback-0'
        self.store.append_record(
            fb.FEEDBACK_DOCUMENT, summary='legacy entry', source_class='OBSERVED',
            status='RECORDED', details=details, record_id='legacy',
            now=RECENT)
        loaded = fb.load_outcomes(self.store)
        self.assertEqual(loaded.outcomes, ())
        self.assertEqual(loaded.ignored, 1)
        self.assertIn('migration decision', ' '.join(loaded.diagnostics))
        report = self.report()
        self.assertEqual(report.cohorts, ())
        self.assertTrue(report.diagnostics)

    def test_a_foreign_record_without_a_schema_is_left_untouched(self):
        self.store.append_record(
            fb.FEEDBACK_DOCUMENT, summary='hand written note',
            source_class='OBSERVED', status='RECORDED',
            details={'note': 'something else'}, record_id='foreign', now=RECENT)
        loaded = fb.load_outcomes(self.store)
        self.assertEqual(loaded.outcomes, ())
        self.assertIn('no schema marker', ' '.join(loaded.diagnostics))
        self.assertIn('hand written note', self.document())

    def test_an_inferred_record_is_excluded_from_calibration(self):
        outcome = execution(1)
        self.store.append_record(
            fb.FEEDBACK_DOCUMENT, summary='inferred', source_class='INFERRED',
            status='RECORDED', details=outcome.as_details(), record_id='inferred',
            now=RECENT)
        loaded = fb.load_outcomes(self.store)
        self.assertEqual(loaded.outcomes, ())
        self.assertIn('not OBSERVED', ' '.join(loaded.diagnostics))

    def test_a_missing_document_is_not_a_pass(self):
        store = local_store.LocalStore(self.root, 'c' * 32, history_allowed=True)
        loaded = fb.load_outcomes(store)
        self.assertEqual(loaded.outcomes, ())
        self.assertIn('not a PASS', ' '.join(loaded.diagnostics))


class ValidationTests(StoreFixture):
    def test_a_task_category_must_be_a_lowercase_slug(self):
        with self.assertRaises(fb.FeedbackError):
            execution(1, category='Code Change')

    def test_negative_counts_are_rejected(self):
        with self.assertRaises(fb.FeedbackError):
            execution(1, rework_rounds=-1)

    def test_an_unknown_severity_is_rejected(self):
        with self.assertRaises(fb.FeedbackError):
            reviewer(1, severity='SEVERE')

    def test_an_unknown_first_acceptance_value_is_rejected(self):
        with self.assertRaises(fb.FeedbackError):
            execution(1, first_acceptance='MAYBE')

    def test_a_negative_cost_is_rejected(self):
        with self.assertRaises(fb.FeedbackError):
            fb.Cost(-1.0)

    def test_a_cost_currency_is_normalized(self):
        self.assertEqual(fb.Cost(1.0, 'usd').currency, 'USD')

    def test_an_unknown_outcome_type_is_refused(self):
        with self.assertRaises(fb.FeedbackError):
            fb.record_outcome(self.store, object())

    def test_a_bad_timestamp_is_rejected(self):
        with self.assertRaises(fb.FeedbackError):
            execution(1, observed_at='yesterday')


class ReportSurfaceTests(StoreFixture):
    def test_the_report_is_structurally_advisory(self):
        for index in range(1, 4):
            self.record(execution(index))
        report = self.report()
        payload = report.as_dict()
        self.assertEqual(payload['scope_id'], SCOPE_A)
        self.assertFalse(payload['applied_automatically'])
        self.assertFalse(payload['changes_configuration'])
        self.assertIn('window', payload)
        self.assertIn('policy', payload)
        self.assertTrue(all(item['advisory'] for item in payload['recommendations']))
        self.assertTrue(all(not item['applied']
                            for item in payload['recommendations']))
        self.assertNotIn('best',
                         ' '.join(item.statement
                                  for item in report.recommendations).lower())
        rendered = '\n'.join(report.render_lines())
        self.assertIn('applied_automatically=False', rendered)
        self.assertIn('coverage=', rendered)

    def test_a_metric_always_carries_sample_window_and_coverage(self):
        for index in range(1, 4):
            self.record(execution(index))
        payload = self.report().as_dict()
        metric = payload['cohorts'][0]['metrics']['first_acceptance_pass_rate']
        for key in ('sample_size', 'missing', 'coverage', 'window_start',
                    'window_end', 'category', 'role', 'unit', 'status'):
            self.assertIn(key, metric)

    def test_no_recommendation_changes_configuration(self):
        for index in range(1, 4):
            self.record(execution(index))
        for recommendation in self.report().recommendations:
            self.assertTrue(recommendation.advisory)
            self.assertFalse(recommendation.applied)

    def test_records_and_reports_stay_inside_the_scope_directory(self):
        self.record(execution(1))
        written = list(self.root.rglob('*'))
        for path in written:
            if path.is_file():
                self.assertIn(self.store.scope_id, str(path))
        self.assertNotIn(str(self.root), self.document())


class EtrCalculationTests(StoreFixture):
    """Fixed synthetic Token data: every number is recomputable by hand.

    The expectations mirror design §7.2: EFFECTIVE=1.0, PARTIAL=0.5,
    INEFFECTIVE=0.0, UNRATED is not scored, telemetry missing is ``UNKNOWN``,
    a zero denominator is ``NO_DATA`` and sub-fields never inflate the total.
    """

    def fill(self, count, contribution, *, total=1000, category='code-change'):
        for index in range(1, count + 1):
            self.record(execution(
                index, category=category, token_usage=usage(total),
                contribution=contribution))

    def etr(self, report=None):
        return (report or self.report()).cohort(
            'execution', 'code-change').etr

    def test_all_effective_is_100_percent(self):
        for index in range(1, 4):
            self.record(execution(index, token_usage=usage(1000),
                                  contribution='EFFECTIVE'))
        etr = self.etr()
        self.assertEqual(etr.value_status, 'OK')
        self.assertEqual(etr.value, 100.0)
        self.assertEqual(etr.token_coverage, 100.0)
        self.assertEqual(etr.observed_tokens, 3000)
        self.assertEqual(etr.scored_tokens, 3000)
        self.assertEqual(etr.rated_tasks, 3)
        self.assertEqual(etr.unrated_tasks, 0)
        self.assertEqual(etr.unit, 'percent')

    def test_a_partial_contribution_is_weighted_half(self):
        self.record(execution(1, token_usage=usage(1000),
                              contribution='EFFECTIVE'))
        self.record(execution(2, token_usage=usage(1000),
                              contribution='PARTIAL'))
        etr = self.etr()
        self.assertEqual(etr.value_status, 'OK')
        self.assertEqual(etr.value, 75.0)
        self.assertEqual(etr.token_coverage, 100.0)

    def test_all_ineffective_is_a_measured_zero_not_missing_data(self):
        for index in range(1, 3):
            self.record(execution(index, token_usage=usage(1000),
                                  contribution='INEFFECTIVE'))
        etr = self.etr()
        self.assertEqual(etr.value_status, 'OK')
        self.assertEqual(etr.value, 0.0)
        self.assertIsNotNone(etr.value)
        self.assertEqual(etr.token_coverage_status, 'OK')

    def test_unrated_tokens_are_outside_the_etr_fraction_but_inside_coverage(self):
        self.fill(2, 'EFFECTIVE')
        self.fill(2, 'UNRATED')
        etr = self.etr()
        self.assertEqual(etr.value_status, 'OK')
        self.assertEqual(etr.value, 100.0)
        self.assertEqual(etr.scored_tokens, 2000)
        self.assertEqual(etr.observed_tokens, 4000)
        self.assertEqual(etr.token_coverage, 50.0)
        self.assertEqual(etr.rated_tasks, 2)
        self.assertEqual(etr.unrated_tasks, 2)

    def test_only_unrated_calls_have_no_etr_but_a_real_zero_coverage(self):
        # T3: observed tokens > 0, scored tokens = 0.
        for index, total in enumerate((1200, 800, 0, 2000), start=1):
            self.record(execution(index, token_usage=usage(total),
                                  contribution='UNRATED'))
        etr = self.etr()
        self.assertEqual(etr.value_status, 'NO_DATA')
        self.assertIsNone(etr.value)
        self.assertEqual(etr.observed_tokens, 4000)
        self.assertEqual(etr.scored_tokens, 0)
        self.assertEqual(etr.token_coverage_status, 'OK')
        self.assertEqual(etr.token_coverage, 0.0)
        self.assertEqual(etr.gate_status, 'NO_GATE')

    def test_zero_observed_tokens_has_no_coverage_or_etr(self):
        # T2: telemetry exists, every call reported 0 Token.
        for index in range(1, 4):
            self.record(execution(index, token_usage=usage(0),
                                  contribution='EFFECTIVE'))
        etr = self.etr()
        self.assertEqual(etr.value_status, 'NO_DATA')
        self.assertIsNone(etr.value)
        self.assertEqual(etr.token_coverage_status, 'NO_DATA')
        self.assertIsNone(etr.token_coverage)
        self.assertEqual(etr.observed_tokens, 0)
        self.assertEqual(etr.gate_status, 'NO_GATE')

    def test_missing_total_cannot_be_summed_from_sub_fields(self):
        # T1: a partial source report is not a total, and no estimate is made.
        self.record(execution(
            1, token_usage=fb.TokenUsage(input_tokens=100, output_tokens=50,
                                         source='runtime:fixture', complete=True),
            contribution='EFFECTIVE'))
        etr = self.etr()
        self.assertEqual(etr.value_status, 'UNKNOWN')
        self.assertIsNone(etr.value)
        self.assertEqual(etr.token_coverage_status, 'UNKNOWN')
        self.assertIsNone(etr.token_coverage)
        self.assertIsNone(etr.observed_tokens)
        self.assertEqual(etr.gate_status, 'NO_GATE')

    def test_cached_and_reasoning_tokens_never_inflate_the_total(self):
        usage_value = usage(1000, input_tokens=800, output_tokens=200,
                            cached_tokens=400, reasoning_tokens=200)
        self.record(execution(1, token_usage=usage_value,
                              contribution='EFFECTIVE'))
        etr = self.etr()
        self.assertEqual(etr.observed_tokens, 1000)
        self.assertNotEqual(etr.observed_tokens, 800 + 200 + 400 + 200)
        self.assertEqual(etr.value, 100.0)
        self.assertEqual(etr.breakdown.input_tokens, 800)
        self.assertEqual(etr.breakdown.output_tokens, 200)
        self.assertEqual(etr.breakdown.cached_tokens, 400)
        self.assertEqual(etr.breakdown.reasoning_tokens, 200)
        self.assertEqual(etr.breakdown.input_reported, 1)
        self.assertIn('never added', ' '.join(etr.notes))

    def test_a_partially_reported_breakdown_is_not_filled_in(self):
        self.record(execution(1, token_usage=usage(1000, input_tokens=800),
                              contribution='EFFECTIVE'))
        self.record(execution(2, token_usage=usage(1000),
                              contribution='EFFECTIVE'))
        breakdown = self.etr().breakdown
        self.assertEqual(breakdown.input_tokens, 800)
        self.assertEqual(breakdown.input_reported, 1)
        self.assertIsNone(breakdown.output_tokens)
        self.assertEqual(breakdown.output_reported, 0)


class EtrTelemetryTests(StoreFixture):
    def test_one_call_without_telemetry_makes_the_cohort_unknown(self):
        self.record(execution(1, token_usage=usage(1000),
                              contribution='EFFECTIVE'))
        self.record(execution(2, contribution='EFFECTIVE'))
        etr = self.report().cohort('execution', 'code-change').etr
        self.assertEqual(etr.value_status, 'UNKNOWN')
        self.assertIsNone(etr.value)
        self.assertIsNone(etr.observed_tokens)
        self.assertIn('telemetry', ' '.join(etr.gate_reasons))

    def test_an_unknown_source_is_not_trusted_telemetry(self):
        self.record(execution(
            1, token_usage=usage(1000, source=fb.UNKNOWN),
            contribution='EFFECTIVE'))
        etr = self.report().cohort('execution', 'code-change').etr
        self.assertEqual(etr.value_status, 'UNKNOWN')
        self.assertIsNone(etr.value)

    def test_an_explicitly_incomplete_count_is_not_trusted(self):
        self.record(execution(
            1, token_usage=usage(1000, complete=False),
            contribution='EFFECTIVE'))
        etr = self.report().cohort('execution', 'code-change').etr
        self.assertEqual(etr.value_status, 'UNKNOWN')
        self.assertIsNone(etr.value)

    def test_old_records_without_token_fields_are_read_but_never_scored(self):
        outcome = execution(1)
        details = dict(outcome.as_details())
        for key in [name for name in details
                    if name.startswith('token_')
                    or name in ('contribution', 'contribution_weight',
                                'agent_alias')]:
            details.pop(key)
        self.store.append_record(
            fb.FEEDBACK_DOCUMENT, summary='legacy outcome',
            source_class='OBSERVED', status='RECORDED', details=details,
            record_id='legacy', now=RECENT)
        loaded = fb.load_outcomes(self.store)
        self.assertEqual(len(loaded.outcomes), 1)
        self.assertIsNone(loaded.outcomes[0].token_usage)
        self.assertEqual(loaded.outcomes[0].contribution, 'UNRATED')
        etr = self.report().cohort('execution', 'code-change').etr
        self.assertEqual(etr.value_status, 'UNKNOWN')
        self.assertIsNone(etr.value)


class EtrThresholdTests(StoreFixture):
    def fill(self, count, contribution):
        for index in range(1, count + 1):
            self.record(execution(index, token_usage=usage(1000),
                                  contribution=contribution))

    def etr(self):
        return self.report().cohort('execution', 'code-change').etr

    def test_low_coverage_keeps_a_measured_value_but_stays_provisional(self):
        self.fill(2, 'EFFECTIVE')
        self.fill(2, 'UNRATED')
        etr = self.etr()
        self.assertEqual(etr.value, 100.0)
        self.assertEqual(etr.token_coverage, 50.0)
        self.assertEqual(etr.gate_status, 'PROVISIONAL')
        self.assertIn('coverage', ' '.join(etr.gate_reasons))
        self.assertFalse(etr.applies_automatically)

    def test_too_few_rated_tasks_never_trigger_a_threshold(self):
        self.fill(3, 'EFFECTIVE')
        etr = self.etr()
        self.assertEqual(etr.value, 100.0)
        self.assertEqual(etr.token_coverage, 100.0)
        self.assertEqual(etr.gate_status, 'PROVISIONAL')
        self.assertIn('20', ' '.join(etr.gate_reasons))

    def test_twenty_effective_tasks_raise_priority(self):
        self.fill(20, 'EFFECTIVE')
        etr = self.etr()
        self.assertEqual(etr.gate_status, 'RAISE_PRIORITY')
        self.assertFalse(etr.applies_automatically)
        self.assertTrue(etr.manual_path_preserved)

    def test_twenty_ineffective_tasks_pause_automatic_selection_only(self):
        self.fill(20, 'INEFFECTIVE')
        etr = self.etr()
        self.assertEqual(etr.value, 0.0)
        self.assertEqual(etr.gate_status, 'PAUSE_AUTO_SELECTION')
        self.assertFalse(etr.applies_automatically)
        self.assertTrue(etr.manual_path_preserved)

    def test_a_value_inside_the_band_takes_no_threshold_action(self):
        self.fill(10, 'EFFECTIVE')
        self.fill(10, 'INEFFECTIVE')
        etr = self.etr()
        self.assertEqual(etr.value, 50.0)
        self.assertEqual(etr.gate_status, 'NO_GATE')
        self.assertIn('band', ' '.join(etr.gate_reasons))

    def test_missing_telemetry_never_triggers_a_threshold_action(self):
        for index in range(1, 21):
            self.record(execution(index, contribution='INEFFECTIVE'))
        etr = self.etr()
        self.assertIsNone(etr.value)
        self.assertEqual(etr.gate_status, 'NO_GATE')

    def test_thresholds_are_configurable_and_reported(self):
        self.fill(20, 'EFFECTIVE')
        policy = fb.EtrPolicy(low_threshold=95.0, high_threshold=99.0,
                              min_scored_tasks=20, min_token_coverage=80.0)
        report = self.report(etr_policy=policy)
        etr = report.cohort('execution', 'code-change').etr
        self.assertEqual(etr.value, 100.0)
        self.assertEqual(etr.gate_status, 'RAISE_PRIORITY')
        self.assertEqual(etr.threshold_low, 95.0)
        self.assertEqual(etr.threshold_high, 99.0)
        self.assertEqual(report.etr_policy.as_dict()['low_threshold'], 95.0)

    def test_the_neutral_prior_is_separate_from_a_measured_rate(self):
        empty = self.report()
        self.assertEqual(empty.neutral_prior.value, 50.0)
        self.assertEqual(empty.neutral_prior.status, 'PROVISIONAL')
        self.assertFalse(empty.neutral_prior.measured)
        self.fill(10, 'EFFECTIVE')
        self.fill(10, 'INEFFECTIVE')
        report = self.report()
        etr = report.cohort('execution', 'code-change').etr
        self.assertEqual(etr.value, 50.0)
        self.assertFalse(etr.prior_applied)
        self.assertEqual(report.neutral_prior.value, 50.0)
        self.assertFalse(report.neutral_prior.measured)
        self.assertIn('prior', ' '.join(report.notes))


class EtrIdentityAndWindowTests(StoreFixture):
    def test_a_changed_model_identity_does_not_inherit_etr(self):
        for index in range(1, 4):
            self.record(execution(index, token_usage=usage(1000),
                                  contribution='EFFECTIVE'))
        upgraded = fb.ModelIdentity('model-a', 'runtime-x', '2.0.0')
        for index in range(4, 6):
            self.record(execution(index, identity=upgraded,
                                  token_usage=usage(1000),
                                  contribution='INEFFECTIVE'))
        report = self.report(current_identities={'execution': upgraded})
        cohorts = {cohort.identity.runtime_version: cohort
                   for cohort in report.cohorts_for('execution')}
        old = cohorts['1.0.0'].etr
        new = cohorts['2.0.0'].etr
        self.assertEqual(old.value, 100.0)
        self.assertEqual(old.evidence_status, 'IDENTITY_CHANGED')
        self.assertEqual(old.gate_status, 'NO_GATE')
        self.assertEqual(new.value, 0.0)
        self.assertEqual(new.rated_tasks, 2)

    def test_an_unknown_model_identity_cannot_trigger_a_threshold(self):
        unknown = fb.ModelIdentity()
        for index in range(1, 21):
            self.record(execution(index, identity=unknown,
                                  token_usage=usage(1000),
                                  contribution='EFFECTIVE'))
        etr = self.report().cohort('execution', 'code-change').etr
        self.assertEqual(etr.value, 100.0)
        self.assertEqual(etr.evidence_status, 'UNKNOWN_IDENTITY')
        self.assertEqual(etr.gate_status, 'NO_GATE')

    def test_records_older_than_the_window_are_stale_and_unscored(self):
        for index in range(1, 4):
            self.record(execution(index, observed_at=STALE,
                                  token_usage=usage(1000),
                                  contribution='EFFECTIVE'))
        etr = self.report().cohort('execution', 'code-change').etr
        self.assertIsNone(etr.value)
        self.assertEqual(etr.value_status, 'NO_DATA')
        self.assertEqual(etr.evidence_status, 'STALE')
        self.assertEqual(etr.gate_status, 'NO_GATE')
        self.assertIn('window', ' '.join(etr.gate_reasons))

    def test_stale_records_do_not_change_a_fresh_etr(self):
        for index in range(1, 4):
            self.record(execution(index, token_usage=usage(1000),
                                  contribution='EFFECTIVE'))
        self.record(execution(9, observed_at=STALE, token_usage=usage(9000),
                              contribution='INEFFECTIVE'))
        etr = self.report().cohort('execution', 'code-change').etr
        self.assertEqual(etr.value, 100.0)
        self.assertEqual(etr.observed_tokens, 3000)
        self.assertEqual(etr.evidence_status, 'CURRENT')


class EtrValidationTests(StoreFixture):
    def test_an_unknown_contribution_status_is_rejected(self):
        with self.assertRaises(fb.FeedbackError):
            execution(1, contribution='GREAT')

    def test_contribution_weights_are_the_frozen_initial_values(self):
        self.assertEqual(fb.contribution_weight('EFFECTIVE'), 1.0)
        self.assertEqual(fb.contribution_weight('PARTIAL'), 0.5)
        self.assertEqual(fb.contribution_weight('INEFFECTIVE'), 0.0)
        self.assertIsNone(fb.contribution_weight('UNRATED'))
        with self.assertRaises(fb.FeedbackError):
            fb.contribution_weight('UNKNOWN')

    def test_a_negative_token_count_is_rejected(self):
        with self.assertRaises(ValueError):
            fb.TokenUsage(total_tokens=-1, source='runtime:fixture', complete=True)

    def test_a_token_source_is_required_to_be_usable(self):
        with self.assertRaises(ValueError):
            fb.TokenUsage(total_tokens=10, source='', complete=True)

    def test_an_illegal_threshold_pair_is_rejected(self):
        for kwargs in ({'low_threshold': 60.0, 'high_threshold': 40.0},
                       {'low_threshold': -1.0},
                       {'high_threshold': 101.0},
                       {'min_scored_tasks': 0},
                       {'min_token_coverage': 0.0},
                       {'min_token_coverage': 120.0}):
            with self.assertRaises(fb.FeedbackError):
                fb.EtrPolicy(**kwargs)

    def test_a_tampered_stored_weight_is_rejected_on_read(self):
        outcome = execution(1, token_usage=usage(1000),
                            contribution='EFFECTIVE')
        details = dict(outcome.as_details())
        details['contribution_weight'] = '0.5'
        self.store.append_record(
            fb.FEEDBACK_DOCUMENT, summary='tampered', source_class='OBSERVED',
            status='RECORDED', details=details, record_id='tampered',
            now=RECENT)
        loaded = fb.load_outcomes(self.store)
        self.assertEqual(loaded.outcomes, ())
        self.assertEqual(loaded.ignored, 1)
        self.assertIn('weight', ' '.join(loaded.diagnostics))


class EtrPersistenceTests(StoreFixture):
    def test_token_usage_and_contribution_survive_a_round_trip(self):
        outcome = execution(
            1, token_usage=usage(1200, input_tokens=900, output_tokens=300,
                                 cached_tokens=100, reasoning_tokens=50),
            contribution='EFFECTIVE', agent_alias='builder-1')
        self.record(outcome)
        loaded = fb.load_outcomes(self.store).outcomes[0]
        self.assertEqual(loaded.token_usage.total_tokens, 1200)
        self.assertEqual(loaded.token_usage.input_tokens, 900)
        self.assertEqual(loaded.token_usage.cached_tokens, 100)
        self.assertEqual(loaded.token_usage.source, 'runtime:fixture')
        self.assertEqual(loaded.contribution, 'EFFECTIVE')
        self.assertEqual(loaded.agent_alias, 'builder-1')
        self.assertIn('token_total: 1200', self.document())
        self.assertIn('contribution_weight: 1.0', self.document())
        etr = self.report().cohort('execution', 'code-change').etr
        self.assertEqual(etr.observed_tokens, 1200)
        self.assertEqual(etr.value, 100.0)

    def test_the_report_renders_etr_without_inventing_a_number(self):
        self.record(execution(1, token_usage=usage(0), contribution='UNRATED'))
        rendered = '\n'.join(self.report().render_lines())
        self.assertIn('etr=', rendered)
        self.assertIn('NO_DATA', rendered)
        self.assertIn('prior', rendered)


if __name__ == '__main__':
    unittest.main()
