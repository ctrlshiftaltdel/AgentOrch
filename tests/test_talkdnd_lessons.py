"""Synthetic regression tests for the cross-module state / recovery rules (#55).

The rule document is `docs/self-improvement/cross-module-state-and-recovery.md`.
It generalises what a downstream project's public PRs exposed; nothing from that
project is copied here. Every fixture used by the scenarios is invented inside a
temporary directory, and the assertions below check three things the document
cannot check by itself:

  * the scenarios behind each rule really run and really assert *final state*
    (not just "the call raised");
  * the rule document and the scenario catalogue stay in sync (a rule that
    points at a scenario id that does not exist fails here);
  * the task packet template and the review checklist keep carrying the
    cross-module checks, because a deleted checklist line silently removes the
    question nobody will then ask.

These tests do **not** prove that a real project's shared-state call sites were
all found. Static analysis cannot establish that, so it stays an explicit
Reviewer item in the rule document.
"""
import importlib.util
import inspect
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / 'scripts' / 'run_delivery_scenarios.py'
RULE_DOC = ROOT / 'docs' / 'self-improvement' / 'cross-module-state-and-recovery.md'
TASK_PACKET = ROOT / 'templates' / 'task-packet.md'
REVIEW_CHECKLIST = ROOT / 'templates' / 'review-checklist.md'

_spec = importlib.util.spec_from_file_location('talkdnd_lessons_runner', RUNNER_PATH)
runner = importlib.util.module_from_spec(_spec)
# Register before exec: the runner uses `from __future__ import annotations`.
sys.modules[_spec.name] = runner
_spec.loader.exec_module(runner)

SCENARIO_DOC = ROOT / runner.SCENARIO_DOC

# The five negative families the Issue names, plus one positive control.
NEW_SCENARIOS = ('C6', 'C7', 'C8', 'C9', 'C10', 'C11')
NEGATIVE_SCENARIOS = ('C7', 'C8', 'C9', 'C10', 'C11')

# Public evidence links only. Copying source text or business data would need a
# different, reviewed fixture; this list is what the document is allowed to cite.
EVIDENCE_LINKS = {
    'https://github.com/ctrlshiftaltdel/TalkDND/pull/124',
    'https://github.com/ctrlshiftaltdel/TalkDND/pull/150',
    'https://github.com/ctrlshiftaltdel/TalkDND/pull/151',
    'https://github.com/ctrlshiftaltdel/TalkDND/pull/158',
    'https://github.com/ctrlshiftaltdel/TalkDND/pull/160',
}


def section(text, heading_contains):
    """Markdown block under the first heading containing `heading_contains`.

    Stops at the next top-level `## ` heading so a checklist block cannot be
    satisfied by a phrase somewhere else in the file.
    """
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


class RegistryTests(unittest.TestCase):
    """The new scenarios exist, are wired, and declare the right polarity."""

    def test_new_scenarios_are_registered(self):
        ids = runner.scenario_ids()
        for scenario_id in NEW_SCENARIOS:
            self.assertIn(scenario_id, ids, f'{scenario_id} is not registered in the runner')

    def test_new_scenarios_have_one_positive_control_and_five_negatives(self):
        by_id = {item.id: item for item in runner.SCENARIOS}
        self.assertEqual(by_id['C6'].kind, 'positive',
                         'C6 must be the positive control for the new family')
        for scenario_id in NEGATIVE_SCENARIOS:
            self.assertEqual(by_id[scenario_id].kind, 'negative',
                             f'{scenario_id} must be an injected-defect scenario')

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


