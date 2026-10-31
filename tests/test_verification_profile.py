"""Tests for the post-merge verification profile (Issue #188).

The default fixpoint is the full post-merge set: quick test, full regression,
backup and static check. Only the exact ``verification_profile: risk_pilot``
value plus caller-supplied L0 proof may drop the full regression. Every test
below either proves that reduction or proves that a missing / non-L0 / risky
input keeps the full set. No test executes the listed commands; the module is a
read-only decision helper.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import verification_profile as profile  # noqa: E402

UNITTEST = 'python -m unittest discover -s tests'
VALIDATE_KIT = 'python scripts/validate_kit.py'
BACKUP = 'python scripts/orchestrator.py backup'
STATIC = 'python -m py_compile scripts/orchestrator.py scripts/validate_kit.py'


def make_evidence(**overrides):
    document = {
        'schema_version': 'verification-l0-evidence/1',
        'risk_level': 'L0',
        'changed_paths': ['docs/README.md'],
        'executable_doc': False,
        'policy': False,
        'contract': False,
        'uncertainty_l3': False,
    }
    document.update(overrides)
    return document


class ProjectFixture(unittest.TestCase):
    """A throwaway project root with its own docs/PROJECT.md."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / 'docs').mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write_project(self, text):
        (self.root / 'docs' / 'PROJECT.md').write_text(text, encoding='utf-8')

    def write_profile(self, value):
        self.write_project(
            '# Project\n\n- `edition`: `github`\n'
            f'- `verification_profile`: `{value}`\n')

    def command_ids(self, document):
        return [item['id'] for item in document['commands']]

    def command_strings(self, document):
        return [item['command'] for item in document['commands']]


class RepositoryConfigTests(unittest.TestCase):
    def test_repository_declares_the_risk_pilot_profile(self):
        self.assertEqual(profile.read_profile(ROOT), 'risk_pilot')

    def test_script_is_part_of_the_shipped_manifest(self):
        from scripts import validate_kit

        self.assertIn('scripts/verification_profile.py', validate_kit.REQUIRED)


class ProvenL0Tests(ProjectFixture):
    """Positive coverage: only a proven L0 change may drop the full regression."""

    def test_proven_l0_keeps_validate_kit_backup_and_static(self):
        self.write_profile('risk_pilot')
        document = profile.resolve(self.root, make_evidence())

        self.assertEqual(document['decision'], 'reduced')
        self.assertEqual(document['reasons'], ['risk_pilot_l0_proven'])
        self.assertEqual(self.command_ids(document), ['quick', 'backup', 'static'])
        self.assertEqual(
            set(self.command_strings(document)), {VALIDATE_KIT, BACKUP, STATIC})
        self.assertNotIn(UNITTEST, self.command_strings(document))


class ProfileFallbackTests(ProjectFixture):
    """Negative coverage: anything but the exact opt-in keeps the full set."""

    def assert_full(self, document, reason=None):
        self.assertEqual(document['decision'], 'full')
        self.assertEqual(
            self.command_ids(document),
            ['quick', 'full_regression', 'backup', 'static'])
        self.assertIn(UNITTEST, self.command_strings(document))
        if reason is not None:
            self.assertIn(reason, document['reasons'])

    def test_absent_profile_keeps_full_regression(self):
        self.write_project('# Project\n\n- `edition`: `github`\n')
        self.assert_full(profile.resolve(self.root, make_evidence()), 'profile_absent')

    def test_missing_project_file_keeps_full_regression(self):
        self.assert_full(profile.resolve(self.root, make_evidence()), 'profile_absent')

    def test_strict_profile_keeps_full_regression(self):
        self.write_profile('strict')
        self.assert_full(profile.resolve(self.root, make_evidence()),
                         'profile_not_risk_pilot')

    def test_any_other_profile_value_keeps_full_regression(self):
        for value in ('risk-pilot', 'Risk_Pilot', 'l0', 'full'):
            with self.subTest(value=value):
                self.write_profile(value)
                self.assert_full(profile.resolve(self.root, make_evidence()),
                                 'profile_not_risk_pilot')

    def test_duplicate_profile_key_keeps_full_regression(self):
        self.write_project(
            '# Project\n\n- `edition`: `github`\n'
            '- `verification_profile`: `risk_pilot`\n'
            '- `verification_profile`: `strict`\n')
        self.assert_full(profile.resolve(self.root, make_evidence()), 'profile_absent')


