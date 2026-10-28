#!/usr/bin/env python3
"""Synthetic delivery regression scenarios for the Agent-Orchestration kit.

This runner answers one question the documents cannot answer by themselves:
does the kit *stop* the failures it describes? Four groups are covered, each
taken from a typical failure mode rather than from a product's private data:

  A  governance / authorization   (evidence gates, revocation, duplicate writes,
                                   self-improvement candidates: source evidence,
                                   prompt injection, dedup, conflicts, risk gates)
  B  migration / versioning       (rerun, partial write, version isolation, and the
                                   safe update engine: conflicts, bad packages,
                                   traversal, removal, baseline, rollback)
  C  cross-module closeout        (unwired rule, ownership, event barrier, crash,
                                   shared state, locking, rollback and staged failure,
                                   architecture contract edges and cycles)
  D  orchestration base           (edition, resource lock, stale SHA, retries, audit,
                                   metric honesty: window / sample / missing, no score)
  E  agent-role selection         (Phase D S5: scope / local records, discovery, role
                                   matching, optional model data and feedback; a
                                   selection is never read as a dispatch)

Every scenario declares its precondition, the defect it injects, the gate that
must fire and the evidence it leaves behind; `docs/testing/delivery-regression-scenarios.md`
carries the same table for humans. Scenarios are synthetic and self-contained:
fixtures are generated in a throw-away directory, nothing is read from or
written to the repository, and no network, credential or external model is used.

The scenario implementations live in `scripts/delivery_scenarios/` (one module
per group, shared fixtures in `common.py`); this file keeps the registry, the
selector, the executor and the CLI.

Two properties are deliberate:

  * one scenario cannot end the run. A scenario that raises anything (including
    a non-assertion exception such as TypeError) is recorded as ERROR with its
    traceback and the remaining scenarios still execute;
  * a negative group always comes with a positive control, so "everything fails"
    is never mistaken for "the gates work".

Exit codes: 0 all scenarios passed, 1 at least one scenario failed or errored,
2 the runner itself could not run (missing module, unknown --only id, unwritable
--output). Only the standard library is used.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.delivery_scenarios.common import (  # noqa: E402
    Context,
    ROOT,
    Result,
    SCRIPTS,
    Scenario,
    ScenarioFailed,
    ToolError,
    _load_module,
)
from scripts.delivery_scenarios.group_a import (  # noqa: E402
    scenario_a1,
    scenario_a10,
    scenario_a11,
    scenario_a12,
    scenario_a13,
    scenario_a14,
    scenario_a15,
    scenario_a2,
    scenario_a3,
    scenario_a4,
    scenario_a5,
    scenario_a6,
    scenario_a7,
    scenario_a8,
    scenario_a9,
)
from scripts.delivery_scenarios.group_b import (  # noqa: E402
    scenario_b1,
    scenario_b10,
    scenario_b11,
    scenario_b12,
    scenario_b13,
    scenario_b14,
    scenario_b2,
    scenario_b3,
    scenario_b4,
    scenario_b5,
    scenario_b6,
    scenario_b7,
    scenario_b8,
    scenario_b9,
)
from scripts.delivery_scenarios.group_c import (  # noqa: E402
    _adapter_contract_run,
    _agm_tree,
    _arch_check,
    _arch_modules,
    _bump_manifest,
    _call_site_findings,
    _capability_findings,
    _capability_record,
    _claim_findings,
    _dispatch_outcome,
    _load_architecture,
    _load_synthetic,
    _restore_tree,
    _scan_entries,
    _stage_report,
    _terminal_findings,
    _transport_behaviour,
    _tree_digest,
    _verification_status,
    _write_agm_contract,
    scenario_c1,
    scenario_c10,
    scenario_c11,
    scenario_c12,
    scenario_c13,
    scenario_c14,
    scenario_c15,
    scenario_c16,
    scenario_c17,
    scenario_c18,
    scenario_c19,
    scenario_c2,
    scenario_c20,
    scenario_c3,
    scenario_c4,
    scenario_c5,
    scenario_c6,
    scenario_c7,
    scenario_c8,
    scenario_c9,
)
from scripts.delivery_scenarios.group_d import (  # noqa: E402
    scenario_d1,
    scenario_d2,
    scenario_d3,
    scenario_d4,
    scenario_d5,
    scenario_d6,
    scenario_d7,
    scenario_d8,
    scenario_d9,
)
from scripts.delivery_scenarios.group_e import (  # noqa: E402
    scenario_e1,
    scenario_e10,
    scenario_e11,
    scenario_e12,
    scenario_e13,
    scenario_e2,
    scenario_e3,
    scenario_e4,
    scenario_e5,
    scenario_e6,
    scenario_e7,
    scenario_e8,
    scenario_e9,
)


SCENARIO_DOC = 'docs/testing/delivery-regression-scenarios.md'
REPORT_SCHEMA = 'agentorch.delivery-scenarios/1'

EXIT_PASS = 0
EXIT_SCENARIO_FAILURES = 1
EXIT_TOOL_ERROR = 2

GROUPS = {
    'A': 'governance / authorization (evidence gates, self-improvement candidate governance)',
    'B': 'migration / versioning (idempotent migration, safe update engine)',
    'C': 'cross-module closeout / shared state, recovery and architecture alignment',
    'D': 'orchestration base / evidence decay (edition, locks, metric honesty)',
    'E': 'agent-role selection / Scope, discovery, model data and local feedback (Phase D S5)',
}


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
SCENARIOS = (
    Scenario('A1', 'A', 'positive', 'legal packet and legal approval cycle',
             'a fresh local project with an explicit edition; one legal packet fixture',
             'none (positive control)',
             'G1/G5: 0 findings; PLANNED -> VERIFIED with head-bound approval',
             'packet findings, task status, approved_sha, remaining locks',
             'temporary project root removed with the scenario sandbox', scenario_a1),
    Scenario('A2', 'A', 'negative', 'authorization revoked but the AC stays met',
             'one AC with a FAIL evidence row for the revoked grant',
             'the task keeps AC-2 checked although its evidence is FAIL',
             'G1: an AC marked as met with FAIL evidence is rejected',
             'validator finding text', 'no state written; sandbox removed', scenario_a2),
    Scenario('A3', 'A', 'negative', 'audit context missing (no evidence item)',
             'two ACs, only one evidence row',
             'AC-2 is checked without any evidence mapping',
             'G1: every AC needs at least one evidence item',
             'validator finding text', 'sandbox removed', scenario_a3),
    Scenario('A4', 'A', 'negative', 'empty acceptance criterion',
             'AC-1 declared with an empty statement',
             'a bare "- [ ] AC-1:" line is treated as an acceptance criterion',
             'G1: an AC without a checkable statement is rejected',
             'validator finding text', 'sandbox removed', scenario_a4),
    Scenario('A5', 'A', 'negative', 'NOT_RUN disguised as PASS',
             'one PASS row whose actual text says the suite did not run',
             'a not-executed check is recorded as PASS',
             'G1: PASS rows that report not-run/blocked evidence are rejected',
             'validator finding text', 'sandbox removed', scenario_a5),
    Scenario('A6', 'A', 'negative', 'duplicate write and concurrent state change',
             'two tasks sharing one allowed path, one already dispatched',
             'a second dispatch of the same task and a concurrent claim on the path',
             'path lock + state machine: both calls are refused, no lock is left behind',
             'refusal messages, lock counts for the refused task', 'sandbox removed', scenario_a6),
    Scenario('A7', 'A', 'negative', 'executor self-approval',
             'a dispatched task with no review states visited',
             'the executor sets APPROVED / VERIFIED directly',
             'G6: illegal transitions are refused and the task does not move',
             'refusal messages, status and approved_sha after the attempt',
             'sandbox removed', scenario_a7),
    Scenario('B1', 'B', 'positive', 'idempotent migration with version isolation',
             'three synthetic legacy records at version 2014',
             'none (positive control)',
             'migration: count, version, ids and completion marker all consistent after a rerun',
             'artifact digest before/after rerun, verification findings',
             'synthetic artifact removed with the sandbox', scenario_b1),
    Scenario('B2', 'B', 'negative', 'migration interrupted halfway',
             'a legacy artifact plus a backup copy',
             'only one of three records is written and the completion marker is absent',
             'migration: partial writes are reported, recovery from the backup verifies clean',
             'verification findings before/after recovery', 'sandbox removed', scenario_b2),
    Scenario('B3', 'B', 'negative', 'two versions mixed in one target set',
             'a migrated set where one row still carries version 2014',
             'version isolation is broken while the migration claims completion',
             'migration: a mixed-version row is reported as a version isolation break',
             'verification finding text', 'sandbox removed', scenario_b3),
    Scenario('B4', 'B', 'negative', 'migration completed claim without an artifact',
             'a structurally valid packet claiming the upgrade is done; no artifact on disk',
             'the claim is the only evidence; the artifact does not exist',
             'G1 + artifact check: the claim is well formed but contradicted by the artifact',
             'packet findings (none) and the artifact-check finding',
             'sandbox removed', scenario_b4),
    Scenario('C1', 'C', 'positive', 'wired rule with a complete event chain',
             'a three-step synthetic turn with ownership, rule and commit barrier wired',
             'none (positive control)',
             'cross-module: rule wired, ownership honoured, commit barrier respected',
             'event list and invariant findings', 'in-memory only', scenario_c1),
    Scenario('C2', 'C', 'negative', 'frozen rule never called',
             'the same turn with the action_resolved rule unwired',
             'the frozen rule is dropped from the call path',
             'cross-module: the missing event is reported as an unwired rule',
             'event list and finding text', 'in-memory only', scenario_c2),
    Scenario('C3', 'C', 'negative', 'ownership check missing',
             'a turn where player-2 acts on a unit owned by player-1',
             'the ownership guard is disabled',
             'cross-module: an action resolved by a non-owner is reported',
             'finding text', 'in-memory only', scenario_c3),
    Scenario('C4', 'C', 'negative', 'event barrier missing',
             'a turn whose state change is emitted before the commit',
             'the commit barrier is bypassed',
             'cross-module: the ordering violation is reported',
             'event list and finding text', 'in-memory only', scenario_c4),
    Scenario('C5', 'C', 'negative', 'non-assertion exception in the test runner',
             'a synthetic unittest module with one passing, one crashing and one later test',
             'test_second_raises raises ZeroDivisionError instead of failing an assertion',
             'runner: the crash is recorded as an error, the traceback is kept, later tests run',
             'testsRun / errors / failures counts, preserved traceback, execution order',
             'in-memory only', scenario_c5),
    Scenario('C6', 'C', 'positive', 'guarded shared state with an exact recovery',
             'a synthetic data tree, a shared manifest and a lock file in one sandbox',
             'none (positive control)',
             'shared state: every call site is guarded, the rollback matches the baseline tree, '
             'the lock is released and the directory scan reports no finding',
             'manifest counters, tree digest vs baseline, lock state, scan report',
             'synthetic sandbox removed with the scenario', scenario_c6),
    Scenario('C7', 'C', 'negative', 'one shared-state call site left unguarded',
             'a shared manifest with two writers, the second one interleaved inside the '
             'first read-modify-write window',
             'the autosave call site runs the cycle without taking the lock',
             'shared state: the inventory names the unguarded call site and the final counter '
             'shows a lost update; the guarded rerun keeps both',
             'call-site inventory, final count and writer list before/after the fix',
             'synthetic sandbox removed with the scenario', scenario_c7),
    Scenario('C8', 'C', 'negative', 'rollback leaves files the backup never knew',
             'a synthetic data tree with a snapshot taken before the change',
             'the rollback restores the remembered files and keeps the newly created ones',
             'recovery: the residue is reported and the tree still differs from the baseline; '
             'the exact restore then matches it',
             'residue list, tree comparison before/after the exact rollback',
             'synthetic sandbox removed with the scenario', scenario_c8),
    Scenario('C9', 'C', 'negative', 'business rollback deletes the lock file',
             'a data tree plus a lock file created after the whole-root snapshot',
             'the rollback restores the whole root, removing the live lock',
             'lock lifecycle: the second writer acquires the orphaned lock; the data-only '
             'rollback keeps the lock and still refuses the second writer',
             'lock presence, second-writer acquisition, LOCK_HELD refusal, tree digest',
             'synthetic sandbox removed with the scenario', scenario_c9),
    Scenario('C10', 'C', 'negative', 'partial commit reported as a complete rollback',
             'a three-stage multi-file commit that fails at the second stage',
             'the report claims rollback COMPLETE although stage 1 is still on disk',
             'staged failure: the honest report names the failed stage, the completed stage, '
             'the side effects and the backup, and the false claim is contradicted by the tree',
             'stage report fields, remaining files in the data tree',
             'synthetic sandbox removed with the scenario', scenario_c10),
    Scenario('C11', 'C', 'negative', 'directory read error swallowed',
             'a directory holding two readable files and one entry that cannot be read as a file',
             'the scan skips the unreadable entry and still reports success',
             'error semantics: the success report is contradicted by processed != total; the '
             'corrected scan reports FAILED and names the entry',
             'scan report before/after the fix, skipped and error lists',
             'synthetic sandbox removed with the scenario', scenario_c11),
    Scenario('C12', 'C', 'positive', 'clean architecture contract with honest coverage',
             'a synthetic tree with domain, application, adapter and two transport entry points',
             'none (positive control)',
             'architecture contract: ENFORCE, no error, scope reported, both transports agree, '
             'mock evidence never labelled real, capability claims bound to a probe',
             'JSON report (status, counts, examined modules, invariant coverage), shared '
             'contract test result, terminal-state matrix, capability findings',
             'synthetic sandbox removed with the scenario', scenario_c12),
    Scenario('C13', 'C', 'negative', 'core module imports an adapter private symbol',
             'the application service importing the adapter runtime session module',
             'the adapter private path leaks into the core through an import',
             'PRIVATE_ACCESS: blocking under ENFORCE, located at rule / module / file / line, '
             'and the bound invariant follows the rule',
             'report blocking flag, PRIVATE_ACCESS finding, invariant coverage',
             'synthetic sandbox removed with the scenario', scenario_c13),
    Scenario('C14', 'C', 'negative', 'two transports implement forked business rules',
             'one transport delegating to the application service, one re-implementing the rule',
             'inline duplication that no import edge reveals, plus the same fork as a direct '
             'domain import',
             'FORBIDDEN_EDGE blocks the structural fork; the shared contract test catches the '
             'inline one; the invariant declared CONTRACT_TEST stays UNKNOWN',
             'static blocking flag per variant, behaviour differences, invariant coverage',
             'synthetic sandbox removed with the scenario', scenario_c14),
    Scenario('C15', 'C', 'negative', 'mock adapter result reported as real verification',
             'a passing shared adapter contract suite executed against a mock implementation',
             'the run is labelled REAL_RUNTIME although the evidence kind is MOCK',
             'evidence labelling: the over-claim is rejected and the mock run stays UNKNOWN '
             'for real-runtime verification',
             'claim findings, verification status per evidence kind',
             'synthetic sandbox removed with the scenario', scenario_c15),
    Scenario('C16', 'C', 'negative', 'cancel, timeout, failure and unknown collapsed',
             'four non-success dispatch terminal states recorded through one generic error',
             'one error code covers failure, cancellation, timeout and "unknown", and unknown '
             'is rewritten as a failure',
             'terminal-state contract: collapsed codes are reported, "unknown" never becomes '
             'success, and the distinct variants keep one code per state',
             'terminal findings, code sets before/after the fix',
             'synthetic sandbox removed with the scenario', scenario_c16),
    Scenario('C17', 'C', 'negative', 'capability marked PASS without evidence',
             'capability records for execution isolation and cancellation',
             'PASS with no probe, with "startup succeeded", with a wildcard scope, or with a '
             'probe that covers a different capability',
             'capability claim gate: every over-claim is rejected, an honest UNKNOWN claim is '
             'accepted and never upgraded',
             'capability findings per record',
             'synthetic sandbox removed with the scenario', scenario_c17),
    Scenario('C18', 'C', 'positive', 'unconfigured project is not judged by inferred rules',
             'the same violating tree without any Architecture Contract',
             'none (positive control): nothing may be guessed from the code alone',
             'contract optionality: OFF / diagnostic only, exit 0, no ERROR or WARNING, no '
             'invariant and no examined module claimed; declaring the contract then blocks',
             'report mode, status, counts, inventory, and the contrast after declaring',
             'synthetic sandbox removed with the scenario', scenario_c18),
    Scenario('D1', 'D', 'positive', 'legal edition with a declared resource',
             'a local project with resource_lease_ttl configured',
             'none (positive control)',
             'edition + resource lock: legal dispatch acquires, VERIFIED releases',
             'edition value, lock rows before/after closeout', 'sandbox removed', scenario_d1),
    Scenario('D2', 'D', 'negative', 'illegal, missing or duplicated edition',
             'four project roots with a missing file, a missing key, an illegal value and a duplicate key',
             'each root declares an unusable edition; one declares github',
             'edition: every case fails closed and names the file to fix; github refuses local scheduling',
             'four refusal messages', 'sandbox removed', scenario_d2),
    Scenario('D3', 'D', 'negative', 'conflicting exclusive resource lock',
             'two tasks with disjoint paths declaring the same exclusive resource',
             'the second task requests the resource the first one holds',
             'resource lock: MODE_CONFLICT denial, no half lock, audit event kept',
             'refusal message, lock counts, denial event count, task status',
             'sandbox removed', scenario_d3),
    Scenario('D4', 'D', 'negative', 'new head invalidates the old approval',
             'a task approved at head aaaa, then a workspace update to another head',
             'the approval is reused for a head that was never reviewed',
             'G3/G4: approval is cleared, merge and in-place re-approval are refused',
             'status, approved_sha, refusal messages', 'sandbox removed', scenario_d4),
    Scenario('D5', 'D', 'negative', 'review-fix rounds beyond the cap',
             'a task that went through three review-fix rounds',
             'a fourth round is requested instead of blocking',
             'state machine: the fourth round becomes BLOCKED with the cap in the note',
             'status, retry counter, block note', 'sandbox removed', scenario_d5),
    Scenario('D6', 'D', 'negative', 'audit closeout with a blocking verdict',
             'a filled milestone-audit conclusion table',
             'one row is BLOCKING and one is NEEDS_HUMAN_DECISION',
             'milestone audit: a clean table passes; blocking verdicts forbid the closeout claim',
             'findings for the clean and the two blocking tables', 'in-memory only', scenario_d6),
    Scenario('D7', 'D', 'negative', 'unbacked result claim',
             'a packet whose AC is checked and whose evidence matrix is empty',
             'the executor claims completion without any evidence item',
             'G1/G6: empty matrix, unbacked AC and direct VERIFIED are all refused',
             'validator findings, refusal message, lock count', 'sandbox removed', scenario_d7),
    Scenario('A8', 'A', 'positive', 'synthetic source evidence becomes a reviewable task',
             'one synthetic merged pull request, one synthetic commit and one CI run',
             'none (positive control)',
             'self-improvement: the lesson is verified, reclassified and generalized before '
             'planning; only READY reaches the carrier, which reports executed=False',
             'scan status / evidence count, lesson id and evidence URLs, proposal status, '
             'registration payload with the evidence-matrix header',
             'in-memory source stand-in; scenario sandbox removed', scenario_a8),
    Scenario('A9', 'A', 'negative', 'source unreachable or forbidden',
             'a registered public source whose pulls endpoint fails with a network error '
             'and with 403; one private source with the same 403',
             'the source cannot be read, so the scan has no evidence',
             'source trust: BLOCKED with the reason preserved; the loop reports '
             'SCAN_INCOMPLETE; a private source escalates to NEEDS_HUMAN_DECISION; no lesson '
             'and no registration',
             'scan status / blocked_reason per case, loop status, decision list, lesson count',
             'in-memory source stand-in; scenario sandbox removed', scenario_a9),
    Scenario('A10', 'A', 'negative', 'prompt injection in source text',
             'a synthetic pull-request title carrying imperative instruction shapes',
             'the source text asks to ignore rules and to send a token',
             'untrusted data: the shapes are counted and the URL flagged, the text is kept as '
             'data, the analyst title and the declared allowed paths are unchanged, and no '
             'task is produced',
             'instruction flags, suspicious URLs, lesson title, proposal allowed paths, '
             'data_cell output', 'in-memory source stand-in; sandbox removed', scenario_a10),
    Scenario('A11', 'A', 'negative', 'the same lesson seen through two evidence URLs',
             'two synthetic merged pull requests analysed with identical root cause / impact',
             'the second evidence item would create a second lesson for one fingerprint',
             'dedup: the second ingestion is reported duplicate, one record survives with both '
             'evidence URLs, and the unverified record cannot be planned',
             'ingestion kinds, surviving lesson id and evidence URLs, duplicate_of, plan status',
             'in-memory source stand-in; sandbox removed', scenario_a11),
    Scenario('A12', 'A', 'negative', 'open conflict between two lessons',
             'a generalized lesson carrying one OPEN CONTRADICTS conflict',
             'the conflict is ignored while an incomplete packet is planned',
             'planning: HD-04 NEEDS_HUMAN_DECISION is raised before the packet check; the loop '
             'stops with NEEDS_HUMAN_DECISION and registers nothing',
             'proposal status / decision id / reason, loop status, decision list, registrations',
             'in-memory records only; sandbox removed', scenario_a12),
    Scenario('A13', 'A', 'negative', 'unverified defect treated as a task',
             'a synthetic defect captured at E1 source-artifact evidence and OBSERVED status',
             'an unverified experience is planned into an implementation task',
             'lesson lifecycle: can_generate_task refuses it, planning returns NO_TASK, and the '
             'state machine refuses OBSERVED -> GENERALIZED as skipped verification',
             'eligibility reason, proposal status, state-machine refusal text',
             'in-memory records only; sandbox removed', scenario_a13),
    Scenario('A14', 'A', 'negative', 'high-risk surface left unresolved',
             'a generalized lesson whose spec would edit AGENTS.md',
             'a governance surface is treated as a routine improvement task',
             'risk gate: HD-05 NEEDS_HUMAN_DECISION, risk_level HIGH with a locatable reason; '
             'the loop registers nothing',
             'proposal status / decision id / risk reasons, loop status, registrations',
             'in-memory records only; sandbox removed', scenario_a14),
    Scenario('A15', 'A', 'positive', 'self-improvement loop end to end',
             'a synthetic source scan, one lesson driven to GENERALIZED and a synthetic carrier',
             'none (positive control)',
             'loop: trigger routed, state restored, scan classified, plan gated, one task '
             'registered with executed=False, one executed check recorded PASS and one '
             'unexecuted command NOT_RUN, closure report written',
             'trigger / state / scan / plan / registration / validation / not_run fields, '
             'closure markdown sections', 'in-memory carrier; sandbox removed', scenario_a15),
    Scenario('B5', 'B', 'positive', 'clean older install upgrades end to end',
             'a synthetic v1.5 project with a baseline manifest and a v1.6 release zip',
             'none (positive control)',
             'safe update: every package check passes, bytes and manifest are applied, the '
             'project-owned files are untouched, the backup verifies, and an independent '
             'post-apply regression finds nothing',
             'report result / versions / 10 check results / backup / rollback, post-apply '
             'regression findings, command exit codes',
             'synthetic tree and zip in the sandbox, removed with it', scenario_b5),
    Scenario('B6', 'B', 'negative', 'two-sided edit of one file',
             'a clean upgrade where AGENTS.md was edited locally before the update',
             'local and upstream both changed the same path',
             'three-way classification: STOPPED_CONFLICT, class THREE_WAY_CONFLICT, zero writes, '
             'HD-10 raised',
             'report result, AGENTS.md classification entry, decision ids, project snapshot',
             'sandbox removed', scenario_b6),
    Scenario('B7', 'B', 'negative', 'structurally invalid package manifest',
             'a release zip whose MANIFEST.json declares schema_version 2',
             'an unsupported manifest schema is accepted by the verifier',
             'package verification: STOPPED_UNSAFE_PACKAGE at MANIFEST_SCHEMA, later checks '
             'NOT_RUN, zero writes',
             'report result, check results, project snapshot', 'sandbox removed', scenario_b7),
    Scenario('B8', 'B', 'negative', 'path traversal member in the release zip',
             'a release zip carrying agentorch-<date>/../escape.txt',
             'an archive member attempts to escape the extraction root',
             'package verification: STOPPED_UNSAFE_PACKAGE at MEMBER_PATHS; nothing is created '
             'outside or inside the project',
             'report result, MEMBER_PATHS result, filesystem checks, project snapshot',
             'sandbox removed (including the would-be escape path)', scenario_b8),
    Scenario('B9', 'B', 'negative', 'local file unknown to both manifests',
             'a local scripts/local-tool.py plus a clean upgrade package',
             'a file the kit never shipped is treated as an upstream path',
             'classification: the update still applies, the file is UNKNOWN_FILE / KEPT_LOCAL '
             'and its bytes are unchanged',
             'report result, classification entry, local file bytes', 'sandbox removed',
             scenario_b9),
    Scenario('B10', 'B', 'negative', 'upstream removal of a shipped file',
             'a target package that no longer ships templates/task-packet.md',
             'the updater deletes a file that disappeared upstream',
             'classification: STOPPED_CONFLICT, UPSTREAM_REMOVAL / KEPT_LOCAL, file kept, HD-09',
             'report result, classification entry, decision ids, project snapshot',
             'sandbox removed', scenario_b10),
    Scenario('B11', 'B', 'negative', 'old install without a baseline manifest',
             'a synthetic old project with no MANIFEST.json',
             'the updater guesses a baseline instead of stopping',
             'baseline gate: STOPPED_INVALID_BASELINE with a read-only inventory note, zero '
             'fetch / download calls, zero writes',
             'report result / baseline status / notes, source call counts, project snapshot',
             'sandbox removed', scenario_b11),
    Scenario('B12', 'B', 'negative', 'uncommitted work in the target tree',
             'a git-backed synthetic project with one uncommitted edit',
             'the updater stashes or rewrites the dirty working tree',
             'workspace isolation: APPLIED in an isolated worktree with the original tree '
             'untouched and an empty stash list; without isolation STOPPED_DIRTY_WORKTREE + HD-12',
             'workspace action / dirty paths / worktree bytes, head SHA, stash list, second '
             'run result and decisions', 'sandbox (including worktrees) removed', scenario_b12),
    Scenario('B13', 'B', 'negative', 'update fails half way through the writes',
             'a clean upgrade with a fault injected after two written files',
             'a partial write would be reported as a completed update',
             'staged failure: ROLLED_BACK, rollback SUCCEEDED, backup verified, exact baseline '
             'restored, rollback verification PASS',
             'report result, rollback fields, post-apply checks, project snapshot',
             'sandbox removed', scenario_b13),
    Scenario('B14', 'B', 'negative', 'rollback verification fails',
             'the same half-way write failure, with the restored file corrupted afterwards',
             'a failed rollback is reported as a clean update',
             'rollback honesty: BLOCKED, rollback FAILED with evidence, HD-11 raised, '
             'rollback_verification FAIL',
             'report result, rollback result / evidence, decision ids, post-apply checks',
             'sandbox removed', scenario_b14),
    Scenario('C19', 'C', 'positive', 'declared allowed dependency passes',
             'two synthetic modules, api -> core declared allowed, one reverse edge forbidden',
             'none (positive control)',
             'dependency direction: ENFORCE with no ERROR, the allowed edge observed, both '
             'rules PASS and nothing blocked',
             'JSON report status / counts / edges / rule statuses',
             'synthetic sandbox removed with the scenario', scenario_c19),
    Scenario('C20', 'C', 'negative', 'two modules import each other',
             'module a imports b and module b imports a',
             'a dependency cycle is treated as acceptable structure',
             'NO_CYCLE: blocking ERROR naming both modules, the cycle path and a located file',
             'report blocking / counts, cycle finding modules / message / file / line',
             'synthetic sandbox removed with the scenario', scenario_c20),
    Scenario('D8', 'D', 'positive', 'metric report carries window, sample and missing',
             'the fixed gold sample embedded in templates/metrics.md',
             'none (positive control)',
             'metric honesty: seven metrics, every row has sample / eligible / missing / '
             'missing_rate / coverage, the window is explicit, and no coupling or cohesion '
             'score is invented',
             'metric rows, window, summary coverage, rendered human output, banned score terms',
             'in-memory dataset only', scenario_d8),
    Scenario('D9', 'D', 'negative', 'empty metric input and a missing end event',
             'an empty event file and a task with READY but no VERIFIED transition',
             'no data is reported as zero, and a missing event becomes a zero-hour duration',
             'metric honesty: every metric is no_data with value null and coverage 0.0; the '
             'READY-only task makes M1 partial with sample 0 / missing 1 and a null value',
             'metric statuses / values / coverage, headline, M1 detail',
             'temporary event files inside the scenario sandbox, removed with it', scenario_d9),
    Scenario('E1', 'E', 'positive', 'role selection end to end, never a dispatch',
             'one initialized scope whose agent-registry lists a Master, an Execution and an '
             'independent Reviewer, plus a fresh synthetic model snapshot',
             'none (positive control)',
             'S1-S4 composed: C1-C4 PASS, scope isolation, registry discovery, hard filters '
             'before external ranking, Reviewer independence, advisory local feedback; the '
             'report keeps executed=False and the selection is not a dispatch',
             'gate / scope id, discovery outcome and candidates, selected agents and models, '
             'source status / freshness / index version, feedback metric with sample and '
             'coverage, selection-history record with executed=false, scope directory list',
             'temporary project root and scope directory removed with the sandbox', scenario_e1),
    Scenario('E2', 'E', 'negative', 'runtime cannot spawn (C1 UNKNOWN, PARTIAL record)',
             'a PARTIAL capability record whose C1 is UNKNOWN',
             'the selector treats an unprobed spawn capability as usable',
             'C1-C4 gate: the callable filter stays UNKNOWN, no role is selected, the task '
             'is handed off and automatic dispatch stays closed; no spawn API exists',
             'gate status / reasons, execution selected / handoff, callable filter, degradation',
             'synthetic candidates only; sandbox removed', scenario_e2),
    Scenario('E3', 'E', 'negative', 'C1-C4 failure and stale capability evidence',
             'one record with C4 FAIL and one whose probe is older than the freshness window',
             'a failed or expired capability unlocks automatic dispatch',
             'C1-C4 gate: BLOCKED for both cases (the stale one flags stale), no role is '
             'selected and automatic dispatch stays closed',
             'gate status / stale flag / reasons, selection results, automatic flag',
             'synthetic candidates only; sandbox removed', scenario_e3),
    Scenario('E4', 'E', 'negative', 'no qualified Execution Agent',
             'discovery returns a Master and a Reviewer but no Execution candidate',
             'the selector invents or auto-dispatches an Execution role',
             'role matching: Execution selected=None, manual handoff with a registration '
             'reminder, the task stays undispatched and automatic dispatch is closed',
             'execution selection, reminder text, degradation list', 'sandbox removed', scenario_e4),
    Scenario('E5', 'E', 'negative', 'no independent Reviewer',
             'discovery returns a Master and an Execution candidate only',
             'Execution self-review substitutes for an independent Reviewer',
             'role matching: Reviewer selected=None, handoff, reminder states self-review is '
             'not accepted, the task stays unreviewed and automatic dispatch is closed',
             'reviewer selection / reminder, degradation list', 'sandbox removed', scenario_e5),
    Scenario('E6', 'E', 'negative', 'Reviewer shares the Execution model id',
             'a Reviewer candidate whose model id equals the selected Execution model',
             'a high external score makes the same underlying model "independent"',
             'independence hard filter: FAIL on the shared model id, no Reviewer selected, '
             'Reviewer dimensions stay UNKNOWN and automatic selection is withheld',
             'excluded candidate filters, independence detail, assessment signals, automatic flag',
             'synthetic snapshot only; sandbox removed', scenario_e6),
    Scenario('E7', 'E', 'negative', 'Master cannot accept image input',
             'a text-only Master candidate with otherwise valid evidence',
             'a text-only Master satisfies the multi-modal hard requirement',
             'modality hard filter: FAIL naming the missing image input, no Master selected, '
             'automatic dispatch closed',
             'modality filter status / detail, master selection, degradation', 'sandbox removed',
             scenario_e7),
    Scenario('E8', 'E', 'negative', 'model source unavailable or cache expired',
             'an AUTH_FAILED fetch and an expired STALE_CACHE batch over the same candidates',
             'a failed or expired source is reused as current evidence, or blocks governance',
             'source honesty: assessments UNLINKED / STALE with UNKNOWN grades, S2 selection '
             'still stands, a fresh-source policy withholds automatic selection',
             'fetch status / live / freshness, assessments, degradations, policy decisions',
             'in-memory batches only; sandbox removed', scenario_e8),
    Scenario('E9', 'E', 'negative', 'candidate exceeds the task budget',
             'an Execution candidate costing more than the task cost limit',
             'a strong candidate is selected despite the hard budget limit',
             'budget hard filter: FAIL with the limit and the observed cost, no Execution '
             'selected, automatic dispatch closed',
             'budget filter detail, selection result, automatic flag', 'sandbox removed',
             scenario_e9),
    Scenario('E10', 'E', 'negative', 'scope cannot be identified / cross-scope record',
             'a project root with no session, TTY or namespace; then two distinct scopes',
             'the resolver falls back to a shared directory, or a record crosses scopes',
             'scope isolation: the resolver fails closed with no directory created; a '
             'cross-scope outcome is refused and neither store receives a record',
             'resolution fields, open_store refusal, scope ids, per-scope record counts, '
             'directory list', 'temporary project root removed with the sandbox', scenario_e10),
    Scenario('E11', 'E', 'negative', 'local Markdown write fails',
             'one initialized scope with the atomic replace failing',
             'a failed feedback / selection write is reported as a complete audit trail',
             'write honesty: FeedbackWriteError says the record was not written and both '
             'documents keep zero records',
             'both refusal messages, record counts', 'sandbox removed', scenario_e11),
    Scenario('E12', 'E', 'negative', 'zero qualified candidates',
             'one candidate that fails tools / permissions, plus an empty discovery',
             'some role is silently chosen from no qualified candidate',
             'role matching: every role selected=None with handoff, the report says no '
             'candidate passed every hard filter, automatic dispatch is closed',
             'three role selections, reasons, degradations, empty-discovery outcome',
             'synthetic candidates only; sandbox removed', scenario_e12),
    Scenario('E13', 'E', 'negative', 'combined worst case stops the selection',
             'no spawn + no Reviewer + stale model data + cross-scope attempt + write failure',
             'partial evidence is combined into a "dispatched / passed" conclusion',
             'the selector stops: Execution and Reviewer stay None with handoff, degradations '
             'name the missing Execution / Reviewer and the stale source, the cross-scope '
             'record is refused and the failed write is reported as not written',
             'degradation list, role selections, scope error, record counts, executed flag',
             'temporary project root and scope directory removed with the sandbox', scenario_e13),
)


def scenario_ids():
    return [scenario.id for scenario in SCENARIOS]


def select_scenarios(only=None):
    if not only:
        return list(SCENARIOS)
    wanted = {item.strip() for item in only if item.strip()}
    known = set(scenario_ids())
    unknown = sorted(wanted - known)
    if unknown:
        raise ToolError(f'unknown scenario id(s): {", ".join(unknown)}; '
                        f'known ids: {", ".join(scenario_ids())}')
    return [scenario for scenario in SCENARIOS if scenario.id in wanted]


# --------------------------------------------------------------------------- #
# Execution and reporting
# --------------------------------------------------------------------------- #
def _redact(text, sandbox):
    """Keep the report free of this machine's temporary paths."""
    text = '' if text is None else str(text)
    for raw in {str(sandbox), str(sandbox).replace('\\', '/')}:
        text = text.replace(raw, '<sandbox>')
    return text


