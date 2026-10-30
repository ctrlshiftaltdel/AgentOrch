#!/usr/bin/env python3
"""Runtime adapter contract for agent discovery and role matching (#121, S2).

Design contract: ``docs/design/agent-role-discovery-and-model-selection.md``
§4–§7. This module defines **only** what a runtime fact is; it does not define
or redefine C1–C8. C1–C4 stay authoritative in ``kit/orchestration.md`` §5 and
C5–C8 in ``templates/runtime-capability.md``; here they are consumed as
evidence and gated, never re-specified.

Three boundaries are enforced in code rather than in prose:

1. **A runtime adapter reports only what the runtime actually exposed.** There
   is no helper that turns a prompt name, a directory name or a config key into
   a capability. An adapter that cannot enumerate agents says so
   (:data:`DISCOVERY_UNSUPPORTED`) instead of returning a guessed list.
2. **Capabilities and permissions are different objects.** A ``PASS`` here says
   what the runtime was observed to do. :class:`PermissionScope` is descriptive
   evidence of an observed boundary; neither one grants a path, a command or a
   network access to any task.
3. **UNKNOWN / STALE never becomes PASS.** A ``PASS`` verdict is rejected at
   construction time unless it carries ``OBSERVED`` evidence with a probe date
   and an addressable evidence reference. A stale probe is downgraded to
   ``STALE`` rather than silently reused.

Standard library only; no network access; no filesystem access.
"""
from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = [
    'CAPABILITY_IDS',
    'GATING_CAPABILITIES',
    'VERDICTS',
    'EVIDENCE_CLASSES',
    'CANONICAL_IDENTITY_STATUSES',
    'RECORD_STATUSES',
    'DISCOVERY_OUTCOMES',
    'DISCOVERY_MODES',
    'CANDIDATE_SOURCES',
    'ROLE_DIMENSIONS',
    'ROLE_GRADES',
    'DEFAULT_MAX_EVIDENCE_AGE_DAYS',
    'RuntimeAdapterError',
    'Capability',
    'CapabilityRecord',
    'SubagentGate',
    'PermissionScope',
    'RoleEvidence',
    'CanonicalIdentity',
    'AgentCandidate',
    'RuntimeDescription',
    'DiscoveryResult',
    'RuntimeAdapter',
    'HostOpaqueRuntimeAdapter',
    'parse_timestamp',
    'evidence_age_days',
    'covers_path',
    'covers_command',
]

# The capability ids are the existing contract ids: C1-C4 from
# kit/orchestration.md §5 and C5-C8 from templates/runtime-capability.md.
CAPABILITY_IDS = ('C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8')
# Only these gate the real automatic dispatch path (kit §5).
GATING_CAPABILITIES = ('C1', 'C2', 'C3', 'C4')

VERDICTS = ('PASS', 'FAIL', 'UNKNOWN', 'NOT_APPLICABLE')
# Record-source classes from design §4. They annotate trust; they do not replace
# a Verdict or a Status.
EVIDENCE_CLASSES = ('OBSERVED', 'INFERRED', 'UNKNOWN', 'STALE')
RECORD_STATUSES = ('COMPLETE', 'PARTIAL', 'STALE')
DISCOVERY_OUTCOMES = ('OK', 'EMPTY', 'UNSUPPORTED', 'NO_ADAPTER', 'ERROR')
DISCOVERY_MODES = ('runtime', 'explicit-registry', 'none')
CANDIDATE_SOURCES = ('runtime-discovery', 'explicit-registry')

ROLE_DIMENSIONS = {
    'master': ('planning', 'quality-judgement', 'instruction-following'),
    'execution': ('coding', 'tool-use', 'instruction-following',
                  'delivery-efficiency'),
    'reviewer': ('defect-detection', 'test-design', 'security-review'),
}
ROLE_GRADES = ('STRONG', 'MODERATE', 'WEAK', 'UNKNOWN')

# (#170) A canonical model identity is the operator-declared underlying model
# behind one or more model ids. Only ``CONFIRMED`` (the operator checked the
# mapping in the current scope registry) is trusted, and only while its
# ``verified_until`` has not passed; every other status is reported and never
# promoted to reviewer-independence evidence. The token deliberately avoids the
# task state machine's vocabulary so a selection report cannot be read as one.
CANONICAL_IDENTITY_STATUSES = ('CONFIRMED', 'UNCONFIRMED', 'ABSENT', 'EXPIRED',
                               'CONFLICT', 'INVALID')

# The project interval after which a capability probe must be repeated. It is a
# module constant so callers can pass a stricter value; it is never adapted per
# candidate to make a stale probe look fresh.
DEFAULT_MAX_EVIDENCE_AGE_DAYS = 30

TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
# A drive-qualified path (C:/..., c:\...) names a host location; the virtual
# project-relative namespace cannot compare it, so it is never coverage proof.
_DRIVE_PATH_RE = re.compile(r'^[A-Za-z]:')
# A URI scheme (s3://..., file://...) also names a location outside the
# project-relative namespace.
_SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.\-]*://')


class RuntimeAdapterError(ValueError):
    """A runtime fact was constructed in a way the contract cannot accept."""


def parse_timestamp(value):
    """Parse a UTC ``YYYY-MM-DDTHH:MM:SSZ`` string; raise on anything else."""
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise RuntimeAdapterError(
            f'expected a UTC timestamp like 2026-10-27T12:00:00Z, got {value!r}')
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=timezone.utc)
    except ValueError as exc:
        raise RuntimeAdapterError(f'invalid UTC timestamp {value!r}: {exc}') from exc


def _now(value):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return parse_timestamp(str(value))


def evidence_age_days(probe_date, now=None):
    """Age of *probe_date* in days; ``None`` when it cannot be parsed."""
    try:
        observed = parse_timestamp(probe_date)
    except RuntimeAdapterError:
        return None
    return max(0.0, (_now(now) - observed).total_seconds() / 86400.0)


def _gating_row_freshness(capability, current, max_age_days):
    """Freshness of one gating capability row, dated by the row alone (#205).

    The record header (:attr:`CapabilityRecord.probe_date`) is a coarse
    pre-check; it never dates a C1-C4 row. Only the row's own ``probe_date``
    says when that capability was observed, so a re-dated header cannot turn
    old evidence into fresh evidence.

    Returns ``(kind, detail)`` with ``kind`` one of:

    * ``None`` - the row is fresh under ``max_age_days`` (or no limit is set);
    * ``'STALE'`` - the row is older than ``max_age_days``; re-probe it;
    * ``'UNKNOWN'`` - the row carries no usable timestamp: missing,
      unparsable, or in the future. A future timestamp is not evidence of
      freshness: :func:`evidence_age_days` clamps negative ages to ``0.0``, so
      reusing it here would read "logged yesterday" as fresh. The clamp itself
      is deliberately left unchanged because candidate evidence time shares it.
    """
    capability_id = capability.capability_id
    if capability.probe_date is None:
        return 'UNKNOWN', (f'{capability_id}: no probe date; freshness cannot '
                           'be verified')
    try:
        observed = parse_timestamp(capability.probe_date)
    except RuntimeAdapterError:
        return 'UNKNOWN', (f'{capability_id}: probe date cannot be parsed; '
                           'freshness cannot be verified')
    if observed > current:
        return 'UNKNOWN', (
            f'{capability_id}: probe date {capability.probe_date} is in the '
            'future; freshness cannot be verified, re-probe before use')
    age = (current - observed).total_seconds() / 86400.0
    if max_age_days is not None and age > max_age_days:
        return 'STALE', (
            f'{capability_id}: evidence is {age:.1f} days old '
            f'(probe {capability.probe_date}, limit {max_age_days}); '
            're-probe before use')
    return None, None


def covers_path(scope_paths, needed):
    """Whether one observed scope of *scope_paths* covers *needed*.

    Coverage is a purely lexical comparison inside the project-relative
    virtual namespace a runtime reported. It is not a host filesystem or
    symlink check, and ``True`` here never grants access to any path.

    * ``''`` and whitespace-only entries are unusable observations, not
      wildcards: they cover nothing. Only an exact ``'*'`` is a wildcard:
      surrounding whitespace is never trimmed into one, and ``' * '`` or
      ``'docs '`` are not comparable.
    * ``'*'`` and the project-root spellings ``'.'`` / ``'./'`` cover every
      comparable relative path.
    * Matching is component-aware, so ``docs`` does not cover ``docs-other``.
    * ``.`` segments, repeated separators and backslashes are resolved
      lexically; a *needed* path may also resolve internal ``..`` segments. A
      scope entry containing ``..`` is not proof of a boundary and is
      discarded.
    * Scheme URIs (``s3://...``), absolute paths, Windows drive paths (also
      after a lexical ``./``) and any path that escapes the project root
      cannot be compared here and are never covered - not even by an explicit
      wildcard.
    """
    needed_kind, needed_parts = _normalise_path(needed, allow_parent=True)
    if needed_kind not in ('root', 'path'):
        return False
    for scope_path in scope_paths or ():
        kind, parts = _normalise_path(scope_path, allow_parent=False)
        if kind in ('wildcard', 'root'):
            return True
        if kind == 'path' and needed_kind == 'path':
            if (len(parts) <= len(needed_parts)
                    and needed_parts[:len(parts)] == parts):
                return True
    return False


