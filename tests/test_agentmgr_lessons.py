"""Synthetic regression tests for the AgentMgr-derived architecture rules (#56).

The rule document is `docs/architecture/agentmgr-contract-rules.md` (AGM-1…AGM-6)
and the evidence mapping is `docs/self-improvement/agentmgr-lessons.md`. Both
generalise lessons from a downstream project's public issues and pull requests;
nothing from that project is copied here, and no fixture contains real business
data. The assertions below check four things the documents cannot check by
themselves:

  * the new scenarios exist, keep the right polarity and really run (a negative
    group only passes when the interception happened);
  * the rule document, the scenario catalogue, the runner registry and this test
    file stay in sync (a rule that points at a missing scenario fails here);
  * the frozen Architecture Contract was not extended to make the rules work
    (no new `kind`, no new `params`, no new report status);
  * the semantic items stay semantic: a declared contract test never becomes
    coverage, and an unconfigured project is never judged by inferred rules.

These tests do **not** prove anything about the downstream project's code or
runtime. Its links are cited for provenance only.
"""
import importlib.util
import inspect
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / 'scripts' / 'run_delivery_scenarios.py'
RULE_DOC = ROOT / 'docs' / 'architecture' / 'agentmgr-contract-rules.md'
LESSONS_DOC = ROOT / 'docs' / 'self-improvement' / 'agentmgr-lessons.md'
ARCH_PACKAGE = ROOT / 'scripts' / 'architecture'
ARCH_CHECK = ARCH_PACKAGE / 'check.py'
SCHEMA = ROOT / 'docs' / 'contracts' / 'schemas' / 'architecture-contract.schema.json'

_spec = importlib.util.spec_from_file_location('agentmgr_lessons_runner', RUNNER_PATH)
runner = importlib.util.module_from_spec(_spec)
# Register before exec: the runner uses `from __future__ import annotations`.
sys.modules[_spec.name] = runner
_spec.loader.exec_module(runner)

SCENARIO_DOC = ROOT / runner.SCENARIO_DOC

# One positive control per family (clean contract, unconfigured project) and five
# injected-defect scenarios, exactly the five families the Issue names.
NEW_SCENARIOS = ('C12', 'C13', 'C14', 'C15', 'C16', 'C17', 'C18')
POSITIVE_SCENARIOS = ('C12', 'C18')
NEGATIVE_SCENARIOS = ('C13', 'C14', 'C15', 'C16', 'C17')
RULE_IDS = ('AGM-1', 'AGM-2', 'AGM-3', 'AGM-4', 'AGM-5', 'AGM-6')

# Public evidence links only: the mapping document may cite these and nothing
# else. They are provenance, not input data.
EVIDENCE_LINKS = {
    'https://github.com/ctrlshiftaltdel/AgentMgr/issues/77',
    'https://github.com/ctrlshiftaltdel/AgentMgr/issues/81',
    'https://github.com/ctrlshiftaltdel/AgentMgr/issues/95',
    'https://github.com/ctrlshiftaltdel/AgentMgr/pull/91',
    'https://github.com/ctrlshiftaltdel/AgentMgr/pull/93',
}

FIXTURE_SOURCES = (
    '_agm_tree', '_write_agm_contract', '_arch_check', '_load_architecture',
    '_load_synthetic', '_transport_behaviour', '_adapter_contract_run',
    '_verification_status', '_claim_findings', '_dispatch_outcome',
    '_terminal_findings', '_capability_record', '_capability_findings',
    'scenario_c12', 'scenario_c13', 'scenario_c14', 'scenario_c15', 'scenario_c16',
    'scenario_c17', 'scenario_c18',
)


def section(text, heading_contains):
    """Markdown block under the first heading containing `heading_contains`."""
    lines, start = text.splitlines(), None
    for index, line in enumerate(lines):
        if line.startswith('#') and heading_contains.lower() in line.lower():
            start = index + 1
            break
    if start is None:
        return None
    body = []
    for line in lines[start:]:
        if line.startswith('## '):
            break
        body.append(line)
    return '\n'.join(body)


