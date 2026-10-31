"""Read-only task topology recommender (Issue #178, Stage B observe-only).

Decision order and concept ownership: ``docs/design/task-driven-agent-topology.md``
§2 (frozen by #177). T0–T4 and this CLI remain design proposals under Stage B;
this module is only a read-only classifier. It consumes one caller snapshot,
returns candidate topologies with hard-filter reasons, and never executes a
step, creates an instance, writes a file, contacts the network or changes task
state.

Input contract: ``topology-planner-input/1`` (see :func:`parse_task`).
Output contract: ``topology-planner-output/1`` or
``topology-planner-diagnostic/1``. Every document carries
``plan_usability: not_a_dispatch`` and ``dispatched: false``: a recommendation
is not a dispatch, an approval or a ``VERIFIED`` signal.

Exit codes (set by :func:`build`): 2 for a structural / usage diagnostic, 3 for
a plan (including ``BLOCKED`` and ``MANUAL_HANDOFF``). Exit code 0 is unused on
purpose.

Only the Python standard library is used. Path normalisation mirrors
``scripts/orchestrator.py`` semantics but is reimplemented here so this tool
never imports the scheduler.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

SCHEMA_VERSION = "topology-planner-input/1"
OUTPUT_SCHEMA_VERSION = "topology-planner-output/1"
DIAGNOSTIC_SCHEMA_VERSION = "topology-planner-diagnostic/1"
PLANNER_VERSION = "178-draft-1"

CANDIDATE_IDS = ("T0", "T1", "T2", "T3", "T4")
CAPABILITY_IDS = ("C1", "C2", "C3", "C4")
CAPABILITY_STATES = ("pass", "fail", "unknown", "stale", "not_run")
DEPENDENCY_STATES = ("verified", "not_verified", "unknown")
CONTEXT_LOADS = ("low", "medium", "high")
RISK_LEVELS = ("low", "medium", "high", "unknown")

HIGH_RISK_SIGNALS = (
    "security", "permission_model", "credentials", "data_deletion",
    "migration", "irreversible_action", "state_machine", "lock_semantics",
    "external_service", "release_artifact", "dispatch_semantics",
)
MEDIUM_RISK_SIGNALS = (
    "cross_module", "interface_change", "reviewer_identity", "large_context",
)
RISK_SIGNALS = HIGH_RISK_SIGNALS + MEDIUM_RISK_SIGNALS

# #170 / agent-registry.md identity fields. This planner records claims of this
# shape as caller assertions only; it never verifies them and never promotes a
# family name, alias, registry string or self report into independence evidence.
REVIEWER_CLAIM_KEYS = (
    "model_family", "model_alias", "registry_status",
    "identity_verified_until", "canonical_identity",
)

# A caller snapshot may not pre-set a result, approval or dispatch field.
FORBIDDEN_FIELDS = frozenset({
    "dispatched", "dispatch_ok", "may_dispatch", "attempt_allowed",
    "approved", "verified", "status", "selection_status", "selected_topology",
    "plan_usability", "identity_ok", "reviewer_identity_proven", "skip",
    "override_approver", "waiver", "role_assignments",
})

DIAGNOSTIC_CODES = {
    "schema_invalid", "edition_invalid", "unknown_field", "forbidden_field",
    "path_invalid", "usage",
}

TASK_REF_RE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,200}$")
AC_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ITEM_RE = re.compile(r"^[a-z0-9._-]{1,80}$")
DEP_RE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,200}$")
TOOL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
RESOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,80}$")
REF_RE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,200}$")

REQUIRED_TOP_LEVEL = (
    "schema_version", "task_ref", "edition", "acceptance", "changed_paths",
    "risk", "ambiguities", "dependencies", "context", "tools", "capabilities",
    "reviewer", "implementation", "locks",
)
ALLOWED_TOP_LEVEL = set(REQUIRED_TOP_LEVEL)


class PlannerError(Exception):
    """A structural input error. Carries the diagnostic code and JSON pointer."""

    def __init__(self, code: str, path: str = "$"):
        if code not in DIAGNOSTIC_CODES:
            raise RuntimeError(f"unknown diagnostic code: {code}")
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _pointer(parts):
    out = "$"
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}"
    return out


def _require_object(value, pointer):
    if not isinstance(value, dict):
        raise PlannerError("schema_invalid", _pointer(pointer))
    return value


def _require_list(value, pointer, minimum=0):
    if not isinstance(value, list) or len(value) < minimum:
        raise PlannerError("schema_invalid", _pointer(pointer))
    return value


def _require_bool(value, pointer):
    if not isinstance(value, bool):
        raise PlannerError("schema_invalid", _pointer(pointer))
    return value


def _require_int(value, pointer, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PlannerError("schema_invalid", _pointer(pointer))
    return value


def _require_str(value, pointer, minimum=1, maximum=None, pattern=None):
    if not isinstance(value, str) or len(value) < minimum:
        raise PlannerError("schema_invalid", _pointer(pointer))
    if maximum is not None and len(value) > maximum:
        raise PlannerError("schema_invalid", _pointer(pointer))
    if pattern is not None and not pattern.fullmatch(value):
        raise PlannerError("schema_invalid", _pointer(pointer))
    return value


def _require_exact_keys(obj, keys, pointer):
    missing = [key for key in keys if key not in obj]
    if missing:
        raise PlannerError("schema_invalid", _pointer(pointer + (missing[0],)))
    extra = [key for key in obj if key not in keys]
    if extra:
        raise PlannerError("unknown_field", _pointer(pointer + (extra[0],)))


def _add(items, value):
    if value not in items:
        items.append(value)


def _scan_forbidden(node, pointer=()):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_FIELDS:
                raise PlannerError("forbidden_field", _pointer(pointer + (key,)))
            _scan_forbidden(value, pointer + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _scan_forbidden(value, pointer + (index,))


def normalize_path(raw, pointer):
    """Mirror scripts/orchestrator.py:normalize_path without importing it."""
    if not isinstance(raw, str):
        raise PlannerError("schema_invalid", _pointer(pointer))
    text = raw.replace("\\", "/").strip()
    raw_parts = text.split("/")
    posix = PurePosixPath(text)
    invalid = (
        not text
        or any(part in (".", "..") for part in raw_parts)
        or posix.is_absolute()
        or re.match(r"^[A-Za-z]:/", text) is not None
        or any(part in ("..", ".") for part in posix.parts)
        or any(char in text for char in "*?[]")
    )
    if invalid:
        raise PlannerError("path_invalid", _pointer(pointer))
    return str(posix).rstrip("/") or "."


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def parse_task(raw):
    """Validate one caller snapshot. Raises :class:`PlannerError` on structure."""
    if not isinstance(raw, dict):
        raise PlannerError("schema_invalid", "$")

    _scan_forbidden(raw)

    for key in raw:
        if key not in ALLOWED_TOP_LEVEL:
            raise PlannerError("unknown_field", _pointer((key,)))

    missing = [key for key in REQUIRED_TOP_LEVEL if key not in raw]
    if missing:
        raise PlannerError("schema_invalid", _pointer((missing[0],)))

    if raw["schema_version"] != SCHEMA_VERSION:
        raise PlannerError("schema_invalid", "$.schema_version")

    task_ref = _require_str(raw["task_ref"], ("task_ref",), maximum=200,
                            pattern=TASK_REF_RE)

    edition = raw["edition"]
    if edition not in ("local", "github"):
        raise PlannerError("edition_invalid", "$.edition")

    acceptance = []
    for index, item in enumerate(_require_list(raw["acceptance"], ("acceptance",), 1)):
        pointer = ("acceptance", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("ac_id", "text"), pointer)
        acceptance.append({
            "ac_id": _require_str(item["ac_id"], pointer + ("ac_id",),
                                  maximum=64, pattern=AC_RE),
            "text": _require_str(item["text"], pointer + ("text",), maximum=500),
        })

    changed_paths = []
    for index, item in enumerate(_require_list(raw["changed_paths"], ("changed_paths",))):
        pointer = ("changed_paths", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("path", "source_ref"), pointer)
        raw_path = item["path"]
        if not isinstance(raw_path, str) or len(raw_path) > 300:
            raise PlannerError("schema_invalid", _pointer(pointer + ("path",)))
        changed_paths.append({
            "path": normalize_path(raw_path, pointer + ("path",)),
            "source_ref": _require_str(item["source_ref"], pointer + ("source_ref",),
                                       maximum=200, pattern=REF_RE),
        })

    risk_raw = _require_object(raw["risk"], ("risk",))
    _require_exact_keys(risk_raw, ("signals", "claimed_level"), ("risk",))
    signals = []
    for index, signal in enumerate(_require_list(risk_raw["signals"], ("risk", "signals"))):
        pointer = ("risk", "signals", index)
        signal = _require_str(signal, pointer, maximum=64, pattern=ITEM_RE)
        if signal not in RISK_SIGNALS:
            raise PlannerError("unknown_field", _pointer(pointer))
        _add(signals, signal)
    claimed_level = risk_raw["claimed_level"]
    if claimed_level is not None and claimed_level not in RISK_LEVELS:
        raise PlannerError("schema_invalid", "$.risk.claimed_level")

    ambiguities = []
    for index, item in enumerate(_require_list(raw["ambiguities"], ("ambiguities",))):
        pointer = ("ambiguities", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("item_id", "source_ref"), pointer)
        ambiguities.append({
            "item_id": _require_str(item["item_id"], pointer + ("item_id",),
                                    maximum=80, pattern=ITEM_RE),
            "source_ref": _require_str(item["source_ref"], pointer + ("source_ref",),
                                       maximum=200, pattern=REF_RE),
        })

    dependencies = []
    for index, item in enumerate(_require_list(raw["dependencies"], ("dependencies",))):
        pointer = ("dependencies", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("dep_id", "state"), pointer)
        state = item["state"]
        if state not in DEPENDENCY_STATES:
            raise PlannerError("schema_invalid", _pointer(pointer + ("state",)))
        dependencies.append({
            "dep_id": _require_str(item["dep_id"], pointer + ("dep_id",),
                                   maximum=200, pattern=DEP_RE),
            "state": state,
        })

    context_raw = _require_object(raw["context"], ("context",))
    context_keys = ("load", "isolation_required", "parallelizable", "subtask_count")
    _require_exact_keys(context_raw, context_keys, ("context",))
    if context_raw["load"] not in CONTEXT_LOADS:
        raise PlannerError("schema_invalid", "$.context.load")
    context = {
        "load": context_raw["load"],
        "isolation_required": _require_bool(context_raw["isolation_required"],
                                            ("context", "isolation_required")),
        "parallelizable": _require_bool(context_raw["parallelizable"],
                                        ("context", "parallelizable")),
        "subtask_count": _require_int(context_raw["subtask_count"],
                                      ("context", "subtask_count")),
    }

    tools_raw = _require_object(raw["tools"], ("tools",))
    _require_exact_keys(tools_raw, ("required", "available"), ("tools",))
    required_tools = []
    for index, tool in enumerate(_require_list(tools_raw["required"], ("tools", "required"))):
        pointer = ("tools", "required", index)
        _add(required_tools, _require_str(tool, pointer, maximum=64, pattern=TOOL_RE))
    available_tools = []
    for index, tool in enumerate(_require_list(tools_raw["available"], ("tools", "available"))):
        pointer = ("tools", "available", index)
        _add(available_tools, _require_str(tool, pointer, maximum=64, pattern=TOOL_RE))

    capabilities_raw = _require_object(raw["capabilities"], ("capabilities",))
    _require_exact_keys(capabilities_raw, CAPABILITY_IDS, ("capabilities",))
    capabilities = {}
    for capability_id in CAPABILITY_IDS:
        state = capabilities_raw[capability_id]
        if state not in CAPABILITY_STATES:
            raise PlannerError("schema_invalid",
                               _pointer(("capabilities", capability_id)))
        capabilities[capability_id] = state

    reviewer_raw = _require_object(raw["reviewer"], ("reviewer",))
    _require_exact_keys(reviewer_raw, ("required", "claims"), ("reviewer",))
    reviewer_required = _require_bool(reviewer_raw["required"], ("reviewer", "required"))
    claims_raw = _require_object(reviewer_raw["claims"], ("reviewer", "claims"))
    claims = {}
    for key, value in claims_raw.items():
        pointer = ("reviewer", "claims", key)
        if key not in REVIEWER_CLAIM_KEYS:
            raise PlannerError("unknown_field", _pointer(pointer))
        claims[key] = _require_str(value, pointer, maximum=200)

    implementation_raw = _require_object(raw["implementation"], ("implementation",))
    _require_exact_keys(implementation_raw, ("required", "master_exception"),
                        ("implementation",))
    implementation = {
        "required": _require_bool(implementation_raw["required"],
                                  ("implementation", "required")),
        "master_exception": _require_bool(implementation_raw["master_exception"],
                                          ("implementation", "master_exception")),
    }

    locks_raw = _require_object(raw["locks"], ("locks",))
    _require_exact_keys(locks_raw, ("path_conflicts", "resource_conflicts"), ("locks",))
    path_conflicts = []
    for index, value in enumerate(_require_list(locks_raw["path_conflicts"],
                                                ("locks", "path_conflicts"))):
        path_conflicts.append(normalize_path(value, ("locks", "path_conflicts", index)))
    resource_conflicts = []
    for index, value in enumerate(_require_list(locks_raw["resource_conflicts"],
                                                ("locks", "resource_conflicts"))):
        pointer = ("locks", "resource_conflicts", index)
        _add(resource_conflicts,
             _require_str(value, pointer, maximum=80, pattern=RESOURCE_RE))

    return {
        "task_ref": task_ref,
        "edition": edition,
        "acceptance": acceptance,
        "changed_paths": changed_paths,
        "signals": signals,
        "claimed_level": claimed_level,
        "ambiguities": ambiguities,
        "dependencies": dependencies,
        "context": context,
        "required_tools": required_tools,
        "available_tools": available_tools,
        "capabilities": capabilities,
        "reviewer_required": reviewer_required,
        "reviewer_claims": claims,
        "implementation": implementation,
        "path_conflicts": path_conflicts,
        "resource_conflicts": resource_conflicts,
    }


# ---------------------------------------------------------------------------
# Risk and independence
# ---------------------------------------------------------------------------

def assess_risk(task):
    """Classify risk. A caller claim can only raise the level, never lower it."""
    reasons = []
    high = False
    medium = False

    for signal in task["signals"]:
        if signal in HIGH_RISK_SIGNALS:
            high = True
        else:
            medium = True
        _add(reasons, f"signal:{signal}")

    if task["ambiguities"]:
        _add(reasons, "ambiguity_declared")
        high = True
    if task["context"]["load"] == "high":
        _add(reasons, "context_load_high")
        medium = True

    level = "high" if high else ("medium" if medium else "low")
    claimed = task["claimed_level"]
    downgrade_attempts = []

    if claimed == "unknown":
        _add(reasons, "claimed_level_unknown")
        if level != "high":
            level = "unknown"
    elif claimed == "high":
        _add(reasons, "claimed_level_high")
        level = "high"
    elif claimed == "medium" and level == "low":
        _add(reasons, "claimed_level_medium")
        level = "medium"
    elif claimed == "low" and level != "low":
        # The caller asserted a lower level than the snapshot supports. Record
        # the attempt and keep the higher level.
        downgrade_attempts.append({
            "claimed": "low",
            "effective": level,
            "reason_codes": ["caller_claim_lower_than_signals"],
        })

    return {
        "level": level,
        "reasons": reasons,
        "claimed_level": claimed,
        "downgrade_attempts": downgrade_attempts,
    }


def assess_independence(task):
    """Reviewer independence. Caller claims are recorded, never trusted."""
    required = task["reviewer_required"]
    claims_rejected = []
    if required:
        for key in REVIEWER_CLAIM_KEYS:
            if key in task["reviewer_claims"]:
                claims_rejected.append({
                    "claim": key,
                    "reason_code": "caller_self_report_not_evidence",
                })
        assessment = "unproven"
    else:
        assessment = "not_required"
    return {
        "required": required,
        "assessment": assessment,
        "verified_by_this_tool": False,
        "claims_rejected": claims_rejected,
    }


# ---------------------------------------------------------------------------
# Candidate construction
# ---------------------------------------------------------------------------

STRUCTURES = {
    "T0": "tool_or_skill_in_main_loop",
    "T1": "sequential_execution_instance",
    "T2": "execution_with_independent_reviewer",
    "T3": "parallel_isolated_executions",
    "T4": "external_or_manual_handoff",
}
ROLES = {
    "T0": ("main_loop", "tool", "skill"),
    "T1": ("execution", "master_review"),
    "T2": ("execution", "independent_reviewer"),
    "T3": ("parallel_execution", "master_review"),
    "T4": ("manual_operator",),
}
TRADEOFFS = {
    "T0": ("lowest_context_cost", "no_isolated_execution_instance"),
    "T1": ("single_execution_instance", "master_review_still_required"),
    "T2": ("independent_reviewer_required", "highest_context_cost"),
    "T3": ("parallel_isolation", "requires_disjoint_paths_and_resources"),
    "T4": ("human_operated_session", "no_runtime_instance_created_by_this_tool"),
}
ROLE_ASSIGNMENTS = {
    "T0": ("main_loop", "tool", "skill"),
    "T1": ("execution", "master_review"),
    "T2": ("execution", "independent_reviewer"),
    "T3": ("parallel_execution", "master_review"),
    "T4": ("manual_operator",),
}


def _constraint(constraint_id, failed_reasons):
    return {
        "constraint_id": constraint_id,
        "result": "fail" if failed_reasons else "pass",
        "reason_codes": list(failed_reasons),
    }


def _not_applicable(constraint_id, reason_codes):
    return {
        "constraint_id": constraint_id,
        "result": "not_applicable",
        "reason_codes": list(reason_codes),
    }


def _candidate(candidate_id, eligible, constraints, excluded_reasons):
    return {
        "id": candidate_id,
        "structure": STRUCTURES[candidate_id],
        "roles": list(ROLES[candidate_id]),
        "eligible": bool(eligible),
        "hard_constraints": constraints,
        "excluded_reasons": list(excluded_reasons),
        "tradeoffs": list(TRADEOFFS[candidate_id]),
    }


def _gaps(task):
    dependency_gaps = []
    for dependency in task["dependencies"]:
        if dependency["state"] == "not_verified":
            _add(dependency_gaps, f"dependency_not_verified:{dependency['dep_id']}")
        elif dependency["state"] == "unknown":
            _add(dependency_gaps, f"dependency_unknown:{dependency['dep_id']}")

    tool_gaps = [
        f"tool_permission_missing:{tool}"
        for tool in task["required_tools"]
        if tool not in task["available_tools"]
    ]
    lock_gaps = [f"path_conflict:{path}" for path in task["path_conflicts"]]
    lock_gaps += [f"resource_conflict:{name}" for name in task["resource_conflicts"]]

    capability_gaps = [
        f"capability_{capability_id}_{state}"
        for capability_id, state in task["capabilities"].items()
        if state != "pass"
    ]

    reviewer_gaps = (["reviewer_identity_unproven"]
                     if task["reviewer_required"] else [])
    implementation_gaps = (
        ["implementation_requires_master_exception"]
        if task["implementation"]["required"]
        and not task["implementation"]["master_exception"]
        else []
    )
    return {
        "dependency": dependency_gaps,
        "tool": tool_gaps,
        "lock": lock_gaps,
        "capability": capability_gaps,
        "reviewer": reviewer_gaps,
        "implementation": implementation_gaps,
    }


def build_candidates(task, risk, gaps):
    shared = [
        _constraint("G-DEPENDENCIES", gaps["dependency"]),
        _constraint("G-TOOLS", gaps["tool"]),
        _constraint("G-LOCKS", gaps["lock"]),
    ]
    shared_ok = not (gaps["dependency"] or gaps["tool"] or gaps["lock"])
    capabilities_ok = not gaps["capability"]
    reviewer_required = task["reviewer_required"]
    isolation = task["context"]["isolation_required"]
    load_high = task["context"]["load"] == "high"
    parallel = (task["context"]["parallelizable"]
                and task["context"]["subtask_count"] >= 2)
    high_risk = risk["level"] in ("high", "unknown")
    ambiguity = bool(task["ambiguities"])

    t0_constraints = list(shared) + [
        _constraint("T0-IMPLEMENTATION-AUTHORITY", gaps["implementation"]),
        _constraint("T0-REVIEW-SCOPE",
                    ["reviewer_required"] if reviewer_required else []),
        _constraint("T0-ISOLATION-SCOPE",
                    ["isolation_required"] if isolation else []),
        _constraint("T0-CONTEXT-SCOPE",
                    ["context_load_high"] if load_high else []),
    ]
    t0_excluded = list(gaps["dependency"]) + list(gaps["tool"]) + list(gaps["lock"])
    t0_excluded += list(gaps["implementation"])
    if reviewer_required:
        _add(t0_excluded, "reviewer_required")
    if isolation:
        _add(t0_excluded, "isolation_required")
    if load_high:
        _add(t0_excluded, "context_load_high")
    t0_eligible = (shared_ok and not gaps["implementation"]
                   and not reviewer_required and not isolation and not load_high)

    t1_constraints = list(shared) + [
        _constraint("G-CAPABILITIES", gaps["capability"]),
        _constraint("T1-REVIEW-SCOPE",
                    ["reviewer_required"] if reviewer_required else []),
    ]
    t1_excluded = list(gaps["dependency"]) + list(gaps["tool"]) + list(gaps["lock"])
    t1_excluded += list(gaps["capability"])
    if reviewer_required:
        _add(t1_excluded, "reviewer_required")
    t1_eligible = shared_ok and capabilities_ok and not reviewer_required

    t2_constraints = list(shared) + [
        _constraint("G-CAPABILITIES", gaps["capability"]),
        _not_applicable("T2-REVIEW-SCOPE", ["reviewer_not_required"])
        if not reviewer_required else _constraint("T2-REVIEW-SCOPE", []),
        _constraint("T2-INDEPENDENCE", gaps["reviewer"]),
    ]
    t2_excluded = list(gaps["dependency"]) + list(gaps["tool"]) + list(gaps["lock"])
    t2_excluded += list(gaps["capability"])
    if not reviewer_required:
        _add(t2_excluded, "reviewer_not_required")
    if reviewer_required:
        _add(t2_excluded, "reviewer_identity_unproven")
    # This planner cannot verify #170 identity, so T2 is never eligible when an
    # independent reviewer is required: the result is BLOCKED / MANUAL_HANDOFF.
    t2_eligible = False

    t3_constraints = list(shared) + [
        _constraint("G-CAPABILITIES", gaps["capability"]),
        _constraint("T3-PARALLEL-SCOPE",
                    [] if parallel else [
                        *(["not_parallelizable"]
                          if not task["context"]["parallelizable"] else []),
                        *(["subtask_count_lt_2"]
                          if task["context"]["subtask_count"] < 2 else []),
                    ]),
        _constraint("T3-REVIEW-SCOPE",
                    ["reviewer_required"] if reviewer_required else []),
    ]
    t3_excluded = list(gaps["dependency"]) + list(gaps["tool"]) + list(gaps["lock"])
    t3_excluded += list(gaps["capability"])
    if not task["context"]["parallelizable"]:
        _add(t3_excluded, "not_parallelizable")
    if task["context"]["subtask_count"] < 2:
        _add(t3_excluded, "subtask_count_lt_2")
    if reviewer_required:
        _add(t3_excluded, "reviewer_required")
    t3_eligible = shared_ok and capabilities_ok and not reviewer_required and parallel

    handoff_trigger = bool(
        gaps["capability"] or gaps["reviewer"] or gaps["tool"]
        or high_risk or ambiguity
    )
    t4_constraints = [
        _constraint("G-DEPENDENCIES", gaps["dependency"]),
        _constraint("G-LOCKS", gaps["lock"]),
        _constraint("T4-HANDOFF-TRIGGER", []) if handoff_trigger
        else _not_applicable("T4-HANDOFF-TRIGGER", ["runtime_capability_sufficient"]),
    ]
    t4_excluded = list(gaps["dependency"]) + list(gaps["lock"])
    if not handoff_trigger:
        _add(t4_excluded, "runtime_capability_sufficient")
    t4_eligible = (not gaps["dependency"] and not gaps["lock"]
                   and handoff_trigger)

    return [
        _candidate("T0", t0_eligible, t0_constraints, t0_excluded),
        _candidate("T1", t1_eligible, t1_constraints, t1_excluded),
        _candidate("T2", t2_eligible, t2_constraints, t2_excluded),
        _candidate("T3", t3_eligible, t3_constraints, t3_excluded),
        _candidate("T4", t4_eligible, t4_constraints, t4_excluded),
    ]


# ---------------------------------------------------------------------------
# Output documents
# ---------------------------------------------------------------------------

def _human_decisions(task, risk, gaps):
    decisions = []
    checks = (
        ("dependency_not_verified", gaps["dependency"]),
        ("lock_conflict", gaps["lock"]),
        ("tool_permission_missing", gaps["tool"]),
        ("topology_high_risk", risk["reasons"]
         if risk["level"] in ("high", "unknown") else []),
        ("topology_ambiguity",
         ["ambiguity_declared"] if task["ambiguities"] else []),
        ("runtime_capability_gap", gaps["capability"]),
        ("reviewer_independence_unproven", gaps["reviewer"]),
    )
    for decision_id, reason_codes in checks:
        if reason_codes:
            decisions.append({
                "decision_id": decision_id,
                "reason_codes": list(reason_codes),
            })
    return decisions


def build_plan(task):
    risk = assess_risk(task)
    independence = assess_independence(task)
    gaps = _gaps(task)
    candidates = build_candidates(task, risk, gaps)
    eligibility = {item["id"]: item["eligible"] for item in candidates}
    high_risk = risk["level"] in ("high", "unknown")
    ambiguity = bool(task["ambiguities"])

    # A dependency or path/resource conflict makes every candidate unexecutable.
    # A missing tool can still be satisfied by an external/manual environment
    # (T4), so it produces a handoff rather than a hard block.
    blocked = bool(gaps["dependency"] or gaps["lock"])
    if blocked:
        selection_status = "BLOCKED"
    elif high_risk or ambiguity:
        selection_status = "MANUAL_HANDOFF"
    elif gaps["capability"] or task["reviewer_required"]:
        selection_status = "MANUAL_HANDOFF"
    else:
        selection_status = "RECOMMENDED"

    selected_topology = None
    if selection_status == "RECOMMENDED":
        if eligibility["T0"]:
            selected_topology = "T0"
        elif eligibility["T3"]:
            selected_topology = "T3"
        elif eligibility["T1"]:
            selected_topology = "T1"
        elif eligibility["T2"]:
            selected_topology = "T2"
        else:
            selection_status = "MANUAL_HANDOFF"

    alternatives = [
        {"id": item["id"], "tradeoffs": list(item["tradeoffs"])}
        for item in candidates
        if item["eligible"] and item["id"] != selected_topology
    ]

    role_assignments = [
        {"role": role, "assignment": "unassigned"}
        for role in ROLE_ASSIGNMENTS.get(selected_topology, ())
    ]

    limitations = [
        "caller_snapshot_unverified",
        "selection_is_not_dispatch",
        "no_execution_no_instance_no_network_no_writes",
    ]
    if task["reviewer_required"]:
        _add(limitations, "reviewer_identity_not_verified_by_this_tool")

    evidence_refs = [
        {"kind": "task_source", "ref": "caller:snapshot"},
        {"kind": "risk_signals", "ref": "caller:risk"},
        {"kind": "runtime_capabilities", "ref": "caller:capabilities"},
        {"kind": "dependency_state", "ref": "caller:dependencies"},
        {"kind": "lock_state", "ref": "caller:locks"},
    ]
    if task["reviewer_required"]:
        evidence_refs.append({"kind": "reviewer_identity", "ref": "caller:reviewer"})

    decision_basis = [
        {"basis_id": "task_source", "reason_codes": []},
        {"basis_id": "risk", "reason_codes": list(risk["reasons"])},
        {"basis_id": "dependencies", "reason_codes": list(gaps["dependency"])},
        {"basis_id": "tool_permissions", "reason_codes": list(gaps["tool"])},
        {"basis_id": "locks", "reason_codes": list(gaps["lock"])},
        {"basis_id": "runtime_capabilities", "reason_codes": list(gaps["capability"])},
        {"basis_id": "independence", "reason_codes": list(gaps["reviewer"])},
        {"basis_id": "implementation_authority",
         "reason_codes": list(gaps["implementation"])},
    ]

    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "planner_version": PLANNER_VERSION,
        "plan_usability": "not_a_dispatch",
        "mode": "observe_only",
        "dispatched": False,
        "task_ref": task["task_ref"],
        "edition": task["edition"],
        "selection_status": selection_status,
        "selected_topology": selected_topology,
        "risk": risk,
        "independence": independence,
        "model_matching": {
            "status": "deferred",
            "authority": "existing_s2_s4_model_selection",
            "reason": "topology_selection_is_upstream_of_model_matching",
        },
        "candidates": candidates,
        "decision_basis": decision_basis,
        "alternatives": alternatives,
        "acceptance_refs": [item["ac_id"] for item in task["acceptance"]],
        "role_assignments": role_assignments,
        "required_human_decisions": _human_decisions(task, risk, gaps),
        "limitations": limitations,
        "evidence_refs": evidence_refs,
    }


def diagnostic(code, path="$"):
    if code not in DIAGNOSTIC_CODES:
        code = "schema_invalid"
    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "plan_usability": "not_a_dispatch",
        "dispatched": False,
        "diagnostic": {"code": code, "path": path},
    }


def build(raw):
    """Validate ``raw`` and return ``(exit_code, document)``. Never returns 0."""
    try:
        task = parse_task(raw)
    except PlannerError as error:
        return 2, diagnostic(error.code, error.path)
    return 3, build_plan(task)
