"""Self-improvement trigger routing and improvement planning (#50).

This module answers two questions and nothing else:

``route_request``
    Does a user message start the self-improvement workflow, ask for an
    explanation, ask for the *update engine* (a different feature) or none of
    those? The wording table is a routing rule set, not a language model: it is
    deliberately boring so a false positive is visible in the tests.

``plan_tasks``
    Given lesson records that already exist (``scripts/self_improvement/lessons``)
    and an explicit, caller-written task specification, produce an improvement
    proposal. A proposal is ``READY`` only when the lesson is eligible, the task
    packet is complete, dependencies are verified and risk leads are either
    absent or resolved by a trusted diff. A lead without a diff stays
    ``BLOCKED`` (``risk_level: NEEDS_EVIDENCE``); only an evidenced, actual
    semantic change reaches ``NEEDS_HUMAN_DECISION``. Everything else is
    ``NO_TASK`` / ``BLOCKED``; nothing is invented from source text.

What the module does **not** do: adopt a lesson, invent a root cause or an
acceptance criterion, create an Issue / task, run a command, or approve a
high-risk change. Field meanings (``negative_cases`` ... ) and the G1–G6 gates
live only in ``kit/orchestration.md`` 4.1 and are referenced, never restated.

Standard library only; no network, no subprocess, no carrier.
"""
from __future__ import annotations

import posixpath
import re
import unicodedata

from . import lessons

# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #

ROUTE_SELF_IMPROVEMENT = 'SELF_IMPROVEMENT'
ROUTE_UPDATE_AGENTORCH = 'UPDATE_AGENTORCH'
ROUTE_EXPLAIN_ONLY = 'EXPLAIN_ONLY'
ROUTE_UNRELATED = 'UNRELATED'

ROUTES = (ROUTE_SELF_IMPROVEMENT, ROUTE_UPDATE_AGENTORCH, ROUTE_EXPLAIN_ONLY,
          ROUTE_UNRELATED)

# Longest phrase first: "进行自我完善" contains "自我完善".
SELF_IMPROVEMENT_PHRASES = (
    ('进行自我完善', True),
    ('根据其他 agentorch 项目的经验改进本项目', True),
    ('总结近期开发教训并推进改进', True),
    ('自我完善', False),
)

UPDATE_VERBS = ('更新', '升级', 'update', 'upgrade')
AGENTORCH_REFERENTS = ('agentorch', 'agent-orch', 'agent orchestration',
                       'orchestration starter kit', '规则包', '套件本身')
# "更新 AgentOrch 的文档" / "更新这个 Issue" name a different object: ask once.
UPDATE_EXCLUDED_OBJECTS = ('文档', '依赖', 'issue', '版本号', '说明', '注释', '报告',
                           'readme', 'documentation')

EXECUTION_MARKERS = ('进行', '开始', '现在', '立即', '马上', '执行', '启动', '推进',
                     '去做', '跑一遍', '走一遍', '落地')
# A question ("怎么进行") is still an explanation unless the user is clearly
# asking for it *now*; minimising false positives is the point of the table.
STRONG_EXECUTION_MARKERS = ('现在', '立即', '马上', '执行', '启动', '去做', '跑一遍',
                            '走一遍', '落地')
QUESTION_MARKERS = ('什么', '怎么', '如何', '为什么', '原理', '机制', '吗', '多少',
                    '?', '？', '介绍', '解释', '说明一下')

VERSION_MARKERS = ('版本', 'version')

_WHITESPACE = re.compile(r'\s+')


def normalize_request(text):
    """NFKC + casefold + whitespace collapse. Punctuation is kept on purpose."""
    value = unicodedata.normalize('NFKC', text or '')
    return _WHITESPACE.sub(' ', value.casefold()).strip()


class RoutingDecision:
    """Outcome of one message. ``starts_workflow`` is the only green light."""

    def __init__(self, route, *, matched=None, reason='', execution_intent=False,
                 needs_clarification=False, owner=None):
        if route not in ROUTES:
            raise ValueError(f'unknown route {route!r}')
        self.route = route
        self.matched = matched
        self.reason = reason
        self.execution_intent = execution_intent
        self.needs_clarification = needs_clarification
        self.owner = owner

    @property
    def starts_workflow(self):
        return (self.route == ROUTE_SELF_IMPROVEMENT and self.execution_intent
                and not self.needs_clarification)

    def as_dict(self):
        return {
            'route': self.route,
            'matched': self.matched,
            'reason': self.reason,
            'execution_intent': self.execution_intent,
            'needs_clarification': self.needs_clarification,
            'owner': self.owner,
            'starts_workflow': self.starts_workflow,
        }


def _has_execution_intent(normalized):
    return any(marker in normalized for marker in EXECUTION_MARKERS)


def _has_strong_execution_intent(normalized):
    return any(marker in normalized for marker in STRONG_EXECUTION_MARKERS)


def _asks_explanation(normalized):
    return any(marker in normalized for marker in QUESTION_MARKERS)


def _match_self_improvement(normalized):
    for phrase, imperative in SELF_IMPROVEMENT_PHRASES:
        if phrase in normalized:
            return phrase, imperative
    return None, False


def _match_update(normalized):
    if not any(referent in normalized for referent in AGENTORCH_REFERENTS):
        return None
    for verb in UPDATE_VERBS:
        if verb in normalized:
            return verb
    return None


def _asks_version_only(normalized):
    return ('agentorch' in normalized
            and any(marker in normalized for marker in VERSION_MARKERS)
            and any(marker in normalized for marker in QUESTION_MARKERS + ('多少',)))


