"""Read-only runtime handoff decision (Issue #179, Stage C observe-only).

Decision order and concept ownership: ``docs/design/task-driven-agent-topology.md``
§2 (frozen by #177). Stage C is §7: runtime adapter wiring. This module consumes
one caller snapshot of observed runtime facts, decides whether the facts are
complete enough for an authorized dispatch to consider the runtime, and returns
either a capability summary or an actionable manual handoff package. It never
spawns, waits, collects, writes a file, contacts the network or changes task
state.

Input contract: ``topology-runtime-input/1`` (see :func:`parse_input`).
Output contract: ``topology-runtime-output/1`` or
``topology-runtime-diagnostic/1``. Every document carries
``plan_usability: not_a_dispatch``, ``dispatched: false`` and
``runtime_support_inferred: false``: a complete capability observation is not a
dispatch, an authorization or a ``VERIFIED`` signal.

Hard rule: a runtime name is not evidence. ``opencode``, ``codex`` and ``dsh``
get no built-in support. A capability counts only when the caller supplies
``OBSERVED`` evidence inside the freshness window; any other claim is reported
as its effective ``UNKNOWN`` / ``STALE`` state, never promoted to ``pass``.

Exit codes (set by :func:`build`): 2 for a structural / usage diagnostic, 3 for
a decision (including every handoff). Exit code 0 is unused on purpose.

Only the Python standard library is used. Path normalisation mirrors
``scripts/orchestrator.py`` semantics but is reimplemented here so this tool
never imports the scheduler.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

SCHEMA_VERSION = "topology-runtime-input/1"
OUTPUT_SCHEMA_VERSION = "topology-runtime-output/1"
DIAGNOSTIC_SCHEMA_VERSION = "topology-runtime-diagnostic/1"
ADAPTER_VERSION = "179-draft-1"

CAPABILITY_IDS = ("spawn", "wait", "collect", "context_isolation")
CAPABILITY_STATES = ("pass", "fail", "unknown", "not_run", "stale")
EVIDENCE_CLASSES = ("OBSERVED", "INFERRED", "UNKNOWN", "STALE")
IDENTITY_STATUSES = ("CONFIRMED", "UNCONFIRMED", "UNKNOWN", "ABSENT",
                     "EXPIRED", "CONFLICT", "INVALID")
MAX_EVIDENCE_AGE_DAYS = 30

CAPABILITY_OBJECT_KEYS = ("state", "evidence_class", "evidence_ref",
                          "observed_at")
TOOL_PERMISSION_KEYS = ("state", "granted_tools", "evidence_class",
                        "evidence_ref", "observed_at")
RUNTIME_KEYS = ("runtime_id", "capabilities", "tool_permissions")
TASK_KEYS = ("required_tools",)
IDENTITY_KEYS = ("declared_status", "canonical_identity", "verified_until",
                 "source_ref")
LOCK_KEYS = ("path_conflicts", "resource_conflicts")
REQUIRED_TOP_LEVEL = ("schema_version", "task_ref", "edition", "as_of",
                      "runtime", "task", "identity", "locks")
ALLOWED_TOP_LEVEL = set(REQUIRED_TOP_LEVEL)

# A caller snapshot may not pre-set a result, capability verdict or dispatch
# field. The decision is produced by this tool, never accepted from the caller.
FORBIDDEN_FIELDS = frozenset({
    "dispatched", "dispatch_ok", "may_dispatch", "attempt_allowed", "approved",
    "verified", "status", "runtime_status", "handoff_required", "plan_usability",
    "identity_ok", "identity_trusted", "capable", "supported", "skip", "waiver",
    "override", "ready",
})

DIAGNOSTIC_CODES = {
    "schema_invalid", "edition_invalid", "unknown_field", "forbidden_field",
    "path_invalid", "usage",
}

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TASK_REF_RE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,200}$")
RUNTIME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
TOOL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
RESOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,80}$")
REF_RE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,200}$")

BASE_LIMITATIONS = (
    "caller_snapshot_unverified",
    "capability_facts_are_not_authorization",
    "recommendation_is_not_dispatch",
    "runtime_support_not_inferred_from_name",
    "no_execution_no_instance_no_network_no_writes",
)
OPERATOR_ACTIONS = (
    "open an isolated execution session outside this tool",
    "record the spawn command, exit code and head SHA in the task carrier",
    "do not mark the task DISPATCHED from this report",
    "re-run this read-only decision after the missing facts are observed",
)
DISPATCH_AUTHORITY = "existing_task_lock_and_master_review"
CAPABILITY_SOURCE = "caller_observed_facts"


class DecisionError(ValueError):
    """A structural input error. Carries the diagnostic code and JSON pointer."""

    def __init__(self, code: str, path: str = "$"):
        if code not in DIAGNOSTIC_CODES:
            raise ValueError(f"unknown diagnostic code: {code}")
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
        raise DecisionError("schema_invalid", _pointer(pointer))
    return value


def _require_list(value, pointer):
    if not isinstance(value, list):
        raise DecisionError("schema_invalid", _pointer(pointer))
    return value


def _require_bool(value, pointer):
    if not isinstance(value, bool):
        raise DecisionError("schema_invalid", _pointer(pointer))
    return value


def _require_str(value, pointer, maximum=None, pattern=None):
    if not isinstance(value, str) or not value:
        raise DecisionError("schema_invalid", _pointer(pointer))
    if maximum is not None and len(value) > maximum:
        raise DecisionError("schema_invalid", _pointer(pointer))
    if pattern is not None and not pattern.fullmatch(value):
        raise DecisionError("schema_invalid", _pointer(pointer))
    return value


def _optional_str(value, pointer, maximum=None):
    if value is None:
        return None
    return _require_str(value, pointer, maximum=maximum)


def _require_exact_keys(obj, keys, pointer):
    missing = [key for key in keys if key not in obj]
    if missing:
        raise DecisionError("schema_invalid", _pointer(pointer + (missing[0],)))
    extra = [key for key in obj if key not in keys]
    if extra:
        raise DecisionError("unknown_field", _pointer(pointer + (extra[0],)))


def _require_unique(values, pointer, pattern, maximum=None):
    result = []
    for index, value in enumerate(values):
        item = _require_str(value, pointer + (index,), maximum=maximum,
                            pattern=pattern)
        if item in result:
            raise DecisionError("schema_invalid", _pointer(pointer + (index,)))
        result.append(item)
    return result


def _add(items, value):
    if value not in items:
        items.append(value)


def _scan_forbidden(node, pointer=()):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_FIELDS:
                raise DecisionError("forbidden_field", _pointer(pointer + (key,)))
            _scan_forbidden(value, pointer + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _scan_forbidden(value, pointer + (index,))


def parse_timestamp(value, pointer):
    """Parse a UTC ``YYYY-MM-DDTHH:MM:SSZ`` string; raise on anything else."""
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise DecisionError("schema_invalid", _pointer(pointer))
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError as exc:
        raise DecisionError("schema_invalid", _pointer(pointer)) from exc


def normalize_path(raw, pointer):
    """Mirror scripts/orchestrator.py:normalize_path without importing it."""
    if not isinstance(raw, str):
        raise DecisionError("schema_invalid", _pointer(pointer))
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
        raise DecisionError("path_invalid", _pointer(pointer))
    return str(posix).rstrip("/") or "."


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def _parse_capability(raw, pointer):
    obj = _require_object(raw, pointer)
    _require_exact_keys(obj, CAPABILITY_OBJECT_KEYS, pointer)
    state = obj["state"]
    if state not in CAPABILITY_STATES:
        raise DecisionError("schema_invalid", _pointer(pointer + ("state",)))
    evidence_class = obj["evidence_class"]
    if evidence_class not in EVIDENCE_CLASSES:
        raise DecisionError("schema_invalid",
                           _pointer(pointer + ("evidence_class",)))
    return {
        "state": state,
        "evidence_class": evidence_class,
        "evidence_ref": _require_str(obj["evidence_ref"],
                                     pointer + ("evidence_ref",),
                                     maximum=200, pattern=REF_RE),
        "observed_at": parse_timestamp(obj["observed_at"],
                                       pointer + ("observed_at",)),
    }


def _parse_tool_permissions(raw, pointer):
    obj = _require_object(raw, pointer)
    _require_exact_keys(obj, TOOL_PERMISSION_KEYS, pointer)
    state = obj["state"]
    if state not in CAPABILITY_STATES:
        raise DecisionError("schema_invalid", _pointer(pointer + ("state",)))
    evidence_class = obj["evidence_class"]
    if evidence_class not in EVIDENCE_CLASSES:
        raise DecisionError("schema_invalid",
                           _pointer(pointer + ("evidence_class",)))
    granted = _require_unique(
        _require_list(obj["granted_tools"], pointer + ("granted_tools",)),
        pointer + ("granted_tools",), TOOL_RE, maximum=64)
    return {
        "state": state,
        "granted_tools": granted,
        "evidence_class": evidence_class,
        "evidence_ref": _require_str(obj["evidence_ref"],
                                     pointer + ("evidence_ref",),
                                     maximum=200, pattern=REF_RE),
        "observed_at": parse_timestamp(obj["observed_at"],
                                       pointer + ("observed_at",)),
    }


def parse_input(raw):
    """Validate one caller snapshot. Raises :class:`DecisionError` on structure."""
    if not isinstance(raw, dict):
        raise DecisionError("schema_invalid", "$")

    _scan_forbidden(raw)

    for key in raw:
        if key not in ALLOWED_TOP_LEVEL:
            raise DecisionError("unknown_field", _pointer((key,)))
    for key in REQUIRED_TOP_LEVEL:
        if key not in raw:
            raise DecisionError("schema_invalid", _pointer((key,)))

    if raw["schema_version"] != SCHEMA_VERSION:
        raise DecisionError("schema_invalid", "$.schema_version")

    task_ref = _require_str(raw["task_ref"], ("task_ref",), maximum=200,
                            pattern=TASK_REF_RE)

    edition = raw["edition"]
    if edition not in ("local", "github"):
        raise DecisionError("edition_invalid", "$.edition")

    as_of = parse_timestamp(raw["as_of"], ("as_of",))

    runtime_raw = _require_object(raw["runtime"], ("runtime",))
    _require_exact_keys(runtime_raw, RUNTIME_KEYS, ("runtime",))
    runtime_id = _require_str(runtime_raw["runtime_id"], ("runtime", "runtime_id"),
                              maximum=64, pattern=RUNTIME_ID_RE)
    capabilities_raw = _require_object(runtime_raw["capabilities"],
                                       ("runtime", "capabilities"))
    _require_exact_keys(capabilities_raw, CAPABILITY_IDS,
                        ("runtime", "capabilities"))
    capabilities = {
        name: _parse_capability(capabilities_raw[name],
                                ("runtime", "capabilities", name))
        for name in CAPABILITY_IDS
    }
    tool_permissions = _parse_tool_permissions(
        runtime_raw["tool_permissions"], ("runtime", "tool_permissions"))

    task_raw = _require_object(raw["task"], ("task",))
    _require_exact_keys(task_raw, TASK_KEYS, ("task",))
    required_tools = _require_unique(
        _require_list(task_raw["required_tools"], ("task", "required_tools")),
        ("task", "required_tools"), TOOL_RE, maximum=64)

    identity_raw = _require_object(raw["identity"], ("identity",))
    _require_exact_keys(identity_raw, IDENTITY_KEYS, ("identity",))
    declared_status = identity_raw["declared_status"]
    if declared_status not in IDENTITY_STATUSES:
        raise DecisionError("schema_invalid", "$.identity.declared_status")
    identity = {
        "status": declared_status,
        "canonical_identity": _optional_str(
            identity_raw["canonical_identity"],
            ("identity", "canonical_identity"), maximum=200),
        "verified_until": (None if identity_raw["verified_until"] is None
                           else parse_timestamp(identity_raw["verified_until"],
                                                ("identity", "verified_until"))),
        "source_ref": _require_str(identity_raw["source_ref"],
                                   ("identity", "source_ref"), maximum=200,
                                   pattern=REF_RE),
    }

    locks_raw = _require_object(raw["locks"], ("locks",))
    _require_exact_keys(locks_raw, LOCK_KEYS, ("locks",))
    path_conflicts = []
    for index, value in enumerate(_require_list(
            locks_raw["path_conflicts"], ("locks", "path_conflicts"))):
        path = normalize_path(value, ("locks", "path_conflicts", index))
        _add(path_conflicts, path)
    resource_conflicts = []
    for index, value in enumerate(_require_list(
            locks_raw["resource_conflicts"],
            ("locks", "resource_conflicts"))):
        resource = _require_str(value, ("locks", "resource_conflicts", index),
                                maximum=80, pattern=RESOURCE_RE)
        _add(resource_conflicts, resource)

    return {
        "task_ref": task_ref,
        "edition": edition,
        "as_of": as_of,
        "runtime_id": runtime_id,
        "capabilities": capabilities,
        "tool_permissions": tool_permissions,
        "required_tools": required_tools,
        "identity": identity,
        "path_conflicts": path_conflicts,
        "resource_conflicts": resource_conflicts,
    }


# ---------------------------------------------------------------------------
# Capability facts
# ---------------------------------------------------------------------------

def _freshness(observed_at, as_of):
    if observed_at > as_of:
        return "future"
    if (as_of - observed_at).days > MAX_EVIDENCE_AGE_DAYS:
        return "stale"
    return "fresh"


def _effective_state(fact, as_of):
    """Return ``(effective_state, downgrade_reasons)`` for one observed fact."""
    effective = fact["state"]
    reasons = []
    if effective != "pass":
        return effective, reasons
    if fact["evidence_class"] != "OBSERVED":
        return "unknown", ["evidence_not_observed"]
    freshness = _freshness(fact["observed_at"], as_of)
    if freshness == "future":
        return "unknown", ["evidence_from_future"]
    if freshness == "stale":
        return "stale", ["evidence_stale"]
    return "pass", reasons


def _capability_fact(fact, as_of):
    effective, reasons = _effective_state(fact, as_of)
    return {
        "declared_state": fact["state"],
        "effective_state": effective,
        "evidence_class": fact["evidence_class"],
        "evidence_ref": fact["evidence_ref"],
        "observed_at": fact["observed_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "downgrade_reasons": reasons,
    }


def _tool_permission_fact(fact, required_tools, as_of):
    effective, reasons = _effective_state(fact, as_of)
    missing = [tool for tool in required_tools
               if tool not in fact["granted_tools"]]
    if missing and effective == "pass":
        effective = "fail"
        reasons.append("required_tool_missing")
    return {
        "declared_state": fact["state"],
        "effective_state": effective,
        "granted_tools": list(fact["granted_tools"]),
        "required_tools": list(required_tools),
        "missing_tools": missing,
        "evidence_class": fact["evidence_class"],
        "evidence_ref": fact["evidence_ref"],
        "observed_at": fact["observed_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "downgrade_reasons": reasons,
    }


def _identity_assessment(identity, as_of):
    declared = identity["status"]
    effective = declared
    reasons = []
    if declared != "CONFIRMED":
        reasons.append(f"identity_status_{declared.lower()}")
    elif not identity["canonical_identity"]:
        effective = "UNCONFIRMED"
        reasons.append("identity_evidence_incomplete")
    elif identity["verified_until"] is None:
        effective = "UNCONFIRMED"
        reasons.append("identity_expiry_missing")
    elif identity["verified_until"] < as_of:
        effective = "EXPIRED"
        reasons.append("identity_expired")
    return {
        "declared_status": declared,
        "status": effective,
        "canonical_identity": identity["canonical_identity"],
        "verified_until": (None if identity["verified_until"] is None
                           else identity["verified_until"].strftime(
                               "%Y-%m-%dT%H:%M:%SZ")),
        "source_ref": identity["source_ref"],
        "trusted": effective == "CONFIRMED",
        "reason_codes": reasons,
    }


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

def _decision(task):
    as_of = task["as_of"]
    capability_facts = {
        name: _capability_fact(task["capabilities"][name], as_of)
        for name in CAPABILITY_IDS
    }
    tool_permissions = _tool_permission_fact(
        task["tool_permissions"], task["required_tools"], as_of)
    identity = _identity_assessment(task["identity"], as_of)
    lock_reasons = [f"path_conflict:{path}" for path in task["path_conflicts"]]
    lock_reasons += [f"resource_conflict:{name}"
                     for name in task["resource_conflicts"]]
    locks_clear = not lock_reasons

    capability_reasons = []
    missing_capabilities = []
    for name in CAPABILITY_IDS:
        fact = capability_facts[name]
        if fact["effective_state"] == "pass":
            continue
        missing_capabilities.append(name)
        _add(capability_reasons,
             f"capability_{name}_{fact['effective_state']}")
        for reason in fact["downgrade_reasons"]:
            _add(capability_reasons, f"capability_{name}_{reason}")
    if tool_permissions["effective_state"] != "pass":
        missing_capabilities.append("tool_permissions")
        _add(capability_reasons,
             f"capability_tool_permissions_"
             f"{tool_permissions['effective_state']}")
        for reason in tool_permissions["downgrade_reasons"]:
            _add(capability_reasons, f"capability_tool_permissions_{reason}")
    for tool in tool_permissions["missing_tools"]:
        _add(capability_reasons, f"tool_permission_missing:{tool}")

    decisions = []
    if missing_capabilities:
        decisions.append({
            "decision_id": "runtime_capability_gap",
            "reason_codes": list(capability_reasons),
        })
    if not identity["trusted"]:
        decisions.append({
            "decision_id": "identity_untrusted",
            "reason_codes": list(identity["reason_codes"]),
        })
    if not locks_clear:
        decisions.append({
            "decision_id": "concurrency_conflict",
            "reason_codes": list(lock_reasons),
        })

    handoff_required = bool(decisions)
    handoff_reasons = []
    for decision in decisions:
        for reason in decision["reason_codes"]:
            _add(handoff_reasons, reason)

    evidence_refs = []
    for name in CAPABILITY_IDS:
        _add_ref(evidence_refs, "runtime_capability",
                 capability_facts[name]["evidence_ref"])
    _add_ref(evidence_refs, "tool_permissions",
             tool_permissions["evidence_ref"])
    _add_ref(evidence_refs, "identity", identity["source_ref"])
    _add_ref(evidence_refs, "locks", "caller:locks")

    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "plan_usability": "not_a_dispatch",
        "mode": "observe_only",
        "dispatched": False,
        "runtime_support_inferred": False,
        "dispatch_authority": DISPATCH_AUTHORITY,
        "task_ref": task["task_ref"],
        "edition": task["edition"],
        "as_of": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_status": ("MANUAL_HANDOFF" if handoff_required
                          else "CAPABLE"),
        "runtime": {
            "runtime_id": task["runtime_id"],
            "capability_source": CAPABILITY_SOURCE,
            "capability_facts": capability_facts,
            "tool_permissions": tool_permissions,
        },
        "identity": identity,
        "locks": {
            "path_conflicts": list(task["path_conflicts"]),
            "resource_conflicts": list(task["resource_conflicts"]),
            "clear": locks_clear,
        },
        "handoff": {
            "required": handoff_required,
            "mode": "manual_session" if handoff_required else "none",
            "reason_codes": handoff_reasons,
            "missing_capabilities": missing_capabilities,
            "missing_tools": list(tool_permissions["missing_tools"]),
            "operator_actions": list(OPERATOR_ACTIONS) if handoff_required else [],
        },
        "required_human_decisions": decisions,
        "limitations": list(BASE_LIMITATIONS),
        "evidence_refs": evidence_refs,
    }


def _add_ref(refs, kind, ref):
    entry = {"kind": kind, "ref": ref}
    if entry not in refs:
        refs.append(entry)


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
        task = parse_input(raw)
    except DecisionError as error:
        return 2, diagnostic(error.code, error.path)
    return 3, _decision(task)
