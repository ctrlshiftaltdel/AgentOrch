"""Read-only shadow verification planner (Issue #186, AVE stage B).

Frozen contract: ``docs/design/verification-planner-b-contract.md``.

This module classifies a caller snapshot, never executes a check, never writes
project state and never contacts the network. It returns either a diagnostic
(structure invalid, exit code 2) or a shadow plan (exit code 3, ``closure:
open``). Exit code 0 is deliberately unused: a plan is not a verification
result, an approval, a dispatch permission or a ``VERIFIED`` signal.

Only the Python standard library is used. The path normalisation below mirrors
``scripts/orchestrator.py`` semantics on purpose but is reimplemented here so
the planner never imports the scheduler.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

SCHEMA_VERSION = "verification-planner-input/1"
OUTPUT_SCHEMA_VERSION = "verification-planner-output/1"
DIAGNOSTIC_SCHEMA_VERSION = "verification-planner-diagnostic/1"
PLANNER_VERSION = "186-draft-2"
GATE_SOURCE_PATH = "kit/orchestration.md"
GATE_IDS = ("G1", "G2", "G3", "G4", "G5", "G6")

L0, L1, L2, L3 = 0, 1, 2, 3
LEVEL_NAMES = {L0: "L0", L1: "L1", L2: "L2", L3: "L3"}

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REF_RE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,200}$")
AC_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ITEM_RE = re.compile(r"^[a-z0-9._-]{1,80}$")
CHECK_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

REQUIRED_TOP_LEVEL = (
    "schema_version", "task_ref", "edition", "base_sha", "head_sha",
    "acceptance", "changed_paths", "semantic_signals", "uncertainties",
    "semantic_review", "profile", "project_floor",
    "requires_independent_reviewer", "dynamic_dependencies",
    "non_probe_conditions",
)
OPTIONAL_TOP_LEVEL = ("runtime_probe", "host_tool_absence_claims")
ALLOWED_TOP_LEVEL = set(REQUIRED_TOP_LEVEL) | set(OPTIONAL_TOP_LEVEL)

SIGNAL_IDS = (
    "executable_example", "instruction", "policy", "checklist",
    "contract_text", "cross_module", "interface_change", "state_machine",
    "lock", "permission", "security", "reviewer_identity", "dispatch",
    "migration_or_upgrade", "release", "evidence_gate",
    "no_high_risk_impact", "semantic_review_complete",
    "high_risk_checks_already_covered", "self_reported_l0",
)
NEGATIVE_SIGNALS = {
    "no_high_risk_impact", "semantic_review_complete",
    "high_risk_checks_already_covered", "self_reported_l0",
}

# §4.2: result-shaped keys inside profile.checks[] are stripped, never echoed.
PROFILE_RESULT_FIELDS = {
    "status", "actual", "expected", "notes", "result", "passed", "pass",
    "ok", "qualified", "success", "green", "approved", "ready", "verified",
    "may_merge", "exit_code", "conclusion",
}

# §3.1 / §3.2 / §1.1 / B.1: forbidden at any level (except profile.checks status).
FORBIDDEN_FIELDS = {
    "declared_risk_level", "status", "identity_ok", "reviewer_identity_proven",
    "model_name", "model_alias", "temperature", "prompt_hash", "cached_review_ok",
    "attempt_allowed", "dispatch_ok", "may_dispatch", "unlock",
    "handover_authorized", "skip", "override_approver", "approved_by", "waiver",
    "checks_cover_high_risk", "source_verified", "topology_advice",
    "suite_seconds", "total_cycle_seconds", "review_rounds", "reviewer_time",
    "token_usage_if_reported", "CI_retries", "regression_escape", "severity",
    "equivalence_rate", "tests_selected", "tests_total", "tests_executed",
    "baseline_sha",
}

CLOSURE_REASON_ORDER = (
    "source_not_verified", "mandatory_baseline_unfrozen",
    "dynamic_dependency_unknown", "declared_uncertainty",
    "semantic_review_not_current", "profile_not_current",
    "negative_self_report", "identity_unproven", "profile_result_field_stripped",
)

BANNED_WORDS = (
    "PASS", "FAIL", "NOT_RUN", "UNKNOWN", "BLOCKED", "NOT_APPLICABLE",
    "PASS_EXECUTED", "PASS_EQUIVALENCE_ATTESTED", "approved", "ready",
    "verified", "may_merge", "ok", "passed", "qualified", "success", "green",
    "attempt_allowed", "dispatch_ok", "may_dispatch", "skip", "identity_ok",
    "declared_risk_level",
)
_BANNED_LOWER = {word.lower() for word in BANNED_WORDS}
# A "complete word" includes underscores and hyphens: the frozen contract itself
# mandates identifiers such as ``R-DYN-UNKNOWN`` and ``not_a_verification_result``,
# which a stricter split would falsely read as a standalone forbidden word.
_WORD_RE = re.compile(r"[A-Za-z0-9_-]+")

DIAGNOSTIC_CODES = {
    "schema_invalid", "sha_invalid", "edition_invalid", "unknown_field",
    "forbidden_field", "path_invalid", "usage",
}


class PlannerError(Exception):
    """A structural input error. Carries the frozen diagnostic code and path."""

    def __init__(self, code: str, path: str = "$"):
        if code not in DIAGNOSTIC_CODES:
            raise RuntimeError(f"unknown diagnostic code: {code}")
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}")


# ---------------------------------------------------------------------------
# Pointer / forbidden-word helpers
# ---------------------------------------------------------------------------

def _safe_segment(segment):
    return not any(word.lower() in _BANNED_LOWER
                   for word in _WORD_RE.findall(str(segment)))


def _pointer(parts):
    """Render a JSON-ish pointer, stopping before any forbidden-word segment."""
    out = "$"
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
            continue
        if not _safe_segment(part):
            break
        out += f".{part}"
    return out


def _iter_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_strings(value)


def _iter_key_nodes(node, path="$"):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _iter_key_nodes(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_key_nodes(value, f"{path}[{index}]")


def banned_hits(payload):
    """Keys and complete string-value words banned by contract §5.1 item 3."""
    hits = []
    for path, node in _iter_key_nodes(payload):
        for key in node:
            if key.lower() in _BANNED_LOWER:
                hits.append(f"{path}: key {key!r}")
    for value in _iter_strings(payload):
        for word in _WORD_RE.findall(value):
            if word.lower() in _BANNED_LOWER:
                hits.append(f"value word {word!r}")
    return hits


def assert_clean_output(payload):
    hits = banned_hits(payload)
    if hits:
        raise RuntimeError("output violates forbidden-word invariant: " + "; ".join(hits))


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------

def _in_profile_check(pointer):
    return (len(pointer) == 3 and pointer[0] == "profile"
            and pointer[1] == "checks" and isinstance(pointer[2], int))


def _scan_forbidden(node, pointer=()):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "status" and _in_profile_check(pointer):
                continue
            if key in FORBIDDEN_FIELDS:
                raise PlannerError("forbidden_field", _pointer(pointer))
            _scan_forbidden(value, pointer + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _scan_forbidden(value, pointer + (index,))


def _require_exact_keys(obj, keys, pointer):
    missing = [key for key in keys if key not in obj]
    if missing:
        raise PlannerError("schema_invalid", _pointer(pointer + (missing[0],)))
    extra = [key for key in obj if key not in keys]
    if extra:
        raise PlannerError("unknown_field", _pointer(pointer + (extra[0],)))


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


def _require_str(value, pointer, minimum=1, maximum=None, pattern=None):
    if not isinstance(value, str) or len(value) < minimum:
        raise PlannerError("schema_invalid", _pointer(pointer))
    if maximum is not None and len(value) > maximum:
        raise PlannerError("schema_invalid", _pointer(pointer))
    if pattern is not None and not pattern.fullmatch(value):
        raise PlannerError("schema_invalid", _pointer(pointer))
    return value


def _require_sha(value, pointer):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise PlannerError("sha_invalid", _pointer(pointer))
    return value


def normalize_path(raw, pointer):
    """Mirror scripts/orchestrator.py:normalize_path without importing it."""
    if not isinstance(raw, str):
        raise PlannerError("schema_invalid", _pointer(pointer))
    text = raw.replace("\\", "/").strip()
    posix = PurePosixPath(text)
    invalid = (
        not text
        or posix.is_absolute()
        or re.match(r"^[A-Za-z]:/", text) is not None
        or any(part in ("..", ".") for part in posix.parts)
        or any(char in text for char in "*?[]")
    )
    if invalid:
        raise PlannerError("path_invalid", _pointer(pointer))
    return str(posix).rstrip("/") or "."


def _strip_profile_results(item, pointer):
    stripped = False
    for key in list(item):
        if key in PROFILE_RESULT_FIELDS:
            item.pop(key)
            stripped = True
    return stripped


def parse_task(raw):
    """Validate a versioned input snapshot. Raises PlannerError on structure."""
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

    task_ref = _require_str(raw["task_ref"], ("task_ref",), maximum=200)

    edition = raw["edition"]
    if edition not in ("local", "github"):
        raise PlannerError("edition_invalid", "$.edition")

    base_sha = _require_sha(raw["base_sha"], ("base_sha",))
    head_sha = _require_sha(raw["head_sha"], ("head_sha",))

    acceptance = []
    for index, item in enumerate(_require_list(raw["acceptance"], ("acceptance",), 1)):
        pointer = ("acceptance", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("ac_id", "text"), pointer)
        acceptance.append({
            "ac_id": _require_str(item["ac_id"], pointer + ("ac_id",), maximum=64,
                                  pattern=AC_RE),
            "text": _require_str(item["text"], pointer + ("text",), maximum=500),
        })

    changed_paths = []
    for index, item in enumerate(_require_list(raw["changed_paths"], ("changed_paths",), 1)):
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

    signal_ids = []
    for index, item in enumerate(_require_list(raw["semantic_signals"], ("semantic_signals",))):
        pointer = ("semantic_signals", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("signal_id", "source_ref"), pointer)
        signal_id = item["signal_id"]
        if signal_id not in SIGNAL_IDS:
            raise PlannerError("unknown_field", _pointer(pointer + ("signal_id",)))
        _require_str(item["source_ref"], pointer + ("source_ref",), maximum=200,
                     pattern=REF_RE)
        signal_ids.append(signal_id)

    uncertainties = []
    for index, item in enumerate(_require_list(raw["uncertainties"], ("uncertainties",))):
        pointer = ("uncertainties", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("item_id", "source_ref"), pointer)
        uncertainties.append(_require_str(item["item_id"], pointer + ("item_id",),
                                          maximum=80, pattern=ITEM_RE))

    review = _require_object(raw["semantic_review"], ("semantic_review",))
    _require_exact_keys(review, ("reviewed_sha", "stale"), ("semantic_review",))
    reviewed_sha = review["reviewed_sha"]
    if reviewed_sha is not None:
        reviewed_sha = _require_sha(reviewed_sha, ("semantic_review", "reviewed_sha"))
    stale_flag = _require_bool(review["stale"], ("semantic_review", "stale"))

    profile_raw = _require_object(raw["profile"], ("profile",))
    _require_exact_keys(profile_raw, ("profile_version", "source_ref", "as_of_sha", "checks"),
                        ("profile",))
    profile_version = _require_str(profile_raw["profile_version"],
                                   ("profile", "profile_version"), maximum=80)
    profile_ref = _require_str(profile_raw["source_ref"], ("profile", "source_ref"),
                               maximum=200, pattern=REF_RE)
    as_of_sha = _require_sha(profile_raw["as_of_sha"], ("profile", "as_of_sha"))

    stripped_result_fields = False
    checks = []
    for index, item in enumerate(_require_list(profile_raw["checks"], ("profile", "checks"), 1)):
        pointer = ("profile", "checks", index)
        item = _require_object(item, pointer)
        stripped_result_fields = _strip_profile_results(item, pointer) or stripped_result_fields
        _require_exact_keys(item, ("check_id", "scope", "source_ref"), pointer)
        scope = item["scope"]
        if scope not in ("quick", "full", "static", "post_merge", "ci", "doc"):
            raise PlannerError("schema_invalid", _pointer(pointer + ("scope",)))
        checks.append({
            "check_id": _require_str(item["check_id"], pointer + ("check_id",),
                                     maximum=64, pattern=CHECK_RE),
            "scope": scope,
            "source_ref": _require_str(item["source_ref"], pointer + ("source_ref",),
                                       maximum=200, pattern=REF_RE),
        })

    project_floor = raw["project_floor"]
    if project_floor not in ("L0", "L1", "L2", "L3"):
        raise PlannerError("schema_invalid", "$.project_floor")

    requires_reviewer = _require_bool(raw["requires_independent_reviewer"],
                                      ("requires_independent_reviewer",))

    dynamic = raw["dynamic_dependencies"]
    if dynamic not in ("known", "unknown", "not_applicable"):
        raise PlannerError("schema_invalid", "$.dynamic_dependencies")

    conditions = _require_object(raw["non_probe_conditions"], ("non_probe_conditions",))
    condition_keys = ("call_failure_observed", "permission_constraint",
                      "lock_constraint", "security_constraint")
    _require_exact_keys(conditions, condition_keys, ("non_probe_conditions",))
    non_probe = {key: _require_bool(conditions[key], ("non_probe_conditions", key))
                 for key in condition_keys}

    probe_items = []
    if "runtime_probe" in raw:
        probe = _require_object(raw["runtime_probe"], ("runtime_probe",))
        _require_exact_keys(probe, ("items",), ("runtime_probe",))
        for index, item in enumerate(_require_list(probe["items"], ("runtime_probe", "items"))):
            pointer = ("runtime_probe", "items", index)
            item = _require_object(item, pointer)
            _require_exact_keys(item, ("probe_id", "caller_code"), pointer)
            probe_id = item["probe_id"]
            if probe_id not in ("C1", "C2", "C3", "C4"):
                raise PlannerError("schema_invalid", _pointer(pointer + ("probe_id",)))
            caller_code = item["caller_code"]
            if caller_code not in ("missing", "not_executed", "stale", "unproven_fail"):
                raise PlannerError("schema_invalid", _pointer(pointer + ("caller_code",)))
            probe_items.append({"probe_id": probe_id, "caller_code": caller_code})

    absence_claims = []
    if "host_tool_absence_claims" in raw:
        for index, item in enumerate(_require_list(raw["host_tool_absence_claims"],
                                                  ("host_tool_absence_claims",))):
            pointer = ("host_tool_absence_claims", index)
            item = _require_object(item, pointer)
            _require_exact_keys(item, ("probe_id", "evidence_ref"), pointer)
            probe_id = item["probe_id"]
            if probe_id not in ("C1", "C2", "C3", "C4"):
                raise PlannerError("schema_invalid", _pointer(pointer + ("probe_id",)))
            absence_claims.append({
                "probe_id": probe_id,
                "evidence_ref": _require_str(item["evidence_ref"], pointer + ("evidence_ref",),
                                             maximum=200, pattern=REF_RE),
            })

    return {
        "task_ref": task_ref,
        "edition": edition,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "acceptance": acceptance,
        "changed_paths": changed_paths,
        "signal_ids": signal_ids,
        "uncertainties": uncertainties,
        "reviewed_sha": reviewed_sha,
        "review_stale": stale_flag,
        "profile_version": profile_version,
        "profile_ref": profile_ref,
        "as_of_sha": as_of_sha,
        "checks": checks,
        "project_floor": project_floor,
        "requires_reviewer": requires_reviewer,
        "dynamic": dynamic,
        "non_probe": non_probe,
        "probe_items": probe_items,
        "absence_claims": absence_claims,
        "stripped_result_fields": stripped_result_fields,
    }


# ---------------------------------------------------------------------------
# Classification (appendix A)
# ---------------------------------------------------------------------------

def _first_segments(paths):
    seen = []
    for path in paths:
        segment = path.split("/")[0]
        if segment not in seen:
            seen.append(segment)
    return seen


def _add_rule(rule_ids, rule_id):
    if rule_id not in rule_ids:
        rule_ids.append(rule_id)


def _classify(task):
    paths = [item["path"] for item in task["changed_paths"]]
    signals = set(task["signal_ids"])
    first_segments = _first_segments(paths)
    rule_ids = []
    path_floor = L0
    semantic_floor = L0
    project_floor = {"L0": L0, "L1": L1, "L2": L2, "L3": L3}[task["project_floor"]]
    high_hit = False

    # --- path rules (A.1) ---------------------------------------------------
    path_l3_rules = (
        ("R-PATH-ORCHESTRATOR", lambda path: path == "scripts/orchestrator.py"),
        ("R-PATH-KIT", lambda path: path == "kit" or path.startswith("kit/")),
        ("R-PATH-CI", lambda path: path.startswith(".github/workflows/")),
        ("R-PATH-PROJECT", lambda path: path == "docs/PROJECT.md"),
        ("R-PATH-PLAN", lambda path: path == "docs/PLAN.md"),
        ("R-PATH-AGENTS", lambda path: path == "AGENTS.md"),
        ("R-PATH-EDITION", lambda path: path.startswith("editions/")),
        ("R-PATH-TEMPLATE", lambda path: path.startswith("templates/")),
        ("R-PATH-RELEASE", lambda path: path == "VERSION.md" or path.startswith("release/")),
        ("R-PATH-PACKAGE", lambda path: path in ("scripts/validate_kit.py",
                                                  "scripts/build_release.py")),
    )
    l3_path_hit = False
    for rule_id, predicate in path_l3_rules:
        if any(predicate(path) for path in paths):
            _add_rule(rule_ids, rule_id)
            l3_path_hit = True
            high_hit = True
            path_floor = max(path_floor, L3)

    docs_only = bool(first_segments) and all(segment == "docs" for segment in first_segments)
    executable_signal = bool(signals & {"executable_example", "instruction"})
    plain_exclusion = executable_signal or bool(
        signals & {"policy", "checklist", "contract_text"})

    if docs_only and not l3_path_hit and not plain_exclusion:
        _add_rule(rule_ids, "R-DOC-PLAIN")
        path_floor = max(path_floor, L0)
    elif len(first_segments) >= 2:
        _add_rule(rule_ids, "R-CROSS-MODULE")
        path_floor = max(path_floor, L2)
    elif len(first_segments) == 1 and first_segments[0] != "docs" and not l3_path_hit:
        _add_rule(rule_ids, "R-LOCAL")
        path_floor = max(path_floor, L1)

    # --- positive semantic signals (A.2) -----------------------------------
    semantic_rules = (
        ("cross_module", "R-SEM-CROSS", L2, False),
        ("interface_change", "R-SEM-INTERFACE", L2, False),
        ("policy", "R-SEM-POLICY", L3, True),
        ("checklist", "R-SEM-CHECKLIST", L3, True),
        ("contract_text", "R-SEM-CONTRACT", L3, True),
        ("state_machine", "R-SEM-STATE", L3, True),
        ("lock", "R-SEM-LOCK", L3, True),
        ("permission", "R-SEM-PERMISSION", L3, True),
        ("security", "R-SEM-SECURITY", L3, True),
        ("reviewer_identity", "R-SEM-REVIEWER", L3, True),
        ("dispatch", "R-SEM-DISPATCH", L3, True),
        ("migration_or_upgrade", "R-SEM-MIGRATION", L3, True),
        ("release", "R-SEM-RELEASE", L3, True),
        ("evidence_gate", "R-SEM-EVIDENCE", L3, True),
    )
    if executable_signal:
        if "docs" in first_segments:
            _add_rule(rule_ids, "R-DOC-EXECUTABLE")
        else:
            _add_rule(rule_ids, "R-SEM-EXECUTABLE")
        semantic_floor = max(semantic_floor, L2)
    for signal_id, rule_id, floor, is_high in semantic_rules:
        if signal_id in signals:
            _add_rule(rule_ids, rule_id)
            semantic_floor = max(semantic_floor, floor)
            high_hit = high_hit or is_high

    # --- non-probe conditions (§3.4) ---------------------------------------
    non_probe_rules = (
        ("call_failure_observed", "R-NONPROBE-CALL"),
        ("permission_constraint", "R-NONPROBE-PERMISSION"),
        ("lock_constraint", "R-NONPROBE-LOCK"),
        ("security_constraint", "R-NONPROBE-SECURITY"),
    )
    for key, rule_id in non_probe_rules:
        if task["non_probe"][key]:
            _add_rule(rule_ids, rule_id)
            semantic_floor = max(semantic_floor, L3)
            high_hit = True

    # --- uncertainty (§2.1 step 4) -----------------------------------------
    # The planner never verifies the caller's sources, so this is unconditional.
    _add_rule(rule_ids, "R-UNVERIFIED-SOURCE")
    _add_rule(rule_ids, "R-BASELINE-UNFROZEN")
    uncertainty_floor = L3

    if task["dynamic"] == "unknown":
        _add_rule(rule_ids, "R-DYN-UNKNOWN")
    if task["uncertainties"]:
        _add_rule(rule_ids, "R-DECLARED-UNCERTAINTY")
    if (task["reviewed_sha"] is None or task["reviewed_sha"] != task["head_sha"]
            or task["review_stale"]):
        _add_rule(rule_ids, "R-STALE-REVIEW")
    if task["as_of_sha"] != task["head_sha"]:
        _add_rule(rule_ids, "R-STALE-PROFILE")
    if task["requires_reviewer"]:
        _add_rule(rule_ids, "R-IDENTITY-UNPROVEN")
        uncertainty_floor = max(uncertainty_floor, L3)
        high_hit = True

    # --- negative self-reports (§2.1 step 5) -------------------------------
    negative = bool(signals & NEGATIVE_SIGNALS)
    if negative:
        _add_rule(rule_ids, "R-NEGATIVE-NO-DOWNGRADE")

    # --- max, then high-risk post-processing (§2.1 steps 6-7) --------------
    level = max(path_floor, semantic_floor, uncertainty_floor, project_floor)
    if high_hit:
        _add_rule(rule_ids, "R-HIGH-AFTER-MAX")
        level = max(level, L3)

    reasons = set()
    reasons.update({"source_not_verified", "mandatory_baseline_unfrozen"})
    if task["dynamic"] == "unknown":
        reasons.add("dynamic_dependency_unknown")
    if task["uncertainties"]:
        reasons.add("declared_uncertainty")
    if (task["reviewed_sha"] is None or task["reviewed_sha"] != task["head_sha"]
            or task["review_stale"]):
        reasons.add("semantic_review_not_current")
    if task["as_of_sha"] != task["head_sha"]:
        reasons.add("profile_not_current")
    if negative:
        reasons.add("negative_self_report")
    if task["requires_reviewer"]:
        reasons.add("identity_unproven")
    if task["stripped_result_fields"]:
        reasons.add("profile_result_field_stripped")
    closure_reasons = [reason for reason in CLOSURE_REASON_ORDER if reason in reasons]

    return {
        "level": LEVEL_NAMES[level],
        "path_floor": LEVEL_NAMES[path_floor],
        "semantic_floor": LEVEL_NAMES[semantic_floor],
        "uncertainty_floor": LEVEL_NAMES[uncertainty_floor],
        "project_floor": LEVEL_NAMES[project_floor],
        "rule_ids": rule_ids,
        "closure_reasons": closure_reasons,
        "unknown_items": list(closure_reasons),
        "negative": negative,
    }


# ---------------------------------------------------------------------------
# Output documents
# ---------------------------------------------------------------------------

def build_plan(task):
    classification = _classify(task)
    check_ids = [check["check_id"] for check in task["checks"]]
    caller_snapshot = [dict(check) for check in task["checks"]]
    shadow_candidates = [dict(check) for check in task["checks"]]
    absence_claims = [dict(claim, level_effect="none", dispatch_effect="none")
                      for claim in task["absence_claims"]]

    if task["requires_reviewer"]:
        identity = {"requirement": "required", "assessment": "unproven"}
    else:
        identity = {"requirement": "not_required", "assessment": "not_assessed"}

    plan = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "planner_version": PLANNER_VERSION,
        "plan_usability": "not_a_verification_result",
        "mode": "shadow_only",
        "task_ref": task["task_ref"],
        "edition": task["edition"],
        "base_sha": task["base_sha"],
        "head_sha": task["head_sha"],
        "closure": "open",
        "closure_reasons": classification["closure_reasons"],
        "identity": identity,
        "policy": {
            "effect": "current_rules_unchanged",
            "refs": ["kit/orchestration.md", "editions/local/master.md",
                     "editions/github/master.md"],
        },
        "classification": {
            "level": classification["level"],
            "floors": {
                "path_floor": classification["path_floor"],
                "semantic_floor": classification["semantic_floor"],
                "uncertainty_floor": classification["uncertainty_floor"],
                "project_floor": classification["project_floor"],
            },
            "rule_ids": classification["rule_ids"],
            "unknown_items": classification["unknown_items"],
        },
        "gates": [{"gate_id": gate_id, "source_path": GATE_SOURCE_PATH}
                  for gate_id in GATE_IDS],
        "ac_map": [
            {
                "ac_id": item["ac_id"],
                "matched_rule_ids": list(classification["rule_ids"]),
                "gate_ids": list(GATE_IDS),
                "check_ids": list(check_ids),
            }
            for item in task["acceptance"]
        ],
        "checks": {
            "authority": "unverified_snapshot",
            "caller_snapshot": caller_snapshot,
            "shadow_candidates": shadow_candidates,
            "counterfactual_diff": [],
        },
        "observations": {
            "runtime_probe": {
                "items": task["probe_items"],
                "tool_absence_claims": absence_claims,
            },
        },
    }
    assert_clean_output(plan)
    return plan


def diagnostic(code, path="$"):
    document = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "plan_usability": "not_a_verification_result",
        "diagnostic": {"code": code, "path": path},
    }
    assert_clean_output(document)
    return document


def build(raw):
    """Validate ``raw`` and return ``(exit_code, document)``. Never returns 0."""
    try:
        task = parse_task(raw)
    except PlannerError as error:
        return 2, diagnostic(error.code, error.path)
    return 3, build_plan(task)


def loads(text):
    """Parse JSON text and classify it. Returns ``(exit_code, document)``."""
    try:
        raw = json.loads(text)
    except ValueError:
        return 2, diagnostic("schema_invalid", "$")
    return build(raw)