def route_request(text):
    """Route one user message.

    Boundary rules (``docs/self-improvement/entrypoints-and-decisions.md`` §1):

    * a matching phrase needs an explicit execution intent; a bare noun phrase or
      a question about the mechanism only explains;
    * ``更新 AgentOrch`` belongs to the update engine, not to this workflow;
    * unrelated requests are left alone.
    """
    normalized = normalize_request(text)
    if not normalized:
        return RoutingDecision(ROUTE_UNRELATED, reason='empty request')

    if _asks_version_only(normalized):
        return RoutingDecision(
            ROUTE_EXPLAIN_ONLY, matched='agentorch version',
            reason='AgentOrch version question only; the update engine is not started',
            owner='update-engine')

    verb = _match_update(normalized)
    if verb is not None:
        excluded = [name for name in UPDATE_EXCLUDED_OBJECTS if name in normalized]
        if excluded:
            return RoutingDecision(
                ROUTE_UPDATE_AGENTORCH, matched=verb,
                reason='update target is ambiguous: ' + ', '.join(sorted(set(excluded))),
                needs_clarification=True, owner='update-engine')
        return RoutingDecision(
            ROUTE_UPDATE_AGENTORCH, matched=verb,
            reason='AgentOrch update request; owned by the update engine, not this workflow',
            execution_intent=True, owner='update-engine')

    phrase, imperative = _match_self_improvement(normalized)
    if phrase is not None:
        execution_intent = imperative or _has_execution_intent(normalized)
        if _asks_explanation(normalized) and not _has_strong_execution_intent(normalized):
            return RoutingDecision(
                ROUTE_EXPLAIN_ONLY, matched=phrase,
                reason='explanation request about the mechanism, not an execution command')
        if not execution_intent:
            return RoutingDecision(
                ROUTE_SELF_IMPROVEMENT, matched=phrase,
                reason='trigger phrase without an execution intent; ask once before starting',
                needs_clarification=True)
        return RoutingDecision(
            ROUTE_SELF_IMPROVEMENT, matched=phrase,
            reason='execution intent recognised', execution_intent=True)

    return RoutingDecision(ROUTE_UNRELATED, reason='no self-improvement or update trigger')


# --------------------------------------------------------------------------- #
# Risk and human-decision gates
# --------------------------------------------------------------------------- #

DECISION_OPEN_CONFLICT = 'HD-04'
DECISION_HIGH_RISK = 'HD-05'
DECISION_CONTRACT = 'HD-13'

RISK_LOW = 'LOW'
RISK_HIGH = 'HIGH'
# Keywords and directory names are investigation leads, not verdicts. When a
# lead exists but no trustworthy diff is bound to a head SHA, the honest answer
# is "collect evidence next", not "the owner must decide" and not "low risk".
RISK_NEEDS_EVIDENCE = 'NEEDS_EVIDENCE'
RISK_LEVELS = (RISK_LOW, RISK_HIGH, RISK_NEEDS_EVIDENCE)

# Execution / state / release surfaces. A real code or configuration change
# here is high risk on its own; a prose-only change is still reviewed.
PROTECTED_PATH_PREFIXES = (
    'scripts/orchestrator.py',
    '.orchestration/',
    '.github/workflows/',
    'release/',
)

# Governance surfaces whose meaning is defined elsewhere. Text changes are
# allowed only with explicit, source-linked authorization and remain subject to
# Master review; they are not an automatic owner decision.
GOVERNANCE_PATH_PREFIXES = (
    'AGENTS.md',
    'docs/orchestration.md',
    'kit/orchestration.md',
    'editions/',
    'docs/contracts/',
)

# Design documents are review surfaces, not frozen runtime behaviour. A normal
# wording fix is allowed once the diff shows it is text-only.
DESIGN_PATH_PREFIXES = ('docs/design/',)

# Kept as the union for callers that only need to know "this path is a lead".
HIGH_RISK_PATH_PREFIXES = (PROTECTED_PATH_PREFIXES + GOVERNANCE_PATH_PREFIXES
                           + DESIGN_PATH_PREFIXES)

DOCUMENTATION_SUFFIXES = ('.md', '.rst', '.txt', '.adoc')
AUTHORIZATION_SCOPE = 'governance-adjustment'

# (keyword, decision id). Terms follow AGENTS.md §5 and the HD-* catalogue; the
# catalogue itself is not redefined here. A hit is a lead: it asks for evidence,
# it does not by itself request an owner decision.
HIGH_RISK_KEYWORDS = (
    ('权限', DECISION_HIGH_RISK),
    ('安全边界', DECISION_HIGH_RISK),
    ('安全模型', DECISION_HIGH_RISK),
    ('锁', DECISION_HIGH_RISK),
    ('状态机', DECISION_HIGH_RISK),
    ('数据迁移', DECISION_HIGH_RISK),
    ('迁移', DECISION_HIGH_RISK),
    ('外部依赖', DECISION_HIGH_RISK),
    ('隐私', DECISION_HIGH_RISK),
    ('凭据', DECISION_HIGH_RISK),
    ('破坏兼容', DECISION_HIGH_RISK),
    ('不兼容', DECISION_HIGH_RISK),
    ('不可逆', DECISION_HIGH_RISK),
    ('销毁', DECISION_HIGH_RISK),
    ('schema', DECISION_CONTRACT),
    ('architecture contract', DECISION_CONTRACT),
)

_SHA_RE = re.compile(r'^[0-9a-f]{40}$')
_PERMISSION_CODE_RE = re.compile(
    r'\b(?:allow(?:ed|s)?|deny|denied|permission(?:s)?|'
    r'authorize(?:d|s|ation)?|privilege(?:s)?|role(?:s)?|'
    r'sudo|chmod|chown|setuid|setgid|credential(?:s)?|secret(?:s)?|'
    r'token(?:s)?|password(?:s)?)\b', re.I)
_IRREVERSIBLE_CODE_RE = re.compile(
    r'(\bDROP\s+TABLE\b|\bTRUNCATE\b|\bDELETE\s+FROM\b|\brmtree\b|'
    r'\bunlink\b|\bgit\s+push\s+--force\b)', re.I)
_MIGRATION_CODE_RE = re.compile(
    r'\b(?:migrat(?:e|ion)|ALTER\s+TABLE|schema_version)\b', re.I)