class ScenarioBehaviourTests(unittest.TestCase):
    """Each rule is actually exercised, and the assertion is on the final state."""

    @classmethod
    def setUpClass(cls):
        cls.workdir = tempfile.TemporaryDirectory()
        cls.vk = runner._load_module('talkdnd_lessons_validate_kit',
                                     runner.SCRIPTS / 'validate_kit.py')
        cls.orch = runner._load_module('talkdnd_lessons_orchestrator',
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

    def test_shared_call_site_omission_is_proven_by_a_lost_update(self):
        evidence = self.evidence('C7')
        # The point is the corrupted final state, not merely that a call was odd.
        self.assertIn('lost update', evidence)
        self.assertIn('count=1', evidence)
        self.assertIn('unguarded', evidence)
        self.assertIn('update_autosave', evidence)

    def test_rollback_residue_is_proven_by_the_file_tree(self):
        evidence = self.evidence('C8')
        self.assertIn('residue', evidence)
        # The corrected rollback must end on a tree identical to the baseline.
        self.assertIn('tree matches baseline: True', evidence)

    def test_lock_lifecycle_loss_is_proven_by_lost_mutual_exclusion(self):
        evidence = self.evidence('C9')
        self.assertIn('lock file deleted by rollback', evidence)
        self.assertIn('LOCK_HELD', evidence)
        self.assertIn('second writer acquired the lock: True', evidence)

    def test_partial_commit_report_cannot_claim_a_complete_rollback(self):
        evidence = self.evidence('C10')
        self.assertIn('failed stage', evidence)
        self.assertIn('rollback', evidence)
        self.assertIn('side effects', evidence)
        self.assertIn('complete rollback', evidence)

    def test_swallowed_error_is_proven_by_a_contradictory_success_report(self):
        evidence = self.evidence('C11')
        self.assertIn('skipped', evidence)
        self.assertIn('broken.json', evidence)
        self.assertIn('surface', evidence)

    def test_positive_control_reaches_the_clean_final_state(self):
        evidence = self.evidence('C6')
        self.assertIn('tree matches baseline: True', evidence)
        self.assertIn('lock released', evidence)
        self.assertIn('0 findings', evidence)

    def test_counts_keep_one_positive_and_five_negatives(self):
        report = runner.build_report(list(self.results.values()))
        totals = report['totals']
        self.assertEqual(totals['positive'], 1)
        self.assertEqual(totals['negative'], 5)
        self.assertEqual(totals['negative_passed'], totals['negative'],
                         'negative scenarios only pass when the interception really happened')
        self.assertEqual(totals['errors'], 0)

    def test_the_new_scenarios_clean_up_their_sandboxes(self):
        leftovers = list(Path(self.workdir.name).iterdir())
        self.assertEqual(leftovers, [], f'sandboxes were not cleaned: {leftovers}')


class RuleDocumentTests(unittest.TestCase):
    """The rule document is project-agnostic, citable and mapped to real scenarios."""

    def setUp(self):
        self.assertTrue(RULE_DOC.is_file(), f'missing rule document: {RULE_DOC}')
        self.text = RULE_DOC.read_text(encoding='utf-8')

    def test_document_refers_to_the_core_contract_instead_of_restating_it(self):
        self.assertIn('docs/orchestration.md', self.text)
        self.assertIn('4.1', self.text)
        # It must say, in so many words, that it adds checkpoints only.
        self.assertTrue('不复制' in self.text or 'only references' in self.text.lower(),
                        'the document must state that it references, not restates, the '
                        'state machine and the evidence matrix')

    def test_document_does_not_redefine_the_state_machine(self):
        # A copied state machine would show the transition chain itself.
        self.assertNotIn('PLANNED → READY → DISPATCHED', self.text)
        self.assertNotIn('PLANNED -> READY -> DISPATCHED', self.text)

    def test_document_cites_only_public_evidence_links(self):
        links = set(re.findall(r'https?://[^\s)>]+', self.text))
        self.assertEqual(links, EVIDENCE_LINKS,
                         'the document must cite exactly the public PR links, nothing copied')

    def test_every_rule_maps_to_a_registered_scenario(self):
        referenced = set(re.findall(r'\bC\d{1,2}\b', self.text))
        self.assertTrue(referenced, 'the mapping table references no scenario id')
        self.assertLessEqual(referenced, set(runner.scenario_ids()),
                             'the document references a scenario the runner does not define')
        self.assertLessEqual(set(NEW_SCENARIOS), referenced,
                             'the document does not reference every new scenario')

    def test_document_names_the_rules_and_their_limits(self):
        for rule_id in ('R1', 'R2', 'R3', 'R4', 'R5', 'R6'):
            self.assertIn(rule_id, self.text, f'missing rule {rule_id}')
        self.assertIn('Reviewer', self.text,
                      'the document must say which judgements stay with the reviewer')
        self.assertIn('合成', self.text, 'the document must state that fixtures are synthetic')

    def test_document_is_referenced_from_the_scenario_catalogue(self):
        catalogue = SCENARIO_DOC.read_text(encoding='utf-8')
        self.assertIn('cross-module-state-and-recovery.md', catalogue)


class TemplateTests(unittest.TestCase):
    """The task packet and the review checklist keep asking the cross-module questions."""

    STRUCTURED_FIELDS = ('invariants', 'negative_cases', 'migration_cases',
                         'integration_paths', 'unverified_assumptions')

    def test_task_packet_carries_the_five_structured_fields(self):
        text = TASK_PACKET.read_text(encoding='utf-8')
        for name in self.STRUCTURED_FIELDS:
            self.assertIn(f'`{name}`', text,
                          f'task packet template no longer carries `{name}`')
        self.assertIn('N/A: <reason>', text,
                      'the small-task escape hatch must keep requiring a reason')

    def test_task_packet_requires_a_call_site_inventory_for_shared_state(self):
        text = TASK_PACKET.read_text(encoding='utf-8')
        block = section(text, 'cross-module') or section(text, 'call-site') or ''
        self.assertTrue(block, 'the task packet has no cross-module / call-site section')
        lowered = block.lower()
        for needle in ('call site', 'read-modify-write', 'invariants',
                       'integration_paths', 'n/a: <reason>'):
            self.assertIn(needle, lowered,
                          f'the cross-module packet section is missing {needle!r}')

    def test_review_checklist_has_a_cross_module_block(self):
        text = REVIEW_CHECKLIST.read_text(encoding='utf-8')
        block = section(text, 'Cross-module state')
        self.assertIsNotNone(block, 'the review checklist has no cross-module block')
        lowered = block.lower()
        for needle in ('call site', 'final state', 'rollback', 'lock',
                       'invariants', 'negative_cases', 'migration_cases',
                       'integration_paths', 'reviewer'):
            self.assertIn(needle, lowered,
                          f'the review checklist cross-module block is missing {needle!r}')

    def test_review_checklist_materially_keeps_the_partial_failure_rule(self):
        text = REVIEW_CHECKLIST.read_text(encoding='utf-8')
        lowered = text.lower()
        self.assertIn('partial', lowered)
        self.assertIn('swallow', lowered)


class SyntheticFixtureTests(unittest.TestCase):
    """The fixtures must stay synthetic: no network, no repo reads, no source copy."""

    SOURCES = tuple(inspect.getsource(getattr(runner, name)) for name in (
        'scenario_c6', 'scenario_c7', 'scenario_c8', 'scenario_c9', 'scenario_c10',
        'scenario_c11', '_bump_manifest', '_call_site_findings', '_restore_tree',
        '_tree_digest', '_stage_report', '_scan_entries',
    ))

    def test_fixtures_do_not_use_the_network_or_the_repository(self):
        for source in self.SOURCES:
            for needle in ('http://', 'https://', 'urllib', 'socket', 'ROOT'):
                self.assertNotIn(needle, source,
                                 f'a new fixture reaches outside its sandbox: {needle}')

    def test_fixtures_do_not_name_the_evidence_source(self):
        source = RUNNER_PATH.read_text(encoding='utf-8')
        self.assertNotIn('ctrlshiftaltdel', source,
                         'the runner must not embed the evidence source or its data')

    def test_scenarios_do_not_git_or_touch_the_state_store(self):
        for source in self.SOURCES:
            for needle in ('subprocess', '.orchestration'):
                self.assertNotIn(needle, source,
                                 f'a new scenario reaches into repository state: {needle}')


if __name__ == '__main__':
    unittest.main()
