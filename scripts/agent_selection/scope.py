#!/usr/bin/env python3
"""Per-terminal scope resolution for local agent-selection records (#120).

The design contract is
``docs/design/agent-role-discovery-and-model-selection.md`` §8.2. The rules,
in the order they are applied:

1. A runtime session id is preferred when the runtime publishes one. The
   resolver only reads the auditable ``RUNTIME_SESSION_ENV_VARS`` below; unknown
   private runtime interfaces are not guessed. Future adapters can pass an
   explicit ``runtime_session_id`` instead of mutating the process environment.
2. An interactive TTY identity is also folded into the fingerprint. The raw
   terminal path is hashed immediately and never leaves this module.
3. ``AGENTORCH_SCOPE_ID`` is a readable *namespace / override*, not the whole
   identity. When a session or terminal identity exists it is appended to the
   namespace so two terminals that happen to export the same value still get
   different directories.
4. With only an explicit id (no session / TTY identity), the id must be unique
   per terminal. Reusing a namespace that already has claims cannot prove
   isolation, so the resolver warns and sets ``history_allowed=False``.
5. With no identity and no explicit id the resolver fails closed: no
   ``default`` / ``shared`` directory, no silent fallback.

A scope id only selects a local directory. It is never an authorization token.
``scope_directory()`` additionally verifies that the resulting path stays under
``<project>/.local/agent-orch`` and refuses symlinked components.
"""
from __future__ import annotations

import hashlib
import os
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    'IDENTITY_ENV_VARS',
    'RUNTIME_SESSION_ENV_VARS',
    'TERMINAL_SESSION_ENV_VARS',
    'NAMESPACE_ENV',
    'MAX_SCOPE_ID_LENGTH',
    'MAX_SCOPE_ID_BYTES',
    'AGENTORCH_DIRNAME',
    'ScopeError',
    'ScopeIdentityError',
    'UnsafeScopeId',
    'ScopeResolution',
    'resolve_scope',
    'probe_tty_name',
    'validate_scope_id',
    'agentorch_root',
    'looks_like_agentorch_root',
    'store_root',
    'scope_directory',
    'identity_digest',
]

PROJECT_ROOT_SEGMENTS = ('.local', 'agent-orch')
AGENTORCH_DIRNAME = 'agentorch'
NAMESPACE_ENV = 'AGENTORCH_SCOPE_ID'

# Explicit, auditable environment sources. Each entry names the variable and who
# publishes it; nothing here is an inferred or unpublished runtime interface.
# A runtime that does not appear in this list must set
# AGENTORCH_RUNTIME_SESSION_ID through its adapter, or the resolver treats the
# session as unidentified.
RUNTIME_SESSION_ENV_VARS = (
    ('AGENTORCH_RUNTIME_SESSION_ID',
     'AgentOrch adapter contract: runtime adapters export their stable session id here'),
)

TERMINAL_SESSION_ENV_VARS = (
    ('TERM_SESSION_ID', 'iTerm2 / macOS Terminal / Terminal-hosted sessions'),
    ('WT_SESSION', 'Windows Terminal (documented per-instance id)'),
    ('KITTY_WINDOW_ID', 'kitty terminal (documented per-window id)'),
    ('TMUX_PANE', 'tmux (documented per-pane id)'),
)

# Flat view used by tests and by callers that only want to display provenance.
IDENTITY_ENV_VARS = RUNTIME_SESSION_ENV_VARS + TERMINAL_SESSION_ENV_VARS

MAX_SCOPE_ID_LENGTH = 128
# A directory name is limited to 255 bytes on ext4/APFS/NTFS. Stay below that
# even when the id contains multi-byte characters; 200 leaves room for future
# suffixes without silently depending on ASCII-only input.
MAX_SCOPE_ID_BYTES = 200
# Long explicit namespaces keep a short readable prefix and add the *full*
# namespace digest, so truncation cannot collide two namespaces that share a
# prefix. 40 chars * at most 4 UTF-8 bytes + the two 16-hex digests = 196 bytes.
LONG_NAMESPACE_PREFIX_CHARS = 40
_DIGEST_HEX = 16
_SAFE_EXTRA = frozenset('._-')
_UNSET = object()


class ScopeError(Exception):
    """Base class for scope resolution / path-safety failures."""


class ScopeIdentityError(ScopeError):
    """No stable scope could be resolved, and no shared fallback is allowed."""


class UnsafeScopeId(ScopeError):
    """A scope id or the resulting directory path failed the safety checks."""