def _append_unique(values, value):
    if value and value not in values:
        values.append(value)


def _normalize_repo_path(value):
    """Normalise a project-relative path without losing its boundary.

    ``AGENTS.md.backup`` must not become ``AGENTS.md``; ``docs//design/../x``
    must still resolve to the file it names. Backslashes are accepted so a
    Windows-style caller cannot bypass the surface table.
    """
    text = str(value).replace('\\', '/').strip()
    if not text:
        return ''
    while text.startswith('./'):
        text = text[2:]
    text = re.sub(r'/+', '/', text)
    normalized = posixpath.normpath(text)
    return '' if normalized == '.' else normalized


def _is_absolute_path(value):
    text = str(value).replace('\\', '/').strip()
    return text.startswith('/') or bool(re.match(r'^[A-Za-z]:/', text))


def _is_escaping_path(value):
    normalized = _normalize_repo_path(value)
    return normalized == '..' or normalized.startswith('../')


def _path_at_or_below(path, prefix):
    normalized_prefix = _normalize_repo_path(prefix).rstrip('/')
    normalized_path = _normalize_repo_path(path)
    if not normalized_prefix or not normalized_path:
        return False
    return (normalized_path == normalized_prefix
            or normalized_path.startswith(normalized_prefix + '/'))


def _matches_any(path, prefixes):
    return any(_path_at_or_below(path, prefix) for prefix in prefixes)


def _path_kind(path):
    normalized = _normalize_repo_path(path)
    if _matches_any(normalized, PROTECTED_PATH_PREFIXES):
        return 'protected'
    if _matches_any(normalized, GOVERNANCE_PATH_PREFIXES):
        return 'governance'
    if _matches_any(normalized, DESIGN_PATH_PREFIXES):
        return 'design'
    return 'ordinary'


def _is_documentation_path(path):
    normalized = _normalize_repo_path(path).lower()
    return any(normalized.endswith(suffix) for suffix in DOCUMENTATION_SUFFIXES)


def _is_test_path(path):
    normalized = _normalize_repo_path(path)
    return (normalized.startswith('tests/') or '/tests/' in normalized
            or posixpath.basename(normalized).startswith('test_'))


def _is_schema_path(path):
    normalized = _normalize_repo_path(path).lower()
    return ('schema' in posixpath.basename(normalized)
            or normalized.endswith('.schema.json'))


def _lesson_risk_text(lesson):
    lesson = lesson or {}
    observation = lesson.get('observation') or {}
    applicability = lesson.get('applicability') or {}
    suggested = lesson.get('suggested_action') or {}
    parts = [
        lesson.get('title') or '',
        observation.get('root_cause') or '',
        observation.get('impact') or '',
        suggested.get('summary') or '',
        ' '.join(applicability.get('applies_to') or []),
    ]
    return ' '.join(parts)


def _collect_leads(proposal, lesson):
    leads = []
    required = []
    keyword_hits = []
    for path in proposal.get('allowed_paths') or []:
        if not isinstance(path, str) or not path.strip():
            continue
        normalized = _normalize_repo_path(path)
        if _is_absolute_path(path) or _is_escaping_path(path):
            _append_unique(
                leads,
                f'allowed path is not project-relative and needs verification: {normalized}')
            _append_unique(
                required,
                f'provide a project-relative allowed path for {normalized}')
            continue
        kind = _path_kind(normalized)
        if kind in ('protected', 'governance', 'design'):
            _append_unique(
                leads,
                f'allowed path touches a {kind} surface: {normalized}')
            _append_unique(
                required,
                f'provide a real diff bound to base/head SHAs for {normalized}')
    text = (_lesson_risk_text(lesson) + ' '
            + str(proposal.get('goal') or '')).casefold()
    for keyword, mapped in HIGH_RISK_KEYWORDS:
        if keyword.casefold() in text:
            keyword_hits.append((keyword, mapped))
            _append_unique(leads,
                           f'lesson or goal mentions high-risk area: {keyword}')
            _append_unique(
                required,
                f'provide a real diff showing whether {keyword} semantics change')
    return leads, required, keyword_hits


def _strip_diff_prefix(path):
    path = path.strip()
    if path.startswith('a/') or path.startswith('b/'):
        return path[2:]
    return path


def _parse_unified_diff(diff_text):
    """Extract paths and changed lines from a caller-supplied unified diff.

    This is deliberately a parser, not a Git client: the planner cannot prove
    the diff is complete, so the report keeps that limitation visible.
    """
    paths = set()
    added = []
    removed = []
    changes = {}
    errors = []
    current_paths = set()
    in_hunk = False

    def record(path):
        paths.add(path)
        changes.setdefault(path, {'added': [], 'removed': []})

    for raw in str(diff_text or '').splitlines():
        line = raw.rstrip('\r')
        if line.startswith('diff --git '):
            current_paths = set()
            rest = line[len('diff --git '):]
            if rest.startswith('a/'):
                rest = rest[2:]
            marker = ' b/'
            index = rest.rfind(marker)
            if index != -1:
                for item in (rest[:index], rest[index + len(marker):]):
                    item = _normalize_repo_path(_strip_diff_prefix(item))
                    if item:
                        record(item)
                        current_paths.add(item)
            in_hunk = False
        elif (line.startswith('--- ') or line.startswith('+++ ')) and not in_hunk:
            candidate = line[4:].split('\t', 1)[0].strip()
            if candidate and candidate != '/dev/null':
                candidate = _normalize_repo_path(_strip_diff_prefix(candidate))
                if candidate:
                    record(candidate)
                    current_paths.add(candidate)
                else:
                    errors.append(f'unreadable diff path: {line}')
            in_hunk = False
        elif line.startswith('@@'):
            in_hunk = True
        elif line.startswith('+') and not line.startswith('+++'):
            if in_hunk:
                added.append(line[1:])
                for path in current_paths:
                    changes[path]['added'].append(line[1:])
        elif line.startswith('-') and not line.startswith('---'):
            if in_hunk:
                removed.append(line[1:])
                for path in current_paths:
                    changes[path]['removed'].append(line[1:])
        elif line.startswith(('rename from ', 'rename to ',
                              'copy from ', 'copy to ')):
            candidate = line.split(' ', 2)[-1]
            candidate = _normalize_repo_path(_strip_diff_prefix(candidate))
            if candidate:
                record(candidate)
                current_paths.add(candidate)
        elif line.startswith('Binary files ') and line.endswith(' differ'):
            body = line[len('Binary files '):-len(' differ')]
            for part in body.split(' and '):
                candidate = _normalize_repo_path(_strip_diff_prefix(part))
                if candidate:
                    record(candidate)
    return {'paths': paths, 'added': added, 'removed': removed,
            'changes': changes, 'errors': errors}


