#!/usr/bin/env python3
"""Role-specific model selection on top of S2 (#122, S3).

Design contract: ``docs/design/agent-role-discovery-and-model-selection.md``
§6–§7. The order is fixed and is **not** re-implemented here:

1. S2 (:mod:`scripts.agent_selection.roles`) applies every hard filter
   (callability, C1–C4, modality, tools, context, permissions, reviewer
   independence, budget) and reports the survivors;
2. this module turns an external snapshot into role-relevant evidence and hands
   the enriched candidates back to ``roles.select_roles``, so the *same* S2
   ranking (project evidence, external evidence, cost, latency) decides;
3. the report keeps the source, the fetch time, the freshness, the index
   version, the limitations and every degradation.

Two consequences matter:

* external data can never *rescue* a candidate: modality, callability,
  permissions and budget are still evaluated on the runtime facts, not on a
  vendor score;
* a Reviewer is never ranked by a general intelligence index. Artificial
  Analysis publishes no code-review / defect-detection benchmark, so those
  dimensions stay ``UNKNOWN`` and the report says why. A same-model Reviewer is
  still rejected structurally by S2 on the underlying model id.

The module never spawns, waits for or collects an agent. ``executed`` is always
``False``.

Standard library only; no network access (the source adapter owns that).
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .model_sources.snapshot import (
    FRESHNESS_FRESH,
    UNKNOWN,
    SnapshotBatch,
)
from .roles import (
    ROLE_EXECUTION,
    ROLE_MASTER,
    ROLE_REVIEWER,
    ROLES,
    SelectionReport,
    evaluate_candidate,
    select_roles,
)
from .runtime_adapter import (
    ROLE_DIMENSIONS,
    ROLE_GRADES,
    RoleEvidence,
)

__all__ = [
    'ROLE_BENCHMARK_MAP',
    'REVIEWER_NO_BENCHMARK_NOTE',
    'DEFAULT_MIN_COHORT_SIZE',
    'LOCAL_EVIDENCE_STATUSES',
    'LOCAL_ACTIONS',
    'ModelSelectionError',
    'ModelLink',
    'AssessmentPolicy',
    'ExternalSignal',
    'ModelAssessment',
    'LocalEvidence',
    'ModelSelectionReport',
    'select_models',
]

# Which published evaluation may speak to which role dimension. Anything not
# listed stays UNKNOWN. A total intelligence score is deliberately absent for
# the Reviewer: it is not a code-review / defect-detection measurement.
ROLE_BENCHMARK_MAP = {
    ROLE_MASTER: (
        ('artificial_analysis_intelligence_index', 'planning',
         'composite intelligence index as a supplementary planning signal'),
        ('ifbench', 'instruction-following', 'IFBench instruction following'),
    ),
    ROLE_EXECUTION: (
        ('artificial_analysis_coding_index', 'coding',
         'composite coding index'),
        ('artificial_analysis_agentic_index', 'tool-use',
         'composite agentic / tool-use index'),
        ('livecodebench', 'coding', 'LiveCodeBench'),
        ('scicode', 'coding', 'SciCode'),
        ('terminalbench_v2_1', 'coding', 'Terminal-Bench v2.1'),
        ('ifbench', 'instruction-following', 'IFBench instruction following'),
        ('tau_banking', 'tool-use', 'tau-bench banking'),
        ('tau2_telecom', 'tool-use', 'tau2-bench telecom'),
    ),
    ROLE_REVIEWER: (),
}

REVIEWER_NO_BENCHMARK_NOTE = (
    'Artificial Analysis publishes no code-review / defect-detection / '
    'security-review benchmark; the general intelligence index is deliberately '
    'not substituted for a review score, so this dimension stays UNKNOWN')

DEFAULT_MIN_COHORT_SIZE = 6
_GRADE_ORDER = {'STRONG': 0, 'MODERATE': 1, 'WEAK': 2, 'UNKNOWN': 3}

# Local, current-scope evidence states. They are separate from the external
# snapshot statuses: a missing local cohort is not "bad quality", and a
# measured ETR is never inherited across a model identity, a task category or
# a scope.
LOCAL_EVIDENCE_STATUSES = (
    'CURRENT', 'PROVISIONAL', 'PAUSE_AUTO_SELECTION', 'RAISE_PRIORITY',
    'NO_HISTORY', 'NO_CATEGORY', 'NO_COHORT', 'AMBIGUOUS_IDENTITY',
    'STALE', 'IDENTITY_CHANGED', 'UNKNOWN_IDENTITY', 'UNKNOWN', 'NO_DATA',
)
# What the selector actually did with the local evidence. ``NOT_APPLICABLE`` is
# a candidate that already failed an existing hard filter; ``MANUAL_ONLY`` is
# the Master, which is always selected by the user.
LOCAL_ACTIONS = ('NONE', 'PROVISIONAL', 'PAUSE_AUTO_SELECTION',
                 'RAISE_PRIORITY', 'NOT_APPLICABLE', 'MANUAL_ONLY')


@dataclass(frozen=True)
class LocalEvidence:
    """One candidate's ETR / credibility view for one role and task category.

    It is sourced only from the caller's *current scope* feedback report and
    peer-review events passed to :func:`select_models`. It cannot change a
    hard filter result: :attr:`applied_action` is ``NOT_APPLICABLE`` when the
    candidate already failed one. ``applied_action`` is also where the
    provisional thresholds are expressed, so ``gate_status`` (the raw measured
    gate) and the action actually taken stay separate.
    """

    role: str
    candidate_id: str
    model_id: str | None
    canonical_model_id: str | None
    identity_status: str
    category: str | None
    scope_id: str | None
    status: str = 'NO_HISTORY'
    etr_value_status: str = UNKNOWN
    etr_value: float | None = None
    token_coverage_status: str = UNKNOWN
    token_coverage: float | None = None
    observed_tokens: int | None = None
    scored_tokens: int | None = None
    rated_tasks: int = 0
    total_tasks: int = 0
    window_start: str = UNKNOWN
    window_end: str = UNKNOWN
    evidence_status: str = UNKNOWN
    gate_status: str = 'NO_GATE'
    applied_action: str = 'NONE'
    credibility: str = UNKNOWN
    credibility_basis: str = ''
    cost_per_mtok: float | None = None
    latency_ms: int | None = None
    hard_filter_status: str = 'UNKNOWN'
    decided_at: str = ''
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    unit = 'percent'

    def __post_init__(self):
        if self.role not in ROLES:
            raise ModelSelectionError(f'unknown role {self.role!r}')
        if self.status not in LOCAL_EVIDENCE_STATUSES:
            raise ModelSelectionError(
                f'unknown local evidence status {self.status!r}')
        if self.applied_action not in LOCAL_ACTIONS:
            raise ModelSelectionError(
                f'unknown local action {self.applied_action!r}')
        for name in ('etr_value', 'token_coverage'):
            value = getattr(self, name)
            if value is not None and not 0.0 <= value <= 100.0:
                raise ModelSelectionError(
                    f'{name} must be a percentage in [0, 100]')

    @property
    def measured(self):
        return self.etr_value_status == 'OK' and self.etr_value is not None

    def as_dict(self):
        return {
            'role': self.role,
            'candidate_id': self.candidate_id,
            'model_id': self.model_id if self.model_id is not None else UNKNOWN,
            'canonical_model_id': (self.canonical_model_id
                                   if self.canonical_model_id is not None
                                   else UNKNOWN),
            'identity_status': self.identity_status,
            'category': self.category if self.category is not None else UNKNOWN,
            'scope_id': self.scope_id if self.scope_id is not None else UNKNOWN,
            'status': self.status,
            'etr_value_status': self.etr_value_status,
            'etr_value': (self.etr_value if self.etr_value is not None
                          else UNKNOWN),
            'unit': self.unit,
            'token_coverage_status': self.token_coverage_status,
            'token_coverage': (self.token_coverage
                               if self.token_coverage is not None else UNKNOWN),
            'observed_tokens': (self.observed_tokens
                                if self.observed_tokens is not None
                                else UNKNOWN),
            'scored_tokens': (self.scored_tokens
                              if self.scored_tokens is not None else UNKNOWN),
            'rated_tasks': self.rated_tasks,
            'total_tasks': self.total_tasks,
            'window': {'start': self.window_start, 'end': self.window_end},
            'evidence_status': self.evidence_status,
            'gate_status': self.gate_status,
            'applied_action': self.applied_action,
            'credibility': self.credibility,
            'credibility_basis': self.credibility_basis,
            'cost_per_mtok': (self.cost_per_mtok
                              if self.cost_per_mtok is not None else UNKNOWN),
            'latency_ms': (self.latency_ms
                           if self.latency_ms is not None else UNKNOWN),
            'hard_filter_status': self.hard_filter_status,
            'decided_at': self.decided_at,
            'reasons': list(self.reasons),
            'notes': list(self.notes),
        }


class ModelSelectionError(ValueError):
    """The selection inputs violate the model-selection contract."""


@dataclass(frozen=True)
class ModelLink:
    """Explicit link from a runtime model id to a source snapshot id.

    The match is exact (canonical id or slug). There is no name-similarity or
    fuzzy fallback: guessing an identity would silently attach another model's
    scores to a candidate.
    """

    candidate_model_id: str
    snapshot_id: str

    def __post_init__(self):
        for name in ('candidate_model_id', 'snapshot_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ModelSelectionError(f'a model link needs a {name}')


@dataclass(frozen=True)
class AssessmentPolicy:
    """When the caller requires external evidence to be present and fresh.

    The defaults are deliberately permissive about *external* data (it is an
    optional adapter) and strict about *reported* status: a missing source is
    always visible in the report. A project that wants external benchmark
    evidence to be mandatory sets one of these switches; the selector then
    returns ``automatic_allowed=False`` with a decision request instead of
    quietly ranking on less evidence.
    """

    require_fresh_source: bool = False
    require_snapshot_for_selected: bool = False
    require_role_evidence: bool = False
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE
    strong_percentile: float = 0.80
    moderate_percentile: float = 0.50

    def __post_init__(self):
        if isinstance(self.min_cohort_size, bool) or not isinstance(
                self.min_cohort_size, int) or self.min_cohort_size < 2:
            raise ModelSelectionError('min_cohort_size must be an integer >= 2')
        for name in ('strong_percentile', 'moderate_percentile'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not 0 < value <= 1:
                raise ModelSelectionError(f'{name} must be in (0, 1]')
        if self.moderate_percentile >= self.strong_percentile:
            raise ModelSelectionError(
                'moderate_percentile must be lower than strong_percentile')


@dataclass(frozen=True)
class ExternalSignal:
    """One role dimension's external evidence, with provenance and limits."""

    role: str
    dimension: str
    grade: str
    evaluation_name: str | None = None
    value: float | None = None
    unit: str = UNKNOWN
    index_version: str | None = None
    percentile: float | None = None
    cohort_size: int | None = None
    evidence_ref: str | None = None
    observed_at: str | None = None
    source_class: str = 'UNKNOWN'
    note: str = ''

    def __post_init__(self):
        if self.role not in ROLE_DIMENSIONS:
            raise ModelSelectionError(f'unknown role {self.role!r}')
        if self.dimension not in ROLE_DIMENSIONS[self.role]:
            raise ModelSelectionError(
                f'{self.role}: unknown dimension {self.dimension!r}')
        if self.grade not in ROLE_GRADES:
            raise ModelSelectionError(
                f'unknown signal grade {self.grade!r}; expected one of '
                f'{ROLE_GRADES}')
        if self.percentile is not None and not 0 <= self.percentile <= 1:
            raise ModelSelectionError('percentile must be within 0..1')
        if self.cohort_size is not None and self.cohort_size < 1:
            raise ModelSelectionError('cohort_size must be positive')
        if self.grade != 'UNKNOWN':
            if self.source_class != 'OBSERVED':
                raise ModelSelectionError(
                    f'{self.dimension}: a graded signal requires OBSERVED data')
            if not self.evidence_ref or not self.observed_at:
                raise ModelSelectionError(
                    f'{self.dimension}: a graded signal needs evidence_ref and '
                    'observed_at')

    @property
    def known(self):
        return self.grade != 'UNKNOWN'

    def as_dict(self):
        return {
            'role': self.role,
            'dimension': self.dimension,
            'grade': self.grade,
            'evaluation_name': (self.evaluation_name
                                if self.evaluation_name is not None else UNKNOWN),
            'value': self.value if self.value is not None else UNKNOWN,
            'unit': self.unit,
            'index_version': (self.index_version
                              if self.index_version is not None else UNKNOWN),
            'percentile': (round(self.percentile, 3)
                           if self.percentile is not None else UNKNOWN),
            'cohort_size': (self.cohort_size
                            if self.cohort_size is not None else UNKNOWN),
            'evidence_ref': (self.evidence_ref
                             if self.evidence_ref is not None else UNKNOWN),
            'observed_at': (self.observed_at
                            if self.observed_at is not None else UNKNOWN),
            'source_class': self.source_class,
            'note': self.note,
        }


