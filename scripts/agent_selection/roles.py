#!/usr/bin/env python3
"""Role matching for Master / Execution / Reviewer candidates (#121, S2).

Design contract: ``docs/design/agent-role-discovery-and-model-selection.md``
§7. The order is hard filters, then an explainable ranking, then a report:

* hard filters decide *who is allowed to be considered*: callability (C1–C4),
  modalities, tools / context, the observed permission boundary, reviewer
  independence and budget / latency limits;
* ranking only orders the survivors by role-relevant evidence, project
  evidence and then cost / latency. There is deliberately no composite
  "intelligence score";
* the report names every candidate, why it passed or failed, what is missing,
  when the evidence was observed, why the winner won, which alternatives exist
  and how to degrade.

Two safety properties are structural rather than advisory:

* capability ``PASS`` never widens a task path: the report echoes the task's
  own allowed paths and the selector has no API to add one;
* Reviewer independence is enforced against the **selected Execution model**,
  not against whatever the caller remembered to put in the requirement;
* nothing here spawns, waits for or collects an agent. Selecting a role is not
  dispatching it, and the report says so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .discovery import (
    DISCOVERY_RUNTIME,
    DISCOVERY_REGISTRY,
    DiscoveryReport,
)
from .runtime_adapter import (
    AgentCandidate,
    CanonicalIdentity,
    DEFAULT_MAX_EVIDENCE_AGE_DAYS,
    ROLE_DIMENSIONS,
    covers_command,
    covers_path,
    evidence_age_days,
)

__all__ = [
    'ROLE_MASTER',
    'ROLE_EXECUTION',
    'ROLE_REVIEWER',
    'ROLES',
    'FILTER_STATUSES',
    'PROJECT_MIN_SAMPLES',
    'FilterResult',
    'RoleRequirement',
    'CandidateEvaluation',
    'RoleSelection',
    'SelectionReport',
    'master_requirement',
    'execution_requirement',
    'reviewer_requirement',
    'evaluate_candidate',
    'select_roles',
]

ROLE_MASTER = 'master'
ROLE_EXECUTION = 'execution'
ROLE_REVIEWER = 'reviewer'
ROLES = (ROLE_MASTER, ROLE_EXECUTION, ROLE_REVIEWER)

FILTER_STATUSES = ('PASS', 'FAIL', 'UNKNOWN', 'NOT_APPLICABLE')

# Project-internal evidence counts as a signal only with a sample. A single
# successful task is an anecdote; it is reported but does not outrank a
# better-sampled candidate.
PROJECT_MIN_SAMPLES = 3

_FILTER_ORDER = ('runtime', 'evidence', 'identity', 'callable', 'modality',
                 'tools', 'context', 'permissions', 'independence', 'budget')


@dataclass(frozen=True)
class FilterResult:
    """One hard-filter outcome, kept for the report even when it passed."""

    filter_id: str
    status: str
    detail: str

    def __post_init__(self):
        if self.filter_id not in _FILTER_ORDER:
            raise ValueError(f'unknown filter {self.filter_id!r}')
        if self.status not in FILTER_STATUSES:
            raise ValueError(f'unknown filter status {self.status!r}')

    def as_dict(self):
        return {'filter': self.filter_id, 'status': self.status,
                'detail': self.detail}


@dataclass(frozen=True)
class RoleRequirement:
    """What one task demands of one role.

    ``write_paths`` / ``read_paths`` are the paths the **task** already allows.
    They are compared against the candidate's observed boundary and are never
    expanded by the result.
    """

    role: str
    required_modalities: tuple[str, ...] = ('text',)
    required_tools: tuple[str, ...] = ()
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    required_commands: tuple[str, ...] = ()
    require_command_access: bool = False
    network_required: bool = False
    min_context_tokens: int | None = None
    max_cost_per_mtok: float | None = None
    max_latency_ms: int | None = None
    excluded_model_ids: tuple[str, ...] = ()
    reference_model_id: str | None = None
    reference_model_family: str | None = None
    reference_canonical_identity: CanonicalIdentity | None = None
    require_read_only: bool = False
    allow_manual_review: bool = False

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f'unknown role {self.role!r}; expected one of '
                             f'{ROLES}')
        for name in ('min_context_tokens', 'max_latency_ms'):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f'{name} must not be negative')
        if self.max_cost_per_mtok is not None and self.max_cost_per_mtok < 0:
            raise ValueError('max_cost_per_mtok must not be negative')

    def as_dict(self):
        return {
            'role': self.role,
            'required_modalities': list(self.required_modalities),
            'required_tools': list(self.required_tools),
            'task_read_paths': list(self.read_paths),
            'task_write_paths': list(self.write_paths),
            'required_commands': list(self.required_commands),
            'require_command_access': self.require_command_access,
            'network_required': self.network_required,
            'min_context_tokens': self.min_context_tokens,
            'max_cost_per_mtok': self.max_cost_per_mtok,
            'max_latency_ms': self.max_latency_ms,
            'excluded_model_ids': list(self.excluded_model_ids),
            'reference_model_id': self.reference_model_id,
            'reference_model_family': self.reference_model_family,
            'reference_canonical_identity': (
                self.reference_canonical_identity.as_dict()
                if self.reference_canonical_identity is not None else None),
            'require_read_only': self.require_read_only,
            'allow_manual_review': self.allow_manual_review,
        }


def master_requirement(**kwargs):
    """Master: real text + image input on the current runtime."""
    kwargs.setdefault('required_modalities', ('text', 'image'))
    kwargs.setdefault('required_tools', ())
    return RoleRequirement(role=ROLE_MASTER, **kwargs)


def execution_requirement(*, read_paths, write_paths, **kwargs):
    """Execution: coding, tools, goal following and delivery efficiency.

    ``read_paths`` / ``write_paths`` are mandatory keyword arguments: passing an
    empty tuple is an explicit statement that the task needs no file access, so
    a forgotten argument cannot silently skip the permission filter. Command
    access is required by default (``require_command_access=True``).
    """
    if read_paths is None or write_paths is None:
        raise ValueError('read_paths and write_paths must be passed explicitly '
                         '(use () only when the task needs no file access)')
    kwargs.setdefault('required_modalities', ('text',))
    kwargs.setdefault('required_tools', ('file-write', 'command'))
    kwargs.setdefault('require_command_access', True)
    return RoleRequirement(role=ROLE_EXECUTION, read_paths=tuple(read_paths),
                           write_paths=tuple(write_paths), **kwargs)


def reviewer_requirement(*, execution_model_id, read_paths,
                         execution_model_family=None,
                         execution_canonical_identity=None, **kwargs):
    """Reviewer: defects / tests / security, read-only, independent model.

    Independence is judged on the *verified canonical* model identity
    (:class:`~.runtime_adapter.CanonicalIdentity`, #170); the model id and family
    are carried along for the report, but a different self-reported id or family
    is not independence evidence on its own.
    """
    if read_paths is None:
        raise ValueError('read_paths must be passed explicitly '
                         '(use () only when the task needs no file access)')
    kwargs.setdefault('required_modalities', ('text',))
    kwargs.setdefault('required_tools', ('file-read',))
    kwargs.setdefault('require_read_only', True)
    excluded = tuple(kwargs.pop('excluded_model_ids', ()) or ())
    if execution_model_id is not None:
        excluded = excluded + (execution_model_id,)
    kwargs.setdefault('reference_model_id', execution_model_id)
    kwargs.setdefault('reference_model_family', execution_model_family)
    kwargs.setdefault('reference_canonical_identity',
                      execution_canonical_identity)
    return RoleRequirement(role=ROLE_REVIEWER, read_paths=tuple(read_paths),
                           excluded_model_ids=excluded, **kwargs)


@dataclass(frozen=True)
class CandidateEvaluation:
    """One candidate's complete hard-filter result for one role."""

    candidate: AgentCandidate
    role: str
    filters: tuple[FilterResult, ...]
    reasons: tuple[str, ...] = ()
    rank_key: tuple | None = None

    @property
    def status(self):
        statuses = [item.status for item in self.filters]
        if 'FAIL' in statuses:
            return 'FAIL'
        if 'UNKNOWN' in statuses:
            return 'UNKNOWN'
        return 'PASS'

    @property
    def passed(self):
        return self.status == 'PASS'

    @property
    def missing(self):
        return tuple(
            f'{item.filter_id}: {item.detail}' for item in self.filters
            if item.status in ('FAIL', 'UNKNOWN'))

    def as_dict(self):
        payload = {
            'candidate_id': self.candidate.candidate_id,
            'runtime_id': self.candidate.runtime_id,
            'runtime_version': self.candidate.runtime_version,
            'agent_id': self.candidate.agent_id,
            'model_id': self.candidate.model_id,
            'model_family': self.candidate.model_family,
            'evidence_time': self.candidate.evidence_time,
            'source': self.candidate.source,
            'status': self.status,
            'filters': [item.as_dict() for item in self.filters],
            'missing': list(self.missing),
            'reasons': list(self.reasons),
        }
        if self.rank_key is not None:
            payload['rank_key'] = list(self.rank_key)
        return payload


def _evidence_filter(candidate, now, max_age_days):
    if candidate.source_class != 'OBSERVED':
        status = 'UNKNOWN' if candidate.source_class == 'UNKNOWN' else 'FAIL'
        return FilterResult(
            'evidence', status,
            f'candidate facts are {candidate.source_class}; they cannot '
            'qualify an automatic selection')
    age = evidence_age_days(candidate.evidence_time, now)
    if age is None:
        return FilterResult('evidence', 'UNKNOWN',
                            'candidate evidence time cannot be parsed')
    if max_age_days is not None and age > max_age_days:
        return FilterResult(
            'evidence', 'FAIL',
            f'candidate facts are {age:.1f} days old (limit {max_age_days}); '
            're-discover before use')
    return FilterResult('evidence', 'PASS',
                        f'candidate facts observed {candidate.evidence_time}')


def _callable_filter(candidate, requirement, now, max_age_days):
    if requirement.role == ROLE_MASTER:
        return FilterResult(
            'callable', 'NOT_APPLICABLE',
            'Master is the running role; C1-C4 gate the dispatch path, not '
            'Master itself')
    record = candidate.capability_record
    if record is None:
        return FilterResult(
            'callable', 'UNKNOWN',
            'no capability record; C1-C4 are unverified for this runtime')
    gate = record.gate(now=now, max_age_days=max_age_days)
    if gate.passed:
        return FilterResult('callable', 'PASS', '; '.join(gate.reasons))
    status = 'UNKNOWN' if gate.status == 'UNKNOWN' else 'FAIL'
    detail = '; '.join(gate.reasons) or 'C1-C4 are not all PASS'
    if gate.stale:
        detail = f'STALE: {detail}'
    return FilterResult('callable', status, detail)


def _modality_filter(candidate, requirement):
    if not requirement.required_modalities:
        return FilterResult('modality', 'NOT_APPLICABLE', 'no modality required')
    missing = [modality for modality in requirement.required_modalities
               if not candidate.has_modality(modality)]
    if missing:
        return FilterResult(
            'modality', 'FAIL',
            f'missing required input modality: {", ".join(missing)}; observed '
            f'{", ".join(candidate.modalities) or "(none)"}')
    return FilterResult(
        'modality', 'PASS',
        f'observed {" + ".join(requirement.required_modalities)} input')


def _tools_filter(candidate, requirement):
    if not requirement.required_tools:
        return FilterResult('tools', 'NOT_APPLICABLE', 'no tool required')
    missing = [tool for tool in requirement.required_tools
               if not candidate.has_tool(tool)]
    if missing:
        return FilterResult(
            'tools', 'FAIL',
            f'missing required tool: {", ".join(missing)}; observed '
            f'{", ".join(candidate.tools) or "(none)"}')
    return FilterResult('tools', 'PASS',
                        f'observed {", ".join(requirement.required_tools)}')


def _context_filter(candidate, requirement):
    if requirement.min_context_tokens is None:
        return FilterResult('context', 'NOT_APPLICABLE',
                            'no context requirement')
    if candidate.context_tokens is None:
        return FilterResult('context', 'UNKNOWN',
                            'candidate context capacity was not reported')
    if candidate.context_tokens < requirement.min_context_tokens:
        return FilterResult(
            'context', 'FAIL',
            f'{candidate.context_tokens} tokens is below the required '
            f'{requirement.min_context_tokens}')
    return FilterResult('context', 'PASS',
                        f'{candidate.context_tokens} tokens')


def _permissions_filter(candidate, requirement):
    """Compare the observed boundary with what the task requires.

    Each dimension is reported as ``COVERED``, ``NOT_COVERED``, ``UNOBSERVED``
    or ``NOT_REQUIRED``. An unobserved dimension is never silently read as
    covered, and a PASS is only emitted when at least one dimension was
    required and every required dimension is covered.
    """
    scope = candidate.permissions
    covered = []
    failures = []
    unknowns = []

    # --- read -------------------------------------------------------------
    if requirement.read_paths:
        if not scope.read_paths:
            unknowns.append('read: UNOBSERVED (no read scope recorded)')
        else:
            missing = [path for path in requirement.read_paths
                       if not covers_path(scope.read_paths, path)]
            if missing:
                failures.append(
                    f'read: NOT_COVERED ({", ".join(missing)})')
            else:
                covered.append(
                    f'read: COVERED ({", ".join(requirement.read_paths)})')
    else:
        covered.append('read: NOT_REQUIRED')

    # --- write ------------------------------------------------------------
    if requirement.write_paths:
        if scope.read_only:
            failures.append('write: NOT_PERMITTED (candidate is read-only)')
        elif not scope.write_paths:
            unknowns.append('write: UNOBSERVED (no write scope recorded)')
        else:
            missing = [path for path in requirement.write_paths
                       if not covers_path(scope.write_paths, path)]
            if missing:
                failures.append(
                    f'write: NOT_COVERED ({", ".join(missing)})')
            else:
                covered.append(
                    f'write: COVERED ({", ".join(requirement.write_paths)})')
    else:
        covered.append('write: NOT_REQUIRED')

    # --- read-only constraint --------------------------------------------
    if requirement.require_read_only:
        if scope.read_only:
            covered.append('read-only: OBSERVED')
        else:
            failures.append('read-only: NOT_OBSERVED (reviewer must be read-only)')
    else:
        covered.append('read-only: NOT_REQUIRED')

    # --- commands ---------------------------------------------------------
    required_commands = tuple(requirement.required_commands or ())
    if required_commands:
        if not scope.commands:
            unknowns.append(
                'commands: UNOBSERVED (no command permission recorded)')
        else:
            missing = [command for command in required_commands
                       if not covers_command(scope.commands, command)]
            if missing:
                failures.append(
                    f'commands: NOT_COVERED ({", ".join(missing)})')
            else:
                covered.append(
                    f'commands: COVERED ({", ".join(required_commands)})')
    elif requirement.require_command_access:
        if not scope.commands:
            unknowns.append(
                'commands: UNOBSERVED (no command permission recorded)')
        else:
            covered.append(
                f'commands: COVERED ({", ".join(scope.commands)})')
    else:
        covered.append('commands: NOT_REQUIRED')

    # --- network ----------------------------------------------------------
    if requirement.network_required:
        if scope.network:
            covered.append('network: COVERED')
        elif scope.network_observed is False:
            failures.append('network: NOT_PERMITTED (observed as disabled)')
        else:
            unknowns.append('network: UNOBSERVED (access was not observed)')
    else:
        covered.append('network: NOT_REQUIRED')

    detail = '; '.join(failures + unknowns + covered)
    if failures:
        return FilterResult('permissions', 'FAIL', detail)
    if unknowns:
        return FilterResult('permissions', 'UNKNOWN', detail)
    if all(item.endswith('NOT_REQUIRED') for item in covered):
        return FilterResult(
            'permissions', 'NOT_APPLICABLE',
            detail + '; no permission requirement was declared, so nothing '
                     'was evaluated (this is not an observed grant)')
    return FilterResult(
        'permissions', 'PASS',
        detail + '; the selector does not widen allowed paths, commands or '
                 'network access')


def _identity_verdict(identity, *, model_id, now):
    """``(trusted, canonical_id, status, detail)`` for one declared identity.

    Trust is decided here, not by the caller: a missing declaration is
    ``ABSENT``, an unverified / conflicting / invalid one keeps its status, and
    a ``CONFIRMED`` one that has expired is ``EXPIRED``. None of them is turned
    into independence evidence.
    """
    if identity is None:
        return (False, None, 'ABSENT',
                f'no canonical model identity is registered for model id '
                f'{model_id!r}, so the same underlying model cannot be told '
                f'apart from a different one')
    trusted, status, detail = identity.trusted_at(now)
    if not trusted:
        return False, None, status, detail
    return True, identity.canonical_id, status, detail


def _independence_filter(candidate, requirement, *, now=None):
    if requirement.role != ROLE_REVIEWER:
        return FilterResult('independence', 'NOT_APPLICABLE',
                            'only the Reviewer requires model independence')
    if candidate.model_id is None:
        return FilterResult(
            'independence', 'UNKNOWN',
            'model identity is UNKNOWN; independence cannot be established')
    reference = requirement.reference_model_id
    if reference is None:
        return FilterResult(
            'independence', 'UNKNOWN',
            'no Execution model was selected; Reviewer independence cannot '
            'be established')
    if candidate.model_id == reference or \
            candidate.model_id in requirement.excluded_model_ids:
        return FilterResult(
            'independence', 'FAIL',
            f'model id {candidate.model_id!r} is the same as the selected '
            'Execution model; the same model with another name is not '
            'independent')
    reviewer_trusted, reviewer_canonical, reviewer_status, reviewer_detail = (
        _identity_verdict(candidate.canonical_identity,
                          model_id=candidate.model_id, now=now))
    execution_trusted, execution_canonical, execution_status, execution_detail = (
        _identity_verdict(requirement.reference_canonical_identity,
                          model_id=reference, now=now))
    if not reviewer_trusted or not execution_trusted:
        return FilterResult(
            'independence', 'UNKNOWN',
            f'Reviewer identity {reviewer_status}: {reviewer_detail}; '
            f'Execution identity {execution_status}: {execution_detail}; '
            'model-level independence cannot be established, so automatic '
            'dispatch stays disabled')
    if reviewer_canonical.casefold() == execution_canonical.casefold():
        return FilterResult(
            'independence', 'FAIL',
            f'model id {candidate.model_id!r} is the same as the selected '
            f'Execution model under verified canonical identity '
            f'{reviewer_canonical!r}; the same underlying model under another '
            'name or family is not independent')
    detail = (f'model-level independence established from verified registry '
              f'identities ({execution_canonical} vs {reviewer_canonical})')
    reference_family = requirement.reference_model_family
    if candidate.model_family is None or reference_family is None:
        detail += ('; model family UNKNOWN; family-level independence is '
                   'reported as UNKNOWN and not claimed')
    elif candidate.model_family.casefold() == reference_family.casefold():
        detail += ('; same model family as Execution; only model-level '
                   'independence is established')
    else:
        detail += (f'; a different model family is self-declared '
                   f'({candidate.model_family} vs {reference_family}) and is '
                   'reported, not used as independence evidence')
    return FilterResult('independence', 'PASS', detail)


def _budget_filter(candidate, requirement):
    problems = []
    unknown = []
    if requirement.max_cost_per_mtok is not None:
        if candidate.cost_per_mtok is None:
            unknown.append('cost per Mtok was not reported')
        elif candidate.cost_per_mtok > requirement.max_cost_per_mtok:
            problems.append(
                f'cost {candidate.cost_per_mtok}/Mtok exceeds the limit '
                f'{requirement.max_cost_per_mtok}')
    if requirement.max_latency_ms is not None:
        if candidate.latency_ms is None:
            unknown.append('latency was not reported')
        elif candidate.latency_ms > requirement.max_latency_ms:
            problems.append(
                f'latency {candidate.latency_ms}ms exceeds the limit '
                f'{requirement.max_latency_ms}ms')
    if problems:
        return FilterResult('budget', 'FAIL', '; '.join(problems))
    if unknown:
        return FilterResult('budget', 'UNKNOWN', '; '.join(unknown))
    if requirement.max_cost_per_mtok is None and requirement.max_latency_ms is None:
        return FilterResult('budget', 'NOT_APPLICABLE',
                            'no task budget / latency limit')
    return FilterResult('budget', 'PASS', 'within the task limits')


def evaluate_candidate(candidate, requirement, *, now=None,
                       max_age_days=DEFAULT_MAX_EVIDENCE_AGE_DAYS):
    """Apply the hard filters in fixed order; ranking is a separate step."""
    filters = (
        FilterResult('runtime', 'PASS',
                     f'{candidate.runtime_id} {candidate.runtime_version} via '
                     f'{candidate.source}'),
        _evidence_filter(candidate, now, max_age_days),
        (FilterResult('identity', 'PASS', f'model id {candidate.model_id}')
         if candidate.identity_known else
         FilterResult('identity', 'FAIL',
                      'model identity is UNKNOWN; an unidentified model cannot '
                      'be a qualified role')),
        _callable_filter(candidate, requirement, now, max_age_days),
        _modality_filter(candidate, requirement),
        _tools_filter(candidate, requirement),
        _context_filter(candidate, requirement),
        _permissions_filter(candidate, requirement),
        _independence_filter(candidate, requirement, now=now),
        _budget_filter(candidate, requirement),
    )
    return CandidateEvaluation(candidate=candidate, role=requirement.role,
                               filters=filters)


def _rank(evaluation, *, project_min_samples=PROJECT_MIN_SAMPLES):
    """Explainable sort key: role evidence, project evidence, then cost."""
    role = evaluation.role
    evidence = evaluation.candidate.evidence_for(role)
    buckets = {'project': {}, 'external-reference': {}}
    notes = []
    for item in evidence:
        if item.grade == 'UNKNOWN':
            notes.append(f'{item.dimension}: UNKNOWN evidence')
            continue
        if item.source_class != 'OBSERVED':
            notes.append(f'{item.dimension}: {item.source_class} evidence '
                         'does not count')
            continue
        if item.origin == 'project' and item.sample_size < project_min_samples:
            notes.append(
                f'{item.dimension}: project sample {item.sample_size} is below '
                f'{project_min_samples}; reported, not counted')
            continue
        buckets[item.origin][item.dimension] = item
        notes.append(
            f'{item.dimension}={item.grade} ({item.origin}, '
            f'n={item.sample_size})')
    grades = ('STRONG', 'MODERATE', 'WEAK')

    def count(origin, grade):
        return sum(1 for item in buckets[origin].values()
                   if item.grade == grade)

    key = tuple(-count('project', grade) for grade in grades) + tuple(
        -count('external-reference', grade) for grade in grades) + (
        evaluation.candidate.cost_per_mtok
        if evaluation.candidate.cost_per_mtok is not None else math.inf,
        evaluation.candidate.latency_ms
        if evaluation.candidate.latency_ms is not None else math.inf,
        evaluation.candidate.candidate_id,
    )
    return key, tuple(notes)


def _reminder(role, requirement):
    if role == ROLE_EXECUTION:
        return (
            'No qualified Execution Agent. Required: fresh C1-C4 PASS on this '
            f'runtime, tools {", ".join(requirement.required_tools) or "(none)"}, '
            f'and an observed write scope covering '
            f'{", ".join(requirement.write_paths) or "(none)"}. Register the '
            'agent in the current scope\'s agent-registry.md with runtime_id, '
            'runtime_version, agent_id, model_id, modalities, tools, '
            'permissions and a capability_ref; the selector does not create or '
            'authorize agents. The task stays undispatched until a real '
            'spawn / wait / collect path exists.')
    if role == ROLE_REVIEWER:
        return (
            'No qualified Reviewer. Required: a different underlying model '
            'from Execution, proven by a verified canonical model identity in '
            'the current scope\'s agent-registry.md (canonical_model_id, '
            'identity_verification=CONFIRMED, identity_verified_until, '
            'identity_evidence); an observed read-only boundary; and '
            'defect-detection / test-design / security-review evidence. A '
            'different model_id or model_family self-report is not '
            'independence. Keep the task unreviewed; Execution self-review is '
            'not a substitute. Human review is allowed only when the project '
            'configures it for this task class.')
    return (
        'Master text + image input is not verified. A process-local CLI cannot '
        'read the host Agent\'s tool list; Master must check its own actually '
        'available tools and record the standardized facts before relying on '
        'image input.')


def _selection(role, requirement, candidates, *, now, max_age_days,
               project_min_samples):
    evaluations = [evaluate_candidate(candidate, requirement, now=now,
                                      max_age_days=max_age_days)
                   for candidate in candidates]
    qualified, unverified, excluded = [], [], []
    for evaluation in evaluations:
        if evaluation.status == 'PASS':
            qualified.append(evaluation)
        elif evaluation.status == 'UNKNOWN':
            unverified.append(evaluation)
        else:
            excluded.append(evaluation)

    ranked = []
    for evaluation in qualified:
        key, notes = _rank(evaluation,
                           project_min_samples=project_min_samples)
        ranked.append(CandidateEvaluation(
            candidate=evaluation.candidate, role=evaluation.role,
            filters=evaluation.filters, reasons=notes, rank_key=key))
    ranked.sort(key=lambda item: item.rank_key)

    selected = ranked[0] if ranked else None
    alternatives = tuple(ranked[1:])

    if selected is not None:
        reasons = (
            f'selected {selected.candidate.candidate_id} '
            f'(model {selected.candidate.model_id}) for {role}',
        ) + selected.reasons
    else:
        reasons = ('no candidate passed every hard filter; no role is '
                   'selected',)

    if candidates:
        missing_items = []
        for evaluation in list(unverified) + list(excluded):
            for item in evaluation.missing:
                entry = f'{evaluation.candidate.candidate_id} {item}'
                if entry not in missing_items:
                    missing_items.append(entry)
        missing = tuple(missing_items) or (
            'no candidate passed every hard filter',)
    else:
        missing = ('discovery returned no candidate at all',)

    if selected is None:
        return RoleSelection(
            role=role, requirement=requirement, selected=None,
            alternatives=(), unverified=tuple(unverified),
            excluded=tuple(excluded), missing=missing,
            reminder=_reminder(role, requirement), reasons=reasons,
            manual_handoff=True, auto_allowed=False)

    return RoleSelection(
        role=role, requirement=requirement, selected=selected,
        alternatives=alternatives, unverified=tuple(unverified),
        excluded=tuple(excluded), missing=(),
        reminder=None,
        reasons=reasons,
        manual_handoff=False, auto_allowed=True)


@dataclass(frozen=True)
class RoleSelection:
    """One role's outcome: who, why, who else, and what is missing."""

    role: str
    requirement: RoleRequirement
    selected: CandidateEvaluation | None
    alternatives: tuple[CandidateEvaluation, ...]
    unverified: tuple[CandidateEvaluation, ...]
    excluded: tuple[CandidateEvaluation, ...]
    missing: tuple[str, ...]
    reminder: str | None
    reasons: tuple[str, ...]
    manual_handoff: bool
    auto_allowed: bool

    def as_dict(self):
        return {
            'role': self.role,
            'requirement': self.requirement.as_dict(),
            'selected': (self.selected.as_dict()
                         if self.selected is not None else None),
            'alternatives': [item.as_dict() for item in self.alternatives],
            'unverified': [item.as_dict() for item in self.unverified],
            'excluded': [item.as_dict() for item in self.excluded],
            'missing': list(self.missing),
            'reminder': self.reminder,
            'reasons': list(self.reasons),
            'manual_handoff': self.manual_handoff,
            'auto_allowed': self.auto_allowed,
        }


@dataclass(frozen=True)
class SelectionReport:
    """The complete role-matching report for one task.

    ``executed`` is always ``False`` in S2: this module has no spawn / wait /
    collect implementation, so a successful selection is a recommendation for
    Master, not a dispatch.
    """

    discovery: DiscoveryReport
    selections: dict
    automatic_dispatch_allowed: bool
    degradations: tuple[str, ...]
    notes: tuple[str, ...] = ()
    executed: bool = False

    def role(self, role):
        return self.selections[role]

    def as_dict(self):
        return {
            'discovery': self.discovery.as_dict(),
            'selections': {role: selection.as_dict()
                           for role, selection in self.selections.items()},
            'automatic_dispatch_allowed': self.automatic_dispatch_allowed,
            'executed': self.executed,
            'degradations': list(self.degradations),
            'notes': list(self.notes),
        }

    def render_lines(self):
        lines = [f'discovery: {self.discovery.outcome}']
        for role in ROLES:
            selection = self.selections[role]
            chosen = (selection.selected.candidate.candidate_id
                      if selection.selected is not None else '(none)')
            lines.append(
                f'{role}: {chosen} auto={selection.auto_allowed} '
                f'handoff={selection.manual_handoff}')
            for reason in selection.reasons:
                lines.append(f'  reason: {reason}')
            for item in selection.missing:
                lines.append(f'  missing: {item}')
            if selection.reminder:
                lines.append(f'  reminder: {selection.reminder}')
        lines.append(
            f'automatic_dispatch_allowed={self.automatic_dispatch_allowed} '
            f'executed={self.executed}')
        for degradation in self.degradations:
            lines.append(f'degradation: {degradation}')
        for note in self.notes:
            lines.append(f'note: {note}')
        return lines


def select_roles(discovery, requirements, *, now=None,
                 max_age_days=DEFAULT_MAX_EVIDENCE_AGE_DAYS,
                 project_min_samples=PROJECT_MIN_SAMPLES) -> SelectionReport:
    """Match every role against the discovered candidates.

    *requirements* must contain one :class:`RoleRequirement` per role. A missing
    requirement raises: silently substituting a lenient default would drop a
    hard constraint (for example Master's image input).
    """
    if not isinstance(discovery, DiscoveryReport):
        raise TypeError('discovery must be a DiscoveryReport')
    missing_requirements = [role for role in ROLES if role not in requirements]
    if missing_requirements:
        raise ValueError(
            'a requirement must be supplied for every role; missing: '
            f'{", ".join(missing_requirements)} (no lenient default is used)')
    selections = {}
    degradations = []
    candidates = discovery.candidates if discovery.discovered else ()
    for role in (ROLE_MASTER, ROLE_EXECUTION):
        requirement = requirements[role]
        if requirement.role != role:
            raise ValueError(
                f'requirement for {role} declares role {requirement.role!r}')
        selections[role] = _selection(
            role, requirement, candidates, now=now,
            max_age_days=max_age_days,
            project_min_samples=project_min_samples)

    # Reviewer independence is a structural consequence of the *selected*
    # Execution model, not of what a caller put in the requirement. Rebuild the
    # reference from the real selection and re-evaluate.
    reviewer_requirement_value = requirements[ROLE_REVIEWER]
    if reviewer_requirement_value.role != ROLE_REVIEWER:
        raise ValueError(
            'requirement for reviewer declares role '
            f'{reviewer_requirement_value.role!r}')
    selected_execution = selections[ROLE_EXECUTION].selected
    execution_model_id = (selected_execution.candidate.model_id
                          if selected_execution is not None else None)
    execution_model_family = (selected_execution.candidate.model_family
                              if selected_execution is not None else None)
    execution_canonical_identity = (
        selected_execution.candidate.canonical_identity
        if selected_execution is not None else None)
    excluded = tuple(dict.fromkeys(
        tuple(reviewer_requirement_value.excluded_model_ids)
        + ((execution_model_id,) if execution_model_id else ())))
    reviewer_requirement_value = replace(
        reviewer_requirement_value,
        excluded_model_ids=excluded,
        reference_model_id=execution_model_id,
        reference_model_family=execution_model_family,
        reference_canonical_identity=execution_canonical_identity)
    selections[ROLE_REVIEWER] = _selection(
        ROLE_REVIEWER, reviewer_requirement_value, candidates, now=now,
        max_age_days=max_age_days,
        project_min_samples=project_min_samples)

    master = selections[ROLE_MASTER]
    execution = selections[ROLE_EXECUTION]
    reviewer = selections[ROLE_REVIEWER]

    if not discovery.discovered:
        degradations.append(
            f'discovery {discovery.outcome}: no candidate can be selected; '
            'degrade to manual handoff and do not claim a dispatch')
    if master.selected is None:
        degradations.append(
            'Master text + image input is not verified; the multi-modal hard '
            'requirement is unmet')
    if execution.selected is None:
        degradations.append(
            'no qualified Execution Agent; the task stays undispatched and '
            'must be handed off manually')
    if reviewer.selected is None:
        if execution.selected is None:
            reason = ('no qualified Execution Agent, so Reviewer independence '
                      'cannot be established')
        else:
            reason = ('no independent Reviewer; the task stays unreviewed and '
                      'Execution self-review is not accepted')
        degradations.append(reason)

    automatic = (
        discovery.outcome in (DISCOVERY_RUNTIME, DISCOVERY_REGISTRY)
        and master.auto_allowed and execution.auto_allowed
        and reviewer.auto_allowed)

    notes = (
        'this selector does not spawn, wait for or collect an agent; a '
        'selection is a recommendation, not a dispatch',
        'capability PASS never grants a path, a command or network access; the '
        'task keeps its own allowed paths',
        'Reviewer independence was enforced against the selected Execution '
        'model\'s verified canonical identity in the current scope registry; a '
        'different model id or model family self-report is reported, not '
        'assumed',
    )
    return SelectionReport(
        discovery=discovery, selections=selections,
        automatic_dispatch_allowed=automatic,
        degradations=tuple(degradations), notes=notes, executed=False)