def _untrusted_evidence(reason):
    return {'status': 'UNTRUSTED', 'reasons': [reason], 'changed_paths': [],
            'added': [], 'removed': [], 'changes': {}, 'outside': []}


def _change_evidence_state(proposal):
    evidence = proposal.get('change_evidence')
    if evidence is None:
        return {'status': 'MISSING', 'reasons': ['no change_evidence supplied'],
                'changed_paths': [], 'added': [], 'removed': [],
                'changes': {}, 'outside': []}
    if not isinstance(evidence, dict):
        return _untrusted_evidence('change_evidence must be a mapping')
    diff = evidence.get('diff')
    if not isinstance(diff, str) or not diff.strip():
        return _untrusted_evidence('change_evidence.diff is required')
    base_sha = str(evidence.get('base_sha') or '').strip().lower()
    head_sha = str(evidence.get('head_sha') or '').strip().lower()
    if not _SHA_RE.match(base_sha):
        return _untrusted_evidence(
            'change_evidence.base_sha must be a 40-character hex SHA')
    if not _SHA_RE.match(head_sha):
        return _untrusted_evidence(
            'change_evidence.head_sha must be a 40-character hex SHA')
    if base_sha == head_sha:
        return _untrusted_evidence(
            'change_evidence base_sha and head_sha must differ')

    parsed = _parse_unified_diff(diff)
    if parsed['errors']:
        return _untrusted_evidence('; '.join(parsed['errors']))
    if not parsed['paths']:
        return _untrusted_evidence('change_evidence.diff names no changed files')
    unsafe_paths = sorted(path for path in parsed['paths']
                          if _is_absolute_path(path) or _is_escaping_path(path))
    if unsafe_paths:
        return _untrusted_evidence(
            'change_evidence.diff paths must be project-relative: '
            + ', '.join(unsafe_paths))
    if not parsed['added'] and not parsed['removed']:
        return _untrusted_evidence(
            'change_evidence.diff contains no added or removed text lines')

    declared = evidence.get('changed_paths')
    if declared is not None:
        if not isinstance(declared, (list, tuple)) or not declared:
            return _untrusted_evidence(
                'change_evidence.changed_paths must be a non-empty list')
        declared_paths = {_normalize_repo_path(item) for item in declared
                          if isinstance(item, str) and item.strip()}
        if declared_paths != parsed['paths']:
            missing = sorted(parsed['paths'] - declared_paths)
            extra = sorted(declared_paths - parsed['paths'])
            return _untrusted_evidence(
                'change_evidence.changed_paths does not match the diff'
                + (f'; missing from declared paths: {", ".join(missing)}'
                   if missing else '')
                + (f'; declared but not in diff: {", ".join(extra)}'
                   if extra else ''))

    allowed = [_normalize_repo_path(path)
               for path in proposal.get('allowed_paths') or []
               if isinstance(path, str) and path.strip()]
    if not allowed:
        return _untrusted_evidence(
            'allowed_paths is required to verify the diff scope')
    outside = sorted(path for path in parsed['paths']
                     if not any(_path_at_or_below(path, item) for item in allowed))
    return {'status': 'PRESENT', 'reasons': [],
            'changed_paths': sorted(parsed['paths']),
            'added': parsed['added'], 'removed': parsed['removed'],
            'changes': parsed['changes'], 'outside': outside}


def _authorization_granted(proposal):
    authorization = proposal.get('authorization')
    if not isinstance(authorization, dict):
        return False
    if not isinstance(authorization.get('granted_by'), str) \
            or not authorization['granted_by'].strip():
        return False
    source = authorization.get('source')
    if not isinstance(source, str) or not re.match(r'^https?://\S+$', source.strip()):
        return False
    scopes = authorization.get('scope')
    if isinstance(scopes, str):
        scopes = [scopes]
    if not isinstance(scopes, (list, tuple)):
        return False
    normalized = {str(scope).strip().casefold() for scope in scopes
                  if isinstance(scope, str) and scope.strip()}
    return AUTHORIZATION_SCOPE in normalized


def _non_comment_lines(lines):
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(('#', '//', '/*', '*', '<!--', '-- ')):
            continue
        result.append(stripped)
    return result


def _comments_only(lines):
    non_empty = [line.strip() for line in lines if line.strip()]
    return bool(non_empty) and all(
        line.startswith(('#', '//', '/*', '*', '<!--', '-- '))
        for line in non_empty)