@dataclass(frozen=True)
class ModelAssessment:
    """One candidate's external view for one role (never a hard filter)."""

    role: str
    candidate_id: str
    model_id: str | None
    status: str
    model_family: str | None = None
    signals: tuple[ExternalSignal, ...] = ()
    snapshot_id: str | None = None
    snapshot_name: str | None = None
    snapshot_slug: str | None = None
    freshness: str = 'UNKNOWN'
    observed_at: str | None = None
    limitations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    selected: bool = False

    ASSESSMENT_STATUSES = ('MATCHED', 'STALE', 'UNLINKED', 'NO_SOURCE', 'UNKNOWN')

    def __post_init__(self):
        if self.role not in ROLES:
            raise ModelSelectionError(f'unknown role {self.role!r}')
        if self.status not in self.ASSESSMENT_STATUSES:
            raise ModelSelectionError(
                f'unknown assessment status {self.status!r}; expected one of '
                f'{self.ASSESSMENT_STATUSES}')

    @property
    def has_role_evidence(self):
        return any(signal.known for signal in self.signals)

    def as_dict(self):
        return {
            'role': self.role,
            'candidate_id': self.candidate_id,
            'model_id': self.model_id if self.model_id is not None else UNKNOWN,
            'model_family': (self.model_family
                             if self.model_family is not None else UNKNOWN),
            'status': self.status,
            'selected': self.selected,
            'snapshot': (
                {
                    'canonical_id': self.snapshot_id,
                    'name': self.snapshot_name,
                    'slug': self.snapshot_slug,
                    'freshness': self.freshness,
                    'observed_at': (self.observed_at
                                    if self.observed_at is not None
                                    else UNKNOWN),
                }
                if self.snapshot_id is not None else None),
            'signals': [signal.as_dict() for signal in self.signals],
            'has_role_evidence': self.has_role_evidence,
            'limitations': list(self.limitations),
            'notes': list(self.notes),
        }


