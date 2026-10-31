#!/usr/bin/env python3
"""Read-only release preparation report (Issue #194, Epic #191 stage C).

The report answers one question: given the caller's latest *published* tag, a
snapshot of merged changes and the evidence the caller can actually point at,
which **single** next version may be prepared?

It is never a release. ``plan_usability`` is ``not_a_release`` and
``release_authorized`` is always ``false``: the standing automatic-release
authorization range is empty (``release/README.md`` §5.3), so a candidate still
needs an explicit Owner decision, a registered release task, an independent
Master review and the full release verification.

Rules implemented here, all from ``release/README.md`` §5.3 / Issue #192:

* the baseline is the caller-supplied latest published tag; the tag and
  ``VERSION.md``'s ``Release version`` must agree, otherwise the report blocks;
* unpublished items are deduplicated by id (duplicates merge pessimistically:
  the worst state and the highest bump win, so nothing is over-claimed);
* only items that are both merged *and* verified count as eligible;
* each item's compatibility class (``patch`` / ``minor`` / ``major``) is
  caller-supplied evidence, never inferred from prose; this tool validates the
  class and applies the aggregation rules below;
* PATCH / MINOR / MAJOR follow backward-compatibility, mixed changes take the
  maximum bump, and at most one next version is suggested;
* an urgent fix may preempt ordinary batching, but it never lowers the bump and
  never produces a second suggestion;
* no eligible change means no release (empty releases are not allowed);
* missing or unreliable identity / CI / permission / asset evidence blocks the
  report and suppresses the version suggestion;
* ``edition`` is echoed as a snapshot field only, and the tool never claims to
  be a persistent background service. It is an on-demand, read-only CLI.

Only the Python standard library is used. The tool opens exactly one input file
read-only: it writes no project file, starts no subprocess, and performs no
network access.
"""
from __future__ import annotations

import re

SCHEMA_VERSION = "release-prep-input/1"
REPORT_SCHEMA_VERSION = "release-prep-report/1"
DIAGNOSTIC_SCHEMA_VERSION = "release-prep-diagnostic/1"
TOOL_VERSION = "194-draft-1"

TAG_RE = re.compile(r"^v(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(?:\.(?P<patch>[0-9]+))?$")
VERSION_RE = re.compile(r"^(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)$")
ID_RE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,200}$")

EDITIONS = ("local", "github")
KINDS = ("patch", "minor", "major")
STATES = ("merged_verified", "merged_unverified", "unmerged", "blocked", "reverted")
EVIDENCE_KEYS = ("identity", "ci", "permissions", "assets")
EVIDENCE_VALUES = ("reliable", "missing", "unreliable")

# A duplicate id is merged pessimistically: the least claim wins. ``reverted``
# is the strongest reason not to count a change, then ``blocked``, then an
# unmerged/unverified state. The order is severity, not preference.
STATE_SEVERITY = {
    "merged_verified": 0,
    "merged_unverified": 1,
    "unmerged": 2,
    "blocked": 3,
    "reverted": 4,
}
EXCLUDED_REASONS = {
    "merged_unverified": "not_verified",
    "unmerged": "not_merged",
    "blocked": "blocked",
    "reverted": "reverted",
}
KIND_ORDER = {"patch": 0, "minor": 1, "major": 2}

REQUIRED_TOP_LEVEL = (
    "schema_version", "edition", "latest_published_tag", "items",
)
OPTIONAL_TOP_LEVEL = ("release_version_record", "evidence")
ALLOWED_TOP_LEVEL = set(REQUIRED_TOP_LEVEL) | set(OPTIONAL_TOP_LEVEL)

REQUIRED_ITEM_FIELDS = ("id", "kind", "state", "summary")
OPTIONAL_ITEM_FIELDS = ("urgent", "risks", "dependencies")
ALLOWED_ITEM_FIELDS = set(REQUIRED_ITEM_FIELDS) | set(OPTIONAL_ITEM_FIELDS)

DIAGNOSTIC_CODES = {
    "schema_invalid", "tag_invalid", "edition_invalid", "unknown_field", "usage",
}

STANDARD_VERIFICATION_COMMANDS = (
    "uv run python scripts/validate_kit.py",
    "uv run python -m unittest discover -s tests",
    "uv run python scripts/run_delivery_scenarios.py --json",
    "uv run python scripts/report_metrics.py --gold-sample "
    "--since 2026-01-01 --until 2026-02-01 --json",
    "uv run python scripts/architecture/check.py --project . --json",
    "uv run python -m py_compile scripts/orchestrator.py scripts/validate_kit.py",
)


class ReportError(Exception):
    """A structural input error. Carries the frozen diagnostic code and path."""

    def __init__(self, code, path="$"):
        if code not in DIAGNOSTIC_CODES:
            raise RuntimeError(f"unknown diagnostic code: {code}")
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}")


def diagnostic(code, path="$"):
    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "plan_usability": "not_a_release",
        "release_authorized": False,
        "diagnostic": {"code": code, "path": path},
    }


