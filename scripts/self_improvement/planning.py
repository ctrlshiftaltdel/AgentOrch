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
    proposal. Risk is decided from the repository's own Git evidence, and every
    proposal stays below ``READY`` in this slice: an empty change set proves
    nothing about the intended goal, the allowed-path scope or the authorization,
    and a non-empty change set always needs a trusted review. A missing, forged or
    incomplete Git claim stays ``BLOCKED`` (``risk_level: NEEDS_EVIDENCE``); a
    **recognised** permission/security/destructive lead reaches
    ``NEEDS_HUMAN_DECISION``. Recognition is a fixed path table plus limited
    keyword / filename checks, not semantic analysis: an unrecognised change is
    never treated as safe and still stops at ``NEEDS_EVIDENCE``. Everything else
    is ``NO_TASK`` / ``BLOCKED``; nothing is invented from source text.

What the module does **not** do: adopt a lesson, invent a root cause or an
acceptance criterion, create an Issue / task, run a command, or approve a
high-risk change. Field meanings (``negative_cases`` ... ) and the G1–G6 gates
live only in ``kit/orchestration.md`` 4.1 and are referenced, never restated.

Standard library only; no network, no carrier. Before running Git, a pure
filesystem preflight refuses a repository whose own config can execute commands
(``filter.<driver>.clean`` / ``.process``, ``include`` / ``includeIf``, a
``filter=`` attribute, an initialised submodule / nested repository carrying
such config or attributes, a promisor / partial-clone remote, an ``ext::``
transport or an HTTP alternate): those cannot be isolated without executing
them, so the planner fails closed and says so. ``core.fsmonitor``, hooks and
lazy fetch are disabled through config overrides / environment. Git is then read
through the local ``git`` executable so risk evidence is the repository's own
diff, never a caller-supplied summary; every Git command repeats the preflight,
including the public diff-digest helper. Every revision or object id handed to
Git is first validated as an exact 40-character lowercase hex string, so a
caller or state value can never become a Git option.
"""
from __future__ import annotations

import hashlib
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
# ``RISK_LOW`` is kept for the future wired-evidence path (authoritative carrier
# adapter + independent review). This slice never returns it: an empty change set
# proves nothing, and a non-empty change set always needs a trusted review.

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
# Schema identity: an exact schema file name, a schema extension, or a file
# living in a schema directory. A documentation suffix wins over a schema-ish
# name so ``docs/schema-notes.md`` stays documentation, not a contract change.
_SCHEMA_FILE_NAMES = frozenset({'schema.json', 'schema.yaml', 'schema.yml'})
_SCHEMA_FILE_SUFFIXES = ('.schema.json', '.schema.yaml', '.schema.yml')
_SCHEMA_DIR_NAMES = frozenset({'schema', 'schemas'})
SECURITY_PATH_TOKENS = (
    'auth', 'permission', 'permissions', 'access', 'security', 'policy',
    'role', 'roles', 'credential', 'credentials', 'secret', 'secrets',
    'token', 'tokens', 'approval', 'approve', 'gate', 'gates',
)
_SPECIAL_MODES = frozenset({'120000', '160000'})
_EXECUTABLE_MODE = '100755'
_SHA_RE = re.compile(r'^[0-9a-f]{40}$')
_SAFE_GIT_CONFIG = (
    'diff.ignoreSubmodules=none',
    'diff.noprefix=false',
    'diff.mnemonicPrefix=false',
    'diff.relative=false',
    'diff.renames=true',
    'core.fileMode=true',
    'core.symlinks=true',
    'core.ignoreCase=false',
    'core.excludesFile=' + os.devnull,
    # These three are executable surfaces too. Disable them explicitly so a
    # repository-local fsmonitor hook or hook path is never invoked by a
    # supposedly read-only probe. Clean/process filters cannot be overridden by a
    # single switch, so :func:`_repository_isolation_error` refuses those before
    # any Git command runs.
    'core.fsmonitor=false',
    'core.hooksPath=' + os.devnull,
    'core.attributesFile=' + os.devnull,
)
# Repository-local config sections that can execute a command or pull in another
# config file that does. Only a raw file read is used: no Git command is run.
# Escapes Git's value grammar accepts. Anything else (including an unknown
# escape or a quote left open at end of line) is a fatal config error in Git, so
# the reader must fail closed instead of guessing.
_CONFIG_VALUE_ESCAPES = {
    't': '\t',
    'b': '\b',
    'n': '\n',
    '\\': '\\',
    '"': '"',
}
_CONFIG_SPACE = ' \t\r\n\v\f'
_FILTER_ISOLATION_MESSAGE = (
    'the repository configures a Git clean/process filter or a filter attribute; '
    'this read-only planner cannot isolate it without executing repository commands, '
    'so it refuses before running any Git command (not implemented)')
_INCLUDE_ISOLATION_MESSAGE = (
    'the repository config uses include/includeIf, so this planner cannot prove that '
    'no executable Git filter is configured; it refuses before running any Git '
    'command (not implemented)')
_LAYOUT_ISOLATION_MESSAGE = (
    'cannot locate a non-bare Git worktree without running Git; this planner will not '
    'execute repository configuration before that check (not implemented)')
_PROMISOR_ISOLATION_MESSAGE = (
    'the repository config declares a promisor / partial-clone remote, an ext:: '
    'transport or an HTTP alternate, so a read-only Git command could fetch objects '
    'or run a remote helper; this planner cannot prove no external command would run '
    'and refuses before starting Git (not implemented)')
_EXECUTABLE_CONFIG_MESSAGE = (
    'the repository config declares a command-executable Git surface (credential '
    'helper, ssh command, proxy command or alternate-refs command); this planner '
    'cannot prove it will not run and refuses before starting Git (not implemented)')
_UNREADABLE_CONFIG_MESSAGE = (
    'a repository config, info/attributes or alternates file cannot be read, so this '
    'planner cannot prove the repository is non-executable; it refuses before '
    'starting Git (not implemented)')
_TRAVERSAL_ISOLATION_MESSAGE = (
    'a directory in the worktree cannot be listed, so this planner cannot prove '
    'that no nested repository (submodule) with an executable Git configuration '
    'is hidden below it; it refuses before starting Git (not implemented)')
_UNSAFE_GIT_ENV = (
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE', 'GIT_COMMON_DIR',
    'GIT_CONFIG', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS',
    'GIT_REPLACE_REF_BASE', 'GIT_SHALLOW_FILE', 'GIT_QUARANTINE_PATH',
    'GIT_PREFIX', 'GIT_EXTERNAL_DIFF', 'GIT_DIFF_OPTS',
)
# Pathspec matching switches a caller can set in the environment. They change
# how a path argument is read (literal/glob/case), and GIT_LITERAL_PATHSPECS=1
# would even disable the explicit ``:(literal)`` prefix. Every Git child
# environment drops all four so the planner always controls the reading.
_UNSAFE_PATHSPEC_ENV = (
    'GIT_LITERAL_PATHSPECS', 'GIT_GLOB_PATHSPECS',
    'GIT_NOGLOB_PATHSPECS', 'GIT_ICASE_PATHSPECS',
)
_SECURITY_CHANGE_RE = re.compile(
    r'(?i)(?:\bonly\s+owner\b|\banyone\b|\bmay\s+approve\b|'
    r'\bis_admin\b|\bpermission(?:s)?\b|\bauthoriz(?:e|ed|ation)\b|'
    r'\ballow(?:ed|s)?\b|\bden(?:y|ied)\b|\breturn\s+true\b|'
    r'\breturn\s+false\b|\bchmod\b|\bchown\b|\bsudo\b|'
    r'\bsecret(?:s)?\b|\btoken(?:s)?\b|\bcredential(?:s)?\b)')
_IRREVERSIBLE_CHANGE_RE = re.compile(
    r'(?i)(?:\bdrop\s+table\b|\btruncate\b|\bdelete\s+from\b|'
    r'\brmtree\b|\bunlink\b|\bpush\s+--force\b)')
SCOPE_KEYWORDS = (
    '权限', '安全边界', '安全模型', '锁', '状态机', '数据迁移', '迁移',
    '外部依赖', '隐私', '凭据', '破坏兼容', '不兼容', '不可逆', '销毁',
    'schema', 'architecture contract', 'permission', 'permissions',
    'security', 'state machine', 'lock', 'locks', 'migration',
    'external dependency', 'privacy', 'credential', 'credentials',
    'incompatible', 'irreversible', 'destroy', 'skip review',
    'bypass review', 'approve without review', '跳过审查', '绕过审查',
    '无审查', '权限扩大', '扩大权限', '外部项目', '外部仓库', '不可逆迁移',
)
# Destructive / irreversible task intent. This is a *wording* lead, never proof:
# it can only raise the risk level, so an incomplete table fails closed (the
# unclassified case is still never LOW). It exists because a task goal such as
# "删除全部用户数据" is not covered by SCOPE_KEYWORDS and used to slip through an
# empty commit range as LOW.
DESTRUCTIVE_INTENT_RE = re.compile(
    r'(?:删除全部|删除所有|删除整个|删除数据|删除用户数据|删库|删表|清库|清表|'
    r'清空全部|清空所有|清空数据|清空数据库|抹除数据|擦除数据|销毁数据|永久删除|'
    r'\bdelete\s+all\b|\bdelete\s+from\b|'
    r'\bdelete\s+the\s+(?:data|database|table|record)s?\b|'
    r'\bdrop\s+(?:table|database|schema)\b|\btruncate\b|\bwipe\b|\bpurge\b|'
    r'\bdestroy\s+(?:all|the)\s+(?:data|records?|database)\b|'
    r'\brm\s+-rf\b|\brmtree\b|\bunlink\b|\bforce\s+push\b|'
    r'\bpush\s+--force\b|\breset\s+--hard\b)')


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
    if value != value.strip():
        raise GitEvidenceError(f'leading/trailing whitespace in diff path: {value!r}')
    try:
        return _canonical_allowed_path(value)
    except ValueError as exc:
        raise GitEvidenceError(f'unsafe path in diff: {value!r} ({exc})') from exc


def _path_at_or_below(path, prefix):
    prefix = prefix.rstrip('/')
    return path == prefix or path.startswith(prefix + '/')


def _paths_overlap(first, second):
    return _path_at_or_below(first, second) or _path_at_or_below(second, first)


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
    """True for a real schema / contract data path, not a schema-named document.

    Schema identity comes from the file name / extension or from a
    ``schema``/``schemas`` directory. A documentation file that merely mentions
    "schema" in its name (``docs/schema-notes.md``) is ordinary documentation:
    a filename is not proof of a contract change, so it must not widen HD-13.
    """
    lower = path.lower()
    pieces = lower.split('/')
    name = pieces[-1]
    if name in _SCHEMA_FILE_NAMES or name.endswith(_SCHEMA_FILE_SUFFIXES):
        return True
    if _is_documentation_path(lower):
        return False
    return any(piece in _SCHEMA_DIR_NAMES for piece in pieces[:-1])


def _resolve_path(value):
    try:
        return Path(value).resolve()
    except (OSError, RuntimeError):
        return None


def _git_layout(repo_root):
    """Locate ``(worktree, git_dir, common_dir)`` by reading the filesystem only.

    No Git command is executed: ``.git`` is either a directory or a ``gitdir:``
    file, and a linked worktree names its common directory in ``commondir``.
    ``None`` means the layout cannot be proven (missing, unreadable, or bare).
    """
    current = _resolve_path(Path(repo_root or os.getcwd()).expanduser())
    if current is None or not current.is_dir():
        return None
    while True:
        dot_git = current / '.git'
        if dot_git.is_dir():
            git_dir = dot_git
            break
        if dot_git.is_file():
            try:
                line = dot_git.read_text(encoding='utf-8', errors='replace').strip()
            except OSError:
                return None
            if not line.lower().startswith('gitdir:'):
                return None
            target = line.split(':', 1)[1].strip()
            if not target:
                return None
            git_dir = Path(target)
            if not git_dir.is_absolute():
                git_dir = current / git_dir
            break
        parent = current.parent
        if parent == current:
            return None
        current = parent
    git_dir = _resolve_path(git_dir)
    if git_dir is None or not git_dir.is_dir():
        return None
    common_dir = git_dir
    commondir = git_dir / 'commondir'
    if commondir.is_file():
        try:
            value = commondir.read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            return None
        if value:
            common_dir = _resolve_path(git_dir / value)
            if common_dir is None or not common_dir.is_dir():
                return None
    return current, git_dir, common_dir


def _attributes_declare_filter(path):
    """True when an attributes file assigns a ``filter`` attribute.

    A line such as ``*.bin filter=lfs`` makes Git execute the driver's clean /
    process command. ``-filter`` and ``!filter`` are unset forms and do not make
    Git run anything. Unreadable content is treated as "may declare a filter".
    """
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return True
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        tokens = line.split()
        for token in tokens[1:]:
            if token.startswith(('-', '!')):
                continue
            name = token[1:] if token.startswith('+') else token
            if name == 'filter' or name.startswith('filter='):
                return True
    return False


def _config_section_parts(base, subsection):
    """Split Git's full header name into ``(section, subsection)``.

    Git splits at the first dot of ``base + "." + subsection``, so
    ``[filter.x "probe"]`` names section ``filter`` with subsection
    ``x.probe``. The old reader kept the dotted text as the section and could
    miss a ``filter`` section entirely.
    """
    full = base if not subsection else base + '.' + subsection
    if '.' in full:
        return tuple(full.split('.', 1))
    return full, ''


def _config_section_header(text, index):
    """Parse ``[section "subsection"]`` starting just after ``[``.

    Mirrors Git's base/extended header grammar: a base name of ASCII letters,
    digits, ``-`` and ``.``, an optional quoted subsection where a backslash
    escapes the next character, and a required closing ``]``. Returns
    ``(section, subsection, next_index)`` or ``None`` when Git would reject the
    header. A backslash-newline inside a subsection is a Git error, not a value
    continuation, so it is refused rather than unfolded.
    """
    length = len(text)
    base = []
    while index < length:
        char = text[index]
        if char == ']':
            if not base:
                return None
            section, subsection = _config_section_parts(''.join(base), '')
            return section, subsection, index + 1
        if char in ' \t':
            break
        if not (char.isascii() and (char.isalnum() or char in '.-')):
            return None
        base.append(char.lower())
        index += 1
    else:
        return None
    if not base:
        return None
    while index < length and text[index] in ' \t':
        index += 1
    if index >= length or text[index] != '"':
        return None
    index += 1
    subsection = []
    while index < length:
        char = text[index]
        index += 1
        if char == '\n':
            return None
        if char == '"':
            break
        if char == '\\':
            if index >= length:
                return None
            char = text[index]
            index += 1
            if char == '\n':
                return None
        subsection.append(char)
    else:
        return None
    if index >= length or text[index] != ']':
        return None
    section, subsection = _config_section_parts(
        ''.join(base), ''.join(subsection))
    return section, subsection, index + 1


def _config_value(text, index):
    """Read one Git config value and return ``(value, next_index)``.

    Follows Git's value grammar instead of physical lines: a backslash-newline
    continues the value with no inserted whitespace, quotes delimit literal
    spans, an unquoted ``#`` / ``;`` starts a comment, and the escapes in
    :data:`_CONFIG_VALUE_ESCAPES` are the only accepted ones. ``None`` means Git
    would reject the file (unknown escape or a quote still open at end of line),
    so the caller refuses before starting Git. A trailing backslash at end of
    file is tolerated because Git accepts it.
    """
    length = len(text)
    value = []
    quoted = False
    while True:
        if index >= length:
            return (None if quoted
                    else (''.join(value).strip(), index))
        char = text[index]
        index += 1
        if char == '\n':
            if quoted:
                return None
            return ''.join(value).strip(), index
        if not quoted and char in '#;':
            newline = text.find('\n', index)
            if newline == -1:
                return ''.join(value).strip(), length
            return ''.join(value).strip(), newline + 1
        if char == '\\':
            if index >= length:
                return ''.join(value).strip(), index
            escaped = text[index]
            index += 1
            if escaped == '\n':
                continue
            mapped = _CONFIG_VALUE_ESCAPES.get(escaped)
            if mapped is None:
                return None
            value.append(mapped)
            continue
        if char == '"':
            quoted = not quoted
            continue
        value.append(char)


def _config_entries(text):
    """Parse section/key/value triples from a Git config file without running Git.

    Git config is not line-oriented. Section headers and keys are separated by
    arbitrary whitespace (two headers may share one line), a value can be
    continued across physical lines with a backslash, and quotes or escapes
    change where a comment starts. Reading physical lines let a continued
    ``ext::`` remote URL or a same-line ``[filter ...]`` header stay invisible,
    so this follows Git's character grammar. Anything this reader cannot parse
    exactly returns ``None`` and the caller fails closed before running Git.
    """
    entries = []
    section = None
    subsection = ''
    text = text.replace('\r\n', '\n')
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        index += 1
        if char in _CONFIG_SPACE:
            continue
        if char in '#;':
            newline = text.find('\n', index)
            index = length if newline == -1 else newline + 1
            continue
        if char == '[':
            parsed = _config_section_header(text, index)
            if parsed is None:
                return None
            section, subsection, index = parsed
            continue
        if not (char.isascii() and char.isalpha()):
            return None
        name = [char]
        while index < length:
            next_char = text[index]
            if next_char.isascii() and (next_char.isalnum()
                                        or next_char == '-'):
                name.append(next_char)
                index += 1
                continue
            break
        while index < length and text[index] in ' \t':
            index += 1
        value = 'true'
        if index < length and text[index] == '=':
            parsed = _config_value(text, index + 1)
            if parsed is None:
                return None
            value, index = parsed
        elif index < length and text[index] != '\n':
            # Git rejects a key followed by anything but '=' or end of line.
            return None
        if section is None:
            return None
        entries.append((section, subsection.lower(),
                        ''.join(name).lower(), value))
    return entries


def _config_isolation_reason(entries):
    """Return why a parsed config can execute a command or reach the network."""
    if entries is None:
        return _UNREADABLE_CONFIG_MESSAGE
    for section, subsection, key, value in entries:
        if section == 'filter':
            return _FILTER_ISOLATION_MESSAGE
        if section in ('include', 'includeif'):
            return _INCLUDE_ISOLATION_MESSAGE
        if section == 'credential':
            return _EXECUTABLE_CONFIG_MESSAGE
        if section == 'extensions' and 'partialclone' in key:
            return _PROMISOR_ISOLATION_MESSAGE
        if section == 'remote':
            # A promisor remote makes ``cat-file`` / ``diff`` fetch missing
            # objects; ``vcs`` selects an external SCM helper.
            if key in ('promisor', 'partialclonefilter', 'vcs'):
                return _PROMISOR_ISOLATION_MESSAGE
            if key == 'url' and 'ext::' in value:
                return _PROMISOR_ISOLATION_MESSAGE
        if section == 'url' and 'ext::' in (subsection + ' ' + value):
            return _PROMISOR_ISOLATION_MESSAGE
        if section == 'core' and key in ('alternaterefscommand', 'sshcommand',
                                         'gitproxy'):
            return _EXECUTABLE_CONFIG_MESSAGE
    return None


class _UnlistableDirectoryError(Exception):
    """A worktree directory could not be listed during the isolation scan."""


def _walk_worktree(worktree):
    """Yield ``os.walk`` entries, failing closed on an unlistable directory.

    ``os.walk`` silently skips a directory it cannot list. A directory with
    execute but no read permission is still traversable by Git, which does not
    need to list it to enter an already-registered submodule. Skipping it would
    hide that submodule's executable Git config, so every traversal error is
    fatal here and the planner refuses before running any Git command.
    """
    def _fail(error):
        raise _UnlistableDirectoryError(str(error)) from error

    return os.walk(worktree, onerror=_fail)


def _attribute_files(worktree):
    """Every ``.gitattributes`` in the worktree, including nested repositories.

    Git reads attributes from nested directories too, and a submodule's
    attributes apply inside its own worktree, so they cannot be skipped.
    """
    paths = []
    for dirpath, dirnames, filenames in _walk_worktree(worktree):
        dirnames[:] = [name for name in dirnames if name != '.git']
        if '.gitattributes' in filenames:
            paths.append(Path(dirpath) / '.gitattributes')
    return paths


def _nested_repository_roots(worktree):
    """Directories below *worktree* that have their own ``.git`` entry.

    Submodules store a ``.git`` *file*, standalone nested repositories a ``.git``
    directory; both are enumerated from the filesystem because ``git status`` in
    the parent enters an initialised submodule and runs its clean filter.
    """
    roots = []
    for dirpath, dirnames, filenames in _walk_worktree(worktree):
        path = Path(dirpath)
        nested = path != worktree and ('.git' in dirnames or '.git' in filenames)
        dirnames[:] = [name for name in dirnames if name != '.git']
        if nested:
            roots.append(path)
    return roots


def _layout_isolation_reason(layout):
    """Why this one repository layout can execute commands or fetch objects."""
    worktree, git_dir, common_dir = layout
    config_paths = []
    seen = set()
    for path in (common_dir / 'config', git_dir / 'config.worktree'):
        key = os.path.abspath(path)
        if key in seen:
            continue
        seen.add(key)
        config_paths.append(path)
    for path in config_paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return _UNREADABLE_CONFIG_MESSAGE
        reason = _config_isolation_reason(_config_entries(text))
        if reason:
            return reason
    for path in (git_dir / 'info' / 'attributes',
                 common_dir / 'info' / 'attributes'):
        if path.is_file() and _attributes_declare_filter(path):
            return _FILTER_ISOLATION_MESSAGE
    for name in ('alternates', 'http-alternates'):
        path = common_dir / 'objects' / 'info' / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return _UNREADABLE_CONFIG_MESSAGE
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Local object stores are plain reads; a URL alternate makes Git
            # fetch, and an ext:: alternate runs a command.
            if '://' in line or line.startswith('ext::'):
                return _PROMISOR_ISOLATION_MESSAGE
    return None


def _repository_isolation_error(repo_root):
    """Refuse to touch a repository whose own configuration can execute commands.

    Clean/process filters cannot be disabled through a single Git config override,
    and ``git status`` executes them while refreshing the index. The same is true
    for an initialised submodule: the parent's ``git status`` enters it and runs
    the submodule's own clean filter. Promisor remotes and ext:: transports can
    make object reads fetch or run a helper. Reading config, attributes and
    alternates files is enough to know that isolation is not possible, so the
    planner fails closed here *before* the first Git command instead of running a
    repository-defined command and reporting the side effect afterwards. A
    worktree directory that cannot be listed is also fatal: Git can still enter a
    submodule registered below it even though ``os.walk`` cannot enumerate that
    directory, so the scan would otherwise miss an executable nested config.
    """
    layout = _git_layout(repo_root)
    if layout is None:
        return _LAYOUT_ISOLATION_MESSAGE
    worktree = layout[0]
    reason = _layout_isolation_reason(layout)
    if reason:
        return reason
    try:
        for path in _attribute_files(worktree):
            if _attributes_declare_filter(path):
                return _FILTER_ISOLATION_MESSAGE
        nested_roots = _nested_repository_roots(worktree)
    except _UnlistableDirectoryError:
        # A directory Git can still traverse may hide an initialised submodule
        # with an executable config; refusing is the only way to stay before
        # the first Git command.
        return _TRAVERSAL_ISOLATION_MESSAGE
    for root in nested_roots:
        try:
            label = str(root.relative_to(worktree))
        except ValueError:
            label = root.name
        nested_layout = _git_layout(root)
        if nested_layout is None:
            return (f'nested Git repository (submodule) at {label}: '
                    + _LAYOUT_ISOLATION_MESSAGE)
        reason = _layout_isolation_reason(nested_layout)
        if reason:
            return f'nested Git repository (submodule) at {label}: ' + reason
    return None


def _git_environment():
    env = {key: value for key, value in os.environ.items()
           if key not in _UNSAFE_GIT_ENV
           and key not in _UNSAFE_PATHSPEC_ENV
           and not key.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_'))}
    env['GIT_NO_REPLACE_OBJECTS'] = '1'
    env['GIT_CONFIG_NOSYSTEM'] = '1'
    env['GIT_CONFIG_GLOBAL'] = os.devnull
    env['GIT_CONFIG_SYSTEM'] = os.devnull
    env['GIT_ATTR_NOSYSTEM'] = '1'
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_OPTIONAL_LOCKS'] = '0'
    # Belt and braces: a partial clone must never lazily fetch during an
    # evidence probe. The preflight already refuses promisor remotes.
    env['GIT_NO_LAZY_FETCH'] = '1'
    return env


def _run_git(repo_root, args):
    # Every Git command, including the public diff-digest helper, re-proves that
    # the repository cannot execute its own configuration first.
    isolation_error = _repository_isolation_error(repo_root)
    if isolation_error:
        raise GitEvidenceError(isolation_error)
    command = ['git', '--no-replace-objects']
    for setting in _SAFE_GIT_CONFIG:
        command.extend(['-c', setting])
    command.extend(['-C', str(repo_root), *args])
    try:
        return subprocess.run(command, capture_output=True, timeout=60,
                              env=_git_environment())
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Do not echo the caller's path into a report; name the failed action.
        raise GitEvidenceError('cannot execute git in the supplied repository') from exc


def _git_text(data):
    return data.decode('utf-8', 'replace').strip()


def _missing_evidence(reason):
    return {'status': 'MISSING', 'reasons': [reason], 'repo_root': None,
            'base_sha': None, 'head_sha': None, 'entries': [],
            'binary_paths': set(), 'ignored_paths': [], 'snapshot': None,
            'diff_sha256': None}


def _untrusted_evidence(reason):
    return {'status': 'UNTRUSTED', 'reasons': [reason], 'repo_root': None,
            'base_sha': None, 'head_sha': None, 'entries': [],
            'binary_paths': set(), 'ignored_paths': [], 'snapshot': None,
            'diff_sha256': None}


def _parse_status(data):
    """Return ``(dirty, ignored_paths)`` from ``git status --porcelain -z``."""
    dirty = False
    ignored = []
    for record in data.split(b'\0'):
        if not record:
            continue
        if record.startswith(b'!! '):
            ignored.append(_safe_git_path(os.fsdecode(record[3:])))
        else:
            dirty = True
    return dirty, ignored


def _hidden_index_flags(data):
    """List paths whose index entry hides worktree changes from ``status``."""
    flagged = []
    for record in data.split(b'\0'):
        if not record:
            continue
        if len(record) < 3 or record[1:2] != b' ':
            raise GitEvidenceError('unreadable index flag record')
        flag = record[:1]
        path = _safe_git_path(os.fsdecode(record[2:]))
        if flag != b'H':
            flagged.append(path)
    return flagged


def _snapshot_digest(head_sha, base_sha, status, index, raw, numstat, ignored):
    digest = hashlib.sha256()
    for item in (head_sha.encode('ascii'), base_sha.encode('ascii'),
                 status, index, raw, numstat,
                 '\0'.join(ignored).encode('utf-8', 'surrogateescape')):
        digest.update(item)
        digest.update(b'\0')
    return digest.hexdigest()


def _is_full_sha(value):
    """True only for an exact ``str`` of 40 lowercase hex characters.

    No coercion and no trimming: a leading space, a trailing newline, a bytes
    value or an object whose ``__str__`` looks like a SHA is not a SHA. ``$``
    alone would match before a final newline, hence ``fullmatch``.
    """
    return isinstance(value, str) and _SHA_RE.fullmatch(value) is not None


def _require_diff_sha(label, value):
    """Return a validated commit SHA or refuse before any Git process.

    Repository revisions are appended to a ``git diff`` command line. An
    unvalidated value such as ``--ext-diff``, ``--textconv`` or
    ``--output=/path`` lands *after* the protective switches and overrides them:
    the first two run repository-configured helpers, the last overwrites a file.
    Only an exact 40-character lowercase hex SHA may reach Git, so option
    injection and whitespace/non-string smuggling are rejected here, before
    ``_run_git`` starts anything.
    """
    if not _is_full_sha(value):
        raise GitEvidenceError(
            f'{label} must be a full 40-character hex Git SHA')
    return value


def _diff_digest(repo_root, base_sha, head_sha):
    base_sha = _require_diff_sha('base_sha', base_sha)
    head_sha = _require_diff_sha('head_sha', head_sha)
    completed = _run_git(repo_root, [
        'diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv',
        base_sha, head_sha])
    if completed.returncode != 0:
        raise GitEvidenceError('cannot read the full diff digest')
    return hashlib.sha256(completed.stdout).hexdigest()


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


def _blob_is_unproven(repo_root, sha):
    """True when a blob cannot be treated as text by this planner."""
    # Only an exact full hex object id may reach ``git cat-file``. Raw-diff
    # headers come from Git, but never let a malformed token become a
    # command-line option or smuggle a trailing newline into the argument.
    if not _is_full_sha(sha):
        return True
    if set(sha) <= {'0'}:
        return False
    size = _run_git(repo_root, ['cat-file', '-s', sha])
    if size.returncode != 0:
        return True
    try:
        length = int(_git_text(size.stdout))
    except ValueError:
        return True
    if length > 5_000_000:
        return True
    blob = _run_git(repo_root, ['cat-file', '-p', sha])
    if blob.returncode != 0:
        return True
    if b'\0' in blob.stdout:
        return True
    try:
        blob.stdout.decode('utf-8')
    except UnicodeDecodeError:
        return True
    return False


def _git_evidence_state(proposal, repo_root, required_base_sha=None,
                        required_head_sha=None):
    evidence = proposal.get('git_evidence')
    if evidence is None:
        if required_base_sha is None or required_head_sha is None:
            return _missing_evidence('no trusted task baseline/head supplied')
        evidence = {'base_sha': required_base_sha, 'head_sha': required_head_sha}
    if not isinstance(evidence, dict):
        return _untrusted_evidence('git_evidence must be a mapping')
    base_sha = evidence.get('base_sha')
    head_sha = evidence.get('head_sha')
    if not isinstance(base_sha, str) or not isinstance(head_sha, str):
        return _untrusted_evidence('git_evidence.base_sha and head_sha are required')
    if not _is_full_sha(base_sha) or not _is_full_sha(head_sha):
        return _untrusted_evidence(
            'git_evidence SHAs must be exactly 40 lowercase hex characters')
    if base_sha == head_sha:
        return _untrusted_evidence('git_evidence base_sha and head_sha must differ')
    if required_base_sha is not None:
        if not _is_full_sha(required_base_sha):
            return _untrusted_evidence(
                'task carrier base_sha is not a 40-character hex value')
        if base_sha != required_base_sha:
            return _untrusted_evidence(
                'git_evidence.base_sha does not match the task carrier base_sha')
    if required_head_sha is not None:
        if not _is_full_sha(required_head_sha):
            return _untrusted_evidence('task carrier head_sha is not a 40-character hex value')
        if head_sha != required_head_sha:
            return _untrusted_evidence(
                'git_evidence.head_sha does not match the task carrier head_sha')
    root = Path(repo_root or os.getcwd()).expanduser()
    isolation_error = _repository_isolation_error(root)
    if isolation_error:
        return _untrusted_evidence(isolation_error)
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
        status = git(['status', '--porcelain=v1', '-z',
                      '--untracked-files=all', '--ignored=matching'])
        if status.returncode != 0:
            raise GitEvidenceError('cannot read worktree status')
        dirty, ignored_paths = _parse_status(status.stdout)
        if dirty:
            raise GitEvidenceError(
                'working tree has uncommitted or untracked changes')
        allowed = [_canonical_allowed_path(item)
                   for item in proposal.get('allowed_paths') or []]
        overlapping = [path for path in ignored_paths
                       if any(_paths_overlap(path, item) for item in allowed)]
        if overlapping:
            raise GitEvidenceError(
                'ignored worktree paths overlap allowed_paths: '
                + ', '.join(overlapping[:5]))
        index = git(['ls-files', '-v', '-z'])
        if index.returncode != 0:
            raise GitEvidenceError('cannot read index flags')
        flagged = _hidden_index_flags(index.stdout)
        if flagged:
            raise GitEvidenceError(
                'index has assume-unchanged or skip-worktree entries: '
                + ', '.join(flagged[:5]))
        # ``--abbrev=40`` keeps raw-diff object ids at 40 hex characters so
        # every later ``cat-file`` argument is a full SHA, never an option.
        raw = git(['diff', '--raw', '-z', '--abbrev=40', '--find-renames',
                   '--find-copies', '--no-ext-diff', '--no-textconv',
                   base_sha, head_sha])
        if raw.returncode != 0:
            raise GitEvidenceError('cannot read the raw diff')
        numstat = git(['diff', '--numstat', '-z', '--find-renames', '--find-copies',
                       '--no-ext-diff', '--no-textconv', base_sha, head_sha])
        if numstat.returncode != 0:
            raise GitEvidenceError('cannot read numstat')
        entries = _parse_raw_diff(raw.stdout)
        binary_paths = _parse_numstat_binary(numstat.stdout)
        for entry in entries:
            for sha in (entry['old_sha'], entry['new_sha']):
                if _blob_is_unproven(repo, sha):
                    binary_paths.add(entry['new_path'])
                    if entry['old_path']:
                        binary_paths.add(entry['old_path'])
        snapshot = _snapshot_digest(
            head_sha, base_sha, status.stdout, index.stdout, raw.stdout,
            numstat.stdout, ignored_paths)
        return {
            'status': 'PRESENT', 'reasons': [], 'repo_root': repo,
            'base_sha': base_sha, 'head_sha': head_sha,
            'entries': entries, 'binary_paths': binary_paths,
            'ignored_paths': ignored_paths, 'snapshot': snapshot,
            'diff_sha256': _diff_digest(repo, base_sha, head_sha),
        }
    except GitEvidenceError as exc:
        return _untrusted_evidence(str(exc))


def _literal_pathspec(path):
    """Return *path* as a literal Git pathspec, disabling every magic prefix.

    Git reads the remainder of a ``:(literal)`` pathspec verbatim, so names
    beginning with ``:`` or ``:(glob)`` cannot be reinterpreted as patterns,
    exclusions or option-like magic.
    """
    return ':(literal)' + path


def _diff_lines_for_path(state, path):
    # This internal entry point validates its own state SHAs. The normal planning
    # chain already checked them, but a handcrafted state must not turn
    # ``--ext-diff`` / ``--output=<file>`` into a Git option and a side effect.
    base_sha = _require_diff_sha('base_sha', state['base_sha'])
    head_sha = _require_diff_sha('head_sha', state['head_sha'])
    # ``--`` stops option parsing but not pathspec magic: a changed path named
    # ``:(glob)foo.py`` would be read as the pathspec ``glob:foo.py`` and the
    # diff would silently come back empty. ``:(literal)`` makes Git read the
    # remainder verbatim. The Git child environment drops the pathspec switches
    # (see ``_UNSAFE_PATHSPEC_ENV``) so a caller cannot re-enable magic or turn
    # the literal prefix itself into a literal file name.
    completed = _run_git(state['repo_root'], [
        'diff', '-U0', '--no-color', '--no-ext-diff', '--no-textconv',
        base_sha, head_sha, '--', _literal_pathspec(path)])
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


def _lesson_scope_text(lesson):
    lesson = lesson or {}
    observation = lesson.get('observation') or {}
    applicability = lesson.get('applicability') or {}
    suggested = lesson.get('suggested_action') or {}
    return ' '.join([
        str(lesson.get('title') or ''),
        str(observation.get('root_cause') or ''),
        str(observation.get('impact') or ''),
        str(suggested.get('summary') or ''),
        ' '.join(applicability.get('applies_to') or []),
    ])


def _scope_leads(proposal, lesson):
    text = (_lesson_scope_text(lesson) + ' '
            + str(proposal.get('goal') or '')).casefold()
    keyword_hits = [keyword for keyword in SCOPE_KEYWORDS
                    if keyword.casefold() in text]
    high_paths = []
    authority_paths = []
    for path in proposal.get('allowed_paths') or []:
        normalized = _canonical_allowed_path(path)
        if _is_authority_path(normalized):
            # An authority surface escalates only together with a keyword (see
            # the high-risk escalation below), so it is tracked separately from
            # the always-suspicious protected / security / schema paths.
            authority_paths.append(normalized)
        if (_is_protected_path(normalized) or _is_security_path(normalized)
                or _is_schema_path(normalized)):
            high_paths.append(normalized)
    return keyword_hits, high_paths, authority_paths


def _destructive_intent_hits(proposal, lesson):
    """Destructive wording in the task scope. A lead only, never proof."""
    text = (_lesson_scope_text(lesson) + ' '
            + str(proposal.get('goal') or '')).casefold()
    return sorted({match.group(0).strip() for match in
                   DESTRUCTIVE_INTENT_RE.finditer(text)})


def _classify_git_evidence(proposal, lesson, evidence):
    result = {'high': [], 'content': [], 'authority': [], 'structural': [],
              'scope': [], 'decision_id': None}
    allowed = [_canonical_allowed_path(path)
               for path in proposal.get('allowed_paths') or []]

    for path in _changed_paths(evidence):
        if not any(_path_at_or_below(path, prefix) for prefix in allowed):
            _append_unique(
                result['structural'],
                f'diff changes a path outside allowed_paths: {path}')

    for entry in evidence['entries']:
        code = entry['status']
        path = entry['new_path']
        if code in ('R', 'C', 'T'):
            _append_unique(
                result['structural'],
                f'rename/copy/type change cannot be proven text-only: {path}')
            continue
        if code == 'D':
            _append_unique(
                result['structural'],
                f'deletion cannot be proven safe: {path}')
            continue
        if entry['old_mode'] in _SPECIAL_MODES or entry['new_mode'] in _SPECIAL_MODES:
            _append_unique(
                result['structural'],
                f'symlink or submodule change cannot be proven safe: {path}')
            continue
        modes_changed = entry['old_mode'] != entry['new_mode']
        if modes_changed and _EXECUTABLE_MODE in (entry['old_mode'], entry['new_mode']):
            _append_unique(
                result['high'],
                f'file mode/permission change: {path}')
            result['decision_id'] = result['decision_id'] or DECISION_HIGH_RISK
            continue
        if (modes_changed and '000000' not in (entry['old_mode'], entry['new_mode'])):
            _append_unique(
                result['high'],
                f'file mode change: {path}')
            result['decision_id'] = result['decision_id'] or DECISION_HIGH_RISK
            continue
        if _is_protected_path(path):
            _append_unique(
                result['high'],
                f'protected execution/release surface change: {path}')
            result['decision_id'] = result['decision_id'] or DECISION_HIGH_RISK
            continue
        if _is_schema_path(path):
            _append_unique(
                result['high'],
                f'contract/schema change: {path}')
            result['decision_id'] = result['decision_id'] or DECISION_CONTRACT
            continue
        if path in evidence['binary_paths']:
            _append_unique(
                result['structural'],
                f'binary change cannot be proven text-only: {path}')
            continue
        changed = _diff_lines_for_path(evidence, path)
        security_change = bool(_SECURITY_CHANGE_RE.search(changed))
        irreversible = bool(_IRREVERSIBLE_CHANGE_RE.search(changed))
        documentation = _is_documentation_path(path)
        if _is_authority_path(path):
            if security_change or irreversible:
                _append_unique(
                    result['high'],
                    f'authority surface permission/security change: {path}')
                result['decision_id'] = result['decision_id'] or DECISION_HIGH_RISK
            else:
                _append_unique(
                    result['authority'],
                    f'authority surface change needs Master review: {path}')
        elif documentation:
            _append_unique(
                result['content'],
                f'documentation change needs review: {path}')
        elif irreversible:
            _append_unique(
                result['high'],
                f'irreversible operation in the real diff: {path}')
            result['decision_id'] = result['decision_id'] or DECISION_HIGH_RISK
        elif security_change:
            _append_unique(
                result['high'],
                f'permission/security-looking code change: {path}')
            result['decision_id'] = result['decision_id'] or DECISION_HIGH_RISK
        elif _is_security_path(path):
            _append_unique(
                result['content'],
                f'security-sensitive code change needs review: {path}')
        else:
            _append_unique(
                result['content'],
                f'code change needs review: {path}')

    keyword_hits, high_paths, authority_paths = _scope_leads(proposal, lesson)
    intent_hits = _destructive_intent_hits(proposal, lesson)
    empty_change = not evidence['entries']
    if intent_hits:
        detail = 'a destructive or irreversible task goal/scope: ' + ', '.join(intent_hits)
        _append_unique(
            result['high'],
            ('an empty change set cannot prove ' if empty_change else
             'task goal/scope requests a change not proven by the current diff: ')
            + detail)
        result['decision_id'] = result['decision_id'] or DECISION_HIGH_RISK
    if (high_paths or authority_paths) and keyword_hits:
        # A scope lead is always HD-05: the diff does not prove a contract
        # change. HD-13 is reserved for a real diff on a schema/contract path.
        detail = ', '.join(sorted(set(keyword_hits + high_paths + authority_paths)))
        _append_unique(
            result['high'],
            ('an empty change set cannot prove a high-risk task scope: '
             if empty_change else
             'task scope requests a high-risk change not proven by the current diff: ')
            + detail)
        result['decision_id'] = result['decision_id'] or DECISION_HIGH_RISK
    elif high_paths:
        _append_unique(
            result['scope'],
            'task scope touches a high-risk path not proven by the current '
            'diff: ' + ', '.join(sorted(set(high_paths))))
    elif keyword_hits:
        _append_unique(
            result['content'],
            'task wording mentions a high-risk area; review the actual diff: '
            + ', '.join(sorted(set(keyword_hits))))

    return result


def _empty_change_reasons(result):
    """Why an empty change set cannot stand in for risk evidence."""
    reasons = ['an empty change set cannot prove the intended goal, the '
               'allowed-path scope or the authorization']
    for key in ('structural', 'scope', 'content', 'authority'):
        for reason in result[key]:
            _append_unique(reasons, reason)
    return reasons


def assess_risk_details(proposal, lesson, trusted_task=None, repo_root=None,
                        required_base_sha=None, required_head_sha=None):
    """Assess one proposal using *real Git evidence*, not caller text.

    An empty change set is never ``LOW``: it cannot prove the intended goal, the
    allowed-path scope or that the authorization is safe, so it stays
    ``NEEDS_EVIDENCE`` (or ``HIGH`` when the declared goal/scope is destructive,
    permission/security or otherwise high risk as far as the fixed path table and
    limited keyword checks can tell). Every non-empty content change is
    ``NEEDS_EVIDENCE`` or ``HIGH``, and a missing, forged or incomplete Git claim
    stays ``NEEDS_EVIDENCE``. A repository whose local config defines clean /
    process filters or config includes, whose submodule / nested repository
    defines them, or which declares a promisor remote, ``ext::`` transport or
    HTTP alternate is refused before any Git command with an explicit "not
    implemented" reason. ``LOW`` is reserved for the not-yet-wired
    authoritative carrier adapter plus independent review and is not returned by
    this slice. No caller-supplied review or authorization is accepted.
    """
    proposal = proposal or {}
    task_id = trusted_task.get('task_id') if isinstance(trusted_task, dict) else None
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
            'snapshot': None, 'diff_sha256': None,
            'git_base_sha': None, 'git_head_sha': None,
            'task_id': task_id, 'review_status': 'NOT_WIRED',
            'next_action': 'INVESTIGATE',
        }

    evidence = _git_evidence_state(proposal, repo_root,
                                   required_base_sha=required_base_sha,
                                   required_head_sha=required_head_sha)
    base_limitations = []
    if proposal.get('authorization') is not None or (
            isinstance(trusted_task, dict) and trusted_task.get('review') is not None):
        base_limitations.append(
            'caller-supplied review/authorization is ignored; the authoritative '
            'task-carrier adapter and independent review are not wired')

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
            'snapshot': evidence['snapshot'],
            'diff_sha256': evidence['diff_sha256'],
            'git_base_sha': evidence['base_sha'],
            'git_head_sha': evidence['head_sha'],
            'task_id': task_id, 'review_status': 'NOT_WIRED',
            'next_action': 'INVESTIGATE',
        }

    result = _classify_git_evidence(proposal, lesson, evidence)
    limitations = base_limitations + [
        'Git evidence proves the checked-out commit range and a clean worktree '
        'at assessment time; it does not cover later edits or another carrier',
        'no change set is auto-LOW: documentation, code, authority surfaces, '
        'binary, rename/copy/type, symlink, submodule, deletion and worktree '
        'state all need a trusted review or remain blocked',
        'an empty change set is not evidence of the proposed work: it proves '
        'neither the goal, the allowed-path scope nor that authorization is safe',
    ]
    if evidence.get('ignored_paths'):
        limitations.append(
            'ignored worktree paths outside allowed_paths were observed: '
            + ', '.join(evidence['ignored_paths'][:10]))
    common = {
        'evidence_status': 'PRESENT',
        'changed_paths': _changed_paths(evidence),
        'snapshot': evidence['snapshot'],
        'diff_sha256': evidence['diff_sha256'],
        'git_base_sha': evidence['base_sha'],
        'git_head_sha': evidence['head_sha'],
        'task_id': task_id,
        'limitations': limitations,
    }
    if result['high']:
        return {'level': RISK_HIGH,
                'decision_id': result['decision_id'] or DECISION_HIGH_RISK,
                'reasons': result['high'], 'leads': result['high'],
                'required_evidence': [], 'review_status': 'NOT_APPLICABLE',
                'next_action': 'OWNER_DECISION', **common}
    if not evidence['entries']:
        reasons = _empty_change_reasons(result)
        return {'level': RISK_NEEDS_EVIDENCE, 'decision_id': None,
                'reasons': reasons, 'leads': reasons,
                'required_evidence': [
                    'supply a verified commit range that actually contains the '
                    'proposed change; an empty diff cannot prove the goal, the '
                    'allowed-path scope or the authorization',
                    'a low-risk decision needs the not-yet-wired authoritative '
                    'task-carrier adapter plus independent review; nothing in '
                    'this slice can unlock LOW/READY from an empty change set'],
                'review_status': 'NOT_WIRED', 'next_action': 'INVESTIGATE',
                **common}
    blocking = result['structural'] + result['scope']
    if blocking:
        required = []
        if any('outside allowed_paths' in reason for reason in blocking):
            required.append(
                'amend allowed_paths through a scope decision or remove the '
                'out-of-scope change')
        if any('task scope' in reason for reason in blocking):
            required.append(
                'separate the high-risk scope into an independently reviewed '
                'task; the current diff does not cover it')
        if not required:
            required.append(
                'resolve the structural Git evidence gap before planning')
        return {'level': RISK_NEEDS_EVIDENCE, 'decision_id': None,
                'reasons': blocking, 'leads': blocking,
                'required_evidence': required, 'review_status': 'NOT_WIRED',
                'next_action': 'INVESTIGATE', **common}
    reviewable = result['content'] + result['authority']
    if reviewable:
        required = [
            'the current planner cannot prove semantic safety or independent '
            'review; a real task-carrier adapter and independent reviewer are '
            'not wired. Required later: task_id, goal/allowed-path hashes, '
            'base/head, complete diff digest, reviewer identity/role, scope, '
            'review time and expiry/revocation from the authoritative carrier'
        ]
        if result['authority']:
            required.append(
                'authority surfaces additionally require a verifiable '
                'Master/Owner decision from the carrier; caller-supplied '
                'review/authorization is not accepted')
        return {'level': RISK_NEEDS_EVIDENCE, 'decision_id': None,
                'reasons': reviewable, 'leads': reviewable,
                'required_evidence': required, 'review_status': 'NOT_WIRED',
                'next_action': 'MASTER_REVIEW', **common}
    # Fail closed: every non-empty change set already produces a lead above, so
    # this branch means the classifier could not account for the change. LOW is
    # deliberately not reachable in this slice — it needs the authoritative
    # task-carrier adapter and independent review, which are not wired.
    fallback = ['the verified change set produced no classifiable risk lead; '
                'treat it as unproven']
    return {'level': RISK_NEEDS_EVIDENCE, 'decision_id': None,
            'reasons': fallback, 'leads': fallback,
            'required_evidence': [
                'classify the changed paths through the authoritative carrier '
                'adapter and independent review before any low-risk decision'],
            'review_status': 'NOT_WIRED', 'next_action': 'INVESTIGATE',
            **common}


def git_diff_digest(repo_root, base_sha, head_sha):
    """Return the SHA-256 of the complete Git diff for a review receipt."""
    return _diff_digest(repo_root, base_sha, head_sha)


def recheck_git_evidence(proposal, repo_root=None, required_base_sha=None,
                         required_head_sha=None):
    """Re-read Git immediately before registration and compare the snapshot."""
    state = _git_evidence_state(
        proposal, repo_root, required_base_sha=required_base_sha,
        required_head_sha=required_head_sha)
    if state['status'] != 'PRESENT':
        return False, list(state['reasons'])
    expected = proposal.get('git_snapshot')
    if expected and state['snapshot'] != expected:
        return False, ['worktree or index changed between planning and registration']
    return True, []


def assess_risk(proposal, lesson, trusted_task=None, repo_root=None,
                required_base_sha=None, required_head_sha=None):
    """Return ``(level, decision_id, reasons)`` for one proposal.

    Backwards-compatible tuple view of :func:`assess_risk_details`.
    """
    details = assess_risk_details(proposal, lesson, trusted_task=trusted_task,
                                  repo_root=repo_root,
                                  required_base_sha=required_base_sha,
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
                   existing_task=None, trusted_task=None, repo_root=None,
                   required_base_sha=None, required_head_sha=None):
    """Turn one lesson + one explicit spec into a gated proposal.

    Status order is deliberate: illegal paths are rejected first, an OPEN conflict
    escalates next, and an evidenced or declared high-risk change is escalated
    before any structural check, so a broken packet never hides a human decision.
    A complete packet and verified dependencies are then required before the
    proposal can report "risk evidence is insufficient"; a missing or untrusted
    Git claim blocks with a concrete next step instead of becoming a false owner
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
    risk = assess_risk_details(proposal, lesson, trusted_task=trusted_task,
                               repo_root=repo_root,
                               required_base_sha=required_base_sha,
                               required_head_sha=required_head_sha)
    level = risk['level']
    proposal['risk_level'] = level
    proposal['risk_reasons'] = risk['reasons']
    proposal['risk_leads'] = risk['leads']
    proposal['required_evidence'] = risk['required_evidence']
    proposal['risk_limitations'] = risk['limitations']
    proposal['git_evidence_status'] = risk['evidence_status']
    proposal['changed_paths'] = risk['changed_paths']
    proposal['git_snapshot'] = risk['snapshot']
    proposal['diff_sha256'] = risk['diff_sha256']
    proposal['git_base_sha'] = risk.get('git_base_sha')
    proposal['git_head_sha'] = risk.get('git_head_sha')
    proposal['task_id'] = risk.get('task_id')
    proposal['review_status'] = risk.get('review_status', 'NOT_WIRED')
    proposal['next_action'] = risk.get('next_action', 'INVESTIGATE')
    if level == RISK_HIGH:
        proposal['status'] = 'NEEDS_HUMAN_DECISION'
        proposal['status_reason'] = 'high-risk change: ' + '; '.join(risk['reasons'])
        proposal['required_human_decision'] = risk['decision_id']
        return proposal

    # A broken packet or an unverified dependency is reported on its own terms
    # before "risk evidence is insufficient", so the concrete gap stays visible.
    if missing:
        proposal['status'] = 'BLOCKED'
        proposal['status_reason'] = 'task spec is incomplete: ' + ', '.join(missing)
        proposal['required_evidence'] = [
            'supply the missing task-packet fields: ' + ', '.join(missing)]
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

    if level == RISK_NEEDS_EVIDENCE:
        proposal['status'] = 'BLOCKED'
        proposal['status_reason'] = 'risk evidence is insufficient: ' + '; '.join(
            risk['reasons'])
        return proposal

    # Unreachable while :func:`assess_risk_details` returns no LOW: this is the
    # future wired-evidence path, not an auto-approval shortcut.
    proposal['status'] = 'READY'
    proposal['status_reason'] = 'eligible lesson, complete packet, verified dependencies, low risk'
    return proposal