def _evidence_findings(proposal, evidence, leads, keyword_hits):
    high = []
    unresolved = []
    low = []
    decision_id = None
    schema_keyword = any(keyword == 'schema' for keyword, _ in keyword_hits)

    for path in evidence['changed_paths']:
        path_change = evidence['changes'].get(path) or {'added': [], 'removed': []}
        path_lines = path_change['added'] + path_change['removed']
        changed_text = '\n'.join(_non_comment_lines(path_lines))
        permission_like = bool(_PERMISSION_CODE_RE.search(changed_text))
        irreversible = bool(_IRREVERSIBLE_CODE_RE.search(changed_text))
        migration_like = bool(_MIGRATION_CODE_RE.search(changed_text))
        comments_only = _comments_only(path_lines)
        kind = _path_kind(path)
        documentation = _is_documentation_path(path)
        if kind == 'protected':
            if documentation or comments_only:
                _append_unique(
                    unresolved,
                    f'text-only change under a protected surface needs review: {path}')
            else:
                _append_unique(
                    high,
                    f'actual code/config change under a protected surface: {path}')
                decision_id = decision_id or DECISION_HIGH_RISK
        elif kind == 'governance':
            if documentation:
                if _authorization_granted(proposal):
                    _append_unique(
                        low,
                        f'governance text change is covered by explicit '
                        f'authorization: {path}')
                else:
                    _append_unique(
                        unresolved,
                        f'governance text change needs explicit authorization: {path}')
            elif schema_keyword or _is_schema_path(path):
                _append_unique(
                    high,
                    f'contract/schema change in a governance surface: {path}')
                decision_id = decision_id or DECISION_CONTRACT
            else:
                _append_unique(
                    high,
                    f'code/config change in a governance surface: {path}')
                decision_id = decision_id or DECISION_HIGH_RISK
        elif kind == 'design':
            if documentation or comments_only:
                _append_unique(
                    low, f'text-only design/documentation change: {path}')
            else:
                _append_unique(
                    unresolved,
                    f'non-text change in a design surface needs semantic review: {path}')
        elif documentation:
            _append_unique(low, f'text-only documentation change: {path}')
        elif comments_only:
            _append_unique(low, f'comment-only change: {path}')
        elif irreversible:
            _append_unique(
                high, f'irreversible operation in the diff for {path}')
            decision_id = decision_id or DECISION_HIGH_RISK
        elif schema_keyword or _is_schema_path(path):
            _append_unique(high, f'contract/schema change: {path}')
            decision_id = decision_id or DECISION_CONTRACT
        elif permission_like:
            if _is_test_path(path):
                _append_unique(
                    unresolved,
                    f'security-looking test change needs review: {path}')
            elif leads:
                _append_unique(
                    high,
                    f'permission/security-looking code change: {path}')
                decision_id = decision_id or DECISION_HIGH_RISK
            else:
                _append_unique(
                    unresolved,
                    f'security-looking code change needs review: {path}')
        elif migration_like and leads:
            _append_unique(
                unresolved,
                f'migration-looking code change needs semantic review: {path}')
        elif leads:
            _append_unique(
                unresolved,
                f'actual code/config change cannot be proven unrelated to a '
                f'flagged risk: {path}')
        else:
            _append_unique(low, f'ordinary code change with no risk lead: {path}')

    for path in evidence['outside']:
        if _path_kind(path) == 'protected' and not _is_documentation_path(path):
            _append_unique(high, f'diff changes a path outside allowed_paths: {path}')
            decision_id = decision_id or DECISION_HIGH_RISK
        else:
            _append_unique(
                unresolved,
                f'diff changes a path outside allowed_paths: {path}')
    return high, unresolved, low, decision_id


def assess_risk_details(proposal, lesson):
    """Return a structured risk assessment for one proposal.

    The only inputs that can produce ``HIGH`` are findings in a real diff (or
    actual changed paths) - never a keyword or directory name by itself. A lead
    without a usable diff yields ``NEEDS_EVIDENCE``: a machine/agent can collect
    the evidence and re-plan, so it is neither ``READY`` nor a false owner
    question.

    ``limitations`` stays visible because the diff is caller-supplied: the
    planner can reject an inconsistent or out-of-scope diff, but it cannot prove
    the caller listed every working-tree change.
    """
    proposal = proposal or {}
    leads, required, keyword_hits = _collect_leads(proposal, lesson)
    evidence = _change_evidence_state(proposal)
    limitations = ([
        'change_evidence is caller-supplied; declared paths are checked against '
        'allowed_paths, but completeness against the working tree is not proven '
        'by this planner'
    ] if evidence['status'] == 'PRESENT' else [])
    if _authorization_granted(proposal):
        limitations.append(
            'authorization is caller-supplied; its shape and source URL are checked, '
            'but the decision record is not fetched or independently verified here')

    if evidence['status'] == 'UNTRUSTED':
        reasons = leads + evidence['reasons']
        for reason in evidence['reasons']:
            _append_unique(required, f'fix change_evidence: {reason}')
        return {'level': RISK_NEEDS_EVIDENCE, 'decision_id': None,
                'reasons': reasons, 'leads': leads,
                'required_evidence': required, 'limitations': limitations,
                'evidence_status': evidence['status'],
                'changed_paths': []}

    if evidence['status'] == 'MISSING':
        if not leads:
            return {'level': RISK_LOW, 'decision_id': None, 'reasons': [],
                    'leads': [], 'required_evidence': [], 'limitations': [],
                    'evidence_status': 'MISSING', 'changed_paths': []}
        reasons = leads + ['no trustworthy change evidence was supplied']
        _append_unique(
            required,
            'supply change_evidence with base_sha, head_sha and the actual diff')
        return {'level': RISK_NEEDS_EVIDENCE, 'decision_id': None,
                'reasons': reasons, 'leads': leads,
                'required_evidence': required, 'limitations': limitations,
                'evidence_status': evidence['status'], 'changed_paths': []}

    high, unresolved, low, decision_id = _evidence_findings(
        proposal, evidence, leads, keyword_hits)
    if high:
        reasons = leads + high
        return {'level': RISK_HIGH,
                'decision_id': decision_id or DECISION_HIGH_RISK,
                'reasons': reasons, 'leads': leads,
                'required_evidence': required, 'limitations': limitations,
                'evidence_status': evidence['status'],
                'changed_paths': evidence['changed_paths']}
    if unresolved:
        reasons = leads + unresolved
        for path in evidence['outside']:
            _append_unique(
                required,
                f'diff changes a path outside allowed paths: {path}; amend '
                f'allowed_paths through a scope decision or remove that change')
        for reason in unresolved:
            if 'explicit authorization' in reason:
                _append_unique(
                    required,
                    'provide authorization with granted_by, scope '
                    f'"{AUTHORIZATION_SCOPE}" and a source URL')
            elif 'needs review' in reason:
                _append_unique(
                    required,
                    'record why the actual change does not alter permissions, '
                    'security, locks, the state machine, data, or compatibility')
        return {'level': RISK_NEEDS_EVIDENCE, 'decision_id': None,
                'reasons': reasons, 'leads': leads,
                'required_evidence': required, 'limitations': limitations,
                'evidence_status': evidence['status'],
                'changed_paths': evidence['changed_paths']}
    return {'level': RISK_LOW, 'decision_id': None, 'reasons': low,
            'leads': leads, 'required_evidence': [], 'limitations': limitations,
            'evidence_status': evidence['status'],
            'changed_paths': evidence['changed_paths']}