@dataclass(frozen=True)
class ScopeResolution:
    """Outcome of one scope resolution.

    ``scope_id`` is ``None`` only in the fail-closed case. ``history_allowed``
    says whether a caller may read or write persistent records for this scope;
    it is ``False`` when isolation cannot be proven.
    """

    scope_id: str | None
    namespace: str | None
    identity_fingerprint: str | None
    identity_sources: tuple[str, ...] = ()
    history_allowed: bool = False
    warning: str | None = None
    error: str | None = None
    mode: str = 'unresolved'
    _details: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def has_scope(self) -> bool:
        return self.scope_id is not None

    @property
    def has_identity(self) -> bool:
        return self.identity_fingerprint is not None

    def require_scope(self) -> str:
        if self.scope_id is None:
            raise ScopeIdentityError(self.error or 'no stable scope resolved')
        return self.scope_id

    def as_dict(self) -> dict:
        """Serializable summary that never contains a raw terminal path."""
        payload = {
            'scope_id': self.scope_id,
            'namespace': self.namespace,
            'identity_fingerprint': self.identity_fingerprint,
            'identity_sources': list(self.identity_sources),
            'history_allowed': self.history_allowed,
            'warning': self.warning,
            'error': self.error,
            'mode': self.mode,
        }
        payload.update(self._details)
        return payload


def probe_tty_name(stream=None) -> str | None:
    """Return the raw TTY name for an interactive stream, else ``None``.

    This is the only place that touches the process TTY. Tests replace or pass
    ``tty_name`` explicitly, so no test needs a real terminal.
    """
    stream = sys.stdin if stream is None else stream
    try:
        if not stream.isatty():
            return None
        return os.ttyname(stream.fileno())
    except (AttributeError, OSError, ValueError):
        return None


def identity_digest(kind: str, value: str) -> str:
    """Irreversible, domain-separated digest for one identity component."""
    material = f'agentorch-scope-v1\x00{kind}\x00{value}'.encode('utf-8')
    return hashlib.sha256(material).hexdigest()


def _invalid_char_reason(value: str) -> str | None:
    for char in value:
        if char in _SAFE_EXTRA or char.isalnum():
            continue
        if char.isspace():
            return 'contains whitespace'
        if unicodedata.category(char).startswith('C'):
            return 'contains a control or format character'
        return f'contains the unsupported character {char!r}'
    return None


def validate_scope_id(value: str) -> str:
    """Validate one directory-name component; return it unchanged or raise.

    Rejections: absolute paths, path separators, ``..``, any whitespace,
    control / format characters, a leading dot / underscore, a trailing dot,
    names longer than :data:`MAX_SCOPE_ID_LENGTH` characters, and names longer
    than :data:`MAX_SCOPE_ID_BYTES` UTF-8 bytes.
    """
    if not isinstance(value, str):
        raise UnsafeScopeId('scope id must be a string')
    if not value:
        raise UnsafeScopeId('scope id must not be empty')
    if value != value.strip():
        raise UnsafeScopeId('scope id must not have leading or trailing whitespace')
    if value in ('.', '..'):
        raise UnsafeScopeId('scope id must not be "." or ".."')
    if '..' in value:
        raise UnsafeScopeId('scope id must not contain ".."')
    if value[0] in '._':
        raise UnsafeScopeId('scope id must not start with "." or "_"')
    if value[-1] == '.':
        raise UnsafeScopeId('scope id must not end with "."')
    if len(value) > MAX_SCOPE_ID_LENGTH:
        raise UnsafeScopeId(
            f'scope id is longer than {MAX_SCOPE_ID_LENGTH} characters')
    if len(value.encode('utf-8')) > MAX_SCOPE_ID_BYTES:
        raise UnsafeScopeId(
            f'scope id is longer than {MAX_SCOPE_ID_BYTES} UTF-8 bytes')
    if value.startswith(('/', '\\')):
        raise UnsafeScopeId('scope id must not be an absolute path')
    if len(value) >= 2 and value[1] == ':':
        raise UnsafeScopeId('scope id must not be a drive-qualified path')
    if '/' in value or '\\' in value:
        raise UnsafeScopeId('scope id must not contain a path separator')
    reason = _invalid_char_reason(value)
    if reason:
        raise UnsafeScopeId(f'scope id {reason}')
    # Windows devices are not a Linux problem, but the same repository can be
    # checked out on Windows; refusing them keeps the directory name portable.
    if value.split('.', 1)[0].upper() in {
            'CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5',
            'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 'LPT3', 'LPT4',
            'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'}:
        raise UnsafeScopeId('scope id must not be a reserved Windows device name')
    return value


