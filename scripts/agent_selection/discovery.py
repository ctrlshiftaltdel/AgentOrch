#!/usr/bin/env python3
"""Agent discovery with an explicit-registry fallback (#121, S2).

Design contract: ``docs/design/agent-role-discovery-and-model-selection.md``
§5.1. The order is fixed and has no third option:

1. the runtime's own formal agent enumeration, when it exposes one;
2. otherwise the explicit local registry (``agent-registry.md`` in the current
   scope, or an explicitly constructed :class:`ExplicitRegistry`);
3. otherwise an explicit ``NO_ADAPTER`` / ``EMPTY`` result.

There is no default agent, no catalog bundled into the code and no inference
from prompt names, file names or configuration keys. Discovery is also not
recommendation: the candidates returned here are handed to
``scripts.agent_selection.roles`` unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .local_store import StoreError
from .runtime_adapter import (
    AgentCandidate,
    CanonicalIdentity,
    CapabilityRecord,
    CANDIDATE_SOURCES,
    DiscoveryResult,
    EVIDENCE_CLASSES,
    PermissionScope,
    RuntimeAdapter,
    RuntimeAdapterError,
    parse_timestamp,
)

__all__ = [
    'REGISTRY_DOCUMENT',
    'DISCOVERY_RUNTIME',
    'DISCOVERY_REGISTRY',
    'DISCOVERY_NO_ADAPTER',
    'DISCOVERY_EMPTY',
    'DISCOVERY_ERROR',
    'REGISTRY_DETAIL_KEYS',
    'IDENTITY_DETAIL_KEYS',
    'IDENTITY_VERIFICATION_VALUES',
    'RegistryError',
    'ExplicitRegistry',
    'DiscoveryReport',
    'discover_candidates',
    'attach_registered_identities',
    'identity_match_key',
    'identity_from_details',
    'registry_candidates_from_records',
]

REGISTRY_DOCUMENT = 'agent-registry.md'

DISCOVERY_RUNTIME = 'RUNTIME'
DISCOVERY_REGISTRY = 'REGISTRY'
DISCOVERY_NO_ADAPTER = 'NO_ADAPTER'
DISCOVERY_EMPTY = 'EMPTY'
DISCOVERY_ERROR = 'ERROR'
REPORT_OUTCOMES = (DISCOVERY_RUNTIME, DISCOVERY_REGISTRY, DISCOVERY_NO_ADAPTER,
                   DISCOVERY_EMPTY, DISCOVERY_ERROR)

# The exact detail keys one registry record may carry. Every key is a plain
# scalar so the Markdown store stays human-readable; nothing is parsed out of
# prose.
REGISTRY_DETAIL_KEYS = (
    'runtime_id', 'runtime_version', 'agent_id', 'model_id', 'model_family',
    'modalities', 'tools', 'read_paths', 'write_paths', 'commands', 'network',
    'network_observed', 'read_only', 'context_tokens', 'cost_per_mtok',
    'latency_ms', 'capability_ref', 'evidence_time', 'source_class',
    'canonical_model_id', 'model_aliases', 'identity_verification',
    'identity_verified_until', 'identity_evidence',
)
REQUIRED_REGISTRY_KEYS = ('runtime_id', 'runtime_version', 'agent_id',
                          'model_id', 'evidence_time', 'source_class')

# (#170) The canonical model identity declaration an operator writes into the
# current scope's ``agent-registry.md``. It is the only place a mapping becomes
# trustworthy: the record names the underlying model, the aliases that resolve
# to it, who verified it, and until when that verification holds. Nothing here
# is inferred from a model name, a date suffix, ``latest``, a prerelease tag or
# a similarly spelled id.
IDENTITY_DETAIL_KEYS = ('canonical_model_id', 'model_aliases',
                        'identity_verification', 'identity_verified_until',
                        'identity_evidence')
IDENTITY_VERIFICATION_VALUES = ('CONFIRMED', 'UNCONFIRMED')


class RegistryError(ValueError):
    """One registry record is not usable as an agent definition."""


def _csv(value):
    return tuple(part.strip() for part in str(value).split(',')
                 if part.strip())


def _strict_bool(value, key):
    text = str(value).strip().lower()
    if text == 'true':
        return True
    if text == 'false':
        return False
    raise RegistryError(f'{key} must be true or false, got {value!r}')


def _optional_int(value, key):
    if value is None or str(value).strip() == '':
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise RegistryError(f'{key} must be an integer, got {value!r}') from exc
    if parsed < 0:
        raise RegistryError(f'{key} must not be negative')
    return parsed


def _optional_float(value, key):
    if value is None or str(value).strip() == '':
        return None
    try:
        parsed = float(str(value).strip())
    except ValueError as exc:
        raise RegistryError(f'{key} must be a number, got {value!r}') from exc
    if parsed < 0:
        raise RegistryError(f'{key} must not be negative')
    return parsed


def _identity_token(value, key):
    """One model id from an identity field; ``None`` when the field is blank."""
    text = str(value).strip()
    if not text:
        return None
    if ',' in text or any(char.isspace() for char in text):
        raise RegistryError(
            f'{key} must be a single model id without whitespace or commas, '
            f'got {value!r}')
    return text


def identity_from_details(details):
    """Parse the (#170) canonical identity declaration of one registry record.

    The result is always a :class:`CanonicalIdentity`; a malformed, unverified
    or incomplete declaration is reported with its own status instead of
    raising, so the candidate stays readable while its independence claim is
    refused. A record that carries none of the identity keys is ``ABSENT``: an
    older registry stays readable, but nothing is upgraded to trusted
    independence on its behalf.
    """
    raw = {key: str(details.get(key, '') or '').strip()
           for key in IDENTITY_DETAIL_KEYS}
    if not any(raw.values()):
        return CanonicalIdentity(
            status='ABSENT',
            detail=('no canonical model identity is registered for this '
                    'record; a different model id or family self-report is not '
                    'evidence of a different underlying model'))
    # A malformed field must not erase the parts of the declaration that did
    # parse. The canonical id (and its model id) is what the conflict scan reads
    # (#170 review), so a bad alias keeps the canonical id and only loses trust.
    problems = []
    try:
        canonical = _identity_token(raw['canonical_model_id'],
                                    'canonical_model_id')
    except RegistryError as exc:
        canonical = None
        problems.append(str(exc))
    aliases = []
    for part in _csv(raw['model_aliases']):
        try:
            aliases.append(_identity_token(part, 'model_aliases'))
        except RegistryError as exc:
            problems.append(str(exc))
    aliases = tuple(aliases)
    if canonical is None and problems:
        return CanonicalIdentity(
            status='INVALID', aliases=aliases,
            detail=f'{"; ".join(problems)}; the mapping is not trusted')
    verification = raw['identity_verification'].upper()
    until = raw['identity_verified_until'] or None
    evidence = raw['identity_evidence'] or None
    if canonical is None:
        return CanonicalIdentity(
            status='INVALID', aliases=aliases, evidence_ref=evidence,
            detail=('canonical_model_id is required when other identity fields '
                    'are present; the mapping is not trusted'))
    if problems:
        return CanonicalIdentity(
            canonical_id=canonical, status='INVALID', aliases=aliases,
            evidence_ref=evidence,
            detail=f'{"; ".join(problems)}; the mapping is not trusted')
    if verification not in IDENTITY_VERIFICATION_VALUES:
        return CanonicalIdentity(
            canonical_id=canonical, status='UNCONFIRMED', aliases=aliases,
            evidence_ref=evidence,
            detail=(f'identity_verification is '
                    f'{verification or "absent"}; only an operator-confirmed '
                    f'mapping ({", ".join(IDENTITY_VERIFICATION_VALUES)}) is '
                    'trusted'))
    if verification == 'UNCONFIRMED':
        return CanonicalIdentity(
            canonical_id=canonical, status='UNCONFIRMED', aliases=aliases,
            evidence_ref=evidence,
            detail=('the operator marked this mapping UNCONFIRMED; it is '
                    'reported and not trusted'))
    problems = []
    if until is None:
        problems.append('identity_verified_until is missing; no default '
                        'validity window is invented')
    else:
        try:
            parse_timestamp(until)
        except RuntimeAdapterError:
            problems.append(f'identity_verified_until {until!r} is not a UTC '
                            'timestamp like 2026-10-30T00:00:00Z')
    if evidence is None:
        problems.append('identity_evidence is missing; a verified mapping must '
                        'name the evidence an operator checked')
    if problems:
        return CanonicalIdentity(
            canonical_id=canonical, status='INVALID', aliases=aliases,
            evidence_ref=evidence,
            detail='; '.join(problems) + '; the mapping is not trusted')
    return CanonicalIdentity(
        canonical_id=canonical, status='CONFIRMED', aliases=aliases,
        evidence_ref=evidence, verified_until=until,
        detail=f'confirmed against {evidence} until {until}')


def _identity_claim(record):
    """The identity declaration a record makes, before any other validation.

    A record that carries any identity field makes a claim even when the
    declaration cannot be resolved (an unparsable ``canonical_model_id``, an
    invalid alias): the registry still said something about this agent, and that
    is exactly what makes a second declaration for the same agent unusable
    (#170 review). ``None`` means the record declares no identity at all.
    """
    details = dict(record.details)
    if not any(str(details.get(key, '') or '').strip()
               for key in IDENTITY_DETAIL_KEYS):
        return None
    identity = identity_from_details(details)
    model_id = str(details.get('model_id', '') or '').strip() or None
    runtime_id = str(details.get('runtime_id', '') or '').strip() or None
    agent_id = str(details.get('agent_id', '') or '').strip() or None
    exact = ((runtime_id, agent_id, model_id)
             if runtime_id and agent_id and model_id else None)
    return identity, model_id, exact


def disputed_identity_keys(records, *, now=None):
    """What this scope's registry contradicts, read from the declarations alone.

    Returns ``(alias_keys, exact_triples)``:

    * ``alias_keys`` - model ids / aliases declared against more than one
      canonical id;
    * ``exact_triples`` - ``runtime_id`` + ``agent_id`` + ``model_id`` triples
      that more than one record claims without agreeing on one *confirmed*
      declaration. A twin whose canonical id happens to match still counts when
      its own declaration is unusable (a malformed alias, a missing or invalid
      field), was never confirmed, or has **expired**: an expired verification
      is not a confirmed declaration, so ``verified_until`` is judged at
      ``now`` like any other freshness fact. Matching the string is not enough.

    Both are computed on **every** non-``SUPERSEDED`` record before the rest of
    the record is validated, so a record rejected for an unrelated field (say an
    unparsable ``read_only``) still contradicts the other one, and skipping it
    must not leave the survivor ``CONFIRMED`` (#170 review).
    """
    claims = {}
    exact_claims = {}
    for record in records:
        if record.status == 'SUPERSEDED':
            continue
        claim = _identity_claim(record)
        if claim is None:
            continue
        identity, model_id, exact = claim
        if identity.canonical_id is not None:
            for key in identity.alias_keys(model_id):
                claims.setdefault(key, set()).add(
                    identity.canonical_id.casefold())
        if exact is not None:
            # Expiry is part of "confirmed": trusted_at() reports EXPIRED once
            # the verification window has passed, so an expired twin cannot
            # stand in as agreement for the same exact triple.
            _, status, _ = identity.trusted_at(now)
            exact_claims.setdefault(exact, set()).add(
                (status,
                 identity.canonical_id.casefold()
                 if identity.canonical_id is not None else None))
    disputed_exact = set()
    for exact, declarations in exact_claims.items():
        if len(declarations) < 2:
            # One record alone is governed by its own status, not by a twin.
            continue
        canonicals = {canonical for _, canonical in declarations}
        if len(canonicals) > 1 or any(status != 'CONFIRMED'
                                      for status, _ in declarations):
            disputed_exact.add(exact)
    return ({key for key, targets in claims.items() if len(targets) > 1},
            disputed_exact)


def _conflict_identity(identity, disputed, *, model_id=None, exact_key=None,
                       disputed_exact=()):
    """Downgrade one declaration that contradicts another record, or return it."""
    reasons = []
    hit = sorted(identity.alias_keys(model_id) & disputed)
    if hit:
        reasons.append(
            f'the canonical model identity mapping for {", ".join(hit)} is '
            'declared against more than one canonical id in this scope')
    if exact_key is not None and exact_key in disputed_exact:
        reasons.append(
            'another record matches this agent on runtime_id, agent_id and '
            'model_id but is not the same confirmed identity declaration '
            '(different, unresolvable, unconfirmed or invalid)')
    if not reasons:
        return identity
    return replace(identity, status='CONFLICT',
                   detail='; '.join(reasons) + '; a conflicting mapping is not '
                                               'trusted')


def identity_match_key(candidate):
    """The exact key that ties an enumerated agent to a registry record (#170).

    ``runtime_id`` + ``agent_id`` + ``model_id``: three operator-writable facts,
    compared literally. No prefix, family, alias or name-similarity match is
    made here — an alias only counts once the registry *record* declares it.
    """
    model_id = candidate.model_id
    if model_id is None or not str(model_id).strip():
        return None
    return (candidate.runtime_id, candidate.agent_id, str(model_id).strip())


def _declaration_signature(identity):
    return (identity.status, identity.key, identity.verified_until,
            identity.evidence_ref, tuple(sorted(identity.aliases)))


def _conflicting_identity():
    return CanonicalIdentity(
        status='CONFLICT',
        detail=('two agent-registry.md records match this agent on '
                'runtime_id, agent_id and model_id with different identity '
                'declarations; a conflicting mapping is not trusted'))


def attach_registered_identities(candidates, registry):
    """Replace any runtime-supplied identity with the scope's declaration.

    A runtime can enumerate an agent but cannot vouch for the underlying model,
    so whatever ``canonical_identity`` it put on a candidate is discarded at
    this boundary before anything else happens (#170 review). What remains is
    owned by the current scope's ``agent-registry.md``: a candidate only gets an
    identity from the record that matches ``runtime_id`` + ``agent_id`` +
    ``model_id`` exactly. No registry, no exact match, or two exact matches that
    disagree all leave the candidate without a trusted identity, so Reviewer
    independence stays UNKNOWN instead of PASS. Every other reported fact stays
    exactly as the runtime reported it.
    """
    entries = tuple(getattr(registry, 'entries', ()) or ()) \
        if registry is not None else ()
    declarations = {}
    for entry in entries:
        identity = entry.canonical_identity
        key = identity_match_key(entry)
        if identity is None or key is None:
            continue
        declarations.setdefault(key, []).append(identity)
    attached = []
    notes = []
    discarded = []
    for original in candidates:
        candidate = original
        if candidate.canonical_identity is not None:
            discarded.append(candidate.agent_id)
            candidate = replace(
                candidate, canonical_identity=None,
                notes=tuple(candidate.notes) + (
                    'the runtime-supplied canonical model identity was '
                    'discarded at the discovery boundary; a runtime cannot '
                    'vouch for the underlying model',))
        key = identity_match_key(candidate)
        matching = declarations.get(key, ()) if key is not None else ()
        distinct = {_declaration_signature(item) for item in matching}
        if not distinct:
            attached.append(candidate)
            continue
        if len(distinct) == 1:
            identity = matching[0]
            attached.append(replace(
                candidate, canonical_identity=identity,
                notes=tuple(candidate.notes) + (
                    'canonical model identity taken from the current scope '
                    'agent-registry.md record that matches runtime_id, '
                    'agent_id and model_id exactly',)))
            continue
        attached.append(replace(
            candidate, canonical_identity=_conflicting_identity(),
            notes=tuple(candidate.notes) + (
                'agent-registry.md has more than one exact match with '
                'different identity declarations',)))
    if discarded:
        notes.append(
            f'runtime-supplied canonical identity discarded for '
            f'{", ".join(sorted(discarded))}: only the current scope '
            'agent-registry.md can declare a trusted identity')
    notes.append(
        f'{len(attached)} runtime candidate(s) were checked against the '
        'current scope agent-registry.md; an identity is used only on an exact '
        'runtime_id + agent_id + model_id match')
    return tuple(attached), tuple(notes)


@dataclass(frozen=True)
class ExplicitRegistry:
    """An explicitly configured agent registry.

    ``entries`` are already-validated candidates. ``issues`` records entries
    that could not be used, so a partial registry is reported rather than
    silently treated as complete.
    """

    entries: tuple[AgentCandidate, ...] = ()
    issues: tuple[str, ...] = ()

    def __post_init__(self):
        for entry in self.entries:
            if entry.source != CANDIDATE_SOURCES[1]:
                raise RegistryError(
                    f'{entry.candidate_id}: registry entries must declare '
                    f'source={CANDIDATE_SOURCES[1]!r}')

    def discover(self) -> DiscoveryResult:
        if not self.entries:
            return DiscoveryResult(
                outcome='EMPTY', mode='explicit-registry',
                notes=tuple(self.issues) + (
                    'the explicit agent registry has no usable entry',))
        notes = tuple(self.issues) + (
            f'{len(self.entries)} entry / entries read from the explicit '
            'registry',)
        return DiscoveryResult(outcome='OK', mode='explicit-registry',
                               candidates=tuple(self.entries), notes=notes)

    @classmethod
    def from_store(cls, store, *, capability_records=None, now=None):
        """Read ``agent-registry.md`` from one initialized local scope."""
        try:
            records = store.read_records(REGISTRY_DOCUMENT)
        except StoreError as exc:
            return cls(entries=(), issues=(f'{REGISTRY_DOCUMENT}: {exc}',))
        entries, issues = registry_candidates_from_records(
            records, capability_records=capability_records, now=now)
        return cls(entries=entries, issues=issues)


def registry_candidates_from_records(records, *, capability_records=None,
                                     now=None):
    """Convert Markdown registry records into candidates (or issues).

    A record that is ``SUPERSEDED`` is skipped: an old definition must not be
    resurrected. Every other unusable record yields an issue and no candidate,
    so the selector can report the gap instead of guessing.

    ``now`` dates the identity-conflict scan only: a verification window that has
    already closed at ``now`` (default: the current time) is not a confirmed
    declaration. Role matching still re-evaluates expiry at its own decision
    time.
    """
    capabilities = dict(capability_records or {})
    records = tuple(records)
    # Identity contradictions are decided from the declarations themselves, so a
    # record rejected for an unrelated field cannot hide one side of a conflict.
    disputed, disputed_exact = disputed_identity_keys(records, now=now)
    entries = []
    issues = []
    for record in records:
        label = f'{REGISTRY_DOCUMENT} record {record.record_id!r}'
        if record.status == 'SUPERSEDED':
            continue
        details = dict(record.details)
        missing = [key for key in REQUIRED_REGISTRY_KEYS
                   if not str(details.get(key, '')).strip()]
        if missing:
            issues.append(f'{label}: missing {", ".join(missing)}; skipped')
            continue
        unknown_keys = sorted(set(details) - set(REGISTRY_DETAIL_KEYS))
        if unknown_keys:
            issues.append(
                f'{label}: unsupported detail key(s) {", ".join(unknown_keys)}; '
                'skipped (unknown fields are not guessed)')
            continue
        try:
            source_class = str(details['source_class']).strip().upper()
            if source_class not in EVIDENCE_CLASSES:
                raise RegistryError(
                    f'source_class must be one of {EVIDENCE_CLASSES}, got '
                    f'{source_class!r}')
            evidence_time = str(details['evidence_time']).strip()
            parse_timestamp(evidence_time)
            capability_ref = str(details.get('capability_ref', '')).strip()
            capability_record = capabilities.get(capability_ref)
            if capability_ref and capability_record is None:
                issues.append(
                    f'{label}: capability record {capability_ref!r} was not '
                    'found; the candidate is kept without capability evidence '
                    'and cannot be auto-selected')
            if capability_record is not None and not isinstance(
                    capability_record, CapabilityRecord):
                raise RegistryError('capability_records values must be '
                                    'CapabilityRecord instances')
            entry = AgentCandidate(
                runtime_id=str(details['runtime_id']).strip(),
                runtime_version=str(details['runtime_version']).strip(),
                agent_id=str(details['agent_id']).strip(),
                model_id=str(details['model_id']).strip(),
                model_family=(str(details['model_family']).strip()
                              if str(details.get('model_family', '')).strip()
                              else None),
                modalities=_csv(details.get('modalities', '')),
                tools=_csv(details.get('tools', '')),
                permissions=PermissionScope(
                    read_paths=_csv(details.get('read_paths', '')),
                    write_paths=_csv(details.get('write_paths', '')),
                    commands=_csv(details.get('commands', '')),
                    network=_strict_bool(details.get('network', 'false'),
                                         'network'),
                    network_observed=(
                        _strict_bool(details['network_observed'],
                                     'network_observed')
                        if str(details.get('network_observed', '')).strip()
                        else None),
                    read_only=_strict_bool(details.get('read_only', 'false'),
                                           'read_only'),
                ),
                capability_record=capability_record,
                source=CANDIDATE_SOURCES[1],
                source_class=source_class,
                evidence_time=evidence_time,
                context_tokens=_optional_int(details.get('context_tokens'),
                                             'context_tokens'),
                cost_per_mtok=_optional_float(details.get('cost_per_mtok'),
                                              'cost_per_mtok'),
                latency_ms=_optional_int(details.get('latency_ms'),
                                         'latency_ms'),
                notes=(f'registry record {record.record_id}',),
                canonical_identity=_conflict_identity(
                    identity_from_details(details), disputed,
                    model_id=str(details['model_id']).strip(),
                    exact_key=(str(details['runtime_id']).strip(),
                               str(details['agent_id']).strip(),
                               str(details['model_id']).strip()),
                    disputed_exact=disputed_exact),
            )
        except (RegistryError, RuntimeAdapterError) as exc:
            issues.append(f'{label}: {exc}; skipped')
            continue
        if entry.canonical_identity.status == 'CONFLICT':
            issues.append(f'{label}: {entry.canonical_identity.detail}')
        entries.append(entry)
    return tuple(entries), tuple(issues)


@dataclass(frozen=True)
class DiscoveryReport:
    """The layered discovery outcome: runtime first, registry second."""

    outcome: str
    runtime_result: DiscoveryResult | None = None
    registry_result: DiscoveryResult | None = None
    candidates: tuple[AgentCandidate, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self):
        if self.outcome not in REPORT_OUTCOMES:
            raise ValueError(
                f'unknown discovery report outcome {self.outcome!r}; expected '
                f'one of {REPORT_OUTCOMES}')
        if self.outcome in (DISCOVERY_RUNTIME, DISCOVERY_REGISTRY) \
                and not self.candidates:
            raise ValueError(
                f'{self.outcome} requires at least one candidate')

    @property
    def discovered(self):
        return bool(self.candidates)

    @property
    def source(self):
        return (self.runtime_result.mode if self.outcome == DISCOVERY_RUNTIME
                else self.registry_result.mode
                if self.outcome == DISCOVERY_REGISTRY else None)

    def as_dict(self):
        return {
            'outcome': self.outcome,
            'source': self.source,
            'runtime_result': (self.runtime_result.as_dict()
                               if self.runtime_result is not None else None),
            'registry_result': (self.registry_result.as_dict()
                                if self.registry_result is not None else None),
            'candidates': [candidate.as_dict()
                           for candidate in self.candidates],
            'notes': list(self.notes),
        }


def _run_runtime(adapter):
    if adapter is None:
        return None, None
    try:
        if not isinstance(adapter, RuntimeAdapter):
            raise RuntimeAdapterError(
                'adapter does not implement the RuntimeAdapter contract')
        description = adapter.describe()
        result = adapter.discover()
        return description, result
    except Exception as exc:  # an adapter must not take discovery down with it
        return None, DiscoveryResult(
            outcome='ERROR', mode='runtime',
            notes=('the runtime adapter failed; no capability or agent is '
                   'inferred from the failure',),
            error=f'{type(exc).__name__}: {exc}')


def discover_candidates(adapter=None, registry=None) -> DiscoveryReport:
    """Discover candidates with the documented fallback order."""
    description, runtime_result = _run_runtime(adapter)
    notes = []
    if description is not None:
        notes.append(
            f'runtime {description.runtime_id} {description.runtime_version} '
            f'(adapter {description.adapter_id})')
        if description.can_enumerate_agents is False:
            notes.append('the runtime does not expose a formal agent '
                         'enumeration interface')
    if runtime_result is not None and runtime_result.notes:
        notes.extend(runtime_result.notes)
    if runtime_result is not None and runtime_result.error:
        notes.append(f'runtime discovery error: {runtime_result.error}')

    if runtime_result is not None and runtime_result.discovered:
        # The runtime wins the enumeration; the scope registry still supplies the
        # underlying-model identity for agents it matches exactly (#170).
        identities, identity_notes = attach_registered_identities(
            runtime_result.candidates, registry)
        notes.extend(identity_notes)
        return DiscoveryReport(
            outcome=DISCOVERY_RUNTIME,
            runtime_result=runtime_result,
            candidates=identities,
            notes=tuple(notes))

    registry_result = registry.discover() if registry is not None else None
    if registry_result is not None:
        notes.extend(registry_result.notes)

    if registry_result is not None and registry_result.discovered:
        notes.append('runtime discovery did not yield agents; the explicit '
                     'registry was used')
        return DiscoveryReport(
            outcome=DISCOVERY_REGISTRY,
            runtime_result=runtime_result,
            registry_result=registry_result,
            candidates=tuple(registry_result.candidates),
            notes=tuple(notes))

    if runtime_result is not None and runtime_result.outcome == 'EMPTY':
        outcome = DISCOVERY_EMPTY
        notes.append('the runtime enumerated no agents and the explicit '
                     'registry is empty; no agent is invented')
    elif runtime_result is None or runtime_result.outcome in (
            'UNSUPPORTED', 'NO_ADAPTER'):
        outcome = DISCOVERY_NO_ADAPTER
        notes.append('no usable runtime adapter and no explicit registry '
                     'entry; no agent is invented')
    elif runtime_result.outcome == 'ERROR':
        outcome = DISCOVERY_ERROR
        notes.append('runtime discovery failed and the explicit registry is '
                     'empty; no agent is invented')
    else:
        outcome = DISCOVERY_EMPTY
        notes.append('the runtime enumerated no agents and the explicit '
                     'registry is empty')
    return DiscoveryReport(outcome=outcome, runtime_result=runtime_result,
                           registry_result=registry_result,
                           candidates=(), notes=tuple(notes))