def _remove_sandbox(path):
    """Remove a scenario sandbox, including read-only files a Windows git repo leaves.

    ``git init`` / ``git worktree add`` mark objects read-only on Windows, and
    ``shutil.rmtree`` cannot unlink a read-only file there. Granting write access
    while walking the tree is the portable way to make the removal succeed; on
    POSIX the mode bits are equally harmless. Cleanup never raises: a leftover
    sandbox is reported by the tests that assert an empty work directory.
    """
    for dirpath, dirnames, filenames in os.walk(path):
        for name in dirnames + filenames:
            try:
                os.chmod(os.path.join(dirpath, name), 0o700)
            except OSError:
                pass
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    shutil.rmtree(path, ignore_errors=True)


def run_scenarios(scenarios, workdir, vk=None, orch=None, emit=None):
    """Run every scenario; a failure or crash never stops the remaining ones."""
    results = []
    for index, scenario in enumerate(scenarios, 1):
        sandbox = Path(workdir) / f'{index:02d}-{scenario.id}'
        sandbox.mkdir(parents=True, exist_ok=True)
        context = Context(vk=vk, orch=orch, sandbox=sandbox)
        try:
            evidence = scenario.fn(context) or []
            if not isinstance(evidence, list):
                evidence = [str(evidence)]
            result = Result(scenario, 'PASS', [_redact(item, sandbox) for item in evidence], {})
        except ScenarioFailed as exc:
            result = Result(scenario, 'FAIL', [], {
                'type': type(exc).__name__,
                'message': _redact(str(exc), sandbox),
                'traceback': _redact(traceback.format_exc(), sandbox),
            })
        except Exception as exc:  # noqa: BLE001 - recorded, never re-raised
            result = Result(scenario, 'ERROR', [], {
                'type': type(exc).__name__,
                'message': _redact(str(exc), sandbox),
                'traceback': _redact(traceback.format_exc(), sandbox),
            })
        finally:
            _remove_sandbox(sandbox)
        results.append(result)
        if emit is not None:
            emit(result)
    return results