def _normalise_path(value, *, allow_parent):
    """Classify one logical runtime path for comparison.

    Returns ``(kind, components)`` where ``kind`` is ``'wildcard'`` (exact
    ``*``), ``'root'`` (``.`` / ``./``), ``'path'`` (a relative path that stays
    inside the project root) or ``'invalid'`` (blank, surrounding whitespace,
    URI scheme, absolute, drive-qualified, non-string, or escaping the root).
    Components are normalised lexically: backslashes become separators, empty
    and ``.`` segments are dropped. With ``allow_parent=True`` a ``..`` segment
    pops the previous segment; scope entries pass ``False`` because a reported
    boundary containing ``..`` cannot be proven and must not become coverage.
    """
    if not isinstance(value, str):
        return 'invalid', ()
    text = value.replace('\\', '/')
    if not text or text != text.strip():
        return 'invalid', ()
    if text == '*':
        return 'wildcard', ()
    probe = text
    while probe.startswith('./'):
        probe = probe[2:]
    if not probe:
        return 'root', ()
    if (probe.startswith('/') or _DRIVE_PATH_RE.match(probe)
            or _SCHEME_RE.match(probe)):
        return 'invalid', ()
    parts = []
    for segment in probe.split('/'):
        if segment in ('', '.'):
            continue
        if segment == '..':
            if not allow_parent or not parts:
                return 'invalid', ()
            parts.pop()
            continue
        if _DRIVE_PATH_RE.match(segment):
            return 'invalid', ()
        parts.append(segment)
    if not parts:
        return 'root', ()
    return 'path', tuple(parts)


def covers_command(scope_commands, needed):
    """Whether one observed command permission covers *needed*.

    Only an exact command name or the explicit wildcard ``*`` matches. Blank or
    whitespace-only entries are ignored: an empty string is never a wildcard,
    and this helper never grants a command that was not observed.
    """
    wanted = str(needed).strip()
    if not wanted:
        return False
    for allowed in scope_commands or ():
        if not isinstance(allowed, str):
            continue
        candidate = allowed.strip()
        if not candidate:
            continue
        if candidate == '*' or candidate == wanted:
            return True
    return False


@dataclass(frozen=True)
class Capability:
    """One C1–C8 row, consumed from the existing capability contract."""

    capability_id: str
    verdict: str
    source_class: str = 'UNKNOWN'
    probe_date: str | None = None
    evidence_ref: str | None = None
    limits: str = ''

    def __post_init__(self):
        if self.capability_id not in CAPABILITY_IDS:
            raise RuntimeAdapterError(
                f'unknown capability id {self.capability_id!r}; expected '
                f'one of {CAPABILITY_IDS}')
        if self.verdict not in VERDICTS:
            raise RuntimeAdapterError(
                f'{self.capability_id}: unknown verdict {self.verdict!r}; '
                f'expected one of {VERDICTS}')
        if self.source_class not in EVIDENCE_CLASSES:
            raise RuntimeAdapterError(
                f'{self.capability_id}: unknown source class '
                f'{self.source_class!r}; expected one of {EVIDENCE_CLASSES}')
        if self.probe_date is not None:
            parse_timestamp(self.probe_date)
        if self.verdict == 'PASS':
            # A PASS that cannot be pointed at must not exist in the model: it
            # is the one shortcut that would unlock the automatic path.
            if self.source_class != 'OBSERVED':
                raise RuntimeAdapterError(
                    f'{self.capability_id}: PASS requires OBSERVED evidence, '
                    f'got {self.source_class}')
            if not self.probe_date or not self.evidence_ref:
                raise RuntimeAdapterError(
                    f'{self.capability_id}: PASS requires a probe date and an '
                    'addressable evidence_ref')