# --- validation -------------------------------------------------------------

def _fail(code, path):
    raise ReportError(code, path)


def _check_object(value, path):
    if not isinstance(value, dict):
        _fail("schema_invalid", path)
    return value


def _check_string(value, path, pattern=None, maximum=None):
    if not isinstance(value, str) or not value:
        _fail("schema_invalid", path)
    if pattern is not None and not pattern.match(value):
        _fail("schema_invalid", path)
    if maximum is not None and len(value) > maximum:
        _fail("schema_invalid", path)
    return value


def _check_string_list(value, path):
    if not isinstance(value, list):
        _fail("schema_invalid", path)
    for index, entry in enumerate(value):
        _check_string(entry, f"{path}[{index}]", maximum=300)
    return list(value)


def _check_unknown(payload, allowed, path):
    for key in payload:
        if key not in allowed:
            _fail("unknown_field", f"{path}.{key}")


def _parse_item(raw, index):
    path = f"$.items[{index}]"
    _check_object(raw, path)
    _check_unknown(raw, ALLOWED_ITEM_FIELDS, path)
    for field in REQUIRED_ITEM_FIELDS:
        if field not in raw:
            _fail("schema_invalid", f"{path}.{field}")

    item_id = _check_string(raw["id"], f"{path}.id", pattern=ID_RE)
    kind = raw["kind"]
    if kind not in KINDS:
        _fail("schema_invalid", f"{path}.kind")
    state = raw["state"]
    if state not in STATES:
        _fail("schema_invalid", f"{path}.state")
    summary = _check_string(raw["summary"], f"{path}.summary", maximum=500)

    urgent = raw.get("urgent", False)
    if not isinstance(urgent, bool):
        _fail("schema_invalid", f"{path}.urgent")
    risks = _check_string_list(raw.get("risks", []), f"{path}.risks")
    dependencies = _check_string_list(
        raw.get("dependencies", []), f"{path}.dependencies")

    return {
        "id": item_id,
        "kind": kind,
        "state": state,
        "summary": summary,
        "urgent": urgent,
        "risks": risks,
        "dependencies": dependencies,
    }


def _parse_evidence(raw):
    statuses = {key: "missing" for key in EVIDENCE_KEYS}
    if raw is None:
        return statuses
    path = "$.evidence"
    _check_object(raw, path)
    for key in raw:
        if key not in EVIDENCE_KEYS:
            _fail("unknown_field", f"{path}.{key}")
    for key, value in raw.items():
        if value not in EVIDENCE_VALUES:
            _fail("schema_invalid", f"{path}.{key}")
        statuses[key] = value
    return statuses


def parse(raw):
    """Validate and normalise a caller snapshot. Raises ``ReportError``."""
    _check_object(raw, "$")
    _check_unknown(raw, ALLOWED_TOP_LEVEL, "$")
    for field in REQUIRED_TOP_LEVEL:
        if field not in raw:
            _fail("schema_invalid", f"$.{field}")

    if raw["schema_version"] != SCHEMA_VERSION:
        _fail("schema_invalid", "$.schema_version")
    if raw["edition"] not in EDITIONS:
        _fail("edition_invalid", "$.edition")

    tag = raw["latest_published_tag"]
    if not isinstance(tag, str) or not TAG_RE.match(tag):
        _fail("tag_invalid", "$.latest_published_tag")

    record = raw.get("release_version_record")
    record_version = None
    if record is not None:
        if not isinstance(record, str) or not VERSION_RE.match(record):
            _fail("schema_invalid", "$.release_version_record")
        record_version = _version_parts(record)

    items = raw["items"]
    if not isinstance(items, list):
        _fail("schema_invalid", "$.items")

    return {
        "edition": raw["edition"],
        "tag": tag,
        "record": record,
        "record_version": record_version,
        "items": [_parse_item(item, index) for index, item in enumerate(items)],
        "evidence": _parse_evidence(raw.get("evidence")),
    }


# --- planning ---------------------------------------------------------------

def _merge_duplicates(items):
    groups = {}
    order = []
    for item in items:
        if item["id"] not in groups:
            groups[item["id"]] = []
            order.append(item["id"])
        groups[item["id"]].append(item)

    merged = []
    for item_id in order:
        group = groups[item_id]
        first = group[0]
        states = [entry["state"] for entry in group]
        kinds = [entry["kind"] for entry in group]
        risks = []
        dependencies = []
        for entry in group:
            for risk in entry["risks"]:
                if risk not in risks:
                    risks.append(risk)
            for dependency in entry["dependencies"]:
                if dependency not in dependencies:
                    dependencies.append(dependency)
        merged.append({
            "id": item_id,
            "kind": max(kinds, key=KIND_ORDER.__getitem__),
            "state": max(states, key=STATE_SEVERITY.__getitem__),
            "summary": first["summary"],
            "urgent": any(entry["urgent"] for entry in group),
            "risks": risks,
            "dependencies": dependencies,
        })
    duplicates = [item_id for item_id in order if len(groups[item_id]) > 1]
    return merged, duplicates


