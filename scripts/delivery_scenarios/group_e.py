"""Group E - agent-role selection scenarios (E1-E13).

Scope and local records, discovery, role matching, optional model data and
local feedback, composed from the real S1-S4 modules on synthetic facts. A
selection is never read as a dispatch, and no scenario spawns an agent or
calls a model API.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest import mock

from .common import (
    ROOT,
    ScenarioFailed,
    _require,
)


# --------------------------------------------------------------------------- #
# Agent-role selection fixtures (Phase D S5)
# --------------------------------------------------------------------------- #
# The scenarios compose the real S1-S4 modules (`scripts/agent_selection/`) on
# synthetic facts only: a temporary project root, an in-memory adapter and a
# fixed model batch. No scenario reads or writes the repository, a real `.local`
# directory, a model API, an API key or the network.

SEL_NOW = '2026-10-27T12:00:00Z'
SEL_PROBE = '2026-10-26T12:00:00Z'
SEL_OLD_PROBE = '2026-07-01T00:00:00Z'
SEL_SCOPE_A = 'e2e-scope-a'
SEL_SCOPE_B = 'e2e-scope-b'
# (#170) The operator-written verification window and evidence reference the
# fixtures use for the canonical model identity of a registered agent.
SEL_IDENTITY_UNTIL = '2027-01-01T00:00:00Z'
SEL_IDENTITY_EVIDENCE = 'fixture:model-identity-registry'
SEL_MODULES = {}


def _sel():
    """Import the S1-S4 package once, from this checkout only."""
    if 'pkg' not in SEL_MODULES:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.agent_selection import (discovery, feedback, local_store,
                                             model_selection, roles,
                                             runtime_adapter, scope)
        from scripts.agent_selection.model_sources import snapshot

        SEL_MODULES['pkg'] = SimpleNamespace(
            discovery=discovery, feedback=feedback, local_store=local_store,
            model_selection=model_selection, roles=roles,
            runtime_adapter=runtime_adapter, scope=scope, snapshot=snapshot)
    return SEL_MODULES['pkg']


def _sel_capability(capability_id='C1', verdict='PASS', *, probe_date=SEL_PROBE,
                    source_class='OBSERVED', evidence_ref=None):
    agent = _sel()
    if evidence_ref is None:
        evidence_ref = f'evidence:{capability_id}' if verdict == 'PASS' else ''
    return agent.runtime_adapter.Capability(
        capability_id=capability_id, verdict=verdict, source_class=source_class,
        probe_date=probe_date, evidence_ref=evidence_ref)


def _sel_capability_record(*, status='COMPLETE', probe_date=SEL_PROBE,
                           overrides=None):
    agent = _sel()
    overrides = dict(overrides or {})
    rows = [_sel_capability(capability_id)
            for capability_id in ('C1', 'C2', 'C3', 'C4')]
    for index, capability_id in enumerate(('C1', 'C2', 'C3', 'C4')):
        if capability_id in overrides:
            rows[index] = overrides.pop(capability_id)
    for capability_id in ('C5', 'C6', 'C7', 'C8'):
        rows.append(overrides.pop(capability_id, _sel_capability(
            capability_id, 'UNKNOWN', probe_date=None)))
    if overrides:
        raise ScenarioFailed(f'unused capability overrides: {sorted(overrides)}')
    return agent.runtime_adapter.CapabilityRecord(
        runtime_id='fixture-runtime', runtime_version='1.0.0', edition='github',
        execution_mode='spawned-subagent', status=status,
        probe_date=probe_date, evidence_ref='fixture:capability-record',
        capabilities=tuple(rows))


def _sel_permissions(*, read_paths=('src',), write_paths=(), commands=(),
                     read_only=False):
    agent = _sel()
    return agent.runtime_adapter.PermissionScope(
        read_paths=tuple(read_paths), write_paths=tuple(write_paths),
        commands=tuple(commands), read_only=read_only)


def _sel_evidence(role, dimension, grade='STRONG'):
    agent = _sel()
    return agent.runtime_adapter.RoleEvidence(
        role=role, dimension=dimension, grade=grade, origin='project',
        evidence_ref=f'fixture:{role}:{dimension}', observed_at=SEL_NOW,
        sample_size=5, source_class='OBSERVED')


def _sel_candidate(agent_id, model_id, *, model_family='family-a',
                   modalities=('text',),
                   tools=('file-read', 'file-write', 'command'),
                   permissions=None, capability='default', cost_per_mtok=1.0,
                   role_evidence=(), evidence_time=SEL_NOW):
    agent = _sel()
    if capability == 'default':
        capability = _sel_capability_record()
    if permissions is None:
        permissions = _sel_permissions(write_paths=('src',), commands=('python',))
    return agent.runtime_adapter.AgentCandidate(
        runtime_id='fixture-runtime', runtime_version='1.0.0',
        agent_id=agent_id, model_id=model_id, model_family=model_family,
        modalities=tuple(modalities), tools=tuple(tools),
        permissions=permissions, capability_record=capability,
        source='runtime-discovery', source_class='OBSERVED',
        evidence_time=evidence_time, context_tokens=200000,
        cost_per_mtok=cost_per_mtok, latency_ms=1000,
        role_evidence=tuple(role_evidence))


def _sel_master_candidate():
    return _sel_candidate(
        'master-1', 'model-master', model_family='family-master',
        modalities=('text', 'image'), tools=('file-read',),
        permissions=_sel_permissions(), capability=None,
        role_evidence=(_sel_evidence('master', 'planning'),
                       _sel_evidence('master', 'quality-judgement')))


def _sel_execution_candidate(**kwargs):
    values = dict(
        agent_id='exec-1', model_id='model-a', model_family='family-a',
        role_evidence=(_sel_evidence('execution', 'coding'),
                       _sel_evidence('execution', 'delivery-efficiency', 'MODERATE'),
                       _sel_evidence('execution', 'tool-use', 'MODERATE')))
    values.update(kwargs)
    return _sel_candidate(**values)


def _sel_reviewer_candidate(model_id='model-b', model_family='family-b', **kwargs):
    values = dict(
        agent_id='review-1', model_id=model_id, model_family=model_family,
        tools=('file-read',),
        permissions=_sel_permissions(read_paths=('src',), read_only=True),
        role_evidence=(_sel_evidence('reviewer', 'defect-detection'),
                       _sel_evidence('reviewer', 'test-design', 'MODERATE'),
                       _sel_evidence('reviewer', 'security-review', 'MODERATE')))
    values.update(kwargs)
    return _sel_candidate(**values)


def _sel_discovery(candidates):
    """A runtime adapter whose formal enumeration is fixed by the scenario.

    The same agents are also registered in the scope's agent registry with an
    operator-confirmed canonical identity, so discovery can attach it on an
    exact runtime_id + agent_id + model_id match (#170). A Reviewer is only
    independent because that mapping exists, never because the fixture says so.
    """
    agent = _sel()
    registry = agent.discovery.ExplicitRegistry(
        entries=_sel_identity_entries(candidates))

    class _FixedAdapter(agent.runtime_adapter.RuntimeAdapter):
        def describe(self):
            return agent.runtime_adapter.RuntimeDescription(
                runtime_id='fixture-runtime', runtime_version='1.0.0',
                edition='github', execution_mode='spawned-subagent',
                adapter_id='fixture', can_enumerate_agents=True)

        def discover(self):
            return agent.runtime_adapter.DiscoveryResult(
                outcome='OK', mode='runtime', candidates=tuple(candidates))

    return agent.discovery.discover_candidates(adapter=_FixedAdapter(),
                                               registry=registry)


def _sel_identity(model_id):
    """The canonical identity an operator wrote for this fixture agent."""
    agent = _sel()
    return agent.runtime_adapter.CanonicalIdentity(
        canonical_id=model_id, status='CONFIRMED',
        evidence_ref=SEL_IDENTITY_EVIDENCE,
        verified_until=SEL_IDENTITY_UNTIL,
        detail='fixture operator-confirmed mapping')


def _sel_identity_entries(candidates):
    """Registry records carrying the fixture agents' confirmed identities."""
    agent = _sel()
    entries = []
    for candidate in candidates:
        if candidate.model_id is None or candidate.canonical_identity is not None:
            continue
        entries.append(agent.runtime_adapter.AgentCandidate(
            runtime_id=candidate.runtime_id,
            runtime_version=candidate.runtime_version,
            agent_id=candidate.agent_id, model_id=candidate.model_id,
            model_family=candidate.model_family,
            source=agent.runtime_adapter.CANDIDATE_SOURCES[1],
            evidence_time=SEL_NOW,
            canonical_identity=_sel_identity(candidate.model_id)))
    return tuple(entries)


def _sel_empty_discovery():
    agent = _sel()

    class _EmptyAdapter(agent.runtime_adapter.RuntimeAdapter):
        def describe(self):
            return agent.runtime_adapter.RuntimeDescription(
                runtime_id='fixture-runtime', runtime_version='1.0.0',
                edition='github', execution_mode='spawned-subagent',
                adapter_id='fixture', can_enumerate_agents=True)

        def discover(self):
            return agent.runtime_adapter.DiscoveryResult(
                outcome='EMPTY', mode='runtime')

    return agent.discovery.discover_candidates(adapter=_EmptyAdapter())


def _sel_requirements(*, execution=None, reviewer=None):
    agent = _sel()
    execution = dict(execution or {})
    reviewer = dict(reviewer or {})
    return {
        agent.roles.ROLE_MASTER: agent.roles.master_requirement(),
        agent.roles.ROLE_EXECUTION: agent.roles.execution_requirement(
            read_paths=('src',), write_paths=('src',), **execution),
        agent.roles.ROLE_REVIEWER: agent.roles.reviewer_requirement(
            execution_model_id='model-a', execution_model_family='family-a',
            read_paths=('src',), **reviewer),
    }


def _sel_model(slug, index, *, coding, intelligence, agentic=50.0):
    agent = _sel()
    snapshot = agent.snapshot
    return snapshot.ModelSnapshot(
        canonical_id=f'id-{index:02d}', name=f'Fixture {slug}', slug=slug,
        source_id='artificial-analysis',
        source_url='https://artificialanalysis.ai/api/v2/language/models',
        fetched_at=SEL_NOW,
        evaluations=(
            snapshot.ModelEvaluation('artificial_analysis_coding_index', coding),
            snapshot.ModelEvaluation(
                'artificial_analysis_intelligence_index', intelligence),
            snapshot.ModelEvaluation(
                'artificial_analysis_agentic_index', agentic),
        ),
        pricing=snapshot.ModelPricing(input_per_mtok=1.0),
        freshness=snapshot.FRESHNESS_FRESH, source_tier='pro')


def _sel_batch():
    """A fresh synthetic snapshot with enough models for a real cohort."""
    agent = _sel()
    snapshot = agent.snapshot
    models = [
        _sel_model('fixture-exec', 0, coding=95.0, intelligence=60.0),
        _sel_model('fixture-review', 1, coding=20.0, intelligence=99.0),
        _sel_model('fixture-master', 2, coding=70.0, intelligence=90.0),
    ]
    for index in range(3, 9):
        models.append(_sel_model(
            f'fixture-filler-{index}', index,
            coding=20.0 + index * 4, intelligence=40.0 + index * 5))
    fetch = snapshot.FetchRecord(
        source_id='artificial-analysis',
        source_url='https://artificialanalysis.ai/api/v2/language/models',
        status='OK', fetched_at=SEL_NOW, tier='pro', index_version='4.3',
        models_returned=len(models), requests=1)
    return snapshot.SnapshotBatch(
        source_id='artificial-analysis', fetch=fetch, models=tuple(models))


def _sel_stale_batch():
    agent = _sel()
    return agent.snapshot.stale_batch(
        _sel_batch(), status='STALE_CACHE',
        message='the live read failed and the cache is older than the window',
        now=SEL_NOW, cache_age_seconds=200000, max_age_seconds=86400,
        live_failure_status='RATE_LIMITED', http_status=429)


def _sel_links():
    return {'model-a': 'fixture-exec', 'model-b': 'fixture-review',
            'model-master': 'fixture-master'}


def _sel_select(discovery, *, batch=None, requirements=None, policy=None):
    agent = _sel()
    return agent.model_selection.select_models(
        _sel_batch() if batch is None else batch, discovery,
        requirements or _sel_requirements(), links=_sel_links(),
        policy=policy, now=SEL_NOW)


def _sel_failed_fetch(status='AUTH_FAILED', *, http_status=401,
                      message='authentication failed'):
    agent = _sel()
    snapshot = agent.snapshot
    fetch = snapshot.FetchRecord(
        source_id='artificial-analysis',
        source_url='https://artificialanalysis.ai/api/v2/language/models',
        status=status, fetched_at=SEL_NOW, http_status=http_status,
        message=message, requests=1)
    return snapshot.SnapshotBatch(
        source_id='artificial-analysis', fetch=fetch, models=())


def _sel_registry_details(*, agent_id, model_id, model_family, modalities,
                          tools, capability_ref='cap-1', identity=True,
                          **extra):
    details = {
        'runtime_id': 'fixture-runtime', 'runtime_version': '1.0.0',
        'agent_id': agent_id, 'model_id': model_id,
        'model_family': model_family, 'modalities': modalities,
        'tools': tools, 'capability_ref': capability_ref,
        'evidence_time': SEL_NOW, 'source_class': 'OBSERVED',
    }
    if identity:
        # (#170) The operator-written canonical identity for this agent: this
        # model id resolves to itself, and no alias is claimed here.
        details.update({
            'canonical_model_id': model_id,
            'identity_verification': 'CONFIRMED',
            'identity_verified_until': SEL_IDENTITY_UNTIL,
            'identity_evidence': SEL_IDENTITY_EVIDENCE,
        })
    details.update(extra)
    return {key: value for key, value in details.items() if value != ''}


def _sel_registry_store(ctx):
    """One initialized scope whose ``agent-registry.md`` lists three agents."""
    agent = _sel()
    root = ctx.sandbox / 'project'
    root.mkdir(parents=True, exist_ok=True)
    resolution = agent.scope.resolve_scope(
        root, env={'AGENTORCH_SCOPE_ID': SEL_SCOPE_A}, tty_name='/dev/pts/9')
    store = agent.local_store.LocalStore.from_resolution(root, resolution)
    store.initialize(now=SEL_NOW)
    entries = (
        _sel_registry_details(
            agent_id='master-1', model_id='model-master',
            model_family='family-master', modalities='text,image',
            tools='file-read', capability_ref=''),
        _sel_registry_details(
            agent_id='exec-1', model_id='model-a', model_family='family-a',
            modalities='text', tools='file-read,file-write,command',
            read_paths='src', write_paths='src', commands='python',
            context_tokens='200000', cost_per_mtok='1.0', latency_ms='1000'),
        _sel_registry_details(
            agent_id='review-1', model_id='model-b', model_family='family-b',
            modalities='text', tools='file-read', read_paths='src',
            read_only='true', cost_per_mtok='1.5', latency_ms='1200'),
    )
    for index, details in enumerate(entries, start=1):
        store.append_record(
            'agent-registry.md', summary=f'fixture agent {index}',
            source_class='OBSERVED', status='RECORDED', details=details,
            record_id=f'agent-{index}', now=SEL_NOW)
    return root, store


def _sel_registry_discovery(store, capability):
    agent = _sel()
    registry = agent.discovery.ExplicitRegistry.from_store(
        store, capability_records={'cap-1': capability})
    return agent.discovery.discover_candidates(registry=registry)


def _sel_has_spawn_api():
    """No selector module may expose a spawn / wait / collect implementation."""
    agent = _sel()
    names = ('spawn', 'start_agent', 'collect', 'wait_for')
    for module in (agent.roles, agent.model_selection, agent.discovery):
        for name in names:
            if hasattr(module, name):
                return f'{module.__name__}.{name}'
    return None


def _sel_record_feedback(store, count=3, *, scope_id=None, category='code-change'):
    agent = _sel()
    identity = agent.feedback.ModelIdentity('model-a', 'fixture-runtime', '1.0.0')
    written = []
    for index in range(1, count + 1):
        outcome = agent.feedback.ExecutionOutcome(
            task_id=f'issue:{index}', category=category, identity=identity,
            observed_at=SEL_NOW,
            evidence=(agent.feedback.EvidenceRef(
                'task_record', f'issue:{index}', SEL_NOW),),
            first_acceptance='PASS', rework_rounds=0,
            post_merge_regression_defects=0,
            scope_id=store.scope_id if scope_id is None else scope_id)
        written.append(agent.feedback.record_outcome(
            store, outcome, now=SEL_NOW, record_id=f'outcome-{index}'))
    return written


def scenario_e1(ctx):
    """Positive control: scope -> store -> discovery -> S3 -> S4, never a dispatch."""
    agent = _sel()
    root, store = _sel_registry_store(ctx)
    capability = _sel_capability_record()
    gate = capability.gate(now=SEL_NOW)
    _require(gate.passed, gate.as_dict())
    _require(store.scope_id.startswith(SEL_SCOPE_A), store.scope_id)
    discovery = _sel_registry_discovery(store, capability)
    _require(discovery.outcome == agent.discovery.DISCOVERY_REGISTRY,
             discovery.outcome)
    _require(len(discovery.candidates) == 3, len(discovery.candidates))

    report = _sel_select(discovery)
    _require(report.automatic_allowed, report.degradations)
    _require(report.executed is False, 'a selection claimed a dispatch')
    _require(report.role('master').selected is not None, 'no Master selected')
    _require(report.role('execution').selected.candidate.candidate_id
             == 'fixture-runtime:exec-1', report.role('execution').reasons)
    _require(report.role('reviewer').selected.candidate.model_id == 'model-b',
             'the Reviewer is not the independent model')
    _require(report.role('execution').selected.candidate.model_id
             != report.role('reviewer').selected.candidate.model_id,
             'Execution and Reviewer share one model')
    _require(report.selected_assessment('execution').status == 'MATCHED',
             report.selected_assessment('execution').status)
    fetch = report.source.fetch
    _require(fetch.status == 'OK' and fetch.live, fetch.status)
    _require(fetch.index_version == '4.3', fetch.index_version)
    _require(_sel_has_spawn_api() is None, _sel_has_spawn_api())

    written = _sel_record_feedback(store)
    _require(len(written) == 3, len(written))
    feedback_report = agent.feedback.build_report(store, now=SEL_NOW)
    _require(feedback_report.scope_id == store.scope_id, feedback_report.scope_id)
    metric = feedback_report.cohort('execution', 'code-change').metric(
        'first_acceptance_pass_rate')
    _require(metric.status == 'OK' and metric.value == 1.0, metric.as_dict())
    _require(metric.sample_size == 3, metric.sample_size)
    _require(feedback_report.applied_automatically is False,
             'local feedback changed the configuration on its own')
    _require(feedback_report.changes_configuration is False,
             'local feedback changed the configuration on its own')

    store.append_record(
        'selection-history.md',
        summary='roles selected; selection is not a dispatch',
        source_class='OBSERVED', status='RECORDED',
        details={
            'execution': report.role('execution').selected.candidate.candidate_id,
            'reviewer': report.role('reviewer').selected.candidate.candidate_id,
            'execution_model': report.role('execution').selected.candidate.model_id,
            'reviewer_model': report.role('reviewer').selected.candidate.model_id,
            'alternatives': ','.join(
                item.candidate.candidate_id
                for role in agent.roles.ROLES
                for item in report.role(role).alternatives) or '(none)',
            'hard_filter_failures': ','.join(
                f'{item.candidate.candidate_id}:{entry.filter_id}'
                for role in agent.roles.ROLES
                for item in report.role(role).excluded
                for entry in item.filters if entry.status == 'FAIL') or '(none)',
            'source': f'{fetch.source_id} {fetch.status}',
            'fetched_at': fetch.fetched_at,
            'freshness': fetch.freshness,
            'degradations': '; '.join(report.degradations) or '(none)',
            'limitations': '; '.join(fetch.limitations) or '(none)',
            'executed': 'false',
            'scope': store.scope_id,
        },
        record_id='selection-1', now=SEL_NOW)
    history = store.read_records('selection-history.md')
    _require(len(history) == 1, len(history))
    _require(history[0].details['executed'] == 'false', history[0].details)
    evidence_path = store.directory / 'selection-history.md'
    _require(evidence_path.is_relative_to(store.directory),
             'the selection evidence is outside its scope')
    evidence_text = evidence_path.read_text(encoding='utf-8')
    for key in ('execution', 'reviewer', 'alternatives', 'hard_filter_failures',
                'source', 'fetched_at', 'freshness', 'degradations',
                'limitations', 'executed', 'scope'):
        _require(f'{key}:' in evidence_text,
                 f'the selection evidence is missing {key}')
    scope_dirs = sorted(path.name for path in
                        (root / '.local' / 'agent-orch').iterdir()
                        if path.is_dir() and not path.name.startswith('_'))
    _require(scope_dirs == [store.scope_id], scope_dirs)
    return [
        f'gate: C1-C4 {gate.status}; scope {store.scope_id} isolated',
        f'discovery: {discovery.outcome} with {len(discovery.candidates)} candidates',
        'selection: master-1 / exec-1 (model-a) / review-1 (model-b), '
        'automatic=True, executed=False',
        f'source: {fetch.source_id} {fetch.status} live={fetch.live} '
        f'index_version={fetch.index_version} fetched_at={fetch.fetched_at}',
        'external evidence: coding=STRONG for exec-1, Reviewer dimensions UNKNOWN '
        '(no review benchmark invented)',
        f'feedback: first_acceptance_pass_rate=1.0 n=3 coverage={metric.coverage}, '
        'applied=False changes_configuration=False',
        'selection-history.md: one Markdown record carrying candidates, alternatives, '
        'hard-filter failures, source / fetched_at / freshness, degradations, '
        'limitations, scope and executed=false; one scope directory only',
    ]


def scenario_e2(ctx):
    """No spawn: a PARTIAL record keeps every role undispatched."""
    agent = _sel()
    partial = _sel_capability_record(
        status='PARTIAL', overrides={'C1': _sel_capability('C1', 'UNKNOWN')})
    gate = partial.gate(now=SEL_NOW)
    _require(gate.status == 'UNKNOWN', gate.as_dict())
    _require('PARTIAL' in ' '.join(gate.reasons), gate.reasons)
    discovery = _sel_discovery([
        _sel_master_candidate(),
        _sel_execution_candidate(capability=partial),
        _sel_reviewer_candidate(capability=partial),
    ])
    report = agent.roles.select_roles(
        discovery, _sel_requirements(), now=SEL_NOW)
    _require(report.executed is False, 'a selection claimed a dispatch')
    _require(report.automatic_dispatch_allowed is False, 'automatic dispatch stayed open')
    execution = report.role('execution')
    _require(execution.selected is None, 'an unverified runtime was selected')
    _require(execution.manual_handoff, 'the task was not handed off')
    filters = {item.filter_id: item.status
               for item in execution.excluded[0].filters}
    _require(filters['callable'] == 'UNKNOWN', filters)
    _require(any('undispatched' in item for item in report.degradations),
             report.degradations)
    _require(_sel_has_spawn_api() is None, _sel_has_spawn_api())
    return [
        f'gate: {gate.status} ({gate.reasons[0][:70]})',
        f'execution: selected=None handoff=True; callable filter={filters["callable"]}',
        f'degradation: {report.degradations[0]}',
        'executed=False; no spawn / collect API exists in the selector modules',
    ]


def scenario_e3(ctx):
    """C1-C4 failure and stale probes both close the automatic path."""
    agent = _sel()

    def evaluate(capability):
        discovery = _sel_discovery([
            _sel_master_candidate(),
            _sel_execution_candidate(capability=capability),
            _sel_reviewer_candidate(capability=capability),
        ])
        return agent.roles.select_roles(
            discovery, _sel_requirements(), now=SEL_NOW)

    failed = _sel_capability_record(
        overrides={'C4': _sel_capability('C4', 'FAIL')})
    failed_gate = failed.gate(now=SEL_NOW)
    failed_report = evaluate(failed)
    _require(failed_gate.status == 'BLOCKED', failed_gate.as_dict())
    _require(any('C4: FAIL' in item for item in failed_gate.reasons),
             failed_gate.reasons)
    _require(failed_report.automatic_dispatch_allowed is False,
             'a FAIL verdict unlocked automatic dispatch')
    _require(failed_report.role('execution').selected is None,
             'an Execution Agent was selected despite C4 FAIL')

    stale = _sel_capability_record(probe_date=SEL_OLD_PROBE)
    stale_gate = stale.gate(now=SEL_NOW)
    stale_report = evaluate(stale)
    _require(stale_gate.status == 'BLOCKED' and stale_gate.stale,
             stale_gate.as_dict())
    _require('re-probe' in ' '.join(stale_gate.reasons), stale_gate.reasons)
    _require(stale_report.automatic_dispatch_allowed is False,
             'stale evidence unlocked automatic dispatch')
    return [
        f'C4 FAIL: gate {failed_gate.status}; automatic=False; execution=None',
        f'stale probe: gate {stale_gate.status} stale={stale_gate.stale}; '
        f'{stale_gate.reasons[0][:70]}',
        'UNKNOWN / STALE / FAIL never became PASS',
    ]


def scenario_e4(ctx):
    """Missing Execution: the task stays undispatched with a concrete reminder."""
    agent = _sel()
    discovery = _sel_discovery([_sel_master_candidate(),
                                _sel_reviewer_candidate()])
    report = agent.roles.select_roles(
        discovery, _sel_requirements(), now=SEL_NOW)
    execution = report.role('execution')
    _require(execution.selected is None, 'an Execution Agent was invented')
    _require(execution.manual_handoff and not execution.auto_allowed,
             execution.as_dict())
    _require('No qualified Execution Agent' in (execution.reminder or ''),
             execution.reminder)
    _require('Register the agent' in (execution.reminder or ''),
             'the reminder does not say how to register a role')
    _require(report.automatic_dispatch_allowed is False,
             'automatic dispatch stayed open')
    _require(any('undispatched' in item for item in report.degradations),
             report.degradations)
    _require(report.executed is False, 'a selection claimed a dispatch')
    return [
        f'execution: selected=None handoff=True; missing={len(execution.missing)} '
        'filter gap(s)',
        f'reminder: {execution.reminder[:110]}...',
        f'degradation: {report.degradations[0]}',
    ]


def scenario_e5(ctx):
    """Missing Reviewer: self-review is refused, the task stays unreviewed."""
    agent = _sel()
    discovery = _sel_discovery([_sel_master_candidate(),
                                _sel_execution_candidate()])
    report = agent.roles.select_roles(
        discovery, _sel_requirements(), now=SEL_NOW)
    _require(report.role('execution').selected is not None,
             'the positive half of the fixture broke')
    reviewer = report.role('reviewer')
    _require(reviewer.selected is None, 'an independent Reviewer was invented')
    _require(reviewer.manual_handoff, 'the review gap was not handed off')
    _require('self-review is not a substitute' in (reviewer.reminder or ''),
             reviewer.reminder)
    _require(any('no independent Reviewer' in item
                 for item in report.degradations), report.degradations)
    _require(report.automatic_dispatch_allowed is False,
             'a task without an independent Reviewer was auto-dispatched')
    return [
        'execution: fixture-runtime:exec-1 selected',
        'reviewer: selected=None handoff=True; Execution self-review is refused',
        f'degradation: {report.degradations[-1]}',
    ]


def scenario_e6(ctx):
    """Same underlying model: the Reviewer is excluded even with top scores."""
    agent = _sel()
    same = _sel_reviewer_candidate(model_id='model-a')
    discovery = _sel_discovery([_sel_master_candidate(),
                                _sel_execution_candidate(), same])
    report = _sel_select(discovery)
    selection = report.role('reviewer')
    _require(selection.selected is None, 'the same model was reused as Reviewer')
    entry = next((item for item in selection.excluded
                  if item.candidate.agent_id == 'review-1'), None)
    _require(entry is not None, 'the same-model candidate is not reported')
    independence = next(item for item in entry.filters
                        if item.filter_id == 'independence')
    _require(independence.status == 'FAIL', independence.as_dict())
    _require('same as the selected Execution model' in independence.detail,
             independence.detail)
    _require(report.automatic_allowed is False, 'automatic selection stayed open')
    assessment = next(item for item in report.assessments_for('reviewer')
                      if item.candidate_id == 'fixture-runtime:review-1')
    defect_signal = next(signal for signal in assessment.signals
                         if signal.dimension == 'defect-detection')
    _require(defect_signal.grade == 'UNKNOWN', defect_signal.as_dict())
    return [
        'review-1 (model-a = selected Execution model) excluded: independence=FAIL',
        f'detail: {independence.detail}',
        'external scores did not rescue it; Reviewer dimensions stay UNKNOWN',
        'automatic=False',
    ]


def scenario_e7(ctx):
    """Modal gap: a text-only Master fails the image hard filter."""
    agent = _sel()
    text_only = _sel_candidate(
        'master-1', 'model-master', model_family='family-master',
        modalities=('text',), tools=('file-read',),
        permissions=_sel_permissions(), capability=None,
        role_evidence=(_sel_evidence('master', 'planning'),))
    discovery = _sel_discovery([text_only, _sel_execution_candidate(),
                                _sel_reviewer_candidate()])
    report = agent.roles.select_roles(
        discovery, _sel_requirements(), now=SEL_NOW)
    master = report.role('master')
    _require(master.selected is None, 'a text-only Master was selected')
    modality = next(item for item in master.excluded[0].filters
                    if item.filter_id == 'modality')
    _require(modality.status == 'FAIL', modality.as_dict())
    _require('image' in modality.detail, modality.detail)
    _require(report.automatic_dispatch_allowed is False,
             'automatic selection stayed open')
    _require(any('multi-modal' in item for item in report.degradations),
             report.degradations)
    return [
        'master: modality=FAIL (image missing); selected=None',
        f'detail: {modality.detail}',
        f'degradation: {report.degradations[0]}',
    ]


def scenario_e8(ctx):
    """Unavailable / expired model data: optional, honest, never called latest."""
    agent = _sel()
    discovery = _sel_discovery([_sel_master_candidate(),
                                _sel_execution_candidate(),
                                _sel_reviewer_candidate()])
    failed = _sel_select(discovery, batch=_sel_failed_fetch())
    _require(failed.source.fetch.status == 'AUTH_FAILED',
             failed.source.fetch.status)
    _require(any('AUTH_FAILED' in item for item in failed.degradations),
             failed.degradations)
    for role in agent.roles.ROLES:
        assessment = failed.selected_assessment(role)
        _require(assessment.status == 'UNLINKED', (role, assessment.status))
        _require(all(signal.grade == 'UNKNOWN' for signal in assessment.signals),
                 f'{role}: a failed source produced external evidence')
    _require(failed.role('execution').selected is not None,
             'a failed optional source blocked the S2 selection')

    strict = _sel_select(
        discovery, batch=_sel_failed_fetch(),
        policy=agent.model_selection.AssessmentPolicy(require_fresh_source=True))
    _require(strict.automatic_allowed is False,
             'a policy requiring fresh data was ignored')
    _require(any('AUTH_FAILED' in item for item in strict.decisions),
             strict.decisions)

    stale = _sel_select(discovery, batch=_sel_stale_batch())
    _require(stale.source.fetch.status == 'STALE_CACHE',
             stale.source.fetch.status)
    _require(stale.source.fetch.usable is False, 'an expired cache was usable')
    _require(stale.source.fetch.live is False, 'an expired cache was marked live')
    _require(stale.source.fetch.freshness == 'STALE',
             stale.source.fetch.freshness)
    for role in agent.roles.ROLES:
        assessment = stale.selected_assessment(role)
        _require(assessment.status == 'STALE', (role, assessment.status))
        _require(all(signal.grade == 'UNKNOWN' for signal in assessment.signals),
                 f'{role}: a stale cache produced ranked external evidence')
    stale_strict = _sel_select(
        discovery, batch=_sel_stale_batch(),
        policy=agent.model_selection.AssessmentPolicy(require_fresh_source=True))
    _require(stale_strict.automatic_allowed is False,
             'stale data with a fresh-source policy stayed automatic')
    _require(any('STALE_CACHE' in item for item in stale_strict.decisions),
             stale_strict.decisions)
    return [
        f'AUTH_FAILED: assessments UNLINKED, S2 selection stands, decisions '
        f'{len(strict.decisions)}',
        'STALE_CACHE: assessments STALE, freshness=STALE, usable=False, '
        'no external grade',
        'strict policy withheld automatic selection for both cases',
    ]


def scenario_e9(ctx):
    """Budget overrun: the affordable model is excluded before ranking."""
    agent = _sel()
    discovery = _sel_discovery([_sel_master_candidate(),
                                _sel_execution_candidate(),
                                _sel_reviewer_candidate()])
    report = _sel_select(
        discovery, requirements=_sel_requirements(
            execution={'max_cost_per_mtok': 0.5}))
    execution = report.role('execution')
    _require(execution.selected is None, 'an over-budget candidate was selected')
    entry = next(item for item in execution.excluded
                 if item.candidate.agent_id == 'exec-1')
    budget = next(item for item in entry.filters if item.filter_id == 'budget')
    _require(budget.status == 'FAIL', budget.as_dict())
    _require('exceeds the limit' in budget.detail, budget.detail)
    _require(report.automatic_allowed is False, 'automatic selection stayed open')
    return [
        f'exec-1 cost=1.0/Mtok exceeds the 0.5 limit: budget=FAIL',
        f'detail: {budget.detail}',
        'selected=None; automatic=False',
    ]


def scenario_e10(ctx):
    """Scope isolation: no shared default directory and no cross-scope record."""
    agent = _sel()
    root = ctx.sandbox / 'project'
    root.mkdir(parents=True, exist_ok=True)
    unresolved = agent.scope.resolve_scope(root, env={}, tty_name=None)
    _require(unresolved.scope_id is None, unresolved.as_dict())
    _require(unresolved.history_allowed is False, 'unproven isolation allowed history')
    _require('shared / default' in (unresolved.error or ''),
             unresolved.error)
    try:
        agent.local_store.open_store(root, env={}, tty_name=None)
    except agent.scope.ScopeIdentityError as exc:
        refusal = str(exc)
    else:
        raise ScenarioFailed('a store was opened without a resolvable scope')
    _require(not (root / '.local').exists(), 'a shared default directory was created')

    scope_a = agent.scope.resolve_scope(
        root, env={'AGENTORCH_SCOPE_ID': SEL_SCOPE_A}, tty_name='/dev/pts/1')
    scope_b = agent.scope.resolve_scope(
        root, env={'AGENTORCH_SCOPE_ID': SEL_SCOPE_B}, tty_name='/dev/pts/2')
    _require(scope_a.scope_id != scope_b.scope_id, 'two terminals shared a scope')
    store_a = agent.local_store.LocalStore.from_resolution(root, scope_a)
    store_b = agent.local_store.LocalStore.from_resolution(root, scope_b)
    store_a.initialize(now=SEL_NOW)
    store_b.initialize(now=SEL_NOW)
    try:
        _sel_record_feedback(store_a, count=1, scope_id=store_b.scope_id)
    except agent.feedback.FeedbackScopeError as exc:
        cross_scope = str(exc)
    else:
        raise ScenarioFailed('a cross-scope outcome was accepted')
    _require('never merged or inherited' in cross_scope, cross_scope)
    _require(store_a.read_records('observed-performance.md') == (),
             'the refused cross-scope record was written')
    _require(store_b.read_records('observed-performance.md') == (),
             'the other scope received a foreign record')
    directories = sorted(path.name for path in
                         (root / '.local' / 'agent-orch').iterdir()
                         if path.is_dir() and not path.name.startswith('_'))
    _require(len(directories) == 2 and len(set(directories)) == 2, directories)
    return [
        f'unresolved scope: error="{refusal[:70]}..." history_allowed=False; '
        'no .local directory created',
        f'two terminals: {store_a.scope_id} != {store_b.scope_id}',
        f'cross-scope record refused: {cross_scope[:80]}...',
        'both stores keep 0 feedback records; no shared default directory',
    ]


def scenario_e11(ctx):
    """Write failure: the audit trail is reported missing, never claimed."""
    agent = _sel()
    root = ctx.sandbox / 'project'
    root.mkdir(parents=True, exist_ok=True)
    resolution = agent.scope.resolve_scope(
        root, env={'AGENTORCH_SCOPE_ID': SEL_SCOPE_A}, tty_name='/dev/pts/3')
    store = agent.local_store.LocalStore.from_resolution(root, resolution)
    store.initialize(now=SEL_NOW)
    identity = agent.feedback.ModelIdentity('model-a', 'fixture-runtime', '1.0.0')
    outcome = agent.feedback.ExecutionOutcome(
        task_id='issue:1', category='code-change', identity=identity,
        observed_at=SEL_NOW,
        evidence=(agent.feedback.EvidenceRef('task_record', 'issue:1', SEL_NOW),),
        first_acceptance='PASS', rework_rounds=0)
    with mock.patch.object(agent.local_store.os, 'replace',
                           side_effect=PermissionError('read-only')):
        try:
            agent.feedback.record_outcome(store, outcome, now=SEL_NOW)
        except agent.feedback.FeedbackWriteError as exc:
            refusal = str(exc)
        else:
            raise ScenarioFailed('a failed write was reported as recorded')
        try:
            store.append_record('selection-history.md', summary='selection',
                                source_class='OBSERVED', status='RECORDED',
                                now=SEL_NOW)
        except agent.local_store.StoreWriteError as exc:
            selection_refusal = str(exc)
        else:
            raise ScenarioFailed('a failed selection-history write was accepted')
    _require('was not written' in refusal, refusal)
    _require(store.read_records('observed-performance.md') == (),
             'the failed feedback write left a record behind')
    _require(store.read_records('selection-history.md') == (),
             'the failed selection write left a record behind')
    return [
        f'feedback write refused: {refusal[:90]}...',
        f'selection-history write refused: {selection_refusal[:70]}...',
        '0 records in both documents; the audit trail is not claimed as complete',
    ]


def scenario_e12(ctx):
    """Zero qualified candidates: no role is silently chosen."""
    agent = _sel()
    junk = _sel_candidate(
        'junk-1', 'model-x', modalities=('text',), tools=(),
        permissions=_sel_permissions(read_paths=(), write_paths=(), commands=()))
    discovery = _sel_discovery([junk])
    report = agent.roles.select_roles(
        discovery, _sel_requirements(), now=SEL_NOW)
    for role in agent.roles.ROLES:
        selection = report.role(role)
        _require(selection.selected is None, f'{role} was selected from no candidate')
        _require(selection.manual_handoff, f'{role} was not handed off')
    _require(report.automatic_dispatch_allowed is False,
             'automatic selection stayed open without a qualified candidate')
    _require(report.executed is False, 'a selection claimed a dispatch')
    _require(all('no candidate passed every hard filter' in selection.reasons[0]
                 for selection in report.selections.values()),
             report.selections)
    _require(any('no qualified' in item for item in report.degradations),
             report.degradations)
    empty = _sel_empty_discovery()
    _require(empty.outcome == agent.discovery.DISCOVERY_EMPTY, empty.outcome)
    empty_report = agent.roles.select_roles(
        empty, _sel_requirements(), now=SEL_NOW)
    _require(all(selection.selected is None
                 for selection in empty_report.selections.values()),
             'an empty discovery produced a candidate')
    _require(not empty_report.automatic_dispatch_allowed,
             'an empty discovery left automatic dispatch open')
    return [
        'junk-1 fails tools / permissions: all three roles None, handoff=True',
        f'degradation: {report.degradations[0]}',
        f'empty discovery: {empty.outcome}; all roles None; automatic=False',
    ]


def scenario_e13(ctx):
    """Combined worst case: no spawn, no Reviewer, stale data, cross scope, write failure."""
    agent = _sel()
    blocked = _sel_capability_record(
        status='PARTIAL', overrides={'C1': _sel_capability('C1', 'UNKNOWN')})
    discovery = _sel_discovery([
        _sel_master_candidate(),
        _sel_execution_candidate(capability=blocked),
    ])
    report = _sel_select(discovery, batch=_sel_stale_batch())
    _require(report.executed is False, 'the combined failure claimed a dispatch')
    _require(report.automatic_allowed is False, 'automatic selection stayed open')
    for role in (agent.roles.ROLE_EXECUTION, agent.roles.ROLE_REVIEWER):
        _require(report.role(role).selected is None,
                 f'{role} was selected despite the combined failure')
        _require(report.role(role).manual_handoff,
                 f'{role} was not handed off')
    _require(report.role(agent.roles.ROLE_MASTER).selected is not None,
             'the Master fixture broke')
    text = ' | '.join(report.degradations)
    for needle in ('no qualified Execution Agent', 'no qualified Execution Agent, so '
                   'Reviewer independence', 'STALE_CACHE'):
        _require(needle in text, (needle, report.degradations))

    root = ctx.sandbox / 'project'
    root.mkdir(parents=True, exist_ok=True)
    scope_a = agent.scope.resolve_scope(
        root, env={'AGENTORCH_SCOPE_ID': SEL_SCOPE_A}, tty_name='/dev/pts/4')
    scope_b = agent.scope.resolve_scope(
        root, env={'AGENTORCH_SCOPE_ID': SEL_SCOPE_B}, tty_name='/dev/pts/5')
    store = agent.local_store.LocalStore.from_resolution(root, scope_a)
    store.initialize(now=SEL_NOW)
    try:
        _sel_record_feedback(store, count=1, scope_id=scope_b.scope_id)
    except agent.feedback.FeedbackScopeError:
        pass
    else:
        raise ScenarioFailed('a cross-scope record was accepted')
    identity = agent.feedback.ModelIdentity('model-a', 'fixture-runtime', '1.0.0')
    outcome = agent.feedback.ExecutionOutcome(
        task_id='issue:9', category='code-change', identity=identity,
        observed_at=SEL_NOW,
        evidence=(agent.feedback.EvidenceRef('task_record', 'issue:9', SEL_NOW),),
        first_acceptance='UNKNOWN', rework_rounds=None)
    with mock.patch.object(agent.local_store.os, 'replace',
                           side_effect=PermissionError('read-only')):
        try:
            agent.feedback.record_outcome(store, outcome, now=SEL_NOW)
        except agent.feedback.FeedbackWriteError:
            pass
        else:
            raise ScenarioFailed('a failed write was reported as recorded')
    _require(store.read_records('observed-performance.md') == (),
             'the failed write left a record behind')
    return [
        'gate: C1 UNKNOWN + PARTIAL -> Execution and Reviewer None with handoff=True',
        'degradations: no Execution / no independent Reviewer / STALE_CACHE',
        'scope: cross-scope record refused; write failure reported as not written',
        'executed=False; no "dispatched" or "passed" conclusion is produced',
    ]