@dataclass(frozen=True)
class ModelSelectionReport:
    """The complete S3 report: S2 selection + external evidence + degradations."""

    source: SnapshotBatch
    role_selection: SelectionReport
    assessments: tuple[ModelAssessment, ...]
    automatic_allowed: bool
    degradations: tuple[str, ...]
    decisions: tuple[str, ...]
    notes: tuple[str, ...]
    executed: bool = False
    local_evidence: tuple[LocalEvidence, ...] = ()
    task_category: str | None = None
    decision_time: str = ''

    def role(self, role):
        return self.role_selection.role(role)

    def assessments_for(self, role):
        return tuple(item for item in self.assessments if item.role == role)

    def selected_assessment(self, role):
        for item in self.assessments:
            if item.role == role and item.selected:
                return item
        return None

    def local_evidence_for(self, role, candidate_id=None):
        items = tuple(item for item in self.local_evidence
                      if item.role == role
                      and (candidate_id is None
                           or item.candidate_id == candidate_id))
        if candidate_id is None:
            return items
        return items[0] if items else None

    def as_dict(self):
        return {
            'source': self.source.as_dict(),
            'role_selection': self.role_selection.as_dict(),
            'assessments': [item.as_dict() for item in self.assessments],
            'local_evidence': [item.as_dict() for item in self.local_evidence],
            'task_category': (self.task_category
                              if self.task_category is not None else UNKNOWN),
            'decision_time': self.decision_time or UNKNOWN,
            'automatic_allowed': self.automatic_allowed,
            'degradations': list(self.degradations),
            'decisions': list(self.decisions),
            'notes': list(self.notes),
            'executed': self.executed,
        }

    def render_lines(self):
        fetch = self.source.fetch
        lines = [
            f'source: {fetch.source_id} status={fetch.status} '
            f'live={fetch.live} freshness={fetch.freshness} '
            f'tier={fetch.tier or UNKNOWN} '
            f'index_version={fetch.index_version or UNKNOWN} '
            f'fetched_at={fetch.fetched_at}',
        ]
        if fetch.message:
            lines.append(f'  source message: {fetch.message}')
        for role in ROLES:
            selection = self.role(role)
            chosen = (selection.selected.candidate.candidate_id
                      if selection.selected is not None else '(none)')
            assessment = self.selected_assessment(role)
            status = assessment.status if assessment is not None else 'UNKNOWN'
            lines.append(
                f'{role}: {chosen} auto={selection.auto_allowed} '
                f'handoff={selection.manual_handoff} external={status}')
            for reason in selection.reasons:
                lines.append(f'  reason: {reason}')
            for item in selection.missing:
                lines.append(f'  missing: {item}')
            if assessment is not None:
                for signal in assessment.signals:
                    lines.append(
                        f'  signal: {signal.dimension}={signal.grade} '
                        f'({signal.evaluation_name or UNKNOWN})')
            for local in self.local_evidence_for(role):
                measured = (local.etr_value if local.measured
                            else local.etr_value_status)
                coverage = (local.token_coverage
                            if local.token_coverage is not None
                            else local.token_coverage_status)
                lines.append(
                    f'  local: {local.candidate_id} status={local.status} '
                    f'etr={measured} coverage={coverage} '
                    f'credibility={local.credibility} '
                    f'action={local.applied_action}')
            for held in selection.withheld:
                lines.append(
                    f'  withheld: {held.candidate.candidate_id}')
            if selection.reminder:
                lines.append(f'  reminder: {selection.reminder}')
        lines.append(
            f'automatic_allowed={self.automatic_allowed} '
            f'executed={self.executed} '
            f'category={self.task_category or UNKNOWN} '
            f'decided_at={self.decision_time or UNKNOWN}')
        for degradation in self.degradations:
            lines.append(f'degradation: {degradation}')
        for decision in self.decisions:
            lines.append(f'decision: {decision}')
        for note in self.notes:
            lines.append(f'note: {note}')
        return lines


