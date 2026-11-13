"""Continuous regression against the *published* v1.7.0 update classifier.

The bridge release (1.7.1) exists because the published v1.7.0 client refuses to
write ``docs/`` and treats a baseline file that vanished from the target as a
blocking removal. This module imports a byte-for-byte frozen copy of that
classifier and runs it against today's ``REQUIRED`` set, so the compatibility
promise is checked on every run without network access and without a git tag.

The frozen inputs live in ``fixtures/``:

* ``v1_7_0_classify.py`` — exact published file, pinned by SHA-256 below;
* ``v1_7_0_manifest.json`` — the published 1.7.0 ``MANIFEST.json`` path/hash
  set. Its content is audited against a digest pinned **in this test module**,
  not against the file's own ``source_manifest_sha256`` declaration.

Negative controls prove the same frozen code still blocks the two failure modes
the bridge release must avoid: deleting ``kit/orchestration.md`` and moving the
contract into ``docs/``. A separate tampering test proves that changing one
fixture entry breaks the audited digest.
"""
import copy
import hashlib
import importlib.util
import json
import re
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
# The published 1.7.0 asset and the MANIFEST.json bytes it contains.
PUBLISHED_ASSET_SHA256 = (
    'cbc35fcf5ddf2aa8c7c799e52c7f5fa83ec8943a7691e1053155cc85988a4c10')
PUBLISHED_MANIFEST_SHA256 = (
    'd07addc8b8a0eb153454af9ac0792d6785de7b1d6915cd3bf82b0a11f2aba4b1')
# Independent audit of the frozen fixture content. Recomputed from the entries as
# sha256("path\0sha256\0size\n" for each path in sorted order); it is pinned here
# so a changed fixture fails even if its own ``source_manifest_sha256`` field is
# rewritten to match. Reproduce with:
#   python3 -c "import hashlib,json; p=json.load(open('<fixture>')); print(hashlib.sha256(''.join(f\"{e['path']}\\0{e['sha256']}\\0{e['size']}\\n\" for e in sorted(p['files'], key=lambda e: e['path'])).encode()).hexdigest())"
AUDITED_MANIFEST_DIGEST = (
    '11e3609c4f799912a51a3f8c76afd86cbbce08c617ee3d5c88eb2cbb474063a1')

SHA256_RE = re.compile(r'^[0-9a-f]{64}$')

CORE = 'kit/orchestration.md'
PROJECT_OWNED_CONTRACT = 'docs/orchestration.md'


def semantic_manifest_digest(entries):
    """Independent digest of a path/sha256/size list (not of the file bytes)."""
    lines = []
    for entry in sorted(entries, key=lambda item: item['path']):
        lines.append(f"{entry['path']}\0{entry['sha256']}\0{entry['size']}\n")
    return hashlib.sha256(''.join(lines).encode('utf-8')).hexdigest()


def audit_frozen_manifest(payload):
    """Return every reason the frozen fixture is not the audited v1.7.0 content."""
    errors = []
    if not isinstance(payload, dict):
        return ['fixture is not a JSON object']
    if payload.get('source_asset_sha256') != PUBLISHED_ASSET_SHA256:
        errors.append('fixture does not declare the published 1.7.0 asset digest')
    if payload.get('source_manifest_sha256') != PUBLISHED_MANIFEST_SHA256:
        errors.append('fixture does not declare the published 1.7.0 manifest digest')
    entries = payload.get('files')
    if not isinstance(entries, list) or not entries:
        return errors + ['fixture carries no file entries']
    paths = []
    complete = True
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append('fixture entry is not an object')
            complete = False
            continue
        path = entry.get('path')
        digest = entry.get('sha256')
        size = entry.get('size')
        if not isinstance(path, str) or not path:
            errors.append('fixture entry has no path')
            complete = False
            continue
        paths.append(path)
        if not isinstance(digest, str) or not SHA256_RE.match(digest):
            errors.append(f'{path}: fixture sha256 is not 64 lowercase hex digits')
            complete = False
        if type(size) is not int or size < 1:
            errors.append(f'{path}: fixture size is not a positive integer')
            complete = False
    if len(paths) != len(set(paths)):
        errors.append('fixture lists a path more than once')
        complete = False
    if complete and semantic_manifest_digest(entries) != AUDITED_MANIFEST_DIGEST:
        errors.append('fixture content does not match the audited v1.7.0 manifest digest')
    return errors


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

    def test_frozen_manifest_matches_the_audited_digest(self):
        """The fixture content is checked against a digest pinned in this module."""
        self.assertEqual(audit_frozen_manifest(self.provenance), [])
        self.assertEqual(semantic_manifest_digest(self.provenance['files']),
                         AUDITED_MANIFEST_DIGEST)
        # The audit must not merely echo the fixture's own declaration.
        self.assertNotEqual(AUDITED_MANIFEST_DIGEST,
                            self.provenance['source_manifest_sha256'])

    def test_tampering_one_manifest_entry_fails_the_audit(self):
        tampered = copy.deepcopy(self.provenance)
        tampered['files'][0]['sha256'] = '0' * 64
        # Rewriting the file's own declaration must not rescue the tampered entry.
        tampered['source_manifest_sha256'] = semantic_manifest_digest(tampered['files'])
        errors = audit_frozen_manifest(tampered)
        self.assertTrue(any('audited v1.7.0 manifest digest' in error for error in errors),
                        errors)
        self.assertNotEqual(semantic_manifest_digest(tampered['files']),
                            AUDITED_MANIFEST_DIGEST)

    def test_tampering_one_manifest_size_fails_the_audit(self):
        tampered = copy.deepcopy(self.provenance)
        tampered['files'][0]['size'] = tampered['files'][0]['size'] + 1
        tampered['source_manifest_sha256'] = semantic_manifest_digest(tampered['files'])
        errors = audit_frozen_manifest(tampered)
        self.assertTrue(any('audited v1.7.0 manifest digest' in error for error in errors),
                        errors)

    def test_malformed_fixture_entry_is_reported_without_crashing(self):
        tampered = copy.deepcopy(self.provenance)
        del tampered['files'][0]['size']
        errors = audit_frozen_manifest(tampered)
        self.assertTrue(any('size is not a positive integer' in error for error in errors),
                        errors)

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