class EvidenceFallbackTests(ProjectFixture):
    """Negative coverage for the caller proof itself."""

    def setUp(self):
        super().setUp()
        self.write_profile('risk_pilot')

    def assert_full(self, document, reason=None):
        self.assertEqual(document['decision'], 'full')
        self.assertEqual(
            [item['id'] for item in document['commands']],
            ['quick', 'full_regression', 'backup', 'static'])
        self.assertIn(UNITTEST, [item['command'] for item in document['commands']])
        if reason is not None:
            self.assertIn(reason, document['reasons'])

    def test_missing_evidence_keeps_full_regression(self):
        self.assert_full(profile.resolve(self.root, None), 'evidence_missing')

    def test_non_l0_risk_level_keeps_full_regression(self):
        for level in ('L1', 'L2', 'L3', 'l0', None):
            with self.subTest(level=level):
                self.assert_full(
                    profile.resolve(self.root, make_evidence(risk_level=level)),
                    'risk_level_not_l0')

    def test_missing_marker_keeps_full_regression(self):
        for marker in profile.REQUIRED_FALSE_MARKERS:
            with self.subTest(marker=marker):
                document = make_evidence()
                del document[marker]
                self.assert_full(profile.resolve(self.root, document),
                                 f'marker_missing:{marker}')

    def test_true_semantic_marker_keeps_full_regression(self):
        markers = profile.REQUIRED_FALSE_MARKERS + profile.OPTIONAL_FALSE_MARKERS
        for marker in markers:
            with self.subTest(marker=marker):
                self.assert_full(
                    profile.resolve(self.root, make_evidence(**{marker: True})),
                    f'marker_true:{marker}')

    def test_non_boolean_marker_keeps_full_regression(self):
        for value in (0, 'false', None):
            with self.subTest(value=value):
                self.assert_full(
                    profile.resolve(self.root, make_evidence(policy=value)),
                    'marker_invalid:policy')

    def test_empty_changed_paths_keep_full_regression(self):
        self.assert_full(
            profile.resolve(self.root, make_evidence(changed_paths=[])),
            'changed_paths_missing')

    def test_restricted_paths_keep_full_regression(self):
        restricted = (
            'scripts/orchestrator.py',
            'docs/orchestration.md',
            '.github/workflows/ci.yml',
            'docs/PROJECT.md',
            'docs/PLAN.md',
            'AGENTS.md',
            'editions/github/master.md',
            'scripts/validate_kit.py',
            'release/README.md',
            'templates/plan.md',
            'VERSION.md',
        )
        for path in restricted:
            with self.subTest(path=path):
                document = profile.resolve(
                    self.root, make_evidence(changed_paths=[path]))
                self.assert_full(document, 'changed_path_restricted')
                self.assertEqual(document['restricted_paths'], [path])

    def test_invalid_paths_keep_full_regression(self):
        for path in ('../outside.md', '/etc/passwd', 'C:/tmp/file.md', ''):
            with self.subTest(path=path):
                self.assert_full(
                    profile.resolve(self.root, make_evidence(changed_paths=[path])),
                    'changed_path_invalid')


class CommandLineTests(ProjectFixture):
    """The CLI is read-only, deterministic and fails closed on broken input."""

    def setUp(self):
        super().setUp()
        self.write_profile('risk_pilot')
        self.evidence_path = self.root / 'evidence.json'

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'verification_profile.py'),
             '--project', str(self.root), *args],
            capture_output=True, text=True, cwd=str(ROOT))

    def test_json_decision_uses_the_current_profile(self):
        self.evidence_path.write_text(json.dumps(make_evidence()), encoding='utf-8')
        proc = self.run_cli('--evidence', str(self.evidence_path), '--json')

        self.assertEqual(proc.returncode, profile.EXIT_OK, proc.stderr)
        document = json.loads(proc.stdout)
        self.assertEqual(document['decision'], 'reduced')
        self.assertEqual([item['id'] for item in document['commands']],
                         ['quick', 'backup', 'static'])

    def test_plain_output_lists_commands_without_the_full_regression(self):
        self.evidence_path.write_text(json.dumps(make_evidence()), encoding='utf-8')
        proc = self.run_cli('--evidence', str(self.evidence_path))

        self.assertEqual(proc.returncode, profile.EXIT_OK, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), [VALIDATE_KIT, BACKUP, STATIC])

    def test_without_evidence_the_cli_prints_the_full_set(self):
        proc = self.run_cli()

        self.assertEqual(proc.returncode, profile.EXIT_OK, proc.stderr)
        self.assertEqual(
            proc.stdout.splitlines(), [VALIDATE_KIT, UNITTEST, BACKUP, STATIC])

    def test_broken_evidence_fails_closed(self):
        self.evidence_path.write_text('{not json', encoding='utf-8')
        proc = self.run_cli('--evidence', str(self.evidence_path), '--json')

        self.assertEqual(proc.returncode, profile.EXIT_INVALID)
        self.assertIn('error', json.loads(proc.stdout))
        self.assertNotIn('unittest', proc.stdout)

    def test_non_object_evidence_fails_closed(self):
        self.evidence_path.write_text('[]', encoding='utf-8')
        proc = self.run_cli('--evidence', str(self.evidence_path))

        self.assertEqual(proc.returncode, profile.EXIT_INVALID)
        self.assertIn('JSON object', proc.stdout)


if __name__ == '__main__':
    unittest.main()