def _link_map(links):
    if links is None:
        return {}
    if isinstance(links, Mapping):
        items = list(links.items())
    else:
        items = []
        for link in links:
            if not isinstance(link, ModelLink):
                raise ModelSelectionError(
                    'links must be ModelLink objects or a mapping')
            items.append((link.candidate_model_id, link.snapshot_id))
    result = {}
    for candidate_model_id, snapshot_id in items:
        if not isinstance(candidate_model_id, str) or not candidate_model_id.strip():
            raise ModelSelectionError('a link needs a candidate_model_id')
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ModelSelectionError('a link needs a snapshot_id')
        if candidate_model_id in result:
            raise ModelSelectionError(
                f'{candidate_model_id}: more than one snapshot link is '
                'ambiguous; keep one explicit mapping')
        result[candidate_model_id] = snapshot_id
    return result


def _percentiles(batch):
    """Value lists per evaluation name, over fresh models only."""
    values = {}
    if not batch.usable:
        return {}
    for snapshot in batch.models:
        if snapshot.freshness != FRESHNESS_FRESH:
            continue
        for evaluation in snapshot.evaluations:
            if evaluation.value is None:
                continue
            values.setdefault(evaluation.name, []).append(evaluation.value)
    return {name: sorted(items) for name, items in values.items()}


def _grade_for(percentile, policy):
    if percentile >= policy.strong_percentile:
        return 'STRONG'
    if percentile >= policy.moderate_percentile:
        return 'MODERATE'
    return 'WEAK'


def _unknown_signal(role, dimension, note, *, evaluation_name=None):
    return ExternalSignal(role=role, dimension=dimension, grade='UNKNOWN',
                          evaluation_name=evaluation_name, note=note)


def _signals_for(snapshot, role, percentiles, policy, *, usable):
    """Best published evaluation per role dimension; never a generic substitute."""
    dimensions = ROLE_DIMENSIONS[role]
    if role == ROLE_REVIEWER:
        return tuple(_unknown_signal(role, dimension,
                                     REVIEWER_NO_BENCHMARK_NOTE)
                     for dimension in dimensions)
    if snapshot is None or not usable or snapshot.freshness != FRESHNESS_FRESH:
        return tuple(
            _unknown_signal(
                role, dimension,
                'no fresh external snapshot for this model; external evidence '
                'is UNKNOWN and was not used')
            for dimension in dimensions)

    best = {}
    for name, dimension, label in ROLE_BENCHMARK_MAP.get(role, ()):
        evaluation = snapshot.evaluation(name)
        if evaluation is None:
            continue
        if evaluation.value is None:
            signal = _unknown_signal(
                role, dimension,
                f'{label} was published as null / not measured; UNKNOWN',
                evaluation_name=name)
        else:
            cohort = percentiles.get(name, [])
            cohort_size = len(cohort)
            if cohort_size < policy.min_cohort_size:
                signal = ExternalSignal(
                    role=role, dimension=dimension, grade='UNKNOWN',
                    evaluation_name=name, value=evaluation.value,
                    unit=evaluation.unit,
                    index_version=evaluation.index_version,
                    cohort_size=cohort_size, source_class='OBSERVED',
                    note=(f'{label} value is published, but the comparison '
                          f'cohort ({cohort_size}) is smaller than '
                          f'{policy.min_cohort_size}; grade stays UNKNOWN'))
            else:
                percentile = bisect_right(cohort, evaluation.value) / cohort_size
                signal = ExternalSignal(
                    role=role, dimension=dimension,
                    grade=_grade_for(percentile, policy),
                    evaluation_name=name, value=evaluation.value,
                    unit=evaluation.unit,
                    index_version=evaluation.index_version,
                    percentile=percentile, cohort_size=cohort_size,
                    evidence_ref=(f'{snapshot.source_url} '
                                  f'model={snapshot.slug} eval={name}'),
                    observed_at=snapshot.fetched_at,
                    source_class='OBSERVED',
                    note=(f'{label}; percentile {percentile:.2f} within the '
                          f'{cohort_size} models of this snapshot; one '
                          'published value (the provider does not publish '
                          'benchmark task counts)'))
        existing = best.get(dimension)
        if existing is None or _signal_sort_key(signal) < _signal_sort_key(existing):
            best[dimension] = signal

    signals = []
    for dimension in dimensions:
        signal = best.get(dimension)
        if signal is None:
            signal = _unknown_signal(
                role, dimension,
                'the source publishes no mapped evaluation for this dimension; '
                'UNKNOWN, not a zero score')
        signals.append(signal)
    return tuple(signals)


def _signal_sort_key(signal):
    return (_GRADE_ORDER[signal.grade],
            -(signal.percentile if signal.percentile is not None else -1.0),
            signal.evaluation_name or '')