@dataclass(frozen=True)
class CapabilityRecord:
    """A runtime's capability record: identity plus C1–C8 rows.

    ``status`` follows ``templates/runtime-capability.md``: ``PARTIAL`` and
    ``STALE`` can never unlock the sub-agent path.
    """

    runtime_id: str
    runtime_version: str
    edition: str
    execution_mode: str
    status: str
    probe_date: str
    evidence_ref: str
    capabilities: tuple[Capability, ...] = ()

    def __post_init__(self):
        for name in ('runtime_id', 'runtime_version', 'execution_mode',
                     'evidence_ref'):
            if not isinstance(getattr(self, name), str) or not getattr(
                    self, name).strip():
                raise RuntimeAdapterError(f'capability record needs a {name}')
        if self.edition not in ('local', 'github'):
            raise RuntimeAdapterError(
                f'edition must be local or github, got {self.edition!r}')
        if self.status not in RECORD_STATUSES:
            raise RuntimeAdapterError(
                f'unknown record status {self.status!r}; expected one of '
                f'{RECORD_STATUSES}')
        parse_timestamp(self.probe_date)
        seen = set()
        for capability in self.capabilities:
            if capability.capability_id in seen:
                raise RuntimeAdapterError(
                    f'duplicate capability row {capability.capability_id}')
            seen.add(capability.capability_id)

    def get(self, capability_id):
        for capability in self.capabilities:
            if capability.capability_id == capability_id:
                return capability
        return None

    def gate(self, *, now=None,
             max_age_days=DEFAULT_MAX_EVIDENCE_AGE_DAYS) -> 'SubagentGate':
        """Whether C1–C4 allow a real sub-agent dispatch from this record.

        Freshness is decided per gating row as well as for the record header,
        with the same ``now``, the same ``max_age_days`` and the same rules: a
        future-dated header or row is unverifiable evidence (``UNKNOWN``), not
        the freshest evidence, even when ``max_age_days`` is ``None``. A
        freshly re-dated header alone never makes stale row evidence fresh.
        """
        current = _now(now)
        reasons = []
        if self.status == 'STALE':
            return SubagentGate(
                status='BLOCKED', stale=True,
                reasons=('capability record is STALE and must be re-probed',),
                missing=tuple(GATING_CAPABILITIES))
        if self.status == 'PARTIAL':
            reasons.append('capability record is PARTIAL; the sub-agent path '
                           'needs COMPLETE C1-C4 evidence')

        # The header is dated here, by the same rule as each gating row, rather
        # than through evidence_age_days(): that helper clamps a future date to
        # 0.0 days (candidate evidence time shares the clamp), so a record
        # stamped in the future would otherwise read as the freshest possible
        # evidence.
        header_unverified = False
        try:
            header_probe = parse_timestamp(self.probe_date)
        except RuntimeAdapterError:
            header_probe = None
        if header_probe is None:
            header_unverified = True
            reasons.append('capability probe date cannot be parsed; freshness '
                           'cannot be verified')
        elif header_probe > current:
            header_unverified = True
            reasons.append(
                f'capability probe date {self.probe_date} is in the future; '
                'freshness cannot be verified, re-probe before use')
        else:
            age = (current - header_probe).total_seconds() / 86400.0
            if max_age_days is not None and age > max_age_days:
                return SubagentGate(
                    status='BLOCKED', stale=True,
                    reasons=(f'capability evidence is {age:.1f} days old '
                             f'(limit {max_age_days}); re-probe before use',),
                    missing=tuple(GATING_CAPABILITIES))

        missing = []
        unknown = []
        stale_rows = []
        for capability_id in GATING_CAPABILITIES:
            capability = self.get(capability_id)
            if capability is None:
                missing.append(capability_id)
                reasons.append(f'{capability_id}: no capability row recorded')
                continue
            if capability.source_class == 'STALE':
                return SubagentGate(
                    status='BLOCKED', stale=True,
                    reasons=(f'{capability_id}: evidence is STALE',),
                    missing=tuple(GATING_CAPABILITIES))
            if capability.verdict == 'PASS':
                kind, detail = _gating_row_freshness(
                    capability, current, max_age_days)
                if kind == 'STALE':
                    stale_rows.append(capability_id)
                    reasons.append(detail)
                elif kind == 'UNKNOWN':
                    unknown.append(capability_id)
                    reasons.append(detail)
                continue
            if capability.verdict == 'UNKNOWN':
                unknown.append(capability_id)
                reasons.append(f'{capability_id}: UNKNOWN; not a PASS')
            else:
                reasons.append(
                    f'{capability_id}: {capability.verdict}; automatic dispatch '
                    'stays closed')

        if stale_rows:
            return SubagentGate(
                status='BLOCKED', stale=True, reasons=tuple(reasons),
                missing=tuple(stale_rows + missing + unknown))
        if missing or unknown or header_unverified:
            return SubagentGate(status='UNKNOWN', reasons=tuple(reasons),
                                missing=tuple(missing + unknown))
        if reasons:
            return SubagentGate(status='BLOCKED', reasons=tuple(reasons))
        return SubagentGate(status='PASS',
                            reasons=('C1-C4 all PASS with fresh OBSERVED '
                                     'evidence',))

    def as_dict(self):
        return {
            'runtime_id': self.runtime_id,
            'runtime_version': self.runtime_version,
            'edition': self.edition,
            'execution_mode': self.execution_mode,
            'status': self.status,
            'probe_date': self.probe_date,
            'evidence_ref': self.evidence_ref,
            'capabilities': [
                {'capability_id': c.capability_id, 'verdict': c.verdict,
                 'source_class': c.source_class, 'probe_date': c.probe_date,
                 'evidence_ref': c.evidence_ref, 'limits': c.limits}
                for c in self.capabilities
            ],
        }