def _version_parts(text):
    match = VERSION_RE.match(text)
    return (int(match.group("major")), int(match.group("minor")),
            int(match.group("patch")))


def _tag_parts(tag):
    match = TAG_RE.match(tag)
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch") or 0)
    form = "three_part" if match.group("patch") is not None else "two_part"
    return (major, minor, patch), form


def _bump(version, kind):
    major, minor, patch = version
    if kind == "major":
        return (major + 1, 0, 0)
    if kind == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def _render(version):
    return "v{}.{}.{}".format(*version)


def _blocked_reasons(evidence, record_version, tag_version):
    reasons = []
    for key in EVIDENCE_KEYS:
        status = evidence[key]
        if status != "reliable":
            reasons.append(f"{key}_{status}")
    if record_version is None:
        reasons.append("version_record_missing")
    elif record_version != tag_version:
        reasons.append("version_baseline_mismatch")
    return reasons


def _recommendation_reason(status, blocked_reasons):
    if status == "blocked":
        if "version_baseline_mismatch" in blocked_reasons:
            return "version_baseline_mismatch"
        if "version_record_missing" in blocked_reasons:
            return "version_record_missing"
        return "blocked_evidence"
    if status == "no_release":
        return "empty_release_not_allowed"
    return "max_bump_of_eligible_changes"


def plan(parsed):
    baseline_version, tag_form = _tag_parts(parsed["tag"])
    merged, duplicates = _merge_duplicates(parsed["items"])
    eligible = [item for item in merged if item["state"] == "merged_verified"]
    excluded = [
        {"id": item["id"], "reason": EXCLUDED_REASONS[item["state"]]}
        for item in merged if item["state"] != "merged_verified"
    ]

    blocked_reasons = _blocked_reasons(
        parsed["evidence"], parsed["record_version"], baseline_version)
    if blocked_reasons:
        status = "blocked"
    elif not eligible:
        status = "no_release"
    else:
        status = "candidate"

    urgent_ids = [item["id"] for item in eligible if item["urgent"]]
    if status == "candidate":
        bump = max((item["kind"] for item in eligible),
                   key=KIND_ORDER.__getitem__)
        next_version = _render(_bump(baseline_version, bump))
        trigger = "urgent_fix" if urgent_ids else "batch"
    else:
        bump = None
        next_version = None
        trigger = None

    reason = _recommendation_reason(status, blocked_reasons)

    commands = list(STANDARD_VERIFICATION_COMMANDS)
    if next_version is not None:
        commands.append(
            "uv run python scripts/build_release.py --release-version "
            + next_version[1:])
        commands.append(
            "uv run python scripts/build_release.py --release-version "
            + next_version[1:] + " --output-dir /tmp/release-b")

    record = parsed["record"]
    consistent = parsed["record_version"] == baseline_version

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "plan_usability": "not_a_release",
        "release_authorized": False,
        "mode": "on_demand_read_only",
        "persistent_background_service": False,
        "edition": parsed["edition"],
        "edition_role": "snapshot_only",
        "baseline": {
            "published_tag": parsed["tag"],
            "normalized_tag": _render(baseline_version),
            "version": "{}.{}.{}".format(*baseline_version),
            "tag_form": tag_form,
            "release_version_record": record,
            "consistent": consistent,
        },
        "status": status,
        "blocked_reasons": blocked_reasons,
        "changes": {
            "received": len(parsed["items"]),
            "unique": len(merged),
            "duplicate_ids": duplicates,
            "duplicate_occurrences": len(parsed["items"]) - len(merged),
            "eligible": [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "urgent": item["urgent"],
                    "summary": item["summary"],
                    "risks": item["risks"],
                    "dependencies": item["dependencies"],
                }
                for item in eligible
            ],
            "excluded": excluded,
        },
        "recommendation": {
            "bump": bump,
            "next_version": next_version,
            "tag": next_version,
            "trigger": trigger,
            "urgent_ids": urgent_ids,
            "single_candidate": True,
            "reason": reason,
        },
        "evidence": {
            key: {
                "status": parsed["evidence"][key],
                "reliable": parsed["evidence"][key] == "reliable",
            }
            for key in EVIDENCE_KEYS
        },
        "authorization": {
            "owner_authorization_required": True,
            "standing_authorization": "empty",
            "bypasses_owner_authorization": False,
        },
        "release_verification": {
            "executed_by_tool": False,
            "commands": commands,
        },
        "actions": {
            "creates_tag": False,
            "creates_release": False,
            "writes_project_files": False,
            "accesses_network": False,
        },
    }


def build(raw):
    """Return ``(exit_code, document)``. Exit code is always 2 or 3."""
    try:
        return 3, plan(parse(raw))
    except ReportError as exc:
        return 2, diagnostic(exc.code, exc.path)