def _role_evidence(signal):
    if not signal.known:
        return RoleEvidence(
            role=signal.role, dimension=signal.dimension, grade='UNKNOWN',
            origin='external-reference', evidence_ref='', observed_at=None,
            sample_size=0, source_class='UNKNOWN', note=signal.note)
    # sample_size is the number of published values (1), not a benchmark task
    # count: the provider publishes neither item counts nor confidence
    # intervals for these indices. The note says exactly that.
    return RoleEvidence(
        role=signal.role, dimension=signal.dimension, grade=signal.grade,
        origin='external-reference', evidence_ref=signal.evidence_ref,
        observed_at=signal.observed_at, sample_size=1,
        source_class='OBSERVED', note=signal.note)


def _resolve(batch, candidate_model_id, links):
    target = links.get(candidate_model_id)
    if target is None:
        return None, 'UNLINKED', (
            'no explicit model link; external identity is UNKNOWN (the lookup '
            'is exact and never inferred from a name)')
    snapshot = batch.model(target)
    if snapshot is None:
        return None, 'UNLINKED', (
            f'the linked snapshot {target!r} is not present in this batch; '
            'external evidence is UNKNOWN')
    if not batch.usable:
        return snapshot, 'STALE', (
            f'the external source is {batch.fetch.status}; the snapshot is '
            'shown but not used as current evidence')
    if snapshot.freshness != FRESHNESS_FRESH:
        return snapshot, 'STALE', (
            f'the external snapshot is {snapshot.freshness}; it is not used as '
            'current evidence')
    return snapshot, 'MATCHED', ''


def _assessment(snapshot, status, reason, candidate, role, signals,
                *, model_notes):
    return ModelAssessment(
        role=role, candidate_id=candidate.candidate_id,
        model_id=candidate.model_id,
        model_family=(snapshot.model_family if snapshot is not None else None),
        status=status, signals=signals,
        snapshot_id=(snapshot.canonical_id if snapshot is not None else None),
        snapshot_name=(snapshot.name if snapshot is not None else None),
        snapshot_slug=(snapshot.slug if snapshot is not None else None),
        freshness=(snapshot.freshness if snapshot is not None else 'UNKNOWN'),
        observed_at=(snapshot.fetched_at if snapshot is not None else None),
        limitations=(snapshot.limitations if snapshot is not None else ()),
        notes=tuple(dict.fromkeys(
            ([reason] if reason else []) + list(model_notes)
            + (list(snapshot.notes) if snapshot is not None else []))),
    )


def _enrich(discovery, batch, links, policy):
    """Attach role evidence to copies of the discovered candidates."""
    percentiles = _percentiles(batch)
    enriched = []
    assessments = []
    for candidate in discovery.candidates:
        snapshot, status, reason = _resolve(batch, candidate.model_id, links)
        model_notes = list(candidate.notes)
        if reason:
            model_notes.append(reason)
        if snapshot is not None:
            if snapshot.model_family is None:
                model_notes.append(
                    'model family is UNKNOWN in the source; family-level '
                    'independence is not claimed')
            if (snapshot.modalities is not None
                    and 'image' not in {item.casefold()
                                        for item in snapshot.modalities}):
                model_notes.append(
                    'the external source does not report image input; runtime '
                    'observations remain authoritative for the hard modality '
                    'filter')
        evidence = []
        for role in ROLES:
            signals = _signals_for(
                snapshot, role, percentiles, policy,
                usable=batch.usable and status == 'MATCHED')
            assessments.append(_assessment(
                snapshot, status, reason, candidate, role, signals,
                model_notes=model_notes))
            evidence.extend(_role_evidence(signal) for signal in signals)
        enriched.append(replace(
            candidate,
            role_evidence=tuple(candidate.role_evidence) + tuple(evidence),
            notes=tuple(dict.fromkeys(model_notes))))
    return tuple(enriched), tuple(assessments), percentiles


def _validate_master_requirement(requirements):
    """Master must demand text + image; a lenient caller is refused."""
    if not isinstance(requirements, Mapping):
        raise ModelSelectionError(
            'requirements must be a mapping of role -> RoleRequirement')
    master = requirements.get(ROLE_MASTER)
    if master is None or getattr(master, 'role', None) != ROLE_MASTER:
        raise ModelSelectionError(
            'a Master RoleRequirement is required; no lenient default is used')
    required = {str(item).casefold() for item in master.required_modalities}
    if not {'text', 'image'} <= required:
        raise ModelSelectionError(
            'the Master requirement must include text and image input; a '
            'text-only Master would drop the hard multi-modal constraint')


def _verify_selected_hard_filters(role_selection, *, now):
    """Defence in depth: re-run S2's own filter on each selected candidate.

    This calls ``roles.evaluate_candidate`` rather than restating a filter. It
    catches a future change that lets external evidence alter a hard fact: the
    selection is only valid while the same candidate still passes the same S2
    filters.
    """
    for role in ROLES:
        selection = role_selection.role(role)
        if selection.selected is None:
            continue
        check = evaluate_candidate(
            selection.selected.candidate, selection.requirement, now=now)
        if not check.passed:
            raise ModelSelectionError(
                f'{role}: the selected candidate no longer passes the S2 hard '
                'filters after external enrichment; external data must not '
                'change a hard constraint')


def _decision_time(now):
    if now is not None:
        return str(now)
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _identity_keys(candidate, now):
    """The model ids this candidate's registry identity covers.

    An untrusted declaration never broadens the match: only the id the
    candidate itself reports is used, so an alias cannot inherit another
    model's scores. A trusted declaration covers its canonical id and its
    confirmed aliases, which keeps two names for the same underlying model on
    one identity instead of inventing two models.
    """
    identity = candidate.canonical_identity
    model_id = candidate.model_id
    own = {model_id.casefold()} if model_id else set()
    if identity is None:
        return own, None, 'ABSENT', (
            'no canonical model identity is registered for this candidate')
    trusted, status, detail = identity.trusted_at(now)
    if not trusted:
        return own, None, status, detail
    return identity.alias_keys(model_id), identity.canonical_id, status, detail