@dataclass(frozen=True)
class SubagentGate:
    """Result of gating C1–C4; ``PASS`` is the only value that opens the path."""

    status: str
    reasons: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    stale: bool = False

    def __post_init__(self):
        if self.status not in ('PASS', 'BLOCKED', 'UNKNOWN'):
            raise RuntimeAdapterError(
                f'gate status must be PASS / BLOCKED / UNKNOWN, got '
                f'{self.status!r}')

    @property
    def passed(self):
        return self.status == 'PASS'

    def as_dict(self):
        return {'status': self.status, 'reasons': list(self.reasons),
                'missing': list(self.missing), 'stale': self.stale}


@dataclass(frozen=True)
class PermissionScope:
    """Observed permission boundary of one candidate — never a grant.

    A selector compares this against the task's already-approved paths. It may
    only narrow what the task already allows; a value here can never widen an
    allowed path, add a command or enable network access.

    An empty ``read_paths`` / ``write_paths`` / ``commands`` tuple means *no
    permission in that class was observed* — not "the candidate was observed to
    have none". ``network_observed`` carries the same distinction for network
    access: ``None`` is unobserved, ``False`` is observed as unavailable.
    Blank or whitespace-only path entries are unusable observations too: they
    cover nothing and are never read as an explicit ``*``. The same applies to
    a path with surrounding whitespace, a URI scheme, an absolute path or a
    Windows drive path: none of them is comparable in the virtual namespace.
    """

    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    network: bool = False
    network_observed: bool | None = None
    read_only: bool = False

    def __post_init__(self):
        if self.read_only and self.write_paths:
            raise RuntimeAdapterError(
                'a read-only permission scope must not declare write paths')
        if self.network_observed not in (True, False, None):
            raise RuntimeAdapterError(
                'network_observed must be True, False or None (unknown)')
        if self.network_observed is not None \
                and bool(self.network) != bool(self.network_observed):
            raise RuntimeAdapterError(
                'network_observed must agree with network: '
                f'network={self.network!r}, '
                f'network_observed={self.network_observed!r}')
        for command in self.commands:
            if not isinstance(command, str) or not command.strip():
                raise RuntimeAdapterError(
                    'command permissions must be non-empty strings; an empty '
                    'entry is not a wildcard')

    def as_dict(self):
        return {
            'read_paths': list(self.read_paths),
            'write_paths': list(self.write_paths),
            'commands': list(self.commands),
            'network': self.network,
            'network_observed': self.network_observed,
            'read_only': self.read_only,
        }


@dataclass(frozen=True)
class RoleEvidence:
    """One piece of role-relevant evidence, with its source and freshness.

    S2 consumes only facts injected by the caller (fixtures, local records or
    an already-verified runtime probe). No external benchmark is read here; S3
    owns that adapter.
    """

    role: str
    dimension: str
    grade: str
    origin: str
    evidence_ref: str
    observed_at: str | None = None
    sample_size: int = 0
    source_class: str = 'OBSERVED'
    note: str = ''

    def __post_init__(self):
        if self.role not in ROLE_DIMENSIONS:
            raise RuntimeAdapterError(f'unknown role {self.role!r}')
        if self.dimension not in ROLE_DIMENSIONS[self.role]:
            raise RuntimeAdapterError(
                f'{self.role}: unknown dimension {self.dimension!r}; expected '
                f'one of {ROLE_DIMENSIONS[self.role]}')
        if self.grade not in ROLE_GRADES:
            raise RuntimeAdapterError(
                f'unknown evidence grade {self.grade!r}; expected one of '
                f'{ROLE_GRADES}')
        if self.origin not in ('project', 'external-reference'):
            raise RuntimeAdapterError(
                f'unknown evidence origin {self.origin!r}')
        if self.source_class not in EVIDENCE_CLASSES:
            raise RuntimeAdapterError(
                f'unknown source class {self.source_class!r}')
        if self.grade != 'UNKNOWN':
            if self.source_class != 'OBSERVED':
                raise RuntimeAdapterError(
                    f'{self.dimension}: a graded result requires OBSERVED '
                    f'evidence, got {self.source_class}')
            if not self.evidence_ref or not self.observed_at:
                raise RuntimeAdapterError(
                    f'{self.dimension}: a graded result needs evidence_ref and '
                    'observed_at')
            if self.sample_size <= 0:
                raise RuntimeAdapterError(
                    f'{self.dimension}: a graded result needs a positive '
                    'sample_size')
        if self.observed_at is not None:
            parse_timestamp(self.observed_at)

    def as_dict(self):
        return {
            'role': self.role, 'dimension': self.dimension,
            'grade': self.grade, 'origin': self.origin,
            'evidence_ref': self.evidence_ref,
            'observed_at': self.observed_at,
            'sample_size': self.sample_size,
            'source_class': self.source_class, 'note': self.note,
        }