def build_report(results, started_at=None):
    counts = {'scenarios': len(results), 'passed': 0, 'failed': 0, 'errors': 0,
              'positive': 0, 'negative': 0, 'positive_passed': 0, 'negative_passed': 0}
    for result in results:
        scenario = result.scenario
        counts['positive' if scenario.kind == 'positive' else 'negative'] += 1
        if result.status == 'PASS':
            counts['passed'] += 1
            counts[f'{"positive" if scenario.kind == "positive" else "negative"}_passed'] += 1
        elif result.status == 'FAIL':
            counts['failed'] += 1
        else:
            counts['errors'] += 1
    groups = {}
    for key, label in GROUPS.items():
        in_group = [r for r in results if r.scenario.group == key]
        groups[key] = {'label': label, 'scenarios': len(in_group),
                       'passed': sum(1 for r in in_group if r.status == 'PASS')}
    return {
        'schema': REPORT_SCHEMA,
        'generated_at': started_at or datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'python': platform.python_version(),
        'platform': platform.platform(),
        'scenario_doc': SCENARIO_DOC,
        'totals': counts,
        'groups': groups,
        'scenarios': [{
            'id': r.scenario.id,
            'group': r.scenario.group,
            'kind': r.scenario.kind,
            'title': r.scenario.title,
            'status': r.status,
            'precondition': r.scenario.precondition,
            'injected_defect': r.scenario.defect,
            'expected_gate': r.scenario.gate,
            'evidence_declared': r.scenario.evidence,
            'cleanup': r.scenario.cleanup,
            'evidence': list(r.evidence),
            'error': r.error or None,
        } for r in results],
    }


