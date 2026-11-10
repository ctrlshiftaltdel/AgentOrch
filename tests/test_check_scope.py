"""Tests for the read-only G2 scope check (`scripts/check_scope.py`).

The tool answers one question: are every changed path (committed since a base
SHA, plus working-tree edits, plus untracked files) inside the allowed paths of
one active task in the local task database? Exit codes mirror
`scripts/check_drift.py`: 0 in scope, 2 out of scope, 1 undeterminable.

Every test builds its own throwaway Git repository and task database; the real
`.orchestration/` database is never opened for writing and the repository's
Git configuration is never touched.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
# Import the module the way a reviewer does: `from scripts.orchestrator import
# ACTIVE` has to resolve, so the repository root must be importable.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import check_scope as check  # noqa: E402
from scripts import orchestrator  # noqa: E402

NEVER = '0' * 40


class GitFixture:
    """A minimal repository with a real task database built by orchestrator.init."""

    def __init__(self, tmp):
        self.root = Path(tmp).resolve()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Scope Test')
        self.git('config', 'user.email', 'scope@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.write('.gitignore', '.orchestration/\n')
        self.write('docs/PROJECT.md', '# Project\n\n- `edition`: `local`\n')
        self.base = self.commit('base')

    # --- git plumbing -------------------------------------------------------
    def git(self, *args, check_ok=True):
        return subprocess.run(['git', '-C', str(self.root), *args],
                              capture_output=True, text=True, check=check_ok)

    def commit(self, message='change'):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD').stdout.strip()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def remove(self, rel):
        (self.root / rel).unlink()

    # --- task database ------------------------------------------------------
    @property
    def db_path(self):
        return self.root / '.orchestration' / 'state.sqlite3'

    def init_db(self):
        with contextlib.redirect_stdout(io.StringIO()):
            orchestrator.init(self.root)
        return self

    def add_task(self, task_id, paths, status='DISPATCHED', head_sha='', approved_sha=''):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                'INSERT INTO tasks(id,title,description,paths_json,depends_json,'
                'acceptance_json,status,head_sha,approved_sha,created_at,updated_at) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (task_id, task_id, '', json.dumps(paths), '[]', '["works"]', status,
                 head_sha, approved_sha, '2026-10-27T00:00:00+00:00',
                 '2026-10-27T00:00:00+00:00'))
            con.commit()
        finally:
            con.close()
        return self

    # --- snapshots ----------------------------------------------------------
    def tree_snapshot(self):
        return sorted(p.relative_to(self.root).as_posix()
                      for p in self.root.rglob('*') if p.is_file())

    def db_digest(self):
        return hashlib.sha256(self.db_path.read_bytes()).hexdigest()


class CheckScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = GitFixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # --- helpers ------------------------------------------------------------
    def run_tool(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check.main(['--root', str(self.fx.root), *args])
        return code, out.getvalue(), err.getvalue()

    def run_json(self, *args):
        code, out, err = self.run_tool('--json', *args)
        return code, json.loads(out), err

    # --- AC-1 / AC-2: base resolution and exit codes ------------------------
    def test_explicit_base_with_in_scope_change_returns_zero(self):
        self.fx.init_db()
        self.fx.write('src/app.py', 'print("ok")\n')
        self.fx.commit('add app')
        self.fx.write('src/app.py', 'print("ok")\nprint("more")\n')
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        code, report, _ = self.run_json('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['status'], 'in-scope')
        self.assertEqual(report['base']['source'], '--base')
        self.assertIn('src/app.py', report['in_scope'])
        self.assertEqual(report['out_of_scope'], [])

    def test_out_of_scope_change_returns_two(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        self.fx.write('docs/notes.md', 'unrelated\n')
        code, report, _ = self.run_json('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 2)
        self.assertEqual(report['status'], 'out-of-scope')
        self.assertEqual(report['out_of_scope'], ['docs/notes.md'])
        self.assertEqual(report['exit_code'], 2)

    def test_missing_base_everywhere_returns_one(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'])
        self.fx.write('src/app.py', 'x\n')
        code, out, err = self.run_tool('--task', 'T-1')
        self.assertEqual(code, 1)
        self.assertIn('pass --base', err)

    def test_base_falls_back_to_approved_sha_then_head_sha(self):
        self.fx.init_db()
        self.fx.write('src/app.py', 'one\n')
        approved = self.fx.commit('app v1')
        self.fx.write('src/app.py', 'one\ntwo\n')
        head = self.fx.commit('app v2')
        self.fx.write('src/app.py', 'one\ntwo\nthree\n')

        self.fx.add_task('T-APPROVED', ['src/'], status='APPROVED',
                         head_sha=head, approved_sha=approved)
        code, report, _ = self.run_json('--task', 'T-APPROVED')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['base']['sha'], approved)
        self.assertEqual(report['base']['source'], 'approved_sha')

        self.fx.add_task('T-HEAD', ['src/'], head_sha=approved)
        code, report, _ = self.run_json('--task', 'T-HEAD')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['base']['sha'], approved)
        self.assertEqual(report['base']['source'], 'head_sha')

    def test_base_that_is_not_a_full_sha_returns_one(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        code, out, err = self.run_tool('--task', 'T-1', '--base', 'deadbeef')
        self.assertEqual(code, 1)
        self.assertIn('40-hex', err)

        for bad in ('main', self.fx.base[:39], self.fx.base + 'a', 'g' * 40):
            code, _, err = self.run_tool('--task', 'T-1', '--base', bad)
            self.assertEqual(code, 1, bad)
            self.assertIn('40-hex', err)

    def test_base_sha_absent_from_the_repository_returns_one(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        code, out, err = self.run_tool('--task', 'T-1', '--base', NEVER)
        self.assertEqual(code, 1)
        self.assertIn('not a commit', err)

    def test_invalid_arguments_return_one_instead_of_argparse_two(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = check.main(['--root', str(self.fx.root), '--nonsense'])
        self.assertEqual(code, 1)

    # --- AC-3: activity set comes from scripts.orchestrator.ACTIVE -----------
    def test_active_set_is_the_orchestrator_object(self):
        self.assertIs(check.ACTIVE, orchestrator.ACTIVE)
        for status in ('DISPATCHED', 'IN_PROGRESS', 'REVIEW_READY', 'MASTER_REVIEW',
                       'APPROVED', 'MERGED', 'BLOCKED', 'NEEDS_HUMAN_DECISION', 'FAILED'):
            self.assertIn(status, check.ACTIVE)
        for status in ('PLANNED', 'READY', 'VERIFIED', 'CANCELED'):
            self.assertNotIn(status, check.ACTIVE)

    def test_approved_only_task_is_still_active(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], status='APPROVED',
                         head_sha=self.fx.base, approved_sha=self.fx.base)
        self.fx.write('src/app.py', 'a\nb\n')
        code, report, _ = self.run_json()
        self.assertEqual(code, 0, report)
        self.assertEqual(report['task']['id'], 'T-1')
        self.assertEqual(report['task']['status'], 'APPROVED')

    def test_no_active_task_returns_one_instead_of_silent_zero(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], status='VERIFIED',
                         head_sha=self.fx.base, approved_sha=self.fx.base)
        self.fx.write('src/app.py', 'x\n')
        code, out, err = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn('no active task', err)

    def test_multiple_active_tasks_require_an_explicit_task(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        self.fx.add_task('T-2', ['docs/'], status='BLOCKED', head_sha=self.fx.base)
        self.fx.write('src/app.py', 'x\n')

        code, out, err = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn('multiple active tasks', err)
        self.assertIn('T-1', err)
        self.assertIn('T-2', err)

        code, report, _ = self.run_json('--task', 'T-1')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['task']['id'], 'T-1')

    def test_unknown_task_returns_one(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        code, out, err = self.run_tool('--task', 'T-NOPE')
        self.assertEqual(code, 1)
        self.assertIn('unknown task', err)

    # --- AC-4: coverage of the change collection ----------------------------
    def test_parent_directory_prefix_overlap(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        self.fx.write('src/deep/nested/mod.py', 'x\n')
        code, report, _ = self.run_json('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 0, report)
        self.assertIn('src/deep/nested/mod.py', report['in_scope'])

    def test_sibling_file_outside_a_single_file_path_is_out_of_scope(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/core.py'], head_sha=self.fx.base)
        self.fx.write('src/other.py', 'x\n')
        code, report, _ = self.run_json('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 2, report)
        self.assertEqual(report['out_of_scope'], ['src/other.py'])

    def test_untracked_files_are_changes(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        self.fx.write('src/new.py', 'x\n')
        self.fx.write('notes.txt', 'x\n')
        code, report, _ = self.run_json('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 2, report)
        self.assertIn('src/new.py', report['in_scope'])
        self.assertEqual(report['out_of_scope'], ['notes.txt'])
        sources = {item['path']: item['sources'] for item in report['changes']}
        self.assertEqual(sources['notes.txt'], ['untracked'])

    def test_deleted_files_are_changes(self):
        self.fx.write('src/old.py', 'x\n')
        self.fx.commit('add old')
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        self.fx.remove('src/old.py')
        code, report, _ = self.run_json('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 0, report)
        self.assertIn('src/old.py', report['in_scope'])
        sources = {item['path']: item['sources'] for item in report['changes']}
        self.assertIn('worktree', sources['src/old.py'])

        self.fx.write('docs/gone.md', 'x\n')
        self.fx.commit('add gone')
        self.fx.remove('docs/gone.md')
        code, report, _ = self.run_json('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 2, report)
        self.assertEqual(report['out_of_scope'], ['docs/gone.md'])

    def test_rename_reports_both_sides(self):
        self.fx.write('src/a.py', 'x\n')
        self.fx.commit('add a')
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        (self.fx.root / 'other').mkdir()
        self.fx.git('mv', 'src/a.py', 'other/a.py')
        code, report, _ = self.run_json('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 2, report)
        self.assertIn('src/a.py', report['in_scope'])
        self.assertEqual(report['out_of_scope'], ['other/a.py'])

    def test_task_database_missing_returns_one(self):
        self.fx.write('src/app.py', 'x\n')
        code, out, err = self.run_tool('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 1)
        self.assertIn('task database', err)

    def test_task_database_corrupt_returns_one(self):
        self.fx.init_db()
        self.fx.db_path.write_bytes(b'not a sqlite database at all')
        code, out, err = self.run_tool('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 1)
        self.assertIn('task database', err)

    def test_sqlite_error_text_is_never_forwarded(self):
        # A view named ``tasks`` that selects from a missing table makes SQLite
        # report the table name in its error. That text is schema content and
        # must not reach the report.
        self.fx.init_db()
        marker = 'secret_schema_marker'
        con = sqlite3.connect(self.fx.db_path)
        try:
            con.execute('DROP TABLE tasks')
            con.execute(
                "CREATE VIEW tasks AS SELECT 'T-1' AS id, 'DISPATCHED' AS status, "
                "'t' AS title, '[\"src/\"]' AS paths_json, '' AS approved_sha, "
                f"'' AS head_sha FROM {marker}")
            con.commit()
        finally:
            con.close()
        code, out, err = self.run_tool('--task', 'T-1', '--base', self.fx.base,
                                       '--json')
        self.assertEqual(code, 1)
        self.assertNotIn(marker, out)
        self.assertNotIn(marker, err)
        self.assertEqual(json.loads(out)['status'], 'error')

    def test_unreadable_allowed_paths_reports_a_fixed_diagnostic(self):
        # Neither the task id nor the JSON parser text may appear in the error:
        # both come from database content.
        self.fx.init_db()
        marker = 'T-SECRET-PATHS-MARKER'
        self.fx.add_task(marker, ['src/'], head_sha=self.fx.base)
        con = sqlite3.connect(self.fx.db_path)
        try:
            con.execute('UPDATE tasks SET paths_json=? WHERE id=?',
                        ('not-json', marker))
            con.commit()
        finally:
            con.close()
        code, out, err = self.run_tool('--task', marker, '--base', self.fx.base,
                                       '--json')
        self.assertEqual(code, 1)
        self.assertNotIn(marker, out)
        self.assertNotIn(marker, err)
        self.assertEqual(json.loads(out)['status'], 'error')

    def test_mixed_database_and_wal_snapshot_is_refused(self):
        # A controlled replacement can leave the database from one commit state
        # next to a write-ahead log from another. Each file verifies on its own,
        # but the pair is not one snapshot: the database alone says ``docs/``
        # (out of scope) while the WAL frames rewrite the same row to ``src/``
        # (in scope). A read-only check cannot prove the two belong together, so
        # it must answer error/1 and leave both source files untouched.
        # The stores live outside the fixture repository, so their files are
        # not themselves untracked changes in the report.
        stores = Path(tempfile.mkdtemp(prefix='scope-mixed-'))
        self.addCleanup(shutil.rmtree, stores, True)
        base = stores / 'base.sqlite3'
        create_task_db(base, [('T-1', 'DISPATCHED', ['docs/'], self.fx.base)])
        wal_store = stores / 'wal.sqlite3'
        checkpointed = stores / 'checkpointed.sqlite3'
        shutil.copyfile(base, wal_store)
        shutil.copyfile(base, checkpointed)

        con = sqlite3.connect(wal_store)
        try:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('PRAGMA wal_autocheckpoint=0')
            con.execute('UPDATE tasks SET paths_json=? WHERE id=?',
                        (json.dumps(['src/']), 'T-1'))
            con.commit()
            wal_path = wal_store.with_name(wal_store.name + '-wal')
            self.assertTrue(wal_path.is_file(), 'the fixture must keep a live WAL')
            wal_bytes = wal_path.read_bytes()
        finally:
            con.close()

        self.fx.db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checkpointed, self.fx.db_path)
        source_wal = self.fx.db_path.with_name(self.fx.db_path.name + '-wal')
        source_wal.write_bytes(wal_bytes)
        self.fx.write('src/app.py', 'x\n')

        db_before = self.fx.db_path.read_bytes()
        code, report, _ = self.run_json('--task', 'T-1', '--base', self.fx.base)
        self.assertEqual(code, 1, report)
        self.assertEqual(report['status'], 'error')
        self.assertIn('write-ahead log', report['error'])
        self.assertEqual(report['in_scope'], [])
        self.assertEqual(report['exit_code'], 1)
        self.assertEqual(self.fx.db_path.read_bytes(), db_before)
        self.assertEqual(source_wal.read_bytes(), wal_bytes)
        self.assertFalse(
            self.fx.db_path.with_name(self.fx.db_path.name + '-shm').exists())

    def test_root_outside_a_git_work_tree_returns_one(self):
        outside = Path(self.tmp.name) / 'plain'
        outside.mkdir()
        (outside / '.orchestration').mkdir()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check.main(['--root', str(outside), '--base', self.fx.base])
        self.assertEqual(code, 1)
        self.assertIn('git', err.getvalue().lower())

    # --- AC-5: read-only, no hooks, no .git/config changes -------------------
    def test_run_never_writes_or_touches_git_configuration(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        self.fx.write('src/app.py', 'x\n')

        config_path = self.fx.root / '.git' / 'config'
        config_before = config_path.read_bytes()
        hooks_before = sorted(p.name for p in (self.fx.root / '.git' / 'hooks').iterdir())
        hooks_path_before = self.fx.git('config', '--get', 'core.hooksPath', check_ok=False)
        tree_before = self.fx.tree_snapshot()
        status_before = self.fx.git('status', '--porcelain').stdout
        digest_before = self.fx.db_digest()

        self.assertEqual(self.run_tool('--task', 'T-1')[0], 0)
        self.assertEqual(self.run_tool('--task', 'T-1', '--base', 'bad')[0], 1)

        self.assertEqual(config_path.read_bytes(), config_before)
        self.assertEqual(sorted(p.name for p in (self.fx.root / '.git' / 'hooks').iterdir()),
                         hooks_before)
        hooks_path_after = self.fx.git('config', '--get', 'core.hooksPath', check_ok=False)
        self.assertEqual(hooks_path_after.returncode, hooks_path_before.returncode)
        self.assertNotEqual(hooks_path_before.returncode, 0,
                            'the fixture repository must not define core.hooksPath')
        self.assertEqual(hooks_path_after.stdout, '')
        self.assertEqual(self.fx.tree_snapshot(), tree_before)
        self.assertEqual(self.fx.git('status', '--porcelain').stdout, status_before)
        self.assertEqual(self.fx.db_digest(), digest_before)

    # --- integration: a task created by the orchestrator CLI -----------------
    def test_task_created_through_the_orchestrator_api_is_readable(self):
        self.fx.init_db()
        args = SimpleNamespace(id='TASK-0001', title='scope', description='',
                               paths=['src/'], depends=[], acceptance=['works'])
        with contextlib.redirect_stdout(io.StringIO()):
            orchestrator.task_add(self.fx.root, args)
            orchestrator.task_ready(self.fx.root, 'TASK-0001')
            orchestrator.task_dispatch(self.fx.root, 'TASK-0001')
        self.fx.write('src/app.py', 'x\n')
        code, report, _ = self.run_json('--base', self.fx.base)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['task']['id'], 'TASK-0001')
        self.assertEqual(report['task']['status'], 'DISPATCHED')
        self.assertEqual(report['task']['allowed_paths'], ['src'])

    # --- AC-6 (isolated layout): default cwd and environment hardening --------
    def test_flat_default_cwd_still_reads_the_current_directory(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        self.fx.write('src/app.py', 'x\n')
        previous = os.getcwd()
        os.chdir(self.fx.root)
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = check.main(['--task', 'T-1', '--json'])
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0, err.getvalue())
        report = json.loads(out.getvalue())
        self.assertEqual(report['root'], str(self.fx.root))
        self.assertEqual(report['layout'], 'flat')

    def test_flat_explicit_root_still_must_be_the_work_tree_top_level(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        subdir = self.fx.root / 'src'
        subdir.mkdir(exist_ok=True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check.main(['--root', str(subdir), '--task', 'T-1',
                               '--base', self.fx.base])
        self.assertEqual(code, 1)
        self.assertIn('top level', err.getvalue())

    def test_git_environment_redirection_is_ignored(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        self.fx.write('src/app.py', 'x\n')

        with tempfile.TemporaryDirectory() as other:
            decoy = GitFixture(other)
            decoy.write('decoy-only.txt', 'x\n')
            saved = {name: os.environ.get(name) for name in ('GIT_DIR', 'GIT_WORK_TREE')}
            os.environ['GIT_DIR'] = str(decoy.root / '.git')
            os.environ['GIT_WORK_TREE'] = str(decoy.root)
            try:
                code, report, err = self.run_json('--task', 'T-1')
            finally:
                for name, value in saved.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
        self.assertEqual(code, 0, (report, err))
        self.assertEqual(report['root'], str(self.fx.root))
        self.assertIn('src/app.py', report['in_scope'])
        self.assertNotIn('decoy-only.txt', [item['path'] for item in report['changes']])

    def test_repository_configured_external_program_is_not_run(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        self.fx.write('src/app.py', 'x\n')

        with tempfile.TemporaryDirectory() as tools:
            marker = Path(tools) / 'fsmonitor-ran'
            script = Path(tools) / 'fsmonitor.sh'
            script.write_text(f'#!/bin/sh\necho ran >> "{marker}"\nexit 0\n',
                              encoding='utf-8')
            os.chmod(script, 0o755)
            self.fx.git('config', 'core.fsmonitor', str(script))

            code, out, err = self.run_tool('--task', 'T-1')
            self.assertEqual(code, 0, err)
            self.assertFalse(
                marker.exists(),
                'check_scope must not let repository config run an external program')

    def test_git_stderr_and_file_contents_are_never_forwarded(self):
        self.fx.init_db()
        self.fx.add_task('T-1', ['src/'], head_sha=self.fx.base)
        secret = 'SECRET-FILE-CONTENT-MUST-NOT-LEAK'
        bindir = Path(self.tmp.name) / 'fake-bin'
        bindir.mkdir()
        fake = bindir / 'git'
        fake.write_text(f'#!/bin/sh\necho "{secret}" >&2\nexit 128\n', encoding='utf-8')
        os.chmod(fake, 0o755)
        # Windows resolves git via PATHEXT; provide the same fake there.
        fake.with_name('git.bat').write_text(
            f'@echo off\r\necho {secret} 1>&2\r\nexit /b 1\r\n', encoding='utf-8')

        previous = os.environ.get('PATH', '')
        os.environ['PATH'] = str(bindir) + os.pathsep + previous
        try:
            code, out, err = self.run_tool('--task', 'T-1', '--json')
        finally:
            os.environ['PATH'] = previous
        self.assertEqual(code, 1)
        self.assertNotIn(secret, out)
        self.assertNotIn(secret, err)
        payload = json.loads(out)
        self.assertEqual(payload['status'], 'error')


# --- isolated layout ---------------------------------------------------------

AGENTORCH = 'agentorch'
ISOLATED_SCRIPTS = ('check_scope.py', 'orchestrator.py')
ISOLATED_DB = Path(AGENTORCH) / '.local' / 'orchestration' / 'state.sqlite3'


def _git(root, *args, check_ok=True):
    return subprocess.run(['git', '-C', str(root), *args],
                          capture_output=True, text=True, check=check_ok)


def _write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def _commit(root, message):
    _git(root, 'add', '-A')
    _git(root, 'commit', '-q', '-m', message)
    return _git(root, 'rev-parse', 'HEAD').stdout.strip()


def build_host_repo(root):
    """A host project with its own files, ignore rules and base commit."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    _git(root, 'init', '-q')
    _git(root, 'config', 'user.name', 'Scope Test')
    _git(root, 'config', 'user.email', 'scope@example.invalid')
    _git(root, 'config', 'commit.gpgsign', 'false')
    _write(root, '.gitignore', '.orchestration/\n')
    _write(root, 'AGENTS.md', '# Host rules\n')
    _write(root, 'src/app.py', 'v1\n')
    return _commit(root, 'host base')


