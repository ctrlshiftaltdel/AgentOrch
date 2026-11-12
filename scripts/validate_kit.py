#!/usr/bin/env python3
"""Static validation for the Agent-Orchestration kit (core + local/github editions)."""
from pathlib import Path
import argparse, fnmatch, hashlib, importlib.util, json, os, posixpath, re, runpy, sys

ROOT = Path(__file__).resolve().parents[1]
# Release package manifest. It exists only in a built package (the repository
# root has none), so every check below is skipped when the file is absent.
MANIFEST_DOC = 'MANIFEST.json'
# Single source of truth for the shippable kit. This list has two jobs at once:
#   1. the structurally required files of this repository, and
#   2. the file manifest of the release package built by scripts/build_release.py.
# scripts/build_release.py parses THIS list to decide what to package, so there is
# no second manifest to maintain. Keep it in sync whenever a versioned artifact is
# added or removed. Files that must not ship (the rest of docs/, release/, the
# release builder itself, .orchestration/, .worktrees/, .workbuddy/) are simply not
# listed here. This bridge release ships the contract at `kit/orchestration.md`
# again: the published v1.7.0 updater will not write anything under `docs/` and
# stops if `kit/orchestration.md` disappears. `docs/orchestration.md` in this
# repository is a non-shipped pointer, not a second contract.
REQUIRED = [
    'AGENTS.md', 'LICENSE.md', 'VERSION.md',
    'kit/orchestration.md',
    'editions/github/active-task-registry.md', 'editions/github/issue-template.md',
    'editions/github/execution.md', 'editions/github/master.md',
    'editions/github/pr-template.md',
    'editions/local/master.md', 'editions/local/execution.md',
    'templates/architecture/README.md', 'templates/architecture/contract.example.json',
    'templates/contract-decision-log.md', 'templates/design.md',
    'templates/implementation-plan.md', 'templates/metrics.md',
    'templates/milestone-audit.md', 'templates/plan.md', 'templates/project.md',
    'templates/project-charter.md', 'templates/review-checklist.md',
    'templates/runtime-capability.md', 'templates/task-packet.md',
    'templates/gitignore.snippet',
    'templates/self-improvement/README.md',
    'templates/self-improvement/closure-report.md',
    'templates/self-improvement/proposal.md',
    'templates/self-improvement/sources.example.json',
    'templates/self-improvement/task-packet.md',
    'scripts/architecture/__init__.py', 'scripts/architecture/check.py',
    'scripts/architecture/contract.py', 'scripts/architecture/report.py',
    'scripts/architecture/rules.py', 'scripts/architecture/adapters/__init__.py',
    'scripts/architecture/adapters/python_ast.py', 'scripts/architecture/adapters/rust.py',
    'scripts/agent_selection/__init__.py', 'scripts/agent_selection/scope.py',
    'scripts/agent_selection/local_store.py',
    'scripts/agent_selection/runtime_adapter.py',
    'scripts/agent_selection/discovery.py', 'scripts/agent_selection/roles.py',
    'scripts/agent_selection/model_selection.py',
    'scripts/agent_selection/feedback.py',
    'scripts/agent_selection/peer_review.py',
    'scripts/agent_selection/model_sources/__init__.py',
    'scripts/agent_selection/model_sources/snapshot.py',
    'scripts/agent_selection/model_sources/artificial_analysis.py',
    'scripts/check_drift.py', 'scripts/check_scope.py', 'scripts/orchestrator.py',
    'scripts/report_metrics.py',
    'scripts/run_delivery_scenarios.py', 'scripts/validate_kit.py',
    'scripts/verification_profile.py',
    'scripts/delivery_scenarios/__init__.py',
    'scripts/delivery_scenarios/common.py',
    'scripts/delivery_scenarios/group_a.py',
    'scripts/delivery_scenarios/group_b.py',
    'scripts/delivery_scenarios/group_c.py',
    'scripts/delivery_scenarios/group_d.py',
    'scripts/delivery_scenarios/group_e.py',
    'scripts/self_improvement/__init__.py', 'scripts/self_improvement/cli.py',
    'scripts/self_improvement/collect.py', 'scripts/self_improvement/lessons.py',
    'scripts/self_improvement/orchestration.py', 'scripts/self_improvement/pipeline.py',
    'scripts/self_improvement/planning.py', 'scripts/self_improvement/sources.py',
    'scripts/self_improvement/untrusted.py',
    'scripts/update_agentorch/__init__.py', 'scripts/update_agentorch/__main__.py',
    'scripts/update_agentorch/classify.py', 'scripts/update_agentorch/cli.py',
    'scripts/update_agentorch/engine.py', 'scripts/update_agentorch/entry.py',
    'scripts/update_agentorch/migrate.py',
    'scripts/update_agentorch/package.py', 'scripts/update_agentorch/report.py',
    'scripts/update_agentorch/sources.py', 'scripts/update_agentorch/versions.py',
    'scripts/update_agentorch/workspace.py',
    'tests/architecture/__init__.py', 'tests/architecture/test_architecture_checker.py',
    'tests/agent_selection/__init__.py',
    'tests/agent_selection/test_scope.py', 'tests/agent_selection/test_local_store.py',
    'tests/agent_selection/support.py',
    'tests/agent_selection/test_runtime_adapter.py',
    'tests/agent_selection/test_discovery.py',
    'tests/agent_selection/test_roles.py',
    'tests/agent_selection/test_model_selection.py',
    'tests/agent_selection/test_model_identity.py',
    'tests/agent_selection/test_module_registry.py',
    'tests/agent_selection/test_feedback.py',
    'tests/agent_selection/test_e2e.py',
    'tests/agent_selection/model_sources/__init__.py',
    'tests/agent_selection/model_sources/fixtures.py',
    'tests/agent_selection/model_sources/test_snapshot.py',
    'tests/agent_selection/model_sources/test_artificial_analysis.py',
    'tests/self_improvement/__init__.py', 'tests/self_improvement/loop_support.py',
    'tests/self_improvement/support.py',
    'tests/self_improvement/test_acceptance_report.py',
    'tests/self_improvement/test_collection.py',
    'tests/self_improvement/test_lesson_lifecycle.py',
    'tests/self_improvement/test_orchestration.py',
    'tests/self_improvement/test_pipeline.py',
    'tests/self_improvement/test_planning.py',
    'tests/self_improvement/test_registry.py',
    'tests/self_improvement/test_routing.py',
    'tests/test_check_scope.py',
    'tests/test_orchestrator.py', 'tests/test_release_drift.py',
    'tests/test_report_metrics.py', 'tests/test_run_delivery_scenarios.py',
    'tests/test_update_entry.py', 'tests/test_validate_kit.py',
    'tests/update_agentorch/__init__.py', 'tests/update_agentorch/support.py',
    'tests/update_agentorch/test_classification.py',
    'tests/update_agentorch/test_contract_migration.py',
    'tests/update_agentorch/test_dependencies.py',
    'tests/update_agentorch/test_engine.py',
    'tests/update_agentorch/test_entry_release.py',
    'tests/update_agentorch/test_package.py',
    'tests/update_agentorch/test_versions_and_sources.py',
]

# --- Isolated layout ---------------------------------------------------------
# An existing project keeps its own files and hosts AgentOrch under
# ``<host>/agentorch/``. The kit root therefore has no AGENTS.md; the one root
# entry lives at ``<host>/AGENTS.md`` and carries a single managed block.
ISOLATED_DIRNAME = 'agentorch'
ISOLATED_MANAGED_FILE = 'AGENTS.md'
ISOLATED_EXTRA_FILE = '.gitignore'
ISOLATED_REQUIRED = [rel for rel in REQUIRED if rel != ISOLATED_MANAGED_FILE]
ISOLATED_MANIFEST_PATHS = set(ISOLATED_REQUIRED) | {ISOLATED_EXTRA_FILE}
ISOLATED_PRIVATE_DIRS = frozenset({
    '.orchestration', '.local', '.worktrees', '.workbuddy', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.git',
})
MANAGED_BEGIN_RE = re.compile(r'^<!-- AGENTORCH:BEGIN(?: v(?P<version>[^ ]+))? -->$')
MANAGED_END_LINE = '<!-- AGENTORCH:END -->'
MANAGED_HASH_RE = re.compile(
    r'^<!-- agentorch:managed-body-sha256: (?P<sha>[0-9a-f]{64}) -->$')
MANAGED_BODY_HEADING = '# AgentOrch 推进引擎（受管区块）'

# --- Repository-only required documents --------------------------------------
# These files are required in a checkout but must NOT be added to REQUIRED:
# scripts/build_release.py refuses every `docs` path (the shipped core contract
# currently lives at `kit/orchestration.md`), so listing any of them above would
# turn every release self-check red. They are still required artifacts — a missing
# file or a deleted section fails validate_kit on a checkout.
#
# `tests/update_agentorch/test_report.py` is repository-only for the same
# reason: it reads the frozen fixtures under `docs/contracts/fixtures/`, which
# never ship. The package still tests the engine and the entry through the
# shipped `tests/update_agentorch/` modules listed in REQUIRED above.
#
# The document set has two layers, and which one is enforced depends on the tree
# (see `tree_mode`):
#
#   * `PROJECT_DOCS`  — every project that keeps a `docs/` directory must have
#     them. A downstream project fills them in after unzipping the kit, so they
#     are the only documents it can be asked for.
#   * `UPSTREAM_DOCS` — this repository's own documents. They never ship, so a
#     downstream project must not be forced to copy them.
#
# `docs/upgrade.md` is the upstream marker: it is repository-only, so its
# presence means "this is an upstream checkout" and the full set is enforced.
# A docs/ directory holding a project config (`docs/PROJECT.md`) is a downstream
# project tree; a docs/ directory holding nothing but the shipped contents of
# REQUIRED (currently nothing, because the contract ships at `kit/`) is a pristine
# release package. See `tree_mode` for the exact rule. A downstream project must
# not create its own `docs/upgrade.md` — that would switch the tree to upstream mode.
PROJECT_DOCS = {
    'docs/PROJECT.md': (),
    'docs/PLAN.md': ('设计目标',),
}
UPSTREAM_DOCS = {
    'docs/README.md': (),
    'docs/implementation-plan.md': ('当前状态',),
    'docs/charter.md': (),
    'docs/upgrade.md': ('升级',),
    'docs/drift.md': (),
    'docs/metrics.md': (),
    'docs/design/resource-locks.md': ('冲突',),
    'docs/testing/delivery-regression-scenarios.md': (
        '场景矩阵', '运行方式', 'Fixture 规则', '正负组', '未验证范围', '清理',
    ),
    'docs/testing/document-consistency-gates.md': (
        '适用范围', '关键文件责任', '文档影响记录', '计划状态', '发行包规则',
        '隔离布局', '未覆盖范围',
    ),
}
# Union kept under the historical name: existing callers and tests read it as
# "every repository-only document", independent of the tree being validated.
REQUIRED_DOCS = {**PROJECT_DOCS, **UPSTREAM_DOCS}
UPSTREAM_MARKER_DOC = 'docs/upgrade.md'