def _project_root(project_root) -> Path:
    root = Path(project_root)
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise UnsafeScopeId(f'project root is not readable: {exc}') from exc
    if not root.is_dir():
        raise UnsafeScopeId('project root is not a directory')
    return root


def agentorch_root(start=None) -> Path:
    """AgentOrch Root derived from this file's location, never from the cwd.

    A checkout (new-project layout) resolves to the repository root; in an
    isolated install this module lives at ``<host>/agentorch/scripts/...`` so the
    same rule resolves to ``<host>/agentorch``.
    """
    if start is not None:
        return Path(start).resolve()
    return Path(__file__).resolve().parents[2]


def looks_like_agentorch_root(path) -> bool:
    """True when *path* carries the two markers of an AgentOrch root."""
    path = Path(path)
    return ((path / 'scripts' / 'orchestrator.py').is_file()
            and ((path / 'VERSION.md').is_file() or (path / 'MANIFEST.json').is_file()))


def store_root(project_root) -> Path:
    """Return the local store root, after refusing symlink escapes.

    An isolated install keeps AgentOrch under ``<host>/agentorch/``; its private
    state belongs to AgentOrch, not to the host project, so the store moves with
    it. A checkout / new-project layout keeps the historical
    ``<project>/.local/agent-orch`` so existing callers and tests are unchanged.
    """
    root = _project_root(project_root)
    nested = root / AGENTORCH_DIRNAME
    base = (nested if nested.is_dir() and not nested.is_symlink()
            and looks_like_agentorch_root(nested) else root)
    local = base / PROJECT_ROOT_SEGMENTS[0]
    store = local / PROJECT_ROOT_SEGMENTS[1]
    for component in (local, store):
        if component.is_symlink():
            raise UnsafeScopeId(
                f'refusing symlinked local store component: '
                f'{component.relative_to(root).as_posix()}')
    return store


def scope_directory(project_root, scope_id: str) -> Path:
    """Resolve the one legal directory for *scope_id* under the project store.

    The id is validated first, then the resulting path is resolved and compared
    with the store root so a symlink or an unexpected traversal cannot escape.
    """
    validate_scope_id(scope_id)
    store = store_root(project_root)
    candidate = store / scope_id
    if candidate.is_symlink():
        raise UnsafeScopeId('refusing a symlinked scope directory')
    try:
        resolved = candidate.resolve()
        resolved_store = store.resolve()
    except OSError as exc:
        raise UnsafeScopeId(f'could not resolve scope directory: {exc}') from exc
    if resolved.parent != resolved_store:
        raise UnsafeScopeId('scope directory escapes .local/agent-orch')
    return candidate


def _first_env(env, variables) -> tuple[str | None, str | None]:
    for name, _description in variables:
        value = env.get(name)
        if isinstance(value, str) and value.strip():
            return name, value
    return None, None


def _namespaced_scope_id(namespace: str, fingerprint: str) -> str:
    """Scope id for ``namespace + identity`` that never exceeds the limits.

    A short namespace keeps its natural ``<namespace>--<identity>`` form. A long
    one keeps a readable prefix plus the *full* SHA-256 digest of the namespace,
    so two namespaces sharing a long prefix never collide. The identity digest
    is always appended, so the same namespace still separates two terminals.
    """
    suffix = fingerprint[:_DIGEST_HEX]
    natural = f'{namespace}--{suffix}'
    if (len(natural) <= MAX_SCOPE_ID_LENGTH
            and len(natural.encode('utf-8')) <= MAX_SCOPE_ID_BYTES):
        return natural
    namespace_digest = hashlib.sha256(
        b'agentorch-scope-namespace-v1\x00' + namespace.encode('utf-8')
    ).hexdigest()[:_DIGEST_HEX]
    prefix = namespace[:LONG_NAMESPACE_PREFIX_CHARS]
    return f'{prefix}--{namespace_digest}--{suffix}'