@dataclass(frozen=True)
class CanonicalIdentity:
    """The operator-declared underlying model identity behind one model id (#170).

    ``canonical_id`` names the underlying model; ``aliases`` are the other model
    ids that were confirmed to resolve to it. A declaration is *trusted* only
    when it is ``CONFIRMED``, carries an ``evidence_ref`` and an unexpired
    ``verified_until``: there is no default validity window, and no date
    suffix, ``latest`` marker, prerelease tag or name similarity is ever used to
    infer that two model ids are the same or different models.

    ``status`` is decided at construction for format reasons. Expiry is decided
    against the decision time by :meth:`trusted_at`, so a declaration that
    expires between two runs cannot keep a stale ``CONFIRMED`` verdict.

    In production only :func:`scripts.agent_selection.discovery
    .registry_candidates_from_records` creates one, from the current scope's
    ``agent-registry.md`` record. A runtime's own agent enumeration has no field
    for it, so a runtime-discovered candidate stays ``None`` (``ABSENT``) until
    an operator registers a verified mapping for it.
    """

    canonical_id: str | None = None
    status: str = 'ABSENT'
    detail: str = ''
    aliases: tuple[str, ...] = ()
    evidence_ref: str | None = None
    verified_until: str | None = None
    # Earliest ``verified_until`` shared by every declaration the same
    # runtime_id + agent_id + model_id carries. The mapping is only as fresh as
    # the shortest window on it, so a skipped record's deadline still governs at
    # the decision time (#170).
    agreement_until: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'aliases', tuple(self.aliases or ()))
        if self.status not in CANONICAL_IDENTITY_STATUSES:
            raise RuntimeAdapterError(
                f'unknown canonical identity status {self.status!r}; expected '
                f'one of {CANONICAL_IDENTITY_STATUSES}')
        if self.canonical_id is not None:
            if not isinstance(self.canonical_id, str) or not \
                    self.canonical_id.strip():
                raise RuntimeAdapterError(
                    'canonical_id must be None or a non-empty string')
            object.__setattr__(self, 'canonical_id', self.canonical_id.strip())
        for alias in self.aliases:
            if not isinstance(alias, str) or not alias.strip():
                raise RuntimeAdapterError(
                    'canonical identity aliases must be non-empty strings')
        if self.status == 'CONFIRMED':
            if self.canonical_id is None:
                raise RuntimeAdapterError(
                    'a CONFIRMED canonical identity needs a canonical_id')
            if not self.evidence_ref or not str(self.evidence_ref).strip():
                raise RuntimeAdapterError(
                    'a CONFIRMED canonical identity needs an evidence '
                    'reference; an unverifiable mapping cannot be trusted')
            if self.verified_until is None:
                raise RuntimeAdapterError(
                    'a CONFIRMED canonical identity needs verified_until; no '
                    'default validity window is invented')
        if self.verified_until is not None:
            parse_timestamp(self.verified_until)
        if self.agreement_until is not None:
            parse_timestamp(self.agreement_until)

    @property
    def known(self):
        return self.canonical_id is not None

    @property
    def key(self):
        """Comparison key: case-insensitive, otherwise literal."""
        return self.canonical_id.casefold() if self.canonical_id else None

    def alias_keys(self, model_id=None):
        """Every model id this declaration claims, as comparison keys.

        ``model_id`` is the id the candidate itself reports; a record about that
        agent implicitly claims it, so a declaration need not repeat it.
        """
        keys = set()
        if self.canonical_id:
            keys.add(self.canonical_id.casefold())
        for alias in self.aliases:
            keys.add(alias.strip().casefold())
        if model_id is not None and str(model_id).strip():
            keys.add(str(model_id).strip().casefold())
        return keys

    def trusted_at(self, now=None):
        """Return ``(trusted, status, detail)`` with expiry applied at *now*."""
        if self.status != 'CONFIRMED':
            return False, self.status, (
                self.detail or f'canonical identity status is {self.status}')
        if self.verified_until is None:  # cannot happen; fail closed anyway
            return False, 'INVALID', ('a CONFIRMED canonical identity carries '
                                      'no verified_until')
        if _now(now) > parse_timestamp(self.verified_until):
            return False, 'EXPIRED', (
                f'canonical identity {self.canonical_id!r} was verified only '
                f'until {self.verified_until}, which has passed; re-verify it '
                'in the current scope agent registry')
        if self.agreement_until is not None and \
                _now(now) > parse_timestamp(self.agreement_until):
            return False, 'CONFLICT', (
                'another declaration for the same runtime_id, agent_id and '
                f'model_id was verified only until {self.agreement_until}, '
                'which has passed; the exact mapping is no longer consistent')
        return True, 'CONFIRMED', (
            f'canonical identity {self.canonical_id!r} confirmed against '
            f'{self.evidence_ref} until {self.verified_until}')

    def as_dict(self):
        return {
            'canonical_id': self.canonical_id,
            'status': self.status,
            'detail': self.detail,
            'aliases': list(self.aliases),
            'evidence_ref': self.evidence_ref,
            'verified_until': self.verified_until,
            'agreement_until': self.agreement_until,
        }


