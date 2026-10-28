"""Group B - migration / versioning scenarios (B1-B14).

Idempotent migration, partial writes, version isolation, and the safe update
engine (conflicts, bad packages, traversal, removal, baseline, rollback).
Every fixture is synthetic and lives in the scenario sandbox.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .common import (
    _canonical,
    _drift,
    _expect_clean,
    _expect_finding,
    _packet,
    _packet_errors,
    _require,
    _row,
    _update,
    _write_text,
)


# --- migration fixtures -----------------------------------------------------
TARGET_VERSION = '2024'
LEGACY_VERSION = '2014'
LEGACY_RECORDS = (
    {'id': 'role.guard', 'version': LEGACY_VERSION, 'name': 'guard', 'hp': 12},
    {'id': 'role.scout', 'version': LEGACY_VERSION, 'name': 'scout', 'hp': 8},
    {'id': 'role.healer', 'version': LEGACY_VERSION, 'name': 'healer', 'hp': 10},
)


def _write_records(path, records):
    path.write_text(json.dumps({'records': list(records)}, sort_keys=True),
                    encoding='utf-8')


def _read_records(path):
    return json.loads(path.read_text(encoding='utf-8'))['records']


def _migrate(records, target=TARGET_VERSION):
    """Deterministic and idempotent: re-running on migrated rows changes nothing."""
    return [{'id': row['id'], 'version': target, 'name': row['name'],
             'hp': row.get('hp', 10)} for row in records]


def _digest(records):
    payload = json.dumps(records, sort_keys=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _verify_migration(path, expected_version=TARGET_VERSION,
                      expected_count=len(LEGACY_RECORDS)):
    """Independent check of a migration artifact; empty list means consistent."""
    if not path.is_file():
        return [f'migration artifact missing: {path.name}']
    records = _read_records(path)
    problems = []
    if len(records) != expected_count:
        problems.append(f'partial migration: {len(records)} of {expected_count} records present')
    ids = [row['id'] for row in records]
    if len(set(ids)) != len(ids):
        problems.append('duplicate record ids: the rerun was not idempotent')
    for row in records:
        if row.get('version') != expected_version:
            problems.append(f'version isolation broken: {row["id"]} is '
                            f'{row.get("version")!r}, expected {expected_version!r}')
    marker = path.with_suffix('.complete')
    if not marker.is_file():
        problems.append('completion marker missing: the migration never closed out')
    elif marker.read_text(encoding='utf-8').strip() != _digest(records):
        problems.append('completion marker does not match the artifact: the write was interrupted')
    return problems


# --------------------------------------------------------------------------- #
# Group B - migration / versioning
# --------------------------------------------------------------------------- #
def scenario_b1(ctx):
    """Positive control: migration is idempotent and version-isolated."""
    path = ctx.sandbox / 'roles.json'
    _write_records(path, LEGACY_RECORDS)
    first = _migrate(_read_records(path))
    _write_records(path, first)
    path.with_suffix('.complete').write_text(_digest(first), encoding='utf-8')
    _expect_clean(_verify_migration(path), 'first migration')

    second = _migrate(_read_records(path))
    _write_records(path, second)
    path.with_suffix('.complete').write_text(_digest(second), encoding='utf-8')
    _expect_clean(_verify_migration(path), 'rerun')
    _require(_digest(first) == _digest(second), 'the rerun changed the artifact digest')
    return [f'migration: {len(first)} records upgraded {LEGACY_VERSION} -> {TARGET_VERSION}',
            f'rerun digest unchanged: {_digest(first)[:16]}',
            'verification: 0 findings (count / version / ids / completion marker)']


def scenario_b2(ctx):
    """A migration interrupted halfway must be visible, not silently accepted."""
    path = ctx.sandbox / 'roles.json'
    _write_records(path, LEGACY_RECORDS)
    backup = _read_records(path)
    _write_records(path, _migrate(backup)[:1])  # crash after the first record
    findings = _verify_migration(path)
    count_finding = _expect_finding(findings, 'partial migration', 'half-written artifact')
    _expect_finding(findings, 'completion marker missing', 'unclosed migration')

    _write_records(path, backup)
    _write_records(path, _migrate(_read_records(path)))
    path.with_suffix('.complete').write_text(_digest(_read_records(path)), encoding='utf-8')
    _expect_clean(_verify_migration(path), 'recovery from the backup')
    return [f'gate fired: {count_finding}', 'gate fired: completion marker missing',
            'recovery: artifact restored from the synthetic backup and verified']


def scenario_b3(ctx):
    """Mixing two rule versions inside one target set is a version isolation break."""
    path = ctx.sandbox / 'roles.json'
    records = _migrate(LEGACY_RECORDS)
    records[1] = dict(records[1], version=LEGACY_VERSION)  # one legacy row survives
    _write_records(path, records)
    path.with_suffix('.complete').write_text(_digest(records), encoding='utf-8')
    finding = _expect_finding(_verify_migration(path), 'version isolation broken',
                              'mixed versions in the target set')
    return [f'gate fired: {finding}']


def scenario_b4(ctx):
    """A 'migration completed' claim is contradicted by the missing artifact."""
    fields = _canonical(ctx)[0]
    text = _packet(ctx, [('x', 'AC-1', '旧格式数据已升级到目标版本')],
                   [_row(fields, ac='AC-1', actual='2024 产物已生成，迁移完成')])
    _expect_clean(_packet_errors(ctx, text), 'claim alone is structurally valid')

    missing = _verify_migration(ctx.sandbox / 'absent.json')
    finding = _expect_finding(missing, 'migration artifact missing', 'artifact check')
    return ['packet: the claim is structurally well formed (this is why a claim is not evidence)',
            f'artifact check contradicts the claim: {finding}']


# --------------------------------------------------------------------------- #
# Safe-update fixtures (#51/#52): synthetic project tree + synthetic release zip
# --------------------------------------------------------------------------- #
UPDATE_SUITE = 'Agent-Orchestration Starter Kit'
UPDATE_TOP = 'agentorch-20260101'
UPDATE_NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
UPDATE_V15 = {
    'AGENTS.md': b'# AGENTS v1.5\n',
    'kit/orchestration.md': b'# orchestration v1.5\n',
    'templates/task-packet.md': b'# packet v1.5\n',
    'editions/github/master.md': b'# master v1.5\n',
}
UPDATE_V16 = {
    'AGENTS.md': b'# AGENTS v1.6\n',
    'kit/orchestration.md': b'# orchestration v1.6\n',
    'templates/task-packet.md': b'# packet v1.6\n',
    'editions/github/master.md': b'# master v1.6\n',
    'kit/new-in-16.md': b'# new in 1.6\n',
}
UPDATE_PROJECT_FILES = {
    'docs/PROJECT.md': b'# synthetic project configuration (project owned)\n',
    'src/app.py': b'print("synthetic project source")\n',
}


def _update_manifest(files, format_version='1.6'):
    drift = _drift()
    return drift.build_manifest({rel: bytes(data) for rel, data in files.items()},
                                UPDATE_SUITE, format_version)


def _update_zip(files, manifest=None, *, top=UPDATE_TOP, raw_members=()):
    drift = _drift()

    def member(archive, name, data, mode=0o644):
        info = zipfile.ZipInfo(filename=name)
        info.external_attr = mode << 16
        info.create_system = 3
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, data)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(files):
            member(archive, f'{top}/{rel}', drift.normalize_line_endings(bytes(files[rel])))
        payload = manifest if manifest is not None else _update_manifest(files)
        if isinstance(payload, bytes):
            text = payload
        else:
            text = drift.dumps_manifest(payload).encode('utf-8')
        member(archive, f'{top}/MANIFEST.json', text)
        for name, data, mode in raw_members:
            member(archive, name, data, mode)
    return buffer.getvalue()


class _UpdateSource:
    """In-memory ReleaseSource: records calls so a scenario can prove zero network."""

    def __init__(self, release, payload=None, *, fetch_error=None, download_error=None):
        self.release = release
        self.payload = payload
        self.fetch_error = fetch_error
        self.download_error = download_error
        self.fetch_count = 0
        self.download_count = 0

    def fetch_release(self):
        self.fetch_count += 1
        if self.fetch_error:
            raise self.fetch_error
        return self.release

    def download_asset(self, asset):
        self.download_count += 1
        if self.download_error:
            raise self.download_error
        return self.payload


def _update_release(tag='v1.6', payload=b'', *, repository=None, digest='auto'):
    update = _update()
    repository = repository or update.sources.OFFICIAL_REPOSITORY
    if digest == 'auto':
        digest = 'sha256:' + hashlib.sha256(payload).hexdigest()
    asset = update.sources.ReleaseAsset(
        name=f'{UPDATE_TOP}.zip', size=len(payload), digest=digest,
        download_url=(f'https://github.com/{repository}/releases/download/'
                      f'{tag}/{UPDATE_TOP}.zip'))
    return update.sources.ReleaseInfo(
        repository=repository, tag_name=tag, assets=(asset,),
        html_url=f'https://github.com/{repository}/releases/tag/{tag}')


def _update_project(sandbox, *, upstream=None, baseline_format='1.5', manifest=True,
                    git=False, name='project'):
    root = Path(sandbox) / name
    for rel, data in (upstream or UPDATE_V15).items():
        _write_text(root, rel, data.decode('utf-8'))
    for rel, data in UPDATE_PROJECT_FILES.items():
        _write_text(root, rel, data.decode('utf-8'))
    if manifest:
        payload = _update_manifest(upstream or UPDATE_V15, baseline_format)
        _write_text(root, 'MANIFEST.json', _drift().dumps_manifest(payload))
    if git:
        _git_init(root)
    return root


def _git(root, *args):
    proc = subprocess.run(
        ['git', '-c', 'user.email=scenario@example.invalid',
         '-c', 'user.name=Scenario', *args],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding='utf-8', errors='replace',
        env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'})
    return proc


def _git_init(root):
    _git(root, 'init', '-q')
    # Pin line endings in the scenario repository itself, not just on the
    # commands above: the update engine later runs `git worktree add` in this
    # repository, so a runner-wide `core.autocrlf=true` would otherwise check
    # the isolated worktree out with CRLF and make the byte comparison against
    # the LF payload depend on the host git configuration.
    _git(root, 'config', 'core.autocrlf', 'false')
    _git(root, 'config', 'core.eol', 'lf')
    _git(root, 'add', '-A')
    _git(root, 'commit', '-qm', 'synthetic baseline')
    return _git(root, 'rev-parse', 'HEAD').stdout.strip()


def _snapshot(root):
    """Every regular file except VCS / worktree metadata, byte for byte."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in {'.git', '.worktrees'})
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink() or not full.is_file():
                continue
            found[full.relative_to(root).as_posix()] = full.read_bytes()
    return dict(sorted(found.items()))