def _match_cohort(report, role, candidate, category, now):
    """Find the one current-scope cohort for this exact role/category/identity.

    No cross-category and no cross-identity fallback is attempted. More than
    one matching cohort means the identity was recorded under several names;
    the safe answer is to apply no threshold action and say so.
    """
    if report is None:
        return None, 'NO_HISTORY', (
            'no local feedback report for the current scope was supplied; no '
            'local ETR or credibility is used')
    if not category:
        return None, 'NO_CATEGORY', (
            'no task category was supplied, so a per-category ETR cohort '
            'cannot be selected without crossing categories')
    cohorts = [item for item in report.cohorts
               if item.role == role and item.category == category]
    if not cohorts:
        return None, 'NO_COHORT', (
            f'this scope has no local cohort for {role}/{category}; a score '
            'is never inherited from another role or category')
    keys, _canonical, _status, _detail = _identity_keys(candidate, now)
    matches = [
        item for item in cohorts
        if item.identity.runtime_id.casefold() == candidate.runtime_id.casefold()
        and item.identity.runtime_version.casefold()
        == candidate.runtime_version.casefold()
        and item.identity.model_id.casefold() in keys]
    if not matches:
        return None, 'NO_COHORT', (
            f'this scope has no local cohort for the exact identity '
            f'{candidate.model_id!r} on {candidate.runtime_id} '
            f'{candidate.runtime_version}; scores are never inherited across '
            'identities')
    if len({item.identity.key for item in matches}) > 1:
        return None, 'AMBIGUOUS_IDENTITY', (
            'several local cohorts match this candidate under different '
            'recorded model ids; the selector does not guess whether they are '
            'the same model, so no ETR threshold action is applied')
    return matches[0], 'MATCHED', ''


def _credibility_for(role, candidate, category, peer_reviews, reliability,
                     now, scope_id=None):
    """Aggregate the peer-review credibility for one role/category/identity.

    Events are filtered by category and subject identity before assessment, so
    a score from another task category or another model is never inherited.
    Only every assessed fact being ``CORROBORATED`` yields ``CORROBORATED``;
    any conflict or low-credibility fact keeps the overall answer at or below
    the weaker status.
    """
    if not peer_reviews:
        return UNKNOWN, (
            'no peer-review events were supplied for the current scope; '
            'credibility is UNKNOWN and no threshold action is taken')
    from . import peer_review

    keys, _canonical, _status, _detail = _identity_keys(candidate, now)
    matching = []
    for event in peer_reviews:
        if getattr(event, 'subject_role', None) != role:
            continue
        if getattr(event, 'category', None) != category:
            continue
        event_scope = getattr(event, 'scope_id', '') or ''
        if event_scope and event_scope != scope_id:
            continue
        subject = getattr(event, 'subject', None)
        if subject is None:
            continue
        if subject.runtime_id.casefold() != candidate.runtime_id.casefold():
            continue
        if subject.runtime_version.casefold() \
                != candidate.runtime_version.casefold():
            continue
        if subject.model_id.casefold() not in keys:
            continue
        matching.append(event)
    if not matching:
        return UNKNOWN, (
            'no peer-review event matches this role, task category and exact '
            'identity; credibility is UNKNOWN, not inherited')
    assessments = peer_review.assess_all(matching, reliability=reliability)
    statuses = {item.credibility for item in assessments}
    basis = (
        f'{len(assessments)} assessed fact(s) from {len(matching)} event(s): '
        + ', '.join(sorted(statuses)))
    if 'CONFLICTED' in statuses:
        return 'CONFLICTED', basis
    if not statuses or statuses == {'UNKNOWN'}:
        return UNKNOWN, basis
    if 'LOW' in statuses:
        return 'LOW', basis
    if statuses == {'CORROBORATED'}:
        return 'CORROBORATED', basis
    return UNKNOWN, basis


