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
    packet is complete, dependencies are verified and the real Git diff proves
    only a mechanical, non-semantic change. A missing, forged or incomplete Git
    claim stays ``BLOCKED`` (``risk_level: NEEDS_EVIDENCE``); an evidenced
    permission/security change reaches ``NEEDS_HUMAN_DECISION``. Everything
    else is ``NO_TASK`` / ``BLOCKED``; nothing is invented from source text.

What the module does **not** do: adopt a lesson, invent a root cause or an
acceptance criterion, create an Issue / task, run a command, or approve a
high-risk change. Field meanings (``negative_cases`` ... ) and the G1–G6 gates
live only in ``kit/orchestration.md`` 4.1 and are referenced, never restated.

Standard library only; no network, no carrier. Git is read through the local
``git`` executable so risk evidence is the repository's own diff, never a
caller-supplied summary.
"""
from __future__ import annotations

import os
import posixpath
import re
import subprocess
import unicodedata
from pathlib import Path

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
# Risk evidence
# --------------------------------------------------------------------------- #

DECISION_OPEN_CONFLICT = 'HD-04'
DECISION_HIGH_RISK = 'HD-05'
DECISION_CONTRACT = 'HD-13'

RISK_LOW = 'LOW'
RISK_HIGH = 'HIGH'
RISK_NEEDS_EVIDENCE = 'NEEDS_EVIDENCE'
RISK_LEVELS = (RISK_LOW, RISK_HIGH, RISK_NEEDS_EVIDENCE)

# Surfaces whose text or code carries authority. A real change here is never
# auto-downgraded by the absence of a keyword; it needs an authoritative
# Master/Owner decision through the task carrier.
AUTHORITY_PATH_PREFIXES = (
    'AGENTS.md',
    'docs/orchestration.md',
    'kit/orchestration.md',
    'editions/',
    'docs/contracts/',
    'docs/design/',
)

# Execution / state / release surfaces. Any real change is high risk.
PROTECTED_PATH_PREFIXES = (
    'scripts/orchestrator.py',
    '.orchestration/',
    '.github/workflows/',
    'release/',
)

DOCUMENTATION_SUFFIXES = ('.md', '.rst', '.txt', '.adoc')
SECURITY_PATH_TOKENS = (
    'auth', 'permission', 'permissions', 'access', 'security', 'policy',
    'role', 'roles', 'credential', 'credentials', 'secret', 'secrets',
    'token', 'tokens', 'approval', 'approve', 'gate', 'gates',
)
_SPECIAL_MODES = frozenset({'120000', '160000'})
_EXECUTABLE_MODE = '100755'
_SHA_RE = re.compile(r'^[0-9a-f]{40}$')
_SECURITY_CHANGE_RE = re.compile(
    r'(?i)(?:\bonly\s+owner\b|\banyone\b|\bmay\s+approve\b|'
    r'\bis_admin\b|\bpermission(?:s)?\b|\bauthoriz(?:e|ed|ation)\b|'
    r'\ballow(?:ed|s)?\b|\bden(?:y|ied)\b|\breturn\s+true\b|'
    r'\breturn\s+false\b|\bchmod\b|\bchown\b|\bsudo\b|'
    r'\bsecret(?:s)?\b|\btoken(?:s)?\b|\bcredential(?:s)?\b)')
_IRREVERSIBLE_CHANGE_RE = re.compile(
    r'(?i)(?:\bdrop\s+table\b|\btruncate\b|\bdelete\s+from\b|'
    r'\brmtree\b|\bunlink\b|\bpush\s+--force\b)')


class GitEvidenceError(Exception):
    """The repository cannot prove the claimed commit range."""


def _append_unique(values, value):
    if value and value not in values:
        values.append(value)


def _canonical_allowed_path(value):
    """Return a canonical project-relative path or raise ``ValueError``.

    Absolute paths, drive letters, backslashes, wildcards, NULs, empty paths
    and any ``..`` segment are rejected. Normalisation is limited to collapsing
    empty and ``.`` segments: it must never turn an escaping path into a valid
    one.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError('empty path')
    text = value.strip()
    if '\x00' in text or any(ord(char) < 32 for char in text):
        raise ValueError('control character in path')
    if '\\' in text:
        raise ValueError('backslashes are not project-relative separators')
    if text.startswith('/') or re.match(r'^[A-Za-z]:', text):
        raise ValueError('absolute path')
    if any(char in text for char in '*?['):
        raise ValueError('wildcard path')
    pieces = [piece for piece in text.split('/') if piece not in ('', '.')]
    if not pieces or any(piece == '..' for piece in pieces):
        raise ValueError('path escapes the project')
    return '/'.join(pieces)