def assess_risk(proposal, lesson):
    """Return ``(level, decision_id, reasons)`` for one proposal.

    Backwards-compatible tuple view of :func:`assess_risk_details`. ``level`` is
    ``LOW``, ``HIGH`` or ``NEEDS_EVIDENCE``; only ``HIGH`` carries an ``HD-*``.
    """
    details = assess_risk_details(proposal, lesson)
    return details['level'], details['decision_id'], details['reasons']


# --------------------------------------------------------------------------- #
# Task packet completeness
# --------------------------------------------------------------------------- #

PROPOSAL_STATUSES = ('READY', 'BLOCKED', 'NEEDS_HUMAN_DECISION', 'NO_TASK',
                     'ALREADY_REGISTERED')

SCENARIO_FIELDS = ('invariants', 'negative_cases', 'migration_cases',
                   'integration_paths', 'unverified_assumptions')
TEXT_REQUIRED_FIELDS = ('title', 'goal', 'context')
AC_ID_RE = re.compile(r'^AC-[0-9]+$')
NA_ONLY = re.compile(r'^n/?a\s*[:：]?$', re.I)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _scenario_errors(packet):
    errors = []
    for field in SCENARIO_FIELDS:
        value = packet.get(field)
        if isinstance(value, (list, tuple)):
            text = ' '.join(str(item) for item in value).strip()
        else:
            text = value.strip() if isinstance(value, str) else ''
        if not text:
            errors.append(f'{field}: required; write "N/A: <reason>" only when the surface '
                          'truly does not apply')
        elif NA_ONLY.match(text):
            errors.append(f'{field}: bare N/A is not allowed; write "N/A: <reason>"')
    return errors


def validate_task_packet(packet):
    """Structural check for one improvement task packet.

    It proves "the fields exist and no unexecuted claim is dressed as a result",
    not that the proposal is a good idea. Field meanings are in
    ``kit/orchestration.md`` 4.1 and are not restated here.
    """
    errors = []
    for field in TEXT_REQUIRED_FIELDS:
        if not _text(packet.get(field)):
            errors.append(f'{field}: required non-empty text')

    allowed = packet.get('allowed_paths')
    if not isinstance(allowed, (list, tuple)) or not allowed:
        errors.append('allowed_paths: at least one concrete file or directory is required')
    else:
        for path in allowed:
            if not _text(path):
                errors.append('allowed_paths: entries must be non-empty strings')
                continue
            stripped = path.strip()
            if stripped in ('.', './', '/', '*', '**'):
                errors.append(f'allowed_paths: {stripped!r} is too broad; name concrete paths')
            elif '*' in stripped:
                errors.append(f'allowed_paths: wildcard {stripped!r} is not allowed')

    forbidden = packet.get('forbidden_paths')
    if not _as_list(forbidden) or not any(_text(item) for item in _as_list(forbidden)):
        errors.append('forbidden_paths: required (use "every other path" when nothing else '
                      'may change)')

    dependencies = packet.get('dependencies')
    if not isinstance(dependencies, (list, tuple)):
        errors.append('dependencies: must be a list (write [] when there is no dependency)')

    criteria = packet.get('acceptance_criteria')
    if not isinstance(criteria, (list, tuple)) or not criteria:
        errors.append('acceptance_criteria: at least one AC is required')
    else:
        seen = set()
        for index, criterion in enumerate(criteria):
            if not isinstance(criterion, dict):
                errors.append(f'acceptance_criteria[{index}]: must be a mapping with id / '
                              'statement / verification')
                continue
            ac_id = criterion.get('id')
            if not isinstance(ac_id, str) or not AC_ID_RE.match(ac_id):
                errors.append(f'acceptance_criteria[{index}].id: must match AC-<n>')
            elif ac_id in seen:
                errors.append(f'acceptance_criteria[{index}].id: duplicate {ac_id}')
            else:
                seen.add(ac_id)
            if not _text(criterion.get('statement')):
                errors.append(f'acceptance_criteria[{index}].statement: required')
            if not _text(criterion.get('verification')):
                errors.append(f'acceptance_criteria[{index}].verification: a command or a '
                              'manual check is required')

    errors.extend(_scenario_errors(packet))

    commands = packet.get('validation_commands')
    if not isinstance(commands, (list, tuple)) or not commands:
        errors.append('validation_commands: at least one command is required')
    else:
        for command in commands:
            if not _text(command):
                errors.append('validation_commands: entries must be non-empty strings')

    return errors


def _missing_spec_fields(proposal):
    missing = []
    for field in ('goal', 'context', 'allowed_paths', 'forbidden_paths',
                  'acceptance_criteria', 'validation_commands') + SCENARIO_FIELDS:
        value = proposal.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
        elif isinstance(value, (list, tuple)) and not value:
            missing.append(field)
    return missing


# --------------------------------------------------------------------------- #
# Proposals
# --------------------------------------------------------------------------- #