def _local_evidence_for(role, candidate, report, category, peer_reviews,
                        reliability, now, hard_status, decided_at,
                        scope_id=None):
    """Build one candidate's local ETR / credibility view and its action."""
    keys, canonical, identity_status, identity_detail = _identity_keys(
        candidate, now)
    credibility, credibility_basis = _credibility_for(
        role, candidate, category, peer_reviews, reliability, now,
        scope_id=scope_id)
    base = dict(
        role=role, candidate_id=candidate.candidate_id,
        model_id=candidate.model_id, canonical_model_id=canonical,
        identity_status=identity_status, category=category,
        scope_id=(report.scope_id if report is not None else None),
        credibility=credibility, credibility_basis=credibility_basis,
        cost_per_mtok=candidate.cost_per_mtok,
        latency_ms=candidate.latency_ms, hard_filter_status=hard_status,
        decided_at=decided_at)
    notes = (
        'local evidence is read only from the current scope; it is never '
        'merged with another scope',
        'ETR is a percentage over observed Token in rated calls; token '
        'coverage is reported separately',
        'a provisional threshold action never deletes the candidate; the '
        'manual path stays open',
    )
    cohort, match_status, match_reason = _match_cohort(
        report, role, candidate, category, now)
    if cohort is None:
        reasons = [match_reason]
        if identity_detail and identity_status != 'CONFIRMED':
            reasons.append(f'identity {identity_status}: {identity_detail}')
        return LocalEvidence(
            status=match_status, reasons=tuple(reasons), notes=notes, **base)
    etr = cohort.etr
    if etr is None:
        return LocalEvidence(
            status='NO_DATA',
            reasons=('the local cohort has no ETR result; no threshold action '
                     'is possible',),
            notes=notes, **base)
    base.update(
        etr_value_status=etr.value_status, etr_value=etr.value,
        token_coverage_status=etr.token_coverage_status,
        token_coverage=etr.token_coverage,
        observed_tokens=etr.observed_tokens, scored_tokens=etr.scored_tokens,
        rated_tasks=etr.rated_tasks, total_tasks=etr.total_tasks,
        window_start=etr.window_start, window_end=etr.window_end,
        evidence_status=etr.evidence_status, gate_status=etr.gate_status)
    reasons = list(etr.gate_reasons)
    if etr.evidence_status != 'CURRENT':
        return LocalEvidence(
            status=etr.evidence_status,
            reasons=tuple(reasons + [
                f'the local evidence is {etr.evidence_status}; no threshold '
                'action is taken on stale or re-identified history']),
            notes=notes, **base)
    if etr.value_status != 'OK' or etr.token_coverage_status != 'OK':
        status = (etr.value_status if etr.value_status != 'OK'
                  else etr.token_coverage_status)
        return LocalEvidence(
            status=status,
            reasons=tuple(reasons + [
                'ETR or coverage has no usable value; it is not filled with 0 '
                'or 100 and no threshold action is taken']),
            notes=notes, **base)

    status = etr.gate_status if etr.gate_status != 'NO_GATE' else 'CURRENT'
    action = 'NONE'
    if hard_status != 'PASS':
        action = 'NOT_APPLICABLE'
        reasons.append(
            f'the existing hard filters are {hard_status}; local evidence '
            'cannot change a hard result and no threshold action is applied')
    elif role == ROLE_MASTER:
        action = 'MANUAL_ONLY'
        reasons.append(
            'Master is always selected and switched by the user; the local '
            'ETR is reported as a recommendation and is never auto-applied')
    elif etr.gate_status == 'PAUSE_AUTO_SELECTION':
        if credibility == 'CORROBORATED':
            action = 'PAUSE_AUTO_SELECTION'
            reasons.append(
                'the measured ETR is below the provisional low line with '
                'sufficient coverage, samples and corroborated evaluations; '
                'automatic selection for this role and category is paused')
        else:
            action = 'PROVISIONAL'
            reasons.append(
                f'credibility is {credibility}, not CORROBORATED, so a low '
                'ETR does not pause automatic selection; the value is reported '
                'as PROVISIONAL')
    elif etr.gate_status == 'RAISE_PRIORITY':
        quality_ok = any(metric.ok for metric in cohort.metrics.values())
        if credibility == 'CORROBORATED' and quality_ok:
            action = 'RAISE_PRIORITY'
            reasons.append(
                'the measured ETR is above the provisional high line with '
                'sufficient coverage, samples, corroborated evaluations and a '
                'locally observed quality metric; it only reorders candidates '
                'that already pass the hard filters')
        else:
            action = 'PROVISIONAL'
            reasons.append(
                'a high ETR does not raise priority unless the hard filters, '
                f'the local quality metric and credibility all qualify '
                f'(credibility={credibility}, quality_ok={quality_ok})')
    elif etr.gate_status == 'PROVISIONAL':
        action = 'PROVISIONAL'
        reasons.append(
            'the measured ETR is reported as PROVISIONAL; the sample, window '
            'or coverage threshold is not met, so it neither pauses nor '
            'raises automatic selection')
    return LocalEvidence(
        status=status, applied_action=action, reasons=tuple(reasons),
        notes=notes, **base)


def _local_policy(local_evidence):
    """Translate applied local actions into withheld / priority maps."""
    withheld, priority = {}, {}
    for item in local_evidence:
        if item.applied_action == 'PAUSE_AUTO_SELECTION':
            reason = (item.reasons[-1] if item.reasons else
                      'local ETR evidence paused automatic selection')
            withheld.setdefault(item.role, {})[item.candidate_id] = reason
        elif item.applied_action == 'RAISE_PRIORITY':
            priority[(item.role, item.candidate_id)] = (0,)
    return withheld, priority


def _source_degradation(batch):
    return (f'external model source {batch.fetch.status} '
            f'({batch.fetch.source_url}): {batch.fetch.message}')