TREE_PACKAGE = 'package'
TREE_DOWNSTREAM = 'downstream'
TREE_UPSTREAM = 'upstream'

# --- Milestone-audit closeout contract ---------------------------------------
# A filled audit record may only be read as "stage complete" when no verdict
# forbids it. These values are the four classes from templates/milestone-audit.md
# plus PASS (verified) and the two non-pass markers; anything else is a typo or a
# second, invented vocabulary, so it is reported instead of counted.
AUDIT_VERDICTS = ('PASS', 'NON_BLOCKING_HARDENING', 'OPTIONAL', 'NEEDS_HUMAN_DECISION',
                  'BLOCKING', 'NOT_RUN', 'BLOCKED', 'NOT_APPLICABLE')
CLOSEOUT_BLOCKING_VERDICTS = ('BLOCKING', 'NEEDS_HUMAN_DECISION')
CLOSEOUT_UNVERIFIED_VERDICTS = ('NOT_RUN', 'BLOCKED')

# --- Reusable contract templates ---------------------------------------------
# DESIGN.md, MILESTONE-AUDIT.md and CONTRACT-DECISION-LOG.md are contracts in
# their own right: a deleted heading silently removes a question nobody will then
# answer, and a broken relative link makes the cross-reference unusable. Both are
# checked here, on structure only — whether the filled answers are true is a
# review decision, not something a static check can prove.
# Section names are matched case-insensitively as substrings of a heading, so a
# numbered heading ("### 1. Functional completeness") still matches.
TEMPLATE_SECTIONS = {
    'templates/design.md': (
        'Scope and non-goals', 'Schema and interfaces', 'State transitions', 'Invariants',
        'Failure semantics', 'Compatibility and migration', 'Permission and trust boundaries',
        'Verification strategy', 'Frozen decisions', 'Open decisions',
        'Versioning and change policy',
    ),
    'templates/milestone-audit.md': (
        'When to use', 'How to fill', 'Verdict classes',
        'Functional completeness', 'Cross-module invariants', 'Permissions and security',
        'Legacy data migration', 'Real and synthetic data validation', 'End-to-end path',
        'Recoverability', 'Performance baseline', 'Public content and privacy',
        'Residual items', 'Audit conclusion',
    ),
    'templates/contract-decision-log.md': (
        'When to use', 'Status values', 'Frozen decisions', 'Open decisions',
        'Revisitable items', 'Superseded records', 'Recording rules',
    ),
    # A capability record that loses a row silently claims a capability nobody
    # probed; a metric definition that loses a column leaves the start / end
    # event, exclusion or missing-data rule undefined. Both are contracts, so
    # only their structure is checked here, never whether the numbers are true.
    'templates/runtime-capability.md': (
        'When to use', 'Record identity', 'Capability matrix', 'Extended boundaries',
        'UNKNOWN and re-probe', 'Permission scope', 'Evidence and limits',
    ),
    'templates/metrics.md': (
        'When to use', 'Reading rules', 'Common definitions', 'Metric definitions',
        'Missing data and coverage', 'Report contract', 'Gold sample', 'Limits',
    ),
}
HEADING_RE = re.compile(r'^#{2,4}\s+(?P<text>.+?)\s*$', re.M)
MD_LINK_RE = re.compile(r'\[[^\]]*\]\(([^)]+)\)')


# Docs teach the link/image syntax itself (e.g. `![图](images/x.png)` inside
# backticks or fenced blocks). Those spans are literal text, not live links, so
# scanning them reported false "broken relative link" errors on every document
# that documents the convention.
CODE_BLOCK_RE = re.compile(r'```.*?```', re.S)
CODE_SPAN_RE = re.compile(r'`[^`\n]*`')


def markdown_links(text):
    """Markdown link targets outside fenced code blocks and inline code."""
    text = CODE_BLOCK_RE.sub('', text)
    text = CODE_SPAN_RE.sub('', text)
    return MD_LINK_RE.findall(text)
EXTERNAL_LINK_PREFIXES = ('http://', 'https://', 'mailto:', '#')

# --- Evidence matrix contract -------------------------------------------------
# The field names, the status enum and the G1 rules are defined once, in
# the core contract (`kit/orchestration.md` for the 1.7.1 bridge release;
# `CORE_DOC` below is the machine-readable copy of that path). This module reads
# that definition back (it does not restate it) and enforces it on the
# local/github task templates. If the doc drifts away from the contract floor
# below, validation fails instead of silently accepting a second definition.
CORE_DOC = 'kit/orchestration.md'
TASK_PACKET_DOC = 'templates/task-packet.md'
ISSUE_TEMPLATE_DOC = 'editions/github/issue-template.md'
EVIDENCE_FIELD_ANCHOR = '证据条目（evidence item）字段'
PACKET_FIELD_ANCHOR = '任务包结构化扩展字段'
# Contract floor for `status`. A silently narrowed or extended enum is reported.
STATUS_VALUES = ('PASS', 'FAIL', 'NOT_RUN', 'BLOCKED', 'NOT_APPLICABLE')
NON_PASS_STATUSES = ('FAIL', 'NOT_RUN', 'BLOCKED')
# Text that claims the evidence was not produced. A row marked PASS while saying
# this is "not executed but counted as passing" and must be rejected.
NOT_RUN_MARKERS = re.compile(
    r'\bnot[ _-]?run\b|\bnot executed\b|\bnot verified\b|未执行|未运行|未验证|blocked by|被阻塞', re.I)
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
# Fields every task packet must carry, on top of the structured scenario fields
# read from the core contract (`kit/orchestration.md`). Names are the template
# labels, lower-cased.
PACKET_REQUIRED_FIELDS = (
    'task id', 'title', 'goal / user-visible result', 'context', 'allowed paths',
    'forbidden paths', 'dependencies (must be verified)', 'acceptance criteria',
    'validation commands', 'baseline branch / commit', 'expected branch / worktree',
    'evidence head sha', 'evidence matrix', 'risks / human decisions',
    'delivery evidence', 'hard boundaries',
)
# Fields whose content lives on the indented lines below the label.
PACKET_LIST_FIELDS = ('acceptance criteria', 'evidence matrix')

FIELD_LINE = re.compile(r'^- \*\*(?P<label>[^*]+?)\*\*\s*:?\s*(?P<value>.*)$')
CHECKBOX_LINE = re.compile(r'^\s*-\s*\[(?P<mark>[ xX])\]\s*(?P<body>.*)$')
AC_HEAD = re.compile(r'^(?P<id>AC-\d+)\s*[:：]\s*(?P<text>.*)$')
SEPARATOR_CELL = re.compile(r'^:?-{2,}:?$')

# --- Named resource declarations (shape only) ---------------------------------
# Semantics live in docs/design/RESOURCE-LOCKS.md; this file only checks that a
# filled carrier field can be read back. The field is OPTIONAL: absent or empty
# means "this task takes no named resource lock" and must never be a failure.
RESOURCE_FIELD = 'declared_resources'
LOCKED_RESOURCE_FIELD = 'locked_resources'
RESOURCE_SPEC_RE = re.compile(
    r'(?P<type>[a-z][a-z0-9-]{0,15}):(?P<name>[a-z0-9][a-z0-9._/-]{0,127})'
    r':(?P<mode>shared|exclusive)(?::(?P<ttl>[1-9][0-9]*))?')


def resource_declaration_errors(text, label, field=RESOURCE_FIELD):
    """Errors for a filled resource set; empty/absent values are legal."""
    fields = parse_task_packet(text)
    if field_label(field) not in fields:
        return []
    value = (field_value(fields, field) or '').strip().strip('`').strip()
    if not value:
        return []
    errors, seen = [], set()
    for raw in value.split(','):
        item = raw.strip().strip('`').strip()
        if not item:
            errors.append(f'{label}: {field} has an empty entry (remove the stray comma)')
            continue
        match = RESOURCE_SPEC_RE.fullmatch(item)
        if not match:
            errors.append(f'{label}: {field} entry {item!r} must be '
                          '<resource_id>:<mode>[:<ttl>] with mode shared or exclusive')
            continue
        if any(part in ('', '.', '..') for part in match.group('name').split('/')):
            errors.append(f'{label}: {field} entry {item!r} has an empty or dots-only path segment')
            continue
        resource_id = f'{match.group("type")}:{match.group("name")}'
        if resource_id in seen:
            errors.append(f'{label}: {field} declares {resource_id} more than once')
        seen.add(resource_id)
    return errors


def read_text(path):
    return Path(path).read_text(encoding='utf-8')