class PlanResult:
    def __init__(self, proposals):
        self.proposals = list(proposals)

    @property
    def ready(self):
        return [p for p in self.proposals if p['status'] == 'READY']

    def by_status(self, status):
        return [p for p in self.proposals if p['status'] == status]

    def counts(self):
        counts = {status: 0 for status in PROPOSAL_STATUSES}
        for proposal in self.proposals:
            counts[proposal['status']] = counts.get(proposal['status'], 0) + 1
        return counts

    def as_dict(self):
        return {'proposals': self.proposals, 'counts': self.counts()}


def _proposal_from_spec(lesson, spec, edition):
    spec = dict(spec or {})
    proposal = {
        'lesson_id': lesson.get('lesson_id'),
        'title': spec.get('title') or lesson.get('title'),
        'goal': spec.get('goal'),
        'context': spec.get('context'),
        'edition': edition,
        'allowed_paths': _as_list(spec.get('allowed_paths')),
        'forbidden_paths': _as_list(spec.get('forbidden_paths')),
        'dependencies': _as_list(spec.get('dependencies')),
        'acceptance_criteria': [
            dict(criterion) if isinstance(criterion, dict) else criterion
            for criterion in _as_list(spec.get('acceptance_criteria'))
        ],
        'declared_resources': _as_list(spec.get('declared_resources')),
        'evidence_urls': list((lesson.get('source') or {}).get('evidence_urls') or []),
        'risks': spec.get('risks') or '',
        'status': 'BLOCKED',
        'status_reason': '',
        'required_human_decision': None,
        'risk_level': 'UNKNOWN',
        'risk_reasons': [],
    }
    for field in SCENARIO_FIELDS:
        value = spec.get(field)
        proposal[field] = _as_list(value) if isinstance(value, (list, tuple)) else value
    # Optional evidence fields are copied only when the caller supplied them, so
    # an old-format proposal keeps exactly its old shape.
    for field in ('change_evidence', 'authorization'):
        if field in spec:
            proposal[field] = spec.get(field)
    proposal['validation_commands'] = _as_list(spec.get('validation_commands'))
    return proposal


def build_proposal(lesson, spec, *, edition, dependency_state=None, coverage=None,
                   existing_task=None):
    """Turn one lesson + one explicit spec into a gated proposal.

    Status order is deliberate: a conflict or an evidenced high-risk change is
    escalated before any structural check, so a broken packet never hides a
    human decision. A lead that still needs evidence blocks with a concrete
    next step instead of becoming a false owner question.
    """
    if edition not in ('local', 'github'):
        raise ValueError(f'unknown edition {edition!r}')
    proposal = _proposal_from_spec(lesson, spec, edition)

    open_conflicts = lessons.open_conflicts(lesson)
    if open_conflicts:
        proposal['status'] = 'NEEDS_HUMAN_DECISION'
        proposal['status_reason'] = 'lesson has an OPEN conflict: ' + ', '.join(
            conflict.get('lesson_id', '?') for conflict in open_conflicts)
        proposal['required_human_decision'] = DECISION_OPEN_CONFLICT
        return proposal

    if existing_task:
        proposal['status'] = 'ALREADY_REGISTERED'
        proposal['status_reason'] = f'task already registered as {existing_task}'
        proposal['risk_level'] = 'UNKNOWN'
        return proposal

    coverage_entry = (coverage or {}).get(lesson.get('lesson_id')) or {}
    if coverage_entry.get('status') == 'COVERED':
        proposal['status'] = 'NO_TASK'
        proposal['status_reason'] = ('existing mechanism already covers this lesson: '
                                     + str(coverage_entry.get('evidence') or 'no reference'))
        return proposal

    eligible, eligibility_reason = lessons.can_generate_task(lesson)
    if not eligible:
        proposal['status'] = 'NO_TASK'
        proposal['status_reason'] = eligibility_reason
        return proposal

    missing = _missing_spec_fields(proposal)
    packet_errors = validate_task_packet(proposal)
    if packet_errors:
        proposal['packet_errors'] = packet_errors

    # Risk is assessed before any structural blocker so a real high-risk change
    # is never hidden behind "the packet is incomplete". A lead without evidence
    # is a BLOCKED proposal with a concrete next step, not an owner question.
    risk = assess_risk_details(proposal, lesson)
    level = risk['level']
    proposal['risk_level'] = level
    proposal['risk_reasons'] = risk['reasons']
    proposal['risk_leads'] = risk['leads']
    proposal['required_evidence'] = risk['required_evidence']
    proposal['risk_limitations'] = risk['limitations']
    proposal['change_evidence_status'] = risk['evidence_status']
    if level == RISK_HIGH:
        proposal['status'] = 'NEEDS_HUMAN_DECISION'
        proposal['status_reason'] = 'high-risk change: ' + '; '.join(risk['reasons'])
        proposal['required_human_decision'] = risk['decision_id']
        return proposal
    if level == RISK_NEEDS_EVIDENCE:
        proposal['status'] = 'BLOCKED'
        proposal['status_reason'] = 'risk evidence is insufficient: ' + '; '.join(
            risk['reasons'])
        return proposal

    if missing:
        proposal['status'] = 'BLOCKED'
        proposal['status_reason'] = 'task spec is incomplete: ' + ', '.join(missing)
        return proposal

    if packet_errors:
        proposal['status'] = 'BLOCKED'
        proposal['status_reason'] = 'task packet is incomplete'
        return proposal

    unresolved = [ref for ref in proposal['dependencies']
                  if (dependency_state or {}).get(str(ref)) != 'VERIFIED']
    if unresolved:
        proposal['status'] = 'BLOCKED'
        proposal['status_reason'] = 'dependencies are not VERIFIED: ' + ', '.join(
            str(ref) for ref in unresolved)
        return proposal

    proposal['status'] = 'READY'
    proposal['status_reason'] = 'eligible lesson, complete packet, verified dependencies, low risk'
    return proposal


