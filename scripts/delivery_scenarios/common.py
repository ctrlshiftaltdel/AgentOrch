"""Shared fixtures, constants and helpers for the delivery scenarios.

The runner (`scripts/run_delivery_scenarios.py`) owns the registry, the
selector, the executor and the CLI. Every fixture that more than one group
needs lives here, together with the module-alias loaders that import the real
kit packages under private names. Nothing here reads or writes the repository:
fixtures are generated inside a throw-away sandbox.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'

HEAD_SHA = 'a' * 40
MERGE_SHA = 'b' * 40


class ToolError(RuntimeError):
    """The runner cannot start; this is not a scenario result."""


class ScenarioFailed(AssertionError):
    """The gate the scenario expected did not fire (or fired for the wrong reason)."""


# --------------------------------------------------------------------------- #
# Synthetic fixtures
# --------------------------------------------------------------------------- #
@dataclass
class Context:
    """Everything a scenario may touch: the two kit modules and a private sandbox."""

    vk: object
    orch: object
    sandbox: Path


@dataclass(frozen=True)
class Scenario:
    id: str
    group: str
    kind: str            # 'positive' control or 'negative' injected defect
    title: str
    precondition: str
    defect: str
    gate: str
    evidence: str
    cleanup: str
    fn: object           # Callable[[Context], list[str]]


@dataclass
class Result:
    scenario: Scenario
    status: str          # PASS / FAIL / ERROR
    evidence: list = field(default_factory=list)
    error: dict = field(default_factory=dict)


def _load_module(name, path):
    if not path.is_file():
        raise ToolError(f'{name}: module not found at {path}')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - surfaced as a tool error, not a scenario
        raise ToolError(f'{name}: cannot import {path}: {exc}') from exc
    return module


@contextlib.contextmanager
def _quiet():
    """Swallow the CLI chatter of orchestrator/init so the report stays readable."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def _require(condition, message):
    if not condition:
        raise ScenarioFailed(message)


def _expect_clean(errors, label):
    if errors:
        raise ScenarioFailed(f'{label}: expected no finding, got: ' + ' | '.join(errors))


def _expect_finding(errors, needle, label):
    hits = [error for error in errors if needle in error]
    if not hits:
        raise ScenarioFailed(f'{label}: expected a finding containing {needle!r}, '
                             f'got: ' + (' | '.join(errors) if errors else '(no finding at all)'))
    return hits[0]


def _expect_refused(call, needle, label):
    """Call something that must be refused and return the refusal message."""
    try:
        call()
    except ScenarioFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - the refusal type is asserted separately
        message = f'{type(exc).__name__}: {exc}'
        if needle not in message:
            raise ScenarioFailed(f'{label}: refused, but the reason never mentions '
                                 f'{needle!r}: {message}') from None
        return message
    raise ScenarioFailed(f'{label}: the call succeeded, but it must be refused')


# --- task packet fixtures ---------------------------------------------------
def _canonical(ctx):
    core = (ROOT / ctx.vk.CORE_DOC).read_text(encoding='utf-8')
    evidence, statuses = ctx.vk.canonical_evidence_fields(core)
    structured = ctx.vk.canonical_packet_fields(core)
    if not evidence or not statuses:
        raise ScenarioFailed(f'cannot read the evidence-item contract from {ctx.vk.CORE_DOC}')
    return evidence, statuses, structured


def _row(evidence_fields, *, ac, status='PASS', actual='2 checks passed', sha=HEAD_SHA,
         notes='(none)', method='`python -m unittest`', expected='the check passes',
         environment='synthetic fixture, stdlib only, local edition',
         ref='synthetic fixture: scenario evidence log'):
    cells = {
        'ac_id': ac, 'method': method, 'expected': expected, 'actual': actual,
        'status': status, 'environment': environment, 'commit_sha': sha,
        'evidence_ref': ref, 'notes': notes,
    }
    missing = [name for name in evidence_fields if name not in cells]
    _require(not missing, f'fixture builder is missing evidence fields: {missing}')
    return '  | ' + ' | '.join(cells[name] for name in evidence_fields) + ' |'


def _packet(ctx, acs, rows, head=HEAD_SHA):
    """Build a filled task packet; `rows` is a list of evidence-matrix lines."""
    evidence_fields, _, structured_fields = _canonical(ctx)
    lines = [
        '- **Task ID:** SYNTH-9001',
        '- **Title:** Synthetic governance task',
        '- **Goal / user-visible result:** the task is judged only by its own evidence',
        '- **Context:** synthetic fixture; no network, credentials or external model',
        '- **Allowed paths:** `src/example.py`',
        '- **Forbidden paths:** every other path in the repository',
        '- **Dependencies (must be VERIFIED):** none',
        '- **Acceptance criteria:**',
    ]
    for mark, ac_id, text in acs:
        lines.append(f'  - [{mark}] {ac_id}: {text}' if text else f'  - [{mark}] {ac_id}:')
    for name in structured_fields:
        lines.append(f'- **{name}:** N/A: covered by the acceptance criteria above')
    lines += [
        '- **Validation commands:** `python -m unittest`',
        '- **Baseline branch / commit:** `main` @ `1111111`',
        '- **Expected branch / Worktree:** `task/9001-synthetic` / `.worktrees/9001-synthetic`',
        f'- **Evidence head SHA:** {head}',
        '- **Evidence matrix:**',
        '  | ' + ' | '.join(evidence_fields) + ' |',
        '  |' + '---|' * len(evidence_fields),
    ]
    lines += list(rows)
    lines += [
        '- **Risks / human decisions:** none identified',
        '- **Delivery evidence:** command output, exit codes, head SHA',
        '- **Hard boundaries:** no self-approval, no merge, no VERIFIED claim',
    ]
    return '\n'.join(lines) + '\n'


