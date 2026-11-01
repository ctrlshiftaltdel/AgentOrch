"""The core-contract path move (kit/ -> docs/) through the real update engine.

Formats up to 1.7 shipped the contract at ``kit/orchestration.md``; it now lives at
``docs/orchestration.md``. These are fixed upgrade fixtures: a synthetic old
install, a synthetic new package, and the real classification / backup / rollback
code. They pin the four properties the migration must keep:

1. the old copy is never deleted (no silent loss of local content);
2. the new copy is never written while the old one exists (never two authorities);
3. a divergent local contract is reported, never overwritten (fail closed);
4. after a real migration the update applies, rolls back cleanly, and reruns
   idempotently.

The external release, network and credentials are never involved.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone

from . import support

engine = support.engine
classify = support.classify
package = support.package
migrate = support.migrate

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
LEGACY = 'kit/orchestration.md'
CORE = 'docs/orchestration.md'

AGENTS_15 = b'# AGENTS v1.5\n'
AGENTS_16 = b'# AGENTS v1.6\n'
PACKET_15 = b'# packet v1.5\n'
PACKET_16 = b'# packet v1.6\n'
MASTER_15 = b'# master v1.5\n'
MASTER_16 = b'# master v1.6\n'
CONTRACT_15 = b'# orchestration v1.5 (core contract)\n'
CONTRACT_16 = b'# orchestration v1.6 (core contract, moved to docs/)\n'

OLD_15 = {
    'AGENTS.md': AGENTS_15,
    LEGACY: CONTRACT_15,
    'templates/task-packet.md': PACKET_15,
    'editions/github/master.md': MASTER_15,
}
NEW_16 = {
    'AGENTS.md': AGENTS_16,
    CORE: CONTRACT_16,
    'templates/task-packet.md': PACKET_16,
    'editions/github/master.md': MASTER_16,
}
PROJECT_FILES = {
    'docs/PROJECT.md': b'# synthetic project config\n\n- `edition`: github\n',
    'src/app.py': b'print("project source")\n',
}


def prepare(tmp, *, baseline=None, target=None, write_manifest=True):
    project = support.ProjectTree(tmp)
    baseline = dict(OLD_15 if baseline is None else baseline)
    target = dict(NEW_16 if target is None else target)
    project.install(baseline, format_version='1.5', write_manifest=write_manifest)
    for rel, data in PROJECT_FILES.items():
        if rel not in baseline:
            project.write(rel, data)
    manifest = support.build_manifest(target, format_version='1.6')
    payload = support.make_zip(target, manifest)
    source = support.FakeReleaseSource(support.release_info('v1.6', payload), payload)
    return project, source, target


def run(project, source, mode='APPLY', fault=None):
    return engine.run_update(project.root, mode=mode, source=source, edition='github',
                             now=NOW, fault_injector=fault)


def entry_of(report, path):
    for item in report['classification']['files']:
        if item['path'] == path:
            return item
    raise AssertionError(f'{path} not in classification: '
                         f'{[item["path"] for item in report["classification"]["files"]]}')


class LegacyContractMigrationTests(unittest.TestCase):
    def test_legacy_contract_blocks_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, source, _ = prepare(tmp)
            before = project.snapshot()
            report = run(project, source)

            self.assertEqual(report['result'], 'STOPPED_CONFLICT')
            self.assertEqual(report['human_decisions'][0]['decision_id'], 'HD-09')
            self.assertEqual(support.report.validate_report(report), [])
            item = entry_of(report, LEGACY)
            self.assertEqual(item['class'], 'UPSTREAM_RENAME')
            self.assertEqual(item['decision'], 'KEPT_LOCAL')
            self.assertEqual(project.read(LEGACY), CONTRACT_15)
            self.assertFalse(project.exists(CORE))
            self.assertEqual(project.snapshot(), before)

    def test_locally_edited_legacy_contract_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, source, _ = prepare(tmp)
            edited = b'# locally edited contract\n'
            project.write(LEGACY, edited)
            before = project.snapshot()
            report = run(project, source)

            self.assertEqual(report['result'], 'STOPPED_CONFLICT')
            self.assertEqual(project.read(LEGACY), edited)
            self.assertFalse(project.exists(CORE))
            self.assertEqual(project.snapshot(), before)

    def test_migrated_project_applies_and_reruns_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = {path: data for path, data in OLD_15.items() if path != LEGACY}
            project, source, target = prepare(tmp, baseline=baseline)
            self.assertFalse(project.exists(LEGACY))

            first = run(project, source)
            self.assertEqual(first['result'], 'APPLIED', first['human_decisions'])
            self.assertEqual(support.report.validate_report(first), [])
            self.assertEqual(project.read(CORE), target[CORE])
            self.assertFalse(project.exists(LEGACY))

            # Re-running the very same release is refused by the version guard:
            # nothing is applied twice, and the project does not move.
            before = project.snapshot()
            again = run(project, source)
            self.assertEqual(again['result'], 'BLOCKED')
            self.assertEqual(again['human_decisions'][0]['decision_id'], 'HD-16')
            self.assertEqual(project.snapshot(), before)

            # A later release still sees the contract as already current: the
            # identical bytes are not rewritten, only the genuinely new files are.
            later = dict(target)
            later['AGENTS.md'] = b'# AGENTS v1.7\n'
            manifest = support.build_manifest(later, format_version='1.7')
            payload = support.make_zip(later, manifest)
            next_source = support.FakeReleaseSource(support.release_info('v1.7', payload), payload)
            report = run(project, next_source)
            self.assertEqual(report['result'], 'APPLIED', report['human_decisions'])
            self.assertEqual(entry_of(report, CORE)['class'], 'ALREADY_CURRENT')
            self.assertEqual(entry_of(report, CORE)['decision'], 'NOT_APPLICABLE')
            self.assertEqual(project.read(CORE), CONTRACT_16)
            self.assertFalse(project.exists(LEGACY))

    def test_divergent_local_contract_stops_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = {path: data for path, data in OLD_15.items() if path != LEGACY}
            project, source, _ = prepare(tmp, baseline=baseline)
            local = b'# hand-written contract, not from any kit\n'
            project.write(CORE, local)
            before = project.snapshot()
            report = run(project, source)

            self.assertEqual(report['result'], 'STOPPED_CONFLICT')
            self.assertEqual(report['human_decisions'][0]['decision_id'], 'HD-10')
            self.assertEqual(entry_of(report, CORE)['decision'], 'KEPT_LOCAL')
            self.assertEqual(project.read(CORE), local)
            self.assertEqual(project.snapshot(), before)

    def test_missing_old_manifest_stops_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, source, _ = prepare(tmp, write_manifest=False)
            before = project.snapshot()
            report = run(project, source)

            self.assertEqual(report['result'], 'STOPPED_INVALID_BASELINE')
            self.assertEqual(source.fetch_count, 0)
            self.assertEqual(source.download_count, 0)
            self.assertEqual(project.snapshot(), before)

    def test_rollback_removes_the_new_contract_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = {path: data for path, data in OLD_15.items() if path != LEGACY}
            project, source, _ = prepare(tmp, baseline=baseline)
            before = project.snapshot()
            fault = support.FaultPlan(fail_after_writes=1)
            report = run(project, source, fault=fault)

            self.assertEqual(report['result'], 'ROLLED_BACK')
            self.assertEqual(report['rollback']['result'], 'SUCCEEDED')
            self.assertFalse(project.exists(CORE))
            self.assertEqual(project.snapshot(), before)


class ContractOwnershipTests(unittest.TestCase):
    def test_contract_is_updatable_but_project_docs_are_not(self):
        self.assertTrue(classify.is_project_owned('docs/PROJECT.md'))
        self.assertTrue(classify.is_project_owned('docs/design/notes.md'))
        self.assertFalse(classify.is_project_owned(CORE))

    def test_contract_is_core_in_the_package_manifest(self):
        self.assertEqual(package.classify_upstream(CORE), 'core')
        self.assertEqual(package.classify_upstream('docs/PROJECT.md'), 'project-profile')

    def test_only_the_documented_move_is_recognised(self):
        self.assertEqual(migrate.target_of(LEGACY), CORE)
        self.assertIsNone(migrate.target_of('kit/anything-else.md'))
        self.assertIsNone(migrate.target_of('docs/orchestration-extra.md'))

    def test_classification_keeps_both_sides_of_the_move(self):
        baseline = {'AGENTS.md': 'a' * 64, LEGACY: 'b' * 64}
        current = dict(baseline)
        target = {'AGENTS.md': 'c' * 64, CORE: 'd' * 64}
        result = classify.classify_files(baseline, current, target)

        old = next(item for item in result.files if item['path'] == LEGACY)
        new = next(item for item in result.files if item['path'] == CORE)
        self.assertEqual((old['class'], old['decision']), ('UPSTREAM_RENAME', 'KEPT_LOCAL'))
        self.assertEqual((new['class'], new['decision']), ('UPSTREAM_RENAME', 'KEPT_LOCAL'))
        self.assertEqual(result.renames, [LEGACY])
        self.assertTrue(result.blocked)


if __name__ == '__main__':
    unittest.main()