def plan_tasks(*, lesson_records, specs=None, edition, dependency_state=None,
               coverage=None, existing_tasks=None, trusted_task=None,
               repo_root=None, required_base_sha=None, required_head_sha=None):
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
            trusted_task=trusted_task, repo_root=repo_root,
            required_base_sha=required_base_sha,
            required_head_sha=required_head_sha))
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
             f"- **Task ID:** {proposal.get('task_id') or '(assigned by `scripts/orchestrator.py` at dispatch)'}",
             f"- **Title:** {proposal.get('title')}",
             f"- **Goal / user-visible result:** {proposal.get('goal')}",
             f"- **Context:** {proposal.get('context')}",
             f"- **Lesson:** {proposal.get('lesson_id')}",
             '- **Evidence links:** ' + (' '.join(f'<{url}>' for url in proposal.get(
                 'evidence_urls') or []) or '(none)'),
             '- **Allowed paths:** ' + (', '.join(proposal.get('allowed_paths') or [])
                                        or '(missing)'),
             '- **Git base SHA:** ' + (proposal.get('git_base_sha') or '(missing)'),
             '- **Git head SHA:** ' + (proposal.get('git_head_sha') or '(missing)'),
             '- **Git diff SHA-256:** ' + (proposal.get('diff_sha256') or '(missing)'),
             '- **Changed paths:** ' + (', '.join(proposal.get('changed_paths') or [])
                                        or '(none)'),
             '- **Review status:** ' + str(proposal.get('review_status') or 'NOT_WIRED'),
             '- **Next action:** ' + str(proposal.get('next_action') or 'INVESTIGATE'),
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
             f"- Task ID: `{proposal.get('task_id') or '(unassigned)'}`",
             f"- Lesson: `{proposal.get('lesson_id')}`",
             '- Evidence: ' + (' '.join(f'<{url}>' for url in proposal.get(
                 'evidence_urls') or []) or '(none)'), '',
             '## 风险证据', '',
             f"- Git base SHA: `{proposal.get('git_base_sha') or '(missing)'}`",
             f"- Git head SHA: `{proposal.get('git_head_sha') or '(missing)'}`",
             f"- Git diff SHA-256: `{proposal.get('diff_sha256') or '(missing)'}`",
             '- Changed paths: ' + (', '.join(
                 f'`{path}`' for path in proposal.get('changed_paths') or []) or '(none)'),
             f"- Review status: `{proposal.get('review_status') or 'NOT_WIRED'}`",
             f"- Next action: `{proposal.get('next_action') or 'INVESTIGATE'}`", '',
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