def build_isolated_host(tmp, linked=False):
    """Host H plus ``H/agentorch`` K, optionally as a Git linked worktree."""
    tmp = Path(tmp)
    if linked:
        main = tmp / 'main'
        build_host_repo(main)
        host = tmp / 'host'
        _git(main, 'worktree', 'add', '-q', '-b', 'task/wt', str(host))
    else:
        main = None
        host = tmp / 'host'
        build_host_repo(host)
    kit = host / AGENTORCH
    (kit / 'scripts').mkdir(parents=True)
    for name in ISOLATED_SCRIPTS:
        shutil.copyfile(ROOT / 'scripts' / name, kit / 'scripts' / name)
    _write(kit, 'VERSION.md', '# Agent-Orchestration Starter Kit\n\n- Format version: 1.7\n')
    _write(kit, 'MANIFEST.json', '{"schema_version": 1}\n')
    _write(kit, '.gitignore', '.local/\n__pycache__/\n')
    scaffold = _commit(host, 'install agentorch')
    return main, host, kit, scaffold


def create_task_db(path, tasks):
    """A minimal task table; check_scope reads exactly these columns."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute(
            'CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT \'\', '
            'paths_json TEXT NOT NULL, status TEXT NOT NULL, '
            'approved_sha TEXT NOT NULL DEFAULT \'\', '
            'head_sha TEXT NOT NULL DEFAULT \'\')')
        for task_id, status, paths, head_sha in tasks:
            con.execute(
                'INSERT INTO tasks(id,title,paths_json,status,approved_sha,head_sha) '
                'VALUES(?,?,?,?,?,?)',
                (task_id, task_id, json.dumps(paths), status, '', head_sha))
        con.commit()
    finally:
        con.close()


def _insert_task(con, task_id, paths, status, head_sha):
    con.execute(
        'INSERT INTO tasks(id,title,description,paths_json,depends_json,'
        'acceptance_json,status,head_sha,approved_sha,created_at,updated_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (task_id, task_id, '', json.dumps(paths), '[]', '["works"]', status,
         head_sha, '', '2026-10-27T00:00:00+00:00', '2026-10-27T00:00:00+00:00'))


def init_isolated_store(kit, tasks):
    """Build the real task schema under ``<kit>/.local/orchestration``."""
    state = Path(kit) / '.local' / 'orchestration'
    with contextlib.redirect_stdout(io.StringIO()):
        orchestrator.init(Path(kit), state)
    con = sqlite3.connect(state / 'state.sqlite3')
    try:
        for task_id, status, paths, head_sha in tasks:
            _insert_task(con, task_id, paths, status, head_sha)
        con.commit()
    finally:
        con.close()
    return state / 'state.sqlite3'


def add_host_decoy(host, base, task_id='T-HOST', paths=('docs/',)):
    """A permissive flat registry next to the host, which must never be read."""
    create_task_db(Path(host) / '.orchestration' / 'state.sqlite3',
                   [(task_id, 'DISPATCHED', list(paths), base)])


def run_isolated(kit, args, cwd):
    """Run the shipped script by path, so ``__file__`` decides the layout."""
    env = os.environ.copy()
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'PYTHONPATH'):
        env.pop(name, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run(
        [sys.executable, '-B', str(Path(kit) / 'scripts' / 'check_scope.py'), *args],
        cwd=str(cwd), capture_output=True, text=True, env=env)


def _outside(tmp):
    path = Path(tmp) / 'outside'
    path.mkdir(exist_ok=True)
    return path


def _dir_listing(path):
    return sorted(item.name for item in Path(path).iterdir())


def _file_digests(path):
    return {item.name: hashlib.sha256(item.read_bytes()).hexdigest()
            for item in Path(path).iterdir() if item.is_file()}


class IsolatedLayoutTest(unittest.TestCase):
    """Git root H and task-store root K are separate; neither may be guessed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_linked_worktree_uses_host_root_and_agentorch_state(self):
        main, host, kit, scaffold = build_isolated_host(self.base, linked=True)
        # A linked worktree has a .git *file*; the tool must not need a .git dir.
        self.assertTrue((host / '.git').is_file())
        init_isolated_store(kit, [('T-1', 'DISPATCHED', ['src/'], scaffold)])

        (host / 'src' / 'app.py').write_text('v2\n', encoding='utf-8')
        (host / 'docs').mkdir()
        (host / 'docs' / 'notes.md').write_text('out of scope\n', encoding='utf-8')
        # The main worktree must not contribute changes to this report.
        (main / 'src' / 'main_only.py').write_text('decoy\n', encoding='utf-8')

        args = ['--task', 'T-1', '--base', scaffold, '--json']
        proc = run_isolated(kit, args, _outside(self.base))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report['status'], 'out-of-scope')
        self.assertEqual(report['layout'], 'isolated')
        self.assertEqual(report['task_database'],
                         'agentorch/.local/orchestration/state.sqlite3')
        self.assertEqual(report['root'], str(host.resolve()))
        self.assertEqual(report['task']['id'], 'T-1')
        self.assertIn('src/app.py', report['in_scope'])
        self.assertEqual(report['out_of_scope'], ['docs/notes.md'])
        paths = [item['path'] for item in report['changes']]
        self.assertNotIn('src/main_only.py', paths)

        explicit = run_isolated(kit, [*args, '--root', str(host)],
                                _outside(self.base))
        self.assertEqual(explicit.returncode, 2, explicit.stderr)

    def test_explicit_root_outside_the_host_is_refused(self):
        main, host, kit, scaffold = build_isolated_host(self.base, linked=True)
        init_isolated_store(kit, [('T-1', 'DISPATCHED', ['src/'], scaffold)])
        add_host_decoy(main, scaffold, task_id='T-MAIN', paths=('.',))

        proc = run_isolated(kit, ['--json', '--root', str(main)],
                            _outside(self.base))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertNotIn('in-scope', proc.stdout)
        self.assertIn('host work-tree top level', proc.stderr)

    def test_agentorch_database_wins_over_a_host_database_decoy(self):
        _, host, kit, scaffold = build_isolated_host(self.base)
        init_isolated_store(kit, [('T-KIT', 'DISPATCHED', ['src/'], scaffold)])
        add_host_decoy(host, scaffold)
        (host / 'docs').mkdir()
        (host / 'docs' / 'notes.md').write_text('outside src\n', encoding='utf-8')

        proc = run_isolated(kit, ['--json'], _outside(self.base))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report['task']['id'], 'T-KIT')
        self.assertEqual(report['out_of_scope'], ['docs/notes.md'])

    def test_missing_agentorch_database_never_falls_back_to_host_database(self):
        _, host, kit, scaffold = build_isolated_host(self.base)
        add_host_decoy(host, scaffold)
        (host / 'docs').mkdir()
        (host / 'docs' / 'notes.md').write_text('outside src\n', encoding='utf-8')

        proc = run_isolated(kit, ['--json'], _outside(self.base))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload['status'], 'error')
        self.assertIn('task database', payload['error'])
        self.assertNotIn('in-scope', proc.stdout)

    def test_symlinked_agentorch_database_is_refused(self):
        _, host, kit, scaffold = build_isolated_host(self.base)
        real = self.base / 'real-store' / 'state.sqlite3'
        create_task_db(real, [('T-REAL', 'DISPATCHED', ['.'], scaffold)])
        state = kit / '.local' / 'orchestration'
        state.mkdir(parents=True)
        os.symlink(real, state / 'state.sqlite3')
        add_host_decoy(host, scaffold)
        (host / 'docs').mkdir()
        (host / 'docs' / 'notes.md').write_text('outside src\n', encoding='utf-8')

        proc = run_isolated(kit, ['--json'], _outside(self.base))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn('symlink', proc.stderr.lower())
        self.assertNotIn('in-scope', proc.stdout)
        self.assertNotIn('T-REAL', proc.stdout)

    def test_hardlinked_agentorch_database_is_refused(self):
        _, host, kit, scaffold = build_isolated_host(self.base)
        real = self.base / 'real-store' / 'state.sqlite3'
        create_task_db(real, [('T-REAL', 'DISPATCHED', ['.'], scaffold)])
        state = kit / '.local' / 'orchestration'
        state.mkdir(parents=True)
        os.link(real, state / 'state.sqlite3')
        add_host_decoy(host, scaffold)
        (host / 'docs').mkdir()
        (host / 'docs' / 'notes.md').write_text('outside src\n', encoding='utf-8')

        proc = run_isolated(kit, ['--json'], _outside(self.base))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn('hard-linked', proc.stderr)
        self.assertNotIn('in-scope', proc.stdout)

    def test_agentorch_shaped_directory_without_markers_is_refused(self):
        _, host, kit, scaffold = build_isolated_host(self.base)
        init_isolated_store(kit, [('T-KIT', 'DISPATCHED', ['src/'], scaffold)])
        for name in ('VERSION.md', 'MANIFEST.json'):
            (kit / name).unlink()
        add_host_decoy(host, scaffold)
        (host / 'docs').mkdir()
        (host / 'docs' / 'notes.md').write_text('outside src\n', encoding='utf-8')

        proc = run_isolated(kit, ['--json'], _outside(self.base))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn('isolated layout', proc.stderr.lower())
        self.assertNotIn('in-scope', proc.stdout)

    def test_corrupt_agentorch_database_is_refused(self):
        _, host, kit, scaffold = build_isolated_host(self.base)
        db = init_isolated_store(kit, [('T-KIT', 'DISPATCHED', ['src/'], scaffold)])
        for sidecar in ('state.sqlite3-wal', 'state.sqlite3-shm'):
            (db.parent / sidecar).unlink(missing_ok=True)
        db.write_bytes(b'not a sqlite database at all')
        add_host_decoy(host, scaffold)
        (host / 'src' / 'app.py').write_text('v2\n', encoding='utf-8')

        proc = run_isolated(kit, ['--json'], _outside(self.base))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn('task database', proc.stderr)
        self.assertNotIn('in-scope', proc.stdout)

    def test_live_wal_is_refused_because_one_snapshot_cannot_be_proven(self):
        # Copying the database and its WAL separately cannot prove both files
        # belong to one commit state; a controlled replacement could produce a
        # mixed snapshot. The check must fail closed instead of replaying it.
        _, _, kit, scaffold = build_isolated_host(self.base)
        state = init_isolated_store(
            kit, [('T-BASE', 'DISPATCHED', ['src/'], scaffold)]).parent
        con = sqlite3.connect(state / 'state.sqlite3')
        try:
            con.execute('PRAGMA wal_autocheckpoint=0')
            _insert_task(con, 'T-WAL', ['src/'], 'DISPATCHED', scaffold)
            con.commit()
            self.assertTrue((state / 'state.sqlite3-wal').is_file(),
                            'the fixture must keep a live WAL')
            digests = _file_digests(state)
            proc = run_isolated(kit, ['--json', '--task', 'T-WAL'],
                                _outside(self.base))
            self.assertEqual(proc.returncode, 1, proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload['status'], 'error')
            self.assertIn('write-ahead log', payload['error'])
            self.assertNotIn('in-scope', proc.stdout)
            self.assertEqual(_file_digests(state), digests)
        finally:
            con.close()

    def test_isolated_run_leaves_the_source_store_unchanged(self):
        _, host, kit, scaffold = build_isolated_host(self.base)
        db = init_isolated_store(kit, [('T-1', 'DISPATCHED', ['src/'], scaffold)])
        state = db.parent
        listing = _dir_listing(state)
        digests = _file_digests(state)
        (host / 'src' / 'app.py').write_text('v2\n', encoding='utf-8')

        proc = run_isolated(kit, ['--json', '--task', 'T-1'],
                            _outside(self.base))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(_dir_listing(state), listing)
        self.assertEqual(_file_digests(state), digests)
        self.assertFalse((state / 'state.sqlite3-shm').exists())


if __name__ == '__main__':
    unittest.main()
