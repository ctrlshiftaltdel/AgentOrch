"""Natural-language entry for "更新 AgentOrch" (Issue #52).

The entry is a thin *router and carrier adapter*:

1. route the user message with :func:`scripts.self_improvement.planning.route_request`
   -- the same deterministic table the self-improvement loop uses, so there is no
   second trigger vocabulary;
2. inspect the project (edition, installed Format / version, baseline manifest,
   workspace state) before any network or write;
3. call the #51 engine (``engine.run_update``) unchanged -- the entry never
   re-classifies files, relaxes verification or decides a rollback;
4. shape the *delivery* for the current edition:
   ``local`` prepares a task-database / branch delivery packet, ``github``
   prepares an Issue / Draft PR delivery packet. The entry itself writes neither
   carrier: no SQLite mutation and no ``gh`` call. Both shapes carry the same
   gate record (review required, no self-approval, no merge).

Command line::

    python3 -m scripts.update_agentorch.entry --project <dir> \
        --request "更新 AgentOrch" --mode PLAN

Exit codes add ``6`` ("the message did not start an update") to the engine's
codes in :data:`scripts.update_agentorch.engine.EXIT_CODES`. Only the Python
standard library is used.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import classify, cli, engine, report as report_module, sources, versions, workspace

try:  # repository checkout / package root on sys.path
    from scripts.self_improvement import planning
except ImportError:  # ``scripts/`` itself on sys.path
    from self_improvement import planning  # type: ignore[no-redef]

EXIT_NOT_UPDATE = 6

TRIGGER_EXAMPLES = (
    '更新 AgentOrch',
    '升级 AgentOrch',
    '把项目中的 AgentOrch 更新到最新版',
)
NON_TRIGGER_EXAMPLES = (
    '更新一下文档',
    '更新依赖',
    '更新这个 Issue',
    'AgentOrch 最新版本是多少',
)

UPDATE_CLARIFICATION = ('你是想更新 AgentOrch 套件本身，还是更新项目内容'
                        '（文档 / 依赖 / Issue）？')
SELF_IMPROVEMENT_MESSAGE = ('这条消息属于“进行自我完善”闭环，不属于更新引擎；'
                            '请按自我完善流程处理，不能在这里启动套件更新。')


class EntryError(ValueError):
    """Invalid project or request; the entry reports it instead of guessing."""


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #

def route(text):
    """Return the routing decision as a dict with the update-specific flags.

    ``starts_update`` is the only green light for this entry. It is deliberately
    narrower than ``route == UPDATE_AGENTORCH``: an ambiguous wording
    ("更新 AgentOrch 的文档") asks once instead of starting an update.
    """
    decision = planning.route_request(text)
    payload = decision.as_dict()
    payload['starts_update'] = bool(
        decision.route == planning.ROUTE_UPDATE_AGENTORCH
        and decision.execution_intent
        and not decision.needs_clarification)
    payload['starts_self_improvement'] = bool(decision.starts_workflow)
    if payload['starts_update']:
        payload['clarification_question'] = None
    elif decision.route == planning.ROUTE_UPDATE_AGENTORCH and decision.needs_clarification:
        payload['clarification_question'] = UPDATE_CLARIFICATION
    else:
        payload['clarification_question'] = None
    return payload


# --------------------------------------------------------------------------- #
# Environment inspection (read-only, before any network call)
# --------------------------------------------------------------------------- #

def _installed_layout():
    """``isolated`` when this entry ships inside ``<host>/agentorch/``.

    Mirrors the scheduler's location hint: it is only used to emit the right
    command shape, never as source authentication or an isolation proof.
    """
    kit = Path(__file__).resolve().parent.parent.parent
    return 'isolated' if kit.name == workspace.AGENTORCH_DIRNAME else 'flat'


def inspect_environment(project):
    """Read edition, installed Format / version, baseline and workspace state."""
    project = Path(project)
    if not project.is_dir():
        raise EntryError(f'project root does not exist or is not a directory: {project}')

    edition = None
    edition_error = None
    try:
        edition = cli.read_edition(project)
    except ValueError as exc:
        edition_error = f'edition could not be read: {exc}'

    baseline = engine._read_baseline(project)  # same reader the engine uses
    git = workspace.inspect_git(project, datetime.now(timezone.utc))

    return {
        'project': str(project.resolve()),
        'layout': _installed_layout(),
        'edition': edition,
        'edition_error': edition_error,
        'suite': baseline.suite if baseline.status == 'VALID' else None,
        'installed_version': (baseline.installed_version
                              if baseline.status == 'VALID' else None),
        'format_version': (baseline.format_version
                           if baseline.status == 'VALID' else None),
        'baseline_status': baseline.status,
        'baseline_notes': baseline.notes,
        'workspace': {
            'is_git_repo': git.is_repo,
            'head_sha': git.head_sha,
            'branch': git.branch,
            'dirty': git.dirty,
            'dirty_paths': list(git.dirty_paths),
            'isolated': git.isolated,
        },
    }


# --------------------------------------------------------------------------- #
# Delivery shape (edition-specific) and shared gates
# --------------------------------------------------------------------------- #

def _applied_and_kept(report):
    applied, kept = [], []
    for item in report.get('classification', {}).get('files', []):
        if item.get('decision') == 'APPLIED':
            applied.append(item['path'])
        elif item.get('class') in classify.CONFLICT_CLASSES:
            kept.append(item['path'])
    return sorted(applied), sorted(kept)


def _suggested_branch(report, edition):
    worktree = (report.get('operation') or {}).get('worktree')
    if worktree:
        name = Path(worktree).name
        if name:
            return f'task/update-agentorch-{name}'
    target = (report.get('target') or {}).get('version') or 'latest'
    return f'task/update-agentorch-{target.replace(".", "-")}-{edition}'


def gates(environment, report, mode):
    """The same gate record for both editions; only the carrier differs."""
    problems = report_module.validate_report(report)
    return {
        'edition': environment['edition'],
        'mode': mode,
        'engine_result': report.get('result'),
        'requires_review': True,
        'self_approval_allowed': False,
        'auto_merge': False,
        'conflicts': sorted(
            item['path'] for item in report.get('classification', {}).get('files', [])
            if item.get('class') in classify.CONFLICT_CLASSES),
        'human_decisions': [entry.get('decision_id')
                            for entry in report.get('human_decisions') or []],
        'evidence_head_sha': report.get('head_sha_after'),
        'report_valid': not problems,
        'report_contract_errors': problems,
    }


def _task_packet(environment, report, applied, kept, branch):
    target = (report.get('target') or {}).get('version')
    installed = environment.get('format_version') or '(unknown)'
    return {
        'task id': '(assigned by scripts/orchestrator.py at dispatch)',
        'title': f'Apply the verified AgentOrch update {installed} -> {target}',
        'goal / user-visible result': (
            'The project runs the verified upstream AgentOrch files from the official '
            'release while every project-owned file keeps its exact bytes.'),
        'context': (
            'Entry routed "更新 AgentOrch" to the #51 safe update engine. The engine '
            'verified the official release and classified every path; this packet only '
            'carries the conflict-free change set for review.'),
        'allowed paths': applied,
        'forbidden paths': [
            'every path not listed under allowed paths',
            'project-owned paths (docs/, .github/, .local/, .orchestration/, src/, release/)',
        ],
        'dependencies (must be VERIFIED)': [],
        'acceptance criteria': [
            'AC-1: every applied path matches the verified package bytes',
            'AC-2: every project-owned / unknown / conflicting path keeps its exact bytes',
            'AC-3: the project verification commands pass on the resulting branch',
        ],
        'validation commands': [
            'python scripts/validate_kit.py',
            'python -m unittest discover -s tests',
        ],
        'baseline branch / commit': environment['workspace'].get('branch') or '(detached)',
        'expected branch / worktree': branch,
        'evidence head sha': (report.get('head_sha_after')
                              or environment['workspace'].get('head_sha') or '(none)'),
        'evidence matrix': [],
        'risks / human decisions': [entry.get('decision_id')
                                    for entry in report.get('human_decisions') or []],
        'delivery evidence': 'engine report, applied / kept path list, pre/post hashes',
        'hard boundaries': 'no self-approval, no merge, no main-branch write',
        'kept paths': kept,
    }


def _local_next_steps(layout):
    """State-mutating commands for the local carrier, matching *layout*.

    The commands are copied by an operator from the directory each layout
    documents: the project root for a flat install, the host root for an
    isolated one. An isolated scheduler refuses a bare invocation, so its
    commands use the managed block's ``agentorch/scripts/orchestrator.py
    --layout isolated`` form; both layouts pass the real ``task add --id``
    flag so the sequence runs unchanged against the shipped CLI.
    """
    if layout == 'isolated':
        where = 'host root (the directory that contains agentorch/)'
        command = 'python agentorch/scripts/orchestrator.py --layout isolated'
    else:
        where = 'project root (the directory that contains scripts/)'
        command = 'python scripts/orchestrator.py'
    return [
        f'run these from the {where}',
        f'{command} init',
        f'{command} task add --id <TASK-ID> --title "<title>" '
        '--paths <allowed paths> --acceptance "<AC-1>" "<AC-2>"',
        f'{command} task ready <TASK-ID>',
        f'{command} task dispatch <TASK-ID>',
        'pass the packet and the engine report to the Execution Agent',
    ]


def _local_delivery(environment, report, mode):
    applied, kept = _applied_and_kept(report)
    branch = _suggested_branch(report, 'local')
    return {
        'carrier': 'local_task_database',
        'automation': 'manual',
        'writes': [],
        'note': ('The entry did not write the local task database. The Master registers '
                 'the packet and dispatches the task; the engine changes stay in the '
                 'branch / Worktree named below.'),
        'branch': branch,
        'worktree': (report.get('operation') or {}).get('worktree'),
        'task_packet': _task_packet(environment, report, applied, kept, branch),
        'next_steps': _local_next_steps(environment.get('layout') or _installed_layout()),
    }


def _issue_body(environment, report, applied, kept):
    target = (report.get('target') or {}).get('version')
    lines = [
        '## 目标', '',
        f'把项目中的 AgentOrch 从 `{environment.get("format_version") or "unknown"}` '
        f'更新到 `{target or "unknown"}`，只应用官方 Release 校验通过的无冲突变更。', '',
        '## 入口识别结果', '',
        f'- edition: `{environment.get("edition")}`',
        f'- installed Format: `{environment.get("format_version")}`',
        f'- baseline: `{environment.get("baseline_status")}`',
        f'- engine result: `{report.get("result")}`',
        f'- verification: `{(report.get("target") or {}).get("verification", {}).get("status")}`',
        f'- head SHA: `{report.get("head_sha_after")}`', '',
        '## 无冲突变更（待审查）', '',
    ]
    lines.extend(f'- `{path}`' for path in applied) if applied else lines.append('(none)')
    lines.extend(['', '## 保留 / 需人工决策的路径', ''])
    lines.extend(f'- `{path}`' for path in kept) if kept else lines.append('(none)')
    lines.extend(['', '## 人工决策', ''])
    decisions = report.get('human_decisions') or []
    lines.extend(f'- `{entry.get("decision_id")}`: {entry.get("question")}'
                 for entry in decisions) if decisions else lines.append('(none)')
    lines.extend([
        '', '## 边界', '',
        '- 本 Issue 由入口生成，不代表已批准、已合并或已验证。',
        '- 不覆盖项目自有配置 / 业务代码；冲突逐文件保留。',
        '- 证据矩阵与 G1–G6 判定仍由 `kit/orchestration.md` 与其 4.1 节定义。',
    ])
    return '\n'.join(lines) + '\n'


def _github_delivery(environment, report, mode):
    applied, kept = _applied_and_kept(report)
    branch = _suggested_branch(report, 'github')
    target = (report.get('target') or {}).get('version')
    return {
        'carrier': 'issue_and_draft_pr',
        'automation': 'manual',
        'writes': [],
        'note': ('The entry did not call gh and did not create anything. Create the Issue '
                 'and Draft PR from these payloads; the Draft PR must link the Issue and '
                 'stay unmerged until Master review.'),
        'issue': {
            'title': f'S5/update: apply verified AgentOrch {target or "latest"}',
            'body': _issue_body(environment, report, applied, kept),
        },
        'draft_pr': {
            'base': 'main',
            'head': branch,
            'title': f'task: apply verified AgentOrch {target or "latest"}',
            'body': ('Draft PR prepared by the "更新 AgentOrch" entry. It links the update '
                     'Issue, carries the engine report and does not merge itself.\n'),
        },
        'next_steps': [
            'gh issue create --title "<title>" --body-file <issue-body.md>',
            'gh pr create --draft --base main --head <branch> --title "<title>" '
            '--body-file <pr-body.md>',
            'link the PR back to the Issue and record the engine report as evidence',
        ],
    }


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #

def prepare_update(project, request, *, mode='PLAN', source=None, edition=None,
                   supported_formats=None, isolation=True, now=None):
    """Route one message, inspect the project, run the engine, shape the carrier.

    The function performs no carrier write: ``local`` returns a task packet and
    ``github`` returns Issue / Draft PR payloads. Use the returned ``status`` to
    decide whether to continue; ``NOT_UPDATE`` and ``NEEDS_CLARIFICATION`` mean
    no engine call happened.

    ``supported_formats`` is the trusted compatibility matrix handed to package
    verification. ``None`` means "use this updater's declared matrix"
    (:data:`scripts.update_agentorch.versions.DECLARED_SUPPORTED_FORMATS`), so the
    shipped entry can bridge a MINOR release that keeps the Format. Pass an
    explicit list / tuple (including an empty one) only to narrow or disable the
    bridge; do not derive it from the downloaded package.
    """
    if mode not in ('PLAN', 'APPLY'):
        raise EntryError(f'mode must be PLAN or APPLY, got {mode!r}')

    routing = route(request)
    if routing['route'] == planning.ROUTE_SELF_IMPROVEMENT:
        return {
            'status': 'NOT_UPDATE',
            'route': routing,
            'message': SELF_IMPROVEMENT_MESSAGE,
            'delivery': None,
            'gates': None,
            'engine_report': None,
            'environment': None,
        }
    if not routing['starts_update']:
        status = 'NEEDS_CLARIFICATION' if routing['needs_clarification'] else 'NOT_UPDATE'
        return {
            'status': status,
            'route': routing,
            'message': routing.get('clarification_question')
            or 'the message does not start an AgentOrch update',
            'delivery': None,
            'gates': None,
            'engine_report': None,
            'environment': None,
        }

    environment = inspect_environment(project)
    if environment['edition_error']:
        return {
            'status': 'STOPPED_INVALID_EDITION',
            'route': routing,
            'message': environment['edition_error'],
            'delivery': None,
            'gates': None,
            'engine_report': None,
            'environment': environment,
        }

    effective_edition = edition or environment['edition']
    if effective_edition not in ('local', 'github'):
        raise EntryError(f'edition must be local or github, got {effective_edition!r}')

    source = source if source is not None else sources.GitHubReleaseSource()
    matrix = (versions.DECLARED_SUPPORTED_FORMATS
              if supported_formats is None else supported_formats)
    report = engine.run_update(project, mode=mode, source=source,
                               edition=effective_edition, now=now,
                               supported_formats=matrix,
                               isolation=isolation)
    environment['edition'] = effective_edition

    gate_record = gates(environment, report, mode)
    if effective_edition == 'local':
        delivery = _local_delivery(environment, report, mode)
    else:
        delivery = _github_delivery(environment, report, mode)

    engine_result = report.get('result')
    status = 'PREPARED' if engine_result in ('PLANNED', 'APPLIED') else engine_result
    return {
        'status': status,
        'route': routing,
        'message': None,
        'delivery': delivery,
        'gates': gate_record,
        'engine_report': report,
        'environment': environment,
    }


_PUBLIC_STATUSES = frozenset({'PLANNED', 'APPLIED', 'NEW_PROJECT_MODE', 'BLOCKED',
                              'STOPPED_CONFLICT', 'STOPPED_UNSAFE_SOURCE',
                              'STOPPED_SCENARIO', 'ROLLED_BACK'})
_PUBLIC_FILE_NAMES = frozenset({'LICENSE.md', 'VERSION.md', 'README.md', 'MANIFEST.json',
                                '.gitignore', 'docs/orchestration.md', 'kit/orchestration.md'})


def _public_enum(value, choices, fallback='UNKNOWN'):
    return value if type(value) is str and value in choices else fallback


def _public_rollback(result):
    raw = result.get('rollback') if type(result) is dict else None
    if type(raw) is not dict:
        raw = {}
    state = _public_enum(raw.get('result'), {'NOT_NEEDED', 'SUCCEEDED', 'FAILED', 'NOT_RUN'})
    return {'result': state,
            'required': raw.get('required') if type(raw.get('required')) is bool else None,
            'attempted': raw.get('attempted') if type(raw.get('attempted')) is bool else None}


def _plan_payload(plan, result):
    """Public isolated-onboarding DTO; never serialize local plan or error text."""
    raw = result if type(result) is dict else {}
    status = _public_enum(raw.get('status'), _PUBLIC_STATUSES)
    mode = _public_enum(plan.mode, {'INSTALL', 'UPGRADE', 'MIGRATE'})
    action = _public_enum(plan.agents_action, {'create', 'extend', 'unchanged', 'replace'})
    summary = plan.managed_summary if type(plan.managed_summary) is dict else {}
    counts = {}
    for name in ('begin_count', 'end_count', 'hash_count'):
        value = summary.get(name)
        counts[name] = value if type(value) is int and 0 <= value <= 1000000 else None
    counts['action'] = action
    counts['conflict'] = status == 'STOPPED_CONFLICT' if status != 'UNKNOWN' else None
    counts['decision'] = ('REFUSED' if status in ('STOPPED_CONFLICT', 'BLOCKED',
                                                   'STOPPED_SCENARIO', 'STOPPED_UNSAFE_SOURCE')
                          else 'UNKNOWN' if status == 'UNKNOWN' else 'NOT_REFUSED')
    if status == 'STOPPED_CONFLICT':
        begin, end = counts['begin_count'], counts['end_count']
        counts['reason'] = ('MARKER_MISMATCH' if begin is not None and end is not None
                            and ((begin, end) not in ((0, 0), (1, 1)))
                            else 'CONFLICT_REQUIRES_LOCAL_REVIEW')
    else:
        counts['reason'] = 'NOT_DETERMINED'
    counts['host_text_included'] = False
    counts['conflict_type'] = _public_enum(summary.get('conflict_type'), {
        'NONE', 'OTHER_CONFLICT', 'MISSING_MARKER', 'DUPLICATE_MARKER',
        'REVERSED_MARKER', 'HASH_MARKER_MISMATCH', 'UNEXPECTED_BLOCK_CONTENT',
        'BODY_HASH_MISMATCH', 'NONCANONICAL_BODY'})
    raw_files = plan.files if type(plan.files) is dict else {}
    files = sorted(name for name in _PUBLIC_FILE_NAMES if name in raw_files)
    written = raw.get('written')
    removed = raw.get('removed')
    sha = getattr(plan.source_binding, 'asset_sha256', None)
    head = plan.worktree_head
    failure = raw.get('failure') if type(raw.get('failure')) is dict else {}
    failure_errno = failure.get('errno')
    return {
        'status': status, 'mode': mode,
        'scenario': _public_enum(plan.scenario, {'existing-project', 'new-project'}),
        'agents_action': action,
        'agents_created': plan.agents_created if type(plan.agents_created) is bool else None,
        'files': files, 'file_count': len(raw_files),
        'problems': ([] if status in ('PLANNED', 'APPLIED', 'NEW_PROJECT_MODE') else
                     ['Operation stopped or outcome is unknown; inspect locally before proceeding.']),
        'guidance': ('Do not overwrite host rules. Inspect the local managed markers and '
                     'preserve the existing files; resolve conflicts manually before retrying.'),
        'diff': counts,
        'written_count': (len(written) if status in ('PLANNED', 'APPLIED', 'NEW_PROJECT_MODE',
                                                     'STOPPED_CONFLICT', 'STOPPED_SCENARIO',
                                                     'STOPPED_UNSAFE_SOURCE', 'ROLLED_BACK')
                          and type(written) is list else
                          len(written) if status == 'BLOCKED' and type(written) is list and
                          (type(raw.get('rollback')) is dict and
                           raw['rollback'].get('required') is False and
                           raw['rollback'].get('attempted') is False and
                           raw['rollback'].get('result') == 'NOT_NEEDED')
                          else None),
        'completed_count': len(written) if type(written) is list and status != 'UNKNOWN' else None,
        'written_count_semantics': ('UNKNOWN_UNLESS_PREWRITE_VERIFIED' if status == 'BLOCKED'
                                    else 'UNKNOWN' if status == 'UNKNOWN' else 'REPORTED'),
        'removed_count': (len(removed) if status in ('PLANNED', 'APPLIED', 'NEW_PROJECT_MODE',
                                                     'STOPPED_CONFLICT', 'STOPPED_SCENARIO',
                                                     'STOPPED_UNSAFE_SOURCE', 'ROLLED_BACK')
                          and type(removed) is list else None),
        'backup_location': None,
        'backup_status': ('LOCAL_LOCATION_UNAVAILABLE' if raw.get('backup_location') is not None
                          else 'NOT_REPORTED'),
        'rollback': _public_rollback(raw),
        'failure': {'phase': _public_enum(failure.get('phase'), {'WRITE', 'FINALIZE'}),
                    'kind': _public_enum(failure.get('kind'), {'OSError', 'PermissionError',
                                         'FileNotFoundError', 'TransactionConflict', 'RuntimeError'}),
                    'errno': failure_errno if type(failure_errno) is int and
                             0 <= failure_errno <= 65535 else None},
        'exclusive_writer': _public_enum(plan.exclusive_writer, {'NOT_DECLARED', 'DECLARED_NOT_VERIFIED'}),
        'worktree_head': head if type(head) is str and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', head) else None,
        'source_asset_sha256': sha if type(sha) is str and re.fullmatch(r'[0-9a-f]{64}', sha) else None,
        'local_diagnostics': 'NOT_PUBLIC',
    }


def _onboarding_worktree_preflight(host_root, *, declared_exclusive, expected_head=None):
    """Read Git state; exclusivity is a caller declaration, not a verified lock."""
    blocked = {'status': 'BLOCKED', 'exclusive_writer': 'NOT_DECLARED'}
    if declared_exclusive is not True:
        return blocked
    blocked = {'status': 'BLOCKED', 'exclusive_writer': 'DECLARED_NOT_VERIFIED'}
    host = Path(host_root)
    if host.is_symlink() or not host.is_dir():
        return blocked
    host = host.resolve()
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}

    def git(*args):
        proc = subprocess.run(['git', '-C', str(host), *args], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              check=False, timeout=30)
        if proc.returncode:
            raise ValueError('Git inspection failed')
        return proc.stdout.decode('utf-8', errors='strict').strip()

    try:
        root = Path(git('rev-parse', '--show-toplevel')).resolve()
        common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        gitdir = Path(git('rev-parse', '--absolute-git-dir')).resolve()
        head = git('rev-parse', '--verify', 'HEAD^{commit}')
        branch = git('symbolic-ref', '--quiet', '--short', 'HEAD')
        dirty = git('status', '--porcelain=v1', '--untracked-files=all')
        if (root != host or gitdir.parent != common / 'worktrees' or not branch
                or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', head) or dirty
                or (expected_head is not None and expected_head != head)):
            return blocked
    except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError):
        return blocked
    return {'status': 'OBSERVED', 'head_sha': head, 'branch': branch,
            'exclusive_writer': 'DECLARED_NOT_VERIFIED'}


def _onboarding_host_observation(host_root):
    """Read a preflight observation, not an atomic lock or write authority."""
    host = Path(host_root).resolve()
    root_stat = host.stat()
    items = []
    for name in ('AGENTS.md', 'agentorch'):
        path = host / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            items.append((name, None))
            continue
        value = (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                 info.st_mtime_ns, info.st_ctime_ns)
        if name == 'AGENTS.md' and stat.S_ISREG(info.st_mode):
            value += (path.read_bytes(),)
        elif name == 'agentorch' and stat.S_ISDIR(info.st_mode):
            value += (tuple(sorted(item.name for item in path.iterdir())),)
        items.append((name, value))
    return ((root_stat.st_dev, root_stat.st_ino), tuple(items))


def prepare_isolated_install(host_root, *, source_root=None, scenario='isolated',
                             mode='APPLY', declared_exclusive=False,
                             expected_head=None):
    """Plan (and optionally apply) an isolated onboarding for an existing project.

    The plan is built only from bytes returned by the fixed official-release
    client and the ten frozen package checks. ``declared_exclusive`` is a caller
    precondition, not proof of isolation: Git worktree / HEAD is re-read before
    each write phase and the result is reported as ``DECLARED_NOT_VERIFIED``.
    This never calls ``gh``, never touches a real downstream project unless the
    caller passes its directory, and never merges or claims approval.
    """
    if mode not in ('PLAN', 'APPLY'):
        raise EntryError(f'mode must be PLAN or APPLY, got {mode!r}')
    verified = workspace.fetch_verified_onboarding_release()
    before = _onboarding_host_observation(host_root)
    # An explicitly requested source is only a consistency check, never the
    # source of planned kit bytes or an authorization to apply them.
    source_mismatch = False
    if source_root is not None:
        local_files, _, local_version, local_suite = workspace._read_source(Path(source_root))
        source_mismatch = (local_files != verified.files
                           or local_version != verified.format_version
                           or local_suite != verified.suite)
    plan = workspace.plan_isolated_onboarding(
        host_root, source_root=source_root or workspace.agentorch_root(),
        scenario=scenario, verified_source=verified)
    if plan.mode == 'UPGRADE' and plan.status == 'STOPPED_CONFLICT':
        # An upgrade that is not already a no-op needs an authenticated old
        # release. Fetch it through the same fixed client; a failure keeps the
        # fail-closed diagnostics and never invents a baseline.
        try:
            baseline = workspace.fetch_verified_onboarding_baseline(host_root)
        except workspace.OnboardingError:
            baseline = None
        if baseline is not None:
            plan = workspace.plan_isolated_onboarding(
                host_root, source_root=source_root or workspace.agentorch_root(),
                scenario=scenario, verified_source=verified,
                verified_baseline=baseline)
    elif plan.mode == 'MIGRATE' and plan.status == 'STOPPED_CONFLICT':
        # A flat old root migrates only from an official historical Release the
        # installer re-fetches and matches byte for byte. A failure keeps the
        # fail-closed diagnostics and never invents a baseline.
        try:
            baseline = workspace.fetch_verified_onboarding_legacy_baseline(host_root)
        except workspace.OnboardingError:
            baseline = None
        if baseline is not None:
            plan = workspace.plan_isolated_onboarding(
                host_root, source_root=source_root or workspace.agentorch_root(),
                scenario=scenario, verified_source=verified,
                verified_baseline=baseline)
    if plan.status == 'PLANNED' and source_mismatch:
        plan.status = 'STOPPED_UNSAFE_SOURCE'
        plan.problems = ['planned kit bytes do not match the verified official release']
        plan.files = {}
        plan.agents_text = None
        plan.source_binding = None
    if mode == 'APPLY' and plan.status == 'PLANNED':
        # Re-fetch through the fixed client; never accept a caller-provided
        # verification object as the authorization for writes.
        latest = workspace.fetch_verified_onboarding_release()
        first_asset = next(c.get('detail') for c in verified.checks
                           if c.get('check') == 'ASSET_SHA256')
        latest_asset = next(c.get('detail') for c in latest.checks
                            if c.get('check') == 'ASSET_SHA256')
        if (first_asset != latest_asset or verified.files != latest.files
                or verified.manifest_bytes != latest.manifest_bytes
                or verified.version != latest.version):
            plan.status = 'STOPPED_UNSAFE_SOURCE'
            plan.problems = ['official release changed between planning and apply']
            plan.files = {}
            plan.agents_text = None
            plan.source_binding = None
        elif _onboarding_host_observation(host_root) != before:
            plan.status = 'STOPPED_CONFLICT'
            plan.problems = ['host write targets changed during planning']
            plan.files = {}
            plan.agents_text = None
            plan.source_binding = None
        elif declared_exclusive is not True:
            plan.status = 'BLOCKED'
            plan.problems = ['an exclusive dedicated worktree must be explicitly declared']
        else:
            preflight = _onboarding_worktree_preflight(
                host_root, declared_exclusive=True, expected_head=expected_head)
            if preflight.get('status') != 'OBSERVED':
                plan.status = 'BLOCKED'
                plan.problems = ['Git worktree / HEAD / clean-status preflight failed']
            else:
                plan.worktree_head = preflight.get('head_sha')
                plan.exclusive_writer = preflight.get('exclusive_writer')
                head = plan.worktree_head

                def still_valid():
                    again = _onboarding_worktree_preflight(
                        host_root, declared_exclusive=True, expected_head=head)
                    return (again.get('status') == 'OBSERVED'
                            and _onboarding_host_observation(host_root) == before)

                result = workspace.apply_isolated_onboarding(
                    plan, precondition=still_valid, declared_exclusive=True,
                    expected_head=head)
                return {'plan': plan, 'result': result,
                        'payload': _plan_payload(plan, result)}
    if mode == 'PLAN' or plan.status != 'PLANNED':
        result = {'status': plan.status, 'problems': plan.problems,
                  'written': [], 'removed': [], 'agents_created': plan.agents_created}
        if mode == 'APPLY':
            result['rollback'] = {'required': False, 'attempted': False, 'result': 'NOT_NEEDED'}
    else:
        result = workspace.apply_isolated_onboarding(plan)
    return {'plan': plan, 'result': result, 'payload': _plan_payload(plan, result)}


def build_parser(isolated=True):
    parser = argparse.ArgumentParser(
        prog='update_agentorch.entry',
        description='Route "更新 AgentOrch" and prepare a safe update for the current edition.',
    )
    parser.add_argument('--project', default='.', help='installed project root (default: .)')
    parser.add_argument('--request', required=not isolated,
                        help='the user message to route, e.g. "更新 AgentOrch"')
    parser.add_argument('--mode', choices=('PLAN', 'APPLY'), default='PLAN',
                        help='PLAN inspects only; APPLY writes the conflict-free change set')
    parser.add_argument('--edition', choices=('local', 'github'),
                        help='override the edition read from docs/PROJECT.md')
    parser.add_argument('--no-isolation', action='store_true',
                        help='do not create an isolated worktree on a dirty APPLY')
    if not isolated:
        parser.add_argument('--output', help='also write the JSON payload to this path')
        return parser
    parser.add_argument('--install-isolated', metavar='HOST_ROOT',
                        help='isolated onboarding: install AgentOrch under '
                             'HOST_ROOT/agentorch/ and extend HOST_ROOT/AGENTS.md')
    parser.add_argument('--source',
                        help='AgentOrch source root for --install-isolated '
                             '(default: the root this entry ships in)')
    parser.add_argument('--scenario', choices=('isolated', 'new-project'),
                        default='isolated',
                        help='install scenario; new-project never runs the isolated installer')
    parser.add_argument('--rollback-backup', metavar='BACKUP_DIR', action='append',
                        help='roll back an isolated onboarding from the backup directory '
                             'reported by a previous --install-isolated APPLY')
    parser.add_argument('--declared-exclusive', action='store_true',
                        help='declare this linked worktree has no other writer; this is '
                             'a precondition, not a verified lock')
    parser.add_argument('--expected-head', metavar='SHA',
                        help='require the host worktree HEAD to equal this commit')
    parser.add_argument('--output', help='also write the JSON payload to this path')
    return parser


def main(argv=None):
    raw_args = list(sys.argv[1:] if argv is None else argv)
    isolated = False
    for arg in raw_args:
        if arg == '--':
            break
        option = arg.split('=', 1)[0]
        if (option.startswith('--') and len(option) > 2
                and '--install-isolated'.startswith(option)):
            isolated = True
            break
    if isolated:
        stderr = sys.stderr
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                args = build_parser().parse_args(raw_args)
            except SystemExit as exc:
                if exc.code == 0:
                    return 0
                print('update_agentorch.entry: invalid isolated arguments; inspect locally.',
                      file=stderr)
                return 2
    else:
        args = build_parser(isolated=False).parse_args(raw_args)
    if isolated and (args.install_isolated == '' or
                     (args.rollback_backup is not None and '' in args.rollback_backup)):
        print('update_agentorch.entry: invalid isolated arguments; inspect locally.',
              file=sys.stderr)
        return 2
    if isolated and args.rollback_backup:
        if not args.install_isolated:
            print('update_agentorch.entry: --rollback-backup also needs '
                  '--install-isolated <HOST_ROOT>', file=sys.stderr)
            return 2
        try:
            result = workspace.rollback_isolated_onboarding({
                'host_root': str(Path(args.install_isolated).resolve()),
                'backup_location': args.rollback_backup[-1],
            })
        except Exception:  # noqa: BLE001 - do not disclose local exception text
            print('Isolated rollback failed; side effects are unknown. Inspect locally.', file=sys.stderr)
            return 1
        status = _public_enum(result.get('status') if type(result) is dict else None,
                              {'ROLLED_BACK', 'BLOCKED'})
        public = {'status': status, 'problems': ([] if status == 'ROLLED_BACK' else
                  ['Rollback is blocked or its outcome is unknown; preserve the local state.']),
                  'rollback': _public_rollback(result)}
        sys.stdout.write(json.dumps(public, indent=2, ensure_ascii=False) + '\n')
        return 0 if status == 'ROLLED_BACK' else 2

    if isolated and args.install_isolated is not None:
        if args.output:
            print('update_agentorch.entry: --output is not supported for isolated '
                  'onboarding; capture stdout outside the host instead', file=sys.stderr)
            return 2
        try:
            prepared = prepare_isolated_install(
                args.install_isolated, source_root=args.source,
                scenario=args.scenario, mode=args.mode,
                declared_exclusive=args.declared_exclusive,
                expected_head=args.expected_head)
        except Exception:  # noqa: BLE001 - public output cannot contain local exception text
            print('update_agentorch.entry: isolated install error; side effects are unknown. '
                  'Preserve state and inspect locally.',
                  file=sys.stderr)
            return 1
        text = json.dumps(prepared['payload'], indent=2, ensure_ascii=False) + '\n'
        sys.stdout.write(text)
        status = prepared['payload']['status']
        return 0 if status in ('PLANNED', 'APPLIED', 'NEW_PROJECT_MODE') else 2

    if args.request is None:
        print('update_agentorch.entry: --request is required unless '
              '--install-isolated is used', file=sys.stderr)
        return 2
    try:
        payload = prepare_update(args.project, args.request, mode=args.mode,
                                 edition=args.edition,
                                 isolation=not args.no_isolation)
    except (EntryError, ValueError) as exc:
        print(f'update_agentorch.entry: configuration error: {exc}', file=sys.stderr)
        return 1

    text = json.dumps(payload, indent=2, ensure_ascii=False) + '\n'
    if args.output:
        try:
            Path(args.output).write_text(text, encoding='utf-8')
        except OSError as exc:
            print(f'update_agentorch.entry: cannot write report to {args.output}: {exc}',
                  file=sys.stderr)
            return 1
    sys.stdout.write(text)

    if payload['status'] == 'NOT_UPDATE':
        print(f"update_agentorch.entry: not an update request ({payload['message']})",
              file=sys.stderr)
        return EXIT_NOT_UPDATE
    if payload['status'] == 'NEEDS_CLARIFICATION':
        print(f"update_agentorch.entry: clarification required: {payload['message']}",
              file=sys.stderr)
        return EXIT_NOT_UPDATE
    if payload['status'] in ('PREPARED', 'PLANNED', 'APPLIED'):
        return 0
    return engine.EXIT_CODES.get((payload.get('engine_report') or {}).get('result'), 1)


if __name__ == '__main__':
    raise SystemExit(main())