@dataclass(frozen=True)
class AgentCandidate:
    """One callable candidate, with the facts that justify any selection.

    Every field is either reported by a runtime's formal discovery interface or
    read from the explicit registry. Absent values stay ``None`` — they are
    never filled with a plausible default.
    """

    runtime_id: str
    runtime_version: str
    agent_id: str
    model_id: str | None
    source: str
    evidence_time: str
    model_family: str | None = None
    modalities: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    permissions: PermissionScope = field(default_factory=PermissionScope)
    capability_record: CapabilityRecord | None = None
    source_class: str = 'OBSERVED'
    context_tokens: int | None = None
    cost_per_mtok: float | None = None
    latency_ms: int | None = None
    role_evidence: tuple[RoleEvidence, ...] = ()
    notes: tuple[str, ...] = ()
    canonical_identity: CanonicalIdentity | None = None

    def __post_init__(self):
        for name in ('runtime_id', 'runtime_version', 'agent_id'):
            if not isinstance(getattr(self, name), str) or not getattr(
                    self, name).strip():
                raise RuntimeAdapterError(f'candidate needs a {name}')
        if self.source not in CANDIDATE_SOURCES:
            raise RuntimeAdapterError(
                f'unknown candidate source {self.source!r}; expected one of '
                f'{CANDIDATE_SOURCES}')
        if self.source_class not in EVIDENCE_CLASSES:
            raise RuntimeAdapterError(
                f'unknown source class {self.source_class!r}')
        parse_timestamp(self.evidence_time)
        if self.model_id is not None:
            if not isinstance(self.model_id, str) or not self.model_id.strip():
                raise RuntimeAdapterError(
                    'model_id must be None or a non-empty string')
        for name in ('modalities', 'tools'):
            values = getattr(self, name)
            if any(not isinstance(value, str) or not value.strip()
                   for value in values):
                raise RuntimeAdapterError(
                    f'{name} entries must be non-empty strings')
        if self.canonical_identity is not None and not isinstance(
                self.canonical_identity, CanonicalIdentity):
            raise RuntimeAdapterError(
                'canonical_identity must be None or a CanonicalIdentity; a '
                'model-id string is not an identity declaration')
        if self.capability_record is not None:
            if self.capability_record.runtime_id != self.runtime_id:
                raise RuntimeAdapterError(
                    f'{self.candidate_id}: capability record belongs to '
                    f'{self.capability_record.runtime_id!r}, not '
                    f'{self.runtime_id!r}')
            if self.capability_record.runtime_version != self.runtime_version:
                raise RuntimeAdapterError(
                    f'{self.candidate_id}: capability record version '
                    f'{self.capability_record.runtime_version!r} does not '
                    f'match {self.runtime_version!r}')

    @property
    def candidate_id(self):
        return f'{self.runtime_id}:{self.agent_id}'

    @property
    def identity_known(self):
        return self.model_id is not None

    def has_modality(self, modality):
        return str(modality).casefold() in {m.casefold() for m in self.modalities}

    def has_tool(self, tool):
        return str(tool).casefold() in {t.casefold() for t in self.tools}

    def evidence_for(self, role):
        return tuple(item for item in self.role_evidence if item.role == role)

    def as_dict(self):
        return {
            'candidate_id': self.candidate_id,
            'runtime_id': self.runtime_id,
            'runtime_version': self.runtime_version,
            'agent_id': self.agent_id,
            'model_id': self.model_id,
            'model_family': self.model_family,
            'modalities': list(self.modalities),
            'tools': list(self.tools),
            'permissions': self.permissions.as_dict(),
            'capability_record': (
                self.capability_record.as_dict()
                if self.capability_record is not None else None),
            'evidence_time': self.evidence_time,
            'source': self.source,
            'source_class': self.source_class,
            'context_tokens': self.context_tokens,
            'cost_per_mtok': self.cost_per_mtok,
            'latency_ms': self.latency_ms,
            'role_evidence': [item.as_dict() for item in self.role_evidence],
            'notes': list(self.notes),
            'canonical_identity': (self.canonical_identity.as_dict()
                                   if self.canonical_identity is not None
                                   else None),
        }


