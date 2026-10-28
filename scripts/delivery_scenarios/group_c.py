"""Group C - cross-module closeout scenarios (C1-C20).

Rule wiring, ownership, event barriers, shared state, locking, rollback,
staged failure, architecture-contract alignment and dependency direction.
Every fixture is synthetic and lives in the scenario sandbox.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import shutil
import sys
import unittest
from pathlib import Path

from .common import (
    HEAD_SHA,
    SCRIPTS,
    ScenarioFailed,
    ToolError,
    _expect_clean,
    _expect_finding,
    _expect_refused,
    _require,
    _write_text,
)


# --- cross-module fixtures --------------------------------------------------
def _run_turn(*, wire_rule=True, enforce_ownership=True, barrier=True,
              actor='player-1', owner='player-1'):
    """A three-step turn: ownership -> rule -> commit, with switchable defects."""
    events, version = [], 0

    def emit(kind, **extra):
        events.append({'type': kind, 'version': version, **extra})

    if enforce_ownership and actor != owner:
        emit('action_rejected', actor=actor, reason='ownership')
        return events
    if not barrier:
        version += 1
        emit('state_changed', unit='unit-1')
    if wire_rule:
        emit('action_resolved', actor=actor, unit='unit-1')
    if barrier:
        version += 1
        emit('state_committed', unit='unit-1')
        emit('state_changed', unit='unit-1')
    return events


def _check_turn(events, *, actor, owner):
    """Invariants of the frozen contract, checked independently of the emitter."""
    violations, kinds = [], [event['type'] for event in events]
    if 'action_resolved' not in kinds:
        violations.append('frozen rule not wired: action_resolved never emitted')
    elif actor != owner:
        violations.append(f'ownership gap: actor {actor} resolved a unit owned by {owner}')
    if 'state_changed' in kinds:
        for index, kind in enumerate(kinds):
            if kind == 'state_changed' and 'state_committed' not in kinds[:index + 1]:
                violations.append('event barrier missing: state_changed emitted before the commit barrier')
                break
    versions = [event['version'] for event in events]
    if versions != sorted(versions):
        violations.append('event versions go backwards')
    return violations


# --- shared-state / recovery fixtures --------------------------------------
# These model the shapes a delivery task has to get right when it touches shared
# mutable state: one read-modify-write body with several call sites, a lock that
# lives next to the data but outside the rolled-back subtree, a snapshot-based
# rollback, a staged multi-file commit and a directory scan that must not hide a
# read error. Everything is synthetic and confined to the scenario sandbox.
LOCK_NAME = '.mutation.lock'
SHARED_MANIFEST = 'data/manifest.json'


def _tree_digest(root):
    """Every file below `root` as relative posix path -> sha256 of its bytes."""
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*')) if path.is_file()}


def _snapshot_tree(base):
    """Byte-for-byte copy of a tree, so a rollback can be compared exactly."""
    return {path.relative_to(base).as_posix(): path.read_bytes()
            for path in sorted(base.rglob('*')) if path.is_file()}


def _restore_tree(base, snapshot):
    """Restore a tree exactly: rewrite every snapshot file, delete everything else.

    Deleting the extra files is the part a "copy the files I remember" rollback
    forgets; leaving them behind is what this fixture exists to catch.
    """
    for path in sorted(base.rglob('*'), key=lambda item: len(item.parts), reverse=True):
        if path.is_file() and path.relative_to(base).as_posix() not in snapshot:
            path.unlink()
    for path in sorted(base.rglob('*'), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for rel, payload in snapshot.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _residue(base, snapshot):
    """Files present now that the snapshot does not know about."""
    return sorted(rel for rel in _tree_digest(base) if rel not in snapshot)


def _read_manifest(root):
    return json.loads((root / SHARED_MANIFEST).read_text(encoding='utf-8'))


def _write_manifest(root, payload):
    path = root / SHARED_MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding='utf-8')


def _bump_manifest(root, actor, pause=None):
    """One read-modify-write cycle; `pause` widens the window a lock must close."""
    payload = _read_manifest(root)
    if pause is not None:
        pause()
    payload['count'] += 1
    payload['writers'].append(actor)
    _write_manifest(root, payload)


class _MutationLock:
    """A lock file that refuses a second holder: that refusal *is* mutual exclusion."""

    def __init__(self, root, holder):
        self.path = root / LOCK_NAME
        self.holder = holder

    def __enter__(self):
        if self.path.exists():
            raise RuntimeError(f'LOCK_HELD: {self.path.name} is already held by '
                               f'{self.path.read_text(encoding="utf-8")}')
        self.path.write_text(self.holder, encoding='utf-8')
        return self

    def __exit__(self, *exc):
        if self.path.is_file() and self.path.read_text(encoding='utf-8') == self.holder:
            self.path.unlink()
        return False


def _bump_guarded(root, actor):
    """The guarded call site: the read-modify-write runs inside the lock."""
    with _MutationLock(root, actor):
        _bump_manifest(root, actor)


RMW_HELPER = '_bump_manifest'
SHARED_STATE_SOURCE = '''
def read_manifest(path):
    return json.loads(path.read_text())


def write_manifest(path, payload):
    path.write_text(json.dumps(payload, sort_keys=True))


def _bump_manifest(root, actor):
    payload = read_manifest(root)
    payload["count"] += 1
    write_manifest(root, payload)


def update_import(root):
    with mutation_lock(root):
        _bump_manifest(root, "import")


def update_autosave(root):
    _bump_manifest(root, "autosave")


def update_manual(root):
    with mutation_lock(root):
        _bump_manifest(root, "manual")
'''


def _call_site_findings(source, helper=RMW_HELPER):
    """Every function performing the shared-state cycle, and whether it holds the lock.

    This is a deliberately small AST check over a synthetic module, not a general
    call-site finder: it sees one helper name in one file. Whether a real project
    listed *all* of its call sites stays a reviewer question.
    """
    findings = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef):
            continue
        calls = [call for call in ast.walk(node)
                 if isinstance(call, ast.Call) and getattr(call.func, 'id', '') == helper]
        if not calls:
            continue
        guarded = any(isinstance(inner, ast.With)
                      and 'lock' in ast.unparse(inner.items[0].context_expr).lower()
                      for inner in ast.walk(node))
        findings.append({'call_site': node.name, 'guarded': guarded})
    findings.sort(key=lambda item: item['call_site'])
    return findings


COMMIT_STAGES = ('write-a', 'write-b', 'write-c')


def _stage_report(root, fail_at=2, claim_complete=False):
    """Apply a multi-file change stage by stage and report what really happened.

    A failure after stage 1 leaves stage 1 on disk. The honest report says so,
    names the failed stage and the backup, and does not claim a rollback that was
    never performed.
    """
    data, backup = root / 'data', root / 'backup'
    data.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=True)
    completed, failed = [], None
    for index, stage in enumerate(COMMIT_STAGES, 1):
        if index == fail_at:
            failed = stage
            break
        target = data / f'{stage}.txt'
        target.write_text(stage, encoding='utf-8')
        shutil.copy2(target, backup / target.name)
        completed.append(stage)
    return {
        'completed_stages': completed,
        'failed_stage': failed,
        'side_effects': sorted(path.name for path in data.iterdir()),
        'backup': 'backup/',
        'rollback': 'COMPLETE' if claim_complete else 'NONE',
    }


def _scan_entries(directory, swallow=False):
    """Read every directory entry; `swallow` hides an unreadable one behind success."""
    processed, errors, skipped = [], [], []
    paths = sorted(directory.iterdir())
    for path in paths:
        try:
            processed.append({'name': path.name, 'text': path.read_text(encoding='utf-8')})
        except OSError as exc:
            if swallow:
                skipped.append(path.name)
                continue
            errors.append(f'unreadable entry: {path.name} ({type(exc).__name__})')
    failed = bool(errors)
    return {'status': 'FAILED' if failed else 'OK', 'processed': len(processed),
            'total': len(paths), 'errors': errors, 'skipped': skipped}


# --- architecture-contract fixtures (adapter isolation, shared service) -----
# The six rules in docs/architecture/agentmgr-contract-rules.md are exercised on
# the *real* checker: a synthetic tree is written to the sandbox, the optional
# Architecture Contract (frozen by #47, implemented by #48) is asked to judge it,
# and the JSON report is the evidence. The semantic rules (shared application
# service, mock versus real evidence, terminal states, capability claims) cannot
# be proven by static analysis, so they run as small contract tests here and are
# labelled as such. Nothing read or written outside the scenario sandbox.
ARCH_PACKAGE = 'agentorch_architecture'
ARCH_EXPECTED_MODULES = ('adapter-hermes', 'application', 'domain',
                         'entrypoint-api', 'entrypoint-mcp')
SYNTHETIC_TOP_PACKAGE = 'src'
TERMINAL_STATES = ('OK', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'UNKNOWN')
EVIDENCE_KINDS = ('MOCK', 'REAL_RUNTIME')
CAPABILITY_EVIDENCE_KINDS = ('PROBE', 'STARTUP_OK', 'DESIGN_DOC')
PROBE_TIMESTAMP = '2026-10-26T00:00:00Z'

AGM_FILES = {
    'src/domain/rules.py':
        'def decide(request):\n'
        '    return {"amount": request["amount"] * 2}\n',
    'src/app/service.py':
        'from src.domain import rules\n'
        '\n'
        '\n'
        'def plan(request):\n'
        '    return rules.decide(request)\n',
    'src/adapters/hermes/api.py':
        'from src.domain import rules\n'
        '\n'
        '\n'
        'class HermesAdapter:\n'
        '    def decide(self, request):\n'
        '        return rules.decide(request)\n',
    'src/adapters/hermes/runtime/session.py':
        'FRAMEWORK_SESSION = object()\n',
    'src/entrypoints/api/handler.py':
        'from src.app import service\n'
        '\n'
        '\n'
        'def handle(request):\n'
        '    return service.plan(request)\n',
    'src/entrypoints/mcp/handler.py':
        'from src.app import service\n'
        '\n'
        '\n'
        'def handle(request):\n'
        '    return service.plan(request)\n',
}

# The defect: the application service reaches into the adapter's private runtime.
SERVICE_IMPORTS_PRIVATE = (
    'from src.adapters.hermes.runtime import session\n'
    'from src.domain import rules\n'
    '\n'
    '\n'
    'def plan(request):\n'
    '    return rules.decide(request)\n')

# The defect, semantic form: the transport re-implements the rule inline. It has
# no import edge at all, so a dependency check cannot see it.
MCP_INLINE_DECISION = (
    'def handle(request):\n'
    '    return {"amount": request["amount"] * 2 + 1}\n')

# The defect, structural form: the same fork written as a direct domain call,
# which the frozen FORBIDDEN_EDGE rule can block.
MCP_IMPORTS_DOMAIN = (
    'from src.domain import rules\n'
    '\n'
    '\n'
    'def handle(request):\n'
    '    return rules.decide(request)\n')

AGM_MODULES = [
    {'module_id': 'domain', 'paths': ['src/domain/'],
     'responsibilities': ['domain decisions and owned state'],
     'public_interfaces': [{'name': 'src.domain.rules', 'kind': 'python_module',
                            'stability': 'stable'}]},
    {'module_id': 'application', 'paths': ['src/app/'],
     'responsibilities': ['single application service shared by every transport'],
     'public_interfaces': [{'name': 'src.app.service', 'kind': 'python_module',
                            'stability': 'stable'}]},
    {'module_id': 'adapter-hermes', 'paths': ['src/adapters/hermes/'],
     'responsibilities': ['runtime adapter that hides its framework internals'],
     'public_interfaces': [{'name': 'src.adapters.hermes.api', 'kind': 'python_module',
                            'stability': 'additive_only'}],
     'private_paths': ['src/adapters/hermes/runtime/']},
    {'module_id': 'entrypoint-api', 'paths': ['src/entrypoints/api/'],
     'responsibilities': ['HTTP transport: protocol translation only'],
     'public_interfaces': [{'name': 'plan:http', 'kind': 'http_api', 'stability': 'stable'}]},
    {'module_id': 'entrypoint-mcp', 'paths': ['src/entrypoints/mcp/'],
     'responsibilities': ['event transport: protocol translation only'],
     'public_interfaces': [{'name': 'plan:event', 'kind': 'event', 'stability': 'stable'}]},
]

AGM_RULES = [
    {'rule_id': 'ARCH-AGM-NO-CYCLE', 'kind': 'NO_CYCLE', 'severity': 'ERROR',
     'enabled': True, 'description': 'modules must not form dependency cycles'},
    {'rule_id': 'ARCH-AGM-ENTRY-SHARED', 'kind': 'FORBIDDEN_EDGE', 'severity': 'ERROR',
     'enabled': True,
     'description': 'transport entry points call the shared application service, never '
                    'the domain or a concrete adapter'},
    {'rule_id': 'ARCH-AGM-ADAPTER-PRIVATE', 'kind': 'PRIVATE_ACCESS', 'severity': 'ERROR',
     'enabled': True,
     'description': 'core modules never import an adapter private runtime path'},
]

AGM_BOUNDARIES = [
    {'boundary_id': 'BND-DOMAIN', 'kind': 'domain', 'members': ['domain']},
    {'boundary_id': 'BND-APP', 'kind': 'application', 'members': ['application']},
    {'boundary_id': 'BND-ADAPTER', 'kind': 'adapter', 'members': ['adapter-hermes']},
    {'boundary_id': 'BND-TRANSPORT', 'kind': 'layer',
     'members': ['entrypoint-api', 'entrypoint-mcp']},
]

AGM_INVARIANTS = [
    {'invariant_id': 'INV-AGM-ENTRY-EDGE',
     'statement': 'no transport entry point reaches the domain or a concrete adapter '
                  'directly; every request goes through the application service',
     'checked_by': 'STATIC_RULE', 'severity': 'ERROR',
     'rule_ids': ['ARCH-AGM-ENTRY-SHARED']},
    {'invariant_id': 'INV-AGM-ADAPTER-ISOLATION',
     'statement': 'the core modules never import an adapter private runtime path',
     'checked_by': 'STATIC_RULE', 'severity': 'ERROR',
     'rule_ids': ['ARCH-AGM-ADAPTER-PRIVATE']},
    {'invariant_id': 'INV-AGM-SHARED-DECISION',
     'statement': 'both transports return the same decision for the same request',
     'checked_by': 'CONTRACT_TEST', 'severity': 'ERROR',
     'notes': 'the checker cannot run the project test suite; the shared contract test '
              'result has to be attached as its own evidence'},
]


def _agm_contract_data():
    return {
        'schema_version': '1.0',
        'contract_version': '1.0.0',
        'project_name': 'agentmgr-alignment-fixture',
        'enforcement_mode': 'ENFORCE',
        'modules': AGM_MODULES,
        'dependencies': {
            'allowed': [
                {'from': 'entrypoint-api', 'to': 'application',
                 'reason': 'the HTTP transport calls the shared application service'},
                {'from': 'entrypoint-mcp', 'to': 'application',
                 'reason': 'the event transport calls the shared application service'},
                {'from': 'application', 'to': 'domain',
                 'reason': 'the application service orchestrates domain rules'},
                {'from': 'adapter-hermes', 'to': 'domain',
                 'reason': 'the adapter implements the domain port'},
            ],
            'forbidden': [
                {'from': 'entrypoint-api', 'to': 'domain',
                 'reason': 'an entry point must not re-implement a business rule',
                 'rule_id': 'ARCH-AGM-ENTRY-SHARED'},
                {'from': 'entrypoint-mcp', 'to': 'domain',
                 'reason': 'an entry point must not re-implement a business rule',
                 'rule_id': 'ARCH-AGM-ENTRY-SHARED'},
                {'from': 'entrypoint-api', 'to': 'adapter-hermes',
                 'reason': 'an entry point must not bind a concrete adapter',
                 'rule_id': 'ARCH-AGM-ENTRY-SHARED'},
                {'from': 'entrypoint-mcp', 'to': 'adapter-hermes',
                 'reason': 'an entry point must not bind a concrete adapter',
                 'rule_id': 'ARCH-AGM-ENTRY-SHARED'},
            ],
        },
        'boundaries': AGM_BOUNDARIES,
        'invariants': AGM_INVARIANTS,
        'rules': AGM_RULES,
        'baseline': {'frozen_at': '2026-01-01T00:00:00Z', 'frozen_by': 'fixture'},
    }


def _agm_tree(root, *, core_imports_private=False, mcp_bypass=None):
    """Write the synthetic architecture tree; `mcp_bypass` is None / inline / import."""
    files = dict(AGM_FILES)
    if core_imports_private:
        files['src/app/service.py'] = SERVICE_IMPORTS_PRIVATE
    if mcp_bypass == 'inline':
        files['src/entrypoints/mcp/handler.py'] = MCP_INLINE_DECISION
    elif mcp_bypass == 'import':
        files['src/entrypoints/mcp/handler.py'] = MCP_IMPORTS_DOMAIN
    for relative, text in files.items():
        _write_text(root, relative, text)
    return files


def _write_agm_contract(root, data=None):
    path = root / 'docs' / 'architecture' / 'contract.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data if data is not None else _agm_contract_data(),
                               indent=2) + '\n', encoding='utf-8')
    return path


def _load_architecture():
    """Import scripts/architecture as a package (once per process)."""
    if ARCH_PACKAGE in sys.modules:
        return sys.modules[ARCH_PACKAGE]
    base = SCRIPTS / 'architecture'
    init = base / '__init__.py'
    if not init.is_file():
        raise ToolError(f'architecture package not found at {base}')
    spec = importlib.util.spec_from_file_location(
        ARCH_PACKAGE, init, submodule_search_locations=[str(base)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[ARCH_PACKAGE] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a missing checker is a tool error
        sys.modules.pop(ARCH_PACKAGE, None)
        raise ToolError(f'architecture: cannot import {base}: {exc}') from exc
    return module


def _arch_modules():
    _load_architecture()
    return tuple(importlib.import_module(f'{ARCH_PACKAGE}.{name}')
                 for name in ('contract', 'rules', 'report'))


def _arch_check(root):
    """Run the real checker on a synthetic tree and return its JSON report."""
    contract_module, rules_module, report_module = _arch_modules()
    loaded = contract_module.load(root)
    executed = None
    if loaded['contract'] is not None:
        # Python fixtures only: the Rust adapter is neither exercised nor claimed.
        executed = rules_module.run(loaded['contract'], root, adapters_enabled=('python',))
    return report_module.build_report(root, loaded, executed, HEAD_SHA, 'explicit', ('python',))


def _load_synthetic(root, names):
    """Import synthetic modules from the sandbox (plain files, namespace packages)."""
    root = str(root)
    sys.path.insert(0, root)
    importlib.invalidate_caches()
    try:
        modules = [importlib.import_module(name) for name in names]
    except Exception as exc:  # noqa: BLE001 - a broken fixture is a scenario failure
        raise ScenarioFailed(f'synthetic module import failed: {exc}') from None
    finally:
        for name in [key for key in sys.modules
                     if key == SYNTHETIC_TOP_PACKAGE
                     or key.startswith(SYNTHETIC_TOP_PACKAGE + '.')]:
            sys.modules.pop(name, None)
        try:
            sys.path.remove(root)
        except ValueError:
            pass
    return modules


def _transport_behaviour(api_plan, mcp_plan, requests):
    """Shared contract test: the same request must produce the same decision."""
    differences = []
    for request in requests:
        api, mcp = api_plan(request), mcp_plan(request)
        if api != mcp:
            differences.append({'request': request, 'api': api, 'mcp': mcp})
    return {'requests': len(requests), 'differences': differences, 'agree': not differences}


def _adapter_contract_run(evidence_kind, *, failing=None):
    """Run the shared adapter contract suite against a synthetic implementation."""
    implementation = {name: (lambda: 'PASS') for name in ('create', 'cancel', 'status')}
    if failing:
        implementation[failing] = lambda: 'FAIL'
    checks = {name: implementation[name]() for name in implementation}
    return {'evidence_kind': evidence_kind, 'checks': checks,
            'all_pass': all(value == 'PASS' for value in checks.values())}


def _verification_status(run):
    """What the run establishes: a mock run cannot establish real behaviour."""
    if run['evidence_kind'] == 'REAL_RUNTIME' and run['all_pass']:
        return 'PASS'
    return 'UNKNOWN'


def _claim_findings(run, claimed):
    """The evidence label must not claim more than the run supports."""
    if claimed not in EVIDENCE_KINDS:
        return [f'unknown evidence kind {claimed!r}']
    findings = []
    if not run['all_pass']:
        findings.append('the shared adapter contract suite did not pass; '
                        'nothing may be claimed from it')
    if claimed == 'REAL_RUNTIME' and run['evidence_kind'] != 'REAL_RUNTIME':
        findings.append(f'claim exceeds evidence: {run["evidence_kind"]} results reported as '
                        'REAL_RUNTIME')
    return findings


class _Terminal(RuntimeError):
    code = 'DISPATCH_ERROR'


class _Failed(_Terminal):
    code = 'DISPATCH_FAILED'


class _Cancelled(_Terminal):
    code = 'DISPATCH_CANCELLED'


class _TimedOut(_Terminal):
    code = 'DISPATCH_TIMED_OUT'


class _Unknown(_Terminal):
    code = 'DISPATCH_UNKNOWN'


_TERMINAL_ERRORS = {'FAILED': _Failed, 'CANCELLED': _Cancelled,
                    'TIMED_OUT': _TimedOut, 'UNKNOWN': _Unknown}


def _dispatch_outcome(outcome, *, collapse=False):
    """Result record for a dispatch terminal state; `collapse` is the defect."""
    if outcome == 'OK':
        return {'outcome': 'OK', 'success': True, 'code': None}
    if collapse:
        # The defect: one generic error covers failure, cancellation, timeout and
        # "we do not know", and "unknown" is rewritten as a failure.
        return {'outcome': 'FAILED', 'success': False, 'code': 'DISPATCH_ERROR'}
    return {'outcome': outcome, 'success': False, 'code': _TERMINAL_ERRORS[outcome].code}


def _terminal_findings(results):
    """Terminal-state contract: distinct codes, never upgraded to success."""
    findings = []
    codes = [result['code'] for result in results if result['code']]
    expected = len(TERMINAL_STATES) - 1  # every non-success state has its own code
    if len(set(codes)) < expected:
        findings.append(f'{expected} non-success terminal states collapse into '
                        f'{len(set(codes))} code(s): {", ".join(sorted(set(codes)))}')
    missing = [outcome for outcome in TERMINAL_STATES
               if outcome not in {result['outcome'] for result in results}]
    if missing:
        findings.append('undistinguishable terminal state(s): ' + ', '.join(missing))
    upgraded = [result['outcome'] for result in results
                if result['outcome'] != 'OK' and result['success']]
    if upgraded:
        findings.append('non-success terminal reported as success: ' + ', '.join(upgraded))
    return findings


def _capability_record(capability, status, evidence, scope):
    return {'capability': capability, 'status': status, 'evidence': list(evidence),
            'scope': scope}


def _capability_findings(record):
    """A capability claim is PASS only when it is bound to evidence and a scope."""
    if record['status'] != 'PASS':
        return []  # a non-PASS claim is never silently upgraded to a pass
    findings = []
    evidence = record['evidence']
    if not evidence:
        findings.append(f'PASS without evidence: {record["capability"]} has no probe record')
    for item in evidence:
        if item.get('kind') not in CAPABILITY_EVIDENCE_KINDS:
            findings.append(f'unknown evidence kind {item.get("kind")!r}')
        elif item.get('kind') != 'PROBE':
            findings.append(f'PASS based on {item.get("kind")}: that is not evidence of '
                            f'{record["capability"]}')
        elif not all(item.get(key) for key in ('probe_id', 'probed_at', 'environment')):
            findings.append('probe evidence is missing probe_id / probed_at / environment')
        elif item.get('capability') != record['capability']:
            findings.append(f'probe covered {item.get("capability")!r}, the claim is '
                            f'{record["capability"]!r}')
    if not record['scope'] or record['scope'] == ['*']:
        findings.append('PASS without a specific scope: the claim does not say what was '
                        'verified')
    return findings


SYNTHETIC_TEST_MODULE = '''
import unittest

RAN = []


class SyntheticTests(unittest.TestCase):
    def test_first_passes(self):
        RAN.append('first')

    def test_second_raises(self):
        RAN.append('second')
        raise ZeroDivisionError('synthetic non-assertion failure')

    def test_third_passes(self):
        RAN.append('third')
'''


# --------------------------------------------------------------------------- #
# Group C - cross-module closeout
# --------------------------------------------------------------------------- #
def scenario_c1(ctx):
    """Positive control: the frozen rule is wired and the event chain closes."""
    events = _run_turn()
    _expect_clean(_check_turn(events, actor='player-1', owner='player-1'), 'wired turn')
    return [f'turn events: {[event["type"] for event in events]}',
            'invariants: rule wired, ownership honoured, commit barrier first']


def scenario_c2(ctx):
    """A frozen rule that is never called leaves no event, so the chain is incomplete."""
    events = _run_turn(wire_rule=False)
    finding = _expect_finding(_check_turn(events, actor='player-1', owner='player-1'),
                              'frozen rule not wired', 'unwired rule')
    return [f'turn events: {[event["type"] for event in events]}', f'gate fired: {finding}']


def scenario_c3(ctx):
    """Ownership check missing: another actor resolves a unit it does not own."""
    events = _run_turn(actor='player-2', owner='player-1', enforce_ownership=False)
    finding = _expect_finding(_check_turn(events, actor='player-2', owner='player-1'),
                              'ownership gap', 'ownership check')
    return [f'gate fired: {finding}']


def scenario_c4(ctx):
    """Event barrier missing: the change event is emitted before the commit."""
    events = _run_turn(barrier=False)
    finding = _expect_finding(_check_turn(events, actor='player-1', owner='player-1'),
                              'event barrier missing', 'barrier')
    return [f'turn events: {[event["type"] for event in events]}', f'gate fired: {finding}']


def scenario_c5(ctx):
    """A non-assertion exception is recorded and the suite keeps running."""
    namespace = {'__name__': 'synthetic_delivery_flow'}
    exec(compile(SYNTHETIC_TEST_MODULE, '<synthetic-tests>', 'exec'), namespace)  # noqa: S102
    suite = unittest.TestLoader().loadTestsFromTestCase(namespace['SyntheticTests'])
    result = unittest.TestResult()
    suite.run(result)
    _require(result.testsRun == 3, f'expected 3 tests to run, got {result.testsRun}')
    _require(len(result.errors) == 1,
             f'expected exactly one non-assertion error, got {len(result.errors)}')
    _require(len(result.failures) == 0, 'a non-assertion exception was recorded as a failure')
    _require('ZeroDivisionError' in result.errors[0][1], 'the traceback was not preserved')
    _require(namespace['RAN'] == ['first', 'second', 'third'],
             f'the suite stopped early: ran {namespace["RAN"]}')
    return [f'testsRun={result.testsRun}, errors={len(result.errors)}, failures=0',
            'traceback preserved: ZeroDivisionError',
            f'later test still executed: {namespace["RAN"]}']


# --------------------------------------------------------------------------- #
# Group C, second family - shared state, locking and recovery
# --------------------------------------------------------------------------- #
def scenario_c6(ctx):
    """Positive control: guarded call sites, exact rollback, intact lock, honest scan."""
    root = ctx.sandbox / 'delivery'
    _write_text(root, 'data/a.txt', 'alpha')
    _write_text(root, 'data/nested/b.txt', 'bravo')
    _write_manifest(root, {'count': 0, 'writers': []})
    baseline = _snapshot_tree(root / 'data')
    baseline_digest = _tree_digest(root / 'data')

    _bump_guarded(root, 'import')
    _bump_guarded(root, 'autosave')
    manifest = _read_manifest(root)
    _require(manifest['count'] == 2 and manifest['writers'] == ['import', 'autosave'],
             f'the guarded call sites did not both land: {manifest}')

    # A change that fails half-way is rolled back to the exact baseline tree.
    _write_text(root, 'data/a.txt', 'alpha-changed')
    _write_text(root, 'data/extra.txt', 'delta')
    _restore_tree(root / 'data', baseline)
    tree_matches = _tree_digest(root / 'data') == baseline_digest
    _require(tree_matches, f'rollback left the tree different: {_residue(root / "data", baseline)}')

    # The lock is gone after the operation and can be taken again.
    lock_released = not (root / LOCK_NAME).exists()
    _require(lock_released, 'the lock file survived a completed operation')
    with _MutationLock(root, 'operation-3'):
        refused = _expect_refused(lambda: _MutationLock(root, 'operation-4').__enter__(),
                                  'LOCK_HELD', 'second holder while the lock is live')

    entries = ctx.sandbox / 'entries'
    _write_text(entries, 'ok-1.txt', 'one')
    _write_text(entries, 'ok-2.txt', 'two')
    scan = _scan_entries(entries)
    _require(scan['status'] == 'OK' and not scan['errors'] and not scan['skipped'],
             f'a clean directory scan reported a problem: {scan}')
    _require(scan['processed'] == scan['total'] == 2, f'the scan lost entries: {scan}')

    return ['guarded call sites: count=2, writers=[import, autosave]',
            'tree matches baseline: True (exact rollback, no residue)',
            'lock released: True',
            f'second holder refused while the lock is live: {refused}',
            f'entry scan: {scan["status"]} over {scan["total"]} entries, 0 findings']


def scenario_c7(ctx):
    """One unguarded call site is enough: the shared counter loses an update."""
    root = ctx.sandbox / 'shared'
    _write_manifest(root, {'count': 0, 'writers': []})

    def second_writer():
        _bump_guarded(root, 'autosave')

    # The defect: the autosave call site runs the cycle without taking the lock.
    _bump_manifest(root, 'import', pause=second_writer)
    lossy = _read_manifest(root)
    lost_update = lossy['count'] != 2 or 'autosave' not in lossy['writers']
    _require(lost_update, f'the unguarded interleaving was expected to lose an update: {lossy}')
    _require(lossy['count'] == 1, f'expected count=1 after the lost update, got {lossy["count"]}')

    # The same two call sites, both guarded: exclusion holds and both land.
    _write_manifest(root, {'count': 0, 'writers': []})
    with _MutationLock(root, 'import'):
        refused = _expect_refused(lambda: _MutationLock(root, 'autosave').__enter__(),
                                  'LOCK_HELD', 'second holder while the first call site runs')
        _bump_manifest(root, 'import')
    _bump_guarded(root, 'autosave')
    fixed = _read_manifest(root)
    _require(fixed['count'] == 2 and fixed['writers'] == ['import', 'autosave'],
             f'the guarded call sites did not preserve both updates: {fixed}')

    findings = _call_site_findings(SHARED_STATE_SOURCE)
    unguarded = [item['call_site'] for item in findings if not item['guarded']]
    _require(unguarded == ['update_autosave'],
             f'the call-site inventory did not isolate the defect: {findings}')
    _require(len(findings) == 3, f'expected three call sites to be inventoried, got {findings}')
    return [f'call sites inventoried: {[item["call_site"] for item in findings]}',
            'unguarded call site: update_autosave',
            f'second holder refused while the guarded site runs: {refused}',
            f'lost update: count={lossy["count"]}, writers={lossy["writers"]} '
            '(the autosave increment disappeared)',
            f'guarded rerun: count={fixed["count"]}, writers={fixed["writers"]}']


def scenario_c8(ctx):
    """Rolling back only the remembered files leaves the extra files behind."""
    root = ctx.sandbox / 'recovery'
    _write_text(root, 'data/a.txt', 'alpha')
    _write_text(root, 'data/nested/b.txt', 'bravo')
    baseline = _snapshot_tree(root / 'data')
    baseline_digest = _tree_digest(root / 'data')

    _write_text(root, 'data/a.txt', 'alpha-changed')
    _write_text(root, 'data/new.txt', 'delta')
    _write_text(root, 'data/nested/late.txt', 'echo')

    # The defect: restore the files the backup remembers, forget the new ones.
    for rel, payload in baseline.items():
        (root / 'data' / rel).write_bytes(payload)
    residue = _residue(root / 'data', baseline)
    _require(residue == ['nested/late.txt', 'new.txt'],
             f'expected two residue files, got {residue}')
    _require(_tree_digest(root / 'data') != baseline_digest,
             'the residue did not survive the "rollback", so the defect is not modelled')

    _restore_tree(root / 'data', baseline)
    restored = _tree_digest(root / 'data') == baseline_digest
    _require(restored, f'the exact rollback still differs: {_residue(root / "data", baseline)}')
    return [f'residue after memory-only rollback: {", ".join(residue)}',
            'the tree still differed from the baseline after that rollback',
            'tree matches baseline: True (after restoring the exact snapshot)']


def scenario_c9(ctx):
    """A business rollback that restores the whole directory deletes the lock file."""
    root = ctx.sandbox / 'locks'
    _write_text(root, 'data/a.txt', 'alpha')
    whole_root = _snapshot_tree(root)

    def acquire(holder):
        _MutationLock(root, holder).__enter__()

    def release(holder):
        lock = root / LOCK_NAME
        if lock.is_file() and lock.read_text(encoding='utf-8') == holder:
            lock.unlink()

    # The defect: the snapshot is taken before the lock exists and covers the
    # whole root, so restoring it removes the lock while the operation is live.
    acquire('operation-1')
    _write_text(root, 'data/a.txt', 'alpha-changed')
    _write_text(root, 'data/tmp.txt', 'written-before-the-failure')
    _restore_tree(root, whole_root)
    lock_deleted = not (root / LOCK_NAME).exists()
    _require(lock_deleted, 'the whole-root rollback kept the lock; the defect is not modelled')
    acquirer = _MutationLock(root, 'operation-2')
    acquirer.__enter__()
    second_writer_in = (root / LOCK_NAME).read_text(encoding='utf-8') == 'operation-2'
    _require(second_writer_in, 'the second writer did not take the orphaned lock')
    acquirer.__exit__(None, None, None)

    # The correct order: snapshot the data subtree only, so the lock survives.
    data_baseline = _snapshot_tree(root / 'data')
    data_digest = _tree_digest(root / 'data')
    acquire('operation-1')
    _write_text(root, 'data/tmp.txt', 'written-before-the-failure')
    _restore_tree(root / 'data', data_baseline)
    lock_survives = (root / LOCK_NAME).is_file()
    _require(lock_survives, 'a data-only rollback removed the lock')
    refused = _expect_refused(lambda: acquire('operation-2'), 'LOCK_HELD',
                              'second writer while the lock survives')
    release('operation-1')
    acquire('operation-2')
    release('operation-2')
    _require(_tree_digest(root / 'data') == data_digest,
             f'the data tree did not return to its baseline: '
             f'{_residue(root / "data", data_baseline)}')
    _require(_residue(root / 'data', data_baseline) == [],
             'the data rollback left residue behind')
    return ['lock file deleted by rollback: True',
            'second writer acquired the lock: True (mutual exclusion was lost)',
            f'correct rollback keeps it: lock survives a data rollback: {lock_survives}',
            f'second writer after the fix: {refused}',
            'lock released and reused: True; the data tree is back to its baseline']


def scenario_c10(ctx):
    """A failure after a partial commit must not be reported as a complete rollback."""
    root = ctx.sandbox / 'commit'
    report = _stage_report(root, fail_at=2)
    _require(report['failed_stage'] == 'write-b',
             f'the report does not name the failed stage: {report}')
    _require(report['completed_stages'] == ['write-a'],
             f'the report hides the completed stage: {report}')
    _require(report['side_effects'] == ['write-a.txt'],
             f'the report does not list the side effects: {report}')
    _require(report['rollback'] == 'NONE',
             f'the honest report claimed a rollback it did not perform: {report}')
    _require(report['backup'] == 'backup/',
             f'the report does not say where the backup is: {report}')

    # The defect: the same partial commit, reported as a complete rollback.
    lie_root = ctx.sandbox / 'commit-claim'
    lie = _stage_report(lie_root, fail_at=2, claim_complete=True)
    remaining = sorted(path.name for path in (lie_root / 'data').iterdir())
    contradicted = lie['rollback'] == 'COMPLETE' and bool(remaining)
    _require(contradicted,
             f'the false complete-rollback claim was not contradicted by the tree: {lie}')
    _require(remaining == ['write-a.txt'],
             f'expected exactly the stage-1 side effect, got {remaining}')
    return [f'failed stage: {report["failed_stage"]}',
            f'completed stages: {report["completed_stages"]}; '
            f'side effects: {report["side_effects"]}; rollback: {report["rollback"]}',
            f'backup location reported: {report["backup"]}',
            f'a "complete rollback" claim is contradicted by {len(remaining)} remaining '
            'file(s): True']


def scenario_c11(ctx):
    """An unreadable directory entry hidden behind a success report is a swallowed error."""
    entries = ctx.sandbox / 'scan'
    _write_text(entries, 'ok-1.txt', 'one')
    _write_text(entries, 'ok-2.txt', 'two')
    (entries / 'broken.json').mkdir(parents=True, exist_ok=True)  # cannot be read as a file

    lossy = _scan_entries(entries, swallow=True)
    _require(lossy['skipped'] == ['broken.json'],
             f'the swallowing scan did not skip the unreadable entry: {lossy}')
    _require(lossy['status'] == 'OK',
             f'the swallowing scan already reported a failure: {lossy}')
    contradiction = lossy['status'] == 'OK' and lossy['processed'] != lossy['total']
    _require(contradiction,
             f'the success report is not contradicted by the entry count: {lossy}')

    honest = _scan_entries(entries)
    _require(honest['status'] == 'FAILED',
             f'the corrected scan still reported success: {honest}')
    _require(any('broken.json' in error for error in honest['errors']),
             f'the corrected scan did not name the unreadable entry: {honest}')
    _require(honest['skipped'] == [], 'the corrected scan still swallowed the entry')
    return [f'skipped with a success report: {", ".join(lossy["skipped"])}',
            f'report claims OK while {lossy["total"] - lossy["processed"]} of '
            f'{lossy["total"]} entries were skipped: True',
            f'surface the error instead: {honest["status"]} naming broken.json '
            f'({len(honest["errors"])} error(s), 0 skipped)']


# --------------------------------------------------------------------------- #
# Group C, third family - architecture contract alignment
# --------------------------------------------------------------------------- #
def scenario_c12(ctx):
    """Positive control: one application service, honest labels, no over-claimed PASS."""
    root = ctx.sandbox / 'architecture'
    _agm_tree(root)
    _write_agm_contract(root)
    report = _arch_check(root)
    _require(report['enforcement_mode'] == 'ENFORCE', report['enforcement_mode'])
    _require(report['blocking'] is False, f'a clean tree blocked: {report["findings"]}')
    _require(report['status'] == 'PASS', f'expected PASS, got {report["status"]}: '
                                         f'{report["findings"]}')
    _require(report['counts']['ERROR'] == 0, report['counts'])
    _require(tuple(sorted(report['examined_modules'])) == ARCH_EXPECTED_MODULES,
             f'the report does not state its scope: {report["examined_modules"]}')
    _require(report['uncovered_modules'] == [], report['uncovered_modules'])
    coverage = {entry['invariant_id']: entry['status'] for entry in report['invariants']}
    _require(coverage.get('INV-AGM-ENTRY-EDGE') == 'PASS', coverage)
    _require(coverage.get('INV-AGM-ADAPTER-ISOLATION') == 'PASS', coverage)
    _require(coverage.get('INV-AGM-SHARED-DECISION') == 'UNKNOWN',
             f'a declared contract test was reported as coverage: {coverage}')

    api_handler, mcp_handler = _load_synthetic(
        root, ['src.entrypoints.api.handler', 'src.entrypoints.mcp.handler'])
    requests = [{'amount': 1}, {'amount': 7}]
    behaviour = _transport_behaviour(api_handler.handle, mcp_handler.handle, requests)
    _require(behaviour['agree'], f'the two transports disagree on a clean tree: {behaviour}')

    mock_run = _adapter_contract_run('MOCK')
    _require(_claim_findings(mock_run, 'MOCK') == [],
             f'a mock run may be claimed as mock: {_claim_findings(mock_run, "MOCK")}')
    _require(_verification_status(mock_run) == 'UNKNOWN',
             'a mock run was reported as real verification')

    fixed_terminals = [_dispatch_outcome(outcome) for outcome in TERMINAL_STATES]
    _require(_terminal_findings(fixed_terminals) == [], _terminal_findings(fixed_terminals))

    unprobed = _capability_record('execution_isolation', 'UNKNOWN', [], None)
    _require(_capability_findings(unprobed) == [],
             'an honest UNKNOWN capability claim was rejected')
    probed = _capability_record(
        'execution_isolation', 'PASS',
        [{'kind': 'PROBE', 'probe_id': 'probe-1', 'probed_at': PROBE_TIMESTAMP,
          'environment': 'synthetic fixture', 'capability': 'execution_isolation'}],
        ['single-process sandbox'])
    _require(_capability_findings(probed) == [],
             f'a probed capability claim with a scope was rejected: '
             f'{_capability_findings(probed)}')

    return [f'contract: mode=ENFORCE status={report["status"]} '
            f'ERROR={report["counts"]["ERROR"]} blocking={report["blocking"]}',
            f'scope: examined modules = {", ".join(report["examined_modules"])}; '
            f'uncovered = {report["uncovered_modules"] or "none"}',
            'invariant coverage: entry-edge PASS, adapter-isolation PASS, '
            'shared-decision UNKNOWN (a declared contract test is not coverage)',
            f'shared contract test: both transports agree on {behaviour["requests"]} requests',
            f'mock adapter contract run: claimed MOCK, verification '
            f'{_verification_status(mock_run)} (never real)',
            f'terminal states: {", ".join(sorted({r["code"] for r in fixed_terminals if r["code"]}))}',
            'capability claim: UNKNOWN accepted; probed + scoped PASS accepted']


def scenario_c13(ctx):
    """A core module importing an adapter private symbol is blocked statically."""
    root = ctx.sandbox / 'private-symbol'
    _agm_tree(root, core_imports_private=True)
    _write_agm_contract(root)
    report = _arch_check(root)
    _require(report['blocking'] is True,
             f'the checker did not block a core -> adapter private import: {report["findings"]}')
    hits = [finding for finding in report['findings']
            if finding['rule_id'] == 'ARCH-AGM-ADAPTER-PRIVATE' and finding['status'] == 'ERROR']
    _require(len(hits) == 1, f'expected one PRIVATE_ACCESS error, got {report["findings"]}')
    hit = hits[0]
    _require(hit['module_id'] == 'application' and hit['target_module'] == 'adapter-hermes',
             f'the violation is not locatable: {hit}')
    _require(hit['file'] == 'src/app/service.py' and hit['line'] == 1,
             f'the import location is missing: {hit}')
    _require('src/adapters/hermes/runtime' in hit['message'], hit['message'])
    coverage = {entry['invariant_id']: entry['status'] for entry in report['invariants']}
    _require(coverage.get('INV-AGM-ADAPTER-ISOLATION') == 'ERROR',
             f'the bound invariant did not follow its rule: {coverage}')
    return [f'blocking: {report["blocking"]}; counts: {report["counts"]}',
            f'PRIVATE_ACCESS error: {hit["module_id"]} -> {hit["target_module"]} '
            f'at {hit["file"]}:{hit["line"]}',
            f'invariant INV-AGM-ADAPTER-ISOLATION coverage: '
            f'{coverage.get("INV-AGM-ADAPTER-ISOLATION")}']


def scenario_c14(ctx):
    """Two transports with forked business rules: static edge or shared contract test."""
    requests = [{'amount': 1}, {'amount': 4}]

    # Semantic fork: an inline re-implementation has no import edge, so the
    # dependency checker is blind to it by construction (and must not pretend).
    semantic_root = ctx.sandbox / 'fork-inline'
    _agm_tree(semantic_root, mcp_bypass='inline')
    _write_agm_contract(semantic_root)
    semantic = _arch_check(semantic_root)
    _require(semantic['blocking'] is False,
             'the dependency checker claimed to catch an inline re-implementation')
    coverage = {entry['invariant_id']: entry['status'] for entry in semantic['invariants']}
    _require(coverage.get('INV-AGM-SHARED-DECISION') == 'UNKNOWN',
             f'a declared contract test was reported as coverage: {coverage}')
    api_handler, mcp_handler = _load_synthetic(
        semantic_root, ['src.entrypoints.api.handler', 'src.entrypoints.mcp.handler'])
    behaviour = _transport_behaviour(api_handler.handle, mcp_handler.handle, requests)
    _require(behaviour['agree'] is False,
             'the shared contract test did not catch the forked decision')
    _require(len(behaviour['differences']) == len(requests),
             f'only part of the fork was observed: {behaviour}')

    # Structural fork: the same divergence written as a direct domain call is
    # exactly what FORBIDDEN_EDGE can block.
    structural_root = ctx.sandbox / 'fork-import'
    _agm_tree(structural_root, mcp_bypass='import')
    _write_agm_contract(structural_root)
    structural = _arch_check(structural_root)
    _require(structural['blocking'] is True,
             f'the forbidden edge was not blocked: {structural["findings"]}')
    hits = [finding for finding in structural['findings']
            if finding['rule_id'] == 'ARCH-AGM-ENTRY-SHARED' and finding['status'] == 'ERROR']
    _require(len(hits) == 1, structural['findings'])
    _require(hits[0]['module_id'] == 'entrypoint-mcp'
             and hits[0]['target_module'] == 'domain', hits[0])
    return [f'inline fork: static blocking={semantic["blocking"]} (a dependency check '
            'cannot see it), shared contract test agreement=False over '
            f'{len(behaviour["differences"])}/{behaviour["requests"]} requests',
            f'inline fork: INV-AGM-SHARED-DECISION coverage='
            f'{coverage.get("INV-AGM-SHARED-DECISION")} (declaration is not coverage)',
            f'structural fork: blocking={structural["blocking"]}, '
            f'{hits[0]["module_id"]} -> {hits[0]["target_module"]} at '
            f'{hits[0]["file"]}:{hits[0]["line"]}']


def scenario_c15(ctx):
    """A mock adapter success must never be reported as real runtime verification."""
    mock = _adapter_contract_run('MOCK')
    _require(mock['all_pass'], f'the mock run should pass its shared contract: {mock}')
    over_claim = _claim_findings(mock, 'REAL_RUNTIME')
    _require(any('exceeds evidence' in finding for finding in over_claim),
             f'the mock -> real over-claim was accepted: {over_claim}')
    _require(_verification_status(mock) == 'UNKNOWN',
             'a mock run was upgraded to real verification')

    honest = _claim_findings(mock, 'MOCK')
    _require(honest == [], f'a mock run labelled mock was rejected: {honest}')

    real = _adapter_contract_run('REAL_RUNTIME')
    _require(_claim_findings(real, 'REAL_RUNTIME') == [],
             'a real run labelled real was rejected')
    _require(_verification_status(real) == 'PASS', _verification_status(real))

    broken = _adapter_contract_run('REAL_RUNTIME', failing='cancel')
    _require(_claim_findings(broken, 'REAL_RUNTIME') != [],
             'a failing contract suite still supported a claim')
    return [f'mock run: all_pass={mock["all_pass"]}, claimed MOCK -> no finding; '
            f'verification status {_verification_status(mock)}',
            'mock run claimed REAL_RUNTIME -> ' + over_claim[0],
            f'real run: verification status {_verification_status(real)}',
            'failing contract suite: ' + _claim_findings(broken, 'REAL_RUNTIME')[0]]


def scenario_c16(ctx):
    """Cancellation, timeout, failure and "unknown" must stay distinguishable."""
    collapsed = [_dispatch_outcome(outcome, collapse=True) for outcome in TERMINAL_STATES]
    findings = _terminal_findings(collapsed)
    _require(bool(findings), 'the collapsed terminal states were not reported')
    _require(any('collapse' in finding for finding in findings), findings)
    _require(all(not record['success'] for record in collapsed[1:]),
             'a non-success terminal was recorded as success')
    collapsed_codes = sorted({record['code'] for record in collapsed if record['code']})
    _require(len(collapsed_codes) == 1, collapsed_codes)

    distinct = [_dispatch_outcome(outcome) for outcome in TERMINAL_STATES]
    _require(_terminal_findings(distinct) == [],
             f'the distinct terminal states were rejected: {_terminal_findings(distinct)}')
    codes = sorted({record['code'] for record in distinct if record['code']})
    _require(len(codes) == len(TERMINAL_STATES) - 1, codes)
    _require(all(not record['success'] for record in distinct[1:]),
             'a distinct non-success terminal was recorded as success')
    return [f'collapsed: {len(TERMINAL_STATES) - 1} non-success states -> 1 code '
            f'({", ".join(collapsed_codes)}); reported: {findings[0]}',
            f'distinct: {", ".join(codes)}',
            'no non-success terminal was reported as success in either variant']


def scenario_c17(ctx):
    """A capability may only be PASS when its evidence and scope are attached."""
    unbacked = _capability_record('execution_isolation', 'PASS', [], None)
    unbacked_findings = _capability_findings(unbacked)
    _require(any('without evidence' in finding for finding in unbacked_findings),
             f'PASS without evidence was accepted: {unbacked_findings}')

    startup_only = _capability_record(
        'execution_isolation', 'PASS',
        [{'kind': 'STARTUP_OK', 'probe_id': 'probe-1', 'probed_at': PROBE_TIMESTAMP,
          'environment': 'synthetic fixture', 'capability': 'execution_isolation'}],
        ['single-process sandbox'])
    startup_findings = _capability_findings(startup_only)
    _require(any('not evidence of' in finding for finding in startup_findings),
             f'"startup succeeded" was accepted as isolation evidence: {startup_findings}')

    wildcard = _capability_record(
        'execution_isolation', 'PASS',
        [{'kind': 'PROBE', 'probe_id': 'probe-1', 'probed_at': PROBE_TIMESTAMP,
          'environment': 'synthetic fixture', 'capability': 'execution_isolation'}],
        ['*'])
    wildcard_findings = _capability_findings(wildcard)
    _require(any('scope' in finding for finding in wildcard_findings),
             f'PASS with a wildcard scope was accepted: {wildcard_findings}')

    wrong_probe = _capability_record(
        'cancellation', 'PASS',
        [{'kind': 'PROBE', 'probe_id': 'probe-1', 'probed_at': PROBE_TIMESTAMP,
          'environment': 'synthetic fixture', 'capability': 'execution_isolation'}],
        ['single-process sandbox'])
    wrong_findings = _capability_findings(wrong_probe)
    _require(any('probe covered' in finding for finding in wrong_findings),
             f'a probe for a different capability was accepted: {wrong_findings}')

    honest = _capability_record('execution_isolation', 'UNKNOWN', [], None)
    _require(_capability_findings(honest) == [],
             'an honest UNKNOWN claim was rejected')
    return [f'PASS without evidence -> {len(unbacked_findings)} finding(s): '
            f'{unbacked_findings[0]}',
            f'"startup succeeded" as isolation evidence -> {startup_findings[0]}',
            f'wildcard scope -> {wildcard_findings[0]}',
            f'probe for another capability -> {wrong_findings[0]}',
            'honest UNKNOWN claim: 0 findings (a non-PASS claim is not upgraded)']


def scenario_c18(ctx):
    """Without a contract the same violations stay diagnostic: no inferred rule fires."""
    root = ctx.sandbox / 'unconfigured'
    files = _agm_tree(root, core_imports_private=True, mcp_bypass='import')
    report = _arch_check(root)
    _require(report['contract_path'] is None, report['contract_path'])
    _require(report['enforcement_mode'] == 'OFF', report['enforcement_mode'])
    _require(report['diagnostic_only'] is True, report['diagnostic_only'])
    _require(report['blocking'] is False, 'an unconfigured project was blocked')
    _require(report['counts']['ERROR'] == 0, report['counts'])
    _require(report['counts']['WARNING'] == 0, report['counts'])
    _require(report['status'] == 'NOT_RUN', report['status'])
    _require(report['invariants'] == [], report['invariants'])
    _require(report['examined_modules'] == [], report['examined_modules'])
    inventory = report.get('inventory') or {}
    _require(inventory.get('python_files') == len(files),
             f'the read-only inventory is incomplete: {inventory}')

    # The only difference is the declaration: the same tree with a contract blocks.
    _write_agm_contract(root)
    declared = _arch_check(root)
    _require(declared['blocking'] is True,
             'the same tree was not blocked once the contract was declared')
    return [f'no contract: mode={report["enforcement_mode"]} status={report["status"]} '
            f'blocking={report["blocking"]} ERROR={report["counts"]["ERROR"]} '
            f'WARNING={report["counts"]["WARNING"]}',
            f'read-only inventory: {inventory.get("python_files")} python files, '
            f'0 rules executed',
            f'the same tree with a declared contract: blocking={declared["blocking"]} '
            f'ERROR={declared["counts"]["ERROR"]}']


# --- minimal architecture contract (dependency edges / cycles) ---------------
SYNTH_CONTRACT = {
    'schema_version': '1.0',
    'contract_version': '1.0.0',
    'project_name': 'synthetic-dependency-fixture',
    'enforcement_mode': 'ENFORCE',
    'modules': [
        {'module_id': 'core', 'paths': ['src/core/'],
         'responsibilities': ['domain decisions owned by the core module'],
         'public_interfaces': [{'name': 'src.core.rules', 'kind': 'python_module',
                                'stability': 'stable'}]},
        {'module_id': 'api', 'paths': ['src/api/'],
         'responsibilities': ['transport translation that delegates to the core'],
         'public_interfaces': [{'name': 'plan:http', 'kind': 'http_api',
                                'stability': 'stable'}]},
    ],
    'dependencies': {
        'allowed': [{'from': 'api', 'to': 'core',
                     'reason': 'the transport delegates every decision to the core module'}],
        'forbidden': [{'from': 'core', 'to': 'api',
                       'reason': 'the core must not depend on a transport entry point',
                       'rule_id': 'ARCH-SYN-NO-CORE-API'}],
    },
    'rules': [
        {'rule_id': 'ARCH-SYN-NO-CYCLE', 'kind': 'NO_CYCLE', 'severity': 'ERROR',
         'enabled': True, 'description': 'declared modules must not form dependency cycles'},
        {'rule_id': 'ARCH-SYN-NO-CORE-API', 'kind': 'FORBIDDEN_EDGE', 'severity': 'ERROR',
         'enabled': True,
         'description': 'the core module must never import the transport module'},
    ],
    'baseline': {'frozen_at': '2026-01-01T00:00:00Z', 'frozen_by': 'fixture'},
}


def _write_contract(root, data):
    path = Path(root) / 'docs' / 'architecture' / 'contract.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    return path


# --------------------------------------------------------------------------- #
# Group C, fourth family - dependency direction and cycles
# --------------------------------------------------------------------------- #
def scenario_c19(ctx):
    """Positive control: a declared allowed dependency passes without a false block."""
    root = ctx.sandbox / 'allowed-edge'
    _write_text(root, 'src/core/rules.py',
                'def decide(request):\n'
                '    return {"amount": request["amount"] * 2}\n')
    _write_text(root, 'src/api/handler.py',
                'from src.core import rules\n'
                '\n'
                '\n'
                'def handle(request):\n'
                '    return rules.decide(request)\n')
    _write_contract(root, SYNTH_CONTRACT)
    report = _arch_check(root)
    _require(report['status'] == 'PASS', f'expected PASS, got {report["status"]}: '
                                         f'{report["findings"]} {report.get("contract_errors")}')
    _require(report['blocking'] is False, report['findings'])
    _require(report['counts']['ERROR'] == 0, report['counts'])
    _contract, rules_module, _report = _arch_modules()
    edges = {(edge['module_id'], edge['target_module'])
             for edge in rules_module.collect_edges(
                 root, SYNTH_CONTRACT['modules'], ('python',))['edges']}
    _require(('api', 'core') in edges,
             f'the allowed dependency was not observed: {sorted(edges)}')
    _require(('core', 'api') not in edges, f'the reverse edge exists in the fixture: {edges}')
    statuses = {entry['rule_id']: entry['status'] for entry in report['findings']}
    _require(statuses.get('ARCH-SYN-NO-CYCLE') == 'PASS', statuses)
    _require(statuses.get('ARCH-SYN-NO-CORE-API') == 'PASS', statuses)
    return [f'contract: ENFORCE, status={report["status"]}, '
            f'ERROR={report["counts"]["ERROR"]}',
            f'observed edges: {sorted(edges)}',
            f'rule statuses: {statuses}',
            'no false hard failure for a declared allowed edge']


def scenario_c20(ctx):
    """Two modules importing each other are blocked by NO_CYCLE, with the path named."""
    root = ctx.sandbox / 'cycle'
    _write_text(root, 'src/a/one.py',
                'from src.b import two\n'
                '\n'
                '\n'
                'def one():\n'
                '    return two.two()\n')
    _write_text(root, 'src/b/two.py',
                'from src.a import one\n'
                '\n'
                '\n'
                'def two():\n'
                '    return one.one()\n')
    contract = json.loads(json.dumps(SYNTH_CONTRACT))
    contract['modules'][0]['module_id'] = 'module-a'
    contract['modules'][0]['paths'] = ['src/a/']
    contract['modules'][1]['module_id'] = 'module-b'
    contract['modules'][1]['paths'] = ['src/b/']
    contract['dependencies'] = {
        'allowed': [], 'forbidden': []}
    contract['rules'] = [{
        'rule_id': 'ARCH-SYN-NO-CYCLE', 'kind': 'NO_CYCLE', 'severity': 'ERROR',
        'enabled': True, 'description': 'declared modules must not form dependency cycles'}]
    _write_contract(root, contract)
    report = _arch_check(root)
    _require(report['blocking'] is True,
             f'the cycle was not blocking: {report["findings"]}')
    hits = [finding for finding in report['findings']
            if finding['rule_id'] == 'ARCH-SYN-NO-CYCLE' and finding['status'] == 'ERROR']
    _require(len(hits) == 1, f'expected one cycle error, got {report["findings"]}')
    hit = hits[0]
    _require(sorted(hit['modules']) == ['module-a', 'module-b'], hit['modules'])
    _require('cycle' in hit['message'] and 'module-a -> module-b -> module-a' in hit['message'],
             hit['message'])
    _require(hit['file'] and hit['file'].endswith('.py'), f'the cycle is not located: {hit}')
    return [f'blocking: {report["blocking"]}; counts: {report["counts"]}',
            f'NO_CYCLE error: modules={hit["modules"]} at {hit["file"]}:{hit["line"]}',
            f'message: {hit["message"]}']