def _table_after(text, anchor):
    """Data rows of the first markdown table following the line containing anchor."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if anchor in line:
            start = i + 1
            break
    if start is None:
        return None
    j = start
    while j < len(lines) and not lines[j].strip().startswith('|'):
        j += 1
    rows = []
    while j < len(lines) and lines[j].strip().startswith('|'):
        rows.append([c.strip() for c in lines[j].strip().strip('|').split('|')])
        j += 1
    rows = [r for r in rows if not all(SEPARATOR_CELL.match(c) for c in r if c)]
    if len(rows) < 2:
        return None
    return rows[1:]  # drop the header row


def _backticked(cell):
    match = re.search(r'`([^`]+)`', cell or '')
    return match.group(1).strip() if match else None


def canonical_evidence_fields(core_text):
    """(field names, status values) as declared by the core orchestration doc."""
    rows = _table_after(core_text, EVIDENCE_FIELD_ANCHOR)
    if not rows:
        return [], []
    fields, statuses = [], []
    for row in rows:
        name = _backticked(row[0]) if row else None
        if not name:
            return [], []
        fields.append(name)
        if name == 'status' and len(row) > 2:
            statuses = re.findall(r'`([A-Z_]+)`', ' '.join(row[1:]))
    return fields, statuses


def canonical_packet_fields(core_text):
    """Structured task-packet field names declared by the core orchestration doc."""
    rows = _table_after(core_text, PACKET_FIELD_ANCHOR)
    if not rows:
        return []
    names = []
    for row in rows:
        name = _backticked(row[0]) if row else None
        if not name:
            return []
        names.append(name)
    return names


def parse_task_packet(text):
    """Map normalised field label -> {'inline': str, 'lines': [str]}."""
    fields = {}
    current = None
    for line in text.splitlines():
        match = FIELD_LINE.match(line)
        if match:
            current = field_label(match.group('label'))
            fields[current] = {'inline': match.group('value').strip(), 'lines': []}
            continue
        if current is not None:
            fields[current]['lines'].append(line)
    return fields


def field_label(name):
    """`unverified_assumptions`, `Unverified assumptions:` -> `unverified assumptions`."""
    label = re.sub(r'[\s_]+', ' ', (name or '').strip()).strip()
    return label.strip(':：').strip().lower()


def _clean(cell):
    return (cell or '').strip().strip('`').strip()


def field_value(fields, name):
    """Scalar value of a field: inline value, else the first non-empty sub-line."""
    item = fields.get(field_label(name))
    if item is None:
        return None
    if item['inline']:
        return item['inline']
    for line in item['lines']:
        if line.strip():
            return line.strip()
    return ''


def parse_matrix(lines):
    header, rows = None, []
    for raw in lines:
        line = raw.strip()
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if cells and all(SEPARATOR_CELL.match(c) for c in cells if c):
            continue
        if header is None:
            header = cells
            continue
        rows.append(cells)
    return header, rows


def _tables(text):
    """[(header, rows)] for every markdown table in text, in document order."""
    tables, header, rows = [], None, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith('|'):
            if header is not None:
                tables.append((header, rows))
                header, rows = None, []
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if all(SEPARATOR_CELL.match(c) for c in cells if c):
            continue
        if header is None:
            header = cells
        else:
            rows.append(cells)
    if header is not None:
        tables.append((header, rows))
    return tables


def audit_conclusion_errors(text, label='milestone audit'):
    """Findings that forbid reading a filled audit as a stage closeout.

    Only the *classification* is machine-checkable: a row whose verdict is
    BLOCKING or NEEDS_HUMAN_DECISION forbids "stage complete", and a NOT_RUN /
    BLOCKED row is not counted as passing. Whether the verdict is justified stays
    a review decision and is never claimed by this function.
    """
    errors, seen = [], 0
    for header, rows in _tables(text):
        labels = [field_label(cell) for cell in header]
        columns = [index for index, name in enumerate(labels)
                   if 'verdict' in name or '结论' in name]
        if not columns:
            continue
        column = columns[0]
        for index, cells in enumerate(rows, 1):
            if len(cells) <= column:
                errors.append(f'{label}: row {index} is shorter than its verdict column')
                continue
            verdict = _clean(cells[column]).upper()
            seen += 1
            if verdict not in AUDIT_VERDICTS:
                errors.append(f'{label}: row {index} has an unknown verdict '
                              f'{verdict or "(empty)"}; allowed: {", ".join(AUDIT_VERDICTS)}')
            elif verdict in CLOSEOUT_BLOCKING_VERDICTS:
                errors.append(f'{label}: row {index} is {verdict}; the stage must not be '
                              'claimed complete or passing')
            elif verdict in CLOSEOUT_UNVERIFIED_VERDICTS:
                errors.append(f'{label}: row {index} is {verdict}; it is not counted as passing')
    if not seen:
        errors.append(f'{label}: no readable verdict rows (expected a table with a '
                      'verdict / 结论 column)')
    return errors


def is_isolated_layout(root=ROOT):
    """True only when *root* looks like ``<host>/agentorch/`` with a host entry.

    The directory name alone is never enough: the host root must carry a regular
    ``AGENTS.md`` and the kit must carry both VERSION.md and MANIFEST.json.
    """
    root = Path(root)
    if root.name != ISOLATED_DIRNAME:
        return False
    host = root.parent
    agents = host / ISOLATED_MANAGED_FILE
    return (agents.is_file() and not agents.is_symlink()
            and (root / 'VERSION.md').is_file()
            and (root / 'MANIFEST.json').is_file())


def _managed_block_errors(agents_path, expected_version):
    path = Path(agents_path)
    if path.is_symlink() or not path.is_file():
        return [f'host root {ISOLATED_MANAGED_FILE} is not a regular file']
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        return [f'host root {ISOLATED_MANAGED_FILE} is not readable UTF-8: {exc}']
    lines = text.split('\n')
    begins, ends, hashes = [], [], []
    for index, line in enumerate(lines):
        stripped = line.strip()
        begin = MANAGED_BEGIN_RE.match(stripped)
        if begin:
            begins.append((index, begin.group('version')))
        elif stripped == MANAGED_END_LINE:
            ends.append(index)
        else:
            digest = MANAGED_HASH_RE.match(stripped)
            if digest:
                hashes.append((index, digest.group('sha')))
    errors = []
    if len(begins) != 1:
        errors.append(f'host root {ISOLATED_MANAGED_FILE} must contain exactly one '
                      f'AgentOrch BEGIN marker, found {len(begins)}')
    if len(ends) != 1:
        errors.append(f'host root {ISOLATED_MANAGED_FILE} must contain exactly one '
                      f'AgentOrch END marker, found {len(ends)}')
    if errors:
        return errors
    begin_index, version = begins[0]
    end_index = ends[0]
    if end_index <= begin_index:
        return [f'host root {ISOLATED_MANAGED_FILE} END marker is not after BEGIN']
    inner = [(index, sha) for index, sha in hashes if begin_index < index < end_index]
    if len(inner) != 1:
        return [f'host root {ISOLATED_MANAGED_FILE} managed block must carry exactly '
                f'one body hash, found {len(inner)}']
    hash_index, recorded = inner[0]
    if hash_index + 1 != end_index:
        return [f'host root {ISOLATED_MANAGED_FILE} has content between the body hash '
                f'and the END marker']
    body_lines = lines[begin_index + 1:hash_index]
    if not body_lines or body_lines[0].strip() != MANAGED_BODY_HEADING:
        return [f'host root {ISOLATED_MANAGED_FILE} managed block is not the canonical '
                f'AgentOrch block']
    body = '\n'.join(body_lines) + '\n'
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
    if digest != recorded:
        errors.append(f'host root {ISOLATED_MANAGED_FILE} managed block body hash '
                      f'does not match its bytes')
    try:
        workspace_path = Path(__file__).resolve().parent / 'update_agentorch' / 'workspace.py'
        workspace = runpy.run_path(str(workspace_path), run_name='_validator_workspace')
        canonical = workspace['managed_block'](version or '')
        canonical_body = '\n'.join(canonical.split('\n')[1:-3]) + '\n'
        if not version or body != canonical_body:
            errors.append(f'host root {ISOLATED_MANAGED_FILE} managed block body is not canonical')
    except Exception as exc:  # noqa: BLE001 - unavailable canonical source fails closed
        errors.append(f'host root {ISOLATED_MANAGED_FILE} canonical block unavailable: {exc}')
    if not version or version != expected_version:
        errors.append(f'host root {ISOLATED_MANAGED_FILE} block version '
                      f'{version!r} does not match VERSION.md format {expected_version!r}')
    return errors


def isolated_layout_errors(root=ROOT):
    """Structural checks for ``<host>/agentorch/``; no independent release anchor."""
    root = Path(root)
    host = root.parent
    errors = []
    if (root / ISOLATED_MANAGED_FILE).exists() or (root / ISOLATED_MANAGED_FILE).is_symlink():
        errors.append(f'{ISOLATED_DIRNAME}/{ISOLATED_MANAGED_FILE} must not exist in '
                      f'the isolated layout')
    drift = _drift_module()
    if drift is None:
        return errors + [f'{ISOLATED_DIRNAME} layout needs scripts/check_drift.py']
    try:
        suite, version = drift.read_version(root)
    except Exception as exc:  # noqa: BLE001 - drift raises its own error type
        return errors + [f'{ISOLATED_DIRNAME}/VERSION.md is unreadable: {exc}']
    errors.extend(_managed_block_errors(host / ISOLATED_MANAGED_FILE, version))

    manifest_path = root / 'MANIFEST.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return errors + [f'{ISOLATED_DIRNAME}/MANIFEST.json is unreadable: {exc}']
    structure = drift.manifest_structure_errors(manifest)
    if structure:
        return errors + [f'{ISOLATED_DIRNAME}/MANIFEST.json: {reason}'
                         for reason in structure]
    if manifest.get('suite') != suite:
        errors.append(f'{ISOLATED_DIRNAME}/MANIFEST.json suite does not match VERSION.md')
    if manifest.get('format_version') != version:
        errors.append(f'{ISOLATED_DIRNAME}/MANIFEST.json format_version does not match '
                      f'VERSION.md')
    listed = set(_listed_manifest_paths(manifest))
    for rel in sorted(ISOLATED_MANIFEST_PATHS - listed):
        errors.append(f'{ISOLATED_DIRNAME}/MANIFEST.json does not list required file: {rel}')
    for rel in sorted(listed - ISOLATED_MANIFEST_PATHS):
        errors.append(f'{ISOLATED_DIRNAME}/MANIFEST.json lists a file that is not in '
                      f'the isolated required set: {rel}')
    errors.extend(f'{ISOLATED_DIRNAME}/MANIFEST.json: {reason}'
                  for reason in drift.verify_manifest_files(root, manifest))

    expected_dirs = set()
    for rel in ISOLATED_MANIFEST_PATHS:
        parts = rel.split('/')[:-1]
        for index in range(1, len(parts) + 1):
            expected_dirs.add('/'.join(parts[:index]))

    def walk_error(exc):
        # Do not include exception text or arbitrary host paths in diagnostics.
        try:
            rel = Path(exc.filename).relative_to(root).as_posix()
        except (TypeError, ValueError):
            rel = '<unknown>'
        if rel != '.' and rel not in expected_dirs:
            rel = '<unknown>'
        code = exc.errno if isinstance(exc.errno, int) else 'unknown'
        errors.append(f'cannot enumerate {ISOLATED_DIRNAME}/{rel} (errno {code})')

    for directory, dirnames, filenames in os.walk(root, onerror=walk_error):
        current = Path(directory)
        rel_dir = current.relative_to(root).as_posix()
        keep = []
        for name in dirnames:
            rel = name if rel_dir == '.' else f'{rel_dir}/{name}'
            path = current / name
            if path.is_symlink():
                errors.append(f'symlinked directory inside {ISOLATED_DIRNAME}/: {rel}')
                continue
            if name in ISOLATED_PRIVATE_DIRS:
                continue
            if rel not in expected_dirs:
                errors.append(f'unknown directory inside {ISOLATED_DIRNAME}/: {rel}')
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            rel = name if rel_dir == '.' else f'{rel_dir}/{name}'
            path = current / name
            if path.is_symlink():
                errors.append(f'symlink inside {ISOLATED_DIRNAME}/: {rel}')
                continue
            if rel == 'MANIFEST.json':
                continue
            if rel not in ISOLATED_MANIFEST_PATHS:
                errors.append(f'unknown file inside {ISOLATED_DIRNAME}/: {rel}')
    return errors


def _scan_files(root, start, errors, excluded=()):
    """Enumerate files without silently treating a failed scan as an empty tree."""
    root, start = Path(root), Path(start)
    known_dirs = {'.', 'docs'}
    for rel in REQUIRED:
        parts = rel.split('/')[:-1]
        known_dirs.update('/'.join(parts[:i]) for i in range(1, len(parts) + 1))

    def onerror(exc):
        try:
            rel = Path(exc.filename).relative_to(root).as_posix()
        except (TypeError, ValueError):
            rel = '<unknown>'
        if rel not in known_dirs:
            rel = '<unknown>'
        code = exc.errno if isinstance(exc.errno, int) else 'unknown'
        errors.append(f'cannot enumerate {rel} (errno {code})')

    for directory, dirnames, filenames in os.walk(start, onerror=onerror):
        dirnames[:] = [name for name in dirnames if name not in excluded]
        for name in filenames:
            path = Path(directory) / name
            if path.is_file():
                yield path


def tree_mode(root=ROOT, errors=None):
    """Which of the three supported trees *root* is.

    `package`    - a pristine release package. This bridge release ships no
                   `docs/` file; a `docs/` tree is a package only while every
                   file under it is in REQUIRED.
    `downstream` - a project tree: `docs/PROJECT.md` exists, `docs/upgrade.md`
                   does not.
    `upstream`   - this repository: `docs/upgrade.md` marks an upstream checkout.

    The published v1.7.0 updater will not write `docs/`, so this release ships
    the contract at `kit/orchestration.md` and ships no `docs/` file. A package
    with no `docs/` directory is still a package. A `docs/` tree without
    `docs/PROJECT.md` is a package only while every file under it is shipped;
    anything else fails closed on a missing `docs/PROJECT.md`.
    """
    root = Path(root)
    if (root / UPSTREAM_MARKER_DOC).is_file():
        return TREE_UPSTREAM
    if (root / 'docs' / 'PROJECT.md').is_file():
        return TREE_DOWNSTREAM
    docs = root / 'docs'
    if not docs.is_dir():
        return TREE_PACKAGE
    scan_errors = []
    shipped = {path.relative_to(root).as_posix()
               for path in _scan_files(root, docs, scan_errors)}
    if scan_errors:
        if errors is not None:
            errors.extend(scan_errors)
        return TREE_DOWNSTREAM
    return TREE_PACKAGE if shipped <= set(REQUIRED) else TREE_DOWNSTREAM


def required_doc_errors(root=ROOT):
    """Missing or structurally incomplete repository-only documents.

    The required set follows the tree (see `tree_mode`): a release package has no
    `docs/` and is skipped; a downstream project only has to provide
    `docs/PROJECT.md` and `docs/PLAN.md`; an upstream checkout must provide every
    document in `REQUIRED_DOCS`.
    """
    root = Path(root)
    errors = []
    mode = tree_mode(root, errors)
    if mode == TREE_PACKAGE:
        return errors
    documents = REQUIRED_DOCS if mode == TREE_UPSTREAM else PROJECT_DOCS
    for rel, sections in documents.items():
        path = root / rel
        if not path.is_file():
            errors.append(f'missing required document: {rel}')
            continue
        text = path.read_text(encoding='utf-8')
        headings = [match.group('text').lower() for match in HEADING_RE.finditer(text)]
        for section in sections:
            if not any(section.lower() in heading for heading in headings):
                errors.append(f'{rel} is missing required section: {section}')
    return errors


def extract_packet_samples(text):
    """Fenced ```task-packet blocks, used as structural test fixtures."""
    samples, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        match = re.match(r'^```task-packet(?:\s+(?P<name>.*))?$', lines[i].strip())
        if not match:
            i += 1
            continue
        name = (match.group('name') or '').strip() or f'sample-{len(samples) + 1}'
        body, i = [], i + 1
        while i < len(lines) and lines[i].strip() != '```':
            body.append(lines[i])
            i += 1
        samples.append({'name': name, 'text': '\n'.join(body)})
        i += 1
    return samples


