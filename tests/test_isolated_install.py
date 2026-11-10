"""Isolated onboarding for an existing project (Issue #153).

Every case is synthetic: the host project is copied from
``tests/fixtures/agentorch_isolated_install/host`` and the AgentOrch source tree
is rebuilt in a temporary directory. Nothing here touches a real downstream
project, the real ``.local`` directory or the network.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_agentorch import workspace  # noqa: E402

FIXTURE_HOST = ROOT / 'tests' / 'fixtures' / 'agentorch_isolated_install' / 'host'

SUITE = 'Agent-Orchestration Starter Kit'
FORMAT_VERSION = '1.7'

# Minimal synthetic AgentOrch source tree. It deliberately mirrors the shipped
# link shapes: root README -> AGENTS.md, docs/ -> ../AGENTS.md,
# editions/<edition>/ -> ../../AGENTS.md, templates/ -> ../AGENTS.md.
SOURCE_FILES = {
    'VERSION.md': (f'# {SUITE}\n\n- Editions: local, github\n'
                   f'- Format version: {FORMAT_VERSION}\n'),
    'AGENTS.md': '# AgentOrch contract\n\nSee [core](docs/orchestration.md).\n',
    'README.md': '# AgentOrch\n\nRules: [AGENTS.md](AGENTS.md). '
                 'Core: [orchestration](docs/orchestration.md).\n',
    'LICENSE.md': 'MIT\n',
    'docs/orchestration.md': '# Core contract\n\nRead [AGENTS.md](../AGENTS.md).\n',
    'editions/local/master.md': 'Local master\n\nSee [AGENTS.md](../../AGENTS.md).\n',
    'editions/github/master.md': 'GitHub master\n\nSee [AGENTS.md](../../AGENTS.md).\n',
    'templates/runtime-capability.md': (
        'Capability record\n\nRules: [AGENTS.md](../AGENTS.md).\n'),
    'scripts/orchestrator.py': '"""placeholder"""\n',
}

PROFILE_PATHS = (
    'ORCHESTRATION-PROJECT.md', 'docs/PROJECT.md', 'PLAN.md', 'docs/PLAN.md',
    'DESIGN.md', 'docs/DESIGN.md',
)


def _normalized(data: bytes) -> bytes:
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        return data
    if b'\x00' in data:
        return data
    return data.replace(b'\r\n', b'\n')


def _sha(data: bytes) -> str:
    return hashlib.sha256(_normalized(data)).hexdigest()


def _category(path: str) -> str:
    if path.startswith('editions/'):
        return 'edition'
    if path.startswith('templates/'):
        return 'template'
    if path in PROFILE_PATHS:
        return 'project-profile'
    return 'core'


def make_source(root: Path, *, version: str = FORMAT_VERSION,
                overrides: dict[str, str] | None = None) -> Path:
    """Create a synthetic AgentOrch source root with a valid MANIFEST.json."""
    root.mkdir(parents=True, exist_ok=True)
    files = dict(SOURCE_FILES)
    files['VERSION.md'] = (f'# {SUITE}\n\n- Editions: local, github\n'
                           f'- Format version: {version}\n')
    files.update(overrides or {})
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8', newline='\n')
    entries = []
    for rel in sorted(files):
        data = _normalized((root / rel).read_bytes())
        entries.append({
            'path': rel,
            'category': _category(rel),
            'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data),
        })
    manifest = {
        'schema_version': 1,
        'suite': SUITE,
        'format_version': version,
        'files': entries,
    }
    (root / 'MANIFEST.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + '\n',
        encoding='utf-8', newline='\n')
    return root


def copy_host(root: Path) -> Path:
    shutil.copytree(FIXTURE_HOST, root)
    return root


def verified_package(source: Path):
    """Build a synthetic release archive and run the real ten-check verifier.

    This is a test-only transport substitute: the production entry obtains the
    same object from the fixed official GitHub client.
    """
    import io
    import zipfile
    from scripts.update_agentorch import package, sources
    manifest_bytes = (source / 'MANIFEST.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    files = {entry['path']: (source / entry['path']).read_bytes()
             for entry in manifest['files']}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for rel, data in files.items():
            archive.writestr('agentorch/' + rel, data)
        archive.writestr('agentorch/MANIFEST.json', manifest_bytes)
    payload = buffer.getvalue()
    tag = 'v' + manifest['format_version']
    asset = sources.ReleaseAsset(
        'agentorch.zip', len(payload),
        'sha256:' + hashlib.sha256(payload).hexdigest(),
        f'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/{tag}/agentorch.zip')
    release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, tag, (asset,))
    result = package.verify_package(release, payload, expected_suite=SUITE)
    if not result.ok:
        raise AssertionError(f'synthetic package did not verify: {result.failed_check}')
    return result


@contextlib.contextmanager
def observe_path_reads():
    """Record every ``Path`` read and every successful raw ``os.open`` in the block.

    A candidate baseline read is only proven rejected when the external canary
    path never appears here, so tests patch the read entry points themselves
    instead of trusting a status string. ``os.open`` is recorded too because the
    hardened byte reader opens descriptors directly; patching only ``Path``
    methods would miss a followed symlink. A relative ``os.open`` with
    ``dir_fd`` is resolved against the directory descriptor's real path.
    """
    from unittest.mock import patch
    reads: list[Path] = []
    original_bytes = Path.read_bytes
    original_text = Path.read_text
    original_open = os.open

    def tracked_bytes(path):
        reads.append(path)
        return original_bytes(path)

    def tracked_text(path, *args, **kwargs):
        reads.append(path)
        return original_text(path, *args, **kwargs)

    def directory_fd_path(directory_fd):
        """The real directory a ``dir_fd`` was opened on, or ``None``.

        ``os.readlink`` on the descriptor's proc entry is how the kernel reports
        the path behind the descriptor; a relative open plus ``dir_fd`` must be
        attributed to that directory or an external canary stays invisible.
        """
        for base in ('/proc/self/fd', '/dev/fd'):
            try:
                return Path(os.readlink(f'{base}/{directory_fd}'))
            except OSError:
                continue
        return None

    def tracked_open(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        try:
            decoded = os.fsdecode(path)
            recorded = None
            directory_fd = kwargs.get('dir_fd')
            if directory_fd is not None and not os.path.isabs(decoded):
                directory = directory_fd_path(directory_fd)
                if directory is not None:
                    recorded = directory / decoded
            if recorded is None:
                recorded = Path(decoded)
            reads.append(recorded)
        except (TypeError, ValueError):
            pass
        return descriptor

    with patch.object(Path, 'read_bytes', tracked_bytes), \
         patch.object(Path, 'read_text', tracked_text), \
         patch.object(os, 'open', tracked_open):
        yield reads


def raw_snapshot(root: Path, *, skip_names=()) -> dict[str, str]:
    """Raw SHA-256 of every file, skipping the named top-level entries."""
    root = Path(root)
    found: dict[str, str] = {}
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.split('/')[0] in skip_names:
            continue
        found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def markdown_links(text: str):
    text = re.sub(r'```.*?```', '', text, flags=re.S)
    text = re.sub(r'`[^`\n]*`', '', text)
    return re.findall(r'\[[^\]]*\]\(([^)]+)\)', text)


# The only substitution is the network transport. Release selection, digest
# checks, archive verification and the real entry code still run in the child.
SYNTHETIC_DRIVER = r'''
import hashlib, io, json, sys, zipfile
from pathlib import Path
from unittest.mock import patch
from scripts.update_agentorch import entry, sources
root = Path(sys.argv[1])
manifest_bytes = (root / "MANIFEST.json").read_bytes()
manifest = json.loads(manifest_bytes)
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w") as archive:
    for item in manifest["files"]:
        archive.writestr("agentorch/" + item["path"], (root / item["path"]).read_bytes())
    archive.writestr("agentorch/MANIFEST.json", manifest_bytes)
payload = buffer.getvalue()
asset = sources.ReleaseAsset("agentorch.zip", len(payload),
    "sha256:" + hashlib.sha256(payload).hexdigest(),
    "https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip")
release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, "v1.7", (asset,))
with patch.object(sources.GitHubReleaseSource, "fetch_release", return_value=release), \
     patch.object(sources.GitHubReleaseSource, "download_asset", return_value=payload):
    raise SystemExit(entry.main(sys.argv[2:]))
'''

# Planning probe: reports status/problems for a host that may contain an entry
# which blocks on open (a FIFO with no writer). A child process is used so a
# hung planning read becomes a short timeout instead of a stuck test run.
PLAN_PROBE_DRIVER = r'''
import json, sys
from scripts.update_agentorch import workspace
plan = workspace.plan_isolated_onboarding(sys.argv[1], source_root=sys.argv[2])
print(json.dumps({"status": plan.status, "problems": plan.problems,
                  "writes": len(plan.writes),
                  "legacy_removals": plan.legacy_removals}))
'''


class IsolatedInstallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.source = make_source(self.tmp / 'source')
        self.verified = verified_package(self.source)
        self.host = copy_host(self.tmp / 'host')

    def plan(self, **kwargs):
        return workspace.plan_isolated_onboarding(
            self.host, source_root=self.source,
            verified_source=self.verified, **kwargs)

    def plan_unverified(self, **kwargs):
        return workspace.plan_isolated_onboarding(
            self.host, source_root=self.source, **kwargs)

    def test_public_report_privacy_plan_and_conflict(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        marker = 'SYNTHETIC_PRIVATE_CANARY_153'
        agents = self.host / 'AGENTS.md'
        agents.write_text('# Synthetic host\n' + marker + '\n', encoding='utf-8')
        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            prepared = entry.prepare_isolated_install(self.host, mode='PLAN')
        public = json.dumps(prepared['payload'], ensure_ascii=False)
        self.assertEqual(prepared['payload']['status'], 'PLANNED')
        self.assertIn('LICENSE.md', prepared['payload']['files'])
        with self.subTest(case='plan'):
            self.assertFalse(marker in public, 'public PLAN must not contain host text')
            self.assertFalse(str(self.tmp) in public, 'public PLAN must not contain absolute paths')
        agents.write_text('# Synthetic host\n' + marker + '\n' +
                          workspace.managed_block(FORMAT_VERSION).replace(workspace.MANAGED_END, ''),
                          encoding='utf-8')
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            prepared = entry.prepare_isolated_install(self.host, mode='PLAN')
        public = json.dumps(prepared['payload'], ensure_ascii=False)
        self.assertEqual(prepared['payload']['status'], 'STOPPED_CONFLICT')
        self.assertTrue(prepared['payload']['problems'])
        self.assertTrue(prepared['payload']['guidance'])
        self.assertTrue(prepared['payload']['diff'])
        with self.subTest(case='conflict'):
            self.assertFalse(marker in public, 'public conflict must not contain host text')
            self.assertFalse(str(self.tmp) in public, 'public conflict must not contain absolute paths')

    def test_public_report_privacy_exception_and_rollback(self):
        import contextlib, io
        from unittest.mock import patch
        from scripts.update_agentorch import entry
        secret = 'SYNTHETIC_PRIVATE_CANARY_153'
        for operation in ('exception', 'rollback'):
            with self.subTest(operation=operation):
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    if operation == 'exception':
                        with patch.object(entry, 'prepare_isolated_install',
                                          side_effect=OSError(f'{self.tmp}/{secret}')):
                            result = entry.main(['--install-isolated', str(self.host), '--mode', 'PLAN'])
                    else:
                        with patch.object(workspace, 'rollback_isolated_onboarding',
                                          return_value={'status': 'BLOCKED', 'problems': [f'{self.tmp}/{secret}']}):
                            result = entry.main(['--install-isolated', str(self.host),
                                                 '--rollback-backup', str(self.tmp / 'backup')])
                self.assertNotEqual(result, 0)
                self.assertFalse(secret in output.getvalue() + error.getvalue(), 'private text in output')
                self.assertFalse(str(self.tmp) in output.getvalue() + error.getvalue(), 'absolute path in output')

    def test_public_report_privacy_real_cli_plan_and_conflict(self):
        import subprocess
        secret = 'SYNTHETIC_PRIVATE_CANARY_153'
        agents = self.host / 'AGENTS.md'
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cmd = [sys.executable, '-c', SYNTHETIC_DRIVER, str(self.source),
               '--install-isolated', str(self.host), '--mode', 'PLAN']
        for conflict in (False, True):
            with self.subTest(conflict=conflict):
                text = '# Synthetic host\n' + secret + '\n'
                if conflict:
                    text += workspace.managed_block(FORMAT_VERSION).replace(workspace.MANAGED_END, '')
                agents.write_text(text, encoding='utf-8')
                proc = subprocess.run(cmd, cwd=self.tmp, env=env, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 2 if conflict else 0)
                payload = json.loads(proc.stdout)
                self.assertEqual(payload['status'], 'STOPPED_CONFLICT' if conflict else 'PLANNED')
                if conflict:
                    self.assertTrue(payload['problems'])
                    self.assertTrue(payload['guidance'])
                    self.assertTrue(payload['diff'])
                self.assertFalse(secret in proc.stdout + proc.stderr, 'private text in CLI')
                self.assertFalse(str(self.tmp) in proc.stdout + proc.stderr, 'absolute path in CLI')

    def test_round2_isolated_cli_parse_errors_are_private(self):
        import subprocess
        secret = 'SYNTHETIC_PRIVATE_CANARY_153'
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        prefix = [sys.executable, '-m', 'scripts.update_agentorch.entry']
        for marker in ('--install-isolated', '--install-isolated=' + str(self.host), '--install-isol'):
            for argument in ('--mode', '--scenario', '--edition', '--unexpected'):
                with self.subTest(marker=marker.split('=')[0], argument=argument):
                    args = [marker] + ([str(self.host)] if '=' not in marker else [])
                    args += [argument, str(self.tmp / secret), '--install-isolated=' + str(self.host)]
                    proc = subprocess.run(prefix + args, cwd=self.tmp, env=env,
                                          capture_output=True, text=True)
                    output = proc.stdout + proc.stderr
                    self.assertEqual(proc.returncode, 2)
                    self.assertFalse(secret in output, 'private argument was displayed')
                    self.assertFalse(str(self.tmp) in output, 'private path was displayed')
                    self.assertNotIn('Traceback', output)

    def test_round3_empty_isolated_cli_never_enters_flat_writer(self):
        import subprocess
        secret = 'SYNTHETIC_PRIVATE_CANARY_153'
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cases = [
            ['--install-isolated='], ['--install-isolated', ''],
            ['--install-isolated=', '--install-isolated='],
            ['--install-isol='], ['--install-isolated', '', '--rollback-backup', 'backup'],
            ['--install-isolated=', '--project', '{host}'],
            ['--install-isolated=', '--request', 'noop', '--output', '{host}/AGENTS.md'],
            ['--request', 'noop', '--install-isolated', '', '--output', '{host}/AGENTS.md'],
            ['--install-isolated=', '--request', secret, '--unknown', secret],
            ['--install-isolated=', '--install-isolated', '{host}', '--request', 'noop',
             '--output', '{host}/AGENTS.md'],
        ]
        for index, arguments in enumerate(cases):
            with self.subTest(case=index):
                host = copy_host(self.tmp / f'round3-host-{index}')
                (host / 'AGENTS.md').write_text('# host\n' + secret, encoding='utf-8')
                def snapshot():
                    return sorted((p.relative_to(host).as_posix(), 'dir' if p.is_dir() else
                                   hashlib.sha256(p.read_bytes()).hexdigest())
                                  for p in host.rglob('*'))
                before = snapshot()
                args = [arg.replace('{host}', str(host)) for arg in arguments]
                proc = subprocess.run([sys.executable, '-m', 'scripts.update_agentorch.entry', *args],
                                      cwd=self.tmp, env=env, capture_output=True, text=True)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(snapshot(), before, 'isolated invalid arguments wrote to host')
                self.assertFalse(secret in proc.stdout + proc.stderr, 'private content in output')
                self.assertFalse(str(self.tmp) in proc.stdout + proc.stderr, 'private path in output')
                self.assertNotIn('Traceback', proc.stdout + proc.stderr)

    def test_round3_flat_cli_matches_baseline_abbreviations(self):
        import subprocess
        baseline_root = self.tmp / 'baseline'
        shutil.copytree(ROOT / 'scripts', baseline_root / 'scripts',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        baseline = subprocess.run(['git', 'show', 'HEAD:scripts/update_agentorch/entry.py'],
                                  cwd=ROOT, capture_output=True, check=True).stdout
        (baseline_root / 'scripts/update_agentorch/entry.py').write_bytes(baseline)
        prefix = [sys.executable, '-m', 'scripts.update_agentorch.entry']
        cases = [('--request', ''), ('--request=',), ('--r', ''),
                 ('--r', 'noop'), ('--request', 'noop', '--e', 'github'),
                 ('--request', 'noop', '--edition', 'github'), ('--request', 'noop'),
                 ('--request=noop', '--m', 'PLAN'), ('--request', 'noop', '--xyz'),
                 ('--request=--install-isolated',), ('--request', 'noop', '--', '--install-isolated')]
        for args in cases:
            with self.subTest(case=args):
                def run(path):
                    return subprocess.run([*prefix, *args], cwd=self.tmp,
                                          env=dict(os.environ, PYTHONPATH=str(path)),
                                          capture_output=True, text=True)
                expected, actual = run(baseline_root), run(ROOT)
                self.assertEqual(actual.returncode, expected.returncode)
                self.assertEqual(actual.stdout, expected.stdout)
                self.assertEqual(actual.stderr, expected.stderr)

    def test_empty_rollback_never_installs_in_real_cli(self):
        import subprocess
        host, head = self._review_linked_host()
        subprocess.run(['git', '-C', str(host), 'rm', '-q', 'AGENTS.md'], check=True)
        subprocess.run(['git', '-C', str(host), 'commit', '-qm', 'synthetic missing entry'], check=True)
        head = subprocess.run(['git', '-C', str(host), 'rev-parse', 'HEAD'], check=True,
                              capture_output=True, text=True).stdout.strip()
        driver = SYNTHETIC_DRIVER.replace('from scripts.update_agentorch import entry, sources',
                                          'from scripts.update_agentorch import sources\nimport runpy')
        driver = driver.replace('    raise SystemExit(entry.main(sys.argv[2:]))',
                                '    sys.argv = ["scripts.update_agentorch.entry", *sys.argv[2:]]\n'
                                '    runpy.run_module("scripts.update_agentorch.entry", run_name="__main__")')
        for present in (False, True):
            if present:
                (host / 'AGENTS.md').write_text('synthetic host rules\n', encoding='utf-8')
            before = raw_snapshot(host)
            base = ['--install-isolated', str(host), '--mode', 'APPLY',
                    '--declared-exclusive', '--expected-head', head]
            cases = [['--rollback-backup='], ['--rollback-backup', ''],
                     ['--rollback-backup=', '--rollback-backup='],
                     ['--rollback-backup=', '--rollback-backup', 'backup'],
                     ['--rollback-backup', 'backup', '--rollback-backup='],
                     ['--rollback-backup=', '--output', str(host / 'report')],
                     ['--rollback-backup=', '--scenario', 'new-project']]
            for args in cases:
                with self.subTest(present=present, args=args):
                    cmd = [sys.executable, '-c', driver, str(self.source), *base, *args]
                    proc = subprocess.run(cmd, cwd=self.tmp,
                                          env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                          capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 2, (proc.stdout, proc.stderr))
                    self.assertIn('invalid isolated arguments', proc.stderr)
                    self.assertNotIn(str(self.tmp), proc.stdout + proc.stderr)
                    self.assertEqual(raw_snapshot(host), before, (proc.stdout, proc.stderr))

    def test_round2_managed_conflict_categories_real_plan(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        secret = 'SYNTHETIC_PRIVATE_CANARY_153'
        block = workspace.managed_block(FORMAT_VERSION)
        lines = block.splitlines()
        changed = block.replace('AgentOrch 作为本项目', 'tampered 作为本项目')
        edited = changed.splitlines()
        digest = hashlib.sha256(('\n'.join(edited[1:-2]) + '\n').encode()).hexdigest()
        edited[-2] = workspace.MANAGED_HASH_TEMPLATE.format(digest)
        cases = {
            'MISSING_MARKER': block.replace(workspace.MANAGED_END, ''),
            'DUPLICATE_MARKER': block + block,
            'REVERSED_MARKER': '\n'.join([lines[-1], *lines[:-1]]) + '\n',
            'BODY_HASH_MISMATCH': changed,
            'NONCANONICAL_BODY': '\n'.join(edited) + '\n',
            'UNEXPECTED_BLOCK_CONTENT': block.replace(workspace.MANAGED_END, secret + '\n' + workspace.MANAGED_END),
        }
        release, payload = self._review_release_transport()
        for expected, text in cases.items():
            with self.subTest(category=expected):
                (self.host / 'AGENTS.md').write_text('# Host\n' + secret + '\n' + text, encoding='utf-8')
                before = raw_snapshot(self.host)
                with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
                     patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
                    prepared = entry.prepare_isolated_install(self.host, mode='PLAN')
                result = prepared['payload']
                self.assertEqual(result['status'], 'STOPPED_CONFLICT')
                self.assertEqual(result['diff'].get('conflict_type'), expected)
                self.assertEqual(result['diff']['decision'], 'REFUSED')
                self.assertTrue(result['guidance'])
                self.assertFalse(secret in json.dumps(result, ensure_ascii=False))
                self.assertEqual(raw_snapshot(self.host), before)
                self.assertTrue(prepared['plan'].diff, expected)
                self.assertIn('--- current AGENTS.md', prepared['plan'].diff)
                self.assertIn('+++ proposed AGENTS.md', prepared['plan'].diff)
                if expected == 'UNEXPECTED_BLOCK_CONTENT':
                    self.assertIn(secret, prepared['plan'].diff)
                if expected == 'NONCANONICAL_BODY':
                    self.assertIn('tampered 作为本项目', prepared['plan'].diff)
        (self.host / 'AGENTS.md').write_text('# Host\n' + secret + '\n', encoding='utf-8')
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = entry.prepare_isolated_install(self.host, mode='PLAN')['payload']
        self.assertEqual(result['status'], 'PLANNED')
        self.assertEqual(result['diff'].get('conflict_type'), 'NONE')

    def _round2_host_no_agents(self):
        import subprocess
        host, _ = self._review_linked_host()
        subprocess.run(['git', '-C', str(host), 'rm', '-q', 'AGENTS.md'], check=True)
        subprocess.run(['git', '-C', str(host), 'commit', '-qm', 'synthetic entry absent'], check=True)
        head = subprocess.run(['git', '-C', str(host), 'rev-parse', 'HEAD'], check=True,
                              capture_output=True, text=True).stdout.strip()
        return host, head

    def test_round2_temp_cleanup_failure_never_reports_applied(self):
        import errno
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._round2_host_no_agents()
        release, payload = self._review_release_transport()
        real_unlink = os.unlink
        reached = []
        def deny(path, *args, **kwargs):
            parent = kwargs.get('dir_fd')
            if (str(path).startswith('.agentorch-write-') and parent is not None
                    and os.fstat(parent).st_ino == host.stat().st_ino):
                reached.append(True)
                raise OSError(errno.EIO, 'synthetic cleanup failure')
            return real_unlink(path, *args, **kwargs)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace.os, 'unlink', side_effect=deny) as mocked_unlink:
            with patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {mocked_unlink}):
                prepared = entry.prepare_isolated_install(host, mode='APPLY',
                             declared_exclusive=True, expected_head=head)
        backup = prepared['result'].get('backup_location')
        if backup:
            self.addCleanup(shutil.rmtree, backup)
        self.assertTrue(reached)
        self.assertNotEqual(prepared['payload']['status'], 'APPLIED')
        self.assertNotEqual(prepared['payload']['rollback']['result'], 'SUCCEEDED')

    def test_round2_link_fsync_and_unlink_failure_is_not_zero_writes(self):
        import errno
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._round2_host_no_agents()
        release, payload = self._review_release_transport()
        real_link, real_fsync, real_unlink = os.link, os.fsync, os.unlink
        linked = []
        def link(*args, **kwargs):
            result = real_link(*args, **kwargs)
            linked.append(args[1])
            return result
        def fsync(fd):
            if linked:
                raise OSError(errno.EIO, 'synthetic sync failure')
            return real_fsync(fd)
        def unlink(path, *args, **kwargs):
            if linked and str(path) == linked[0]:
                raise OSError(errno.EPERM, 'synthetic restore failure')
            return real_unlink(path, *args, **kwargs)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace.os, 'link', side_effect=link) as mocked_link, \
             patch.object(workspace.os, 'fsync', side_effect=fsync), \
             patch.object(workspace.os, 'unlink', side_effect=unlink) as mocked_unlink:
            with patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {mocked_link, mocked_unlink}):
                prepared = entry.prepare_isolated_install(host, mode='APPLY',
                             declared_exclusive=True, expected_head=head)
        backup = prepared['result'].get('backup_location')
        if backup:
            self.addCleanup(shutil.rmtree, backup)
        self.assertTrue(linked)
        self.assertEqual(prepared['payload']['status'], 'BLOCKED')
        self.assertEqual(prepared['payload']['rollback']['result'], 'FAILED')
        self.assertIsNone(prepared['payload']['written_count'])

    def test_round2_replaced_created_directory_is_not_removed(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._round2_host_no_agents()
        release, payload = self._review_release_transport()
        observed = []
        original_hook = workspace._invoke_hook
        def hook(fault, name, *args):
            if name == 'after_write' and not observed:
                (host / 'agentorch').rename(host / 'synthetic-displaced')
                (host / 'agentorch').mkdir()
                observed.append(True)
                raise OSError('synthetic competing directory')
            return original_hook(fault, name, *args)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace, '_invoke_hook', side_effect=hook):
            prepared = entry.prepare_isolated_install(host, mode='APPLY',
                         declared_exclusive=True, expected_head=head)
        backup = prepared['result'].get('backup_location')
        if backup:
            self.addCleanup(shutil.rmtree, backup)
        self.assertTrue(observed)
        self.assertTrue((host / 'agentorch').is_dir(), 'foreign empty directory must remain')
        self.assertEqual(prepared['payload']['status'], 'BLOCKED')
        self.assertEqual(prepared['payload']['rollback']['result'], 'FAILED')

    def test_round2_side_effect_status_positive_controls(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._round2_host_no_agents()
        release, payload = self._review_release_transport()
        before = raw_snapshot(host, skip_names=('.git',))
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            refused = entry.prepare_isolated_install(host, mode='APPLY', declared_exclusive=False)
        self.assertEqual(refused['payload']['status'], 'BLOCKED')
        self.assertEqual(refused['payload']['written_count'], 0)
        self.assertEqual(raw_snapshot(host, skip_names=('.git',)), before)
        seen = []
        original_hook = workspace._invoke_hook
        def hook(fault, name, *args):
            if name == 'after_write' and not seen:
                seen.append(True)
                raise OSError('synthetic failure after write')
            return original_hook(fault, name, *args)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace, '_invoke_hook', side_effect=hook):
            recovered = entry.prepare_isolated_install(host, mode='APPLY',
                        declared_exclusive=True, expected_head=head)
        backup = recovered['result'].get('backup_location')
        if backup:
            self.addCleanup(shutil.rmtree, backup)
        self.assertTrue(seen)
        self.assertEqual(recovered['payload']['status'], 'ROLLED_BACK')
        self.assertEqual(recovered['payload']['rollback']['result'], 'SUCCEEDED')
        self.assertEqual(recovered['payload']['written_count'], 0)
        self.assertEqual(raw_snapshot(host, skip_names=('.git',)), before)

    def test_round2_temp_identity_swap_preserves_foreign_file(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._round2_host_no_agents()
        release, payload = self._review_release_transport()
        real_link, real_unlink = os.link, os.unlink
        swapped = []
        def link(source, target, **kwargs):
            value = real_link(source, target, **kwargs)
            if not swapped:
                parent = kwargs['src_dir_fd']
                real_unlink(source, dir_fd=parent)
                fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                try:
                    os.write(fd, b'synthetic foreign file')
                finally:
                    os.close(fd)
                swapped.append(source)
            return value
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace.os, 'link', side_effect=link) as mocked_link:
            with patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {mocked_link}):
                prepared = entry.prepare_isolated_install(host, mode='APPLY',
                           declared_exclusive=True, expected_head=head)
        backup = prepared['result'].get('backup_location')
        if backup:
            self.addCleanup(shutil.rmtree, backup)
        self.assertTrue(swapped)
        self.assertEqual(prepared['payload']['status'], 'BLOCKED')
        self.assertEqual(prepared['payload']['rollback']['result'], 'FAILED')
        matches = list(host.rglob(swapped[0]))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].read_bytes(), b'synthetic foreign file')

    def test_round2_temp_identity_capture_failure_is_unknown(self):
        import errno
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._round2_host_no_agents()
        release, payload = self._review_release_transport()
        real_fstat = os.fstat
        observed = []
        def fstat(fd):
            try:
                name = os.readlink(f'/proc/self/fd/{fd}')
            except OSError:
                name = ''
            if '.agentorch-write-' in name and Path(name).parent == host:
                observed.append(True)
                raise OSError(errno.EIO, 'synthetic fstat failure')
            return real_fstat(fd)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace.os, 'fstat', side_effect=fstat):
            prepared = entry.prepare_isolated_install(host, mode='APPLY',
                       declared_exclusive=True, expected_head=head)
        backup = prepared['result'].get('backup_location')
        if backup:
            self.addCleanup(shutil.rmtree, backup)
        self.assertTrue(observed)
        self.assertEqual(prepared['payload']['status'], 'BLOCKED')
        self.assertEqual(prepared['payload']['rollback']['result'], 'FAILED')
        self.assertIsNone(prepared['payload']['written_count'])

    def test_round2_root_temp_reappears_is_not_silently_accepted(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._round2_host_no_agents()
        release, payload = self._review_release_transport()
        original = os.unlink
        inserted = []
        def unlink(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            parent = kwargs.get('dir_fd')
            if (not inserted and str(path).startswith('.agentorch-write-') and parent is not None
                    and os.fstat(parent).st_ino == host.stat().st_ino):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                try:
                    os.write(fd, b'synthetic other writer')
                finally:
                    os.close(fd)
                inserted.append(str(path))
            return result
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace.os, 'unlink', side_effect=unlink) as mocked_unlink:
            with patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {mocked_unlink}):
                prepared = entry.prepare_isolated_install(host, mode='APPLY',
                            declared_exclusive=True, expected_head=head)
        backup = prepared['result'].get('backup_location')
        if backup:
            self.addCleanup(shutil.rmtree, backup)
        self.assertTrue(inserted)
        self.assertNotEqual(prepared['payload']['status'], 'APPLIED')
        self.assertEqual(prepared['payload']['rollback']['result'], 'FAILED')
        self.assertEqual((host / inserted[0]).read_bytes(), b'synthetic other writer')

    def test_round2_child_temp_unlink_eperm_stays_blocked(self):
        import errno
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._round2_host_no_agents()
        release, payload = self._review_release_transport()
        original = os.unlink
        reached = []
        def unlink(path, *args, **kwargs):
            parent = kwargs.get('dir_fd')
            if (str(path).startswith('.agentorch-write-') and parent is not None
                    and os.fstat(parent).st_ino != host.stat().st_ino):
                reached.append(True)
                raise OSError(errno.EPERM, 'synthetic cleanup failure')
            return original(path, *args, **kwargs)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace.os, 'unlink', side_effect=unlink) as mocked_unlink:
            with patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {mocked_unlink}):
                prepared = entry.prepare_isolated_install(host, mode='APPLY',
                            declared_exclusive=True, expected_head=head)
        backup = prepared['result'].get('backup_location')
        if backup:
            self.addCleanup(shutil.rmtree, backup)
        self.assertTrue(reached)
        self.assertEqual(prepared['payload']['status'], 'BLOCKED')
        self.assertEqual(prepared['payload']['rollback']['result'], 'FAILED')
        self.assertIsNone(prepared['payload']['written_count'])

    def test_round2_primary_link_failure_not_hidden_by_cleanup_failure(self):
        import errno
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._round2_host_no_agents()
        release, payload = self._review_release_transport()
        original = os.unlink
        reached = []
        def link(*args, **kwargs):
            reached.append('link')
            raise OSError(errno.EIO, 'synthetic link failure')
        def unlink(path, *args, **kwargs):
            if str(path).startswith('.agentorch-write-'):
                reached.append('cleanup')
                raise OSError(errno.EPERM, 'synthetic cleanup failure')
            return original(path, *args, **kwargs)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace.os, 'link', side_effect=link) as mocked_link, \
             patch.object(workspace.os, 'unlink', side_effect=unlink) as mocked_unlink:
            with patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {mocked_link, mocked_unlink}):
                prepared = entry.prepare_isolated_install(host, mode='APPLY',
                            declared_exclusive=True, expected_head=head)
        backup = prepared['result'].get('backup_location')
        if backup:
            self.addCleanup(shutil.rmtree, backup)
        self.assertIn('link', reached)
        self.assertIn('cleanup', reached)
        self.assertEqual(prepared['payload']['status'], 'BLOCKED')
        self.assertEqual(prepared['payload']['rollback']['result'], 'FAILED')
        self.assertEqual(prepared['payload'].get('failure'),
                         {'phase': 'WRITE', 'kind': 'OSError', 'errno': errno.EIO})

    def test_public_report_privacy_structured_conflict_and_unknown_values(self):
        from scripts.update_agentorch import entry
        plan = self.plan()
        secret = 'SYNTHETIC_PRIVATE_CANARY_153'
        plan.files[secret + '.txt'] = b'private'
        plan.diff = secret
        plan.guidance = secret
        result = {'status': 'BLOCKED', 'problems': [secret], 'written': [secret],
                  'backup_location': str(self.tmp / secret),
                  'rollback': {'result': 'FAILED', 'conflicts': [secret],
                               'extra': {'secret': secret}}, 'unknown': {'secret': secret}}
        public = entry._plan_payload(plan, result)
        data = json.dumps(public, ensure_ascii=False)
        self.assertFalse(secret in data, 'unknown / user-controlled text exposed')
        self.assertFalse(str(self.tmp) in data, 'absolute path exposed')
        self.assertEqual(public['status'], 'BLOCKED')
        self.assertEqual(public['rollback']['result'], 'FAILED')
        self.assertTrue(public['guidance'])
        self.assertIsInstance(public['diff'], dict)
        self.assertIn('begin_count', public['diff'])
        result['status'] = secret
        unknown = entry._plan_payload(plan, result)
        self.assertEqual(unknown['status'], 'UNKNOWN')
        self.assertIsNone(unknown['written_count'])
        self.assertFalse(secret in json.dumps(unknown, ensure_ascii=False))

    def test_public_report_privacy_apply_and_exception_cli(self):
        import subprocess
        secret = 'SYNTHETIC_PRIVATE_CANARY_153'
        script = r'''
import sys
from unittest.mock import patch
from scripts.update_agentorch import entry, workspace
host, operation, secret = sys.argv[1:]
if operation == 'rollback':
    arguments=['--install-isolated',host,'--rollback-backup',host+'/backup']
    with patch.object(workspace,'rollback_isolated_onboarding',return_value={
         'status':'BLOCKED','problems':[secret],'rollback':{'unknown':secret}}):
        raise SystemExit(entry.main(arguments))
else:
    arguments=['--install-isolated',host,'--mode','APPLY']
    with patch.object(entry,'prepare_isolated_install',side_effect=OSError(host+'/'+secret)):
        raise SystemExit(entry.main(arguments))
'''
        for op in ('exception', 'rollback'):
            with self.subTest(operation=op):
                proc = subprocess.run([sys.executable, '-c', script, str(self.host), op, secret],
                                      cwd=self.tmp, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                      capture_output=True, text=True)
                output = proc.stdout + proc.stderr
                self.assertNotEqual(proc.returncode, 0)
                self.assertFalse(secret in output, 'exception/rollback exposed private text')
                self.assertFalse(str(self.tmp) in output, 'exception/rollback exposed private path')
                self.assertNotIn('Traceback', output)

    def test_public_report_privacy_real_apply_blocked_and_marker_counts(self):
        import subprocess
        secret = 'SYNTHETIC_PRIVATE_CANARY_153'
        agents = self.host / 'AGENTS.md'
        agents.write_text('# Synthetic host\n' + secret + '\n' +
                          workspace.managed_block(FORMAT_VERSION).replace(workspace.MANAGED_END, ''),
                          encoding='utf-8')
        cmd = [sys.executable, '-c', SYNTHETIC_DRIVER, str(self.source),
               '--install-isolated', str(self.host), '--mode', 'APPLY']
        proc = subprocess.run(cmd, cwd=self.tmp, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        public = json.loads(proc.stdout)
        self.assertEqual(public['status'], 'STOPPED_CONFLICT')
        self.assertIsInstance(public['diff'], dict)
        self.assertEqual(public['diff']['begin_count'], 1)
        self.assertEqual(public['diff']['end_count'], 0)
        self.assertIn('reason', public['diff'])
        self.assertEqual(public['diff']['reason'], 'MARKER_MISMATCH')
        self.assertEqual(public['diff']['decision'], 'REFUSED')
        self.assertTrue(public['problems'])
        self.assertTrue(public['guidance'])
        self.assertFalse(secret in proc.stdout + proc.stderr)
        self.assertFalse(str(self.tmp) in proc.stdout + proc.stderr)

    def test_public_report_privacy_writes_and_rollback_states_are_not_masked(self):
        from scripts.update_agentorch import entry
        plan = self.plan()
        result = {'status': 'BLOCKED', 'written': ['user/private-name'],
                  'removed': [], 'rollback': {'result': 'FAILED', 'required': True,
                                               'attempted': True},
                  'backup_location': str(self.tmp / 'backup')}
        public = entry._plan_payload(plan, result)
        # Failed recovery cannot establish the remaining write count; preserve
        # the independently confirmed completion count rather than hiding it.
        self.assertIsNone(public['written_count'])
        self.assertEqual(public['completed_count'], 1)
        self.assertEqual(public['rollback']['result'], 'FAILED')
        self.assertTrue(public['rollback']['attempted'])
        self.assertEqual(public['backup_status'], 'LOCAL_LOCATION_UNAVAILABLE')
        self.assertNotIn('user/private-name', json.dumps(public))
        self.assertNotIn(str(self.tmp), json.dumps(public))

    def test_public_report_privacy_apply_positive_real_transaction(self):
        import subprocess
        from unittest.mock import patch
        from scripts.update_agentorch import entry, report, sources
        host, _ = self._review_linked_host()
        subprocess.run(['git', '-C', str(host), 'rm', '-q', 'AGENTS.md'], check=True)
        subprocess.run(['git', '-C', str(host), 'commit', '-qm', 'synthetic missing entry'], check=True)
        head = subprocess.run(['git', '-C', str(host), 'rev-parse', 'HEAD'], check=True,
                              capture_output=True, text=True).stdout.strip()
        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            prepared = entry.prepare_isolated_install(host, mode='APPLY',
                         declared_exclusive=True, expected_head=head)
        backup = prepared['result'].get('backup_location')
        if backup:
            self.addCleanup(shutil.rmtree, backup)
        public = prepared['payload']
        self.assertEqual(public['status'], 'APPLIED')
        self.assertGreater(public['written_count'], 0)
        self.assertEqual(public['backup_status'], 'LOCAL_LOCATION_UNAVAILABLE')
        self.assertTrue((host / 'agentorch' / 'LICENSE.md').is_file())
        self.assertNotIn(str(self.tmp), json.dumps(public))
        self.assertTrue(report.validate_report(public),
                        'isolated DTO must not claim frozen update-report schema compatibility')

    def test_public_report_privacy_real_rollback_stays_blocked(self):
        import subprocess
        cmd = [sys.executable, '-m', 'scripts.update_agentorch.entry',
               '--install-isolated', str(self.host), '--rollback-backup', str(self.tmp / 'backup')]
        proc = subprocess.run(cmd, cwd=self.tmp, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)['status'], 'BLOCKED')
        self.assertNotIn(str(self.tmp), proc.stdout + proc.stderr)

    def _validator_complete_tree(self):
        from scripts import build_release, check_drift, validate_kit
        files = build_release.collect_sources(build_release.read_required())
        suite, version = check_drift.read_version(ROOT)
        agentorch = self.host / 'agentorch'
        transformed = {
            rel: workspace._rewrite_host_agents_links(rel, data, ROOT, self.host)
            for rel, data in files.items() if rel != 'AGENTS.md'
        }
        transformed['.gitignore'] = workspace.AGENTORCH_GITIGNORE.encode()
        for rel, data in transformed.items():
            path = agentorch / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (agentorch / 'MANIFEST.json').write_bytes(
            workspace._manifest_bytes(transformed, suite, version))
        (self.host / 'AGENTS.md').write_text(
            '# Synthetic host\n\n' + workspace.managed_block(version), encoding='utf-8')
        self.assertEqual(validate_kit.validate_kit(agentorch)[0], [])
        return agentorch, version

    def test_validator_security_private_symlinks_rejected(self):
        from unittest.mock import patch
        from scripts import validate_kit
        agentorch, _ = self._validator_complete_tree()
        outside = self.tmp / 'external'
        outside.mkdir()
        sentinel = outside / 'private.txt'
        sentinel.write_bytes(b'unchanged')
        for name in sorted(validate_kit.ISOLATED_PRIVATE_DIRS):
            private = agentorch / name
            with self.subTest(name=name, kind='directory'):
                private.mkdir()
                (private / 'private.txt').write_bytes(b'private')
                self.assertEqual(validate_kit.validate_kit(agentorch)[0], [])
                shutil.rmtree(private)
            for target in (outside, self.tmp / 'nonexistent'):
                with self.subTest(name=name, target=target.name):
                    private.symlink_to(target, target_is_directory=True)
                    try:
                        scandir = os.scandir
                        def guarded_scandir(path):
                            self.assertNotEqual(Path(path).resolve(), outside.resolve(),
                                                'validator must not scan symlink target')
                            return scandir(path)
                        with patch.object(os, 'scandir', side_effect=guarded_scandir):
                            errors = validate_kit.validate_kit(agentorch)[0]
                        self.assertTrue(any('symlink' in e and name in e for e in errors), errors)
                        self.assertEqual(sentinel.read_bytes(), b'unchanged')
                    finally:
                        private.unlink()

    def test_validator_walk_injected_errors_fail_closed(self):
        import errno
        from unittest.mock import patch
        from scripts import validate_kit
        agentorch, _ = self._validator_complete_tree()
        public = agentorch / 'docs'
        outside = self.tmp / 'synthetic-outside'
        outside.mkdir()
        (public / '.local').symlink_to(outside, target_is_directory=True)
        self.assertTrue(any('symlink' in e and 'docs/.local' in e
                            for e in validate_kit.isolated_layout_errors(agentorch)))
        real_scandir = os.scandir
        for number in (errno.EACCES, errno.EIO):
            with self.subTest(errno=number):
                def failing_scandir(path):
                    if Path(path) == public:
                        raise OSError(number, 'synthetic-private-message', str(public))
                    return real_scandir(path)
                with patch.object(os, 'scandir', side_effect=failing_scandir):
                    errors = validate_kit.isolated_layout_errors(agentorch)
                message = '\n'.join(errors)
                self.assertIn('docs', message)
                self.assertIn(str(number), message)
                self.assertIn('enumerat', message)
                self.assertNotIn(str(self.tmp), message)
                self.assertNotIn('synthetic-private-message', message)

    def test_validator_walk_real_permission_denial_fails_closed(self):
        import subprocess
        from scripts import validate_kit
        agentorch, _ = self._validator_complete_tree()
        public = agentorch / 'docs'
        outside = self.tmp / 'synthetic-outside'
        outside.mkdir()
        (public / '.local').symlink_to(outside, target_is_directory=True)
        public.chmod(0o111)
        try:
            try:
                with os.scandir(public):
                    pass
            except PermissionError:
                errors = validate_kit.isolated_layout_errors(agentorch)
                message = '\n'.join(errors)
                self.assertIn('docs', message)
                self.assertIn('enumerat', message)
                self.assertNotIn(str(self.tmp), message)
                proc = subprocess.run([sys.executable, '-I', '-B',
                                       str(agentorch / 'scripts' / 'validate_kit.py')],
                                      cwd=self.tmp, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertIn('enumerat', proc.stdout)
                self.assertNotIn(str(self.tmp), proc.stdout + proc.stderr)
            else:
                # Privileged processes may enumerate despite mode 0111; the
                # separate injected-error test always exercises the failure.
                self.assertTrue(os.access(public, os.R_OK))
        finally:
            public.chmod(0o755)

    def test_validator_edition_enumeration_cli_fails_closed(self):
        import errno
        import subprocess
        agentorch, _ = self._validator_complete_tree()
        script = agentorch / 'scripts' / 'validate_kit.py'
        cli = [sys.executable, '-I', '-B', str(script)]
        positive = subprocess.run(cli, cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(positive.returncode, 0, positive.stdout + positive.stderr)
        injection = r'''
import os, pathlib, runpy, sys
script, target, number = sys.argv[1], pathlib.Path(sys.argv[2]), int(sys.argv[3])
original = pathlib.Path.iterdir
def injected(path):
    if path == target:
        print('INJECTION_REACHED', file=sys.stderr)
        raise OSError(number, 'synthetic-secret-message', str(target))
    return original(path)
pathlib.Path.iterdir = injected
sys.argv = [script]
runpy.run_path(script, run_name='__main__')
'''
        for edition in ('local', 'github'):
            directory = agentorch / 'editions' / edition
            def check(proc, number):
                output = proc.stdout + proc.stderr
                self.assertNotEqual(proc.returncode, 0, output)
                self.assertIn(f'editions/{edition}', output)
                self.assertIn(f'errno {number}', output)
                self.assertNotIn(str(self.tmp), output)
                self.assertNotIn('synthetic-secret-message', output)
                self.assertNotIn('Traceback', output)
            for number in (errno.EACCES, errno.EIO):
                with self.subTest(edition=edition, injected_errno=number):
                    proc = subprocess.run([sys.executable, '-I', '-B', '-c', injection,
                                           str(script), str(directory), str(number)],
                                          cwd=self.tmp, capture_output=True, text=True)
                    self.assertIn('INJECTION_REACHED', proc.stderr)
                    check(proc, number)
            with self.subTest(edition=edition, real_permission=True):
                directory.chmod(0o111)
                try:
                    try:
                        with os.scandir(directory):
                            pass
                    except PermissionError:
                        proc = subprocess.run(cli, cwd=self.tmp, capture_output=True, text=True)
                        check(proc, errno.EACCES)
                        self.assertIn(f'cannot enumerate agentorch/editions/{edition} '
                                      f'(errno {errno.EACCES})', proc.stdout)
                    else:
                        # Injected CLI cases above remain mandatory on privileged systems.
                        self.assertTrue(os.access(directory, os.R_OK))
                finally:
                    directory.chmod(0o755)

    def test_validator_scan_flat_permission_fails_closed(self):
        import errno
        import subprocess
        from scripts import build_release
        root = self.tmp / 'flat'
        for rel, data in build_release.collect_sources(build_release.read_required()).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        docs = root / 'docs'
        (docs / 'synthetic-project.txt').write_text('synthetic', encoding='utf-8')
        cli = [sys.executable, '-I', '-B', str(root / 'scripts' / 'validate_kit.py')]
        readable = subprocess.run(cli, cwd=self.tmp, capture_output=True, text=True)
        self.assertNotEqual(readable.returncode, 0)
        self.assertIn('missing required document: docs/PROJECT.md', readable.stdout)
        wrapper = r'''
import os,sys,runpy
script,target,code,phase=sys.argv[1:]; original=os.scandir
class Broken:
    def __init__(self, inner): self.inner=inner; self.count=0
    def __enter__(self): self.inner.__enter__(); return self
    def __exit__(self,*args): return self.inner.__exit__(*args)
    def __iter__(self): return self
    def __next__(self):
        if self.count:
            print('INJECTION_REACHED',file=sys.stderr)
            raise OSError(int(code),'synthetic-secret-message',target)
        self.count+=1
        return next(self.inner)
def scan(path):
    if str(path)==target:
        if phase=='open':
            print('INJECTION_REACHED',file=sys.stderr)
            raise OSError(int(code),'synthetic-secret-message',target)
        return Broken(original(path))
    return original(path)
os.scandir=scan
sys.argv=[script]
runpy.run_path(script,run_name='__main__')
'''
        for number in (errno.EACCES, errno.EIO):
            for phase in ('open', 'iterate'):
                with self.subTest(errno=number, phase=phase):
                    proc = subprocess.run([sys.executable, '-I', '-B', '-c', wrapper,
                                           str(root / 'scripts/validate_kit.py'), str(docs),
                                           str(number), phase], cwd=self.tmp,
                                          capture_output=True, text=True)
                    output = proc.stdout + proc.stderr
                    self.assertIn('INJECTION_REACHED', output)
                    self.assertEqual(proc.returncode, 1, output)
                    self.assertIn('cannot enumerate docs', output)
                    self.assertIn(f'errno {number}', output)
                    self.assertNotIn(str(self.tmp), output)
                    self.assertNotIn('synthetic-secret-message', output)
                    self.assertNotIn('Traceback', output)
        docs.chmod(0o111)
        try:
            try:
                with os.scandir(docs):
                    pass
            except PermissionError:
                proc = subprocess.run(cli, cwd=self.tmp, capture_output=True, text=True)
                output = proc.stdout + proc.stderr
                self.assertEqual(proc.returncode, 1, output)
                self.assertIn('docs', output)
                self.assertIn(f'errno {errno.EACCES}', output)
                self.assertNotIn(str(self.tmp), output)
                self.assertNotIn('Traceback', output)
            else:
                self.assertTrue(os.access(docs, os.R_OK))
        finally:
            docs.chmod(0o755)

    def test_validator_scan_isolated_late_error_fails_closed(self):
        import subprocess
        agentorch, _ = self._validator_complete_tree()
        file = agentorch / 'editions/github/master.md'
        file.write_bytes(file.read_bytes() + b'\n[synthetic](missing-link.md)\n')
        manifest_path = agentorch / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        entry = next(e for e in manifest['files'] if e['path'] == 'editions/github/master.md')
        data = file.read_bytes()
        entry['sha256'] = hashlib.sha256(data).hexdigest()
        entry['size'] = len(data)
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        script = agentorch / 'scripts/validate_kit.py'
        cli = [sys.executable, '-I', '-B', str(script)]
        visible = subprocess.run(cli, cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(visible.returncode, 1, visible.stdout + visible.stderr)
        self.assertIn('broken relative link: missing-link.md', visible.stdout)
        wrapper = r'''
import os,sys,runpy,errno
script,target=sys.argv[1:]; original=os.scandir; count=0
def scan(path):
    global count
    if str(path)==target:
        count+=1
        if count>=2:
            print('LATE_EIO',file=sys.stderr)
            raise OSError(errno.EIO,'synthetic-private-message',str(path))
    return original(path)
os.scandir=scan
sys.argv=[script]
runpy.run_path(script,run_name='__main__')
'''
        proc = subprocess.run([sys.executable, '-I', '-B', '-c', wrapper,
                               str(script), str(agentorch / 'editions/github')],
                              cwd=self.tmp, capture_output=True, text=True)
        output = proc.stdout + proc.stderr
        self.assertIn('LATE_EIO', proc.stderr)
        self.assertEqual(proc.returncode, 1, output)
        self.assertIn('editions/github', output)
        self.assertIn('errno 5', output)
        self.assertNotIn('synthetic-private-message', output)
        self.assertNotIn(str(self.tmp), output)
        self.assertNotIn('Traceback', output)

    def test_validator_security_rejects_self_signed_noncanonical_body(self):
        import subprocess
        from unittest.mock import patch
        from scripts import validate_kit
        agentorch, version = self._validator_complete_tree()
        path = self.host / 'AGENTS.md'
        valid = path.read_text(encoding='utf-8')
        tampered = valid.replace('AgentOrch 作为本项目', 'tampered 作为本项目')
        lines = tampered.splitlines()
        begin = next(i for i, line in enumerate(lines) if line.startswith('<!-- AGENTORCH:BEGIN'))
        digest_line = next(i for i, line in enumerate(lines) if line.startswith('<!-- agentorch:managed-body-sha256:'))
        digest = hashlib.sha256(('\n'.join(lines[begin + 1:digest_line]) + '\n').encode()).hexdigest()
        lines[digest_line] = workspace.MANAGED_HASH_TEMPLATE.format(digest)
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        errors = validate_kit.validate_kit(agentorch)[0]
        self.assertTrue(any('canonical' in e for e in errors), errors)
        proc = subprocess.run([sys.executable, '-I', '-B', str(agentorch / 'scripts' / 'validate_kit.py')],
                              cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('not canonical', proc.stdout)
        path.write_text(valid.replace('<!-- AGENTORCH:END -->', '<!-- BROKEN END -->'), encoding='utf-8')
        self.assertTrue(any('END marker' in e for e in validate_kit.validate_kit(agentorch)[0]))
        path.write_text('# Synthetic host\n\n' + workspace.managed_block('999.1'), encoding='utf-8')
        self.assertTrue(any('block version' in e for e in validate_kit.validate_kit(agentorch)[0]))
        path.write_text(valid, encoding='utf-8')
        self.assertEqual(validate_kit.validate_kit(agentorch)[0], [])
        with patch.object(validate_kit.runpy, 'run_path', side_effect=OSError('missing source')):
            self.assertTrue(any('canonical block unavailable' in e
                                for e in validate_kit.validate_kit(agentorch)[0]))

    def test_validator_security_other_layout_regressions(self):
        import subprocess
        from scripts import validate_kit
        agentorch, _ = self._validator_complete_tree()
        cmd = [sys.executable, '-I', '-B', str(agentorch / 'scripts' / 'validate_kit.py')]
        proc = subprocess.run(cmd, cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        extra = agentorch / 'AGENTS.md'
        extra.write_text('extra', encoding='utf-8')
        self.assertTrue(any('must not exist' in e for e in validate_kit.validate_kit(agentorch)[0]))
        extra.unlink()
        manifest = agentorch / 'MANIFEST.json'
        original = manifest.read_bytes()
        manifest.write_text('{}', encoding='utf-8')
        self.assertTrue(validate_kit.validate_kit(agentorch)[0])
        manifest.write_bytes(original)
        self.assertEqual(validate_kit.validate_kit(ROOT)[0], [])

    def _review_linked_host(self):
        import subprocess
        repo = self.tmp / 'review-repository'
        shutil.copytree(self.host, repo)
        def git(*args):
            return subprocess.run(['git', '-C', str(repo), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git('init', '-q')
        git('config', 'user.email', 'synthetic@example.invalid')
        git('config', 'user.name', 'Synthetic Test')
        git('add', '--all')
        git('commit', '-qm', 'synthetic baseline')
        linked = self.tmp / 'review-worktree'
        git('worktree', 'add', '-q', '-b', 'task/review-synthetic', str(linked))
        return linked, git('rev-parse', 'HEAD')

    def _release_transport_from(self, source):
        """Build the archive the fixed official client would return for *source*.

        Only the network transport is substituted: release selection, the ten
        package checks, digest validation and every write still run for real.
        """
        import io
        import zipfile
        from scripts.update_agentorch import package, sources
        manifest_bytes = (source / 'MANIFEST.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for entry in manifest['files']:
                archive.writestr('agentorch/' + entry['path'],
                                 (source / entry['path']).read_bytes())
            archive.writestr('agentorch/MANIFEST.json', manifest_bytes)
        payload = buffer.getvalue()
        tag = 'v' + manifest['format_version']
        asset = sources.ReleaseAsset(
            'agentorch.zip', len(payload),
            'sha256:' + hashlib.sha256(payload).hexdigest(),
            f'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/{tag}/agentorch.zip')
        release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, tag, (asset,))
        checked = package.verify_package(release, payload, expected_suite=SUITE)
        if not checked.ok:
            raise AssertionError(
                f'synthetic transport did not verify: {checked.failed_check}')
        return release, payload

    def _tagged_release(self, source, tag):
        """Build a verified synthetic release with an explicit official tag.

        ``make_source`` derives the tag from the Format version; this helper
        keeps the Format and varies the release tag so a patch release
        (``v1.7.3``) can be modelled without inventing a new Format.
        """
        import io
        import zipfile
        from scripts.update_agentorch import package, sources
        manifest_bytes = (source / 'MANIFEST.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for entry in manifest['files']:
                archive.writestr('agentorch/' + entry['path'],
                                 (source / entry['path']).read_bytes())
            archive.writestr('agentorch/MANIFEST.json', manifest_bytes)
        payload = buffer.getvalue()
        asset = sources.ReleaseAsset(
            'agentorch.zip', len(payload),
            'sha256:' + hashlib.sha256(payload).hexdigest(),
            f'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/{tag}/agentorch.zip')
        release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, tag, (asset,))
        checked = package.verify_package(release, payload, expected_suite=SUITE)
        if not checked.ok:
            raise AssertionError(
                f'synthetic tagged release did not verify: {checked.failed_check}')
        return checked, release, payload

    def _review_release_transport(self):
        import io
        import zipfile
        from scripts.update_agentorch import sources
        files = {rel: (self.source / rel).read_bytes() for rel in SOURCE_FILES}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for rel, data in files.items():
                archive.writestr('agentorch/' + rel, data)
            archive.writestr('agentorch/MANIFEST.json',
                             workspace._manifest_bytes(files, SUITE, FORMAT_VERSION))
        payload = buffer.getvalue()
        asset = sources.ReleaseAsset('agentorch.zip', len(payload),
            'sha256:' + hashlib.sha256(payload).hexdigest(),
            'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip')
        return sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, 'v1.7', (asset,)), payload

    def test_review_e2e_verified_install(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._review_linked_host()
        release, payload = self._review_release_transport()
        original = (host / 'AGENTS.md').read_bytes()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            prepared = entry.prepare_isolated_install(host, mode='APPLY',
                        declared_exclusive=True, expected_head=head)
        self.assertEqual(prepared['result']['status'], 'APPLIED', prepared['result'])
        self.assertEqual((host / 'agentorch' / 'LICENSE.md').read_bytes(),
                         SOURCE_FILES['LICENSE.md'].encode())
        self.assertTrue((host / 'AGENTS.md').read_bytes().startswith(original))

    def _assert_xattr_install_blocked(self, result, host, target, original, info,
                                      snapshot, xattr_value):
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertEqual(result['written'], [], result)
        self.assertEqual(result['rollback']['result'], 'NOT_NEEDED', result)
        self.assertFalse((host / 'agentorch').exists())
        self.assertEqual(raw_snapshot(host, skip_names=('.git',)), snapshot)
        self.assertEqual(target.read_bytes(), original)
        after = target.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid),
                         (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid))
        self.assertEqual(after.st_mtime_ns, info.st_mtime_ns)
        self.assertEqual(os.getxattr(target, 'user.review'), xattr_value)

    def test_review_e2e_install_with_xattr_fails_closed(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._review_linked_host()
        release, payload = self._review_release_transport()
        target = host / 'AGENTS.md'
        target.chmod(0o640)
        os.setxattr(target, 'user.review', b'preserve-me')
        original = target.read_bytes()
        info = target.stat()
        before = raw_snapshot(host, skip_names=('.git',))
        old_umask = os.umask(0o077)
        try:
            with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
                 patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
                result = entry.prepare_isolated_install(host, mode='APPLY',
                            declared_exclusive=True, expected_head=head)['result']
        finally:
            os.umask(old_umask)
        self._assert_xattr_install_blocked(
            result, host, target, original, info, before, b'preserve-me')
        self.assertTrue(any('extended attributes' in problem
                            for problem in result['problems']), result)

    def test_review_inplace_append_with_xattr_fails_closed(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._review_linked_host()
        release, payload = self._review_release_transport()
        target = host / 'AGENTS.md'
        target.chmod(0o640)
        os.setxattr(target, 'user.review', b'metadata')
        original = target.read_bytes()
        info = target.stat()
        before = raw_snapshot(host, skip_names=('.git',))
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = entry.prepare_isolated_install(host, mode='APPLY',
                        declared_exclusive=True, expected_head=head)['result']
        self._assert_xattr_install_blocked(
            result, host, target, original, info, before, b'metadata')

    def test_review_inplace_short_write_with_xattr_fails_closed(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._review_linked_host()
        release, payload = self._review_release_transport()
        target = host / 'AGENTS.md'
        target.chmod(0o640)
        os.setxattr(target, 'user.review', b'metadata')
        original = target.read_bytes()
        info = target.stat()
        before = raw_snapshot(host, skip_names=('.git',))
        real_write = os.write
        calls = []
        def inject(fd, data):
            calls.append(True)
            return real_write(fd, data)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace.os, 'write', side_effect=inject):
            result = entry.prepare_isolated_install(host, mode='APPLY',
                        declared_exclusive=True, expected_head=head)['result']
        self.assertEqual(calls, [], 'an xattr target must be refused before any write')
        self._assert_xattr_install_blocked(
            result, host, target, original, info, before, b'metadata')

    def test_review_e2e_rollback_with_xattr_fails_closed(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._review_linked_host()
        release, payload = self._review_release_transport()
        target = host / 'AGENTS.md'
        original = target.read_bytes()
        target.chmod(0o640)
        os.setxattr(target, 'user.review', b'preserve-me')
        info = target.stat()
        before = raw_snapshot(host, skip_names=('.git',))
        old_umask = os.umask(0o077)
        try:
            with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
                 patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
                result = entry.prepare_isolated_install(host, mode='APPLY',
                            declared_exclusive=True, expected_head=head)['result']
        finally:
            os.umask(old_umask)
        self._assert_xattr_install_blocked(
            result, host, target, original, info, before, b'preserve-me')

    def test_review_e2e_unproven_metadata_fails_closed(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._review_linked_host()
        release, payload = self._review_release_transport()
        target = host / 'AGENTS.md'
        target.chmod(0o640)
        os.setxattr(target, 'user.review', b'preserve-me')
        original = target.read_bytes()
        info = target.stat()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = entry.prepare_isolated_install(host, mode='APPLY',
                        declared_exclusive=True, expected_head=head)['result']
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertFalse((host / 'agentorch').exists())
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.stat().st_mode & 0o7777, 0o640)
        self.assertEqual((target.stat().st_uid, target.stat().st_gid), (info.st_uid, info.st_gid))
        self.assertEqual((target.stat().st_ino, target.stat().st_mtime_ns),
                         (info.st_ino, info.st_mtime_ns))
        self.assertEqual(os.getxattr(target, 'user.review'), b'preserve-me')

    def test_review_metadata_low_level_replacement_fails_closed(self):
        target = self.host / 'AGENTS.md'
        original = target.read_bytes()
        target.chmod(0o640)
        os.setxattr(target, 'user.review', b'preserve-me')
        root_fd = os.open(self.host, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(workspace.TransactionConflict):
                workspace._append_file(root_fd, 'AGENTS.md', original, original + b'x')
            with self.assertRaises(workspace.TransactionConflict):
                workspace._replace_file_at(root_fd, 'AGENTS.md', original, original + b'x')
        finally:
            os.close(root_fd)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.stat().st_mode & 0o7777, 0o640)
        self.assertEqual(os.getxattr(target, 'user.review'), b'preserve-me')

    def test_review_e2e_concurrent_equal_file_survives(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._review_linked_host()
        release, payload = self._review_release_transport()
        target = host / 'agentorch' / '.gitignore'
        observed = []
        real_hook = workspace._invoke_hook
        def inject(fault, name, *args):
            if name == 'before_write' and args == ('agentorch/.gitignore',):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(workspace.AGENTORCH_GITIGNORE.encode())
                observed.append(True)
            return real_hook(fault, name, *args)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace, '_invoke_hook', side_effect=inject):
            prepared = entry.prepare_isolated_install(host, mode='APPLY',
                        declared_exclusive=True, expected_head=head)
        self.assertEqual(observed, [True])
        self.assertEqual(target.read_bytes(), workspace.AGENTORCH_GITIGNORE.encode())
        self.assertNotEqual(prepared['result']['rollback']['result'], 'SUCCEEDED')

    def test_review_e2e_short_agents_write_restores_original(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        host, head = self._review_linked_host()
        release, payload = self._review_release_transport()
        target = host / 'AGENTS.md'
        original = target.read_bytes()
        before = target.stat()
        expected = workspace._agents_update(original.decode(),
                    workspace.managed_block(FORMAT_VERSION))['text'].encode()
        suffix = expected[len(original):]
        self.assertNotEqual(suffix, expected)
        real_write = workspace._write_all
        observed = []
        def inject(fd, data):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (before.st_dev, before.st_ino):
                observed.append(data)
                os.write(fd, data[:20])
                raise OSError('injected partial AGENTS write')
            return real_write(fd, data)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace, '_write_all', side_effect=inject):
            prepared = entry.prepare_isolated_install(host, mode='APPLY',
                        declared_exclusive=True, expected_head=head)
        self.assertEqual(observed, [suffix], 'only the appended suffix may be written')
        self.assertEqual(target.read_bytes(), original)
        after = target.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid),
                         (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid))
        self.assertEqual(prepared['result']['rollback']['result'], 'SUCCEEDED')

    def test_review_e2e_forged_plan_cannot_write_nonofficial_bytes(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        host, head = self._review_linked_host()
        release, payload = self._review_release_transport()
        plan = workspace.plan_isolated_onboarding(host, source_root=self.source)
        plan.files['LICENSE.md'] = b'nonofficial content'
        fake = SimpleNamespace(ok=True, checks=[{'check': 'ASSET_SHA256',
            'result': 'PASS', 'detail': 'sha256=' + 'a' * 64}],
            manifest_bytes=b'fake', files={})
        plan.source_binding = workspace.bind_verified_source(fake, plan)
        before = raw_snapshot(host, skip_names=('.git',))
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release) as fetch, \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload) as download:
            result = workspace.apply_isolated_onboarding(plan, declared_exclusive=True,
                                                         expected_head=head)
        self.assertGreaterEqual(fetch.call_count, 1)
        self.assertGreaterEqual(download.call_count, 1)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertIn('official write set or host preconditions changed',
                      result['problems'], result)
        self.assertFalse((host / 'agentorch').exists())
        self.assertEqual(raw_snapshot(host, skip_names=('.git',)), before)

    def test_review_e2e_direct_apply_rechecks_worktree_after_backup(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        host, head = self._review_linked_host()
        release, payload = self._review_release_transport()
        plan = workspace.plan_isolated_onboarding(host, source_root=self.source,
                                                  verified_source=self.verified)
        real_backup = workspace.create_backup
        def dirty_worktree(*args, **kwargs):
            backup = real_backup(*args, **kwargs)
            (host / 'README.md').write_text('concurrent edit\n')
            return backup
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace, 'create_backup', side_effect=dirty_worktree):
            result = workspace.apply_isolated_onboarding(plan, declared_exclusive=True,
                                                         expected_head=head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertIn('worktree or host changed before writing', result['problems'], result)
        self.assertEqual((host / 'README.md').read_text(), 'concurrent edit\n')
        self.assertFalse((host / 'agentorch').exists())

    def test_review_forged_release_result_never_authorizes_writes(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        head = self._install_linked_worktree()
        fake = SimpleNamespace(ok=True, checks=[{'check': 'ASSET_SHA256',
            'result': 'PASS', 'detail': 'sha256=' + 'a' * 64}],
            manifest_bytes=b'fake', files={})
        plan = self.plan_unverified()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        plan.source_binding = workspace.bind_verified_source(fake, plan)
        before = raw_snapshot(self.host)
        # The forged result is not the official source: APPLY refetches through
        # the fixed client and must refuse a plan that no longer matches it.
        other = make_source(self.tmp / 'other-source', overrides={
            'README.md': '# Another release\n\nRules: [AGENTS.md](AGENTS.md).\n'})
        release, payload = self._release_transport_from(other)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertIn('official write set or host preconditions changed',
                      result['problems'], result)
        self.assertEqual(raw_snapshot(self.host), before)
        self.assertFalse((self.host / 'agentorch').exists())

    def test_review_concurrent_identical_creation_is_not_deleted(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        head = self._install_linked_worktree()
        plan = self.plan()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        rel = sorted(r for r in plan.writes if r != 'AGENTS.md')[0]
        target = self.host / rel
        class Fault:
            def before_write(self, path):
                if path == rel:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(plan.writes[rel])
        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, fault_injector=Fault(),
                declared_exclusive=True, expected_head=head)
        self.assertTrue(target.exists(), 'concurrent file must remain present')
        self.assertEqual(target.read_bytes(), plan.writes[rel])
        self.assertNotEqual(result['rollback']['result'], 'SUCCEEDED')

    def test_review_partial_append_restores_original(self):
        from unittest.mock import patch
        target = self.host / 'AGENTS.md'
        original = target.read_bytes()
        expected = original + b'new managed block'
        suffix = expected[len(original):]
        before = target.stat()
        calls = []
        real_write_all = workspace._write_all
        def short_write(fd, data):
            calls.append(data)
            real_write_all(fd, data[:20])
            raise OSError('injected short write')
        record = {'rel': 'AGENTS.md', 'kind': 'append', 'original': original,
                  'expected': expected}
        root_fd = os.open(self.host, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with patch.object(workspace, '_write_all', side_effect=short_write):
                with self.assertRaises(OSError):
                    workspace._append_file(root_fd, 'AGENTS.md', original, expected)
            result = workspace._rollback_transaction(root_fd, [record], [], None)
        finally:
            os.close(root_fd)
        self.assertEqual(calls, [suffix], 'the append must only write the new suffix')
        self.assertEqual((self.host / 'AGENTS.md').read_bytes(), original)
        after = target.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid),
                         (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid))
        self.assertEqual(result['result'], 'SUCCEEDED')

    def test_review_transaction_eexist_never_deletes_unowned_inode(self):
        rel = 'agentorch/race.txt'
        target = self.host / rel
        target.parent.mkdir()
        target.write_bytes(b'other writer')
        record = {'rel': rel, 'kind': 'create', 'original': None,
                  'expected': b'other writer'}
        root_fd = os.open(self.host, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(workspace.TransactionConflict):
                workspace._write_new_file(root_fd, rel, record['expected'])
            result = workspace._rollback_transaction(root_fd, [record], [], None)
        finally:
            os.close(root_fd)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b'other writer')
        self.assertEqual(result['result'], 'FAILED')

    def test_review_transaction_short_write_preserves_original(self):
        from unittest.mock import patch
        target = self.host / 'AGENTS.md'
        original = target.read_bytes()
        expected = original + b'x' * 80
        suffix = expected[len(original):]
        before = target.stat()
        calls = []
        record = {'rel': 'AGENTS.md', 'kind': 'append', 'original': original,
                  'expected': expected}
        root_fd = os.open(self.host, os.O_RDONLY | os.O_DIRECTORY)
        def short_write(fd, data):
            calls.append(data)
            os.write(fd, data[:20])
            raise OSError('injected short write')
        try:
            with patch.object(workspace, '_write_all', side_effect=short_write):
                with self.assertRaises(OSError):
                    workspace._append_file(root_fd, 'AGENTS.md', original, expected)
            result = workspace._rollback_transaction(root_fd, [record], [], None)
        finally:
            os.close(root_fd)
        self.assertEqual(calls, [suffix])
        self.assertEqual(target.read_bytes(), original)
        after = target.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid),
                         (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid))
        self.assertEqual(result['result'], 'SUCCEEDED')

    def test_review_transaction_created_inode_is_rolled_back(self):
        rel = 'agentorch/created.txt'
        record = {'rel': rel, 'kind': 'create', 'original': None, 'expected': b'ours'}
        root_fd = os.open(self.host, os.O_RDONLY | os.O_DIRECTORY)
        try:
            workspace._write_new_file(root_fd, rel, b'ours', record=record)
            self.assertIn('created_identity', record)
            result = workspace._rollback_transaction(root_fd, [record], [], None)
        finally:
            os.close(root_fd)
        self.assertFalse((self.host / rel).exists())
        self.assertEqual(result['result'], 'SUCCEEDED')

    def test_review_transaction_append_failure_restores_original_in_place(self):
        from unittest.mock import patch
        target = self.host / 'AGENTS.md'
        original = target.read_bytes()
        expected = original + b'new managed block'
        suffix = expected[len(original):]
        before = target.stat()
        calls = []
        real_write_all = workspace._write_all
        def fail_after_full_append(fd, data):
            calls.append(data)
            real_write_all(fd, data)
            raise OSError('injected after the append reached disk')
        record = {'rel': 'AGENTS.md', 'kind': 'append', 'original': original,
                  'expected': expected}
        root_fd = os.open(self.host, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with patch.object(workspace, '_write_all', side_effect=fail_after_full_append):
                with self.assertRaises(OSError):
                    workspace._append_file(root_fd, 'AGENTS.md', original, expected)
            result = workspace._rollback_transaction(root_fd, [record], [], None)
        finally:
            os.close(root_fd)
        self.assertEqual(calls, [suffix], 'the append must only write the new suffix')
        self.assertEqual(target.read_bytes(), original)
        after = target.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid),
                         (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid))
        self.assertEqual(result['result'], 'SUCCEEDED')

    def test_review_transaction_append_conflict_preserves_other_bytes(self):
        from unittest.mock import patch
        target = self.host / 'AGENTS.md'
        original = target.read_bytes()
        expected = original + b'new managed block'
        suffix = expected[len(original):]
        before = target.stat()
        calls = []
        record = {'rel': 'AGENTS.md', 'kind': 'append', 'original': original,
                  'expected': expected}
        real_write = workspace._write_all
        def change_before_verify(fd, data):
            calls.append(data)
            real_write(fd, data)
            target.write_bytes(b'concurrent change')
        root_fd = os.open(self.host, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with patch.object(workspace, '_write_all', side_effect=change_before_verify):
                with self.assertRaises(workspace.TransactionConflict):
                    workspace._append_file(root_fd, 'AGENTS.md', original, expected)
            result = workspace._rollback_transaction(root_fd, [record], [], None)
        finally:
            os.close(root_fd)
        self.assertEqual(calls, [suffix])
        self.assertEqual(target.read_bytes(), b'concurrent change')
        after = target.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid),
                         (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid))
        self.assertEqual(result['result'], 'FAILED')

    def test_review_transaction_restore_failure_is_reported(self):
        from unittest.mock import patch
        target = self.host / 'AGENTS.md'
        original = target.read_bytes()
        expected = original + b'new managed block'
        record = {'rel': 'AGENTS.md', 'kind': 'append', 'original': original,
                  'expected': expected}
        before = target.stat()
        root_fd = os.open(self.host, os.O_RDONLY | os.O_DIRECTORY)
        try:
            workspace._append_file(root_fd, 'AGENTS.md', original, expected)
            with patch.object(workspace, '_truncate_at', side_effect=OSError('restore failed')):
                result = workspace._rollback_transaction(root_fd, [record], [], None)
        finally:
            os.close(root_fd)
        self.assertEqual(target.read_bytes(), expected)
        after = target.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid),
                         (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid))
        self.assertEqual(result['result'], 'FAILED')
        self.assertTrue(result['failures'])

    def install(self, **kwargs):
        # A real APPLY is only permitted inside a dedicated, clean linked
        # worktree with an explicit exclusivity declaration (docs/upgrade.md
        # section 16). Positive-install cases must establish that declared
        # precondition instead of calling the low-level apply from a plain
        # directory, where the product correctly fails closed. APPLY also
        # re-fetches through the fixed client, so the test substitutes only the
        # network transport, as the module docstring requires.
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        head = self._install_linked_worktree()
        plan = self.plan(**kwargs)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        return plan, result

    def _install_full_release(self):
        from unittest.mock import patch
        from scripts import build_release
        from scripts.update_agentorch import sources
        source = self.tmp / 'full-source'
        files = build_release.collect_sources(build_release.read_required())
        for rel, data in files.items():
            target = source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (source / 'MANIFEST.json').write_bytes(
            workspace._manifest_bytes(files, SUITE, FORMAT_VERSION))
        verified = verified_package(source)
        head = self._install_linked_worktree()
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=source, verified_source=verified)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        release, payload = self._release_transport_from(source)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'APPLIED', result)
        return self.host / 'agentorch'

    def _linked_worktree_host(self):
        """Turn self.host into a clean linked Git worktree with fixture content."""
        import subprocess
        repo = self.tmp / 'cli-repo'
        repo.mkdir()
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'Synthetic Test',
               'GIT_AUTHOR_EMAIL': 'synthetic@example.invalid',
               'GIT_COMMITTER_NAME': 'Synthetic Test',
               'GIT_COMMITTER_EMAIL': 'synthetic@example.invalid'}
        def git_at(cwd, *args):
            return subprocess.run(['git', '-C', str(cwd), *args], check=True, env=env,
                                  capture_output=True, text=True).stdout.strip()
        def git(*args):
            return git_at(repo, *args)
        git('init', '-q')
        git('config', 'user.email', 'synthetic@example.invalid')
        git('config', 'user.name', 'Synthetic Test')
        (repo / 'base.txt').write_text('synthetic baseline\n', encoding='utf-8')
        git('add', 'base.txt')
        git('commit', '-qm', 'baseline')
        shutil.rmtree(self.host)
        git('worktree', 'add', '-q', '-b', 'task/synthetic-host', str(self.host))
        for directory, _dirnames, filenames in os.walk(FIXTURE_HOST):
            relative = Path(directory).relative_to(FIXTURE_HOST)
            for name in filenames:
                target = self.host / relative / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(directory) / name, target)
        git_at(self.host, 'add', '-A')
        git_at(self.host, 'commit', '-qm', 'synthetic host content')
        return git_at(self.host, 'rev-parse', 'HEAD')

    def _install_linked_worktree(self):
        """Turn self.host into a clean linked worktree without changing its bytes.

        Positive install cases must run inside the dedicated worktree that
        ``docs/upgrade.md`` section 16 declares as an install precondition. This
        preserves whatever the test staged in ``self.host`` first, so a case
        that removed ``AGENTS.md`` still exercises the "create the root entry"
        branch inside the real guarded transaction.
        """
        import subprocess
        repo = self.tmp / 'install-repository'
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'Synthetic Test',
               'GIT_AUTHOR_EMAIL': 'synthetic@example.invalid',
               'GIT_COMMITTER_NAME': 'Synthetic Test',
               'GIT_COMMITTER_EMAIL': 'synthetic@example.invalid'}

        def git_at(cwd, *args):
            return subprocess.run(['git', '-C', str(cwd), *args], check=True, env=env,
                                  capture_output=True, text=True).stdout.strip()

        if not repo.exists():
            repo.mkdir()
            git_at(repo, 'init', '-q')
            git_at(repo, 'config', 'user.email', 'synthetic@example.invalid')
            git_at(repo, 'config', 'user.name', 'Synthetic Test')
            # Empty baseline: the worktree starts with no extra harness file,
            # so host snapshots see exactly the test's own fixture content.
            git_at(repo, 'commit', '--allow-empty', '-qm', 'baseline')
        try:
            toplevel = Path(git_at(self.host, 'rev-parse', '--show-toplevel')).resolve()
            gitdir = Path(git_at(self.host, 'rev-parse', '--absolute-git-dir')).resolve()
            common = Path(git_at(self.host, 'rev-parse', '--path-format=absolute',
                                 '--git-common-dir')).resolve()
            dirty = git_at(self.host, 'status', '--porcelain=v1', '--untracked-files=all')
            if (toplevel == self.host.resolve() and gitdir.parent == common / 'worktrees'
                    and not dirty):
                return git_at(self.host, 'rev-parse', 'HEAD')
        except (OSError, subprocess.CalledProcessError):
            pass
        staging = self.tmp / 'install-staging'
        shutil.move(str(self.host), str(staging))
        git_at(repo, 'worktree', 'add', '-q', '-b', 'task/synthetic-install', str(self.host))
        for path in staging.rglob('*'):
            relative = path.relative_to(staging)
            target = self.host / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                target.symlink_to(os.readlink(path))
            elif path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                shutil.copy2(path, target)
        shutil.rmtree(staging)
        git_at(self.host, 'add', '-A')
        git_at(self.host, 'commit', '-qm', 'synthetic host content')
        return git_at(self.host, 'rev-parse', 'HEAD')

    def _git_env(self):
        return {**os.environ, 'GIT_AUTHOR_NAME': 'Synthetic Test',
                'GIT_AUTHOR_EMAIL': 'synthetic@example.invalid',
                'GIT_COMMITTER_NAME': 'Synthetic Test',
                'GIT_COMMITTER_EMAIL': 'synthetic@example.invalid'}

    def _make_linked_worktree(self, target, *, repo, branch):
        """Create a clean linked worktree at *target* holding the fixture host."""
        import subprocess

        def git_at(cwd, *args):
            return subprocess.run(['git', '-C', str(cwd), *args], check=True,
                                  env=self._git_env(), capture_output=True,
                                  text=True).stdout.strip()

        if not repo.exists():
            repo.mkdir(parents=True)
            git_at(repo, 'init', '-q')
            git_at(repo, 'config', 'user.email', 'synthetic@example.invalid')
            git_at(repo, 'config', 'user.name', 'Synthetic Test')
            git_at(repo, 'commit', '--allow-empty', '-qm', 'baseline')
        git_at(repo, 'worktree', 'add', '-q', '-b', branch, str(target))
        for directory, _dirnames, filenames in os.walk(FIXTURE_HOST):
            relative = Path(directory).relative_to(FIXTURE_HOST)
            for name in filenames:
                destination = target / relative / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(directory) / name, destination)
        git_at(target, 'add', '-A')
        git_at(target, 'commit', '-qm', 'synthetic host content')
        return git_at(target, 'rev-parse', 'HEAD')

    def _commit_all(self, host, message):
        """Commit a test-staged change so the worktree stays a declared clean state."""
        import subprocess
        subprocess.run(['git', '-C', str(host), 'add', '-A'], check=True,
                       env=self._git_env(), capture_output=True, text=True)
        subprocess.run(['git', '-C', str(host), 'commit', '-qm', message], check=True,
                       env=self._git_env(), capture_output=True, text=True)
        return subprocess.run(['git', '-C', str(host), 'rev-parse', 'HEAD'], check=True,
                              env=self._git_env(), capture_output=True,
                              text=True).stdout.strip()

    def test_actual_isolated_install_passes_shipped_validator(self):
        import subprocess
        from unittest.mock import patch
        from scripts import build_release
        from scripts.update_agentorch import sources
        source = self.tmp / 'full-source'
        files = build_release.collect_sources(build_release.read_required())
        for rel, data in files.items():
            target = source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (source / 'MANIFEST.json').write_bytes(
            workspace._manifest_bytes(files, SUITE, FORMAT_VERSION))
        head = self._install_linked_worktree()
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=source, verified_source=verified_package(source))
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        release, payload = self._release_transport_from(source)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'APPLIED', result)
        proc = subprocess.run([sys.executable, str(self.host / 'agentorch' / 'scripts' /
                                                   'validate_kit.py')],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_shipped_validator_rejects_isolated_layout_tampering(self):
        from scripts import validate_kit
        agentorch = self._install_full_release()
        self.assertEqual(validate_kit.validate_kit(agentorch)[0], [])
        self.assertEqual(validate_kit.validate_kit(ROOT)[0], [])

        agents = self.host / 'AGENTS.md'
        original = agents.read_bytes()
        agents.unlink()
        errors = validate_kit.validate_kit(agentorch)[0]
        self.assertTrue(any('AGENTS.md' in error for error in errors), errors)
        agents.write_bytes(original)

        edited = original.replace(b'AgentOrch Root', b'AgentOrch Root (hand edited)', 1)
        agents.write_bytes(edited)
        errors = validate_kit.validate_kit(agentorch)[0]
        self.assertTrue(any('managed block' in error for error in errors), errors)
        agents.write_bytes(original)

        (agentorch / 'stray.txt').write_text('not part of the release\n', encoding='utf-8')
        errors = validate_kit.validate_kit(agentorch)[0]
        self.assertTrue(any('stray.txt' in error for error in errors), errors)
        (agentorch / 'stray.txt').unlink()

        victim = self.tmp / 'validator-symlink-target.md'
        victim.write_text('outside\n', encoding='utf-8')
        (agentorch / 'LICENSE.md').unlink()
        (agentorch / 'LICENSE.md').symlink_to(victim)
        errors = validate_kit.validate_kit(agentorch)[0]
        self.assertTrue(any('symlink' in error for error in errors), errors)
        (agentorch / 'LICENSE.md').unlink()

        (agentorch / 'AGENTS.md').write_text('second authority\n', encoding='utf-8')
        errors = validate_kit.validate_kit(agentorch)[0]
        self.assertTrue(any('AGENTS.md' in error for error in errors), errors)

    def test_security_source_manifest_rejects_parent_path(self):
        victim = self.tmp / 'outside.txt'
        victim.write_bytes(b'outside private data')
        manifest_path = self.source / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files'].append({'path': '../outside.txt', 'category': 'core',
                                  'sha256': _sha(victim.read_bytes()),
                                  'size': victim.stat().st_size})
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(workspace.OnboardingError):
            self.plan_unverified()

    def test_link_rewrite_is_lexical_not_symlink_dependent(self):
        alias = self.source / 'alias.md'
        alias.symlink_to(self.source / 'AGENTS.md')
        text = b'[real](AGENTS.md) [alias](alias.md)\n'
        got = workspace._rewrite_host_agents_links('README.md', text, self.source, self.host)
        self.assertEqual(got, b'[real](../AGENTS.md) [alias](alias.md)\n')

    def test_official_release_acquisition_returns_verified_archive_bytes(self):
        import io
        import zipfile
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        files = {rel: (self.source / rel).read_bytes() for rel in SOURCE_FILES}
        manifest = workspace._manifest_bytes(files, SUITE, FORMAT_VERSION)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for rel, data in files.items():
                archive.writestr('agentorch/' + rel, data)
            archive.writestr('agentorch/MANIFEST.json', manifest)
        payload = buffer.getvalue()
        asset = sources.ReleaseAsset('agentorch.zip', len(payload),
            'sha256:' + hashlib.sha256(payload).hexdigest(),
            'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip')
        release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, 'v1.7', (asset,))
        function = getattr(workspace, 'fetch_verified_onboarding_release', None)
        self.assertTrue(callable(function), 'official source acquisition is missing')
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = function()
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.files, files)
        self.assertEqual(result.manifest_bytes, manifest)

    def test_official_release_acquisition_rejects_bad_digest(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        asset = sources.ReleaseAsset('agentorch.zip', 3, 'sha256:' + '0' * 64,
            'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip')
        release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, 'v1.7', (asset,))
        function = getattr(workspace, 'fetch_verified_onboarding_release', None)
        self.assertTrue(callable(function), 'official source acquisition is missing')
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=b'bad'):
            with self.assertRaises(workspace.OnboardingError):
                function()

    def test_official_release_network_failure_never_retries(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        asset = sources.ReleaseAsset('agentorch.zip', 3, 'sha256:' + '0' * 64,
            'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip')
        release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, 'v1.7', (asset,))
        for failed_stage in ('metadata', 'asset'):
            with self.subTest(failed_stage=failed_stage):
                with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                                  side_effect=OSError('offline') if failed_stage == 'metadata' else None,
                                  return_value=release) as fetch, \
                     patch.object(sources.GitHubReleaseSource, 'download_asset',
                                  side_effect=OSError('offline')) as download, \
                     patch('time.sleep'):
                    with self.assertRaises(workspace.OnboardingError):
                        workspace.fetch_verified_onboarding_release()
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(download.call_count, 0 if failed_stage == 'metadata' else 1)

    def test_entry_plan_rejects_locally_forged_source(self):
        import io
        import zipfile
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        files = {rel: (self.source / rel).read_bytes() for rel in SOURCE_FILES}
        manifest = workspace._manifest_bytes(files, SUITE, FORMAT_VERSION)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for rel, data in files.items():
                archive.writestr('agentorch/' + rel, data)
            archive.writestr('agentorch/MANIFEST.json', manifest)
        payload = buffer.getvalue()
        asset = sources.ReleaseAsset('agentorch.zip', len(payload),
            'sha256:' + hashlib.sha256(payload).hexdigest(),
            'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip')
        release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, 'v1.7', (asset,))
        forged = make_source(self.tmp / 'forged', overrides={'README.md': 'forged local content\n'})
        before = raw_snapshot(self.host)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            prepared = entry.prepare_isolated_install(self.host, source_root=forged, mode='PLAN')
        self.assertNotEqual(prepared['result']['status'], 'PLANNED')
        self.assertEqual(raw_snapshot(self.host), before)

    def test_entry_plan_accepts_matching_official_bytes_without_writing_host(self):
        import io
        import zipfile
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        files = {rel: (self.source / rel).read_bytes() for rel in SOURCE_FILES}
        manifest = workspace._manifest_bytes(files, SUITE, FORMAT_VERSION)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for rel, data in files.items():
                archive.writestr('agentorch/' + rel, data)
            archive.writestr('agentorch/MANIFEST.json', manifest)
        payload = buffer.getvalue()
        asset = sources.ReleaseAsset('agentorch.zip', len(payload),
            'sha256:' + hashlib.sha256(payload).hexdigest(),
            'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip')
        release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, 'v1.7', (asset,))
        before = raw_snapshot(self.host)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            prepared = entry.prepare_isolated_install(self.host, source_root=self.source, mode='PLAN')
        self.assertEqual(prepared['result']['status'], 'PLANNED', prepared['result'])
        self.assertEqual(raw_snapshot(self.host), before)

    def test_entry_default_plan_uses_verified_bytes_not_local_source(self):
        import io
        import zipfile
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        files = {rel: (self.source / rel).read_bytes() for rel in SOURCE_FILES}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for rel, data in files.items():
                archive.writestr('agentorch/' + rel, data)
            archive.writestr('agentorch/MANIFEST.json',
                             workspace._manifest_bytes(files, SUITE, FORMAT_VERSION))
        payload = buffer.getvalue()
        asset = sources.ReleaseAsset('agentorch.zip', len(payload),
            'sha256:' + hashlib.sha256(payload).hexdigest(),
            'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip')
        release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, 'v1.7', (asset,))
        before = raw_snapshot(self.host)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload), \
             patch.object(workspace, '_read_source', return_value=({}, {}, FORMAT_VERSION, SUITE)):
            prepared = entry.prepare_isolated_install(self.host, mode='PLAN')
        self.assertEqual(prepared['result']['status'], 'PLANNED', prepared['result'])
        self.assertEqual(prepared['plan'].files['VERSION.md'], files['VERSION.md'])
        self.assertEqual(raw_snapshot(self.host), before)

    def test_worktree_preflight_requires_declared_exclusivity_and_clean_linked_worktree(self):
        import subprocess
        from scripts.update_agentorch import entry
        repo = self.tmp / 'repository'
        repo.mkdir()
        def git(*args):
            return subprocess.run(['git', '-C', str(repo), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git('init', '-q')
        git('config', 'user.email', 'synthetic@example.invalid')
        git('config', 'user.name', 'Synthetic Test')
        (repo / 'tracked.txt').write_text('synthetic\n')
        git('add', 'tracked.txt')
        git('commit', '-qm', 'synthetic baseline')
        linked = self.tmp / 'linked-worktree'
        git('worktree', 'add', '-q', '-b', 'task/synthetic', str(linked))
        head = git('rev-parse', 'HEAD')
        function = getattr(entry, '_onboarding_worktree_preflight', None)
        self.assertTrue(callable(function), 'worktree preflight is missing')
        result = function(linked, declared_exclusive=True)
        self.assertEqual(result['status'], 'OBSERVED', result)
        self.assertEqual(result['head_sha'], head)
        self.assertEqual(result['exclusive_writer'], 'DECLARED_NOT_VERIFIED')
        self.assertEqual(function(linked, declared_exclusive=False)['status'], 'BLOCKED')
        self.assertEqual(function(repo, declared_exclusive=True)['status'], 'BLOCKED')
        (linked / 'tracked.txt').write_text('changed\n')
        self.assertEqual(function(linked, declared_exclusive=True)['status'], 'BLOCKED')

    def test_worktree_preflight_rejects_changed_expected_head(self):
        import inspect
        import subprocess
        from scripts.update_agentorch import entry
        repo = self.tmp / 'head-repo'
        repo.mkdir()
        def git(*args):
            return subprocess.run(['git', '-C', str(repo), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git('init', '-q')
        git('config', 'user.email', 'synthetic@example.invalid')
        git('config', 'user.name', 'Synthetic Test')
        (repo / 'tracked.txt').write_text('synthetic\n')
        git('add', 'tracked.txt')
        git('commit', '-qm', 'synthetic baseline')
        linked = self.tmp / 'head-linked'
        git('worktree', 'add', '-q', '-b', 'task/head-synthetic', str(linked))
        head = git('rev-parse', 'HEAD')
        self.assertIn('expected_head', inspect.signature(entry._onboarding_worktree_preflight).parameters)
        result = entry._onboarding_worktree_preflight(linked, declared_exclusive=True,
                                                       expected_head='0' * len(head))
        self.assertEqual(result['status'], 'BLOCKED', result)

    def test_entry_apply_reverifies_official_release_and_rejects_change(self):
        import io
        import zipfile
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        files = {rel: (self.source / rel).read_bytes() for rel in SOURCE_FILES}

        def make_release(current):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as archive:
                for rel, data in current.items():
                    archive.writestr('agentorch/' + rel, data)
                archive.writestr('agentorch/MANIFEST.json',
                                 workspace._manifest_bytes(current, SUITE, FORMAT_VERSION))
            payload = buf.getvalue()
            asset = sources.ReleaseAsset('agentorch.zip', len(payload),
                'sha256:' + hashlib.sha256(payload).hexdigest(),
                'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip')
            return sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, 'v1.7', (asset,)), payload

        release1, payload1 = make_release(files)
        release2, payload2 = make_release({**files, 'README.md': b'changed release\n'})
        before = raw_snapshot(self.host)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                          side_effect=[release1, release2]), \
             patch.object(sources.GitHubReleaseSource, 'download_asset',
                          side_effect=[payload1, payload2]):
            prepared = entry.prepare_isolated_install(self.host, source_root=self.source, mode='APPLY')
        self.assertEqual(prepared['result']['status'], 'STOPPED_UNSAFE_SOURCE', prepared['result'])
        self.assertEqual(raw_snapshot(self.host), before)

    def test_entry_apply_stops_if_agents_changes_during_source_reverification(self):
        import io
        import zipfile
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        files = {rel: (self.source / rel).read_bytes() for rel in SOURCE_FILES}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for rel, data in files.items():
                archive.writestr('agentorch/' + rel, data)
            archive.writestr('agentorch/MANIFEST.json',
                             workspace._manifest_bytes(files, SUITE, FORMAT_VERSION))
        payload = buffer.getvalue()
        asset = sources.ReleaseAsset('agentorch.zip', len(payload),
            'sha256:' + hashlib.sha256(payload).hexdigest(),
            'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip')
        release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, 'v1.7', (asset,))
        calls = 0
        other = b'concurrent host rules\n'

        def download(_asset):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.host / 'AGENTS.md').write_bytes(other)
            return payload

        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', side_effect=download):
            prepared = entry.prepare_isolated_install(self.host, source_root=self.source, mode='APPLY')
        self.assertEqual(prepared['result']['status'], 'STOPPED_CONFLICT', prepared['result'])
        self.assertEqual((self.host / 'AGENTS.md').read_bytes(), other)
        self.assertFalse((self.host / 'agentorch').exists())

    def test_security_source_manifest_rejects_symlink(self):
        victim = self.tmp / 'outside.txt'
        victim.write_bytes(b'outside private data')
        (self.source / 'linked.txt').symlink_to(victim)
        manifest_path = self.source / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files'].append({'path': 'linked.txt', 'category': 'core',
                                  'sha256': _sha(victim.read_bytes()),
                                  'size': victim.stat().st_size})
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(workspace.OnboardingError):
            self.plan_unverified()

    def test_plan_rejects_dangling_host_agents_symlink(self):
        agents = self.host / 'AGENTS.md'
        agents.unlink()
        agents.symlink_to(self.tmp / 'missing-host-rules')
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(agents.is_symlink())

    def test_plan_preserves_host_bytes_and_computes_agents_block(self):
        agents = self.host / 'AGENTS.md'
        original = b'# Host\r\n\r\nRules owned by host.\r\n'
        agents.write_bytes(original)
        before = raw_snapshot(self.host)
        plan = self.plan()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        self.assertTrue(plan.writes['AGENTS.md'].startswith(original))
        self.assertEqual(plan.writes['AGENTS.md'].count(
            workspace.MANAGED_BEGIN_PREFIX.encode()), 1)
        self.assertNotIn('agentorch/AGENTS.md', plan.writes)
        self.assertEqual(raw_snapshot(self.host), before)

    def test_security_content_after_block_hash_is_not_overwritten(self):
        block = workspace.managed_block('1.7').replace(
            workspace.MANAGED_END, 'user content\n' + workspace.MANAGED_END)
        with self.assertRaises(workspace.ManagedBlockError) as caught:
            workspace._agents_update(block, workspace.managed_block('1.8'))
        self.assertEqual(caught.exception.conflict_type, 'UNEXPECTED_BLOCK_CONTENT')
        self.assertTrue(caught.exception.diff)
        self.assertIn('user content', caught.exception.diff)

    def test_security_self_signed_modified_managed_block_does_not_authorize_replacement(self):
        block = workspace.managed_block('1.7')
        lines = block.splitlines()
        lines[1] = 'host owned content'
        digest = hashlib.sha256(('\n'.join(lines[1:-2]) + '\n').encode()).hexdigest()
        lines[-2] = workspace.MANAGED_HASH_TEMPLATE.format(digest)
        with self.assertRaises(workspace.ManagedBlockError) as caught:
            workspace._agents_update('\n'.join(lines) + '\n', workspace.managed_block('1.8'))
        self.assertEqual(caught.exception.conflict_type, 'NONCANONICAL_BODY')
        self.assertTrue(caught.exception.diff)
        self.assertIn('host owned content', caught.exception.diff)

    def test_security_forged_installed_manifest_cannot_authorize_upgrade(self):
        plan = self.plan()
        for rel, data in plan.writes.items():
            path = self.host / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        victim = self.host / 'agentorch' / 'README.md'
        victim.write_text('host-owned content\n')
        manifest_path = self.host / 'agentorch' / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text())
        entry = next(e for e in manifest['files'] if e['path'] == 'README.md')
        entry['sha256'] = _sha(victim.read_bytes())
        entry['size'] = victim.stat().st_size
        manifest_path.write_text(json.dumps(manifest))
        newer = make_source(self.tmp / 'new-source', version='1.8',
                            overrides={'README.md': 'new upstream content\n'})
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=newer, verified_source=verified_package(newer))
        self.assertNotEqual(plan.status, 'PLANNED', plan.problems)
        self.assertEqual(victim.read_text(), 'host-owned content\n')

    def test_security_empty_target_does_not_bypass_legacy_detection(self):
        self._make_legacy_install()
        (self.host / 'agentorch').mkdir()
        before = raw_snapshot(self.host)
        plan = self.plan()
        self.assertNotEqual(plan.status, 'PLANNED', plan.problems)
        self.assertEqual(raw_snapshot(self.host), before)

    def test_legacy_kit_orchestration_layout_fails_closed(self):
        # Historical format 1.6 uses kit/orchestration.md, not docs/.
        self._make_legacy_install()
        legacy = self.host / 'kit' / 'orchestration.md'
        legacy.parent.mkdir()
        (self.host / 'docs' / 'orchestration.md').rename(legacy)
        before = raw_snapshot(self.host)
        plan = self.plan()
        self.assertNotEqual(plan.status, 'PLANNED', plan.problems)
        self.assertEqual(raw_snapshot(self.host), before)

    def test_security_changed_agents_after_plan_is_not_overwritten(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        head = self._install_linked_worktree()
        plan = self.plan()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        agents = self.host / 'AGENTS.md'
        other = b'other writer changed host rules\n'
        agents.write_bytes(other)
        # Commit the concurrent edit so the worktree precondition itself still
        # holds; the refusal must come from the write-set drift guard.
        new_head = self._commit_all(self.host, 'concurrent host entry change')
        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=new_head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertIn('official write set or host preconditions changed',
                      result['problems'], result)
        self.assertEqual(agents.read_bytes(), other)
        self.assertFalse((self.host / 'agentorch').exists())

    def test_security_apply_without_verified_source_binding_is_blocked(self):
        head = self._install_linked_worktree()
        plan = self.plan_unverified()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        self.assertIsNone(plan.source_binding)
        before = raw_snapshot(self.host)
        result = workspace.apply_isolated_onboarding(
            plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertTrue(any('not bound to the verified official release' in problem
                            for problem in result['problems']), result)
        self.assertEqual(raw_snapshot(self.host), before)
        self.assertFalse((self.host / 'agentorch').exists())

    def test_security_mutated_agents_plan_is_not_authorized(self):
        head = self._install_linked_worktree()
        plan = self.plan()
        before = raw_snapshot(self.host)
        plan.agents_text = '# substituted host entry\n'
        result = workspace.apply_isolated_onboarding(
            plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertTrue(any('not bound to the verified official release' in problem
                            for problem in result['problems']), result)
        self.assertEqual(raw_snapshot(self.host), before)
        self.assertFalse((self.host / 'agentorch').exists())

    def test_security_write_set_rejects_escape_and_second_agents(self):
        repo = self.tmp / 'write-set-repository'
        for index, bad in enumerate(('../outside.txt', '/absolute.txt', 'AGENTS.md',
                                     'a/../../b')):
            with self.subTest(path=bad):
                host = self.tmp / f'host-write-set-{index}'
                head = self._make_linked_worktree(
                    host, repo=repo, branch=f'write-set/{index}')
                plan = workspace.plan_isolated_onboarding(
                    host, source_root=self.source, verified_source=self.verified)
                plan.files = {bad: b'x'}
                plan.agents_text = None
                plan.source_binding = workspace.bind_verified_source(self.verified, plan)
                rejected = workspace.write_set_problems(plan.writes)
                self.assertTrue(rejected, 'the write-set guard must reject this plan')
                before = raw_snapshot(host)
                result = workspace.apply_isolated_onboarding(
                    plan, declared_exclusive=True, expected_head=head)
                self.assertEqual(result['status'], 'BLOCKED', result)
                for problem in rejected:
                    self.assertIn(problem, result['problems'], result)
                self.assertEqual(raw_snapshot(host), before)
                self.assertFalse((self.tmp / 'outside.txt').exists())

    def test_security_platform_without_dir_fd_primitives_fails_closed(self):
        from unittest.mock import patch
        plan = self.plan()
        before = raw_snapshot(self.host)
        with patch.object(workspace, '_platform_capability_problems',
                          return_value=['synthetic platform lacks dir_fd']):
            result = workspace.apply_isolated_onboarding(plan)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertIn('synthetic platform lacks dir_fd', result['problems'], result)
        self.assertEqual(raw_snapshot(self.host), before)

    def test_security_symlink_swap_after_plan_does_not_write_outside(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        head = self._install_linked_worktree()
        outside = self.tmp / 'outside-target'
        outside.mkdir()
        plan = self.plan()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        (self.host / 'agentorch').symlink_to(outside, target_is_directory=True)
        new_head = self._commit_all(self.host, 'symlinked target')
        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=new_head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertTrue(any('agentorch/' in problem for problem in result['problems']), result)
        self.assertEqual(sorted(outside.iterdir()), [])

        (self.host / 'agentorch').unlink()
        target = self.host / 'agentorch'
        target.mkdir()
        (target / 'docs').symlink_to(outside, target_is_directory=True)
        new_head = self._commit_all(self.host, 'nested symlinked target')
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=new_head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertTrue(any('agentorch/' in problem for problem in result['problems']), result)
        self.assertEqual(sorted(outside.iterdir()), [])

    def test_security_target_appearing_after_plan_stops_without_touching_it(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        head = self._install_linked_worktree()
        plan = self.plan()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        target = self.host / 'agentorch'
        target.mkdir()
        intruder = target / 'user-data.md'
        intruder.write_text('mine\n', encoding='utf-8')
        new_head = self._commit_all(self.host, 'concurrent target directory')
        before = raw_snapshot(self.host)
        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=new_head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertTrue(any('agentorch/' in problem for problem in result['problems']), result)
        self.assertEqual(raw_snapshot(self.host), before)
        self.assertEqual(intruder.read_text(encoding='utf-8'), 'mine\n')

    def test_ac9_every_write_point_rolls_back_the_exact_original(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        plan_template = self.plan()
        write_points = len(plan_template.writes)
        self.assertGreater(write_points, 1)
        repo = self.tmp / 'ac9-repository'
        release, payload = self._review_release_transport()
        for fail_at in range(1, write_points + 1):
            for hook_name in ('before_write', 'after_write'):
                with self.subTest(fail_at=fail_at, hook=hook_name):
                    # Each case needs a real dedicated linked worktree with its
                    # own clean HEAD: a plain copied directory is refused by the
                    # declared-precondition preflight before any write point runs.
                    host = self.tmp / f'host-{hook_name}-{fail_at}'
                    head = self._make_linked_worktree(
                        host, repo=repo,
                        branch=f'ac9/{hook_name}-{fail_at}')
                    plan = workspace.plan_isolated_onboarding(
                        host, source_root=self.source, verified_source=self.verified)
                    self.assertEqual(plan.status, 'PLANNED', plan.problems)
                    before = raw_snapshot(host)

                    class Fault:
                        count = 0

                        def before_write(self, rel):
                            if hook_name != 'before_write':
                                return
                            self.count += 1
                            if self.count == fail_at:
                                raise RuntimeError('synthetic write failure')

                        def after_write(self, rel):
                            if hook_name != 'after_write':
                                return
                            self.count += 1
                            if self.count == fail_at:
                                raise RuntimeError('synthetic write failure')

                    with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                                      return_value=release), \
                         patch.object(sources.GitHubReleaseSource, 'download_asset',
                                      return_value=payload):
                        result = workspace.apply_isolated_onboarding(
                            plan, fault_injector=Fault(),
                            declared_exclusive=True, expected_head=head)
                    # The first before_write failure happens before any record
                    # exists: zero writes is the exact correct outcome there.
                    # Every later point must have actually written and rolled back.
                    wrote = hook_name == 'after_write' or fail_at > 1
                    self.assertEqual(result.get('failure', {}).get('phase'), 'WRITE', result)
                    if wrote:
                        self.assertEqual(result['status'], 'ROLLED_BACK', result)
                        self.assertEqual(result['rollback']['result'], 'SUCCEEDED', result)
                    else:
                        self.assertEqual(result['status'], 'BLOCKED', result)
                        self.assertEqual(result['rollback']['result'], 'NOT_NEEDED', result)
                        self.assertEqual(result['written'], [], result)
                    self.assertEqual(raw_snapshot(host), before)
                    self.assertFalse((host / 'agentorch').exists())

    def test_ac9_rollback_preserves_a_concurrently_modified_file(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        head = self._install_linked_worktree()
        plan = self.plan()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        host_agents = (self.host / 'AGENTS.md').read_bytes()
        host = self.host

        class Fault:
            count = 0
            first = None

            def after_write(self, rel):
                self.count += 1
                if self.count == 1:
                    self.first = rel
                    (host / rel).write_text('foreign writer\n', encoding='utf-8')
                if self.count == 3:
                    raise RuntimeError('synthetic failure')

        fault = Fault()
        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, fault_injector=fault,
                declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertEqual(result['rollback']['result'], 'FAILED')
        self.assertEqual((self.host / fault.first).read_text(encoding='utf-8'),
                         'foreign writer\n')
        self.assertEqual((self.host / 'AGENTS.md').read_bytes(), host_agents)

    def test_ac9_agents_append_failure_restores_crlf_original(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        agents = self.host / 'AGENTS.md'
        original = agents.read_bytes().replace(b'\n', b'\r\n')
        agents.write_bytes(original)
        head = self._install_linked_worktree()
        plan = self.plan()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        before = raw_snapshot(self.host)

        class Fault:
            def after_write(self, rel):
                if rel == 'AGENTS.md':
                    raise RuntimeError('synthetic failure after host entry append')

        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, fault_injector=Fault(),
                declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'ROLLED_BACK', result)
        self.assertEqual(result['rollback']['result'], 'SUCCEEDED', result)
        self.assertEqual(agents.read_bytes(), original)
        self.assertEqual(raw_snapshot(self.host), before)
        self.assertFalse((self.host / 'agentorch').exists())

    def test_entry_apply_requires_exclusive_declaration_and_stable_head(self):
        import io
        import subprocess
        import zipfile
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        head = self._linked_worktree_host()
        files = {rel: (self.source / rel).read_bytes() for rel in SOURCE_FILES}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for rel, data in files.items():
                archive.writestr('agentorch/' + rel, data)
            archive.writestr('agentorch/MANIFEST.json',
                             workspace._manifest_bytes(files, SUITE, FORMAT_VERSION))
        payload = buffer.getvalue()
        asset = sources.ReleaseAsset('agentorch.zip', len(payload),
            'sha256:' + hashlib.sha256(payload).hexdigest(),
            'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip')
        release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, 'v1.7', (asset,))
        before = raw_snapshot(self.host)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            missing = entry.prepare_isolated_install(
                self.host, source_root=self.source, mode='APPLY',
                declared_exclusive=False, expected_head=head)
            drifted = entry.prepare_isolated_install(
                self.host, source_root=self.source, mode='APPLY',
                declared_exclusive=True, expected_head='0' * len(head))
        self.assertEqual(missing['result']['status'], 'BLOCKED', missing['result'])
        self.assertEqual(drifted['result']['status'], 'BLOCKED', drifted['result'])
        self.assertEqual(raw_snapshot(self.host), before)
        self.assertFalse((self.host / 'agentorch').exists())

    # --- AC-1 -------------------------------------------------------------
    def test_ac1_install_places_files_under_agentorch_without_second_agents(self):
        plan, result = self.install()
        self.assertEqual(result['status'], 'APPLIED', result)
        agentorch = self.host / 'agentorch'
        self.assertTrue((agentorch / 'README.md').is_file())
        self.assertTrue((agentorch / 'docs' / 'orchestration.md').is_file())
        self.assertTrue((agentorch / 'editions' / 'local' / 'master.md').is_file())
        self.assertTrue((agentorch / 'scripts' / 'orchestrator.py').is_file())
        self.assertTrue((agentorch / 'MANIFEST.json').is_file())
        # The kit's root AGENTS.md must not become a second AGENTS.md.
        self.assertFalse((agentorch / 'AGENTS.md').exists())
        # No kit file may leak to the host root.
        self.assertFalse((self.host / 'docs' / 'orchestration.md').exists())
        self.assertFalse((self.host / 'editions').exists())
        self.assertFalse((self.host / 'templates').exists())
        self.assertFalse((self.host / 'scripts').exists())

    # --- AC-2 -------------------------------------------------------------
    def test_ac2_host_agents_prefix_preserved_and_reinstall_is_idempotent(self):
        original = (self.host / 'AGENTS.md').read_bytes()
        self.install()
        after_first = (self.host / 'AGENTS.md').read_bytes()
        self.assertTrue(after_first.startswith(original),
                        'the host AGENTS.md must keep its original bytes as a prefix')
        text = after_first.decode('utf-8')
        self.assertEqual(text.count(workspace.MANAGED_BEGIN_PREFIX), 1)
        self.assertEqual(text.count(workspace.MANAGED_END), 1)
        self.assertEqual(len(workspace.managed_block_hashes(text)), 1)

        second_plan = self.plan()
        self.assertEqual(second_plan.status, 'PLANNED', second_plan.problems)
        self.assertEqual(second_plan.agents_action, 'unchanged')
        second = workspace.apply_isolated_onboarding(second_plan)
        self.assertIn(second['status'], ('PLANNED', 'APPLIED'), second)
        self.assertEqual((self.host / 'AGENTS.md').read_bytes(), after_first)
        self.assertEqual(second.get('written') or [], [])

    def test_ac2_upgrade_requires_an_authenticated_baseline_and_stops(self):
        self.install()
        first_agents = (self.host / 'AGENTS.md').read_bytes()
        host_before = raw_snapshot(self.host)

        upgraded_source = make_source(
            self.tmp / 'source-1.8', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n\nRules: '
                                     '[AGENTS.md](AGENTS.md).\n'})
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded_source,
            verified_source=verified_package(upgraded_source))
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertEqual(plan.mode, 'UPGRADE')
        result = workspace.apply_isolated_onboarding(plan)
        self.assertNotEqual(result['status'], 'APPLIED', result)
        self.assertEqual(raw_snapshot(self.host), host_before)
        self.assertEqual((self.host / 'AGENTS.md').read_bytes(), first_agents)

    def test_ac9_upgrade_refuses_to_overwrite_a_locally_modified_file(self):
        self.install()
        victim = self.host / 'agentorch' / 'README.md'
        victim.write_text('local fork\n', encoding='utf-8')
        newer = make_source(self.tmp / 'source-1.8', version='1.8',
                            overrides={'README.md': '# changed\n'})
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=newer, verified_source=verified_package(newer))
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('README.md' in problem for problem in plan.problems))
        self.assertEqual(victim.read_text(encoding='utf-8'), 'local fork\n')

    def _upgrade_transport(self, upgraded_source):
        """Route the fixed official client to the old and new synthetic releases."""
        from scripts.update_agentorch import package
        baseline_release, baseline_payload = self._release_transport_from(self.source)
        target_release, target_payload = self._release_transport_from(upgraded_source)
        baseline = package.verify_package(
            baseline_release, baseline_payload, expected_suite=SUITE)
        target = package.verify_package(
            target_release, target_payload, expected_suite=SUITE)
        self.assertTrue(baseline.ok, baseline.failed_check)
        self.assertTrue(target.ok, target.failed_check)
        payloads = {baseline_release.assets[0].download_url: baseline_payload,
                    target_release.assets[0].download_url: target_payload}

        def fetch(tag=None):
            if tag == baseline_release.tag_name:
                return baseline_release
            if tag is None or tag == target_release.tag_name:
                return target_release
            raise AssertionError(f'unexpected release tag {tag!r}')

        def download(asset):
            return payloads[asset.download_url]

        return (baseline, target, baseline_release, target_release, fetch, download)

    def test_ac2_authenticated_same_origin_upgrade_applies_and_repeat_is_zero_write(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        _plan, installed = self.install()
        self.assertEqual(installed['status'], 'APPLIED', installed)
        head = self._commit_all(self.host, 'installed 1.7 baseline')
        upgraded = make_source(
            self.tmp / 'source-1.8', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n\nRules: '
                                     '[AGENTS.md](AGENTS.md).\n'})
        baseline, target, _baseline_release, _target_release, fetch, download = \
            self._upgrade_transport(upgraded)
        host_before = raw_snapshot(self.host, skip_names=('agentorch', 'AGENTS.md', '.git'))
        before = raw_snapshot(self.host, skip_names=('.git',))
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=baseline)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        self.assertEqual(plan.mode, 'UPGRADE')
        self.assertIsNotNone(plan.baseline_binding)
        self.assertIn('agentorch/README.md', plan.writes)
        self.assertIn('agentorch/MANIFEST.json', plan.writes)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', side_effect=fetch), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', side_effect=download):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'APPLIED', result)
        self.assertTrue(result['written'])
        projection = workspace._installed_projection(
            dict(target.files), SUITE, '1.8', self.host)
        for rel, data in projection.items():
            self.assertEqual((self.host / 'agentorch' / rel).read_bytes(), data, rel)
        self.assertFalse((self.host / 'agentorch' / 'AGENTS.md').exists())
        agents = (self.host / 'AGENTS.md').read_bytes()
        self.assertIn(b'<!-- AGENTORCH:BEGIN v1.8 -->', agents)
        self.assertEqual(len(workspace.managed_block_hashes(agents.decode('utf-8'))), 1)
        self.assertEqual(
            raw_snapshot(self.host, skip_names=('agentorch', 'AGENTS.md', '.git')),
            host_before)
        after_upgrade = raw_snapshot(self.host, skip_names=('.git',))
        self.assertNotEqual(after_upgrade, before)

        second_plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=target)
        self.assertEqual(second_plan.status, 'PLANNED', second_plan.problems)
        self.assertEqual(second_plan.mode, 'UPGRADE')
        self.assertEqual(second_plan.writes, {})
        self.assertEqual(second_plan.agents_action, 'unchanged')
        second = workspace.apply_isolated_onboarding(second_plan)
        self.assertEqual(second['status'], 'PLANNED', second)
        self.assertEqual(second['written'], [])
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), after_upgrade)

    def test_ac2_authenticated_upgrade_via_entry_apply(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        _plan, installed = self.install()
        self.assertEqual(installed['status'], 'APPLIED', installed)
        head = self._commit_all(self.host, 'installed 1.7 baseline')
        upgraded = make_source(
            self.tmp / 'source-1.8', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n\nRules: '
                                     '[AGENTS.md](AGENTS.md).\n'})
        _baseline, target, _baseline_release, _target_release, fetch, download = \
            self._upgrade_transport(upgraded)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', side_effect=fetch), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', side_effect=download):
            prepared = entry.prepare_isolated_install(
                self.host, source_root=upgraded, mode='APPLY',
                declared_exclusive=True, expected_head=head)
        self.assertEqual(prepared['result']['status'], 'APPLIED', prepared['result'])
        self.assertEqual(prepared['payload']['status'], 'APPLIED')
        self.assertEqual(prepared['payload']['mode'], 'UPGRADE')
        self.assertGreater(prepared['payload']['written_count'], 0)
        self.assertEqual(
            (self.host / 'agentorch' / 'README.md').read_bytes(),
            workspace._installed_projection(
                dict(target.files), SUITE, '1.8', self.host)['README.md'])

    def test_ac9_authenticated_baseline_still_blocks_local_edits_and_conflicts(self):
        self.install()
        upgraded = make_source(
            self.tmp / 'source-1.8', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, _target_release, _fetch, _download = \
            self._upgrade_transport(upgraded)
        original = raw_snapshot(self.host, skip_names=('.git',))
        readme = self.host / 'agentorch' / 'README.md'
        readme_original = readme.read_bytes()
        manifest_path = self.host / 'agentorch' / 'MANIFEST.json'
        manifest_original = manifest_path.read_bytes()

        def plan():
            return workspace.plan_isolated_onboarding(
                self.host, source_root=upgraded, verified_source=target,
                verified_baseline=baseline)

        readme.write_text('local fork\n', encoding='utf-8')
        edited_before = raw_snapshot(self.host, skip_names=('.git',))
        edited = plan()
        self.assertEqual(edited.status, 'STOPPED_CONFLICT', edited.problems)
        self.assertTrue(any('README.md' in problem for problem in edited.problems))
        self.assertNotEqual(workspace.apply_isolated_onboarding(edited)['status'], 'APPLIED')
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), edited_before)
        readme.write_bytes(readme_original)
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), original)

        readme.write_text('local fork\n', encoding='utf-8')
        manifest = json.loads(manifest_original)
        entry = next(item for item in manifest['files'] if item['path'] == 'README.md')
        forged = readme.read_bytes()
        entry['sha256'] = _sha(forged)
        entry['size'] = len(_normalized(forged))
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        forged_before = raw_snapshot(self.host, skip_names=('.git',))
        forged_plan = plan()
        self.assertEqual(forged_plan.status, 'STOPPED_CONFLICT', forged_plan.problems)
        self.assertTrue(any('README.md' in problem or 'MANIFEST' in problem
                            for problem in forged_plan.problems))
        self.assertNotEqual(workspace.apply_isolated_onboarding(forged_plan)['status'],
                            'APPLIED')
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), forged_before)
        readme.write_bytes(readme_original)
        manifest_path.write_bytes(manifest_original)

        intruder = self.host / 'agentorch' / 'notes.md'
        intruder.write_text('keep\n', encoding='utf-8')
        conflict_before = raw_snapshot(self.host, skip_names=('.git',))
        conflicted = plan()
        self.assertEqual(conflicted.status, 'STOPPED_CONFLICT', conflicted.problems)
        self.assertTrue(any('notes.md' in problem for problem in conflicted.problems))
        self.assertNotEqual(workspace.apply_isolated_onboarding(conflicted)['status'],
                            'APPLIED')
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), conflict_before)
        intruder.unlink()

        manifest_path.unlink()
        missing = plan()
        self.assertEqual(missing.status, 'STOPPED_CONFLICT', missing.problems)
        self.assertTrue(any('MANIFEST' in problem for problem in missing.problems))
        self.assertNotEqual(workspace.apply_isolated_onboarding(missing)['status'], 'APPLIED')
        manifest_path.write_bytes(manifest_original)
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), original)

    def test_ac9_upgrade_apply_reauthenticates_the_baseline(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        _plan, installed = self.install()
        self.assertEqual(installed['status'], 'APPLIED', installed)
        head = self._commit_all(self.host, 'installed 1.7 baseline')
        upgraded = make_source(
            self.tmp / 'source-1.8', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, target_release, _fetch, download = \
            self._upgrade_transport(upgraded)
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=baseline)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        before = raw_snapshot(self.host, skip_names=('.git',))
        readme_before = (self.host / 'agentorch' / 'README.md').read_bytes()
        # APPLY must not trust the plan's baseline: the re-fetched release is the
        # new target here, so the installed 1.7 tree cannot match it.
        with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                          side_effect=lambda tag=None: target_release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset',
                          side_effect=download):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertTrue(any('baseline' in problem for problem in result['problems']),
                        result['problems'])
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)
        self.assertEqual(
            (self.host / 'agentorch' / 'README.md').read_bytes(), readme_before)

    def test_ac9_upgrade_rollback_restores_the_inplace_agents_rewrite(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        _plan, installed = self.install()
        self.assertEqual(installed['status'], 'APPLIED', installed)
        head = self._commit_all(self.host, 'installed 1.7 baseline')
        upgraded = make_source(
            self.tmp / 'source-1.8', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, _target_release, fetch, download = \
            self._upgrade_transport(upgraded)
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=baseline)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        before = raw_snapshot(self.host, skip_names=('.git',))
        agents = self.host / 'AGENTS.md'
        original = agents.read_bytes()
        stat_before = agents.stat()

        class Fault:
            def after_write(self, rel):
                if rel == 'AGENTS.md':
                    raise RuntimeError('synthetic failure after managed block rewrite')

        with patch.object(sources.GitHubReleaseSource, 'fetch_release', side_effect=fetch), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', side_effect=download):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head,
                fault_injector=Fault())
        self.assertEqual(result['status'], 'ROLLED_BACK', result)
        self.assertEqual(result['rollback']['result'], 'SUCCEEDED', result)
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)
        self.assertEqual(agents.read_bytes(), original)
        after = agents.stat()
        self.assertEqual(
            (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid),
            (stat_before.st_dev, stat_before.st_ino, stat_before.st_mode,
             stat_before.st_uid, stat_before.st_gid))

    def test_ac9_block_only_upgrade_plans_and_applies_consistently(self):
        """A stale managed block alone must not PLAN and then BLOCK on APPLY."""
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        _plan, installed = self.install()
        self.assertEqual(installed['status'], 'APPLIED', installed)
        agents = self.host / 'AGENTS.md'
        text = agents.read_text(encoding='utf-8')
        info = workspace._analyze_agents(text)
        self.assertEqual(len(info['begins']), 1)
        begin, end = info['begins'][0][0], info['ends'][0]
        lines = text.split('\n')
        stale = workspace.managed_block('1.6')
        agents.write_text(
            '\n'.join(lines[:begin] + stale.rstrip('\n').split('\n') + lines[end + 1:]),
            encoding='utf-8', newline='')
        prefix = agents.read_bytes().split(b'<!-- AGENTORCH:BEGIN')[0]
        head = self._commit_all(self.host, 'stale managed block')
        agentorch_before = raw_snapshot(self.host / 'agentorch')

        # Plan and APPLY must see the same official bytes; the transport below is
        # the one APPLY re-fetches, so use its verification object for the plan.
        from scripts.update_agentorch import package
        release, payload = self._review_release_transport()
        official = package.verify_package(release, payload, expected_suite=SUITE)
        self.assertTrue(official.ok, official.failed_check)
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=self.source, verified_source=official)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        self.assertEqual(plan.mode, 'UPGRADE')
        self.assertEqual(plan.files, {})
        self.assertEqual(plan.agents_action, 'replace')
        self.assertEqual(list(plan.writes), ['AGENTS.md'])
        self.assertIsNotNone(plan.baseline_binding)

        with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                          return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset',
                          return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'APPLIED', result)
        self.assertEqual(result['written'], ['AGENTS.md'])
        self.assertEqual(raw_snapshot(self.host / 'agentorch'), agentorch_before)
        after = agents.read_bytes()
        self.assertTrue(after.startswith(prefix))
        self.assertIn(b'<!-- AGENTORCH:BEGIN v1.7 -->', after)
        self.assertEqual(len(workspace.managed_block_hashes(after.decode('utf-8'))), 1)

        repeat = workspace.plan_isolated_onboarding(
            self.host, source_root=self.source, verified_source=self.verified)
        self.assertEqual(repeat.status, 'PLANNED', repeat.problems)
        self.assertEqual(repeat.writes, {})
        self.assertEqual(workspace.apply_isolated_onboarding(repeat)['status'], 'PLANNED')
        self.assertEqual(agents.read_bytes(), after)

    def test_ac9_non_object_installed_manifest_stops_without_attribute_error(self):
        """Local MANIFEST.json that is JSON but not an object is unknown, not a crash."""
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        self.install()
        manifest_path = self.host / 'agentorch' / 'MANIFEST.json'
        newer = make_source(
            self.tmp / 'source-1.8-nondict', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        target = verified_package(newer)
        release, payload = self._review_release_transport()
        bodies = ('[]', 'null', '"text"', '{"files": 5}', '{')
        for body in bodies:
            with self.subTest(body=body):
                manifest_path.write_text(body, encoding='utf-8')
                before = raw_snapshot(self.host, skip_names=('.git',))
                plan = workspace.plan_isolated_onboarding(
                    self.host, source_root=newer, verified_source=target)
                self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
                self.assertEqual(plan.writes, {})
                self.assertNotEqual(
                    workspace.apply_isolated_onboarding(plan)['status'], 'APPLIED')
                with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                                  return_value=release), \
                     patch.object(sources.GitHubReleaseSource, 'download_asset',
                                  return_value=payload):
                    prepared = entry.prepare_isolated_install(self.host, mode='PLAN')
                self.assertEqual(prepared['result']['status'], 'STOPPED_CONFLICT',
                                 prepared['result'])
                self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac9_installed_manifest_path_escape_is_rejected_before_any_read(self):
        from unittest.mock import patch
        self.install()
        secret = self.tmp / 'host-escape-canary.txt'
        secret.write_text('SYNTHETIC_PRIVATE_CANARY_153_ESCAPE\n', encoding='utf-8')
        manifest_path = self.host / 'agentorch' / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        data = secret.read_bytes()
        manifest['files'].append({
            'path': '../../host-escape-canary.txt', 'category': 'core',
            'sha256': _sha(data), 'size': len(_normalized(data))})
        manifest['files'].sort(key=lambda item: item['path'])
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        newer = make_source(self.tmp / 'source-1.8-escape', version='1.8',
                            overrides={'README.md': '# AgentOrch 1.8\n'})
        target = verified_package(newer)
        before = raw_snapshot(self.host, skip_names=('.git',))
        original_read = Path.read_bytes
        reads = []

        def tracked(path):
            reads.append(path)
            return original_read(path)

        with patch.object(Path, 'read_bytes', tracked):
            plan = workspace.plan_isolated_onboarding(
                self.host, source_root=newer, verified_source=target)
        self.assertNotIn(secret.resolve(), [path.resolve() for path in reads])
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('unsafe path' in problem for problem in plan.problems),
                        plan.problems)
        self.assertNotEqual(workspace.apply_isolated_onboarding(plan)['status'], 'APPLIED')
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac9_installed_manifest_symlink_is_rejected_before_any_read(self):
        """A symlinked local MANIFEST.json is not read, even if it parses."""
        self.install()
        manifest_path = self.host / 'agentorch' / 'MANIFEST.json'
        canary = self.tmp / 'installed-manifest-canary.json'
        canary.write_bytes(manifest_path.read_bytes())
        manifest_path.unlink()
        manifest_path.symlink_to(canary)
        newer = make_source(self.tmp / 'source-1.8-manifest-symlink', version='1.8',
                            overrides={'README.md': '# AgentOrch 1.8\n'})
        target = verified_package(newer)
        before = raw_snapshot(self.host, skip_names=('.git',))
        with observe_path_reads() as reads:
            plan = workspace.plan_isolated_onboarding(
                self.host, source_root=newer, verified_source=target)
        self.assertNotIn(canary.resolve(), [path.resolve() for path in reads])
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('not a regular file' in problem or 'symlink' in problem
                            for problem in plan.problems), plan.problems)
        self.assertEqual(plan.writes, {})
        self.assertNotEqual(workspace.apply_isolated_onboarding(plan)['status'], 'APPLIED')
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac9_installed_manifest_entry_symlink_is_rejected_before_any_read(self):
        """A manifest-listed path that is a symlink is refused, not followed."""
        self.install()
        canary = self.tmp / 'installed-entry-canary.txt'
        canary.write_text('SYNTHETIC_PRIVATE_CANARY_153_SYMLINK_ENTRY\n',
                          encoding='utf-8')
        linked = self.host / 'agentorch' / 'README.md'
        linked.unlink()
        linked.symlink_to(canary)
        manifest_path = self.host / 'agentorch' / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        data = canary.read_bytes()
        for entry in manifest['files']:
            if entry['path'] == 'README.md':
                entry['sha256'] = _sha(data)
                entry['size'] = len(_normalized(data))
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        newer = make_source(self.tmp / 'source-1.8-entry-symlink', version='1.8',
                            overrides={'README.md': '# AgentOrch 1.8\n'})
        target = verified_package(newer)
        before = raw_snapshot(self.host, skip_names=('.git',))
        with observe_path_reads() as reads:
            plan = workspace.plan_isolated_onboarding(
                self.host, source_root=newer, verified_source=target)
        self.assertNotIn(canary.resolve(), [path.resolve() for path in reads])
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('not a regular file' in problem or 'symlink' in problem
                            for problem in plan.problems), plan.problems)
        self.assertEqual(plan.writes, {})
        self.assertNotEqual(workspace.apply_isolated_onboarding(plan)['status'], 'APPLIED')
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac9_installed_manifest_entry_directory_is_rejected_without_read(self):
        """A manifest-listed path that is a directory is refused as non-regular."""
        self.install()
        entry_path = self.host / 'agentorch' / 'README.md'
        entry_path.unlink()
        entry_path.mkdir()
        newer = make_source(self.tmp / 'source-1.8-entry-directory', version='1.8',
                            overrides={'README.md': '# AgentOrch 1.8\n'})
        target = verified_package(newer)
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=newer, verified_source=target)
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('not a regular file' in problem
                            for problem in plan.problems), plan.problems)
        self.assertEqual(plan.writes, {})

    def test_ac9_patch_release_tag_is_not_recoverable_from_the_local_manifest(self):
        """The local manifest only yields v<format> / v<format>.0 candidates."""
        from unittest.mock import patch
        patched_source = make_source(
            self.tmp / 'source-1.7.3', version='1.7',
            overrides={'README.md': '# AgentOrch 1.7.3 patch\n'})
        patched, _release, _payload = self._tagged_release(patched_source, 'v1.7.3')
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=patched_source, verified_source=patched)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        self.assertEqual(plan.mode, 'INSTALL')
        for rel, data in plan.writes.items():
            path = self.host / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        hint, hint_problems = workspace._installed_baseline_hint(self.host / 'agentorch')
        self.assertEqual(hint_problems, [])
        self.assertEqual(hint['format_version'], '1.7')

        plain_source = make_source(self.tmp / 'source-1.7-plain', version='1.7')
        plain, _plain_release, _plain_payload = self._tagged_release(plain_source, 'v1.7')
        attempted = []

        def fetch(tag=None):
            attempted.append(tag)
            if tag in ('v1.7', 'v1.7.0'):
                return plain
            raise AssertionError(f'a patch tag must never be requested: {tag!r}')

        before = raw_snapshot(self.host)
        with patch.object(workspace, 'fetch_verified_onboarding_release', fetch):
            with self.assertRaises(workspace.OnboardingError) as caught:
                workspace.fetch_verified_onboarding_baseline(self.host)
        self.assertEqual(attempted, ['v1.7', 'v1.7.0'])
        self.assertIn('cannot be authenticated', str(caught.exception))
        self.assertEqual(raw_snapshot(self.host), before)

    def test_ac9_same_tag_with_different_bytes_cannot_replace_the_baseline(self):
        _plan, installed = self.install()
        self.assertEqual(installed['status'], 'APPLIED', installed)
        upgraded = make_source(
            self.tmp / 'source-1.8-same-tag', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        _baseline, target, _bl_release, _tg_release, _fetch, _download = \
            self._upgrade_transport(upgraded)
        forged_source = make_source(
            self.tmp / 'source-1.7-forged', version='1.7',
            overrides={'README.md': '# same tag, different bytes\n'})
        forged, forged_release, _forged_payload = self._tagged_release(
            forged_source, 'v1.7')
        self.assertEqual(forged_release.tag_name, 'v1.7')
        before = raw_snapshot(self.host, skip_names=('.git',))
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=forged)
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('authenticated baseline' in problem
                            for problem in plan.problems), plan.problems)
        self.assertEqual(plan.writes, {})
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)
        self.assertNotEqual(workspace.apply_isolated_onboarding(plan)['status'], 'APPLIED')
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac2_crlf_host_agents_prefix_is_preserved_byte_for_byte(self):
        agents = self.host / 'AGENTS.md'
        crlf = agents.read_bytes().replace(b'\n', b'\r\n')
        agents.write_bytes(crlf)
        self.install()
        text = agents.read_bytes()
        self.assertTrue(text.startswith(crlf))
        second = self.plan()
        self.assertEqual(second.status, 'PLANNED', second.problems)
        self.assertEqual(second.agents_action, 'unchanged')
        self.assertEqual(agents.read_bytes(), text)

    def test_ac2_no_trailing_newline_prefix_is_preserved_byte_for_byte(self):
        agents = self.host / 'AGENTS.md'
        original = agents.read_bytes().rstrip(b'\n')
        self.assertFalse(original.endswith(b'\n'))
        agents.write_bytes(original)
        plan, result = self.install()
        self.assertEqual(result['status'], 'APPLIED', result)
        text = agents.read_bytes()
        self.assertTrue(text.startswith(original))
        self.assertNotEqual(text, original)
        second = self.plan()
        self.assertEqual(second.status, 'PLANNED', second.problems)
        self.assertEqual(second.agents_action, 'unchanged')
        self.assertEqual(agents.read_bytes(), text)

    def test_ac2_provable_mode_and_inode_are_preserved_by_inplace_append(self):
        # The ordinary-file path this task implements: no xattr/ACL, one hard
        # link. An in-place append must keep dev/inode/mode/uid/gid and the
        # original bytes as a prefix, and only AGENTS.md plus agentorch/ change.
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        head = self._install_linked_worktree()
        target = self.host / 'AGENTS.md'
        target.chmod(0o640)
        original = target.read_bytes()
        before = target.stat()
        plan = self.plan()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'APPLIED', result)
        after = target.stat()
        self.assertEqual(
            (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid),
            (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid))
        self.assertEqual(after.st_nlink, 1)
        updated = target.read_bytes()
        self.assertTrue(updated.startswith(original))
        self.assertNotEqual(updated, original)

    # --- AC-3 -------------------------------------------------------------
    def test_ac3_malformed_or_user_modified_block_fails_closed(self):
        self.install()
        agents = self.host / 'AGENTS.md'
        original = agents.read_bytes()

        broken = original.replace(workspace.MANAGED_END.encode('utf-8'), b'')
        agents.write_bytes(broken)
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('END' in problem or '标记' in problem for problem in plan.problems))
        self.assertTrue(plan.diff)
        self.assertTrue(plan.guidance)

        duplicate = original + b'\n' + original[original.index(workspace.MANAGED_BEGIN_PREFIX.encode()):]
        agents.write_bytes(duplicate)
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)

        agents.write_bytes(original)
        edited = original.replace(b'AgentOrch Root', b'AgentOrch Root (hand edited)', 1)
        agents.write_bytes(edited)
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(plan.diff)
        self.assertTrue(plan.guidance)
        # A failed plan must not have written anything.
        self.assertEqual(agents.read_bytes(), edited)

    # --- AC-4 / AC-10 -----------------------------------------------------
    def test_ac4_ac10_host_files_outside_allowed_area_are_unchanged(self):
        # Success path: the install must actually apply, and only agentorch/**
        # plus the root AGENTS.md managed block may change. Asserting APPLIED is
        # what stops this from passing on a silent BLOCKED / zero-write run.
        before = raw_snapshot(self.host, skip_names=('agentorch', '.git'))
        original_agents = (self.host / 'AGENTS.md').read_bytes()
        plan, result = self.install()
        self.assertEqual(result['status'], 'APPLIED', result)
        self.assertTrue(result['written'])
        for rel in result['written']:
            self.assertTrue(rel == 'AGENTS.md' or rel.startswith('agentorch/'), rel)
        after = raw_snapshot(self.host, skip_names=('agentorch', 'AGENTS.md', '.git'))
        expected = {key: value for key, value in before.items() if key != 'AGENTS.md'}
        self.assertEqual(after, expected)
        updated = (self.host / 'AGENTS.md').read_bytes()
        self.assertTrue(updated.startswith(original_agents))
        self.assertNotEqual(updated, original_agents)
        text = updated.decode('utf-8')
        self.assertEqual(text.count(workspace.MANAGED_BEGIN_PREFIX), 1)
        self.assertEqual(text.count(workspace.MANAGED_END), 1)

    def test_ac4_failure_path_writes_nothing(self):
        # A failed plan must be asserted on its own: zero host writes, no
        # agentorch/ tree, and the original AGENTS.md bytes intact.
        agents = self.host / 'AGENTS.md'
        original = agents.read_bytes()
        duplicate = original + b'\n' + workspace.managed_block(FORMAT_VERSION).encode('utf-8') * 2
        agents.write_bytes(duplicate)
        before = raw_snapshot(self.host)
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        result = workspace.apply_isolated_onboarding(plan)
        self.assertNotEqual(result['status'], 'APPLIED', result)
        self.assertEqual(result['written'], [])
        self.assertEqual(raw_snapshot(self.host), before)
        self.assertEqual(agents.read_bytes(), duplicate)
        self.assertFalse((self.host / 'agentorch').exists())

    # --- AC-5 -------------------------------------------------------------
    def test_ac5_missing_agents_is_created_and_reported(self):
        (self.host / 'AGENTS.md').unlink()
        plan, result = self.install()
        self.assertEqual(plan.agents_action, 'create')
        self.assertTrue(result['agents_created'])
        text = (self.host / 'AGENTS.md').read_text(encoding='utf-8')
        self.assertIn('Host project rules', text)
        self.assertEqual(text.count(workspace.MANAGED_BEGIN_PREFIX), 1)

    def test_ac5_existing_non_empty_agentorch_is_not_overwritten(self):
        (self.host / 'agentorch').mkdir()
        (self.host / 'agentorch' / 'user-data.md').write_text('mine\n', encoding='utf-8')
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('agentorch' in problem for problem in plan.problems))
        self.assertEqual((self.host / 'agentorch' / 'user-data.md').read_text(encoding='utf-8'),
                         'mine\n')

    # --- AC-6 -------------------------------------------------------------
    def test_ac6_paths_resolve_from_any_cwd_and_writes_stay_inside_host(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        head = self._install_linked_worktree()
        external = self.tmp / 'external'
        external.mkdir()
        external_before = raw_snapshot(external)
        cwd = Path.cwd()
        try:
            os.chdir(external)
            plan = workspace.plan_isolated_onboarding(
                self.host, source_root=self.source, verified_source=self.verified)
        finally:
            os.chdir(cwd)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        self.assertEqual(plan.host_root, self.host.resolve())
        self.assertEqual(plan.agentorch_root, (self.host / 'agentorch').resolve())
        self.assertEqual(workspace.agentorch_root(), ROOT.resolve())

        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'APPLIED', result)
        for rel in result['written']:
            self.assertFalse(Path(rel).is_absolute(), rel)
            self.assertFalse(rel.startswith('..'), rel)
        self.assertEqual(raw_snapshot(external), external_before)

    # --- AC-7 -------------------------------------------------------------
    def test_ac7_relative_markdown_links_resolve_after_embedding(self):
        self.install()
        agentorch = self.host / 'agentorch'
        for path in sorted(agentorch.rglob('*.md')):
            for target in markdown_links(path.read_text(encoding='utf-8')):
                if target.startswith(('http://', 'https://', 'mailto:', '#')):
                    continue
                resolved = (path.parent / target.split('#', 1)[0]).resolve()
                with self.subTest(file=path.relative_to(agentorch).as_posix(),
                                  target=target):
                    self.assertTrue(resolved.exists(), f'{target} does not resolve')
        core = (agentorch / 'docs' / 'orchestration.md').read_text(encoding='utf-8')
        self.assertIn('../../AGENTS.md', core)
        self.assertTrue((agentorch / 'docs' / '../../AGENTS.md').resolve().is_file())
        readme = (agentorch / 'README.md').read_text(encoding='utf-8')
        self.assertIn('../AGENTS.md', readme)

    # --- AC-8 -------------------------------------------------------------
    def test_private_state_generated_ignore_in_synthetic_git(self):
        import subprocess
        before = (self.host / '.gitignore').read_bytes()
        subprocess.run(['git', 'init', '-q'], cwd=self.host, check=True)
        agentorch = self.host / 'agentorch'
        agentorch.mkdir()
        (agentorch / '.gitignore').write_bytes(workspace.AGENTORCH_GITIGNORE.encode())
        paths = ('.orchestration/state.sqlite3', '.orchestration/config.json',
                 '.orchestration/state.sqlite3-wal', '.orchestration/backups/state.sqlite3')
        for rel in paths:
            target = agentorch / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'synthetic private state')
            with self.subTest(path=rel):
                result = subprocess.run(['git', 'check-ignore', '-q', 'agentorch/' + rel],
                                        cwd=self.host)
                self.assertEqual(result.returncode, 0)
        (agentorch / 'ordinary.txt').write_text('public')
        result = subprocess.run(['git', 'check-ignore', '-q', 'agentorch/ordinary.txt'],
                                cwd=self.host)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.host / '.gitignore').read_bytes(), before)

    def test_private_state_validator_excludes_state_but_not_manifest_entries(self):
        from scripts import validate_kit
        self.assertTrue(workspace._is_private_rel('.orchestration/state.sqlite3'))
        self.assertFalse(workspace._is_private_rel('docs/orchestration.md'))
        agentorch = self.host / 'agentorch'
        agentorch.mkdir()
        files = {'VERSION.md': SOURCE_FILES['VERSION.md'].encode()}
        (agentorch / 'VERSION.md').write_bytes(files['VERSION.md'])
        (agentorch / 'MANIFEST.json').write_bytes(workspace._manifest_bytes(files, SUITE, FORMAT_VERSION))
        before = validate_kit.isolated_layout_errors(agentorch)
        state = agentorch / '.orchestration' / 'backups' / 'state.sqlite3'
        state.parent.mkdir(parents=True)
        state.write_bytes(b'private')
        self.assertEqual(validate_kit.isolated_layout_errors(agentorch), before)
        files['.orchestration/backups/state.sqlite3'] = b'private'
        (agentorch / 'MANIFEST.json').write_bytes(workspace._manifest_bytes(files, SUITE, FORMAT_VERSION))
        errors = validate_kit.isolated_layout_errors(agentorch)
        self.assertTrue(any('lists a file that is not' in e and '.orchestration/' in e for e in errors), errors)

    def test_private_state_release_rejects_explicit_required_path(self):
        from unittest.mock import patch
        from scripts import build_release, check_drift
        files = build_release.collect_sources(build_release.read_required())
        manifest = check_drift.build_manifest(files, SUITE, FORMAT_VERSION)
        self.assertIn('docs/orchestration.md', files)
        self.assertFalse(any('.orchestration' in p.split('/') for p in files))
        self.assertFalse(any('.orchestration' in e['path'].split('/')
                             for e in manifest['files']))
        source = self.tmp / 'release-source'
        state = source / 'agentorch' / '.orchestration' / 'state.sqlite3'
        state.parent.mkdir(parents=True)
        state.write_bytes(b'private')
        with patch.object(build_release, 'ROOT', source):
            with self.assertRaises(SystemExit):
                build_release.collect_sources(['agentorch/.orchestration/state.sqlite3'])

    def test_ac8_agentorch_gitignore_covers_private_state_and_host_ignore_is_untouched(self):
        host_ignore = (self.host / '.gitignore').read_bytes()
        self.install()
        self.assertEqual((self.host / '.gitignore').read_bytes(), host_ignore)
        ignore = (self.host / 'agentorch' / '.gitignore').read_text(encoding='utf-8')
        for pattern in ('.local/', '.worktrees/', '__pycache__/', '*.pyc', '*.tmp'):
            self.assertIn(pattern, ignore)

        if shutil.which('git'):
            import subprocess
            subprocess.run(['git', 'init', '-q'], cwd=self.host, check=True)
            for rel in ('.local/private.md', '.worktrees/w/file.txt',
                        '__pycache__/x.pyc', 'cache.tmp'):
                target = self.host / 'agentorch' / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('x\n', encoding='utf-8')
                proc = subprocess.run(['git', 'check-ignore', '-q', f'agentorch/{rel}'],
                                      cwd=self.host)
                self.assertEqual(proc.returncode, 0, rel)

    def test_ac8_release_file_set_never_includes_private_state(self):
        from scripts import build_release
        files = build_release.collect_sources(build_release.read_required())
        for rel in files:
            self.assertNotIn('.local', rel.split('/'))
            self.assertNotIn('.worktrees', rel.split('/'))
            self.assertNotIn('.orchestration', rel.split('/'))
        self.assertIn('.local', build_release.FORBIDDEN_SEGMENTS)

    # --- AC-9 -------------------------------------------------------------
    def _make_legacy_install(self):
        """Copy the synthetic kit to the host root as an old flat install."""
        manifest = json.loads((self.source / 'MANIFEST.json').read_text(encoding='utf-8'))
        for entry in manifest['files']:
            rel = entry['path']
            target = self.host / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.source / rel, target)
        shutil.copyfile(self.source / 'MANIFEST.json', self.host / 'MANIFEST.json')
        return manifest

    def _add_legacy_member(self, rel, text):
        """Add one file to the synthetic historical release and its manifest."""
        path = self.source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
        manifest_path = self.source / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        data = _normalized(path.read_bytes())
        manifest['files'].append({
            'path': rel,
            'category': _category(rel),
            'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data),
        })
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + '\n',
            encoding='utf-8', newline='\n')

    def test_ac9_legacy_root_layout_is_preserved_and_migration_stops(self):
        self._make_legacy_install()
        before = raw_snapshot(self.host)
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertEqual(plan.mode, 'MIGRATE')
        self.assertEqual(plan.legacy_removals, [])
        result = workspace.apply_isolated_onboarding(plan)
        self.assertNotEqual(result['status'], 'APPLIED', result)
        # Every old-root byte stays in place: ownership is not independently
        # authenticated, so nothing is deleted or moved.
        self.assertEqual(raw_snapshot(self.host), before)
        self.assertFalse((self.host / 'agentorch').exists())
        rolled = workspace.rollback_isolated_onboarding(result)
        self.assertEqual(rolled['status'], 'BLOCKED', rolled)
        self.assertEqual(raw_snapshot(self.host), before)

    def test_ac9_missing_baseline_stops(self):
        self._make_legacy_install()
        (self.host / 'MANIFEST.json').unlink()
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('MANIFEST' in problem or 'baseline' in problem
                            for problem in plan.problems))

    def test_ac9_local_manifest_cannot_authorize_host_file_deletion(self):
        self._make_legacy_install()
        victim = self.host / 'src' / 'host_owned.py'
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim.write_bytes(b'host data must survive\n')
        manifest_path = self.host / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        data = victim.read_bytes()
        manifest['files'].append({'path': 'src/host_owned.py', 'category': 'core',
                                  'sha256': _sha(data), 'size': len(data)})
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        before = raw_snapshot(self.host)
        plan = self.plan()
        self.assertNotEqual(plan.status, 'PLANNED', plan.problems)
        self.assertNotIn('src/host_owned.py', plan.legacy_removals)
        self.assertEqual(raw_snapshot(self.host), before)

    def test_ac9_legacy_manifest_path_escape_is_rejected_before_any_read(self):
        from unittest.mock import patch
        self._make_legacy_install()
        secret = self.tmp / 'root-escape-canary.txt'
        secret.write_text('SYNTHETIC_PRIVATE_CANARY_153_ROOT_ESCAPE\n', encoding='utf-8')
        manifest_path = self.host / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        data = secret.read_bytes()
        manifest['files'].append({
            'path': '../root-escape-canary.txt', 'category': 'core',
            'sha256': _sha(data), 'size': len(_normalized(data))})
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        before = raw_snapshot(self.host)
        original_read = Path.read_bytes
        reads = []

        def tracked(path):
            reads.append(path)
            return original_read(path)

        with patch.object(Path, 'read_bytes', tracked):
            plan = self.plan()
        self.assertNotIn(secret.resolve(), [path.resolve() for path in reads])
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('unsafe path' in problem for problem in plan.problems),
                        plan.problems)
        self.assertEqual(plan.legacy_removals, [])
        self.assertEqual(raw_snapshot(self.host), before)

    def test_ac9_legacy_manifest_symlink_is_rejected_before_any_read(self):
        """A symlinked old-root MANIFEST.json is not read, even if it parses."""
        self._make_legacy_install()
        manifest_path = self.host / 'MANIFEST.json'
        canary = self.tmp / 'legacy-manifest-canary.json'
        canary.write_bytes(manifest_path.read_bytes())
        manifest_path.unlink()
        manifest_path.symlink_to(canary)
        before = raw_snapshot(self.host)
        with observe_path_reads() as reads:
            plan = self.plan()
        self.assertNotIn(canary.resolve(), [path.resolve() for path in reads])
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('not a regular file' in problem or 'symlink' in problem
                            for problem in plan.problems), plan.problems)
        self.assertEqual(plan.writes, {})
        self.assertEqual(plan.legacy_removals, [])
        self.assertEqual(raw_snapshot(self.host), before)

    def test_ac9_legacy_manifest_entry_symlink_is_rejected_before_any_read(self):
        """A listed old-root file that is a symlink is refused, not followed."""
        self._make_legacy_install()
        canary = self.tmp / 'legacy-entry-canary.txt'
        canary.write_text('SYNTHETIC_PRIVATE_CANARY_153_LEGACY_SYMLINK\n',
                          encoding='utf-8')
        linked = self.host / 'README.md'
        linked.unlink()
        linked.symlink_to(canary)
        manifest_path = self.host / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        data = canary.read_bytes()
        for entry in manifest['files']:
            if entry['path'] == 'README.md':
                entry['sha256'] = _sha(data)
                entry['size'] = len(_normalized(data))
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        before = raw_snapshot(self.host)
        with observe_path_reads() as reads:
            plan = self.plan()
        self.assertNotIn(canary.resolve(), [path.resolve() for path in reads])
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('not a regular file' in problem or 'symlink' in problem
                            for problem in plan.problems), plan.problems)
        self.assertEqual(plan.writes, {})
        self.assertEqual(plan.legacy_removals, [])
        self.assertEqual(raw_snapshot(self.host), before)

    def test_ac9_legacy_manifest_entry_directory_is_rejected_without_read(self):
        """A listed old-root path that is a directory is refused as non-regular."""
        self._make_legacy_install()
        entry_path = self.host / 'README.md'
        entry_path.unlink()
        entry_path.mkdir()
        before = raw_snapshot(self.host)
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('not a regular file' in problem
                            for problem in plan.problems), plan.problems)
        self.assertEqual(plan.writes, {})
        self.assertEqual(raw_snapshot(self.host), before)

    def test_ac9_legacy_manifest_entry_fifo_does_not_block_planning(self):
        """A listed old-root FIFO with no writer must be rejected, not waited on.

        ``open(..., O_RDONLY)`` on a FIFO blocks until a writer appears, so type
        rejection that only happens after a blocking open makes planning hang.
        Planning runs in a short-timeout child: a blocking open fails the test as
        a timeout, while the fixed reader opens non-blocking and refuses the
        non-regular leaf before any content is read.
        """
        import subprocess
        self._make_legacy_install()
        entry_path = self.host / 'README.md'
        entry_path.unlink()
        os.mkfifo(entry_path)
        before = raw_snapshot(self.host)
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cmd = [sys.executable, '-c', PLAN_PROBE_DRIVER, str(self.host), str(self.source)]
        try:
            proc = subprocess.run(cmd, cwd=self.tmp, env=env, capture_output=True,
                                  text=True, timeout=10)
        except subprocess.TimeoutExpired:
            self.fail('planning blocked on an old-root FIFO instead of rejecting it')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload['status'], 'STOPPED_CONFLICT', payload)
        self.assertTrue(any('not a regular file' in problem
                            for problem in payload['problems']), payload['problems'])
        self.assertEqual(payload['writes'], 0)
        self.assertEqual(payload['legacy_removals'], [])
        self.assertTrue(entry_path.exists())
        self.assertEqual(raw_snapshot(self.host), before)

    def test_observe_path_reads_positive_control_detects_dir_fd_open(self):
        """A relative ``os.open`` via ``dir_fd`` must be attributed to its real path.

        Without resolving ``dir_fd`` the spy sees only a bare leaf name, so an
        external canary opened through a directory descriptor would look like a
        benign relative open. This positive control proves the spy can see it.
        """
        external = self.tmp / 'external-dir-fd-control'
        external.mkdir()
        canary = external / 'canary.txt'
        canary.write_text('SYNTHETIC_PRIVATE_CANARY_153_DIRFD\n', encoding='utf-8')
        directory_fd = os.open(external, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with observe_path_reads() as reads:
                descriptor = os.open('canary.txt', os.O_RDONLY, dir_fd=directory_fd)
                try:
                    os.read(descriptor, 8)
                finally:
                    os.close(descriptor)
        finally:
            os.close(directory_fd)
        self.assertIn(canary.resolve(), [path.resolve() for path in reads])

    def test_ac9_legacy_reader_rejects_symlink_parent_at_open_time(self):
        """The old-root reader walks from the root dir_fd and rejects on open.

        Calling the primitive directly means no separate full-path ``lstat``
        pre-check can be the thing that rejects the symlinked ``docs/``; the
        component open itself must fail before the external canary is read.
        """
        self._make_legacy_install()
        external = self.tmp / 'external-fd-walk-docs'
        shutil.move(str(self.host / 'docs'), str(external))
        (self.host / 'docs').symlink_to(external, target_is_directory=True)
        canary = external / 'orchestration.md'
        self.assertEqual(canary.read_bytes(),
                         (self.source / 'docs' / 'orchestration.md').read_bytes())
        root_fd = os.open(self.host, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with observe_path_reads() as reads:
                with self.assertRaises(workspace.OnboardingError) as raised:
                    workspace._read_regular_file_at(
                        root_fd, 'docs/orchestration.md',
                        'old root docs/orchestration.md')
        finally:
            os.close(root_fd)
        self.assertIn('symlink', str(raised.exception))
        self.assertNotIn(canary.resolve(), [path.resolve() for path in reads])

    def test_ac9_legacy_symlinked_docs_directory_is_rejected_before_any_read(self):
        """A symlinked old-root ``docs/`` must not redirect a baseline read."""
        self._make_legacy_install()
        external = self.tmp / 'external-legacy-docs'
        shutil.move(str(self.host / 'docs'), str(external))
        (self.host / 'docs').symlink_to(external, target_is_directory=True)
        canary = external / 'orchestration.md'
        self.assertEqual(canary.read_bytes(),
                         (self.source / 'docs' / 'orchestration.md').read_bytes())
        self._install_linked_worktree()
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-symlinked-docs', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, _target_release, _fetch, _download = \
            self._upgrade_transport(upgraded)
        before = raw_snapshot(self.host, skip_names=('.git',))
        with observe_path_reads() as reads:
            plan = workspace.plan_isolated_onboarding(
                self.host, source_root=upgraded, verified_source=target,
                verified_baseline=baseline)
        self.assertNotIn(canary.resolve(), [path.resolve() for path in reads])
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('symlink' in problem for problem in plan.problems),
                        plan.problems)
        self.assertEqual(plan.writes, {})
        self.assertEqual(plan.legacy_removals, [])
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac9_legacy_symlinked_tests_directory_is_rejected_before_any_read(self):
        """A symlinked old-root ``tests/`` must not redirect a baseline read."""
        self._add_legacy_member('tests/test_fixture.py', 'synthetic downstream test\n')
        self._make_legacy_install()
        external = self.tmp / 'external-legacy-tests'
        shutil.move(str(self.host / 'tests'), str(external))
        (self.host / 'tests').symlink_to(external, target_is_directory=True)
        canary = external / 'test_fixture.py'
        self._install_linked_worktree()
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-symlinked-tests', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, _target_release, _fetch, _download = \
            self._upgrade_transport(upgraded)
        before = raw_snapshot(self.host, skip_names=('.git',))
        with observe_path_reads() as reads:
            plan = workspace.plan_isolated_onboarding(
                self.host, source_root=upgraded, verified_source=target,
                verified_baseline=baseline)
        self.assertNotIn(canary.resolve(), [path.resolve() for path in reads])
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('symlink' in problem for problem in plan.problems),
                        plan.problems)
        self.assertEqual(plan.writes, {})
        self.assertEqual(plan.legacy_removals, [])
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac9_rollback_rejects_untrusted_backup_deletion_of_host_file(self):
        victim = self.host / 'src' / 'host_owned.py'
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim.write_bytes(b'host data must survive\n')
        backup = self.tmp / 'forged-backup'
        backup.mkdir()
        (backup / 'backup.json').write_text(json.dumps({
            'files': [{'path': 'src/host_owned.py', 'present': False,
                       'sha256': None, 'size': None}]}), encoding='utf-8')
        result = workspace.rollback_isolated_onboarding({
            'host_root': str(self.host), 'backup_location': str(backup)})
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertEqual(victim.read_bytes(), b'host data must survive\n')

    def test_ac9_local_modification_stops_without_writes(self):
        manifest = self._make_legacy_install()
        victim = self.host / 'README.md'
        victim.write_text('local edit\n', encoding='utf-8')
        before = raw_snapshot(self.host)
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('README.md' in problem for problem in plan.problems))
        self.assertEqual(raw_snapshot(self.host), before)

    def test_ac9_untracked_kit_file_stops(self):
        self._make_legacy_install()
        (self.host / 'scripts' / 'local_fork.py').write_text('fork\n', encoding='utf-8')
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)

    def test_ac9_target_conflict_stops(self):
        self._make_legacy_install()
        (self.host / 'agentorch').mkdir()
        (self.host / 'agentorch' / 'notes.md').write_text('keep\n', encoding='utf-8')
        plan = self.plan()
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)

    def test_ac9_authenticated_legacy_migration_installs_layout_and_preserves_root(self):
        """Only the provable slice: exact old root + re-fetched official Release."""
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        self._make_legacy_install()
        head = self._install_linked_worktree()
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-migrate', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n\nRules: '
                                     '[AGENTS.md](AGENTS.md).\n'})
        baseline, target, _baseline_release, _target_release, fetch, download = \
            self._upgrade_transport(upgraded)
        before = raw_snapshot(self.host, skip_names=('agentorch', '.git'))
        requested = []

        def recording_fetch(tag=None):
            requested.append(tag)
            return fetch(tag)

        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=baseline)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        self.assertEqual(plan.mode, 'MIGRATE')
        self.assertEqual(plan.legacy_removals, [])
        self.assertIsNone(plan.agents_text)
        self.assertEqual(plan.agents_action, 'unchanged')
        self.assertTrue(plan.writes)
        self.assertTrue(all(rel.startswith('agentorch/') for rel in plan.writes),
                        sorted(plan.writes))
        self.assertNotIn('AGENTS.md', plan.writes)
        self.assertFalse((self.host / 'agentorch').exists())

        with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                          side_effect=recording_fetch), \
             patch.object(sources.GitHubReleaseSource, 'download_asset',
                          side_effect=download):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'APPLIED', result)
        self.assertEqual(result['mode'], 'MIGRATE')
        self.assertEqual(result['removed'], [])
        # APPLY re-fetched the historical Release through the fixed client.
        self.assertIn('v1.7', requested)
        self.assertTrue((self.host / 'agentorch' / 'MANIFEST.json').is_file())
        # Every old-root path and byte survives; nothing was moved or deleted.
        self.assertEqual(raw_snapshot(self.host, skip_names=('agentorch', '.git')),
                         before)
        for rel in baseline.files:
            self.assertEqual((self.host / rel).read_bytes(), baseline.files[rel], rel)
        self.assertTrue((self.host / 'MANIFEST.json').is_file())

    def test_ac9_legacy_migration_repeat_does_not_rewrite_old_root_agents(self):
        """A second migration run must not append a managed block to the old root."""
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        self._make_legacy_install()
        head = self._install_linked_worktree()
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-repeat', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, _target_release, fetch, download = \
            self._upgrade_transport(upgraded)
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=baseline)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        self.assertEqual(plan.mode, 'MIGRATE')
        with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                          side_effect=fetch), \
             patch.object(sources.GitHubReleaseSource, 'download_asset',
                          side_effect=download):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'APPLIED', result)

        old_agents = (self.host / 'AGENTS.md').read_bytes()
        old_root_before = raw_snapshot(self.host, skip_names=('agentorch', '.git'))
        head = self._commit_all(self.host, 'legacy migration applied')
        second = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=target)
        self.assertIn(second.status, ('PLANNED', 'STOPPED_CONFLICT'), second.problems)
        if second.status == 'PLANNED':
            self.assertIsNone(second.agents_text)
            self.assertEqual(second.agents_action, 'unchanged')
            self.assertEqual(second.writes, {})
            replayed = workspace.apply_isolated_onboarding(
                second, declared_exclusive=True, expected_head=head)
            self.assertEqual(replayed.get('written') or [], [], replayed)
        self.assertEqual((self.host / 'AGENTS.md').read_bytes(), old_agents)
        self.assertNotIn(b'AGENTORCH:BEGIN', old_agents)
        self.assertEqual(raw_snapshot(self.host, skip_names=('agentorch', '.git')),
                         old_root_before)

    def test_ac9_legacy_migration_accepts_an_empty_target_directory(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        self._make_legacy_install()
        head = self._install_linked_worktree()
        (self.host / 'agentorch').mkdir()
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-empty-target', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, _target_release, fetch, download = \
            self._upgrade_transport(upgraded)
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=baseline)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        self.assertTrue(plan.target_present)
        self.assertTrue(plan.target_empty)
        with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                          side_effect=fetch), \
             patch.object(sources.GitHubReleaseSource, 'download_asset',
                          side_effect=download):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'APPLIED', result)
        self.assertTrue((self.host / 'agentorch' / 'MANIFEST.json').is_file())

    def test_ac9_legacy_migration_apply_rejects_changed_historical_release(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        self._make_legacy_install()
        head = self._install_linked_worktree()
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-rerefetch', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, _target_release, _fetch, _download = \
            self._upgrade_transport(upgraded)
        forged_source = make_source(
            self.tmp / 'source-1.7-forged-migration', version='1.7',
            overrides={'README.md': '# same tag, different historical bytes\n'})
        _forged, forged_release, forged_payload = self._tagged_release(
            forged_source, 'v1.7')
        target_release, target_payload = self._release_transport_from(upgraded)
        self.assertEqual(forged_release.tag_name, 'v1.7')
        payloads = {forged_release.assets[0].download_url: forged_payload,
                    target_release.assets[0].download_url: target_payload}

        def fetch(tag=None):
            return forged_release if tag == 'v1.7' else target_release

        def download(asset):
            return payloads[asset.download_url]

        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=baseline)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        before = raw_snapshot(self.host, skip_names=('.git',))
        with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                          side_effect=fetch), \
             patch.object(sources.GitHubReleaseSource, 'download_asset',
                          side_effect=download):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertEqual(result['written'], [])
        self.assertFalse((self.host / 'agentorch').exists())
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac9_legacy_migration_apply_rechecks_the_old_root_before_write(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        self._make_legacy_install()
        self._install_linked_worktree()
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-host-change', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, _target_release, fetch, download = \
            self._upgrade_transport(upgraded)
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=baseline)
        self.assertEqual(plan.status, 'PLANNED', plan.problems)
        # Commit the change so the worktree stays clean and the only possible
        # reason to stop is the old-root recheck inside APPLY.
        (self.host / 'README.md').write_text('edited after plan\n', encoding='utf-8')
        head = self._commit_all(self.host, 'edit old root after planning')
        before = raw_snapshot(self.host, skip_names=('.git',))
        with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                          side_effect=fetch), \
             patch.object(sources.GitHubReleaseSource, 'download_asset',
                          side_effect=download):
            result = workspace.apply_isolated_onboarding(
                plan, declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertEqual(result['written'], [])
        self.assertFalse((self.host / 'agentorch').exists())
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac9_authenticated_legacy_migration_rejects_local_modification(self):
        self._make_legacy_install()
        (self.host / 'README.md').write_text('local edit\n', encoding='utf-8')
        head = self._install_linked_worktree()
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-modified', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, _target_release, _fetch, _download = \
            self._upgrade_transport(upgraded)
        before = raw_snapshot(self.host, skip_names=('.git',))
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=baseline)
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertTrue(any('README.md' in problem for problem in plan.problems),
                        plan.problems)
        self.assertEqual(plan.writes, {})
        self.assertEqual(plan.legacy_removals, [])
        self.assertNotEqual(workspace.apply_isolated_onboarding(
            plan, declared_exclusive=True, expected_head=head)['status'], 'APPLIED')
        self.assertFalse((self.host / 'agentorch').exists())
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac9_authenticated_legacy_migration_target_conflict_stops(self):
        self._make_legacy_install()
        (self.host / 'agentorch').mkdir()
        (self.host / 'agentorch' / 'notes.md').write_text('keep\n', encoding='utf-8')
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-target', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        baseline, target, _baseline_release, _target_release, _fetch, _download = \
            self._upgrade_transport(upgraded)
        before = raw_snapshot(self.host)
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=upgraded, verified_source=target,
            verified_baseline=baseline)
        self.assertEqual(plan.status, 'STOPPED_CONFLICT', plan.problems)
        self.assertEqual(plan.writes, {})
        self.assertNotEqual(workspace.apply_isolated_onboarding(plan)['status'], 'APPLIED')
        self.assertEqual(raw_snapshot(self.host), before)

    def test_ac9_legacy_migration_missing_historical_asset_stays_zero_write(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        self._make_legacy_install()
        head = self._install_linked_worktree()
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-missing', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n'})
        _baseline, _target, _baseline_release, target_release, _fetch, download = \
            self._upgrade_transport(upgraded)

        def fetch(tag=None):
            if tag is None:
                return target_release
            raise sources.SourceError('ASSET_SHA256', 'synthetic historical asset missing')

        before = raw_snapshot(self.host, skip_names=('.git',))
        with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                          side_effect=fetch), \
             patch.object(sources.GitHubReleaseSource, 'download_asset',
                          side_effect=download):
            prepared = entry.prepare_isolated_install(
                self.host, source_root=upgraded, mode='APPLY',
                declared_exclusive=True, expected_head=head)
        self.assertEqual(prepared['result']['status'], 'STOPPED_CONFLICT',
                         prepared['result'])
        self.assertFalse((self.host / 'agentorch').exists())
        self.assertEqual(raw_snapshot(self.host, skip_names=('.git',)), before)

    def test_ac9_legacy_migration_via_entry_apply_preserves_old_root(self):
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        self._make_legacy_install()
        head = self._install_linked_worktree()
        upgraded = make_source(
            self.tmp / 'source-1.8-legacy-entry', version='1.8',
            overrides={'README.md': '# AgentOrch 1.8\n\nRules: '
                                     '[AGENTS.md](AGENTS.md).\n'})
        _baseline, _target, _baseline_release, _target_release, fetch, download = \
            self._upgrade_transport(upgraded)
        before = raw_snapshot(self.host, skip_names=('agentorch', '.git'))
        with patch.object(sources.GitHubReleaseSource, 'fetch_release',
                          side_effect=fetch), \
             patch.object(sources.GitHubReleaseSource, 'download_asset',
                          side_effect=download):
            prepared = entry.prepare_isolated_install(
                self.host, source_root=upgraded, mode='APPLY',
                declared_exclusive=True, expected_head=head)
        self.assertEqual(prepared['result']['status'], 'APPLIED', prepared['result'])
        self.assertEqual(prepared['payload']['mode'], 'MIGRATE')
        self.assertTrue((self.host / 'agentorch' / 'MANIFEST.json').is_file())
        self.assertEqual(raw_snapshot(self.host, skip_names=('agentorch', '.git')),
                         before)

    # --- AC-11 ------------------------------------------------------------
    def test_ac11_empty_directory_is_not_treated_as_an_existing_project(self):
        empty = self.tmp / 'empty'
        empty.mkdir()
        self.assertEqual(workspace.detect_install_scenario(empty), 'new-project')
        plan = workspace.plan_isolated_onboarding(
            empty, source_root=self.source, scenario='isolated')
        self.assertEqual(plan.status, 'STOPPED_SCENARIO', plan.problems)
        self.assertTrue(plan.guidance)

    def test_ac11_new_project_mode_is_not_applied_to_an_existing_project(self):
        self.assertEqual(workspace.detect_install_scenario(self.host), 'existing-project')
        plan = workspace.plan_isolated_onboarding(
            self.host, source_root=self.source, scenario='new-project')
        self.assertEqual(plan.status, 'STOPPED_SCENARIO', plan.problems)
        self.assertFalse((self.host / 'agentorch').exists())

    def test_ac9_write_failure_rolls_back_automatically(self):
        from unittest.mock import patch
        from scripts.update_agentorch import sources
        head = self._install_linked_worktree()
        before = raw_snapshot(self.host)
        plan = self.plan()
        self.assertEqual(plan.status, 'PLANNED', plan.problems)

        class Explode:
            def __init__(self):
                self.count = 0

            def after_write(self, rel):
                self.count += 1
                if self.count == 2:
                    raise RuntimeError('synthetic write failure')

        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            result = workspace.apply_isolated_onboarding(
                plan, fault_injector=Explode(),
                declared_exclusive=True, expected_head=head)
        self.assertEqual(result['status'], 'ROLLED_BACK', result)
        self.assertEqual(result['rollback']['result'], 'SUCCEEDED')
        self.assertEqual(raw_snapshot(self.host), before)
        self.assertFalse((self.host / 'agentorch').exists())

    # --- CLI entry --------------------------------------------------------
    def test_cli_install_isolated_runs_from_an_external_cwd(self):
        import subprocess
        head = self._linked_worktree_host()
        external = self.tmp / 'external'
        external.mkdir()
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cmd = [sys.executable, '-c', SYNTHETIC_DRIVER, str(self.source),
               '--install-isolated', str(self.host), '--source', str(self.source),
               '--mode', 'APPLY', '--declared-exclusive', '--expected-head', head]
        proc = subprocess.run(cmd, cwd=external, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload['status'], 'APPLIED', payload)
        self.assertEqual(payload['exclusive_writer'], 'DECLARED_NOT_VERIFIED')
        self.assertEqual(payload['worktree_head'], head)
        self.assertTrue((self.host / 'agentorch' / 'README.md').is_file())
        self.assertTrue((self.host / 'AGENTS.md').read_text(encoding='utf-8').count(
            workspace.MANAGED_BEGIN_PREFIX) == 1)

    def test_cli_rollback_is_blocked_and_preserves_the_installed_tree(self):
        import subprocess
        from unittest.mock import patch
        from scripts.update_agentorch import entry, sources
        head = self._linked_worktree_host()
        release, payload = self._review_release_transport()
        with patch.object(sources.GitHubReleaseSource, 'fetch_release', return_value=release), \
             patch.object(sources.GitHubReleaseSource, 'download_asset', return_value=payload):
            prepared = entry.prepare_isolated_install(
                self.host, source_root=self.source, mode='APPLY',
                declared_exclusive=True, expected_head=head)
        self.assertEqual(prepared['result']['status'], 'APPLIED', prepared['result'])
        # The public payload deliberately omits the local backup path; only the
        # in-process result carries the real directory the rollback may be given.
        self.assertIsNone(prepared['payload']['backup_location'])
        backup = prepared['result']['backup_location']
        self.assertIsInstance(backup, str)
        self.assertTrue(Path(backup).is_dir())
        after_apply = raw_snapshot(self.host)
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        rollback_cmd = [sys.executable, '-m', 'scripts.update_agentorch.entry',
                        '--install-isolated', str(self.host),
                        '--rollback-backup', backup]
        rolled = subprocess.run(rollback_cmd, cwd=self.tmp, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(rolled.returncode, 0)
        self.assertEqual(json.loads(rolled.stdout)['status'], 'BLOCKED')
        self.assertEqual(raw_snapshot(self.host), after_apply)

    def test_cli_plan_mode_does_not_write(self):
        import subprocess
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        before = raw_snapshot(self.host)
        # Substitute only the network transport in this child process; the
        # production Release selection, digest and archive verifier still run.
        driver = '''
import hashlib, io, json, sys, zipfile
from pathlib import Path
from unittest.mock import patch
from scripts.update_agentorch import entry, sources
root = Path(sys.argv[1])
manifest_bytes = (root / "MANIFEST.json").read_bytes()
manifest = json.loads(manifest_bytes)
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w") as archive:
    for item in manifest["files"]:
        archive.writestr("agentorch/" + item["path"], (root / item["path"]).read_bytes())
    archive.writestr("agentorch/MANIFEST.json", manifest_bytes)
payload = buffer.getvalue()
asset = sources.ReleaseAsset("agentorch.zip", len(payload),
    "sha256:" + hashlib.sha256(payload).hexdigest(),
    "https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.7/agentorch.zip")
release = sources.ReleaseInfo(sources.OFFICIAL_REPOSITORY, "v1.7", (asset,))
with patch.object(sources.GitHubReleaseSource, "fetch_release", return_value=release), \\
     patch.object(sources.GitHubReleaseSource, "download_asset", return_value=payload):
    raise SystemExit(entry.main(sys.argv[2:]))
'''
        cmd = [sys.executable, '-c', driver, str(self.source),
               '--install-isolated', str(self.host), '--source', str(self.source),
               '--mode', 'PLAN']
        proc = subprocess.run(cmd, cwd=self.tmp, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['status'], 'PLANNED')
        self.assertEqual(raw_snapshot(self.host), before)

    def test_cli_blocked_install_does_not_overwrite_host_report_target(self):
        import subprocess
        victim = self.host / 'README.md'
        original = victim.read_bytes()
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cmd = [sys.executable, '-m', 'scripts.update_agentorch.entry',
               '--install-isolated', str(self.host), '--source', str(self.source),
               '--mode', 'APPLY', '--output', str(victim)]
        proc = subprocess.run(cmd, cwd=self.tmp, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(victim.read_bytes(), original)

    # --- scope root -------------------------------------------------------
    def test_scope_store_uses_agentorch_root_inside_an_isolated_install(self):
        from scripts.agent_selection import scope
        self.install()
        store = scope.store_root(self.host)
        self.assertEqual(store, (self.host / 'agentorch' / '.local' / 'agent-orch').resolve())
        cwd = Path.cwd()
        try:
            os.chdir(self.tmp)
            self.assertEqual(scope.agentorch_root(), ROOT.resolve())
        finally:
            os.chdir(cwd)


class IsolatedEntryCommandsTest(unittest.TestCase):
    """#153: the supported isolated entry must name the layout parameter.

    The isolated scheduler deliberately refuses a bare invocation because a
    damaged isolated install and a legacy flat copy can be byte-identical. Every
    command the managed block, the local manual and the updater next steps hand
    to an operator therefore has to carry ``--layout isolated`` explicitly,
    while flat checkouts keep their historical bare commands.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.host = copy_host(self.tmp / 'host')

    def _complete_tree(self):
        from scripts import build_release, check_drift
        files = build_release.collect_sources(build_release.read_required())
        suite, version = check_drift.read_version(ROOT)
        agentorch = self.host / 'agentorch'
        transformed = {
            rel: workspace._rewrite_host_agents_links(rel, data, ROOT, self.host)
            for rel, data in files.items() if rel != 'AGENTS.md'
        }
        transformed['.gitignore'] = workspace.AGENTORCH_GITIGNORE.encode()
        for rel, data in transformed.items():
            path = agentorch / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (agentorch / 'MANIFEST.json').write_bytes(
            workspace._manifest_bytes(transformed, suite, version))
        (self.host / 'AGENTS.md').write_text(
            '# Synthetic host\n\n' + workspace.managed_block(version), encoding='utf-8')
        return agentorch

    def test_managed_block_and_manual_name_the_layout_parameter(self):
        block = workspace.managed_block(FORMAT_VERSION)
        self.assertIn('--layout isolated', block)
        self.assertIn('agentorch/scripts/orchestrator.py', block)
        self.assertIn('agentorch/.local/orchestration/', block)
        manual = (ROOT / 'editions' / 'local' / 'master.md').read_text(encoding='utf-8')
        self.assertIn('--layout isolated', manual)
        self.assertIn('agentorch/.local/orchestration/', manual)
        # Flat installs keep the historical bare command.
        self.assertIn('python scripts/orchestrator.py init', manual)
        execution = (ROOT / 'editions' / 'local' / 'execution.md').read_text(encoding='utf-8')
        self.assertIn('--layout isolated', execution)
        project = (ROOT / 'templates' / 'project.md').read_text(encoding='utf-8')
        self.assertIn('agentorch/.local', project)

    def _run_generated_next_steps(self, steps, cwd, task_id):
        """Execute the generated commands and return the state directory.

        The updater emits operator commands, not a script: the test performs the
        same placeholder substitution an operator would, then runs the real
        scheduler so a wrong flag or a wrong working directory fails here.
        """
        import shlex
        import sqlite3
        import subprocess

        replacements = {
            '<TASK-ID>': task_id,
            '<title>': 'isolated onboarding next steps',
            '<allowed paths>': 'src/',
            '<AC-1>': 'task add exits 0',
            '<AC-2>': 'dispatch takes the path lock',
        }
        commands = []
        for step in steps:
            for placeholder, value in replacements.items():
                step = step.replace(placeholder, value)
            tokens = shlex.split(step)
            if not tokens or tokens[0] not in ('python', 'python3'):
                continue
            tokens[0] = sys.executable
            commands.append(tokens)
            proc = subprocess.run(tokens, cwd=str(cwd), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f'{step}\n{proc.stdout}{proc.stderr}')
        self.assertTrue(commands, steps)
        return commands

    def test_updater_local_next_steps_run_from_the_documented_root(self):
        from scripts.update_agentorch import entry
        import sqlite3

        # Flat install: run the historical bare command from the project root,
        # which is also where scripts/orchestrator.py lives.
        flat = self.tmp / 'flat'
        (flat / 'scripts').mkdir(parents=True)
        shutil.copyfile(ROOT / 'scripts' / 'orchestrator.py',
                        flat / 'scripts' / 'orchestrator.py')
        (flat / 'docs').mkdir()
        (flat / 'docs' / 'PROJECT.md').write_text(
            '# Project\n\n- `edition`: `local`\n', encoding='utf-8')
        flat_commands = self._run_generated_next_steps(
            entry._local_next_steps('flat'), flat, 'TASK-153F')
        self.assertTrue(flat_commands)
        self.assertTrue(all('--layout' not in command for command in flat_commands))
        flat_state = flat / '.orchestration'
        self.assertTrue((flat_state / 'state.sqlite3').is_file())
        self.assertFalse((flat / '.local' / 'orchestration').exists())
        con = sqlite3.connect(flat_state / 'state.sqlite3')
        try:
            self.assertEqual(
                con.execute('SELECT status FROM tasks WHERE id=?',
                            ('TASK-153F',)).fetchone()[0], 'DISPATCHED')
            self.assertEqual(
                [row[0] for row in con.execute(
                    'SELECT path FROM locks WHERE task_id=?', ('TASK-153F',))],
                ['src'])
        finally:
            con.close()

        # Isolated install: the managed block documents the host root as the
        # working directory and agentorch/scripts/orchestrator.py --layout
        # isolated as the command. The generated sequence starts with the
        # idempotent `init`, then registers, readies and dispatches the task.
        agentorch = self._complete_tree()
        (agentorch / 'docs' / 'PROJECT.md').write_text(
            '# Project\n\n- `edition`: `local`\n', encoding='utf-8')
        isolated_commands = self._run_generated_next_steps(
            entry._local_next_steps('isolated'), self.host, 'TASK-153E')
        self.assertTrue(isolated_commands)
        for command in isolated_commands:
            self.assertEqual(command[1], 'agentorch/scripts/orchestrator.py', command)
            self.assertEqual(command[2:4], ['--layout', 'isolated'], command)
        state = agentorch / '.local' / 'orchestration'
        self.assertTrue((state / 'state.sqlite3').is_file())
        self.assertFalse((self.host / '.orchestration').exists())
        con = sqlite3.connect(state / 'state.sqlite3')
        try:
            self.assertEqual(
                con.execute('SELECT status FROM tasks WHERE id=?',
                            ('TASK-153E',)).fetchone()[0], 'DISPATCHED')
            self.assertEqual(
                [row[0] for row in con.execute(
                    'SELECT path FROM locks WHERE task_id=?', ('TASK-153E',))],
                ['src'])
        finally:
            con.close()

    def test_documented_isolated_init_runs_and_lands_in_kit_local(self):
        import subprocess
        agentorch = self._complete_tree()
        documented = [sys.executable, 'agentorch/scripts/orchestrator.py',
                      '--layout', 'isolated', 'init']
        proc = subprocess.run(documented, cwd=str(self.host), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        state = agentorch / '.local' / 'orchestration'
        self.assertTrue((state / 'state.sqlite3').is_file())
        self.assertTrue((state / 'config.json').is_file())
        self.assertTrue((state / 'backups').is_dir())
        self.assertFalse((self.host / '.orchestration').exists())
        self.assertFalse((agentorch / '.orchestration').exists())
        bare = subprocess.run(
            [sys.executable, 'agentorch/scripts/orchestrator.py', 'init'],
            cwd=str(self.host), capture_output=True, text=True)
        self.assertEqual(bare.returncode, 2, bare.stdout + bare.stderr)
        self.assertIn('--layout', bare.stderr)

    def test_flat_package_self_check_and_flat_commands_are_not_rejected(self):
        import subprocess
        from scripts import validate_kit
        self.assertEqual(validate_kit.validate_kit(ROOT)[0], [])
        flat = self.tmp / 'flat-root'
        (flat / 'docs').mkdir(parents=True)
        (flat / 'docs' / 'PROJECT.md').write_text(
            '# Project\n\n- `edition`：`local`\n', encoding='utf-8')
        proc = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'orchestrator.py'),
             '--root', str(flat), 'init'],
            cwd=str(self.tmp), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((flat / '.orchestration' / 'state.sqlite3').is_file())
        self.assertFalse((flat / '.local' / 'orchestration').exists())


if __name__ == '__main__':
    unittest.main()