def resolve_scope(project_root=None, *, env=None, tty_name=_UNSET,
                  runtime_session_id=None, namespace=None,
                  namespace_claims=None) -> ScopeResolution:
    """Resolve the stable scope for this terminal / session.

    Parameters
    ----------
    project_root:
        Accepted for symmetry with :func:`scope_directory`; resolution itself
        does not touch the filesystem. Unused, but kept so callers cannot
        accidentally forget which project the scope belongs to.
    env:
        Mapping used instead of ``os.environ`` (tests pass a plain dict).
    tty_name:
        ``_UNSET`` probes the process TTY; ``None`` means "no interactive TTY";
        a string simulates one.
    runtime_session_id:
        Adapter-supplied session id. Takes precedence over the environment list.
    namespace:
        Explicit override for ``AGENTORCH_SCOPE_ID`` (otherwise read from env).
    namespace_claims:
        Identity fingerprints already recorded for the explicit namespace.
        Supplied by the store so this module stays read-only.
    """
    del project_root  # documented above; resolution is intentionally pure
    env = dict(os.environ) if env is None else dict(env)

    raw_namespace = namespace if namespace is not None else env.get(NAMESPACE_ENV)
    namespace_value = None
    if raw_namespace is not None and str(raw_namespace) != '':
        # No strip: a leading / trailing space is a whitespace anomaly and must
        # be rejected rather than silently normalised into a different scope.
        namespace_value = validate_scope_id(str(raw_namespace))

    components: list[tuple[str, str, str]] = []
    sources: list[str] = []

    session_name = None
    session_value = runtime_session_id
    if session_value is None:
        session_name, session_value = _first_env(env, RUNTIME_SESSION_ENV_VARS)
    if isinstance(session_value, str) and session_value.strip():
        components.append(('runtime-session', 'runtime-session',
                           session_value.strip()))
        sources.append(f'runtime-session:{session_name or "adapter"}')

    terminal_name, terminal_value = _first_env(env, TERMINAL_SESSION_ENV_VARS)
    if terminal_value is not None:
        components.append(('terminal-session', terminal_name, terminal_value))
        sources.append(f'terminal-session:{terminal_name}')

    if tty_name is _UNSET:
        tty_name = probe_tty_name()
    if isinstance(tty_name, str) and tty_name.strip():
        components.append(('tty', 'tty', tty_name))
        sources.append('tty')

    fingerprint = None
    if components:
        material = '\n'.join(f'{kind}\x00{name}\x00{value}'
                             for kind, name, value in components)
        fingerprint = hashlib.sha256(
            b'agentorch-scope-identity-v1\x00' + material.encode('utf-8')).hexdigest()

    claims = tuple(namespace_claims or ())
    warning = None
    history_allowed = True

    if fingerprint is not None:
        if namespace_value is not None:
            scope_id = _namespaced_scope_id(namespace_value, fingerprint)
            mode = 'namespace+identity'
        else:
            kind = 'session' if all(c[0] == 'runtime-session' for c in components) \
                else 'terminal'
            scope_id = f'{kind}-{fingerprint[:20]}'
            mode = 'identity'
        others = {claim for claim in claims if claim not in ('unverified', fingerprint)}
        if 'unverified' in claims:
            warning = (
                'this explicit namespace also has records created without a '
                'verifiable terminal identity; those records are not read.')
        elif others:
            warning = (
                f'this explicit namespace is shared by {len(others) + 1} verified '
                'terminal / session identities; each one has its own directory.')
    elif namespace_value is not None:
        scope_id = namespace_value
        mode = 'namespace-only'
        if claims:
            history_allowed = False
            warning = (
                'AGENTORCH_SCOPE_ID was already used by another run and no '
                'terminal / session identity is available, so isolation cannot '
                'be proven; old history is disabled. Use a unique id per '
                'terminal, or run in one-shot isolation mode.')
        else:
            warning = (
                'no terminal / session identity is available; AGENTORCH_SCOPE_ID '
                'is the only scope marker, so it must be unique to this terminal.')
    else:
        scope_id = None
        mode = 'unresolved'
        history_allowed = False
        error = (
            'cannot determine a stable scope: no runtime session id, terminal '
            'identity or interactive TTY was found. Set a unique '
            f'{NAMESPACE_ENV} per terminal, or run in one-shot isolation mode '
            '(no history is read or written). Falling back to a shared / default '
            'directory is not allowed.')
        return ScopeResolution(
            scope_id=None, namespace=None, identity_fingerprint=None,
            identity_sources=tuple(sources), history_allowed=False,
            warning=warning, error=error, mode=mode)

    # Belt and braces: every produced id must itself be a legal component.
    validate_scope_id(scope_id)
    return ScopeResolution(
        scope_id=scope_id, namespace=namespace_value,
        identity_fingerprint=fingerprint, identity_sources=tuple(sources),
        history_allowed=history_allowed, warning=warning, error=None, mode=mode,
        _details={'identity_components': len(components)},
    )