def load_python(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class RegistryTests(unittest.TestCase):
    """The new scenarios exist, are wired, and declare the right polarity."""

    def test_new_scenarios_are_registered(self):
        ids = runner.scenario_ids()
        for scenario_id in NEW_SCENARIOS:
            self.assertIn(scenario_id, ids, f'{scenario_id} is not registered in the runner')
        self.assertEqual(len(ids), len(set(ids)), 'scenario ids must stay unique')

    def test_new_scenarios_keep_two_positives_and_five_negatives(self):
        by_id = {item.id: item for item in runner.SCENARIOS}
        for scenario_id in POSITIVE_SCENARIOS:
            self.assertEqual(by_id[scenario_id].kind, 'positive',
                             f'{scenario_id} must be a positive control')
        for scenario_id in NEGATIVE_SCENARIOS:
            self.assertEqual(by_id[scenario_id].kind, 'negative',
                             f'{scenario_id} must be an injected-defect scenario')
        self.assertEqual(set(POSITIVE_SCENARIOS) | set(NEGATIVE_SCENARIOS),
                         set(NEW_SCENARIOS))

    def test_new_scenarios_declare_precondition_defect_gate_and_evidence(self):
        by_id = {item.id: item for item in runner.SCENARIOS}
        for scenario_id in NEW_SCENARIOS:
            item = by_id[scenario_id]
            for attribute in ('title', 'precondition', 'defect', 'gate', 'evidence',
                              'cleanup'):
                self.assertTrue(getattr(item, attribute),
                                f'{scenario_id} has an empty {attribute}')

    def test_scenario_catalogue_lists_every_new_scenario(self):
        text = SCENARIO_DOC.read_text(encoding='utf-8')
        for scenario_id in NEW_SCENARIOS:
            self.assertIn(f'| {scenario_id} |', text,
                          f'{scenario_id} is missing from {runner.SCENARIO_DOC}')

    def test_scenario_catalogue_points_at_the_rule_document(self):
        text = SCENARIO_DOC.read_text(encoding='utf-8')
        self.assertIn('agentmgr-contract-rules.md', text)


class ScenarioBehaviourTests(unittest.TestCase):
    """Each rule is exercised through the real checker, and the state is asserted."""

    @classmethod
    def setUpClass(cls):
        cls.workdir = tempfile.TemporaryDirectory()
        cls.vk = runner._load_module('agentmgr_lessons_validate_kit',
                                     runner.SCRIPTS / 'validate_kit.py')
        cls.orch = runner._load_module('agentmgr_lessons_orchestrator',
                                       runner.SCRIPTS / 'orchestrator.py')
        selected = runner.select_scenarios(list(NEW_SCENARIOS))
        cls.results = {result.scenario.id: result
                       for result in runner.run_scenarios(selected, cls.workdir.name,
                                                          vk=cls.vk, orch=cls.orch)}

    @classmethod
    def tearDownClass(cls):
        cls.workdir.cleanup()

    def evidence(self, scenario_id):
        result = self.results[scenario_id]
        self.assertEqual(result.status, 'PASS',
                         f'{scenario_id} did not pass: {result.error.get("message", "")}')
        return '\n'.join(result.evidence)

    def test_every_new_scenario_passes_and_leaves_evidence(self):
        for scenario_id in NEW_SCENARIOS:
            with self.subTest(scenario=scenario_id):
                self.assertTrue(self.evidence(scenario_id))

    def test_positive_control_reports_pass_with_its_scope(self):
        evidence = self.evidence('C12')
        self.assertIn('status=PASS', evidence)
        self.assertIn('examined modules = ', evidence)
        self.assertIn('uncovered = none', evidence)
        self.assertIn('both transports agree', evidence)

    def test_private_adapter_symbol_is_blocked_and_located(self):
        evidence = self.evidence('C13')
        self.assertIn('blocking: True', evidence)
        self.assertIn('PRIVATE_ACCESS error: application -> adapter-hermes', evidence)
        self.assertIn('src/app/service.py:1', evidence)
        self.assertIn('INV-AGM-ADAPTER-ISOLATION coverage: ERROR', evidence)

    def test_forked_entry_points_need_both_layers(self):
        evidence = self.evidence('C14')
        # The static layer must not pretend to catch an inline re-implementation.
        self.assertIn('static blocking=False', evidence)
        self.assertIn('agreement=False', evidence)
        self.assertIn('INV-AGM-SHARED-DECISION coverage=UNKNOWN', evidence)
        # The same fork expressed as a direct import is blocked.
        self.assertIn('structural fork: blocking=True', evidence)

    def test_mock_success_cannot_be_reported_as_real(self):
        evidence = self.evidence('C15')
        self.assertIn('claim exceeds evidence', evidence)
        self.assertIn('verification status UNKNOWN', evidence)

    def test_terminal_states_cannot_collapse(self):
        evidence = self.evidence('C16')
        self.assertIn('collapse', evidence)
        self.assertIn('DISPATCH_ERROR', evidence)
        self.assertIn('DISPATCH_CANCELLED', evidence)
        self.assertIn('DISPATCH_TIMED_OUT', evidence)
        self.assertIn('DISPATCH_UNKNOWN', evidence)

    def test_capability_without_evidence_is_rejected(self):
        evidence = self.evidence('C17')
        self.assertIn('PASS without evidence', evidence)
        self.assertIn('not evidence of', evidence)
        self.assertIn('scope', evidence)
        self.assertIn('honest UNKNOWN claim: 0 findings', evidence)

    def test_unconfigured_project_is_not_judged_by_inferred_rules(self):
        evidence = self.evidence('C18')
        self.assertIn('mode=OFF', evidence)
        self.assertIn('blocking=False', evidence)
        self.assertIn('ERROR=0 WARNING=0', evidence)
        self.assertIn('declared contract: blocking=True', evidence)

    def test_counts_keep_two_positives_and_five_negatives(self):
        report = runner.build_report(list(self.results.values()))
        totals = report['totals']
        self.assertEqual(totals['positive'], len(POSITIVE_SCENARIOS))
        self.assertEqual(totals['negative'], len(NEGATIVE_SCENARIOS))
        self.assertEqual(totals['negative_passed'], totals['negative'],
                         'negative scenarios only pass when the interception really happened')
        self.assertEqual(totals['errors'], 0)

    def test_the_new_scenarios_clean_up_their_sandboxes(self):
        leftovers = list(Path(self.workdir.name).iterdir())
        self.assertEqual(leftovers, [], f'sandboxes were not cleaned: {leftovers}')


class RuleDocumentTests(unittest.TestCase):
    """The rule document is project-agnostic, cites no project, maps to real scenarios."""

    def setUp(self):
        self.assertTrue(RULE_DOC.is_file(), f'missing rule document: {RULE_DOC}')
        self.text = RULE_DOC.read_text(encoding='utf-8')

    def test_document_refers_to_the_core_contract_instead_of_restating_it(self):
        self.assertIn('docs/orchestration.md', self.text)
        self.assertIn('4.1', self.text)
        self.assertTrue('不复制' in self.text,
                        'the document must state that it references, not restates, the '
                        'state machine and the evidence matrix')

    def test_document_does_not_redefine_the_state_machine(self):
        self.assertNotIn('PLANNED → READY → DISPATCHED', self.text)
        self.assertNotIn('PLANNED -> READY -> DISPATCHED', self.text)

    def test_document_names_every_rule_and_its_layers(self):
        for rule_id in RULE_IDS:
            self.assertIn(rule_id, self.text, f'missing rule {rule_id}')
        self.assertIn('Reviewer', self.text,
                      'the document must say which judgements stay with the reviewer')
        self.assertIn('合成', self.text, 'the document must state that fixtures are synthetic')
        self.assertIn('UNKNOWN', self.text,
                      'the document must keep the honest unknown outcome visible')

    def test_document_does_not_mandate_a_directory_layout_or_framework(self):
        self.assertTrue('不规定目录名' in self.text or '不强制' in self.text,
                        'the rules must stay project-agnostic about layout and framework')
        lowered = self.text.lower()
        for needle in ('frameworks are mandatory', 'must use the directory'):
            self.assertNotIn(needle, lowered)

    def test_document_records_that_no_contract_kind_was_added(self):
        self.assertIn('未新增 `kind`', self.text)
        self.assertIn('DUPLICATE_BUSINESS_RULE', self.text,
                      'the unimplemented kinds must stay explicit, not silently implemented')

    def test_every_rule_maps_to_a_registered_scenario(self):
        referenced = set(re.findall(r'\bC\d{1,2}\b', self.text))
        self.assertTrue(referenced, 'the mapping table references no scenario id')
        self.assertLessEqual(referenced, set(runner.scenario_ids()),
                             'the document references a scenario the runner does not define')
        self.assertLessEqual(set(NEW_SCENARIOS), referenced,
                             'the document does not reference every new scenario')

    def test_rule_document_cites_no_projects(self):
        self.assertEqual(re.findall(r'https?://[^\s)>]+', self.text), [],
                         'the rule document must stay project-agnostic; provenance lives in '
                         'the lessons mapping document')


class LessonsDocumentTests(unittest.TestCase):
    """The mapping document cites provenance only, and never copies project content."""

    def setUp(self):
        self.assertTrue(LESSONS_DOC.is_file(), f'missing lessons document: {LESSONS_DOC}')
        self.text = LESSONS_DOC.read_text(encoding='utf-8')

    def test_document_cites_only_the_public_evidence_links(self):
        links = set(re.findall(r'https?://[^\s)>]+', self.text))
        self.assertEqual(links, EVIDENCE_LINKS,
                         'the document must cite exactly the public issue / pull-request links')

    def test_document_maps_every_rule(self):
        for rule_id in RULE_IDS:
            self.assertIn(rule_id, self.text, f'the mapping does not cover {rule_id}')

    def test_document_maps_to_registered_scenarios_only(self):
        referenced = set(re.findall(r'\bC\d{1,2}\b', self.text))
        self.assertTrue(referenced, 'the mapping references no scenario id')
        self.assertLessEqual(referenced, set(runner.scenario_ids()))
        self.assertLessEqual(set(NEW_SCENARIOS), referenced)

    def test_document_keeps_limits_and_unverified_assumptions(self):
        block = section(self.text, '限制与未验证假设')
        self.assertIsNotNone(block, 'the document has no limits section')
        for needle in ('Reviewer', '静态', '未验证假设'):
            self.assertIn(needle, block, f'the limits section does not mention {needle!r}')

    def test_document_copies_no_source_text(self):
        self.assertNotIn('```', self.text,
                         'the mapping must not embed code or quoted source from the project')

    def test_document_points_at_the_rule_document(self):
        self.assertIn('agentmgr-contract-rules.md', self.text)

    def test_document_states_that_the_project_was_not_run(self):
        self.assertTrue('没有' in self.text and ('真实数据' in self.text or '端到端' in self.text),
                        'the mapping must state that the project was never run here')


class FrozenContractTests(unittest.TestCase):
    """The rules work on the frozen contract: no new kind, param or status."""

    def setUp(self):
        # Load through the same package path the checker uses: `rules` imports
        # `contract` relatively, so importing the file alone would not work.
        self.contract, self.rules, self.report = runner._arch_modules()

    def test_rule_kinds_equal_the_frozen_schema_enum(self):
        data = json.loads(SCHEMA.read_text(encoding='utf-8'))
        enum = self._find_enum(data, 'NO_CYCLE')
        self.assertIsNotNone(enum, 'the schema no longer declares a rule-kind enum')
        self.assertEqual(sorted(enum), sorted(self.contract.RULE_KINDS),
                         'the checker must implement exactly the frozen rule kinds')

    def test_every_rule_kind_still_declares_its_param_budget(self):
        self.assertEqual(set(self.contract.RULE_PARAMS), set(self.contract.RULE_KINDS),
                         'every frozen kind must keep an explicit (possibly empty) param list')
        for kind in ('DUPLICATE_BUSINESS_RULE', 'CONTRACT_TEST', 'CUSTOM'):
            self.assertIn(kind, self.rules.UNIMPLEMENTED_KINDS,
                          f'{kind} must stay declared-but-unimplemented, not silently guessed')
            self.assertEqual(self.contract.RULE_PARAMS[kind], (),
                             f'{kind} takes no params; a heuristic implementation would be a '
                             'guess the frozen contract does not authorise')

    def test_report_status_vocabulary_is_unchanged(self):
        for status in ('ERROR', 'WARNING', 'UNKNOWN', 'NOT_RUN'):
            self.assertIn(status, self.report.STATUSES)

    def _find_enum(self, node, wanted):
        if isinstance(node, dict):
            for value in node.values():
                found = self._find_enum(value, wanted)
                if found is not None:
                    return found
        elif isinstance(node, list):
            if wanted in node:
                return node
            for item in node:
                found = self._find_enum(item, wanted)
                if found is not None:
                    return found
        return None


class UnconfiguredProjectTests(unittest.TestCase):
    """An existing project without a contract is never failed by inferred rules."""

    def test_this_repository_stays_diagnostic_only(self):
        """The kit itself has no contract: OFF, exit 0, no ERROR and no invention."""
        proc = subprocess.run([sys.executable, str(ARCH_CHECK), '--project', str(ROOT),
                               '--json'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertIsNone(report['contract_path'])
        self.assertEqual(report['enforcement_mode'], 'OFF')
        self.assertTrue(report['diagnostic_only'])
        self.assertFalse(report['blocking'])
        self.assertEqual(report['counts']['ERROR'], 0)
        self.assertEqual(report['invariants'], [])
        self.assertEqual(report['examined_modules'], [])


class SyntheticFixtureTests(unittest.TestCase):
    """The fixtures stay synthetic: no network, no repository state, no project names."""

    SOURCES = tuple(inspect.getsource(getattr(runner, name)) for name in FIXTURE_SOURCES)

    def test_fixtures_do_not_use_the_network(self):
        for source in self.SOURCES:
            for needle in ('http://', 'https://', 'urllib', 'socket'):
                self.assertNotIn(needle, source,
                                 f'a new fixture reaches outside its sandbox: {needle}')

    def test_fixtures_do_not_touch_repository_state(self):
        for source in self.SOURCES:
            for needle in ('subprocess', '.orchestration', '.git'):
                self.assertNotIn(needle, source,
                                 f'a new fixture reaches into repository state: {needle}')

    def test_runner_does_not_embed_the_evidence_source(self):
        source = RUNNER_PATH.read_text(encoding='utf-8')
        self.assertNotIn('AgentMgr', source,
                         'the runner must not embed the evidence source or its data')
        self.assertNotIn('ctrlshiftaltdel', source)

    def test_this_test_file_adds_no_skips(self):
        source = Path(__file__).read_text(encoding='utf-8')
        # Assembled so this guard does not match its own needle list.
        forbidden = ('@unittest.' + 'skip', 'skip' + 'Test', 'expected' + 'Failure')
        for needle in forbidden:
            self.assertNotIn(needle, source,
                             'new tests must not be skipped or marked as expected failures')


if __name__ == '__main__':
    unittest.main()