def validate_task_packet(text, canonical_fields, status_values, label='task packet',
                         structured_fields=(), require_docs_impact=False):
    """Return structural errors for one filled task packet; empty list means valid."""
    if not canonical_fields:
        return [f'{label}: canonical evidence fields are unavailable in {CORE_DOC}']
    errors = []
    fields = parse_task_packet(text)

    for name in PACKET_REQUIRED_FIELDS:
        if field_label(name) not in fields:
            errors.append(f'{label}: missing required field: {name}')
        elif name not in PACKET_LIST_FIELDS and not field_value(fields, name):
            errors.append(f'{label}: required field is empty: {name}')

    for name in structured_fields:
        value = field_value(fields, name)
        if value is None:
            errors.append(f'{label}: missing structured field: {name}')
            continue
        if not value:
            errors.append(f'{label}: structured field is empty: {name}')
            continue
        if value.upper().startswith('N/A'):
            parts = re.split(r'[:：]', value, maxsplit=1)
            reason = parts[1].strip() if len(parts) > 1 else ''
            if not reason:
                errors.append(f'{label}: structured field {name} must give a reason after "N/A:"')

    acs = {}
    item = fields.get(field_label('acceptance criteria'))
    ac_source = ([item['inline']] if item and item['inline'] else []) + (item['lines'] if item else [])
    for line in ac_source:
        match = CHECKBOX_LINE.match(line)
        if not match:
            continue
        body = match.group('body').strip()
        head = AC_HEAD.match(body)
        if not head:
            errors.append(f'{label}: acceptance criterion without an AC-<n> id: {body or "(empty)"}')
            continue
        ac_id = head.group('id')
        if not head.group('text').strip():
            errors.append(f'{label}: acceptance criterion {ac_id} has no checkable statement')
        if ac_id in acs:
            errors.append(f'{label}: duplicate acceptance criterion id: {ac_id}')
        acs[ac_id] = match.group('mark').lower() == 'x'
    if not acs:
        errors.append(f'{label}: no acceptance criteria found (expected "- [ ] AC-1: ...")')

    head_sha = _clean(field_value(fields, 'evidence head sha') or '')
    if head_sha and not SHA_RE.match(head_sha.lower()):
        errors.append(f'{label}: evidence head SHA must be a full 40-hex commit SHA')

    matrix_lines = fields.get('evidence matrix', {}).get('lines', [])
    header, rows = parse_matrix(matrix_lines)
    item_rows = []
    if 'evidence matrix' in fields:
        if header is None:
            errors.append(f'{label}: evidence matrix table not found')
        else:
            if [_clean(c) for c in header] != list(canonical_fields):
                errors.append(f'{label}: evidence matrix columns must be exactly {list(canonical_fields)}')
            if not rows:
                errors.append(f'{label}: evidence matrix has no evidence items')
    for index, cells in enumerate(rows, 1):
        if len(cells) != len(canonical_fields):
            errors.append(f'{label}: evidence item #{index} has {len(cells)} fields, '
                          f'expected {len(canonical_fields)}')
            continue
        row = {name: _clean(cell) for name, cell in zip(canonical_fields, cells)}
        item_rows.append(row)
        status = row.get('status', '').upper()
        if status not in status_values:
            errors.append(f'{label}: evidence item #{index} has unknown status: '
                          f'{row.get("status") or "(empty)"}')
            continue
        ac_id = row.get('ac_id', '')
        if ac_id not in acs:
            errors.append(f'{label}: evidence item #{index} references unknown acceptance '
                          f'criterion: {ac_id or "(empty)"}')
        if status == 'NOT_APPLICABLE':
            notes = row.get('notes', '')
            if not notes or notes.lower() in ('(none)', 'none', 'n/a'):
                errors.append(f'{label}: evidence item #{index} is NOT_APPLICABLE without a reason in notes')
        else:
            if not row.get('actual'):
                errors.append(f'{label}: evidence item #{index} has empty actual for status {status}')
            if not row.get('commit_sha'):
                errors.append(f'{label}: evidence item #{index} has empty commit_sha for status {status}')
            elif head_sha and row['commit_sha'].lower() != head_sha.lower():
                errors.append(f'{label}: evidence item #{index} commit_sha does not match the '
                              f'declared evidence head SHA')
        if status == 'PASS' and NOT_RUN_MARKERS.search(f"{row.get('actual', '')} {row.get('notes', '')}"):
            errors.append(f'{label}: evidence item #{index} is PASS but reports not-run/blocked evidence')

    for ac_id, checked in acs.items():
        ac_rows = [r for r in item_rows if r.get('ac_id') == ac_id]
        if not ac_rows:
            errors.append(f'{label}: acceptance criterion {ac_id} has no evidence item')
            continue
        if not checked:
            continue
        bad = sorted({r.get('status', '').upper() for r in ac_rows
                      if r.get('status', '').upper() in NON_PASS_STATUSES})
        if bad:
            errors.append(f'{label}: acceptance criterion {ac_id} is marked as met but has '
                          f'{", ".join(bad)} evidence')
        if not any(r.get('status', '').upper() == 'PASS' for r in ac_rows):
            errors.append(f'{label}: acceptance criterion {ac_id} is marked as met without PASS evidence')
    errors.extend(resource_declaration_errors(text, label))
    if require_docs_impact:
        errors.extend(docs_impact_errors(text, label))
    return errors


