"""Continuous regression against the *published* v1.7.0 update classifier.

The bridge release (1.7.1) exists because the published v1.7.0 client refuses to
write ``docs/`` and treats a baseline file that vanished from the target as a
blocking removal. This module imports a byte-for-byte frozen copy of that
classifier and runs it against today's ``REQUIRED`` set, so the compatibility
promise is checked on every run without network access and without a git tag.

The frozen inputs live in ``fixtures/``:

* ``v1_7_0_classify.py`` — exact published file, pinned by SHA-256 below;
* ``v1_7_0_manifest.json`` — the published 1.7.0 ``MANIFEST.json`` path/hash
  set with its asset and manifest digests recorded for provenance.

Negative controls prove the same frozen code still blocks the two failure modes
the bridge release must avoid: deleting ``kit/orchestration.md`` and moving the
contract into ``docs/``.
"""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures'
FROZEN_CLASSIFIER = FIXTURE_DIR / 'v1_7_0_classify.py'
FROZEN_MANIFEST = FIXTURE_DIR / 'v1_7_0_manifest.json'

# SHA-256 of the published v1.7.0 scripts/update_agentorch/classify.py, taken from
# the official AgentOrch-1.7.0.zip asset (asset SHA-256 cbc35fcf...4c10).
PUBLISHED_CLASSIFIER_SHA256 = (
    '95313520e12edc102075cc1dba848cd5f8b513a285125cf7bd38ea54c0ec9ff0')

CORE = 'kit/orchestration.md'
PROJECT_OWNED_CONTRACT = 'docs/orchestration.md'


def load(name, path, *, register=False):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if register:  # dataclasses look their module up in sys.modules
        sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PublishedV170ClassifierCompatibilityTests(unittest.TestCase):
    """The real v1.7.0 classifier must accept this release and keep its gates."""

    @classmethod
    def setUpClass(cls):
        cls.classify = load('published_v170_classify', FROZEN_CLASSIFIER, register=True)
        cls.validate = load('validate_kit_for_v170_compat',
                            ROOT / 'scripts' / 'validate_kit.py')
        cls.drift = load('check_drift_for_v170_compat', ROOT / 'scripts' / 'check_drift.py')
        frozen = json.loads(FROZEN_MANIFEST.read_text(encoding='utf-8'))
        cls.provenance = frozen
        cls.baseline = {entry['path']: entry['sha256'] for entry in frozen['files']}
        cls.target = {}
        for rel in cls.validate.REQUIRED:
            data = cls.drift.normalize_line_endings((ROOT / rel).read_bytes())
            cls.target[rel] = hashlib.sha256(data).hexdigest()

    def entries(self, result):
        return {entry['path']: entry for entry in result.files}

    # -- frozen inputs ----------------------------------------------------- #

    def test_frozen_classifier_is_the_published_file(self):
        digest = hashlib.sha256(FROZEN_CLASSIFIER.read_bytes()).hexdigest()
        self.assertEqual(digest, PUBLISHED_CLASSIFIER_SHA256)
        # The published rule this release depends on: docs/ is project-owned.
        self.assertIn('docs/', self.classify.PROJECT_OWNED_PREFIXES)
        self.assertTrue(self.classify.is_project_owned(PROJECT_OWNED_CONTRACT))
        self.assertFalse(self.classify.is_project_owned(CORE))

    def test_frozen_manifest_records_its_published_provenance(self):
        self.assertEqual(self.provenance['source_asset'], 'AgentOrch-1.7.0.zip')
        self.assertEqual(self.provenance['format_version'], '1.7')
        self.assertEqual(len(self.baseline), 117)
        self.assertIn(CORE, self.baseline)
        self.assertNotIn(PROJECT_OWNED_CONTRACT, self.baseline)
        self.assertFalse(any(path.startswith('docs/') for path in self.target))
        self.assertIn(CORE, self.target)

    # -- positive: today's release classifies cleanly ---------------------- #

    def test_published_classifier_plans_this_release_without_a_block(self):
        result = self.classify.classify_files(
            self.baseline, dict(self.baseline), self.target, mode='PLAN')
        self.assertFalse(result.blocked)
        self.assertEqual(result.removals, [])
        self.assertEqual(result.renames, [])
        self.assertEqual(result.conflicts, [])
        self.assertEqual(result.counts['UPSTREAM_REMOVAL'], 0)
        self.assertEqual(result.counts['UPSTREAM_RENAME'], 0)
        self.assertEqual(result.counts['THREE_WAY_CONFLICT'], 0)
        self.assertEqual(set(self.baseline) - set(self.target), set())
        core = self.entries(result)[CORE]
        self.assertEqual(core['class'], 'UPSTREAM_UNCHANGED')
        self.assertEqual(core['decision'], 'DEFERRED')

    def test_published_classifier_applies_this_release_without_a_block(self):
        result = self.classify.classify_files(
            self.baseline, dict(self.baseline), self.target, mode='APPLY')
        self.assertFalse(result.blocked)
        core = self.entries(result)[CORE]
        self.assertEqual(core['class'], 'UPSTREAM_UNCHANGED')
        self.assertEqual(core['decision'], 'APPLIED')

    # -- negative: the publishable failure modes stay blocked -------------- #

    def test_removing_kit_orchestration_md_is_still_blocked(self):
        target = dict(self.target)
        target.pop(CORE)
        result = self.classify.classify_files(
            self.baseline, dict(self.baseline), target, mode='APPLY')
        self.assertTrue(result.blocked)
        self.assertEqual(result.removals, [CORE])
        entry = self.entries(result)[CORE]
        self.assertEqual(entry['class'], 'UPSTREAM_REMOVAL')
        self.assertEqual(entry['decision'], 'KEPT_LOCAL')

    def test_moving_the_contract_into_docs_is_still_blocked(self):
        target = dict(self.target)
        target.pop(CORE)
        # An upstream rename is byte-identical: the new path carries the same
        # bytes the old baseline path had, so the published rename rule fires.
        target[PROJECT_OWNED_CONTRACT] = self.baseline[CORE]
        result = self.classify.classify_files(
            self.baseline, dict(self.baseline), target, mode='APPLY')
        self.assertTrue(result.blocked)
        self.assertEqual(result.removals, [])
        self.assertEqual(result.renames, [CORE])
        entries = self.entries(result)
        self.assertEqual(entries[CORE]['class'], 'UPSTREAM_RENAME')
        self.assertEqual(entries[PROJECT_OWNED_CONTRACT]['class'], 'PROJECT_OWNED')

    def test_a_project_owned_docs_contract_is_never_applied(self):
        target = dict(self.target)
        target[PROJECT_OWNED_CONTRACT] = hashlib.sha256(b'local contract').hexdigest()
        result = self.classify.classify_files(
            self.baseline, dict(self.baseline), target, mode='APPLY')
        self.assertFalse(result.blocked)
        entry = self.entries(result)[PROJECT_OWNED_CONTRACT]
        self.assertEqual(entry['class'], 'PROJECT_OWNED')
        self.assertEqual(entry['decision'], 'KEPT_LOCAL')
        self.assertNotIn(PROJECT_OWNED_CONTRACT,
                         [item['path'] for item in result.files
                          if item['decision'] == 'APPLIED'])


if __name__ == '__main__':
    unittest.main()
