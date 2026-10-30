"""Runtime adapter contract tests (#121, design §4–§5).

Fixed fixtures only: no runtime process, no network, no real model data.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent_selection import runtime_adapter  # noqa: E402
from tests.agent_selection import support  # noqa: E402

NOW = support.NOW


class CapabilityValidationTests(unittest.TestCase):
    def test_pass_requires_observed_evidence_with_a_reference(self):
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.Capability('C1', 'PASS', source_class='INFERRED',
                                       probe_date=support.PROBE,
                                       evidence_ref='probe:1')
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.Capability('C1', 'PASS', source_class='OBSERVED',
                                       probe_date=support.PROBE)
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.Capability('C1', 'PASS', source_class='OBSERVED',
                                       evidence_ref='probe:1')

    def test_unknown_and_fail_never_need_evidence(self):
        for verdict in ('UNKNOWN', 'FAIL', 'NOT_APPLICABLE'):
            with self.subTest(verdict=verdict):
                row = runtime_adapter.Capability('C1', verdict)
                self.assertEqual(row.verdict, verdict)

    def test_unknown_capability_id_is_rejected(self):
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.Capability('C9', 'UNKNOWN')


class CapabilityGateTests(unittest.TestCase):
    def gate(self, record):
        return record.gate(now=NOW, max_age_days=30)

    def test_all_c1_to_c4_pass_opens_the_path(self):
        gate = self.gate(support.capability_record())
        self.assertTrue(gate.passed)
        self.assertEqual(gate.status, 'PASS')

    def test_c1_failure_blocks_the_path(self):
        record = support.capability_record(overrides={
            'C1': support.capability('C1', 'FAIL', evidence_ref='')})
        gate = self.gate(record)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertIn('C1: FAIL', ' '.join(gate.reasons))

    def test_c2_context_isolation_failure_blocks_the_path(self):
        record = support.capability_record(overrides={
            'C2': support.capability(
                'C2', 'FAIL', evidence_ref='',
                limits='two probes shared one writable session')})
        gate = self.gate(record)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertIn('C2', ' '.join(gate.reasons))

    def test_c3_empty_result_is_not_success(self):
        record = support.capability_record(overrides={
            'C3': support.capability(
                'C3', 'FAIL', evidence_ref='',
                limits='wait returned an empty result that could not be '
                       'classified')})
        gate = self.gate(record)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertFalse(gate.passed)

    def test_unknown_row_cannot_be_counted_as_pass(self):
        record = support.capability_record(overrides={
            'C4': support.capability('C4', 'UNKNOWN', probe_date=None)})
        self.assertEqual(self.gate(record).status, 'UNKNOWN')

    def test_missing_row_is_unknown_not_pass(self):
        record = support.capability_record()
        rows = tuple(row for row in record.capabilities
                     if row.capability_id != 'C2')
        without = runtime_adapter.CapabilityRecord(
            runtime_id=record.runtime_id, runtime_version=record.runtime_version,
            edition=record.edition, execution_mode=record.execution_mode,
            status=record.status, probe_date=record.probe_date,
            evidence_ref=record.evidence_ref, capabilities=rows)
        gate = self.gate(without)
        self.assertEqual(gate.status, 'UNKNOWN')
        self.assertIn('C2', gate.missing)

    def test_partial_and_stale_records_never_unlock(self):
        for status in ('PARTIAL', 'STALE'):
            with self.subTest(status=status):
                gate = self.gate(support.capability_record(status=status))
                self.assertEqual(gate.status, 'BLOCKED')

    def test_stale_probe_is_downgraded(self):
        record = support.capability_record(probe_date=support.OLD_PROBE)
        gate = self.gate(record)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertTrue(gate.stale)
        self.assertIn('re-probe', ' '.join(gate.reasons))

    def test_stale_row_evidence_is_blocked(self):
        stale_row = runtime_adapter.Capability(
            'C4', 'UNKNOWN', source_class='STALE',
            probe_date=support.PROBE, evidence_ref='probe:old')
        record = support.capability_record(overrides={'C4': stale_row})
        gate = self.gate(record)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertTrue(gate.stale)


class CapabilityRowFreshnessTests(unittest.TestCase):
    """Every gating row is fresh on its own probe date, not the header's (#205).

    A freshly re-dated capability *record* must not launder stale per-row
    evidence. ``gate`` applies the same ``now``, the same default / explicit
    ``max_age_days`` and a fail-closed future-date rule to the header and to
    each of C1-C4.
    """

    HEADER = NOW

    def record_with_row(self, capability_id, probe_date):
        return support.capability_record(
            probe_date=self.HEADER,
            overrides={capability_id: support.capability(
                capability_id, probe_date=probe_date)})

    def assert_not_fresh(self, gate):
        self.assertFalse(gate.passed)
        self.assertNotEqual(gate.status, 'PASS')

    def test_a_fresh_header_does_not_refresh_one_stale_row(self):
        for capability_id in support.PASS_VERDICTS:
            with self.subTest(capability_id=capability_id):
                gate = self.record_with_row(
                    capability_id, support.OLD_PROBE).gate(now=NOW,
                                                           max_age_days=30)
                self.assert_not_fresh(gate)
                self.assertEqual(gate.status, 'BLOCKED')
                self.assertTrue(gate.stale)
                joined = ' '.join(gate.reasons)
                self.assertIn(f'{capability_id}:', joined)
                self.assertIn('re-probe', joined)

    def test_a_fresh_header_does_not_refresh_all_stale_rows(self):
        overrides = {capability_id: support.capability(
            capability_id, probe_date=support.OLD_PROBE)
            for capability_id in support.PASS_VERDICTS}
        record = support.capability_record(probe_date=self.HEADER,
                                           overrides=overrides)
        gate = record.gate(now=NOW, max_age_days=30)
        self.assert_not_fresh(gate)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertTrue(gate.stale)
        joined = ' '.join(gate.reasons)
        for capability_id in support.PASS_VERDICTS:
            self.assertIn(f'{capability_id}:', joined)

    def test_row_freshness_uses_the_same_explicit_max_age_days(self):
        record = self.record_with_row('C2', '2026-10-17T12:00:00Z')  # 10 days
        self.assertTrue(record.gate(now=NOW, max_age_days=10).passed)
        gate = record.gate(now=NOW, max_age_days=9)
        self.assert_not_fresh(gate)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertTrue(gate.stale)
        self.assertIn('C2:', ' '.join(gate.reasons))

    def test_row_freshness_boundary_matches_the_record_limit(self):
        at_limit = self.record_with_row('C1', '2026-09-27T12:00:00Z')  # 30 days
        self.assertTrue(at_limit.gate(now=NOW, max_age_days=30).passed)
        past_limit = self.record_with_row('C1', '2026-09-27T11:59:59Z')
        gate = past_limit.gate(now=NOW, max_age_days=30)
        self.assert_not_fresh(gate)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertTrue(gate.stale)

    def test_default_max_age_days_applies_to_rows(self):
        record = self.record_with_row('C4', '2026-09-26T12:00:00Z')  # 31 days
        gate = record.gate(now=NOW)
        self.assert_not_fresh(gate)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertTrue(gate.stale)

    def test_future_row_probe_date_is_not_trusted_as_fresh(self):
        record = self.record_with_row('C3', '2026-10-28T12:00:00Z')
        gate = record.gate(now=NOW)
        self.assert_not_fresh(gate)
        self.assertEqual(gate.status, 'UNKNOWN')
        joined = ' '.join(gate.reasons)
        self.assertIn('C3:', joined)
        self.assertIn('future', joined)

    def test_future_row_probe_date_is_untrusted_without_an_age_limit(self):
        record = self.record_with_row('C2', '2026-10-28T12:00:00Z')
        gate = record.gate(now=NOW, max_age_days=None)
        self.assert_not_fresh(gate)
        self.assertEqual(gate.status, 'UNKNOWN')

    def test_a_pass_row_cannot_exist_without_a_probe_date(self):
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.Capability(
                'C1', 'PASS', source_class='OBSERVED', probe_date=None,
                evidence_ref='probe:1')

    def test_gate_fails_closed_on_a_row_built_outside_its_constructor(self):
        # Defence in depth: a row materialised outside the validating
        # constructor (for example by a future deserialiser) must still not be
        # treated as fresh evidence.
        class UncheckedCapability(runtime_adapter.Capability):
            def __post_init__(self):
                pass

        row = UncheckedCapability('C1', 'PASS', source_class='OBSERVED',
                                  probe_date=None, evidence_ref='probe:1')
        record = support.capability_record(probe_date=self.HEADER,
                                           overrides={'C1': row})
        gate = record.gate(now=NOW)
        self.assert_not_fresh(gate)
        self.assertIn('C1:', ' '.join(gate.reasons))

    def test_an_invalid_row_probe_date_is_rejected_at_construction(self):
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.Capability(
                'C2', 'PASS', source_class='OBSERVED',
                probe_date='2026-13-01T00:00:00Z', evidence_ref='probe:1')
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.Capability(
                'C2', 'PASS', source_class='OBSERVED',
                probe_date='not-a-timestamp', evidence_ref='probe:1')

    def test_gate_fails_closed_on_an_invalid_row_date_built_outside_its_constructor(
            self):
        class UncheckedCapability(runtime_adapter.Capability):
            def __post_init__(self):
                pass

        row = UncheckedCapability('C3', 'PASS', source_class='OBSERVED',
                                  probe_date='not-a-timestamp',
                                  evidence_ref='probe:1')
        record = support.capability_record(probe_date=self.HEADER,
                                           overrides={'C3': row})
        gate = record.gate(now=NOW)
        self.assert_not_fresh(gate)
        self.assertEqual(gate.status, 'UNKNOWN')
        joined = ' '.join(gate.reasons)
        self.assertIn('C3:', joined)
        self.assertIn('cannot be parsed', joined)

    def test_a_future_record_header_is_not_trusted_as_fresh(self):
        record = support.capability_record(probe_date='2026-10-28T12:00:00Z')
        gate = record.gate(now=NOW, max_age_days=30)
        self.assert_not_fresh(gate)
        self.assertEqual(gate.status, 'UNKNOWN')
        self.assertFalse(gate.stale)
        joined = ' '.join(gate.reasons)
        self.assertIn('future', joined)
        self.assertIn('capability probe date', joined)

    def test_a_future_record_header_is_untrusted_without_an_age_limit(self):
        record = support.capability_record(probe_date='2026-10-28T12:00:00Z')
        gate = record.gate(now=NOW, max_age_days=None)
        self.assert_not_fresh(gate)
        self.assertEqual(gate.status, 'UNKNOWN')

    def test_a_past_record_header_still_uses_the_age_limit(self):
        record = support.capability_record(probe_date=support.OLD_PROBE)
        gate = record.gate(now=NOW, max_age_days=30)
        self.assertEqual(gate.status, 'BLOCKED')
        self.assertTrue(gate.stale)
        self.assertIn('re-probe', ' '.join(gate.reasons))

    def test_gate_does_not_rewrite_stored_row_probe_dates(self):
        record = self.record_with_row('C1', support.OLD_PROBE)
        before = record.as_dict()
        record.gate(now=NOW)
        self.assertEqual(record.as_dict(), before)
        self.assertEqual(record.get('C1').probe_date, support.OLD_PROBE)


class AdapterContractTests(unittest.TestCase):
    def test_host_opaque_adapter_reports_unsupported_and_no_candidates(self):
        adapter = runtime_adapter.HostOpaqueRuntimeAdapter(
            runtime_id='process-local', runtime_version='0.0.0', edition='github')
        description = adapter.describe()
        self.assertFalse(description.can_enumerate_agents)
        result = adapter.discover()
        self.assertEqual(result.outcome, 'UNSUPPORTED')
        self.assertEqual(result.candidates, ())
        self.assertIn('process-local', ' '.join(description.notes))

    def test_discovery_result_rejects_ok_without_candidates(self):
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.DiscoveryResult(outcome='OK', mode='runtime')

    def test_candidate_requires_a_matching_capability_record(self):
        other = support.capability_record(runtime_id='other-runtime')
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            support.candidate(capability_record_value=other)

    def test_read_only_scope_cannot_declare_write_paths(self):
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.PermissionScope(read_only=True, write_paths=('src',))


class PathAndEvidenceHelperTests(unittest.TestCase):
    def test_covers_path_is_component_aware(self):
        self.assertTrue(runtime_adapter.covers_path(('docs',), 'docs/a.md'))
        self.assertTrue(runtime_adapter.covers_path(('./docs/',), 'docs/a.md'))
        self.assertFalse(runtime_adapter.covers_path(('docs',), 'docs-other/a.md'))
        self.assertFalse(runtime_adapter.covers_path(('docs',), 'src/a.py'))
        self.assertFalse(runtime_adapter.covers_path((), 'docs/a.md'))

    def test_blank_scope_path_is_never_a_wildcard(self):
        self.assertFalse(
            runtime_adapter.covers_path(('',), 'private/secret.py'))
        self.assertFalse(runtime_adapter.covers_path(('',), 'src/a.py'))
        self.assertFalse(runtime_adapter.covers_path(('', ''), 'docs/a.md'))

    def test_whitespace_scope_paths_are_never_a_wildcard(self):
        for scope in (('   ',), ('\t',), ('  ', '\t')):
            with self.subTest(scope=scope):
                self.assertFalse(
                    runtime_adapter.covers_path(scope, 'private/secret.py'))

    def test_blank_paths_do_not_cover_through_permission_scope(self):
        scope = runtime_adapter.PermissionScope(
            read_paths=('',), write_paths=('   ',))
        self.assertFalse(
            runtime_adapter.covers_path(scope.read_paths, 'private/secret.py'))
        self.assertFalse(
            runtime_adapter.covers_path(scope.write_paths, 'private/secret.py'))

    def test_parent_segment_traversal_leaves_the_scope(self):
        self.assertFalse(
            runtime_adapter.covers_path(
                ('docs',), 'docs/../private/secret.py'))
        self.assertFalse(
            runtime_adapter.covers_path(
                ('docs',), 'docs/../../private/secret.py'))
        self.assertFalse(
            runtime_adapter.covers_path(
                ('docs/sub',), 'docs/sub/../../private/secret.py'))

    def test_a_scope_that_escapes_the_project_root_covers_nothing(self):
        self.assertFalse(runtime_adapter.covers_path(('../docs',), 'docs/a.md'))
        self.assertFalse(runtime_adapter.covers_path(('..',), 'docs/a.md'))
        self.assertFalse(runtime_adapter.covers_path(('docs',), '../docs/a.md'))

    def test_scope_parent_segments_are_not_proof_of_a_boundary(self):
        for scope in (('docs/..',), ('docs/sub/..',), ('docs/../docs',)):
            with self.subTest(scope=scope):
                self.assertFalse(
                    runtime_adapter.covers_path(scope, 'docs/a.md'))
                self.assertFalse(
                    runtime_adapter.covers_path(scope, 'private/secret.py'))

    def test_absolute_and_drive_paths_are_not_covered_without_proof(self):
        for scope in (('*',), ('.',), ('./',), ('/',), ('docs',)):
            with self.subTest(scope=scope):
                self.assertFalse(
                    runtime_adapter.covers_path(scope, '/etc/passwd'))
                self.assertFalse(
                    runtime_adapter.covers_path(scope, 'C:/project/a.py'))
                self.assertFalse(
                    runtime_adapter.covers_path(scope, 'c:\\project\\a.py'))
        self.assertFalse(
            runtime_adapter.covers_path(('C:/project',), 'C:/project/a.py'))

    def test_non_string_scope_entries_are_not_evidence(self):
        self.assertFalse(runtime_adapter.covers_path((None,), 'docs/a.md'))
        self.assertFalse(runtime_adapter.covers_path((42,), 'docs/a.md'))
        self.assertFalse(runtime_adapter.covers_path(('docs',), None))

    def test_uncomparable_path_forms_are_never_covered(self):
        for scope in (('*',), ('.',), ('./',), ('docs',)):
            for needed in (
                    's3://private/secret', './C:/private/secret',
                    'C:/private/secret', '/etc/passwd', 'file:///etc/passwd'):
                with self.subTest(scope=scope, needed=needed):
                    self.assertFalse(
                        runtime_adapter.covers_path(scope, needed))

    def test_surrounding_whitespace_is_not_silently_trimmed(self):
        self.assertFalse(
            runtime_adapter.covers_path((' * ',), 'private/secret'))
        self.assertFalse(runtime_adapter.covers_path(('docs ',), 'docs/private'))
        self.assertFalse(runtime_adapter.covers_path((' docs',), 'docs/private'))
        self.assertFalse(
            runtime_adapter.covers_path(('\tdocs',), 'docs/private'))
        self.assertFalse(runtime_adapter.covers_path(('docs',), 'docs/private '))
        self.assertFalse(
            runtime_adapter.covers_path(('docs',), ' docs/private'))

    def test_scope_covers_the_same_path_and_real_subdirectories(self):
        self.assertTrue(runtime_adapter.covers_path(('docs',), 'docs'))
        self.assertTrue(runtime_adapter.covers_path(('docs',), 'docs/a/b.md'))
        self.assertTrue(
            runtime_adapter.covers_path(('docs/a',), 'docs/a/b.md'))

    def test_separator_and_dot_spellings_are_normalised(self):
        self.assertTrue(runtime_adapter.covers_path(('docs\\a',), 'docs/a/b.md'))
        self.assertTrue(runtime_adapter.covers_path(('docs//a',), 'docs/a/b.md'))
        self.assertTrue(runtime_adapter.covers_path(('docs',), './docs//a.md'))
        self.assertTrue(runtime_adapter.covers_path(('docs',), 'docs/./a.md'))
        self.assertTrue(runtime_adapter.covers_path(('docs',), 'docs/a/../b.md'))
        self.assertFalse(
            runtime_adapter.covers_path(('docs',), './docs-other/a.md'))

    def test_explicit_wildcard_still_covers_relative_paths(self):
        self.assertTrue(runtime_adapter.covers_path(('*',), 'private/secret.py'))
        self.assertTrue(runtime_adapter.covers_path(('*',), 'docs/a.md'))
        self.assertTrue(runtime_adapter.covers_path(('*',), 'docs/file.py'))

    def test_project_root_still_covers_relative_paths(self):
        for scope in (('.',), ('./',), ('./.',)):
            with self.subTest(scope=scope):
                self.assertTrue(
                    runtime_adapter.covers_path(scope, 'src/a.py'))
                self.assertTrue(
                    runtime_adapter.covers_path(scope, 'private/secret.py'))

    def test_covers_command_is_exact_or_explicit_wildcard(self):
        self.assertTrue(runtime_adapter.covers_command(('python',), 'python'))
        self.assertFalse(runtime_adapter.covers_command(('python',), 'python3'))
        self.assertTrue(runtime_adapter.covers_command(('*',), 'python3'))
        self.assertFalse(runtime_adapter.covers_command((), 'python'))

    def test_covers_command_ignores_blank_entries(self):
        self.assertFalse(runtime_adapter.covers_command(('',), 'python'))
        self.assertFalse(runtime_adapter.covers_command(('   ',), 'python'))
        self.assertFalse(runtime_adapter.covers_command(('', '   '), 'python'))
        self.assertTrue(runtime_adapter.covers_command(('*',), 'python3'))

    def test_permission_scope_rejects_blank_command_entries(self):
        for commands in (('',), ('   ',), ('python', '')):
            with self.subTest(commands=commands):
                with self.assertRaises(runtime_adapter.RuntimeAdapterError):
                    runtime_adapter.PermissionScope(commands=commands)

    def test_network_enabled_cannot_be_observed_off(self):
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.PermissionScope(network=True, network_observed=False)

    def test_network_disabled_cannot_be_observed_on(self):
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.PermissionScope(network=False, network_observed=True)

    def test_evidence_age_uses_the_supplied_now(self):
        self.assertAlmostEqual(
            runtime_adapter.evidence_age_days(support.PROBE, NOW), 1.0)
        self.assertIsNone(runtime_adapter.evidence_age_days('not-a-date', NOW))

    def test_timestamp_format_is_enforced(self):
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.parse_timestamp('2026-10-27 12:00:00')

    def test_graded_role_evidence_cannot_be_inferred(self):
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.RoleEvidence(
                role='reviewer', dimension='defect-detection', grade='STRONG',
                origin='project', evidence_ref='fixture', observed_at=NOW,
                sample_size=5, source_class='INFERRED')
        with self.assertRaises(runtime_adapter.RuntimeAdapterError):
            runtime_adapter.RoleEvidence(
                role='reviewer', dimension='defect-detection', grade='STRONG',
                origin='project', evidence_ref='fixture', observed_at=NOW,
                sample_size=0)


if __name__ == '__main__':
    unittest.main()