@dataclass(frozen=True)
class RuntimeDescription:
    """What the runtime says about itself, without guessing its interfaces."""

    runtime_id: str
    runtime_version: str
    edition: str
    execution_mode: str
    adapter_id: str
    can_enumerate_agents: bool | None
    notes: tuple[str, ...] = ()

    def __post_init__(self):
        if self.edition not in ('local', 'github'):
            raise RuntimeAdapterError(
                f'edition must be local or github, got {self.edition!r}')
        if self.can_enumerate_agents not in (True, False, None):
            raise RuntimeAdapterError(
                'can_enumerate_agents must be True, False or None (unknown)')

    def as_dict(self):
        return {
            'runtime_id': self.runtime_id,
            'runtime_version': self.runtime_version,
            'edition': self.edition,
            'execution_mode': self.execution_mode,
            'adapter_id': self.adapter_id,
            'can_enumerate_agents': self.can_enumerate_agents,
            'notes': list(self.notes),
        }


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of one discovery attempt (runtime or explicit registry)."""

    outcome: str
    mode: str
    candidates: tuple[AgentCandidate, ...] = ()
    notes: tuple[str, ...] = ()
    error: str | None = None

    def __post_init__(self):
        if self.outcome not in DISCOVERY_OUTCOMES:
            raise RuntimeAdapterError(
                f'unknown discovery outcome {self.outcome!r}; expected one of '
                f'{DISCOVERY_OUTCOMES}')
        if self.mode not in DISCOVERY_MODES:
            raise RuntimeAdapterError(
                f'unknown discovery mode {self.mode!r}; expected one of '
                f'{DISCOVERY_MODES}')
        if self.outcome == 'OK' and not self.candidates:
            raise RuntimeAdapterError(
                'an OK discovery must return at least one candidate; use '
                'EMPTY when nothing was found')

    @property
    def discovered(self):
        return self.outcome == 'OK' and bool(self.candidates)

    def as_dict(self):
        return {
            'outcome': self.outcome,
            'mode': self.mode,
            'candidates': [candidate.as_dict() for candidate in self.candidates],
            'notes': list(self.notes),
            'error': self.error,
        }


class RuntimeAdapter(abc.ABC):
    """Contract every runtime adapter implements.

    An adapter may only report what the runtime exposed. When the runtime has no
    formal agent enumeration, :meth:`discover` must return
    ``UNSUPPORTED``; the caller then reads the explicit registry. Nothing in
    this class inspects prompts, directories or configuration names.
    """

    @abc.abstractmethod
    def describe(self) -> RuntimeDescription:
        """Return the runtime identity and what the adapter is allowed to read."""

    @abc.abstractmethod
    def discover(self) -> DiscoveryResult:
        """Return the agents the runtime itself enumerated."""


class HostOpaqueRuntimeAdapter(RuntimeAdapter):
    """A visible runtime whose host agent inventory is not introspectable.

    This is the honest description of a process-local Python CLI: it can see
    its own runtime identity, but it cannot read the host Agent's tool list or
    subagent registry. It therefore never returns candidates. Use the explicit
    registry for those agents.
    """

    def __init__(self, *, runtime_id, runtime_version, edition,
                 execution_mode='manual-handoff', adapter_id='host-opaque'):
        self._description = RuntimeDescription(
            runtime_id=runtime_id,
            runtime_version=runtime_version,
            edition=edition,
            execution_mode=execution_mode,
            adapter_id=adapter_id,
            can_enumerate_agents=False,
            notes=('the host Agent inventory and tool list are not exposed to a '
                   'process-local runtime; enumerate them only by reported '
                   'facts or the explicit registry',))

    def describe(self):
        return self._description

    def discover(self):
        return DiscoveryResult(
            outcome='UNSUPPORTED',
            mode='runtime',
            notes=self._description.notes,
            error='this runtime does not expose a formal agent enumeration '
                  'interface')