def _drift_module():
    """Load scripts/check_drift.py by path (manifest rules live only there)."""
    path = Path(__file__).resolve().parent / 'check_drift.py'
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location('check_drift_for_validate_kit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _listed_manifest_paths(manifest):
    """Paths a manifest lists, tolerating a malformed `files` value.

    Structure errors are reported separately; this helper only has to stay
    iterable so the REQUIRED-set comparison cannot raise on a broken manifest.
    """
    files = manifest.get('files') if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        return []
    return [entry.get('path') for entry in files
            if isinstance(entry, dict) and isinstance(entry.get('path'), str)]


def check_release_manifest(root, scan_errors=None):
    """Validate MANIFEST.json when the tree carries one (a built package does).

    The repository root has no MANIFEST.json, so this returns no errors there and
    the pre-release workflow keeps working on a raw checkout.

    Strictness follows the tree (see `tree_mode`). A pristine package and an
    upstream checkout must match every hash and size. A downstream project keeps
    the official manifest as its baseline while its own files legitimately
    diverge, so only the manifest contract is enforced there — structure,
    categories, path legality, the REQUIRED set and the presence of every listed
    file. Byte drift is reported by `scripts/check_drift.py` instead, so a local
    fix does not make `validate_kit` unusable.
    """
    manifest_path = Path(root) / MANIFEST_DOC
    if not manifest_path.is_file():
        return []
    drift = _drift_module()
    if drift is None:
        return [f'{MANIFEST_DOC} is present but scripts/check_drift.py is missing, '
                'so the manifest cannot be validated']
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return [f'{MANIFEST_DOC} is not valid JSON: {exc}']
    root = Path(root)
    mode_errors = []
    downstream = tree_mode(root, mode_errors) == TREE_DOWNSTREAM
    if mode_errors:
        if scan_errors is not None:
            scan_errors.extend(mode_errors)
        return mode_errors
    if downstream:
        errors = [f'{MANIFEST_DOC}: {reason}'
                  for reason in drift.manifest_structure_errors(manifest)]
    else:
        errors = [f'{MANIFEST_DOC}: {reason}'
                  for reason in drift.validate_manifest(root, manifest)]
    listed = _listed_manifest_paths(manifest)
    for rel in sorted(set(REQUIRED) - set(listed)):
        errors.append(f'{MANIFEST_DOC} does not list required file: {rel}')
    for rel in sorted(set(listed) - set(REQUIRED)):
        errors.append(f'{MANIFEST_DOC} lists a file that is not in REQUIRED: {rel}')
    if downstream:
        # Hashes are not compared here, but the manifest must still describe the
        # file set of this tree: a listed path that is gone is a broken baseline.
        for rel in sorted(set(listed)):
            if not (root / rel).is_file():
                errors.append(f'{MANIFEST_DOC} lists a file that is missing on disk: {rel}')
    return errors


def template_structure_errors(root=ROOT):
    """Missing contract template, missing required heading, or a broken relative link.

    A missing file is reported here as well as by the REQUIRED loop: the two
    checks answer different questions ("is the artifact shipped?" vs "does it
    still ask the required questions?"), and a caller that only imports this
    function should not get a silent pass.
    """
    errors, root = [], Path(root)
    for rel, sections in TEMPLATE_SECTIONS.items():
        path = root / rel
        if not path.is_file():
            errors.append(f'missing required template: {rel}')
            continue
        text = path.read_text(encoding='utf-8')
        headings = [match.group('text').lower() for match in HEADING_RE.finditer(text)]
        for section in sections:
            if not any(section.lower() in heading for heading in headings):
                errors.append(f'{rel} is missing required section: {section}')
        for target in markdown_links(text):
            if target.startswith(EXTERNAL_LINK_PREFIXES):
                continue
            target = target.split('#', 1)[0]
            if target and not (path.parent / target).exists():
                errors.append(f'{rel}: broken relative link: {target}')
    return errors


# --- Document consistency gates (Issue #154) ----------------------------------
# Deterministic, report-only checks: they never rewrite a document, never read
# the network and never guess a GitHub Issue state. A task status that cannot be
# proved from repository content is printed for manual verification instead of
# being counted as verified. The gate contract is described in
# docs/testing/document-consistency-gates.md.
REPO_MAP_DOC = 'docs/architecture/repository-map.md'
LIFECYCLE_DOC = 'docs/architecture/document-lifecycle.md'
PLAN_DOC = 'docs/implementation-plan.md'
RELEASE_DOC = 'release/README.md'
REVIEW_CHECKLIST_DOC = 'templates/review-checklist.md'

# `mixed` marks a container row (today `docs/`) that indexes a per-section
# inventory; it proves nothing by itself, so every document below it is still
# registered individually. Source and test files are covered by their directory
# rows on purpose (see document-lifecycle.md §4.4), so adding a normal source
# file never forces a map edit.
MAP_PURPOSE_TAGS = ('authoritative', 'reference', 'implementation', 'derived-local',
                    'outdated', 'mixed')
MAP_LIFECYCLE_STATES = ('PROPOSED', 'ACTIVE', 'IMPLEMENTED', 'DROPPED', 'SUPERSEDED',
                        'ARCHIVED')
MAP_EXCEPTION_HEADING = '例外登记'
# Derived / private paths that are exempt from registration by construction. The
# map's exception section must still name them, so the exemption is written down
# instead of being a silent hole in the scanner.
MAP_EXEMPT_PREFIXES = ('.local/', '.orchestration/', '.worktrees/', '.workbuddy/',
                       '__pycache__/', '.pytest_cache/', '.mypy_cache/', 'docs/images/')
KEY_ROOT_FILES = ('AGENTS.md', 'LICENSE.md', 'README.md', 'VERSION.md')
KEY_FILE_ROOTS = ('docs', 'editions', 'templates')

DOC_IMPACT_FIELD = 'documentation impact'
DOC_IMPACT_STATUSES = ('UPDATE', 'NO_CHANGE', 'NOT_APPLICABLE')
DOC_IMPACT_COLUMNS = ('path', 'status', 'change or reason')
EMPTY_CELL_MARKERS = ('', '(none)', 'none', 'n/a', '-', '—')

# Task state vocabulary. READY / DISPATCHED / ... are defined once in the core
# contract (`kit/orchestration.md` 4.1); OPEN / CLOSED / PLANNED are planning
# words a phase plan may use for its rows. An unknown token is a typo or a second
# vocabulary.
PLAN_STATUS_VALUES = (
    'PLANNED', 'READY', 'DISPATCHED', 'IN_PROGRESS', 'PR_OPEN', 'MASTER_REVIEW',
    'APPROVED', 'MERGED', 'VERIFIED', 'BLOCKED', 'FAILED', 'CANCELED', 'OPEN',
    'CLOSED', 'NOT_RUN', 'UNKNOWN', 'NEEDS_HUMAN_DECISION',
)
PLAN_STATUS_RE = re.compile(r'(?<![A-Za-z0-9_])[A-Z][A-Z_]{2,}(?![A-Za-z0-9_])')
PLAN_UNPROVEN_NOTE = '待人工核验'

# Directories that hold generated, vendored or private state. A document scan
# must not treat them as project documentation, and registration coverage must
# not demand a row for them.
DOC_SCAN_EXCLUDED_DIRS = frozenset({
    '.git', 'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', '.orchestration', '.local', '.worktrees', '.workbuddy',
})
# Release-path segments that must never reach a package. `release/` and
# `scripts/build_release.py` cover the builder itself; `docs/` is a single-file
# allowlist rather than a blanket ban (the core contract lives there).
RELEASE_FORBIDDEN_SEGMENTS = frozenset({
    '.orchestration', 'release', '.worktrees', '.workbuddy', '.git', '.local',
})
RELEASE_DOC_TOKENS = ('PACKAGE_DOC_PATHS', 'REQUIRED', 'MANIFEST.json')


def _section_body(text, needle):
    """Body of every heading containing *needle*, joined; up to the next heading.

    A document may carry more than one related section (e.g. a per-directory
    exception note and a consolidated exception register); the check reads all of
    them instead of silently stopping at the first heading.
    """
    lines = text.splitlines()
    bodies, index = [], 0
    while index < len(lines):
        match = re.match(r'^(#{1,6})\s+(.*)$', lines[index])
        if not match or needle not in match.group(2):
            index += 1
            continue
        level, index = len(match.group(1)), index + 1
        body = []
        while index < len(lines):
            inner = re.match(r'^(#{1,6})\s+', lines[index])
            if inner and len(inner.group(1)) <= level:
                break
            body.append(lines[index])
            index += 1
        bodies.append('\n'.join(body))
    return '\n'.join(bodies)


def _first_token(cell):
    """First backticked token of a map cell, else its first plain word."""
    match = re.search(r'`([^`]+)`', cell or '')
    if match:
        return match.group(1).strip()
    plain = re.sub(r'[*\[\]]', '', cell or '').strip()
    return plain.split()[0] if plain else ''


def _map_cell_paths(cell):
    """[(repo_relative_path, kind)] for every relative link in a map cell."""
    paths = []
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', cell or ''):
        target = target.strip()
        if not target or target.startswith(EXTERNAL_LINK_PREFIXES):
            continue
        target = target.split('#', 1)[0].strip()
        if not target:
            continue
        normalised = posixpath.normpath(
            posixpath.join(posixpath.dirname(REPO_MAP_DOC), target))
        if normalised in ('', '.', '..') or normalised.startswith('../'):
            continue
        if '*' in normalised:
            paths.append((normalised, 'glob'))
        elif target.endswith('/'):
            paths.append((normalised.rstrip('/') + '/', 'dir'))
        else:
            paths.append((normalised, 'file'))
    return paths


def map_registration_rows(text):
    """Registration rows of the repository map (path / purpose / lifecycle / scope)."""
    rows = []
    for header, body in _tables(text):
        labels = [field_label(cell) for cell in header]
        if '路径' not in labels or '生命周期' not in labels:
            continue
        column = {name: labels.index(name) for name in labels}
        for cells in body:
            if len(cells) <= column['生命周期']:
                continue
            for path, kind in _map_cell_paths(cells[column['路径']]):
                rows.append({
                    'path': path,
                    'kind': kind,
                    'purpose': _first_token(cells[column['用途']] if '用途' in column else ''),
                    'lifecycle': _first_token(cells[column['生命周期']]),
                    'distribution': cells[column['分发']] if '分发' in column else '',
                    'responsibility': cells[-1],
                })
    return rows


def _registered(rel, rows):
    for row in rows:
        if row['kind'] == 'file' and row['path'] == rel:
            return True
        if row['kind'] == 'dir' and rel.startswith(row['path'].rstrip('/') + '/'):
            return True
        if row['kind'] == 'glob' and fnmatch.fnmatch(rel, row['path']):
            return True
    return False


def _map_exempt(rel):
    return any(rel == prefix.rstrip('/') or rel.startswith(prefix)
               for prefix in MAP_EXEMPT_PREFIXES)


def repository_map_coverage_errors(root, rows):
    """Key files that no registration row covers.

    Only the governance surface is covered: root entry / contract files and every
    file under `docs/`, `editions/` and `templates/`. `scripts/` and `tests/` are
    registered as directory groups, so a host project never has to list its own
    source files in this upstream map.
    """
    root, errors, scan_errors = Path(root), [], []
    covering = [row for row in rows if row['purpose'] != 'mixed']
    for path in _scan_files(root, root, scan_errors, DOC_SCAN_EXCLUDED_DIRS):
        rel = path.relative_to(root).as_posix()
        if _map_exempt(rel):
            continue
        if rel not in KEY_ROOT_FILES and rel.split('/')[0] not in KEY_FILE_ROOTS:
            continue
        if not _registered(rel, covering):
            errors.append(f'{rel} is not registered in {REPO_MAP_DOC}; add a row with its '
                          'purpose, lifecycle and distribution scope, or write an explicit '
                          f'exception under "{MAP_EXCEPTION_HEADING}"')
    errors.extend(scan_errors)
    return errors


def repository_map_errors(root=ROOT):
    """Registration, tags, existence and the written exception rules of the map."""
    root = Path(root)
    path = root / REPO_MAP_DOC
    if not path.is_file():
        return [f'missing repository map: {REPO_MAP_DOC}']
    text = path.read_text(encoding='utf-8')
    rows = map_registration_rows(text)
    if not rows:
        return [f'{REPO_MAP_DOC}: no registration rows found (expected a table whose header '
                'carries 路径 / 用途 / 生命周期 columns)']
    errors = []
    for row in rows:
        purpose, lifecycle = row['purpose'], row['lifecycle']
        if purpose not in MAP_PURPOSE_TAGS:
            errors.append(f'{REPO_MAP_DOC}: {row["path"]} declares unknown purpose tag: '
                          f'{purpose or "(empty)"}')
        if lifecycle not in MAP_LIFECYCLE_STATES:
            errors.append(f'{REPO_MAP_DOC}: {row["path"]} declares unknown lifecycle state: '
                          f'{lifecycle or "(empty)"}')
        target = root / row['path'].rstrip('/')
        if row['kind'] == 'dir':
            exists = target.is_dir()
        elif row['kind'] == 'glob':
            exists = target.parent.is_dir() and any(target.parent.glob(target.name))
        else:
            exists = target.is_file()
        if (not exists and lifecycle not in ('ARCHIVED', 'SUPERSEDED', 'DROPPED')
                and purpose != 'outdated'):
            errors.append(f'{REPO_MAP_DOC}: {row["path"]} is registered but missing on disk')
        responsibility = row['responsibility']
        if purpose == 'outdated' and not (markdown_links(responsibility)
                                          or '无替代' in responsibility):
            errors.append(f'{REPO_MAP_DOC}: {row["path"]} is outdated without a successor or an '
                          'explicit 无替代 decision; a stale tag must never stand alone')
        if lifecycle in ('ARCHIVED', 'SUPERSEDED') and (
                ('保留' not in responsibility and '归档' not in responsibility)
                or not (markdown_links(responsibility) or '无替代' in responsibility)):
            errors.append(f'{REPO_MAP_DOC}: {row["path"]} {lifecycle} must state retention and a '
                          'successor (link or explicit 无替代)')
    if not re.search(r'^#{1,4}\s+.*' + MAP_EXCEPTION_HEADING, text, re.M):
        errors.append(f'{REPO_MAP_DOC}: missing an {MAP_EXCEPTION_HEADING} section: the '
                      'derived-local / private / archived exceptions must be written down '
                      '(derived-local, .local/, .orchestration/)')
    else:
        body = _section_body(text, MAP_EXCEPTION_HEADING)
        if 'derived-local' not in body:
            errors.append(f'{REPO_MAP_DOC}: {MAP_EXCEPTION_HEADING} must name the derived-local '
                          'exception and the paths that are exempt from registration')
        if '.local' not in body and '.orchestration' not in body:
            errors.append(f'{REPO_MAP_DOC}: {MAP_EXCEPTION_HEADING} must name the private paths '
                          'that are exempt from registration (.local/, .orchestration/)')
    errors.extend(repository_map_coverage_errors(root, rows))
    return errors


def legacy_path_registry(text):
    """Rows of the 旧路径登记 table in the lifecycle document."""
    entries, rows = [], _table_after(text, '旧路径登记')
    if not rows:
        return entries
    for cells in rows:
        if len(cells) < 3:
            continue
        old, new = _backticked(cells[0]), _backticked(cells[1])
        if not old or not new:
            continue
        allowed = [item.strip().strip('/') for item in re.findall(r'`([^`]+)`', cells[2])]
        note = cells[4] if len(cells) > 4 else ''
        deferral, deferral_error = _deferral_state(note)
        entries.append({'old': old.strip('/').strip(), 'new': new.strip(),
                        'allowed': [item for item in allowed if item],
                        'deferral': deferral, 'deferral_error': deferral_error})
    return entries


# A deferral is a temporary, auditable exception, not a keyword that switches
# the checks off. Only the exact marker `` `DEFERRED`: <reason> `` at the start of
# the 说明 cell defers anything, and the reason must name the authorising task
# (``#238``). Anything else that mentions the word DEFERRED is malformed and is
# reported instead of silently disabling (or silently running) the strict checks.
DEFERRAL_MARKER_RE = re.compile(r'^\s*`DEFERRED`\s*[:：]\s*(?P<reason>.+?)\s*$')
DEFERRAL_MENTION_RE = re.compile(r'DEFERRED', re.IGNORECASE)
DEFERRAL_AUTH_RE = re.compile(r'#\d+')


def _deferral_state(note):
    """Return ``(state, detail)`` for one 说明 cell: none / valid / malformed."""
    if not note or not DEFERRAL_MENTION_RE.search(note):
        return 'none', ''
    match = DEFERRAL_MARKER_RE.match(note)
    if not match:
        return ('malformed',
                'the deferral marker must be exactly `DEFERRED` at the start of the 说明 '
                'cell, followed by ":" or "：" and a reason')
    if not DEFERRAL_AUTH_RE.search(match.group('reason')):
        return ('malformed',
                'the deferral reason must cite the authorising task (for example #238)')
    return 'valid', ''


def legacy_path_errors(root=ROOT):
    """A migrated path may only appear where the migration registry allows it."""
    root = Path(root)
    registry_path = root / LIFECYCLE_DOC
    if not registry_path.is_file():
        return [f'missing legacy path registry: {LIFECYCLE_DOC}']
    entries = legacy_path_registry(registry_path.read_text(encoding='utf-8'))
    if not entries:
        return [f'{LIFECYCLE_DOC}: no 旧路径登记 table found (columns: 旧路径 / 替代路径 / '
                '允许出现的文件 / 说明)']
    errors, scan_errors = [], []
    for entry in entries:
        old, new = entry['old'], entry['new']
        state = entry.get('deferral', 'none')
        if state == 'malformed':
            errors.append(f'{LIFECYCLE_DOC}: legacy {old} has a malformed deferral marker: '
                          f'{entry.get("deferral_error") or "invalid marker"}')
            continue
        if not (root / new).exists():
            errors.append(f'{LIFECYCLE_DOC}: legacy {old} successor {new} does not exist')
        for rel in entry['allowed']:
            if not (root / rel).exists():
                errors.append(f'{LIFECYCLE_DOC}: legacy {old} allowed file {rel} does not exist')
        if state == 'valid':
            # Exactly three migrated-state checks are deferred, and only for this
            # row: the old path may still exist, may still be a release path, and
            # may still be referenced anywhere until the migration is executed.
            # A deferral whose old path is already gone is stale and must be
            # cleaned up instead of staying as an open-ended exception.
            if not (root / old).exists():
                errors.append(f'{LIFECYCLE_DOC}: legacy {old} is marked deferred but no longer '
                              'exists on disk; remove the deferral and close the migration row')
            continue
        if (root / old).exists():
            errors.append(f'{LIFECYCLE_DOC}: legacy path {old} still exists on disk; keep one '
                          'authority instead of a second copy')
        if any(rel == old or rel.startswith(old + '/') for rel in REQUIRED):
            errors.append(f'{LIFECYCLE_DOC}: legacy path {old} must not be a release path')
        allowed = set(entry['allowed'])
        for path in _scan_files(root, root, scan_errors, DOC_SCAN_EXCLUDED_DIRS):
            if path.suffix.lower() != '.md':
                continue
            rel = path.relative_to(root).as_posix()
            if rel == LIFECYCLE_DOC:
                continue  # the registry itself has to name the old path
            content = CODE_BLOCK_RE.sub(
                '', path.read_text(encoding='utf-8', errors='replace'))
            if old in content and rel not in allowed:
                errors.append(f'{rel}: legacy path {old} is not registered for this file in '
                              f'{LIFECYCLE_DOC} §7.1')
    errors.extend(scan_errors)
    return errors


def docs_impact_errors(text, label='task packet'):
    """The documentation impact record: three statuses, each with a reason."""
    fields = parse_task_packet(text)
    if field_label(DOC_IMPACT_FIELD) not in fields:
        return [f'{label}: missing required field: {DOC_IMPACT_FIELD}']
    header, rows = parse_matrix(fields[field_label(DOC_IMPACT_FIELD)]['lines'])
    if header is None:
        return [f'{label}: documentation impact table not found']
    errors = []
    if [_clean(cell) for cell in header] != list(DOC_IMPACT_COLUMNS):
        errors.append(f'{label}: documentation impact columns must be exactly '
                      f'{list(DOC_IMPACT_COLUMNS)}')
    if not rows:
        errors.append(f'{label}: documentation impact lists no documents')
    seen = set()
    for index, cells in enumerate(rows, 1):
        if len(cells) != len(DOC_IMPACT_COLUMNS):
            errors.append(f'{label}: documentation impact row #{index} has {len(cells)} fields, '
                          f'expected {len(DOC_IMPACT_COLUMNS)}')
            continue
        path, status, detail = (_clean(cell) for cell in cells)
        if not path:
            errors.append(f'{label}: documentation impact row #{index} has an empty path')
        elif path.startswith('/') or '..' in Path(path).parts or ':' in path[:3]:
            errors.append(f'{label}: documentation impact row #{index} path {path} must be '
                          'repo-relative')
        elif path in seen:
            errors.append(f'{label}: documentation impact lists {path} more than once')
        else:
            seen.add(path)
        if status not in DOC_IMPACT_STATUSES:
            errors.append(f'{label}: documentation impact row #{index} has unknown status: '
                          f'{status or "(empty)"} (allowed: {", ".join(DOC_IMPACT_STATUSES)})')
            continue
        empty = not detail.strip() or detail.strip().lower() in EMPTY_CELL_MARKERS
        if status == 'UPDATE' and empty:
            errors.append(f'{label}: documentation impact row #{index} is UPDATE without a '
                          'change description')
        elif status != 'UPDATE' and empty:
            errors.append(f'{label}: documentation impact row #{index} is {status} without a '
                          'reason')
    return errors


def docs_impact_template_errors(root=ROOT):
    """The shipped templates must carry the impact contract, not only mention it."""
    root, errors = Path(root), []
    packet = root / TASK_PACKET_DOC
    if not packet.is_file():
        errors.append(f'missing required template: {TASK_PACKET_DOC}')
    else:
        text = packet.read_text(encoding='utf-8')
        if DOC_IMPACT_FIELD not in text.lower():
            errors.append(f'{TASK_PACKET_DOC} does not carry the documentation impact field')
        if '| ' + ' | '.join(DOC_IMPACT_COLUMNS) + ' |' not in text:
            errors.append(f'{TASK_PACKET_DOC} does not declare the impact table header '
                          f'{list(DOC_IMPACT_COLUMNS)}')
        for status in DOC_IMPACT_STATUSES:
            if status not in text:
                errors.append(f'{TASK_PACKET_DOC} does not document the impact status {status}')
    checklist = root / REVIEW_CHECKLIST_DOC
    if not checklist.is_file():
        errors.append(f'missing required template: {REVIEW_CHECKLIST_DOC}')
    else:
        text = checklist.read_text(encoding='utf-8')
        covered = [line for line in text.splitlines()
                   if line.lstrip().startswith('- [ ]')
                   and all(status in line for status in DOC_IMPACT_STATUSES)]
        if not covered:
            errors.append(f'{REVIEW_CHECKLIST_DOC} has no checklist item covering the '
                          'documentation impact record (UPDATE / NO_CHANGE / NOT_APPLICABLE)')
    return errors


def plan_status_table(text):
    """(header, rows, status column) of the task status table, if it exists."""
    for header, rows in _tables(text):
        labels = [field_label(cell) for cell in header]
        if '任务' in labels and '状态' in labels:
            return header, rows, labels.index('状态')
    return None, [], None


def _claims_verified(status):
    for match in re.finditer('VERIFIED', status):
        prefix = status[max(0, match.start() - 4):match.start()]
        if '未' in prefix or 'not' in prefix.lower():
            continue
        return True
    return False


def _evidence_pointer(cells):
    # The first cell is the task itself (usually its Issue link); evidence must
    # come from another column, otherwise every row would look "proven".
    row = ' '.join(cells[1:])
    if re.search(r'https?://[^\s)]+/(?:pull|issues|releases|commit)/', row):
        return True
    if re.search(r'\b[0-9a-f]{40}\b', row):
        return True
    return any(target and not target.startswith(EXTERNAL_LINK_PREFIXES)
               for target in markdown_links(row))


def plan_status_errors(root=ROOT):
    """Only the statically provable part of the phase plan's status table.

    An unknown state token or a completion claim with no pointer is a defect a
    file can prove. Whether a GitHub Issue is really `VERIFIED` is not, and is
    deliberately never guessed here.
    """
    path = Path(root) / PLAN_DOC
    if not path.is_file():
        return [f'missing phase plan: {PLAN_DOC}']
    header, rows, column = plan_status_table(path.read_text(encoding='utf-8'))
    if header is None:
        return [f'{PLAN_DOC}: no task status table found (expected a table with 任务 and 状态 '
                f'columns; status values are defined in {CORE_DOC} 4.1)']
    errors = []
    for cells in rows:
        if column >= len(cells):
            errors.append(f'{PLAN_DOC}: a task row is shorter than its status column')
            continue
        task = _clean(cells[0]) or '(unnamed task)'
        status = cells[column]
        for token in PLAN_STATUS_RE.findall(status):
            if token not in PLAN_STATUS_VALUES:
                errors.append(f'{PLAN_DOC}: status {token} is not a known task state '
                              f'(see {CORE_DOC} 4.1)')
        if _claims_verified(status) and not _evidence_pointer(cells):
            errors.append(f'{PLAN_DOC}: {task} claims VERIFIED without an evidence pointer '
                          '(Issue / PR / commit SHA / repository link)')
    return errors


def plan_status_manual_lines(root=ROOT):
    """Status rows the repository cannot prove, reported for manual verification."""
    path = Path(root) / PLAN_DOC
    if not path.is_file():
        return []
    header, rows, column = plan_status_table(path.read_text(encoding='utf-8'))
    if header is None or not rows:
        return []
    tasks = []
    for cells in rows:
        if not cells:
            continue
        match = re.search(r'/issues/(\d+)', cells[0]) or re.search(r'#(\d+)', cells[0])
        tasks.append(f'#{match.group(1)}' if match else _clean(cells[0]))
    if not tasks:
        return []
    return [f'{PLAN_DOC}: {PLAN_UNPROVEN_NOTE} — {len(tasks)} task status row(s) '
            f'({", ".join(tasks)}) depend on live GitHub Issue / PR state; repository '
            'content cannot prove them, so this check neither guesses nor edits the plan.']


def release_rule_errors(required, package_doc_paths, forbidden_paths, forbidden_segments,
                        build_script='scripts/build_release.py'):
    """One rule set for REQUIRED, the manifest and the package self-check."""
    segments = set(forbidden_segments)
    package_doc_paths = set(package_doc_paths)
    errors, seen = [], set()
    for rel in required:
        if rel in seen:
            errors.append(f'REQUIRED lists a path more than once: {rel}')
        seen.add(rel)
        parts = rel.split('/')
        if any(part in segments for part in parts):
            errors.append(f'REQUIRED contains a forbidden path segment: {rel}')
        if 'docs' in parts and rel not in package_doc_paths:
            errors.append(f'REQUIRED contains an upstream design / archive document that must '
                          f'not ship: {rel}')
    if build_script not in set(forbidden_paths):
        errors.append(f'{build_script} must be listed as a forbidden release path')
    for rel in sorted(package_doc_paths):
        if 'docs' not in rel.split('/'):
            errors.append(f'the package docs allowlist may only name docs/ files: {rel}')
        elif rel not in seen:
            errors.append(f'the package docs allowlist names a file that is not in REQUIRED: {rel}')
    for segment in ('.orchestration', 'release', '.worktrees', '.workbuddy'):
        if segment not in segments:
            errors.append(f'release must forbid the path segment {segment}')
    return errors


def _release_builder(root):
    """Import ``scripts/build_release.py`` from *root*, or None when absent."""
    path = Path(root) / 'scripts' / 'build_release.py'
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location('build_release_for_validate_kit', path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - an unimportable builder fails the check closed
        return None
    return module


def release_consistency_errors(root=ROOT):
    """The release builder's rules and the published standard must agree with REQUIRED."""
    root = Path(root)
    builder = _release_builder(root)
    if builder is None:
        # The builder is repository-only and never ships, so a package or a
        # downstream project has nothing to compare here.
        return []
    errors = release_rule_errors(
        REQUIRED, getattr(builder, 'PACKAGE_DOC_PATHS', ()),
        getattr(builder, 'FORBIDDEN_PATHS', ()),
        getattr(builder, 'FORBIDDEN_SEGMENTS', ()),
    )
    readme = root / RELEASE_DOC
    if not readme.is_file():
        errors.append(f'missing release standard: {RELEASE_DOC}')
    else:
        text = readme.read_text(encoding='utf-8')
        for token in RELEASE_DOC_TOKENS:
            if token not in text:
                errors.append(f'{RELEASE_DOC} must document the package rule {token}')
    return errors


def documentation_consistency_errors(root=ROOT):
    """All document consistency gates that apply to the tree being validated."""
    root = Path(root)
    errors = []
    errors.extend(docs_impact_template_errors(root))
    errors.extend(release_consistency_errors(root))
    if tree_mode(root) == TREE_UPSTREAM:
        errors.extend(repository_map_errors(root))
        errors.extend(legacy_path_errors(root))
        errors.extend(plan_status_errors(root))
    return errors


def validate_kit(root=ROOT, project_ready=False):
    errors = []
    root = Path(root)
    isolated = is_isolated_layout(root)

    for rel in ISOLATED_REQUIRED if isolated else REQUIRED:
        if not (root / rel).is_file():
            errors.append(f'missing required file: {rel}')

    if isolated:
        errors.extend(isolated_layout_errors(root))

    # Since Format 1.4 the design-goal authority lives at docs/PLAN.md. A root
    # PLAN.md is the legacy (<=1.3) location: it must be moved, not kept, or the
    # project ends up with two competing design-goal authorities. init refuses
    # to seed docs/PLAN.md while it exists, so validation fails closed too.
    if (root / 'PLAN.md').is_file():
        errors.append("legacy root PLAN.md found: move it to docs/PLAN.md (`git mv PLAN.md docs/PLAN.md`) "
                      'so design goals keep a single authority — see README section 10')
    if (root / 'ORCHESTRATION-PROJECT.md').is_file():
        errors.append("legacy root ORCHESTRATION-PROJECT.md found: move it to docs/PROJECT.md "
                      '(`git mv ORCHESTRATION-PROJECT.md docs/PROJECT.md`) — see docs/upgrade.md')

    errors.extend(template_structure_errors(root))

    # Document consistency gates: file responsibility / lifecycle registration,
    # legacy path usage, the documentation impact record and the phase plan.
    # Report-only — nothing here rewrites a document or guesses an Issue state.
    errors.extend(documentation_consistency_errors(root))

    # Repository-only documents. Which ones are required depends on the tree:
    # a package carries no docs/, a downstream project only fills in
    # docs/PROJECT.md + docs/PLAN.md, an upstream checkout has the full set.
    errors.extend(required_doc_errors(root))

    # A built package also carries MANIFEST.json; validate it when present.
    # Hashes are strict in a package and in an upstream checkout, structural
    # only in a downstream project (see check_release_manifest). The isolated
    # layout has its own required set and host-root entry, checked above.
    if not isolated:
        errors.extend(check_release_manifest(root))

    # The two editions must expose the same public file set; github additionally
    # ships three templates. Comparing the *intersection* catches a file that
    # exists on only one side, while comparing each side against its expected set
    # catches strays.
    common_edition_files = ('master.md', 'execution.md')
    github_only_files = ('active-task-registry.md', 'issue-template.md', 'pr-template.md')
    edition_names = {}
    for edition in ('local', 'github'):
        directory = root / 'editions' / edition
        if not directory.is_dir():
            errors.append(f'missing edition directory: editions/{edition}')
            edition_names[edition] = set()
            continue
        try:
            actual = {p.name for p in directory.iterdir() if p.is_file()}
        except OSError as exc:
            code = exc.errno if isinstance(exc.errno, int) else 'unknown'
            errors.append(f'cannot enumerate editions/{edition} (errno {code})')
            edition_names[edition] = None
            continue
        edition_names[edition] = actual
        expected = set(common_edition_files) | (set(github_only_files) if edition == 'github' else set())
        for name in sorted(expected - actual):
            errors.append(f'editions/{edition} is missing file: {name}')
        for name in sorted(actual - expected):
            errors.append(f'editions/{edition} contains unexpected file: {name}')
    if edition_names.get('local') is not None and edition_names.get('github') is not None:
        shared = edition_names['local'] & edition_names['github']
        if shared != set(common_edition_files):
            errors.append('editions/local and editions/github disagree on the shared file set: '
                          f'intersection={sorted(shared)}')
    for name in github_only_files:
        if not (root / 'editions' / 'github' / name).is_file():
            errors.append(f'missing github-only template: editions/github/{name}')

    # The core orchestration document must really define the governance concepts it
    # is supposed to own: PR_OPEN (github-only state), merge gates G1-G6, runtime
    # capability contracts C1-C4 and the evidence matrix. This is a content check,
    # not a self-certification.
    core_path = root / CORE_DOC
    core_text = ''
    if core_path.is_file():
        core_text = core_path.read_text(encoding='utf-8')
        core_needles = ['PR_OPEN'] + [f'G{i}' for i in range(1, 7)] + [f'C{i}' for i in range(1, 5)]
        for needle in core_needles:
            if needle not in core_text:
                errors.append(f'{CORE_DOC} is missing required concept: {needle}')
    else:
        errors.append(f'missing core document: {CORE_DOC}')

    evidence_fields, status_values = canonical_evidence_fields(core_text)
    packet_fields = canonical_packet_fields(core_text)
    if not evidence_fields:
        errors.append(f'{CORE_DOC}: cannot read the evidence item field table '
                      f'(expected a table after "{EVIDENCE_FIELD_ANCHOR}")')
    if not status_values:
        errors.append(f'{CORE_DOC}: cannot read the allowed status values for evidence items')
    elif set(status_values) != set(STATUS_VALUES):
        errors.append(f'{CORE_DOC}: evidence status values {sorted(set(status_values))} '
                      f'do not match the contract {sorted(STATUS_VALUES)}')
    if not packet_fields:
        errors.append(f'{CORE_DOC}: cannot read the structured task packet field table '
                      f'(expected a table after "{PACKET_FIELD_ANCHOR}")')

    # Both edition templates must carry the same field names; the meaning is only
    # defined in the core document, so only the names are checked here.
    for rel in (TASK_PACKET_DOC, ISSUE_TEMPLATE_DOC):
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8')
        for name in list(evidence_fields) + list(packet_fields):
            if f'`{name}`' not in text:
                errors.append(f'{rel} does not carry the field `{name}` declared in {CORE_DOC}')

    # The optional named-resource carrier fields must exist where they are
    # documented, and their value must be readable when a task fills them in.
    for rel, names in ((TASK_PACKET_DOC, (RESOURCE_FIELD,)),
                       (ISSUE_TEMPLATE_DOC, (LOCKED_RESOURCE_FIELD,))):
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8')
        for name in names:
            if f'`{name}`' not in text:
                errors.append(f'{rel} does not carry the optional resource field `{name}`')

    samples = []
    packet_path = root / TASK_PACKET_DOC
    if packet_path.is_file():
        samples = extract_packet_samples(packet_path.read_text(encoding='utf-8'))
    if packet_path.is_file() and not samples:
        errors.append(f'{TASK_PACKET_DOC}: no ```task-packet samples found to validate')
    for sample in samples:
        errors.extend(validate_task_packet(
            sample['text'], evidence_fields, status_values,
            label=f'{TASK_PACKET_DOC} sample "{sample["name"]}"',
            structured_fields=packet_fields,
            require_docs_impact=True,
        ))

    # The edition selector is the routing key for the whole kit. It must be present
    # and explicit: silently defaulting would run a project on the wrong task carrier.
    config_path = root / 'docs/PROJECT.md'
    if config_path.is_file():
        config_text = config_path.read_text(encoding='utf-8')
        match = re.search(r'^\s*[-*]\s*`?edition`?\s*[:：]\s*`?([A-Za-z0-9_-]+)`?', config_text, re.M)
        if not match:
            errors.append('docs/PROJECT.md is missing the `edition` key '
                          "(expected `edition`: `local` or `github`)")
        else:
            edition_value = match.group(1)
            if edition_value not in ('local', 'github'):
                errors.append("docs/PROJECT.md `edition` must be 'local' or 'github', "
                              f"got {edition_value!r}")

    # Governance concepts are only meaningful in documentation. Scan document-class
    # files (*.md / *.txt) and skip code dirs and binaries so the check cannot be
    # satisfied by the validator's own source or by non-document assets.
    doc_suffixes = ('.md', '.txt')
    # Vendored dependency trees are not project documentation: a downstream
    # checkout may keep a JavaScript plugin's `node_modules/`, whose README
    # files belong to third parties and whose relative links do not resolve
    # against the project. Skip them in both document scans below.
    vendored_dirs = {'node_modules'}
    excluded_dirs = {'scripts', '.orchestration', '.git', '__pycache__'} | vendored_dirs
    doc_texts = []
    private_dirs = ISOLATED_PRIVATE_DIRS if isolated else frozenset()
    for p in _scan_files(root, root, errors, private_dirs | vendored_dirs):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.suffix.lower() not in doc_suffixes:
            continue
        if excluded_dirs & set(rel.parts):
            continue
        raw = p.read_text(encoding='utf-8', errors='replace')
        if '\x00' in raw:
            continue  # skip binary payloads
        doc_texts.append(raw)
    text_all = '\n'.join(doc_texts)
    for needle in ['PLANNED', 'DISPATCHED', 'MASTER_REVIEW', 'APPROVED', 'VERIFIED', 'NEEDS_HUMAN_DECISION']:
        if needle not in text_all:
            errors.append(f'missing expected governance concept: {needle}')
    # Relative Markdown links must resolve. Ignore anchors and external URLs.
    # The contract templates are already link-checked with template-scoped
    # messages by template_structure_errors(), so skip them here to avoid one
    # break being reported twice.
    for md in _scan_files(root, root, errors, private_dirs | vendored_dirs):
        if not md.match('*.md'):
            continue
        rel = md.relative_to(root)
        if rel.as_posix() in TEMPLATE_SECTIONS:
            continue
        # Keep this scan's established exclusions narrow: only vendored trees
        # are added, so project documents under `scripts/` are still checked.
        if vendored_dirs & set(rel.parts):
            continue
        content = md.read_text(encoding='utf-8')
        for target in markdown_links(content):
            if target.startswith(('http://', 'https://', 'mailto:', '#')):
                continue
            target = target.split('#', 1)[0]
            if target and not (md.parent / target).exists():
                # Report the repository-relative path in POSIX form on every
                # platform: the error text is an assertion target, and a host
                # separator (Windows `\`) would make the same break look like a
                # different document.
                errors.append(f'{rel.as_posix()}: broken relative link: {target}')
    if project_ready:
        cfg_path = root / 'docs' / 'PROJECT.md'
        if not cfg_path.is_file():
            errors.append('docs/PROJECT.md is missing: copy templates/project.md and replace '
                          'every FILL_IN placeholder (edition, allowed paths, verification commands)')
        else:
            cfg = cfg_path.read_text(encoding='utf-8')
            missing = cfg.count('FILL_IN')
            if missing:
                errors.append(f'docs/PROJECT.md has {missing} FILL_IN placeholders')
    return errors, len(samples)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-ready', action='store_true',
                        help='fail while docs/PROJECT.md still has FILL_IN placeholders')
    parser.add_argument('--task-packet', metavar='PATH',
                        help='validate one filled task packet instead of the whole kit')
    args = parser.parse_args(argv)

    if args.task_packet:
        core_text = read_text(ROOT / CORE_DOC) if (ROOT / CORE_DOC).is_file() else ''
        evidence_fields, status_values = canonical_evidence_fields(core_text)
        packet_fields = canonical_packet_fields(core_text)
        errors = validate_task_packet(
            read_text(args.task_packet), evidence_fields, status_values,
            label=args.task_packet, structured_fields=packet_fields,
            require_docs_impact=True,
        )
        if errors:
            print('TASK PACKET VALIDATION FAILED')
            print('\n'.join(f'- {e}' for e in errors))
            return 1
        print(f'Task packet validation passed: {args.task_packet} '
              f'({len(evidence_fields)} evidence fields, {len(packet_fields)} structured fields).')
        return 0

    errors, sample_count = validate_kit(ROOT, project_ready=args.project_ready)
    if errors:
        print('VALIDATION FAILED')
        print('\n'.join(f'- {e}' for e in errors))
        # Status rows the repository cannot prove are still reported on failure:
        # a failed structural check must not hide what needs a human.
        manual = plan_status_manual_lines(ROOT)
        if manual:
            print('\n'.join(manual))
        return 1
    scan_errors = []
    mode = tree_mode(ROOT, scan_errors)
    if scan_errors:
        print('VALIDATION FAILED')
        print('\n'.join(f'- {e}' for e in scan_errors))
        return 1
    isolated = is_isolated_layout(ROOT)
    documents = 0 if mode == TREE_PACKAGE else len(
        REQUIRED_DOCS if mode == TREE_UPSTREAM else PROJECT_DOCS)
    required_count = len(ISOLATED_REQUIRED) if isolated else len(REQUIRED)
    layout = 'isolated (host: agentorch/ + root AGENTS.md)' if isolated else f'tree mode {mode}'
    print(f'Kit validation passed ({required_count} required files checked; '
          f'{layout}; '
          f'editions local/github share 2 files, github adds 3 templates; '
          f'{documents} repository-only document(s) checked; '
          f'{sample_count} task packet samples validated).')
    manual = plan_status_manual_lines(ROOT)
    if manual:
        print('\n'.join(manual))
    return 0


if __name__ == '__main__':
    sys.exit(main())
