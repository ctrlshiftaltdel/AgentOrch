"""Tests for the read-only delivery risk classifier.

Every case builds a real temporary Git repository and runs the CLI in a
subprocess with the host's Git configuration isolated. No network, no
third-party imports, no writes to the repository under test.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'delivery_risk.py'
PY = sys.executable

_EXPECTED_KEYS = {'schema', 'base', 'head', 'level', 'full_validation',
                  'independent_review', 'reasons', 'changed_paths', 'version'}


def _git(where, *args, check=True):
    return subprocess.run(['git', '--no-optional-locks', *args], cwd=where,
                          check=check, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class RiskCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        _git(self.repo, 'init', '-q', '-b', 'main')
        _git(self.repo, 'config', 'user.email', 'test@example.invalid')
        _git(self.repo, 'config', 'user.name', 'Test')
        (self.repo / 'base.txt').write_text('base\n')
        (self.repo / 'docs').mkdir()
        (self.repo / 'docs' / 'base.md').write_text('# base\n')
        _git(self.repo, 'add', 'base.txt', 'docs/base.md')
        _git(self.repo, 'commit', '-q', '-m', 'base')
        self.env = dict(os.environ)
        self.env.update({
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_CONFIG_SYSTEM': os.devnull,
            'HOME': str(self.home),
        })
        for name in ('GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_DIR',
                     'GIT_WORK_TREE', 'GIT_COMMON_DIR'):
            self.env.pop(name, None)

    # -- fixtures ---------------------------------------------------------
    def head(self, ref='HEAD'):
        return _git(self.repo, 'rev-parse', ref).stdout.decode().strip()

    def branch(self, name='head'):
        _git(self.repo, 'checkout', '-q', '-b', name, 'main')

    def commit(self, writes=(), deletes=()):
        for name, text in writes:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for name in deletes:
            _git(self.repo, 'rm', '-q', name)
        if writes or deletes:
            _git(self.repo, 'add', '-A')
        _git(self.repo, 'commit', '-q', '--allow-empty', '-m', 'change')

    def classify(self, base='main', head='head'):
        return subprocess.run(
            [PY, str(SCRIPT), '--repo', str(self.repo), '--base', base,
             '--head', head, '--json'],
            cwd=self.repo, env=self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def result(self, base='main', head='head'):
        run = self.classify(base, head)
        self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads(run.stdout)
        self.assertEqual(set(data), _EXPECTED_KEYS)
        return data


class ClassificationTests(RiskCase):
    def test_docs_only_edit_is_l0(self):
        self.branch()
        self.commit([('docs/note.md', 'note\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L0')
        self.assertFalse(data['full_validation'])
        self.assertEqual(data['independent_review'], 'none')
        self.assertEqual(data['changed_paths'], ['docs/note.md'])

    def test_docs_deletion_is_still_l0(self):
        self.branch()
        self.commit(deletes=['docs/base.md'])
        data = self.result()
        self.assertEqual(data['level'], 'L0')

    def test_script_and_test_are_l1(self):
        self.branch()
        self.commit([('scripts/tool.py', 'print(1)\n'),
                     ('tests/test_tool.py', 'pass\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L1')
        self.assertFalse(data['full_validation'])
        self.assertEqual(data['independent_review'], 'batch')

    def test_contract_workflow_and_template_are_l2(self):
        for name in ('AGENTS.md', '.github/workflows/ci.yml',
                     'scripts/batch_delivery.py', 'templates/x.md'):
            with self.subTest(name=name):
                self.setUp()
                self.branch()
                self.commit([(name, 'x\n')])
                data = self.result()
                self.assertEqual(data['level'], 'L2')
                self.assertTrue(data['full_validation'])
                self.assertEqual(data['independent_review'], 'required')

    def test_secret_path_is_l3(self):
        self.branch()
        self.commit([('.env', 'TOKEN=x\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L3')
        self.assertTrue(data['full_validation'])
        self.assertEqual(data['independent_review'], 'required')

    def test_env_example_is_not_a_secret(self):
        self.branch()
        self.commit([('.env.example', 'TOKEN=\n')])
        data = self.result()
        # Not secret-shaped, but also not a recognized rule: fail closed at L2.
        self.assertEqual(data['level'], 'L2')
        self.assertIn('unclassified path: .env.example', data['reasons'])

    def test_case_variant_secret_paths_are_l3(self):
        # A mixed-case spelling of a secret path is the same secret: casing is
        # a platform detail, not a security boundary.
        for name in ('.ENV', '.Env', '.ENV.PRODUCTION', 'Credentials.JSON',
                     'docs/TOKEN.md', 'deploy/Secret/x.txt', 'KEY.PEM'):
            with self.subTest(name=name):
                self.setUp()
                self.branch()
                self.commit([(name, 'TOKEN=x\n')])
                data = self.result()
                self.assertEqual(data['level'], 'L3', data)
                self.assertTrue(data['full_validation'])
                self.assertEqual(data['independent_review'], 'required')
                self.assertIn(f'secret-shaped path: {name}', data['reasons'])

    def test_uppercase_env_example_is_secret(self):
        # Only the exact `.env.example` template is exempt; a look-alike does
        # not inherit the exemption.
        self.branch()
        self.commit([('.ENV.EXAMPLE', 'TOKEN=\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L3', data)
        self.assertIn('secret-shaped path: .ENV.EXAMPLE', data['reasons'])

    def test_exact_case_classifications_are_unchanged(self):
        for name, level in (('README.md', 'L1'), ('docs/guide.md', 'L0'),
                            ('src/main.py', 'L1')):
            with self.subTest(name=name):
                self.setUp()
                self.branch()
                self.commit([(name, 'x\n')])
                data = self.result()
                self.assertEqual(data['level'], level, data)

    def test_non_secret_uppercase_path_is_not_l3(self):
        self.branch()
        self.commit([('UPPER/CASE.TXT', 'x\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L2', data)
        self.assertFalse([reason for reason in data['reasons']
                          if 'secret-shaped' in reason], data)

    def test_ordinary_source_and_config_are_l1(self):
        for name in ('app.py', 'src/main.py', 'package.json', 'Dockerfile'):
            with self.subTest(name=name):
                self.setUp()
                self.branch()
                self.commit([(name, 'x\n')])
                data = self.result()
                self.assertEqual(data['level'], 'L1', data)

    def test_unknown_binary_stays_l2(self):
        self.branch()
        self.commit([('mystery.bin', 'data\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L2')

    def test_non_ascii_secret_path_stays_l3(self):
        self.branch()
        self.commit([('docs/é/.env', 'TOKEN=x\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L3')
        self.assertEqual(data['changed_paths'], ['docs/é/.env'])

    def test_secret_path_with_spaces_stays_l3(self):
        self.branch()
        self.commit([('docs/my secret/.env', 'TOKEN=x\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L3')

    def test_backslash_secret_path_stays_l3(self):
        self.branch()
        self.commit([('foo\\bar/.env', 'TOKEN=x\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L3')

    def test_non_ascii_source_is_l1(self):
        self.branch()
        self.commit([('src/é.py', 'print(1)\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L1')

    def test_high_risk_paths_stay_l2(self):
        for name in ('AGENTS.md', 'scripts/batch_delivery.py'):
            with self.subTest(name=name):
                self.setUp()
                self.branch()
                self.commit([(name, 'x\n')])
                data = self.result()
                self.assertEqual(data['level'], 'L2')

    def test_mixed_levels_take_the_maximum(self):
        self.branch()
        self.commit([('docs/note.md', 'note\n'), ('AGENTS.md', 'contract\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L2')
        self.assertEqual(data['changed_paths'], ['AGENTS.md', 'docs/note.md'])

    def test_unknown_path_is_l2(self):
        self.branch()
        self.commit([('weird.bin', 'data\n')])
        data = self.result()
        self.assertEqual(data['level'], 'L2')
        self.assertIn('unclassified path: weird.bin', data['reasons'])

    def test_empty_diff_is_l0(self):
        data = self.result(base='main', head='main')
        self.assertEqual(data['level'], 'L0')
        self.assertEqual(data['reasons'], ['empty diff'])
        self.assertEqual(data['changed_paths'], [])

    def test_license_deletion_is_l3(self):
        (self.repo / 'LICENSE.md').write_text('licence\n')
        _git(self.repo, 'add', 'LICENSE.md')
        _git(self.repo, 'commit', '-q', '-m', 'add licence')
        self.branch()
        self.commit(deletes=['LICENSE.md'])
        data = self.result()
        self.assertEqual(data['level'], 'L3')

    def test_schema_and_output_are_stable(self):
        self.branch()
        self.commit([('docs/b.md', 'b\n'), ('docs/a.md', 'a\n')])
        data = self.result()
        self.assertEqual(data['schema'], 'delivery-risk/1')
        self.assertEqual(data['version'], '250')
        self.assertEqual(data['changed_paths'], ['docs/a.md', 'docs/b.md'])
        self.assertEqual(data['base'], self.head('main'))
        self.assertEqual(data['head'], self.head('head'))
        # The human-readable form also names the level.
        text = subprocess.run(
            [PY, str(SCRIPT), '--repo', str(self.repo), '--base', 'main',
             '--head', 'head'], env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertTrue(text.stdout.startswith('LEVEL L0\n'))

    def test_classification_writes_nothing(self):
        self.branch()
        self.commit([('docs/note.md', 'note\n')])
        self.result()
        status = _git(self.repo, 'status', '--porcelain').stdout
        self.assertEqual(status, b'')


class InputErrorTests(RiskCase):
    def test_unknown_ref_exits_2(self):
        run = self.classify(base='nope', head='main')
        self.assertEqual(run.returncode, 2)
        self.assertIn('unknown ref', run.stderr)

    def test_no_merge_base_exits_2(self):
        _git(self.repo, 'checkout', '-q', '--orphan', 'orphan')
        _git(self.repo, 'rm', '-q', '-rf', '.')
        (self.repo / 'orphan.txt').write_text('x\n')
        _git(self.repo, 'add', 'orphan.txt')
        _git(self.repo, 'commit', '-q', '-m', 'orphan')
        run = self.classify(base='main', head='orphan')
        self.assertEqual(run.returncode, 2)
        self.assertIn('no merge base', run.stderr)

    def test_non_repository_is_invalid(self):
        outside = self.root / 'outside'
        outside.mkdir()
        run = subprocess.run(
            [PY, str(SCRIPT), '--repo', str(outside), '--base', 'main',
             '--head', 'main'], env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.assertEqual(run.returncode, 2)


if __name__ == '__main__':
    unittest.main()