def format_human(report):
    totals = report['totals']
    lines = ['Synthetic delivery regression scenarios',
             f'  {totals["scenarios"]} scenarios: {totals["positive"]} positive control(s), '
             f'{totals["negative"]} injected-defect scenario(s)']
    for key in GROUPS:
        group = report['groups'][key]
        lines.append(f'  {key} {group["label"]}: {group["passed"]}/{group["scenarios"]} passed')
    lines.append('')
    for item in report['scenarios']:
        marker = {'PASS': 'PASS ', 'FAIL': 'FAIL ', 'ERROR': 'ERROR'}[item['status']]
        lines.append(f'{marker} {item["id"]}  {item["title"]}')
        if item['status'] == 'PASS':
            for evidence in item['evidence']:
                lines.append(f'       - {evidence}')
        else:
            lines.append(f'       gate: {item["expected_gate"]}')
            lines.append(f'       {item["error"]["type"]}: {item["error"]["message"]}')
    lines.append('')
    lines.append(f'Summary: {totals["passed"]} passed, {totals["failed"]} failed, '
                 f'{totals["errors"]} errored of {totals["scenarios"]}')
    lines.append('Exit code: ' + ('0 (all scenarios passed)'
                                  if totals['passed'] == totals['scenarios']
                                  else '1 (at least one scenario failed or errored)'))
    return '\n'.join(lines)