def _path_policy_errors(paths):
    errors = []
    if paths is None:
        return ['allowed_paths: at least one concrete file or directory is required']
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, (list, tuple)):
        return ['allowed_paths: must be a list of project-relative paths']
    for index, path in enumerate(paths):
        try:
            _canonical_allowed_path(path)
        except ValueError as exc:
            errors.append(f'allowed_paths[{index}]: {exc}: {path!r}')
    return errors


def _safe_git_path(value):
    if any(ord(char) < 32 for char in value):
        raise GitEvidenceError(f'control character in diff path: {value!r}')
    try:
        return _canonical_allowed_path(value)
    except ValueError as exc:
        raise GitEvidenceError(f'unsafe path in diff: {value!r} ({exc})') from exc


def _path_at_or_below(path, prefix):
    prefix = prefix.rstrip('/')
    return path == prefix or path.startswith(prefix + '/')


def _is_authority_path(path):
    return any(_path_at_or_below(path, prefix)
               for prefix in AUTHORITY_PATH_PREFIXES)


def _is_protected_path(path):
    return any(_path_at_or_below(path, prefix)
               for prefix in PROTECTED_PATH_PREFIXES)


def _is_documentation_path(path):
    return any(path.lower().endswith(suffix) for suffix in DOCUMENTATION_SUFFIXES)


def _is_security_path(path):
    words = re.split(r'[^a-z0-9]+', posixpath.basename(path).lower())
    return any(token in words for token in SECURITY_PATH_TOKENS)


def _is_schema_path(path):
    lower = path.lower()
    return 'schema' in posixpath.basename(lower) or lower.endswith('.schema.json')