class _UpdateFault:
    """Deterministic fault injection for the update engine (scenario local only)."""

    def __init__(self, *, fail_after_writes=None, before_backup=False,
                 corrupt_after_restore=None):
        self.fail_after_writes = fail_after_writes
        self.fail_before_backup = before_backup
        self.corrupt_after_restore = corrupt_after_restore
        self.writes = 0

    def before_backup(self):
        if self.fail_before_backup:
            raise OSError('synthetic backup failure')

    def after_write(self, relative):
        self.writes += 1
        if self.fail_after_writes is not None and self.writes >= self.fail_after_writes:
            raise OSError(f'synthetic write failure after {relative}')

    def after_restore(self, root):
        if self.corrupt_after_restore:
            (Path(root) / self.corrupt_after_restore).write_bytes(b'corrupted by scenario\n')


def _run_update(project, source, *, mode='APPLY', fault=None, isolation=True):
    update = _update()
    return update.engine.run_update(project, mode=mode, source=source, edition='github',
                                    now=UPDATE_NOW, fault_injector=fault,
                                    isolation=isolation)


def _update_regression(root, target, manifest):
    """Independent post-apply regression: bytes, bookkeeping, project-owned files."""
    problems = []
    for rel, data in target.items():
        path = Path(root) / rel
        if not path.is_file():
            problems.append(f'applied file missing: {rel}')
        elif path.read_bytes() != data:
            problems.append(f'applied bytes differ: {rel}')
    try:
        recorded = json.loads((Path(root) / 'MANIFEST.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        problems.append(f'manifest unreadable after apply: {type(exc).__name__}')
    else:
        if recorded != manifest:
            problems.append('manifest bookkeeping differs from the applied package')
    for rel, data in UPDATE_PROJECT_FILES.items():
        path = Path(root) / rel
        if not path.is_file() or path.read_bytes() != data:
            problems.append(f'project-owned file was overwritten: {rel}')
    return problems


# --------------------------------------------------------------------------- #
# Group B, second family - the safe update engine
# --------------------------------------------------------------------------- #
def _clean_update(ctx, name='project', **kwargs):
    root = _update_project(ctx.sandbox, name=name, **kwargs)
    target = dict(UPDATE_V16)
    manifest = _update_manifest(target)
    payload = _update_zip(target, manifest)
    source = _UpdateSource(_update_release('v1.6', payload), payload)
    return root, target, manifest, source


def scenario_b5(ctx):
    """Positive control: a clean older install upgrades and passes a post-apply check."""
    update = _update()
    root, target, manifest, source = _clean_update(ctx, git=True)
    before_project = {rel: (root / rel).read_bytes() for rel in UPDATE_PROJECT_FILES}
    result = _run_update(root, source)
    _require(result['result'] == 'APPLIED', f'the clean upgrade ended {result["result"]}')
    _require(update.report.validate_report(result) == [],
             f'the report is structurally invalid: {update.report.validate_report(result)}')
    checks = {item['check']: item['result'] for item in result['target']['verification']['checks']}
    _require(len(checks) == len(update.package.CHECK_ORDER) and set(checks.values()) == {'PASS'},
             f'not every package check passed: {checks}')
    _require(result['baseline']['installed_format_version'] == '1.5',
             result['baseline'])
    _require(result['target']['version'] == '1.6.0', result['target']['version'])
    _require(result['backup']['required'] and result['backup']['verified'],
             f'the backup is missing or unverified: {result["backup"]}')
    _require(result['rollback']['result'] == 'NOT_NEEDED', result['rollback'])
    problems = _update_regression(root, target, manifest)
    _require(not problems, f'the post-apply regression failed: {problems}')
    for rel, data in before_project.items():
        _require((root / rel).read_bytes() == data, f'project-owned file changed: {rel}')
    commands = {item['command']: item['exit_code'] for item in result['commands']}
    fetch = next((code for name, code in commands.items() if 'releases/latest' in name), None)
    _require(fetch == 0, f'the release fetch is not recorded as a real call: {commands}')
    return [f'result: {result["result"]} {result["baseline"]["installed_format_version"]} -> '
            f'{result["target"]["version"]}',
            f'package checks: {len(checks)}/{len(update.package.CHECK_ORDER)} PASS',
            f'backup: verified={result["backup"]["verified"]} '
            f'rollback={result["rollback"]["result"]}',
            f'post-apply regression: {len(problems)} finding(s)',
            f'commands: {sorted(commands)}']


def scenario_b6(ctx):
    """Both sides changed one file: stop with zero writes and ask HD-10."""
    update = _update()
    root, _target, _manifest, source = _clean_update(ctx)
    _write_text(root, 'AGENTS.md', '# local edit that conflicts with upstream\n')
    before = _snapshot(root)
    result = _run_update(root, source)
    _require(result['result'] == 'STOPPED_CONFLICT', result['result'])
    _require(_snapshot(root) == before, 'a stopped conflict still changed the project')
    item = next(entry for entry in result['classification']['files']
                if entry['path'] == 'AGENTS.md')
    _require(item['class'] == 'THREE_WAY_CONFLICT' and item['decision'] == 'CONFLICT_REPORTED',
             item)
    decisions = {entry['decision_id'] for entry in result['human_decisions']}
    _require(decisions == {'HD-10'}, decisions)
    _require(update.report.validate_report(result) == [], update.report.validate_report(result))
    return [f'result: {result["result"]}',
            f'AGENTS.md: {item["class"]} / {item["decision"]}',
            f'human decisions: {sorted(decisions)}',
            'project snapshot unchanged']


def scenario_b7(ctx):
    """A package whose manifest is structurally invalid is refused before any write."""
    update = _update()
    root = _update_project(ctx.sandbox)
    target = dict(UPDATE_V16)
    manifest = _update_manifest(target)
    manifest['schema_version'] = 2
    payload = _update_zip(target, manifest)
    source = _UpdateSource(_update_release('v1.6', payload), payload)
    before = _snapshot(root)
    result = _run_update(root, source)
    checks = {item['check']: item['result'] for item in result['target']['verification']['checks']}
    _require(result['result'] == 'STOPPED_UNSAFE_PACKAGE', result['result'])
    _require(checks.get('MANIFEST_SCHEMA') == 'FAIL', checks)
    _require('NOT_RUN' in checks.values(), f'the later checks were not marked NOT_RUN: {checks}')
    _require(_snapshot(root) == before, 'an unsafe package still wrote to the project')
    _require(update.report.validate_report(result) == [], update.report.validate_report(result))
    return [f'result: {result["result"]}',
            f'MANIFEST_SCHEMA: {checks.get("MANIFEST_SCHEMA")}',
            f'checks after the failure: {sorted(set(checks.values()))}',
            'project snapshot unchanged']


def scenario_b8(ctx):
    """A traversal member is refused by the verifier and never extracted."""
    update = _update()
    root = _update_project(ctx.sandbox)
    target = dict(UPDATE_V16)
    manifest = _update_manifest(target)
    payload = _update_zip(target, manifest,
                          raw_members=((f'{UPDATE_TOP}/../escape.txt', b'evil\n', 0o644),))
    source = _UpdateSource(_update_release('v1.6', payload), payload)
    before = _snapshot(root)
    result = _run_update(root, source)
    checks = {item['check']: item['result'] for item in result['target']['verification']['checks']}
    _require(result['result'] == 'STOPPED_UNSAFE_PACKAGE', result['result'])
    _require(checks.get('MEMBER_PATHS') == 'FAIL', checks)
    _require(_snapshot(root) == before, 'the traversal package changed the project')
    _require(not (ctx.sandbox / 'escape.txt').exists(), 'a traversal member escaped the archive')
    _require(not (ctx.sandbox / 'project' / 'escape.txt').exists(), 'traversal wrote into the project')
    return [f'result: {result["result"]}',
            f'MEMBER_PATHS: {checks.get("MEMBER_PATHS")}',
            'escape.txt: not created outside or inside the project',
            'project snapshot unchanged']


def scenario_b9(ctx):
    """A local file the kit never shipped is kept and reported, not overwritten."""
    update = _update()
    root, target, manifest, source = _clean_update(ctx)
    _write_text(root, 'scripts/local-tool.py', '# local tool the kit never shipped\n')
    before = (root / 'scripts/local-tool.py').read_bytes()
    result = _run_update(root, source)
    _require(result['result'] == 'APPLIED', result['result'])
    item = next(entry for entry in result['classification']['files']
                if entry['path'] == 'scripts/local-tool.py')
    _require(item['class'] == 'UNKNOWN_FILE' and item['decision'] == 'KEPT_LOCAL', item)
    _require((root / 'scripts/local-tool.py').read_bytes() == before,
             'the unknown local file was overwritten')
    _require(update.report.validate_report(result) == [], update.report.validate_report(result))
    return [f'result: {result["result"]} with one local file outside the manifests',
            f'scripts/local-tool.py: {item["class"]} / {item["decision"]}',
            'local bytes unchanged']


def scenario_b10(ctx):
    """A path upstream no longer ships is kept; deletion needs HD-09."""
    update = _update()
    target = {path: data for path, data in UPDATE_V16.items()
              if path != 'templates/task-packet.md'}
    root = _update_project(ctx.sandbox)
    manifest = _update_manifest(target)
    payload = _update_zip(target, manifest)
    source = _UpdateSource(_update_release('v1.6', payload), payload)
    before = _snapshot(root)
    result = _run_update(root, source)
    item = next(entry for entry in result['classification']['files']
                if entry['path'] == 'templates/task-packet.md')
    decisions = {entry['decision_id'] for entry in result['human_decisions']}
    _require(result['result'] == 'STOPPED_CONFLICT', result['result'])
    _require(item['class'] == 'UPSTREAM_REMOVAL' and item['decision'] == 'KEPT_LOCAL', item)
    _require((root / 'templates/task-packet.md').is_file(), 'the removed upstream file was deleted')
    _require('HD-09' in decisions, decisions)
    _require(_snapshot(root) == before, 'the removal conflict changed the project')
    return [f'result: {result["result"]}',
            f'templates/task-packet.md: {item["class"]} / {item["decision"]}',
            f'human decisions: {sorted(decisions)}',
            'file kept and snapshot unchanged']


def scenario_b11(ctx):
    """An old install with no baseline manifest is a manual migration, not a guess."""
    update = _update()
    root = _update_project(ctx.sandbox, manifest=False)
    target = dict(UPDATE_V16)
    manifest = _update_manifest(target)
    payload = _update_zip(target, manifest)
    source = _UpdateSource(_update_release('v1.6', payload), payload)
    before = _snapshot(root)
    result = _run_update(root, source)
    _require(result['result'] == 'STOPPED_INVALID_BASELINE', result['result'])
    _require(result['baseline']['baseline_status'] == 'MISSING',
             result['baseline']['baseline_status'])
    _require('inventory' in result['baseline']['notes'], result['baseline']['notes'])
    _require(source.fetch_count == 0 and source.download_count == 0,
             'a missing baseline still reached the network')
    _require(_snapshot(root) == before, 'a missing baseline still wrote to the project')
    _require(update.report.validate_report(result) == [], update.report.validate_report(result))
    return [f'result: {result["result"]} baseline={result["baseline"]["baseline_status"]}',
            f'read-only inventory note: {result["baseline"]["notes"][:80]}...',
            'release fetch / download calls: 0',
            'project snapshot unchanged']


def scenario_b12(ctx):
    """Uncommitted work is isolated or the run stops; it is never stashed away."""
    update = _update()
    root, target, _manifest, source = _clean_update(ctx, git=True)
    _write_text(root, 'docs/PROJECT.md', '# uncommitted local edit\n')
    head = _git(root, 'rev-parse', 'HEAD').stdout.strip()
    before = _snapshot(root)
    result = _run_update(root, source)
    _require(result['result'] == 'APPLIED', result['result'])
    _require(result['workspace']['action'] == 'ISOLATED_BRANCH',
             result['workspace']['action'])
    _require('docs/PROJECT.md' in result['workspace']['dirty_paths'],
             result['workspace']['dirty_paths'])
    _require(_snapshot(root) == before,
             'the original working tree changed while the update was isolated')
    worktree = root / result['operation']['worktree']
    _require(worktree.is_dir(), f'the isolation worktree is missing: {worktree}')
    _require((worktree / 'AGENTS.md').read_bytes() == target['AGENTS.md'],
             'the isolated worktree did not receive the update')
    _require(_git(root, 'stash', 'list').stdout.strip() == '',
             'the engine used git stash on uncommitted work')
    _require(result['head_sha_before'] == head, result['head_sha_before'])

    blocked = _run_update(root, source, isolation=False)
    _require(blocked['result'] == 'STOPPED_DIRTY_WORKTREE', blocked['result'])
    _require(blocked['workspace']['action'] == 'STOPPED_DIRTY_WORKTREE',
             blocked['workspace']['action'])
    _require('HD-12' in {entry['decision_id'] for entry in blocked['human_decisions']},
             blocked['human_decisions'])
    _require(blocked['head_sha_after'] == head, blocked['head_sha_after'])
    _require(update.report.validate_report(blocked) == [],
             update.report.validate_report(blocked))
    return [f'isolated: {result["result"]} action={result["workspace"]["action"]} '
            f'worktree={result["operation"]["worktree"]}',
            'original dirty file untouched; git stash list empty',
            f'without isolation: {blocked["result"]} action={blocked["workspace"]["action"]}',
            f'HD-12 raised: {[e["decision_id"] for e in blocked["human_decisions"]]}']


def scenario_b13(ctx):
    """A write failure half-way through the update rolls back to the exact baseline."""
    update = _update()
    root, _target, _manifest, source = _clean_update(ctx)
    before = _snapshot(root)
    result = _run_update(root, source, fault=_UpdateFault(fail_after_writes=2))
    _require(result['result'] == 'ROLLED_BACK', result['result'])
    _require(result['rollback']['attempted'] and result['rollback']['result'] == 'SUCCEEDED',
             result['rollback'])
    _require(result['backup']['verified'], result['backup'])
    _require(_snapshot(root) == before, 'the rollback did not restore the exact baseline')
    checks = {item['name']: item['status'] for item in result['post_apply_checks']}
    _require(checks.get('rollback_verification') == 'PASS', checks)
    _require(update.report.validate_report(result) == [], update.report.validate_report(result))
    return [f'result: {result["result"]}',
            f'rollback: attempted={result["rollback"]["attempted"]} '
            f'result={result["rollback"]["result"]}',
            f'post-apply checks: {checks}',
            'project snapshot equals the exact baseline']


def scenario_b14(ctx):
    """A rollback that fails verification is BLOCKED with HD-11, never reported clean."""
    update = _update()
    root, _target, _manifest, source = _clean_update(ctx)
    fault = _UpdateFault(fail_after_writes=2, corrupt_after_restore='AGENTS.md')
    result = _run_update(root, source, fault=fault)
    _require(result['result'] == 'BLOCKED', result['result'])
    _require(result['rollback']['result'] == 'FAILED', result['rollback'])
    _require('rollback verification failed' in (result['rollback']['evidence'] or ''),
             result['rollback']['evidence'])
    _require('HD-11' in {entry['decision_id'] for entry in result['human_decisions']},
             result['human_decisions'])
    checks = {item['name']: item['status'] for item in result['post_apply_checks']}
    _require(checks.get('rollback_verification') == 'FAIL', checks)
    _require(update.report.validate_report(result) == [], update.report.validate_report(result))
    _require(fault.writes >= 2, f'only {fault.writes} write(s) were attempted')
    return [f'result: {result["result"]}',
            f'rollback: {result["rollback"]["result"]}',
            f'evidence: {result["rollback"]["evidence"]}',
            f'HD-11 raised; post-apply checks: {checks}']
