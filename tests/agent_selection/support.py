"""Shared fixed fixtures for the S2 role-discovery tests (#121).

Every fixture is synthetic: no real model API, no network, no personal
terminal data and no real ``.local`` directory. Callers pass ``now`` explicitly
so freshness decisions are deterministic.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent_selection import discovery, roles, runtime_adapter  # noqa: E402

NOW = '2026-10-27T12:00:00Z'
PROBE = '2026-10-26T12:00:00Z'
OLD_PROBE = '2026-07-01T00:00:00Z'
# A verification window that is still open at NOW, and one that closed before it.
VERIFIED_UNTIL = '2027-01-01T00:00:00Z'
EXPIRED_UNTIL = '2026-10-01T00:00:00Z'

PASS_VERDICTS = ('C1', 'C2', 'C3', 'C4')


def identity(canonical_id, *, aliases=(), status='CONFIRMED',
             verified_until=VERIFIED_UNTIL,
             evidence_ref='fixture:model-identity-registry'):
    """A canonical identity declaration, as the scope registry would carry it."""
    return runtime_adapter.CanonicalIdentity(
        canonical_id=canonical_id, status=status, aliases=tuple(aliases),
        evidence_ref=evidence_ref, verified_until=verified_until,
        detail='fixture identity declaration')


def absent_identity(detail='no canonical model identity was registered'):
    return runtime_adapter.CanonicalIdentity(status='ABSENT', detail=detail)


def capability(capability_id='C1', verdict='PASS', *,
               source_class='OBSERVED', probe_date=PROBE,
               evidence_ref=None, limits=''):
    if evidence_ref is None:
        evidence_ref = f'evidence:{capability_id}' if verdict == 'PASS' else ''
    return runtime_adapter.Capability(
        capability_id=capability_id, verdict=verdict,
        source_class=source_class, probe_date=probe_date,
        evidence_ref=evidence_ref, limits=limits)


def capability_record(*, runtime_id='fixture-runtime',
                      runtime_version='1.0.0', status='COMPLETE',
                      probe_date=PROBE, overrides=None):
    """C1-C4 PASS by default; C5-C8 remain UNKNOWN as in a partial record."""
    overrides = dict(overrides or {})
    rows = []
    for capability_id in PASS_VERDICTS:
        rows.append(overrides.pop(capability_id, capability(capability_id)))
    for capability_id in ('C5', 'C6', 'C7', 'C8'):
        rows.append(overrides.pop(
            capability_id, capability(capability_id, 'UNKNOWN', probe_date=None)))
    if overrides:
        raise AssertionError(f'unused overrides: {sorted(overrides)}')
    return runtime_adapter.CapabilityRecord(
        runtime_id=runtime_id, runtime_version=runtime_version,
        edition='github', execution_mode='spawned-subagent', status=status,
        probe_date=probe_date, evidence_ref='fixture:capability-record',
        capabilities=tuple(rows))


def permissions(*, read_paths=('src',), write_paths=(), commands=(),
                network=False, network_observed=None, read_only=False):
    return runtime_adapter.PermissionScope(
        read_paths=tuple(read_paths), write_paths=tuple(write_paths),
        commands=tuple(commands), network=network,
        network_observed=network_observed, read_only=read_only)


def evidence(role, dimension, grade='STRONG', *, origin='project', sample=5,
             observed_at=NOW, evidence_ref=None, source_class='OBSERVED'):
    return runtime_adapter.RoleEvidence(
        role=role, dimension=dimension, grade=grade, origin=origin,
        evidence_ref=evidence_ref or f'fixture:{role}:{dimension}',
        observed_at=observed_at, sample_size=sample,
        source_class=source_class)


def candidate(agent_id='agent-1', *, model_id='model-a', model_family='family-a',
              runtime_id='fixture-runtime', runtime_version='1.0.0',
              modalities=('text',),
              tools=('file-read', 'file-write', 'command'),
              permission=None, capability_record_value='default',
              source='runtime-discovery', source_class='OBSERVED',
              evidence_time=NOW, context_tokens=200000,
              cost_per_mtok=1.0, latency_ms=1000, role_evidence=(),
              canonical_identity='fixture'):
    """One synthetic candidate.

    ``canonical_identity='fixture'`` declares the same shape the scope registry
    would carry: a verified mapping from this ``model_id`` to itself, because
    most fixtures are about other hard filters. Pass
    ``canonical_identity=None`` for "nothing was registered" or an explicit
    :func:`identity` / :func:`absent_identity` value to exercise the #170 paths.
    """
    if canonical_identity == 'fixture':
        canonical_identity = identity(model_id) if model_id is not None else None
    if capability_record_value == 'default':
        capability_record_value = capability_record(
            runtime_id=runtime_id, runtime_version=runtime_version)
    if permission is None:
        permission = permissions(write_paths=('src',),
                                 commands=('python',))
    return runtime_adapter.AgentCandidate(
        runtime_id=runtime_id, runtime_version=runtime_version,
        agent_id=agent_id, model_id=model_id, model_family=model_family,
        modalities=tuple(modalities), tools=tuple(tools),
        permissions=permission, capability_record=capability_record_value,
        source=source, source_class=source_class, evidence_time=evidence_time,
        context_tokens=context_tokens, cost_per_mtok=cost_per_mtok,
        latency_ms=latency_ms, role_evidence=tuple(role_evidence),
        canonical_identity=canonical_identity)


class FixtureAdapter(runtime_adapter.RuntimeAdapter):
    """A runtime adapter whose answer is fixed by the test."""

    def __init__(self, result, *, runtime_id='fixture-runtime',
                 runtime_version='1.0.0', can_enumerate=True):
        self._result = result
        self._description = runtime_adapter.RuntimeDescription(
            runtime_id=runtime_id, runtime_version=runtime_version,
            edition='github', execution_mode='spawned-subagent',
            adapter_id='fixture', can_enumerate_agents=can_enumerate)

    def describe(self):
        return self._description

    def discover(self):
        return self._result


def ok_result(candidates):
    return runtime_adapter.DiscoveryResult(
        outcome='OK', mode='runtime', candidates=tuple(candidates))


def task_requirements(*, execution_model_id='model-a',
                      execution_family='family-a'):
    return {
        roles.ROLE_MASTER: roles.master_requirement(),
        roles.ROLE_EXECUTION: roles.execution_requirement(
            read_paths=('src',), write_paths=('src',)),
        roles.ROLE_REVIEWER: roles.reviewer_requirement(
            execution_model_id=execution_model_id,
            execution_model_family=execution_family,
            read_paths=('src',)),
    }


def standard_candidates():
    """One candidate per role that satisfies the default task requirements."""
    master = candidate(
        'master-1', model_id='model-master', model_family='family-master',
        modalities=('text', 'image'),
        tools=('file-read',), permission=permissions(),
        capability_record_value=None, context_tokens=400000,
        role_evidence=(evidence('master', 'planning'),
                       evidence('master', 'quality-judgement')))
    execution = candidate(
        'exec-1', model_id='model-a', model_family='family-a',
        modalities=('text',),
        role_evidence=(evidence('execution', 'coding'),
                       evidence('execution', 'delivery-efficiency',
                                'MODERATE', sample=4),
                       evidence('execution', 'tool-use', 'MODERATE', sample=3)))
    reviewer = candidate(
        'review-1', model_id='model-b', model_family='family-b',
        modalities=('text',), tools=('file-read',),
        permission=permissions(read_only=True),
        role_evidence=(evidence('reviewer', 'defect-detection'),
                       evidence('reviewer', 'test-design', 'MODERATE', sample=4),
                       evidence('reviewer', 'security-review', 'MODERATE',
                                sample=3)))
    return master, execution, reviewer


def report_with(candidates, requirements=None, *, adapter=None):
    adapter = adapter or FixtureAdapter(ok_result(candidates))
    discovered = discovery.discover_candidates(adapter=adapter)
    return roles.select_roles(discovered,
                              requirements or task_requirements(), now=NOW)