def _packet_errors(ctx, text):
    evidence, statuses, structured = _canonical(ctx)
    return ctx.vk.validate_task_packet(text, evidence, statuses,
                                       label='synthetic packet', structured_fields=structured)


# --- orchestrator fixtures --------------------------------------------------
def _project_root(ctx, edition='local', config=None):
    """A throw-away project root with an explicit edition and an initialized store."""
    root = ctx.sandbox / f'project-{edition}'
    root.mkdir(parents=True, exist_ok=True)
    (root/'docs').mkdir(parents=True, exist_ok=True) or (root / 'docs/PROJECT.md').write_text(
        f'# Synthetic project\n\n- `edition`\uFF1A`{edition}`\n', encoding='utf-8')
    (root / '.orchestration').mkdir(exist_ok=True)
    payload = {'retry_cap': 3, 'local_dir': '.local'}
    payload.update(config or {})
    (root / '.orchestration' / 'config.json').write_text(
        json.dumps(payload, sort_keys=True), encoding='utf-8')
    with _quiet():
        ctx.orch.init(root)
    return root


def _add(ctx, root, task_id, paths, resources=None):
    payload = SimpleNamespace(id=task_id, title=task_id, description='synthetic fixture',
                              paths=list(paths), depends=[], acceptance=['synthetic criterion'],
                              resources=list(resources or []))
    with _quiet():
        ctx.orch.task_add(root, payload)


def _ready(ctx, root, task_id):
    with _quiet():
        ctx.orch.task_ready(root, task_id)


def _dispatch(ctx, root, task_id, holder='synthetic-host:1', resources=None):
    options = SimpleNamespace(holder=holder, resource=list(resources or []),
                              actor='scenario-runner', evidence='synthetic scenario')
    with _quiet():
        ctx.orch.task_dispatch(root, task_id, options)


def _status(ctx, root, task_id, dest, head_sha=None, merge_sha=None,
            note='synthetic status change with evidence'):
    args = SimpleNamespace(id=task_id, status=dest, head_sha=head_sha,
                           merge_sha=merge_sha, note=note)
    with _quiet():
        ctx.orch.task_status(root, args)


def _task_row(ctx, root, task_id, columns='status,head_sha,approved_sha,retries'):
    con = ctx.orch.connect(root)
    try:
        return con.execute(f'SELECT {columns} FROM tasks WHERE id=?', (task_id,)).fetchone()
    finally:
        con.close()


def _count(ctx, root, sql, params=()):
    con = ctx.orch.connect(root)
    try:
        return con.execute(sql, params).fetchone()[0]
    finally:
        con.close()


def _write_text(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')
# --------------------------------------------------------------------------- #
# Module aliases for the real kit packages
# --------------------------------------------------------------------------- #
# A, B and D all load a shipped package under a private alias. The cache
# keeps one import per alias, and the loaders live here so no group module
# has to import another one.
UPDATE_PACKAGE = 'agentorch_update_engine'
DRIFT_MODULE = 'agentorch_check_drift'
METRICS_MODULE = 'agentorch_report_metrics'

_MODULES = {}


def _package(alias, directory, names):
    """Import a shipped package under a private alias (never its bare name)."""
    if alias not in sys.modules:
        init = directory / '__init__.py'
        if not init.is_file():
            raise ToolError(f'{alias}: package not found at {directory}')
        spec = importlib.util.spec_from_file_location(
            alias, init, submodule_search_locations=[str(directory)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - surfaced as a tool error
            sys.modules.pop(alias, None)
            raise ToolError(f'{alias}: cannot import {directory}: {exc}') from exc
    key = (alias, tuple(names))
    if key not in _MODULES:
        _MODULES[key] = SimpleNamespace(**{
            name: importlib.import_module(f'{alias}.{name}') for name in names})
    return _MODULES[key]


def _update():
    return _package(UPDATE_PACKAGE, SCRIPTS / 'update_agentorch',
                    ('sources', 'versions', 'package', 'classify', 'workspace',
                     'report', 'engine'))


def _drift():
    if 'drift' not in _MODULES:
        _MODULES['drift'] = _load_module(DRIFT_MODULE, SCRIPTS / 'check_drift.py')
    return _MODULES['drift']


def _metrics():
    if 'metrics' not in _MODULES:
        _MODULES['metrics'] = _load_module(METRICS_MODULE, SCRIPTS / 'report_metrics.py')
    return _MODULES['metrics']