def plan_tasks(*, lesson_records, specs=None, edition, dependency_state=None,
               coverage=None, existing_tasks=None):
    """Gate every lesson through :func:`build_proposal`."""
    specs = specs or {}
    existing_tasks = existing_tasks or {}
    proposals = []
    for lesson in lesson_records:
        lesson_id = lesson.get('lesson_id')
        proposals.append(build_proposal(
            lesson, specs.get(lesson_id), edition=edition,
            dependency_state=dependency_state, coverage=coverage,
            existing_task=existing_tasks.get(lesson_id)))
    return PlanResult(proposals)


# --------------------------------------------------------------------------- #
# Rendering (carrier-independent content)
# --------------------------------------------------------------------------- #

FIELD_REFERENCE = ('> Field meanings and the G1 evidence rules live only in '
                   '`kit/orchestration.md` 4.1; this packet carries the fields and does not '
                   'restate them.')
UNTRUSTED_NOTICE = ('> Lesson-derived text in this packet is untrusted data: quoted for a '
                    'human reviewer, never executed and never used to widen allowed paths or '
                    'acceptance criteria.')


def _render_acceptance(criteria):
    lines = []
    for criterion in criteria or []:
        if not isinstance(criterion, dict):
            continue
        lines.append(f"- [ ] {criterion.get('id')}: {criterion.get('statement')}")
        lines.append(f"  - 验证：`{criterion.get('verification')}`")
    return lines


def _render_scenarios(proposal):
    lines = []
    for field in SCENARIO_FIELDS:
        value = proposal.get(field)
        if isinstance(value, (list, tuple)):
            value = '; '.join(str(item) for item in value)
        lines.append(f'- **{field}:** {value or "(missing)"}')
    return lines


def render_local_task_packet(proposal):
    """Markdown in the shape of ``templates/task-packet.md`` (local carrier)."""
    lines = ['# Local Task Packet', '', FIELD_REFERENCE, UNTRUSTED_NOTICE, '',
             f"- **Task ID:** (assigned by `scripts/orchestrator.py` at dispatch)",
             f"- **Title:** {proposal.get('title')}",
             f"- **Goal / user-visible result:** {proposal.get('goal')}",
             f"- **Context:** {proposal.get('context')}",
             f"- **Lesson:** {proposal.get('lesson_id')}",
             '- **Evidence links:** ' + (' '.join(f'<{url}>' for url in proposal.get(
                 'evidence_urls') or []) or '(none)'),
             '- **Allowed paths:** ' + (', '.join(proposal.get('allowed_paths') or [])
                                        or '(missing)'),
             '- **Forbidden paths:** ' + (', '.join(proposal.get('forbidden_paths') or [])
                                          or '(missing)'),
             '- **declared_resources:** ' + (', '.join(
                 proposal.get('declared_resources') or []) or '(none)'),
             '- **Dependencies (must be VERIFIED):** ' + (', '.join(
                 str(item) for item in proposal.get('dependencies') or []) or '无'),
             '- **Acceptance criteria:**']
    lines.extend('  ' + line for line in _render_acceptance(
        proposal.get('acceptance_criteria')))
    lines.extend(_render_scenarios(proposal))
    lines.extend([
        '- **Validation commands:** ' + (', '.join(
            f'`{command}`' for command in proposal.get('validation_commands') or [])
            or '(missing)'),
        '- **Evidence matrix:**',
        '  | ac_id | method | expected | actual | status | environment | commit_sha | '
        'evidence_ref | notes |',
        '  |------|--------|----------|--------|--------|-------------|------------|'
        '--------------|-------|',
        '- **Risks / human decisions:** ' + (proposal.get('risks') or '(none recorded)'),
        '- **Hard boundaries:** no scope expansion, no self-approval, no merge.',
    ])
    return '\n'.join(lines) + '\n'


def render_github_issue_body(proposal):
    """Markdown in the shape of ``editions/github/issue-template.md``."""
    lines = ['## 目标', '',
             UNTRUSTED_NOTICE, '',
             str(proposal.get('goal')), '',
             str(proposal.get('context') or ''), '',
             f"- Lesson: `{proposal.get('lesson_id')}`",
             '- Evidence: ' + (' '.join(f'<{url}>' for url in proposal.get(
                 'evidence_urls') or []) or '(none)'), '',
             '## 验收标准', '']
    lines.extend(_render_acceptance(proposal.get('acceptance_criteria')))
    lines.extend(['', FIELD_REFERENCE, '',
                  '**证据矩阵**（执行时填写；列顺序与 4.1 一致）：', '',
                  '| ac_id | method | expected | actual | status | environment | commit_sha | '
                  'evidence_ref | notes |',
                  '|------|--------|----------|--------|--------|-------------|------------|'
                  '--------------|-------|', '',
                  '## 可改路径', ''])
    lines.extend(f'- `{path}`' for path in proposal.get('allowed_paths') or [])
    lines.extend(['', '## 禁改路径', ''])
    lines.extend(f'- `{path}`' for path in proposal.get('forbidden_paths') or [])
    lines.extend(['', '## 依赖', ''])
    lines.append(', '.join(str(item) for item in proposal.get('dependencies') or []) or '无')
    lines.extend(['', '## 结构化场景字段', ''])
    lines.extend(_render_scenarios(proposal))
    lines.extend(['', '## 验证命令', ''])
    lines.extend(f'- `{command}`' for command in proposal.get('validation_commands') or [])
    lines.extend(['', '## 风险与人工决策', '',
                  str(proposal.get('risks') or '(none recorded)'),
                  f"- required_human_decision: {proposal.get('required_human_decision') or '(none)'}"])
    return '\n'.join(lines) + '\n'


def render_packet(proposal, edition):
    if edition == 'local':
        return render_local_task_packet(proposal)
    if edition == 'github':
        return render_github_issue_body(proposal)
    raise ValueError(f'unknown edition {edition!r}')