def _exit_code(results):
    return EXIT_PASS if all(result.status == 'PASS' for result in results) else EXIT_SCENARIO_FAILURES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--json', action='store_true',
                        help='print the machine-readable report instead of the human summary')
    parser.add_argument('--output', metavar='PATH',
                        help='also write the JSON report to PATH (never written into the kit)')
    parser.add_argument('--only', action='append', metavar='ID',
                        help='run only the given scenario id (repeatable)')
    parser.add_argument('--list', action='store_true', help='list scenario ids and exit')
    args = parser.parse_args(argv)

    if args.list:
        for scenario in SCENARIOS:
            print(f'{scenario.id}\t{scenario.group}\t{scenario.kind}\t{scenario.title}')
        return EXIT_PASS

    try:
        selected = select_scenarios(args.only)
        vk = _load_module('agentorch_validate_kit', SCRIPTS / 'validate_kit.py')
        orch = _load_module('agentorch_orchestrator', SCRIPTS / 'orchestrator.py')
    except ToolError as exc:
        print(f'RUNNER ERROR: {exc}', file=sys.stderr)
        return EXIT_TOOL_ERROR

    # `mkdtemp` + `_remove_sandbox` instead of `TemporaryDirectory`: the git-backed
    # scenarios leave read-only object files on Windows, which need the same
    # explicit permission fix the per-scenario cleanup applies.
    workdir = Path(tempfile.mkdtemp(prefix='agentorch-delivery-scenarios-'))
    try:
        results = run_scenarios(selected, workdir, vk=vk, orch=orch)
    finally:
        _remove_sandbox(workdir)

    report = build_report(results)
    if args.output:
        try:
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                                         encoding='utf-8')
        except OSError as exc:
            print(f'RUNNER ERROR: cannot write --output {args.output}: {exc}', file=sys.stderr)
            return EXIT_TOOL_ERROR

    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_human(report))
    return _exit_code(results)


if __name__ == '__main__':
    sys.exit(main())
