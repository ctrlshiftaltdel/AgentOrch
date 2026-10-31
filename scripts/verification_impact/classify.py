"""Read-only shadow verification-impact classifier (Issue #187, AVE stage C).

Frozen contract: ``docs/design/verification-impact-c-contract.md``.

The module reads one caller snapshot, never scans the repository, never executes
a test, never writes project state (no ``.orchestration/``, no SQLite, no output
file) and never contacts the network. It returns either a diagnostic (exit code
2) or a shadow report (exit code 3, ``closure: open``). Exit code 0 is
deliberately unused: the report is ``not_a_verification_result`` and
``shadow_only`` — never an approval, a READY, a ``VERIFIED``, an authorization
or a skip instruction.

Only the Python standard library is used. Path normalisation mirrors
``scripts/orchestrator.py`` semantics on purpose but is reimplemented here so the
tool never imports the scheduler or ``scripts/agent_selection/``.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

SCHEMA_VERSION = "verification-impact-input/1"
OUTPUT_SCHEMA_VERSION = "verification-impact-output/1"
DIAGNOSTIC_SCHEMA_VERSION = "verification-impact-diagnostic/1"
TOOL_VERSION = "187-draft-1"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REF_RE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,200}$")
AC_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
CHECK_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

REQUIRED_TOP_LEVEL = (
    "schema_version", "task_ref", "edition", "base_sha", "head_sha",
    "acceptance", "changed_paths", "impact_graph", "dynamic_references",
    "checks", "old_evidence", "comparison", "requires_independent_reviewer",
)
ALLOWED_TOP_LEVEL = set(REQUIRED_TOP_LEVEL)

SCOPES = ("quick", "full", "static", "post_merge", "ci", "doc")
NODE_KINDS = ("changed_path", "test", "caller")
EDGE_KINDS = ("covers", "calls")
REFERENCE_STATES = ("known", "unknown", "missing")

# High-risk paths from contract §2. Any hit keeps coverage ``incomplete`` and a
# subset may never replace the full caller snapshot.
HIGH_RISK_PATHS = frozenset({
    "scripts/orchestrator.py", "docs/PROJECT.md", "docs/PLAN.md", "AGENTS.md",
    "scripts/validate_kit.py", "scripts/build_release.py",
})
HIGH_RISK_PREFIXES = ("kit", ".github/workflows", "editions", "release")

COVERAGE_REASON_ORDER = (
    "empty_mapping", "dynamic_references_absent", "dynamic_reference_unknown",
    "dynamic_reference_missing", "path_missing_node",
    "path_without_coverage_edge", "high_risk_path",
)

# Qualification-shaped words that must never appear in a shadow report. Unlike
# the B-stage planner, ``UNKNOWN`` is a legitimate absence marker in this
# contract (§4) and is therefore not listed here.
BANNED_WORDS = (
    "PASS", "FAIL", "NOT_RUN", "BLOCKED", "NOT_APPLICABLE",
    "PASS_EXECUTED", "PASS_EQUIVALENCE_ATTESTED", "approved", "ready",
    "verified", "may_merge", "ok", "passed", "qualified", "success",
    "green", "attempt_allowed", "dispatch_ok", "may_dispatch", "skip",
    "identity_ok", "declared_risk_level",
)
_BANNED_LOWER = {word.lower() for word in BANNED_WORDS}
# A "complete word" includes underscores and hyphens: identifiers such as
# ``not_a_verification_result`` are one word.
_WORD_RE = re.compile(r"[A-Za-z0-9_-]+")

FORBIDDEN_FIELDS = frozenset({
    # result-shaped values must not be smuggled in as inputs
    "status", "result", "passed", "pass", "ok", "qualified", "success",
    "green", "approved", "ready", "verified", "may_merge", "exit_code",
    "conclusion", "attempt_allowed", "dispatch_ok", "may_dispatch", "unlock",
    "handover_authorized", "skip", "override_approver", "approved_by",
    "waiver", "checks_cover_high_risk", "source_verified",
    "declared_risk_level",
    # identity self-reports (#170 and family claims are not evidence)
    "model_name", "model_alias", "model_family", "family", "temperature",
    "prompt_hash", "identity_ok", "reviewer_identity_proven",
})

DIAGNOSTIC_CODES = {
    "schema_invalid", "sha_invalid", "edition_invalid", "unknown_field",
    "forbidden_field", "path_invalid", "usage",
}


class ImpactError(Exception):
    """A structural input error carrying a frozen diagnostic code and pointer."""

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
    """Render a JSON-ish pointer, stopping before a forbidden-word segment."""
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


def _has_banned_word(text):
    return any(word.lower() in _BANNED_LOWER for word in _WORD_RE.findall(text))


def _require_output_safe(value, pointer):
    if _has_banned_word(value):
        raise ImpactError("schema_invalid", _pointer(pointer))
    return value


# ---------------------------------------------------------------------------
# Structural validation helpers
# ---------------------------------------------------------------------------

def _scan_forbidden(node, pointer=()):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_FIELDS:
                raise ImpactError("forbidden_field", _pointer(pointer + (key,)))
            _scan_forbidden(value, pointer + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _scan_forbidden(value, pointer + (index,))


def _require_exact_keys(obj, keys, pointer):
    missing = [key for key in keys if key not in obj]
    if missing:
        raise ImpactError("schema_invalid", _pointer(pointer + (missing[0],)))
    extra = [key for key in obj if key not in keys]
    if extra:
        raise ImpactError("unknown_field", _pointer(pointer + (extra[0],)))


def _require_object(value, pointer):
    if not isinstance(value, dict):
        raise ImpactError("schema_invalid", _pointer(pointer))
    return value


def _require_list(value, pointer, minimum=0):
    if not isinstance(value, list) or len(value) < minimum:
        raise ImpactError("schema_invalid", _pointer(pointer))
    return value


def _require_bool(value, pointer):
    if not isinstance(value, bool):
        raise ImpactError("schema_invalid", _pointer(pointer))
    return value


def _require_str(value, pointer, minimum=1, maximum=None, pattern=None):
    if not isinstance(value, str) or len(value) < minimum:
        raise ImpactError("schema_invalid", _pointer(pointer))
    if maximum is not None and len(value) > maximum:
        raise ImpactError("schema_invalid", _pointer(pointer))
    if pattern is not None and not pattern.fullmatch(value):
        raise ImpactError("schema_invalid", _pointer(pointer))
    return _require_output_safe(value, pointer)


def _require_sha(value, pointer):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ImpactError("sha_invalid", _pointer(pointer))
    return value


def _require_optional_str(value, pointer, maximum=200):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) == 0 or len(value) > maximum:
        raise ImpactError("schema_invalid", _pointer(pointer))
    return _require_output_safe(value, pointer)


def normalize_path(raw, pointer):
    """Mirror scripts/orchestrator.py:normalize_path without importing it."""
    if not isinstance(raw, str):
        raise ImpactError("schema_invalid", _pointer(pointer))
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
        raise ImpactError("path_invalid", _pointer(pointer))
    return str(posix).rstrip("/") or "."


# ---------------------------------------------------------------------------
# Snapshot parsing
# ---------------------------------------------------------------------------

def _parse_acceptance(raw):
    items = []
    for index, item in enumerate(_require_list(raw, ("acceptance",), 1)):
        pointer = ("acceptance", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("ac_id", "text"), pointer)
        ac_id = _require_str(item["ac_id"], pointer + ("ac_id",), maximum=64,
                             pattern=AC_RE)
        text = _require_str(item["text"], pointer + ("text",), maximum=500)
        items.append({"ac_id": ac_id, "text": text})
    return items


def _parse_changed_paths(raw):
    items = []
    for index, item in enumerate(_require_list(raw, ("changed_paths",), 1)):
        pointer = ("changed_paths", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("path", "source_ref"), pointer)
        raw_path = item["path"]
        if not isinstance(raw_path, str) or len(raw_path) > 300:
            raise ImpactError("schema_invalid", _pointer(pointer + ("path",)))
        items.append({
            "path": normalize_path(raw_path, pointer + ("path",)),
            "source_ref": _require_str(item["source_ref"], pointer + ("source_ref",),
                                       maximum=200, pattern=REF_RE),
        })
    return items


def _parse_graph(raw):
    graph = _require_object(raw, ("impact_graph",))
    _require_exact_keys(graph, ("nodes", "edges"), ("impact_graph",))

    nodes = []
    node_ids = set()
    for index, item in enumerate(_require_list(graph["nodes"], ("impact_graph", "nodes"))):
        pointer = ("impact_graph", "nodes", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("node_id", "kind", "path", "source_ref"), pointer)
        node_id = _require_str(item["node_id"], pointer + ("node_id",), maximum=64,
                               pattern=ID_RE)
        if node_id in node_ids:
            raise ImpactError("schema_invalid", _pointer(pointer + ("node_id",)))
        node_ids.add(node_id)
        kind = item["kind"]
        if kind not in NODE_KINDS:
            raise ImpactError("schema_invalid", _pointer(pointer + ("kind",)))
        raw_path = item["path"]
        if not isinstance(raw_path, str) or len(raw_path) > 300:
            raise ImpactError("schema_invalid", _pointer(pointer + ("path",)))
        nodes.append({
            "node_id": node_id,
            "kind": kind,
            "path": normalize_path(raw_path, pointer + ("path",)),
            "source_ref": _require_str(item["source_ref"], pointer + ("source_ref",),
                                       maximum=200, pattern=REF_RE),
        })

    edges = []
    edge_ids = set()
    for index, item in enumerate(_require_list(graph["edges"], ("impact_graph", "edges"))):
        pointer = ("impact_graph", "edges", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("edge_id", "from_node", "to_node", "kind",
                                   "source_ref"), pointer)
        edge_id = _require_str(item["edge_id"], pointer + ("edge_id",), maximum=64,
                               pattern=ID_RE)
        if edge_id in edge_ids:
            raise ImpactError("schema_invalid", _pointer(pointer + ("edge_id",)))
        edge_ids.add(edge_id)
        from_node = _require_str(item["from_node"], pointer + ("from_node",),
                                 maximum=64, pattern=ID_RE)
        to_node = _require_str(item["to_node"], pointer + ("to_node",),
                               maximum=64, pattern=ID_RE)
        if from_node not in node_ids:
            raise ImpactError("schema_invalid", _pointer(pointer + ("from_node",)))
        if to_node not in node_ids:
            raise ImpactError("schema_invalid", _pointer(pointer + ("to_node",)))
        kind = item["kind"]
        if kind not in EDGE_KINDS:
            raise ImpactError("schema_invalid", _pointer(pointer + ("kind",)))
        edges.append({
            "edge_id": edge_id,
            "from_node": from_node,
            "to_node": to_node,
            "kind": kind,
            "source_ref": _require_str(item["source_ref"], pointer + ("source_ref",),
                                       maximum=200, pattern=REF_RE),
        })
    return {"nodes": nodes, "edges": edges}


def _parse_dynamic_references(raw):
    items = []
    for index, item in enumerate(_require_list(raw, ("dynamic_references",))):
        pointer = ("dynamic_references", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("item_id", "reference_state", "source_ref"),
                            pointer)
        item_id = _require_str(item["item_id"], pointer + ("item_id",), maximum=80,
                               pattern=ID_RE)
        state = item["reference_state"]
        if state not in REFERENCE_STATES:
            raise ImpactError("schema_invalid",
                              _pointer(pointer + ("reference_state",)))
        items.append({
            "item_id": item_id,
            "reference_state": state,
            "source_ref": _require_str(item["source_ref"], pointer + ("source_ref",),
                                       maximum=200, pattern=REF_RE),
        })
    return items


CHECK_KEYS = ("check_id", "scope", "source_ref", "mandatory", "ci_required",
              "approval_class", "security_class", "cacheable", "command",
              "input_hash", "dependency_fingerprint", "environment_fingerprint")


def _parse_checks(raw):
    checks = []
    seen = set()
    for index, item in enumerate(_require_list(raw, ("checks",), 1)):
        pointer = ("checks", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, CHECK_KEYS, pointer)
        check_id = _require_str(item["check_id"], pointer + ("check_id",),
                                maximum=64, pattern=CHECK_RE)
        if check_id in seen:
            raise ImpactError("schema_invalid", _pointer(pointer + ("check_id",)))
        seen.add(check_id)
        scope = item["scope"]
        if scope not in SCOPES:
            raise ImpactError("schema_invalid", _pointer(pointer + ("scope",)))
        checks.append({
            "check_id": check_id,
            "scope": scope,
            "source_ref": _require_str(item["source_ref"], pointer + ("source_ref",),
                                       maximum=200, pattern=REF_RE),
            "mandatory": _require_bool(item["mandatory"], pointer + ("mandatory",)),
            "ci_required": _require_bool(item["ci_required"],
                                         pointer + ("ci_required",)),
            "approval_class": _require_bool(item["approval_class"],
                                            pointer + ("approval_class",)),
            "security_class": _require_bool(item["security_class"],
                                            pointer + ("security_class",)),
            "cacheable": _require_bool(item["cacheable"], pointer + ("cacheable",)),
            "command": _require_str(item["command"], pointer + ("command",),
                                    maximum=500),
            "input_hash": _require_optional_str(item["input_hash"],
                                                pointer + ("input_hash",)),
            "dependency_fingerprint": _require_optional_str(
                item["dependency_fingerprint"],
                pointer + ("dependency_fingerprint",)),
            "environment_fingerprint": _require_optional_str(
                item["environment_fingerprint"],
                pointer + ("environment_fingerprint",)),
        })
    return checks


def _parse_old_evidence(raw):
    evidence = []
    seen = set()
    for index, item in enumerate(_require_list(raw, ("old_evidence",))):
        pointer = ("old_evidence", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("evidence_id", "check_id", "ran_on_sha",
                                   "command", "input_hash",
                                   "dependency_fingerprint",
                                   "environment_fingerprint", "tests"), pointer)
        evidence_id = _require_str(item["evidence_id"], pointer + ("evidence_id",),
                                   maximum=80, pattern=ID_RE)
        tests = []
        for test_index, test in enumerate(
                _require_list(item["tests"], pointer + ("tests",))):
            test_pointer = pointer + ("tests", test_index)
            test = _require_object(test, test_pointer)
            _require_exact_keys(test, ("test_id", "path", "modified_since_evidence"),
                                test_pointer)
            raw_path = test["path"]
            if not isinstance(raw_path, str) or len(raw_path) > 300:
                raise ImpactError("schema_invalid",
                                  _pointer(test_pointer + ("path",)))
            tests.append({
                "test_id": _require_str(test["test_id"], test_pointer + ("test_id",),
                                        maximum=120, pattern=ID_RE),
                "path": normalize_path(raw_path, test_pointer + ("path",)),
                "modified_since_evidence": _require_bool(
                    test["modified_since_evidence"],
                    test_pointer + ("modified_since_evidence",)),
            })
        parsed = {
            "evidence_id": evidence_id,
            "check_id": _require_str(item["check_id"], pointer + ("check_id",),
                                     maximum=64, pattern=CHECK_RE),
            "ran_on_sha": _require_sha(item["ran_on_sha"], pointer + ("ran_on_sha",)),
            "command": _require_str(item["command"], pointer + ("command",),
                                    maximum=500),
            "input_hash": _require_optional_str(item["input_hash"],
                                                pointer + ("input_hash",)),
            "dependency_fingerprint": _require_optional_str(
                item["dependency_fingerprint"],
                pointer + ("dependency_fingerprint",)),
            "environment_fingerprint": _require_optional_str(
                item["environment_fingerprint"],
                pointer + ("environment_fingerprint",)),
            "tests": tests,
        }
        if evidence_id in seen:
            # Contract §4: duplicate copies of the same evidence_id may be
            # dropped, but they are validated first so a malformed duplicate
            # cannot hide as a structural error.
            continue
        seen.add(evidence_id)
        evidence.append(parsed)
    return evidence


def _parse_comparison(raw):
    comparison = _require_object(raw, ("comparison",))
    _require_exact_keys(comparison, ("suite_failures_included", "suite_failures",
                                     "timings_included", "suite_seconds_before",
                                     "suite_seconds_after"), ("comparison",))
    failures_included = _require_bool(comparison["suite_failures_included"],
                                      ("comparison", "suite_failures_included"))
    timings_included = _require_bool(comparison["timings_included"],
                                     ("comparison", "timings_included"))
    failures = []
    for index, item in enumerate(_require_list(comparison["suite_failures"],
                                               ("comparison", "suite_failures"))):
        pointer = ("comparison", "suite_failures", index)
        item = _require_object(item, pointer)
        _require_exact_keys(item, ("failure_id", "test_path", "source_ref"), pointer)
        raw_path = item["test_path"]
        if not isinstance(raw_path, str) or len(raw_path) > 300:
            raise ImpactError("schema_invalid", _pointer(pointer + ("test_path",)))
        failures.append({
            "failure_id": _require_str(item["failure_id"],
                                       pointer + ("failure_id",), maximum=80,
                                       pattern=ID_RE),
            "test_path": normalize_path(raw_path, pointer + ("test_path",)),
            "source_ref": _require_str(item["source_ref"], pointer + ("source_ref",),
                                       maximum=200, pattern=REF_RE),
        })
    if not failures_included and failures:
        raise ImpactError("schema_invalid",
                          _pointer(("comparison", "suite_failures")))

    def _seconds(value, pointer):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ImpactError("schema_invalid", _pointer(pointer))
        if value < 0:
            raise ImpactError("schema_invalid", _pointer(pointer))
        return float(value)

    before = _seconds(comparison["suite_seconds_before"],
                      ("comparison", "suite_seconds_before"))
    after = _seconds(comparison["suite_seconds_after"],
                     ("comparison", "suite_seconds_after"))
    if timings_included and (before is None or after is None):
        raise ImpactError("schema_invalid",
                          _pointer(("comparison", "suite_seconds_before")))
    return {
        "suite_failures_included": failures_included,
        "suite_failures": failures,
        "timings_included": timings_included,
        "suite_seconds_before": before,
        "suite_seconds_after": after,
    }


def parse_task(raw):
    """Validate a versioned input snapshot and return a normalised task."""
    if not isinstance(raw, dict):
        raise ImpactError("schema_invalid", "$")

    _scan_forbidden(raw)

    for key in raw:
        if key not in ALLOWED_TOP_LEVEL:
            raise ImpactError("unknown_field", _pointer((key,)))

    missing = [key for key in REQUIRED_TOP_LEVEL if key not in raw]
    if missing:
        raise ImpactError("schema_invalid", _pointer((missing[0],)))

    if raw["schema_version"] != SCHEMA_VERSION:
        raise ImpactError("schema_invalid", "$.schema_version")

    task_ref = _require_str(raw["task_ref"], ("task_ref",), maximum=200)
    edition = raw["edition"]
    if edition not in ("local", "github"):
        raise ImpactError("edition_invalid", "$.edition")
    base_sha = _require_sha(raw["base_sha"], ("base_sha",))
    head_sha = _require_sha(raw["head_sha"], ("head_sha",))

    graph = _parse_graph(raw["impact_graph"])
    return {
        "task_ref": task_ref,
        "edition": edition,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "acceptance": _parse_acceptance(raw["acceptance"]),
        "changed_paths": _parse_changed_paths(raw["changed_paths"]),
        "graph": graph,
        "dynamic_references": _parse_dynamic_references(raw["dynamic_references"]),
        "checks": _parse_checks(raw["checks"]),
        "old_evidence": _parse_old_evidence(raw["old_evidence"]),
        "comparison": _parse_comparison(raw["comparison"]),
        "requires_reviewer": _require_bool(
            raw["requires_independent_reviewer"],
            ("requires_independent_reviewer",)),
    }


# ---------------------------------------------------------------------------
# Impact graph (snapshot nodes + explicit edges only)
# ---------------------------------------------------------------------------

def is_high_risk_path(path):
    if path in HIGH_RISK_PATHS:
        return True
    for prefix in HIGH_RISK_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _coverage(task):
    nodes_by_id = {node["node_id"]: node for node in task["graph"]["nodes"]}
    changed_nodes_by_path = {}
    for node in task["graph"]["nodes"]:
        if node["kind"] == "changed_path":
            changed_nodes_by_path.setdefault(node["path"], []).append(node["node_id"])

    covered = []
    uncovered = []
    observed = []
    tokens = []
    reasons = set()

    if not task["graph"]["edges"]:
        reasons.add("empty_mapping")
        tokens.append("coverage:empty_mapping")

    for changed in task["changed_paths"]:
        path = changed["path"]
        node_ids = changed_nodes_by_path.get(path)
        if not node_ids:
            uncovered.append(path)
            reasons.add("path_missing_node")
            tokens.append(f"coverage:path_missing_node:{path}")
            continue
        targets = []
        for edge in task["graph"]["edges"]:
            if edge["kind"] != "covers" or edge["from_node"] not in node_ids:
                continue
            target = nodes_by_id[edge["to_node"]]
            if target["kind"] == "test":
                targets.append(target)
        if not targets:
            uncovered.append(path)
            reasons.add("path_without_coverage_edge")
            tokens.append(f"coverage:path_without_coverage_edge:{path}")
            continue
        covered.append(path)
        seen = {(item["test_id"], item["covers_path"]) for item in observed}
        for target in targets:
            key = (target["node_id"], path)
            if key in seen:
                continue
            seen.add(key)
            observed.append({
                "test_id": target["node_id"],
                "path": target["path"],
                "covers_path": path,
                "source_ref": target["source_ref"],
            })

    dynamic = task["dynamic_references"]
    if not dynamic:
        reasons.add("dynamic_references_absent")
        tokens.append("coverage:dynamic_references_absent")
    for item in dynamic:
        if item["reference_state"] == "unknown":
            reasons.add("dynamic_reference_unknown")
            tokens.append(f"coverage:dynamic_reference_unknown:{item['item_id']}")
        elif item["reference_state"] == "missing":
            reasons.add("dynamic_reference_missing")
            tokens.append(f"coverage:dynamic_reference_missing:{item['item_id']}")

    high_risk = []
    for changed in task["changed_paths"]:
        if is_high_risk_path(changed["path"]):
            high_risk.append(changed["path"])
            reasons.add("high_risk_path")
            tokens.append(f"coverage:high_risk_path:{changed['path']}")

    ordered_reasons = [reason for reason in COVERAGE_REASON_ORDER
                       if reason in reasons]
    return {
        "coverage_status": "incomplete" if ordered_reasons else "complete",
        "coverage_reasons": ordered_reasons,
        "covered_paths": covered,
        "uncovered_paths": uncovered,
        "high_risk_paths": high_risk,
        "observed_tests": observed,
        "unknown_tokens": tokens,
    }


# ---------------------------------------------------------------------------
# Old-evidence reuse
# ---------------------------------------------------------------------------

def _reuse(task):
    checks = {check["check_id"]: check for check in task["checks"]}
    decisions = []
    for evidence in task["old_evidence"]:
        reasons = []
        check = checks.get(evidence["check_id"])
        decision = None
        if check is None:
            decision = "rejected"
            reasons.append("unknown_check")
        elif check["ci_required"]:
            decision = "not_reused"
            reasons.append("ci_required")
        elif check["approval_class"]:
            decision = "not_reused"
            reasons.append("approval_class")
        elif check["security_class"]:
            decision = "not_reused"
            reasons.append("security_class")
        elif not check["cacheable"]:
            decision = "not_reused"
            reasons.append("not_cacheable")
        else:
            fields = ("input_hash", "dependency_fingerprint",
                      "environment_fingerprint")
            if (not check["command"] or not evidence["command"]
                    or any(check[field] is None or evidence[field] is None
                           for field in fields)):
                decision = "rejected"
                reasons.append("missing_fingerprint")
            elif evidence["command"] != check["command"]:
                decision = "rejected"
                reasons.append("command_mismatch")
            elif evidence["input_hash"] != check["input_hash"]:
                decision = "rejected"
                reasons.append("input_changed")
            elif (evidence["dependency_fingerprint"]
                  != check["dependency_fingerprint"]):
                decision = "rejected"
                reasons.append("dependency_changed")
            elif (evidence["environment_fingerprint"]
                  != check["environment_fingerprint"]):
                decision = "rejected"
                reasons.append("environment_drift")
            elif any(test["modified_since_evidence"]
                     for test in evidence["tests"]):
                decision = "rejected"
                reasons.append("modified_test")
            else:
                decision = "applicable_on_head"
                reasons.extend(["fingerprints_match", "caller_marked_cacheable"])
                reasons.append("same_head" if evidence["ran_on_sha"]
                               == task["head_sha"] else "older_sha")

        decisions.append({
            "check_id": evidence["check_id"],
            "evidence_id": evidence["evidence_id"],
            "reuse_decision": decision,
            "reasons": reasons,
            "ran_on_sha": evidence["ran_on_sha"],
            "head_sha": task["head_sha"],
            "same_head": evidence["ran_on_sha"] == task["head_sha"],
            # An old-SHA result is never presented as a current-HEAD rerun.
            "head_rerun": False,
        })
    return decisions


# ---------------------------------------------------------------------------
# Comparison and review package
# ---------------------------------------------------------------------------

def _comparison(task, coverage):
    observed_paths = {item["path"] for item in coverage["observed_tests"]}
    failures = []
    out_of_mapping = []
    for failure in task["comparison"]["suite_failures"]:
        mapped = failure["test_path"] in observed_paths
        enriched = dict(failure, mapped=mapped)
        failures.append(enriched)
        if not mapped:
            out_of_mapping.append(dict(failure))

    included = task["comparison"]["suite_failures_included"]
    if included:
        false_negatives = [item["failure_id"] for item in out_of_mapping]
    else:
        false_negatives = "UNKNOWN"

    timings_included = task["comparison"]["timings_included"]
    if timings_included:
        time_saved = round(task["comparison"]["suite_seconds_before"]
                           - task["comparison"]["suite_seconds_after"], 3)
    else:
        time_saved = "UNKNOWN"

    return {
        "suite_failures_included": included,
        "timings_included": timings_included,
        "false_negatives": false_negatives,
        "time_saved": time_saved,
        "out_of_mapping_failures": out_of_mapping,
        "mapping_authority": "observation_only",
        "failures": failures,
    }


def _unknowns(task, coverage, reuse, comparison):
    tokens = list(coverage["unknown_tokens"])
    if not task["comparison"]["suite_failures_included"]:
        tokens.append("comparison:false_negatives_unknown")
    if not task["comparison"]["timings_included"]:
        tokens.append("comparison:time_saved_unknown")
    if task["requires_reviewer"]:
        tokens.append("identity:reviewer_unproven")
    for decision in reuse:
        if decision["reuse_decision"] == "applicable_on_head":
            tokens.append(f"reuse:applicable_on_head:{decision['evidence_id']}")
    unique = []
    for token in tokens:
        if token not in unique:
            unique.append(token)
    return unique


# ---------------------------------------------------------------------------
# Output documents
# ---------------------------------------------------------------------------

def build_report(task):
    coverage = _coverage(task)
    reuse = _reuse(task)
    comparison = _comparison(task, coverage)
    unknowns = _unknowns(task, coverage, reuse, comparison)

    if task["requires_reviewer"]:
        identity = {"requirement": "required", "assessment": "unproven"}
    else:
        identity = {"requirement": "not_required", "assessment": "not_assessed"}

    caller_snapshot = [dict(check) for check in task["checks"]]
    suggested = [dict(check) for check in task["checks"]]

    report = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "plan_usability": "not_a_verification_result",
        "mode": "shadow_only",
        "task_ref": task["task_ref"],
        "edition": task["edition"],
        "base_sha": task["base_sha"],
        "head_sha": task["head_sha"],
        "closure": "open",
        "impact": {
            "authority": "caller_snapshot_only",
            "repository_scanned": False,
            "coverage_status": coverage["coverage_status"],
            "coverage_reasons": coverage["coverage_reasons"],
            "covered_paths": coverage["covered_paths"],
            "uncovered_paths": coverage["uncovered_paths"],
            "high_risk_paths": coverage["high_risk_paths"],
            "dynamic_references": [dict(item) for item in task["dynamic_references"]],
            "nodes": [dict(node) for node in task["graph"]["nodes"]],
            "edges": [dict(edge) for edge in task["graph"]["edges"]],
            "observed_tests": coverage["observed_tests"],
            "mapped_tests_authority": "observation_only",
        },
        "checks": {
            "authority": "unverified_snapshot",
            "cache_policy": "not_cacheable_by_default",
            "caller_snapshot": caller_snapshot,
            "suggested_checks": suggested,
            "counterfactual_removed": [],
            "subset_claimed_sufficient": False,
        },
        "reuse": reuse,
        "comparison": {
            "suite_failures_included": comparison["suite_failures_included"],
            "timings_included": comparison["timings_included"],
            "false_negatives": comparison["false_negatives"],
            "time_saved": comparison["time_saved"],
            "out_of_mapping_failures": comparison["out_of_mapping_failures"],
            "mapping_authority": comparison["mapping_authority"],
        },
        "review_package": {
            "acceptance": [dict(item) for item in task["acceptance"]],
            "mandatory_checks": [
                {"check_id": check["check_id"], "scope": check["scope"],
                 "source_ref": check["source_ref"]}
                for check in task["checks"] if check["mandatory"]
            ],
            "unknowns": unknowns,
            "rejected_reuse": [
                {"check_id": item["check_id"], "evidence_id": item["evidence_id"],
                 "reasons": list(item["reasons"])}
                for item in reuse if item["reuse_decision"] == "rejected"
            ],
            "failures": comparison["failures"],
            "gates_unchanged": True,
        },
        "identity": identity,
        "policy": {
            "effect": "current_rules_unchanged",
            "cache_default": "not_cacheable",
            "refs": ["docs/orchestration.md", "editions/local/master.md",
                     "editions/github/master.md"],
        },
    }
    return report


def diagnostic(code, path="$"):
    document = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "plan_usability": "not_a_verification_result",
        "diagnostic": {"code": code, "path": path},
    }
    if banned_hits(document):
        document["diagnostic"] = {"code": "schema_invalid", "path": "$"}
    return document


def build(raw):
    """Validate ``raw`` and return ``(exit_code, document)``. Never returns 0."""
    try:
        task = parse_task(raw)
    except ImpactError as error:
        return 2, diagnostic(error.code, error.path)
    report = build_report(task)
    if banned_hits(report):
        # The final scan must never become an uncaught exception or a traceback.
        return 2, diagnostic("schema_invalid", "$")
    return 3, report


def loads(text):
    """Parse JSON text and classify it. Returns ``(exit_code, document)``."""
    try:
        raw = json.loads(text)
    except ValueError:
        return 2, diagnostic("schema_invalid", "$")
    return build(raw)