def _run_git(repo_root, args):
    try:
        return subprocess.run(['git', '-C', str(repo_root), *args],
                              capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Do not echo the caller's path into a report; name the failed action.
        raise GitEvidenceError('cannot execute git in the supplied repository') from exc


def _git_text(data):
    return data.decode('utf-8', 'replace').strip()


def _missing_evidence(reason):
    return {'status': 'MISSING', 'reasons': [reason], 'repo_root': None,
            'base_sha': None, 'head_sha': None, 'entries': [],
            'binary_paths': set(), 'whitespace_only': False}


def _untrusted_evidence(reason):
    return {'status': 'UNTRUSTED', 'reasons': [reason], 'repo_root': None,
            'base_sha': None, 'head_sha': None, 'entries': [],
            'binary_paths': set(), 'whitespace_only': False}


def _parse_raw_diff(data):
    tokens = data.split(b'\0')
    entries = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token:
            index += 1
            continue
        if not token.startswith(b':'):
            raise GitEvidenceError('unexpected raw diff token')
        header = token[1:].decode('ascii', 'replace').split(' ')
        if len(header) < 5:
            raise GitEvidenceError('unreadable raw diff header')
        old_mode, new_mode, old_sha, new_sha, status = header[:5]
        code = status[0]
        index += 1
        if index >= len(tokens):
            raise GitEvidenceError('truncated raw diff')
        first = _safe_git_path(os.fsdecode(tokens[index]))
        index += 1
        second = None
        if code in ('R', 'C'):
            if index >= len(tokens):
                raise GitEvidenceError('truncated rename/copy diff')
            second = _safe_git_path(os.fsdecode(tokens[index]))
            index += 1
        old_path = first if second is not None else None
        new_path = second if second is not None else first
        entries.append({
            'old_mode': old_mode, 'new_mode': new_mode,
            'old_sha': old_sha, 'new_sha': new_sha, 'status': code,
            'old_path': old_path, 'new_path': new_path,
        })
    return entries


def _parse_numstat_binary(data):
    tokens = data.split(b'\0')
    binary = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token:
            index += 1
            continue
        text = token.decode('utf-8', 'replace')
        parts = text.split('\t')
        if len(parts) >= 2 and (parts[0] == '-' or parts[1] == '-'):
            if len(parts) >= 3 and parts[2]:
                binary.add(_safe_git_path(parts[2]))
            else:
                if index + 2 >= len(tokens):
                    raise GitEvidenceError('truncated binary rename/copy diff')
                binary.add(_safe_git_path(os.fsdecode(tokens[index + 1])))
                binary.add(_safe_git_path(os.fsdecode(tokens[index + 2])))
                index += 2
        index += 1
    return binary


def _git_evidence_state(proposal, repo_root, required_head_sha=None):
    evidence = proposal.get('git_evidence')
    if evidence is None:
        return _missing_evidence('no git_evidence supplied')
    if not isinstance(evidence, dict):
        return _untrusted_evidence('git_evidence must be a mapping')
    base_sha = evidence.get('base_sha')
    head_sha = evidence.get('head_sha')
    if not isinstance(base_sha, str) or not isinstance(head_sha, str):
        return _untrusted_evidence('git_evidence.base_sha and head_sha are required')
    base_sha = base_sha.strip().lower()
    head_sha = head_sha.strip().lower()
    if not _SHA_RE.match(base_sha) or not _SHA_RE.match(head_sha):
        return _untrusted_evidence('git_evidence SHAs must be 40-character hex values')
    if base_sha == head_sha:
        return _untrusted_evidence('git_evidence base_sha and head_sha must differ')
    if required_head_sha is not None:
        expected = str(required_head_sha).strip().lower()
        if not _SHA_RE.match(expected):
            return _untrusted_evidence('task carrier head_sha is not a 40-character hex value')
        if head_sha != expected:
            return _untrusted_evidence(
                'git_evidence.head_sha does not match the task carrier head_sha')
    root = Path(repo_root or os.getcwd()).expanduser()
    try:
        top = _run_git(root, ['rev-parse', '--show-toplevel'])
        if top.returncode != 0:
            raise GitEvidenceError('supplied repository root is not a Git worktree')
        repo = _git_text(top.stdout)
        if not repo or not Path(repo).is_dir():
            raise GitEvidenceError('Git repository root is not a directory')

        def git(args):
            return _run_git(repo, args)

        for label, sha in (('base_sha', base_sha), ('head_sha', head_sha)):
            completed = git(['cat-file', '-e', f'{sha}^{{commit}}'])
            if completed.returncode != 0:
                raise GitEvidenceError(
                    f'{label} {sha} is not a commit in this repository')
        ancestor = git(['merge-base', '--is-ancestor', base_sha, head_sha])
        if ancestor.returncode != 0:
            raise GitEvidenceError('base_sha is not an ancestor of head_sha')
        shallow = git(['rev-parse', '--is-shallow-repository'])
        if shallow.returncode != 0:
            raise GitEvidenceError('cannot read shallow state')
        if _git_text(shallow.stdout).lower() == 'true':
            raise GitEvidenceError('a shallow repository cannot prove a complete history')
        current = git(['rev-parse', 'HEAD'])
        if current.returncode != 0:
            raise GitEvidenceError('cannot read HEAD')
        if _git_text(current.stdout).lower() != head_sha:
            raise GitEvidenceError(
                'checked-out HEAD does not match git_evidence.head_sha')
        status = git(['status', '--porcelain=v1', '-z', '--untracked-files=all'])
        if status.returncode != 0:
            raise GitEvidenceError('cannot read worktree status')
        if status.stdout:
            raise GitEvidenceError(
                'working tree has uncommitted or untracked changes')
        raw = git(['diff', '--raw', '-z', '--find-renames', '--find-copies',
                   '--no-ext-diff', '--no-textconv', base_sha, head_sha])
        if raw.returncode != 0:
            raise GitEvidenceError('cannot read the raw diff')
        numstat = git(['diff', '--numstat', '-z', '--find-renames', '--find-copies',
                       '--no-ext-diff', '--no-textconv', base_sha, head_sha])
        if numstat.returncode != 0:
            raise GitEvidenceError('cannot read numstat')
        whitespace = git(['diff', '--quiet', '--ignore-all-space',
                          '--ignore-blank-lines', base_sha, head_sha])
        if whitespace.returncode not in (0, 1):
            raise GitEvidenceError('cannot test whitespace-only change')
        return {
            'status': 'PRESENT', 'reasons': [], 'repo_root': repo,
            'base_sha': base_sha, 'head_sha': head_sha,
            'entries': _parse_raw_diff(raw.stdout),
            'binary_paths': _parse_numstat_binary(numstat.stdout),
            'whitespace_only': whitespace.returncode == 0,
        }
    except GitEvidenceError as exc:
        return _untrusted_evidence(str(exc))


def _diff_lines_for_path(state, path):
    completed = _run_git(state['repo_root'], [
        'diff', '-U0', '--no-color', '--no-ext-diff', '--no-textconv',
        state['base_sha'], state['head_sha'], '--', path])
    if completed.returncode not in (0, 1):
        raise GitEvidenceError('cannot read file diff')
    lines = []
    in_hunk = False
    for line in completed.stdout.decode('utf-8', 'replace').splitlines():
        if line.startswith('@@'):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith(('+', '-')):
            lines.append(line[1:])
    return '\n'.join(lines)


def _changed_paths(evidence):
    paths = set()
    for entry in evidence['entries']:
        for path in (entry['old_path'], entry['new_path']):
            if path:
                paths.add(path)
    return sorted(paths)


def _classify_git_evidence(proposal, evidence):
    high = []
    unresolved = []
    low = []
    decision_id = None
    allowed = [_canonical_allowed_path(path)
               for path in proposal.get('allowed_paths') or []]

    for path in _changed_paths(evidence):
        if not any(_path_at_or_below(path, prefix) for prefix in allowed):
            _append_unique(
                unresolved,
                f'diff changes a path outside allowed_paths: {path}')

    for entry in evidence['entries']:
        code = entry['status']
        path = entry['new_path']
        if code in ('R', 'C', 'T'):
            _append_unique(
                unresolved,
                f'rename/copy/type change cannot be proven text-only: {path}')
            continue
        if code == 'D':
            _append_unique(
                unresolved,
                f'deletion cannot be proven safe: {path}')
            continue
        if entry['old_mode'] in _SPECIAL_MODES or entry['new_mode'] in _SPECIAL_MODES:
            _append_unique(
                unresolved,
                f'symlink or submodule change cannot be proven safe: {path}')
            continue
        modes_changed = entry['old_mode'] != entry['new_mode']
        if modes_changed and _EXECUTABLE_MODE in (entry['old_mode'], entry['new_mode']):
            _append_unique(
                high,
                f'file mode/permission change: {path}')
            decision_id = decision_id or DECISION_HIGH_RISK
            continue
        if (modes_changed and '000000' not in (entry['old_mode'], entry['new_mode'])):
            _append_unique(
                high,
                f'file mode change: {path}')
            decision_id = decision_id or DECISION_HIGH_RISK
            continue
        if _is_protected_path(path):
            _append_unique(
                high,
                f'protected execution/release surface change: {path}')
            decision_id = decision_id or DECISION_HIGH_RISK
            continue
        if _is_schema_path(path):
            _append_unique(
                high,
                f'contract/schema change: {path}')
            decision_id = decision_id or DECISION_CONTRACT
            continue
        if path in evidence['binary_paths']:
            _append_unique(
                unresolved,
                f'binary change cannot be proven text-only: {path}')
            continue
        changed = _diff_lines_for_path(evidence, path)
        security_change = bool(_SECURITY_CHANGE_RE.search(changed))
        irreversible = bool(_IRREVERSIBLE_CHANGE_RE.search(changed))
        documentation = _is_documentation_path(path)
        if _is_authority_path(path):
            if security_change or irreversible:
                _append_unique(
                    high,
                    f'authority surface permission/security change: {path}')
                decision_id = decision_id or DECISION_HIGH_RISK
            else:
                _append_unique(
                    unresolved,
                    f'authority surface change needs authoritative review: {path}')
        elif documentation:
            if evidence['whitespace_only']:
                _append_unique(
                    low,
                    f'whitespace-only documentation change: {path}')
            else:
                _append_unique(
                    unresolved,
                    f'documentation change cannot be proven semantic-neutral: {path}')
        elif irreversible:
            _append_unique(
                high,
                f'irreversible operation in the real diff: {path}')
            decision_id = decision_id or DECISION_HIGH_RISK
        elif security_change:
            _append_unique(
                high,
                f'permission/security-looking code change: {path}')
            decision_id = decision_id or DECISION_HIGH_RISK
        elif _is_security_path(path):
            _append_unique(
                unresolved,
                f'security-sensitive code change needs review: {path}')
        else:
            _append_unique(
                unresolved,
                f'code change cannot be proven semantic-neutral: {path}')

    if not evidence['entries']:
        low.append('no changed paths in the verified commit range')
    return high, unresolved, low, decision_id


def assess_risk_details(proposal, lesson, repo_root=None, required_head_sha=None):
    """Assess one proposal using *real Git evidence*, not caller text.

    ``LOW`` is returned only for a verified commit range whose changed paths are
    all non-authority documentation and whose diff is whitespace-only. Every
    other content change is ``NEEDS_EVIDENCE`` or ``HIGH``; a missing, forged or
    incomplete Git claim stays ``NEEDS_EVIDENCE``. Keywords, path names, caller
    diffs and caller authorization never decide.
    """
    proposal = proposal or {}
    path_errors = _path_policy_errors(proposal.get('allowed_paths'))
    if path_errors:
        return {
            'level': RISK_NEEDS_EVIDENCE, 'decision_id': None,
            'reasons': path_errors, 'leads': list(path_errors),
            'required_evidence': [
                'use project-relative allowed_paths without absolute paths, '
                'backslashes, wildcards or any .. segment'],
            'limitations': [],
            'evidence_status': 'MISSING', 'changed_paths': [],
        }

    evidence = _git_evidence_state(proposal, repo_root,
                                   required_head_sha=required_head_sha)
    base_limitations = []
    if proposal.get('authorization') is not None:
        base_limitations.append(
            'caller-supplied authorization is ignored; only the authoritative '
            'task carrier / Owner decision can authorize a governance change')

    if evidence['status'] != 'PRESENT':
        reasons = evidence['reasons'] or ['no trustworthy Git evidence supplied']
        return {
            'level': RISK_NEEDS_EVIDENCE, 'decision_id': None,
            'reasons': reasons, 'leads': reasons,
            'required_evidence': [
                'supply git_evidence with base_sha and head_sha from this '
                'repository; a diff or SHA supplied by the caller is not proof'],
            'limitations': base_limitations,
            'evidence_status': evidence['status'], 'changed_paths': [],
        }

    high, unresolved, low, decision_id = _classify_git_evidence(proposal, evidence)
    limitations = base_limitations + [
        'Git evidence proves the checked-out commit range and a clean worktree '
        'at assessment time; it does not cover later edits or another carrier',
        'auto-LOW covers only whitespace-only changes in non-authority '
        'documentation; code, authority surfaces, binary, rename/copy/type, '
        'symlink, submodule, deletion and worktree state are outside that proof']
    if high:
        return {
            'level': RISK_HIGH,
            'decision_id': decision_id or DECISION_HIGH_RISK,
            'reasons': high, 'leads': high,
            'required_evidence': [], 'limitations': limitations,
            'evidence_status': 'PRESENT',
            'changed_paths': _changed_paths(evidence),
        }
    if unresolved:
        required = []
        if any('outside allowed_paths' in reason for reason in unresolved):
            required.append(
                'amend allowed_paths through a scope decision or remove the '
                'out-of-scope change')
        if any('authority surface' in reason for reason in unresolved):
            required.append(
                'attach the authoritative task-carrier decision (Master/Owner) '
                'for the authority surface; caller-supplied authorization is '
                'not accepted')
        if not required:
            required.append(
                'investigate the real Git diff and record why it does not '
                'change permissions, security, locks, state, data, or '
                'compatibility')
        return {
            'level': RISK_NEEDS_EVIDENCE, 'decision_id': None,
            'reasons': unresolved, 'leads': unresolved,
            'required_evidence': required, 'limitations': limitations,
            'evidence_status': 'PRESENT',
            'changed_paths': _changed_paths(evidence),
        }
    return {
        'level': RISK_LOW, 'decision_id': None, 'reasons': low, 'leads': low,
        'required_evidence': [], 'limitations': limitations,
        'evidence_status': 'PRESENT',
        'changed_paths': _changed_paths(evidence),
    }


def assess_risk(proposal, lesson, repo_root=None, required_head_sha=None):
    """Return ``(level, decision_id, reasons)`` for one proposal.

    Backwards-compatible tuple view of :func:`assess_risk_details`.
    """
    details = assess_risk_details(proposal, lesson, repo_root=repo_root,
                                  required_head_sha=required_head_sha)
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
        errors.extend(_path_policy_errors(allowed))

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
    # an old-format proposal keeps exactly its old shape. ``authorization`` is
    # kept for visibility only; it is never accepted as proof.
    for field in ('git_evidence', 'authorization'):
        if field in spec:
            proposal[field] = spec.get(field)
    proposal['validation_commands'] = _as_list(spec.get('validation_commands'))
    return proposal


def build_proposal(lesson, spec, *, edition, dependency_state=None, coverage=None,
                   existing_task=None, repo_root=None, required_head_sha=None):
    """Turn one lesson + one explicit spec into a gated proposal.

    Status order is deliberate: illegal paths are rejected first, then an
    evidenced high-risk change is escalated before any structural check, so a
    broken packet never hides a human decision. A missing or untrusted Git claim
    blocks with a concrete next step instead of becoming a false owner
    question.
    """
    if edition not in ('local', 'github'):
        raise ValueError(f'unknown edition {edition!r}')
    proposal = _proposal_from_spec(lesson, spec, edition)

    path_errors = _path_policy_errors(proposal.get('allowed_paths'))
    if path_errors:
        proposal['status'] = 'BLOCKED'
        proposal['status_reason'] = 'invalid allowed_paths: ' + '; '.join(path_errors)
        proposal['required_evidence'] = [
            'use project-relative allowed_paths without absolute paths, '
            'backslashes, wildcards or any .. segment']
        return proposal

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

    if not proposal.get('allowed_paths'):
        proposal['status'] = 'BLOCKED'
        proposal['status_reason'] = 'task spec is incomplete: allowed_paths'
        return proposal

    # Risk is assessed before any structural blocker so a real high-risk change
    # is never hidden behind "the packet is incomplete". Missing or untrusted
    # Git evidence is a BLOCKED proposal with a concrete next step, not an owner
    # question.
    risk = assess_risk_details(proposal, lesson, repo_root=repo_root,
                               required_head_sha=required_head_sha)
    level = risk['level']
    proposal['risk_level'] = level
    proposal['risk_reasons'] = risk['reasons']
    proposal['risk_leads'] = risk['leads']
    proposal['required_evidence'] = risk['required_evidence']
    proposal['risk_limitations'] = risk['limitations']
    proposal['git_evidence_status'] = risk['evidence_status']
    proposal['changed_paths'] = risk['changed_paths']
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
               coverage=None, existing_tasks=None, repo_root=None,
               required_head_sha=None):
    """Gate every lesson through :func:`build_proposal`."""
    specs = specs or {}
    existing_tasks = existing_tasks or {}
    proposals = []
    for lesson in lesson_records:
        lesson_id = lesson.get('lesson_id')
        proposals.append(build_proposal(
            lesson, specs.get(lesson_id), edition=edition,
            dependency_state=dependency_state, coverage=coverage,
            existing_task=existing_tasks.get(lesson_id),
            repo_root=repo_root, required_head_sha=required_head_sha))
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