def select_models(batch, discovery, requirements, *, links=None,
                  policy=None, now=None, feedback_report=None,
                  task_category=None, scope_id=None, peer_reviews=None,
                  peer_review_reliability=None):
    """Select roles from S2 hard filters, then external and local evidence.

    *batch* is a :class:`SnapshotBatch` (a failed fetch is allowed),
    *discovery* the S2 :class:`DiscoveryReport` and *requirements* one
    :class:`RoleRequirement` per role. *links* must be explicit
    :class:`ModelLink` values or a mapping; there is no fuzzy identity match.

    *feedback_report* is this scope's :class:`feedback.FeedbackReport` and
    *peer_reviews* are its :class:`peer_review.PeerReview` events. Both are
    optional: without them the selection behaves exactly as before. When a
    report is supplied, *scope_id* must name the same current scope; a report
    from another scope is never inherited. *task_category* selects one local
    cohort; without it no per-category ETR is applied.

    Both local sources are applied **after** every existing hard filter. They
    can withhold an already-qualified candidate from *automatic* selection or
    reorder already-qualified candidates; they can never turn a hard failure
    into a pass, and nothing here spawns or switches an agent.
    """
    if not isinstance(batch, SnapshotBatch):
        raise TypeError('batch must be a SnapshotBatch')
    from .discovery import DiscoveryReport

    if not isinstance(discovery, DiscoveryReport):
        raise TypeError('discovery must be a DiscoveryReport')
    policy = policy or AssessmentPolicy()
    if not isinstance(policy, AssessmentPolicy):
        raise TypeError('policy must be an AssessmentPolicy')
    _validate_master_requirement(requirements)
    link_map = _link_map(links)

    local_report = feedback_report
    scope_problem = ''
    if feedback_report is not None:
        from .feedback import FeedbackReport
        if not isinstance(feedback_report, FeedbackReport):
            raise TypeError('feedback_report must be a FeedbackReport')
        if not scope_id:
            scope_problem = (
                'a local feedback report was supplied without the current '
                'scope id; it is not used, because scores are never inherited '
                'across scopes')
        elif feedback_report.scope_id != scope_id:
            scope_problem = (
                f'local feedback belongs to scope {feedback_report.scope_id!r}, '
                f'not the current scope {scope_id!r}; it is not inherited')
        if scope_problem:
            local_report = None
    if peer_reviews is not None and not isinstance(peer_reviews, (tuple, list)):
        peer_reviews = tuple(peer_reviews)

    enriched, assessments, _ = _enrich(
        discovery, batch, link_map, policy)

    enriched_discovery = replace(discovery, candidates=enriched)
    decided_at = _decision_time(now)
    preliminary = select_roles(enriched_discovery, requirements, now=now)
    local_evidence = []
    for role in ROLES:
        requirement = preliminary.role(role).requirement
        for candidate in enriched:
            hard = evaluate_candidate(candidate, requirement, now=now)
            local_evidence.append(_local_evidence_for(
                role, candidate, local_report, task_category,
                peer_reviews or (), peer_review_reliability, now,
                hard.status, decided_at, scope_id=scope_id))
    withheld, priority = _local_policy(local_evidence)
    role_selection = select_roles(
        enriched_discovery, requirements, now=now,
        withheld_for_role=withheld, candidate_priority=priority)
    _verify_selected_hard_filters(role_selection, now=now)

    selected_ids = {}
    for role in ROLES:
        selection = role_selection.role(role)
        selected_ids[role] = (
            selection.selected.candidate.candidate_id
            if selection.selected is not None else None)

    marked = tuple(
        replace(item, selected=(selected_ids.get(item.role) == item.candidate_id))
        for item in assessments)

    degradations = list(role_selection.degradations)
    decisions = []
    if scope_problem:
        degradations.append(scope_problem)
    for item in local_evidence:
        if item.applied_action == 'PAUSE_AUTO_SELECTION':
            degradations.append(
                f'{item.role}: {item.candidate_id} is withheld from automatic '
                f'selection by local ETR evidence (ETR {item.etr_value}%, '
                f'coverage {item.token_coverage}%, credibility '
                f'{item.credibility}); the candidate and the manual path are '
                'preserved')
    for role in ROLES:
        selection = role_selection.role(role)
        if selection.selected is None:
            continue
        local = next(
            (item for item in local_evidence
             if item.role == role
             and item.candidate_id == selection.selected.candidate.candidate_id),
            None)
        if local is not None and local.status in (
                'NO_DATA', 'UNKNOWN', 'STALE', 'IDENTITY_CHANGED',
                'UNKNOWN_IDENTITY', 'AMBIGUOUS_IDENTITY'):
            degradations.append(
                f'{role}: selected {local.candidate_id} has local evidence '
                f'{local.status}; no local ETR threshold action was applied')
    if not batch.usable:
        degradations.append(_source_degradation(batch))
        if policy.require_fresh_source:
            decisions.append(
                f'policy requires fresh external model data, but the source '
                f'status is {batch.fetch.status}; choose a cached/offline '
                'policy or restore access before automatic selection')
    elif not batch.live:
        degradations.append(_source_degradation(batch))

    for role in ROLES:
        selection = role_selection.role(role)
        if selection.selected is None:
            continue
        assessment = next(
            (item for item in marked
             if item.role == role and item.selected), None)
        if assessment is None:  # pragma: no cover - structurally unreachable
            continue
        if assessment.status != 'MATCHED':
            degradations.append(
                f'{role}: selected {assessment.candidate_id} has external '
                f'status {assessment.status}; external evidence was not used')
            if policy.require_snapshot_for_selected:
                decisions.append(
                    f'{role}: the selected model {assessment.model_id!r} has no '
                    'fresh linked snapshot and policy requires one; record a '
                    'ModelLink or relax the policy')
        if policy.require_role_evidence and not assessment.has_role_evidence:
            decisions.append(
                f'{role}: policy requires role-specific external evidence, but '
                f'none is available for {assessment.model_id!r}; decide whether '
                'project evidence alone is sufficient')

    if decisions:
        degradations.append(
            'automatic model selection is withheld until the decision(s) below '
            'are resolved')

    notes = list(role_selection.notes)
    notes.extend(f'source warning: {warning}' for warning in batch.warnings)
    notes.extend((
        ('external model data is an optional reference: it never satisfies or '
         'relaxes a hard filter, and the S2 hard filters ran on the same '
         'candidate facts'),
        ('a source score is attributed to the index version and fetch time in '
         'the report; a cached or expired reading is never called live'),
        ('Reviewer dimensions are UNKNOWN unless a source publishes a '
         'code-review-specific benchmark; a general intelligence score is not '
         'used as a review score'),
        ('model family is UNKNOWN unless the source publishes it; Reviewer '
         'independence is enforced on the underlying model id'),
        ('local ETR and peer-review credibility come only from the current '
         'scope and the supplied task category; they are never inherited '
         'across scopes, categories, roles or model identities'),
        ('a local ETR above the high line only reorders candidates that '
         'already passed the hard filters and have a qualified quality metric; '
         'a local ETR below the low line pauses automatic selection only when '
         'the window, sample, coverage and credibility thresholds are met'),
        ('Master is always selected and switched by the user; local evidence '
         'about Master is a recommendation, never an automatic change'),
        (f'this selector does not spawn, wait for or collect an agent; '
         f'executed={False}'),
    ))
    for item in local_evidence:
        if item.applied_action == 'RAISE_PRIORITY':
            notes.append(
                f'{item.role}: {item.candidate_id} raised by a gated ETR '
                f'({item.etr_value}%, coverage {item.token_coverage}%) among '
                'hard-qualified candidates only')
    if batch.usable and not batch.live:
        notes.append(
            f'the report uses {batch.fetch.status} data; it is explicitly not '
            'a live / latest reading')

    return ModelSelectionReport(
        source=batch,
        role_selection=role_selection,
        assessments=marked,
        automatic_allowed=(role_selection.automatic_dispatch_allowed
                           and not decisions),
        degradations=tuple(dict.fromkeys(degradations)),
        decisions=tuple(dict.fromkeys(decisions)),
        notes=tuple(dict.fromkeys(notes)),
        executed=False,
        local_evidence=tuple(local_evidence),
        task_category=task_category,
        decision_time=decided_at,
    )
