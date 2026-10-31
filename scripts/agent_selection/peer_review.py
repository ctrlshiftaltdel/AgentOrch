#!/usr/bin/env python3
"""Traceable cross-role evaluation events and reviewer credibility (#156).

Design contract: ``docs/design/next-phase-governance-and-agent-performance.md``
§8. Master, Execution and Reviewer may score each other in the six directions,
but a score and its *credibility* are two separate facts:

* a score is exactly ``+1``, ``0``, ``-1`` or ``NOT_RATED``. ``0`` is an
  explicit neutral judgement (Just Average); ``NOT_RATED`` means "not enough
  basis" and never becomes ``0`` or a quality figure;
* credibility is ``LOW``, ``CORROBORATED``, ``CONFLICTED`` or ``UNKNOWN`` and
  is derived only from the recorded evidence, task version, evidence scope,
  blindness and the caller-verified canonical identities. Model confidence,
  fluent wording and a self-assessment are not inputs: there is no field for
  them and no code path that reads one;
* two agreeing evaluations are ``CORROBORATED`` only when they were completed
  independently, without seeing each other's conclusion, on the same task
  version and evidence scope, by different canonical model identities that the
  caller verified. An alias, a temperature, a prompt or a model-family claim is
  not identity evidence. Agreement raises credibility but the quality fact is
  counted once, and a disagreement stays ``CONFLICTED`` instead of being
  averaged into a neutral value;
* the module never reads a model registry or a runtime tool list to prove an
  identity. ``identity_verified`` is a caller declaration; without it an
  independence check is ``UNKNOWN``;
* a reviewer's missed-issue rate is computed only from an independent defect
  set (independent review, synthetic injection or later confirmed defects);
  without one it is ``UNKNOWN``, never ``0``;
* an evaluator's own reliability comes only from later evaluations that
  independent evidence confirmed or refuted, never from confidence or fluency;
* events live in the current scope's ``observed-performance.md`` through the S1
  :class:`scripts.agent_selection.local_store.LocalStore`, beside the existing
  role-performance records. There is no second database, no cross-scope read
  and no full prompt or raw conversation: a reason is a short, single-line
  summary and the store's privacy scan still applies;
* output is advisory. Nothing here changes a permission, a prompt, a model
  configuration or an ETR calculation, and a substantive disagreement about the
  Master needs independent evidence or a user decision.

Standard library only. No network, no third-party dependency, no real
``.local`` directory: tests always use a temporary project root.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import local_store
from .feedback import (
    FEEDBACK_DOCUMENT,
    PEER_REVIEW_SCHEMA,
    EvidenceRef,
    FeedbackError,
    ModelIdentity,
    UNKNOWN,
)
from .local_store import StoreError
from .roles import PROJECT_MIN_SAMPLES, ROLE_EXECUTION, ROLE_MASTER, ROLE_REVIEWER

__all__ = [
    'PEER_REVIEW_SCHEMA',
    'DIRECTIONS',
    'DIRECTION_ROLES',
    'DIRECTION_DIMENSIONS',
    'POSITIVE',
    'NEUTRAL',
    'NEGATIVE',
    'NOT_RATED',
    'SCORES',
    'SCORE_NUMBERS',
    'LOW',
    'CORROBORATED',
    'CONFLICTED',
    'CREDIBILITY_STATUSES',
    'CREDIBILITY_CONDITIONS',
    'INDEPENDENT',
    'NOT_INDEPENDENT',
    'INDEPENDENCE_STATUSES',
    'DEFECT_SOURCES',
    'PeerReviewError',
    'PeerReviewScopeError',
    'PeerReviewWriteError',
    'PeerReview',
    'PeerReviewLoad',
    'Independence',
    'Assessment',
    'ReviewConfirmation',
    'EvaluatorReliability',
    'DefectSet',
    'MissedIssueReport',
    'record_review',
    'load_reviews',
    'independence',
    'assess',
    'assess_all',
    'evaluator_reliability',
    'missed_issue_rate',
]

# The shared scope document is ``observed-performance.md``. Its schema marker
# distinguishes the two record kinds without a second store or file.
POSITIVE = '+1'
NEUTRAL = '0'
NEGATIVE = '-1'
NOT_RATED = 'NOT_RATED'
SCORES = (POSITIVE, NEUTRAL, NEGATIVE, NOT_RATED)
RATED_SCORES = (POSITIVE, NEUTRAL, NEGATIVE)
# ``NOT_RATED`` maps to ``None`` on purpose: it is not a zero.
SCORE_NUMBERS = {POSITIVE: 1, NEUTRAL: 0, NEGATIVE: -1, NOT_RATED: None}
_INTEGER_SCORES = {1: POSITIVE, 0: NEUTRAL, -1: NEGATIVE}

# The six directions of design §8.1. The role pair is derived from the
# direction, so an event cannot claim an inconsistent evaluator / subject role.
DIRECTION_ROLES = {
    'execution->master': (ROLE_EXECUTION, ROLE_MASTER),
    'reviewer->execution': (ROLE_REVIEWER, ROLE_EXECUTION),
    'execution->reviewer': (ROLE_EXECUTION, ROLE_REVIEWER),
    'master->execution': (ROLE_MASTER, ROLE_EXECUTION),
    'master->reviewer': (ROLE_MASTER, ROLE_REVIEWER),
    'reviewer->master': (ROLE_REVIEWER, ROLE_MASTER),
}
DIRECTIONS = tuple(DIRECTION_ROLES)

# Role-relevant dimensions only. A dimension from another direction is refused:
# Prompt length, token spend or finding count are not quality dimensions.
DIRECTION_DIMENSIONS = {
    'execution->master': ('task_packet_clarity', 'acceptance_testability',
                          'dependency_information', 'planning_rework'),
    'reviewer->execution': ('defect_confirmation', 'severity_accuracy',
                            'fix_quality', 'test_evidence'),
    'execution->reviewer': ('finding_reproducibility', 'false_positive_control',
                            'actionability', 'specificity'),
    'master->execution': ('goal_conformance', 'scope_discipline',
                          'first_acceptance', 'regression_defects'),
    'master->reviewer': ('true_positive_findings', 'false_positive_control',
                         'missed_issue_control', 'risk_coverage'),
    'reviewer->master': ('requirement_contradictions', 'acceptance_omissions',
                         'dependency_omissions', 'split_quality'),
}

LOW = 'LOW'
CORROBORATED = 'CORROBORATED'
CONFLICTED = 'CONFLICTED'
CREDIBILITY_STATUSES = (LOW, CORROBORATED, CONFLICTED, UNKNOWN)
# Every status states the evidence it needs, so a reader can check the claim
# instead of trusting a label.
CREDIBILITY_CONDITIONS = {
    LOW: 'one evaluator, or agreeing evaluations that are not independent, '
         'blind or identity-verified, or an evaluator whose past evaluations '
         'were independently refuted; the score is recorded and advisory',
    CORROBORATED: 'two or more independent evaluations by different '
                  'caller-verified canonical model identities, completed '
                  'blind, on the same task version and evidence scope, each '
                  'with a sufficient evidence reference, and agreeing',
    CONFLICTED: 'rated evaluations disagree; the conflict is preserved and '
                'never averaged into 0, and needs deterministic verification, '
                'an independent third review or a user decision',
    UNKNOWN: 'the canonical identity, task version, evidence scope or a '
             'sufficient evidence reference could not be confirmed, so there '
             'is no quality figure',
}

INDEPENDENT = 'INDEPENDENT'
NOT_INDEPENDENT = 'NOT_INDEPENDENT'
INDEPENDENCE_STATUSES = (INDEPENDENT, NOT_INDEPENDENT, UNKNOWN)

DEFECT_SOURCES = ('independent_review', 'synthetic_injection', 'later_confirmed')

_CATEGORY_RE = re.compile(r'[a-z0-9][a-z0-9._-]{0,31}')
_TOKEN_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/#@+-]{0,63}')
_REASON_LIMIT = 400
_LOCATOR_LIMIT = 200


class PeerReviewError(FeedbackError):
    """A peer-review event or assessment is not valid as supplied."""


class PeerReviewScopeError(PeerReviewError):
    """The event belongs to another scope and is never merged into this one."""


class PeerReviewWriteError(PeerReviewError):
    """The event was not written; the caller must not claim it was."""


# --- validation helpers ------------------------------------------------------
def _token(value, label):
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise PeerReviewError(
            f'{label} must be a short single token without spaces, pipes or '
            f'semicolons, got {value!r}')
    return value


def _category(value):
    if not isinstance(value, str) or not _CATEGORY_RE.fullmatch(value):
        raise PeerReviewError(
            'task_category must be a lowercase slug such as "code-change" or '
            f'"planning", got {value!r}')
    return value


def _locator(value, label, limit=_LOCATOR_LIMIT):
    if not isinstance(value, str) or not value.strip():
        raise PeerReviewError(f'{label} needs a locator')
    if ('|' in value or ';' in value or '\n' in value or '\r' in value
            or len(value) > limit):
        raise PeerReviewError(
            f'{label} must be single-line, <= {limit} characters and must not '
            f'contain "|" or ";"')
    return value


def _timestamp(value, label):
    if not isinstance(value, str):
        raise PeerReviewError(f'{label} must be a UTC timestamp string')
    try:
        return local_store.utc_timestamp(value)
    except ValueError as exc:
        raise PeerReviewError(
            f'{label} must look like 2030-01-01T00:00:00Z: {exc}') from exc


def _flag(value, label):
    if not isinstance(value, bool):
        raise PeerReviewError(f'{label} must be True or False, got {value!r}')
    return value


def _reason(value):
    if not isinstance(value, str) or not value.strip():
        raise PeerReviewError('a review event needs a short reason')
    if '\n' in value or '\r' in value:
        raise PeerReviewError(
            'the reason must be a single line; raw conversations and multi-line '
            'prompts are not stored')
    if len(value) > _REASON_LIMIT:
        raise PeerReviewError(
            f'the reason must be at most {_REASON_LIMIT} characters; only a '
            'short justification is stored, never a full prompt or transcript')
    return value.strip()


def _score(value):
    if isinstance(value, bool):
        raise PeerReviewError(f'got {value!r}; use one of {SCORES}')
    if isinstance(value, int) and value in _INTEGER_SCORES:
        return _INTEGER_SCORES[value]
    if value in SCORES:
        return value
    raise PeerReviewError(
        f'got {value!r}; a score is exactly one of {SCORES}, and NOT_RATED is '
        'not a zero')


# --- the event ---------------------------------------------------------------
@dataclass(frozen=True)
class PeerReview:
    """One recorded evaluation of one role's work in one direction.

    ``identity_verified`` is the caller's declaration that both canonical model
    identities were checked through an independent source. This module does not
    read a registry, a runtime tool list or a model-family self-description, so
    an unverified event can never become independent.
    """

    direction: str
    task_id: str
    category: str
    dimension: str
    score: str
    evaluator: ModelIdentity
    subject: ModelIdentity
    observed_at: str
    evidence: tuple = ()
    reason: str = ''
    task_version: str = UNKNOWN
    evidence_scope: str = UNKNOWN
    evaluator_alias: str = UNKNOWN
    subject_alias: str = UNKNOWN
    config_id: str = UNKNOWN
    identity_verified: bool = False
    blind: bool = False
    scope_id: str = ''

    def __post_init__(self):
        if self.direction not in DIRECTION_ROLES:
            raise PeerReviewError(
                f'unknown direction {self.direction!r}; expected one of '
                f'{DIRECTIONS}')
        object.__setattr__(self, 'task_id', _token(self.task_id, 'task_id'))
        object.__setattr__(self, 'category', _category(self.category))
        if self.dimension not in DIRECTION_DIMENSIONS[self.direction]:
            raise PeerReviewError(
                f'dimension {self.dimension!r} does not belong to '
                f'{self.direction}; expected one of '
                f'{DIRECTION_DIMENSIONS[self.direction]}')
        object.__setattr__(self, 'score', _score(self.score))
        for label, identity in (('evaluator', self.evaluator),
                                ('subject', self.subject)):
            if not isinstance(identity, ModelIdentity):
                raise PeerReviewError(f'{label} must be a ModelIdentity')
        object.__setattr__(self, 'observed_at',
                           _timestamp(self.observed_at, 'observed_at'))
        for item in self.evidence:
            if not isinstance(item, EvidenceRef):
                raise PeerReviewError(
                    f'evidence must be EvidenceRef items, got {item!r}')
        object.__setattr__(self, 'evidence', tuple(self.evidence))
        object.__setattr__(self, 'reason', _reason(self.reason))
        for name in ('task_version', 'evidence_scope', 'evaluator_alias',
                     'subject_alias', 'config_id'):
            object.__setattr__(self, name, _token(getattr(self, name), name))
        object.__setattr__(self, 'identity_verified',
                           _flag(self.identity_verified, 'identity_verified'))
        object.__setattr__(self, 'blind', _flag(self.blind, 'blind'))
        if self.scope_id and not isinstance(self.scope_id, str):
            raise PeerReviewError('scope_id must be a string or empty')

    @property
    def evaluator_role(self):
        return DIRECTION_ROLES[self.direction][0]

    @property
    def subject_role(self):
        return DIRECTION_ROLES[self.direction][1]

    @property
    def fact_key(self):
        """The one quality fact this evaluation speaks about.

        Corroborating evaluations share this key; ``assess_all`` therefore
        counts the fact once no matter how many evaluations agree.
        """
        return (self.direction, self.task_id, self.task_version,
                self.dimension, self.subject.key)

    @property
    def has_sufficient_evidence(self):
        return any(item.sufficient for item in self.evidence)

    @property
    def summary(self):
        return (f'peer review {self.direction} {self.task_id} '
                f'{self.dimension} score={self.score}')

    def as_details(self):
        value = SCORE_NUMBERS[self.score]
        details = {
            'schema': PEER_REVIEW_SCHEMA,
            'kind': 'peer-review',
            'direction': self.direction,
            'evaluator_role': self.evaluator_role,
            'subject_role': self.subject_role,
            'task_id': self.task_id,
            'task_category': self.category,
            'task_version': self.task_version,
            'dimension': self.dimension,
            'score': self.score,
            'score_value': UNKNOWN if value is None else str(value),
            'evaluator_model_id': self.evaluator.model_id,
            'evaluator_runtime_id': self.evaluator.runtime_id,
            'evaluator_runtime_version': self.evaluator.runtime_version,
            'evaluator_alias': self.evaluator_alias,
            'subject_model_id': self.subject.model_id,
            'subject_runtime_id': self.subject.runtime_id,
            'subject_runtime_version': self.subject.runtime_version,
            'subject_alias': self.subject_alias,
            'config_id': self.config_id,
            'identity_verified': 'true' if self.identity_verified else 'false',
            'blind': 'true' if self.blind else 'false',
            'evidence_scope': self.evidence_scope,
            'observed_at': self.observed_at,
            'reason': self.reason,
            'evidence_count': str(len(self.evidence)),
        }
        for index, item in enumerate(self.evidence, start=1):
            details[f'evidence_{index}'] = item.encode()
        return details


@dataclass(frozen=True)
class PeerReviewLoad:
    scope_id: str
    reviews: tuple = ()
    diagnostics: tuple = ()
    ignored: int = 0

    @property
    def ok(self):
        return not self.diagnostics


@dataclass(frozen=True)
class Independence:
    status: str
    reasons: tuple = ()

    def as_dict(self):
        return {'status': self.status, 'reasons': list(self.reasons)}


def _events(reviews):
    items = tuple(reviews)
    if not items:
        raise PeerReviewError('at least one peer-review event is required')
    for item in items:
        if not isinstance(item, PeerReview):
            raise PeerReviewError(
                f'expected PeerReview events, got {type(item).__name__}')
    return items


# --- persistence -------------------------------------------------------------
def record_review(store, review, *, now=None, record_id=None):
    """Append one event to this scope's ``observed-performance.md``.

    A failed write raises :class:`PeerReviewWriteError`: the caller must report
    the incomplete audit trail instead of claiming the event was stored.
    """
    if not isinstance(review, PeerReview):
        raise PeerReviewError(f'unknown event type {type(review).__name__}')
    if review.scope_id and review.scope_id != store.scope_id:
        raise PeerReviewScopeError(
            'refusing a cross-scope record: the evaluation belongs to another '
            'scope; peer reviews are never merged or inherited across scopes')
    try:
        return store.append_record(
            FEEDBACK_DOCUMENT, summary=review.summary,
            source_class='OBSERVED', status='RECORDED',
            details=review.as_details(), record_id=record_id, now=now)
    except StoreError as exc:
        raise PeerReviewWriteError(
            f'peer review {review.direction}/{review.task_id} was not written: '
            f'{exc}') from exc


def _parse_flag(value, label):
    if value == 'true':
        return True
    if value == 'false':
        return False
    raise PeerReviewError(f'{label} must be true or false, got {value!r}')


def _parse_event(record):
    details = dict(record.details)
    if details.get('schema') != PEER_REVIEW_SCHEMA:
        raise PeerReviewError('not a peer-review record')
    raw_count = details.get('evidence_count', '0')
    if not isinstance(raw_count, str) or not re.fullmatch(r'[0-9]+', raw_count):
        raise PeerReviewError(f'evidence_count is not a non-negative integer: '
                              f'{raw_count!r}')
    evidence = []
    for index in range(1, int(raw_count) + 1):
        key = f'evidence_{index}'
        if key not in details:
            raise PeerReviewError(f'missing {key}')
        evidence.append(EvidenceRef.decode(details[key]))
    return PeerReview(
        direction=details.get('direction', ''),
        task_id=details.get('task_id', ''),
        category=details.get('task_category', ''),
        task_version=details.get('task_version', UNKNOWN),
        dimension=details.get('dimension', ''),
        score=details.get('score', ''),
        evaluator=ModelIdentity(
            model_id=details.get('evaluator_model_id', UNKNOWN),
            runtime_id=details.get('evaluator_runtime_id', UNKNOWN),
            runtime_version=details.get('evaluator_runtime_version', UNKNOWN)),
        evaluator_alias=details.get('evaluator_alias', UNKNOWN),
        subject=ModelIdentity(
            model_id=details.get('subject_model_id', UNKNOWN),
            runtime_id=details.get('subject_runtime_id', UNKNOWN),
            runtime_version=details.get('subject_runtime_version', UNKNOWN)),
        subject_alias=details.get('subject_alias', UNKNOWN),
        observed_at=details.get('observed_at', ''),
        evidence=tuple(evidence),
        reason=details.get('reason', ''),
        evidence_scope=details.get('evidence_scope', UNKNOWN),
        config_id=details.get('config_id', UNKNOWN),
        identity_verified=_parse_flag(
            details.get('identity_verified'), 'identity_verified'),
        blind=_parse_flag(details.get('blind'), 'blind'),
    )


def load_reviews(store):
    """Read this scope's peer-review events; never reads another scope.

    Role-performance records share the same document and are skipped silently:
    they are a different, known record kind, not a broken schema.
    """
    diagnostics = []
    state = store.read_document(FEEDBACK_DOCUMENT)
    if not state.exists:
        return PeerReviewLoad(
            scope_id=store.scope_id,
            diagnostics=(f'{FEEDBACK_DOCUMENT}: document does not exist '
                         '(not a PASS)',))
    if state.issues:
        return PeerReviewLoad(scope_id=store.scope_id,
                              diagnostics=tuple(state.issues))
    reviews, ignored = [], 0
    for record in state.records:
        if record.details.get('schema') != PEER_REVIEW_SCHEMA:
            ignored += 1
            continue
        if record.status == 'SUPERSEDED':
            ignored += 1
            continue
        if record.source_class != 'OBSERVED':
            diagnostics.append(
                f'{record.record_id}: source_class {record.source_class!r} is '
                'not OBSERVED; excluded from peer review')
            ignored += 1
            continue
        try:
            reviews.append(_parse_event(record))
        except FeedbackError as exc:
            diagnostics.append(f'{record.record_id}: {exc}')
            ignored += 1
    return PeerReviewLoad(scope_id=store.scope_id, reviews=tuple(reviews),
                          diagnostics=tuple(diagnostics), ignored=ignored)


# --- independence ------------------------------------------------------------
def independence(reviews):
    """Whether these evaluations were produced independently of each other.

    ``INDEPENDENT`` needs more than different names: different caller-verified
    canonical model identities, a blind process, the same task version and the
    same evidence scope, no self-assessment, and at least two evaluations.
    Anything that cannot be confirmed is ``UNKNOWN``, never a silent pass.
    """
    items = _events(reviews)
    if len(items) < 2:
        return Independence(
            UNKNOWN, ('a single evaluation cannot establish independence',))

    if any(not item.evaluator.known or not item.subject.known
           for item in items):
        return Independence(
            UNKNOWN,
            ('a canonical model identity is UNKNOWN; an alias, a temperature, '
             'a prompt or a model-family self-description is not identity '
             'evidence',))
    if any(not item.identity_verified for item in items):
        return Independence(
            UNKNOWN,
            ('the caller did not verify the canonical identities; this module '
             'never reads a registry to prove one',))
    if any(item.task_version == UNKNOWN for item in items):
        return Independence(UNKNOWN, ('the task version is UNKNOWN',))
    if any(item.evidence_scope == UNKNOWN for item in items):
        return Independence(UNKNOWN, ('the evidence scope is UNKNOWN',))

    negatives = []
    if any(not item.blind for item in items):
        negatives.append(
            'at least one evaluator saw the other conclusion (not blind)')
    if any(item.evaluator.key == item.subject.key for item in items):
        negatives.append('an evaluator assessed itself')
    evaluator_keys = [item.evaluator.key for item in items]
    if len(set(evaluator_keys)) != len(evaluator_keys):
        negatives.append(
            'the same canonical model evaluated twice; aliases, temperature, '
            'prompt and family claims are not independence')
    if len({item.task_version for item in items}) != 1:
        negatives.append('the evaluations target different task versions')
    if len({item.evidence_scope for item in items}) != 1:
        negatives.append('the evaluations use different evidence scopes')
    if len({item.subject.key for item in items}) != 1:
        negatives.append('the evaluations assess different subjects')
    if len({item.direction for item in items}) != 1:
        negatives.append('the evaluations use different directions')
    if negatives:
        return Independence(NOT_INDEPENDENT, tuple(negatives))
    return Independence(
        INDEPENDENT,
        ('different caller-verified canonical identities, blind, same task '
         'version and evidence scope',))


# --- credibility -------------------------------------------------------------
@dataclass(frozen=True)
class Assessment:
    """The credibility and the single quality contribution of one fact."""

    fact_key: tuple
    direction: str
    task_id: str
    task_version: str
    dimension: str
    subject_key: str
    credibility: str
    independence: str
    quality_score: object
    counted: int
    evaluations: int
    raw_scores: tuple
    self_assessed: bool
    requires_human: bool
    reasons: tuple
    evidence_condition: str

    def as_dict(self):
        return {
            'fact_key': list(self.fact_key),
            'direction': self.direction,
            'task_id': self.task_id,
            'task_version': self.task_version,
            'dimension': self.dimension,
            'subject': self.subject_key,
            'credibility': self.credibility,
            'independence': self.independence,
            'quality_score': self.quality_score,
            'counted': self.counted,
            'evaluations': self.evaluations,
            'raw_scores': list(self.raw_scores),
            'self_assessed': self.self_assessed,
            'requires_human': self.requires_human,
            'reasons': list(self.reasons),
            'evidence_condition': self.evidence_condition,
        }


def assess(reviews, *, reliability=None):
    """Assess one quality fact: credibility first, then at most one score.

    ``reliability`` is the mapping from :func:`evaluator_reliability`; it is the
    only route by which an evaluator's own accuracy can lower credibility.
    """
    items = _events(reviews)
    if len({item.fact_key for item in items}) != 1:
        raise PeerReviewError(
            'the events belong to different quality facts; group them with '
            'fact_key first')
    fact = items[0]
    rated = tuple(item for item in items if item.score in RATED_SCORES)
    admissible = tuple(item for item in rated
                       if item.has_sufficient_evidence)
    # Independence is about the evaluations that can carry a score; a
    # ``NOT_RATED`` or evidence-less event never corroborates and never blocks
    # the corroboration of two admissible evaluations.
    if admissible:
        independence_result = independence(admissible)
    else:
        independence_result = Independence(
            UNKNOWN, ('no admissible evaluation; independence is not '
                      'established',))
    self_assessed = any(item.evaluator.key == item.subject.key
                        for item in items)
    reasons = list(independence_result.reasons)
    raw_scores = tuple(item.score for item in items)
    requires_human = fact.subject_role == ROLE_MASTER

    if len({item.score for item in rated}) > 1:
        credibility = CONFLICTED
        quality_score, counted = None, 0
        reasons.append('the rated scores disagree; the conflict is kept and is '
                       'not averaged into a neutral 0')
        if fact.subject_role == ROLE_MASTER or self_assessed:
            requires_human = True
            reasons.append('a substantive dispute about the Master cannot be '
                           'closed by the Master alone; independent evidence or '
                           'a user decision is required')
    elif not admissible:
        credibility = UNKNOWN
        quality_score, counted = None, 0
        if any(item.score == NOT_RATED for item in items):
            reasons.append('NOT_RATED is not a score and does not enter the '
                           'quality figure')
        else:
            reasons.append('no rated evaluation carries a sufficient evidence '
                           'reference')
    elif any(not item.identity_verified or not item.evaluator.known
             or not item.subject.known for item in admissible):
        credibility = UNKNOWN
        quality_score, counted = None, 0
        reasons.append('the canonical evaluator or subject identity was not '
                       'verified; family self-description is not identity')
    elif len(admissible) == 1:
        credibility = LOW
        quality_score, counted = admissible[0].score, 1
        reasons.append('a single evaluation cannot be corroborated')
    elif independence_result.status == UNKNOWN:
        credibility = UNKNOWN
        quality_score, counted = None, 0
    elif independence_result.status == NOT_INDEPENDENT:
        credibility = LOW
        quality_score, counted = admissible[0].score, 1
    else:
        weak = [item for item in admissible
                if reliability is not None
                and reliability.get(item.evaluator.key) is not None
                and reliability[item.evaluator.key].status == LOW]
        if weak:
            credibility = LOW
            quality_score, counted = admissible[0].score, 1
            reasons.append('an evaluator\'s past evaluations were independently '
                           'refuted (reliability LOW); agreement is not '
                           'corroboration')
        else:
            credibility = CORROBORATED
            quality_score, counted = admissible[0].score, 1
            reasons.append('independent, blind, identity-verified evaluations '
                           'agree on this fact; it is counted once')

    return Assessment(
        fact_key=fact.fact_key, direction=fact.direction,
        task_id=fact.task_id, task_version=fact.task_version,
        dimension=fact.dimension, subject_key=fact.subject.key,
        credibility=credibility, independence=independence_result.status,
        quality_score=quality_score, counted=counted, evaluations=len(items),
        raw_scores=raw_scores, self_assessed=self_assessed,
        requires_human=requires_human, reasons=tuple(reasons),
        evidence_condition=CREDIBILITY_CONDITIONS[credibility])


def assess_all(reviews, *, reliability=None):
    """Group by quality fact and assess each once (no double counting)."""
    items = _events(reviews)
    groups = {}
    for item in items:
        groups.setdefault(item.fact_key, []).append(item)
    return tuple(assess(group, reliability=reliability)
                 for group in groups.values())


# --- evaluator reliability ---------------------------------------------------
@dataclass(frozen=True)
class ReviewConfirmation:
    """A later, independently evidenced check of one earlier evaluation.

    ``evidence`` must contain at least one sufficient evidence kind; a model's
    own confidence, fluency or self-rating is not accepted (and has no field).
    """

    evaluator: ModelIdentity
    evaluation_ref: str
    outcome: str
    evidence: tuple
    observed_at: str

    def __post_init__(self):
        if not isinstance(self.evaluator, ModelIdentity):
            raise PeerReviewError('evaluator must be a ModelIdentity')
        object.__setattr__(self, 'evaluation_ref',
                           _locator(self.evaluation_ref, 'evaluation_ref'))
        if self.outcome not in ('CONFIRMED', 'REFUTED'):
            raise PeerReviewError(
                f'outcome must be CONFIRMED or REFUTED, got {self.outcome!r}')
        for item in self.evidence:
            if not isinstance(item, EvidenceRef):
                raise PeerReviewError(
                    f'evidence must be EvidenceRef items, got {item!r}')
        object.__setattr__(self, 'evidence', tuple(self.evidence))
        if not any(item.sufficient for item in self.evidence):
            raise PeerReviewError(
                'a confirmation needs at least one sufficient evidence '
                'reference; a self-assessment is not independent confirmation')
        object.__setattr__(self, 'observed_at',
                           _timestamp(self.observed_at, 'observed_at'))


@dataclass(frozen=True)
class EvaluatorReliability:
    identity_key: str
    confirmed: int
    refuted: int
    sample_size: int
    status: str
    note: str

    def as_dict(self):
        return {'identity_key': self.identity_key, 'confirmed': self.confirmed,
                'refuted': self.refuted, 'sample_size': self.sample_size,
                'status': self.status, 'note': self.note}


def evaluator_reliability(confirmations):
    """Calibrate evaluators from independently confirmed outcomes only."""
    groups = {}
    for item in confirmations:
        if not isinstance(item, ReviewConfirmation):
            raise PeerReviewError(
                f'expected ReviewConfirmation items, got {type(item).__name__}')
        groups.setdefault(item.evaluator.key, []).append(item)
    result = {}
    for key, items in groups.items():
        sample = len(items)
        confirmed = sum(1 for item in items if item.outcome == 'CONFIRMED')
        refuted = sample - confirmed
        if sample < PROJECT_MIN_SAMPLES:
            status = UNKNOWN
            note = (f'fewer than {PROJECT_MIN_SAMPLES} independently confirmed '
                    'outcomes; no reliability claim')
        elif confirmed / sample < 0.5:
            status = LOW
            note = 'most past evaluations were refuted by independent evidence'
        else:
            status = 'OK'
            note = 'most past evaluations were confirmed by independent evidence'
        result[key] = EvaluatorReliability(
            identity_key=key, confirmed=confirmed, refuted=refuted,
            sample_size=sample, status=status, note=note)
    return result


# --- reviewer missed-issue rate ----------------------------------------------
@dataclass(frozen=True)
class DefectSet:
    """A defect list that exists independently of the review under test."""

    label: str
    refs: tuple
    evidence: tuple
    source: str

    def __post_init__(self):
        object.__setattr__(self, 'label', _token(self.label, 'label'))
        if self.source not in DEFECT_SOURCES:
            raise PeerReviewError(
                f'source must be one of {DEFECT_SOURCES}, got {self.source!r}')
        if not isinstance(self.refs, (tuple, list)) or not self.refs:
            raise PeerReviewError('a defect set needs at least one defect ref')
        refs = tuple(_locator(item, 'defect ref') for item in self.refs)
        if len(set(refs)) != len(refs):
            raise PeerReviewError('a defect set must not repeat a ref')
        object.__setattr__(self, 'refs', refs)
        for item in self.evidence:
            if not isinstance(item, EvidenceRef):
                raise PeerReviewError(
                    f'evidence must be EvidenceRef items, got {item!r}')
        object.__setattr__(self, 'evidence', tuple(self.evidence))
        if not any(item.sufficient for item in self.evidence):
            raise PeerReviewError(
                'a defect set needs at least one sufficient evidence reference')


@dataclass(frozen=True)
class MissedIssueReport:
    status: str
    value: object
    missed: object
    total: object
    note: str

    @property
    def computed(self):
        return self.status == 'OK'

    def as_dict(self):
        return {'status': self.status, 'value': self.value,
                'missed': self.missed, 'total': self.total,
                'computed': self.computed, 'note': self.note}


def missed_issue_rate(reported_refs, defect_set=None):
    """Count defects an independent set contains but the review did not report.

    Without a defect set the rate is ``UNKNOWN``: a review's own list is not a
    comparator, and no number is invented.
    """
    if defect_set is None:
        return MissedIssueReport(
            status=UNKNOWN, value=None, missed=None, total=None,
            note='no independent defect set, synthetic injection or later '
                 'confirmed defects: the missed-issue rate is not computed')
    if not isinstance(defect_set, DefectSet):
        raise PeerReviewError('defect_set must be a DefectSet or None')
    if not isinstance(reported_refs, (tuple, list, set, frozenset)):
        raise PeerReviewError('reported_refs must be an iterable of refs')
    reported = set()
    for item in reported_refs:
        reported.add(_locator(item, 'reported defect ref'))
    total = len(defect_set.refs)
    missed = len([item for item in defect_set.refs if item not in reported])
    return MissedIssueReport(
        status='OK', value=round(missed / total, 3), missed=missed,
        total=total,
        note=(f'compared with defect set {defect_set.label} '
              f'({defect_set.source}); {missed} of {total} independently known '
              'defects were not reported'))
