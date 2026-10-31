"""Read-only topology evaluation (Issue #180, Stage D observe-only).

Decision order and concept ownership: ``docs/design/task-driven-agent-topology.md``
§2 (frozen by #177). The evaluation benchmark is §5. This module consumes one
caller snapshot, compares the caller's fixed selection with the T0–T4 candidates
and returns an evidence report. It never executes a task, creates an instance,
writes a file, contacts the network or changes task state.

Input contract: ``topology-eval-input/1`` (see :func:`parse_input`).
Output contract: ``topology-eval-output/1`` or ``topology-eval-diagnostic/1``.
Every document carries ``plan_usability: not_a_quality_result``,
``savings_claimed: false``, ``efficiency_proven: false`` and
``release_status: not_released``: an evaluation is not a quality verdict, a
release gate, an approval or a ``VERIFIED`` signal.

Exit codes (set by :func:`build`): 2 for a structural / usage diagnostic, 3 for
an evaluation (including ``ROLLBACK_TO_FULL_PROCESS``). Exit code 0 is unused on
purpose.

Missing data stays ``UNKNOWN``. A metric is only aggregated (sum, or mean for a
rate) when every sample declares it; a single absent value makes the whole
metric ``UNKNOWN`` with ``value: null``. Nothing is estimated and absent data is
never written as ``0`` or ``PASS``. The caller's ``tokens`` count is recorded as
declared (it must include subagent tokens); ETR, performance feedback and
reviewer credibility have no verified records here, so they are reported as
unavailable rather than inferred.

Only the Python standard library is used.
"""
from __future__ import annotations

import math
import re

INPUT_SCHEMA_VERSION = "topology-eval-input/1"
OUTPUT_SCHEMA_VERSION = "topology-eval-output/1"
DIAGNOSTIC_SCHEMA_VERSION = "topology-eval-diagnostic/1"
EVALUATOR_VERSION = "180-draft-1"

TOPOLOGIES = ("T0", "T1", "T2", "T3", "T4")
STRUCTURES = {
    "T0": "tool_or_skill_in_main_loop",
    "T1": "sequential_execution_instance",
    "T2": "execution_with_independent_reviewer",
    "T3": "parallel_isolated_executions",
    "T4": "external_or_manual_handoff",
}

OUTCOMES = ("verified", "not_verified", "failed", "unknown")
GOAL_STATES = ("met", "degraded", "missing", "unknown")
QUALITY_STATES = ("pass", "fail", "missing", "unknown")
REGRESSION_STATES = ("pass", "fail", "unknown")

# Zero tolerance. A declared violation is never offset by cost or latency.
VIOLATION_CODES = (
    "safety_overreach",
    "self_approval",
    "identity_bypass",
    "lock_bypass",
    "dependency_bypass",
    "fabricated_evidence",
)

METRIC_KEYS = (
    "tokens", "cost_usd", "latency_seconds", "handoffs", "false_positives",
    "false_negatives", "ac_pass_rate", "rework_rounds", "severe_regressions",
)
INT_METRICS = frozenset({
    "tokens", "handoffs", "false_positives", "false_negatives",
    "rework_rounds", "severe_regressions",
})
FLOAT_METRICS = frozenset({"cost_usd", "latency_seconds"})
RATE_METRICS = frozenset({"ac_pass_rate"})

# Tool-owned sufficiency thresholds. A caller cannot lower them.
MIN_SAMPLES_PER_TOPOLOGY = 3
MIN_SAMPLES_PER_STRATUM = 2

# A caller snapshot may not pre-set a result, approval, release or savings field.
FORBIDDEN_FIELDS = frozenset({
    "approved", "verified", "status", "selection_status", "selected_topology",
    "plan_usability", "recommendation", "rollback", "dispatched", "may_dispatch",
    "attempt_allowed", "hard_gate_passed", "quality_passed", "passed", "proof",
    "waiver", "override", "skip", "savings_claimed", "savings_percent",
    "measured_savings", "efficiency_proven", "released", "shipped", "deployed",
    "production",
})

DIAGNOSTIC_CODES = {
    "schema_invalid", "edition_invalid", "unknown_field", "forbidden_field",
    "usage",
}

REF_RE = re.compile(r"^[A-Za-z0-9_.:/#-]{1,200}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

REQUIRED_TOP_LEVEL = (
    "schema_version", "evaluation_ref", "edition", "baseline", "strata",
)
ALLOWED_TOP_LEVEL = set(REQUIRED_TOP_LEVEL)
BASELINE_KEYS = ("selection", "source_ref")
STRATUM_KEYS = ("stratum_id", "samples")
SAMPLE_KEYS = (
    "sample_id", "selection", "task_ref", "outcome", "goal_attainment",
    "quality_constraints", "adjacent_regression", "hard_gate", "metrics",
    "trajectory_ref",
)
REQUIRED_SAMPLE_KEYS = ("sample_id", "selection", "task_ref")
HARD_GATE_KEYS = ("violations",)
METRIC_KEY_SET = set(METRIC_KEYS)

BASE_LIMITATIONS = (
    "caller_records_unverified",
    "evaluation_is_not_a_quality_result",
    "evaluation_is_not_a_dispatch",
    "savings_not_claimed",
    "not_a_release_gate",
    "no_execution_no_instance_no_network_no_writes",
    "etr_performance_feedback_and_reviewer_credibility_unavailable",
)


class EvaluationError(Exception):
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
        raise EvaluationError("schema_invalid", _pointer(pointer))
    return value


def _require_list(value, pointer, minimum=0):
    if not isinstance(value, list) or len(value) < minimum:
        raise EvaluationError("schema_invalid", _pointer(pointer))
    return value


def _require_str(value, pointer, maximum=None, pattern=None):
    if not isinstance(value, str) or not value:
        raise EvaluationError("schema_invalid", _pointer(pointer))
    if maximum is not None and len(value) > maximum:
        raise EvaluationError("schema_invalid", _pointer(pointer))
    if pattern is not None and not pattern.fullmatch(value):
        raise EvaluationError("schema_invalid", _pointer(pointer))
    return value


def _require_exact_keys(obj, keys, pointer):
    missing = [key for key in keys if key not in obj]
    if missing:
        raise EvaluationError("schema_invalid", _pointer(pointer + (missing[0],)))
    extra = [key for key in obj if key not in keys]
    if extra:
        raise EvaluationError("unknown_field", _pointer(pointer + (extra[0],)))


def _require_metric(value, pointer, key):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError("schema_invalid", _pointer(pointer))
    if not math.isfinite(value) or value < 0:
        raise EvaluationError("schema_invalid", _pointer(pointer))
    if key in INT_METRICS and not isinstance(value, int):
        raise EvaluationError("schema_invalid", _pointer(pointer))
    if key in RATE_METRICS and value > 1:
        raise EvaluationError("schema_invalid", _pointer(pointer))
    return value


def _add(items, value):
    if value not in items:
        items.append(value)


def _scan_forbidden(node, pointer=()):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_FIELDS:
                raise EvaluationError("forbidden_field",
                                      _pointer(pointer + (key,)))
            _scan_forbidden(value, pointer + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _scan_forbidden(value, pointer + (index,))


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def _parse_sample(raw, pointer):
    sample = _require_object(raw, pointer)
    for key in sample:
        if key not in SAMPLE_KEYS:
            raise EvaluationError("unknown_field", _pointer(pointer + (key,)))
    for key in REQUIRED_SAMPLE_KEYS:
        if key not in sample:
            raise EvaluationError("schema_invalid",
                                  _pointer(pointer + (key,)))

    selection = sample["selection"]
    if selection not in TOPOLOGIES:
        raise EvaluationError("schema_invalid",
                              _pointer(pointer + ("selection",)))

    outcome = sample.get("outcome", "unknown")
    if outcome not in OUTCOMES:
        raise EvaluationError("schema_invalid",
                              _pointer(pointer + ("outcome",)))
    goal = sample.get("goal_attainment", "unknown")
    if goal not in GOAL_STATES:
        raise EvaluationError("schema_invalid",
                              _pointer(pointer + ("goal_attainment",)))
    quality = sample.get("quality_constraints", "unknown")
    if quality not in QUALITY_STATES:
        raise EvaluationError("schema_invalid",
                              _pointer(pointer + ("quality_constraints",)))
    adjacent = sample.get("adjacent_regression", "unknown")
    if adjacent not in REGRESSION_STATES:
        raise EvaluationError("schema_invalid",
                              _pointer(pointer + ("adjacent_regression",)))

    gate_present = "hard_gate" in sample
    violations = []
    if gate_present:
        gate = _require_object(sample["hard_gate"],
                               pointer + ("hard_gate",))
        _require_exact_keys(gate, HARD_GATE_KEYS, pointer + ("hard_gate",))
        for index, code in enumerate(_require_list(
                gate["violations"], pointer + ("hard_gate", "violations"))):
            code = _require_str(
                code, pointer + ("hard_gate", "violations", index),
                maximum=64, pattern=ID_RE)
            if code not in VIOLATION_CODES:
                raise EvaluationError(
                    "unknown_field",
                    _pointer(pointer + ("hard_gate", "violations", index)))
            _add(violations, code)

    metrics = {key: None for key in METRIC_KEYS}
    if "metrics" in sample:
        metrics_raw = _require_object(sample["metrics"],
                                      pointer + ("metrics",))
        for key, value in metrics_raw.items():
            if key not in METRIC_KEY_SET:
                raise EvaluationError(
                    "unknown_field", _pointer(pointer + ("metrics", key)))
            metrics[key] = _require_metric(
                value, pointer + ("metrics", key), key)

    trajectory_ref = None
    if "trajectory_ref" in sample:
        trajectory_ref = _require_str(
            sample["trajectory_ref"], pointer + ("trajectory_ref",),
            maximum=200, pattern=REF_RE)

    return {
        "sample_id": _require_str(sample["sample_id"],
                                  pointer + ("sample_id",),
                                  maximum=64, pattern=ID_RE),
        "selection": selection,
        "task_ref": _require_str(sample["task_ref"], pointer + ("task_ref",),
                                 maximum=200, pattern=REF_RE),
        "outcome": outcome,
        "goal_attainment": goal,
        "quality_constraints": quality,
        "adjacent_regression": adjacent,
        "gate_present": gate_present,
        "violations": violations,
        "metrics": metrics,
        "trajectory_ref": trajectory_ref,
    }


def parse_input(raw):
    """Validate one caller snapshot. Raises :class:`EvaluationError`."""
    if not isinstance(raw, dict):
        raise EvaluationError("schema_invalid", "$")

    _scan_forbidden(raw)

    for key in raw:
        if key not in ALLOWED_TOP_LEVEL:
            raise EvaluationError("unknown_field", _pointer((key,)))
    for key in REQUIRED_TOP_LEVEL:
        if key not in raw:
            raise EvaluationError("schema_invalid", _pointer((key,)))

    if raw["schema_version"] != INPUT_SCHEMA_VERSION:
        raise EvaluationError("schema_invalid", "$.schema_version")

    evaluation_ref = _require_str(raw["evaluation_ref"],
                                  ("evaluation_ref",), maximum=200,
                                  pattern=REF_RE)

    edition = raw["edition"]
    if edition not in ("local", "github"):
        raise EvaluationError("edition_invalid", "$.edition")

    baseline_raw = _require_object(raw["baseline"], ("baseline",))
    _require_exact_keys(baseline_raw, BASELINE_KEYS, ("baseline",))
    baseline_selection = baseline_raw["selection"]
    if baseline_selection not in TOPOLOGIES:
        raise EvaluationError("schema_invalid", "$.baseline.selection")
    baseline = {
        "selection": baseline_selection,
        "source_ref": _require_str(baseline_raw["source_ref"],
                                   ("baseline", "source_ref"),
                                   maximum=200, pattern=REF_RE),
    }

    strata = []
    seen_strata = set()
    seen_samples = set()
    for stratum_index, stratum_raw in enumerate(_require_list(
            raw["strata"], ("strata",), 1)):
        pointer = ("strata", stratum_index)
        stratum = _require_object(stratum_raw, pointer)
        _require_exact_keys(stratum, STRATUM_KEYS, pointer)
        stratum_id = _require_str(stratum["stratum_id"],
                                  pointer + ("stratum_id",),
                                  maximum=64, pattern=ID_RE)
        if stratum_id in seen_strata:
            raise EvaluationError("schema_invalid",
                                  _pointer(pointer + ("stratum_id",)))
        seen_strata.add(stratum_id)
        samples = []
        for sample_index, sample_raw in enumerate(_require_list(
                stratum["samples"], pointer + ("samples",))):
            sample_pointer = pointer + ("samples", sample_index)
            sample = _parse_sample(sample_raw, sample_pointer)
            if sample["sample_id"] in seen_samples:
                raise EvaluationError("schema_invalid",
                                      _pointer(sample_pointer + ("sample_id",)))
            seen_samples.add(sample["sample_id"])
            samples.append(sample)
        strata.append({"stratum_id": stratum_id, "samples": samples})

    return {
        "evaluation_ref": evaluation_ref,
        "edition": edition,
        "baseline": baseline,
        "strata": strata,
    }


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

def _samples_by_topology(strata):
    grouped = {topology: [] for topology in TOPOLOGIES}
    counts = {topology: {} for topology in TOPOLOGIES}
    for stratum in strata:
        for sample in stratum["samples"]:
            grouped[sample["selection"]].append(sample)
            bucket = counts[sample["selection"]]
            bucket[stratum["stratum_id"]] = bucket.get(
                stratum["stratum_id"], 0) + 1
    return grouped, counts


def _counts(samples, key, values):
    result = {value: 0 for value in values}
    for sample in samples:
        result[sample[key]] += 1
    return result


def _hard_gate(samples):
    if not samples:
        return {
            "status": "UNKNOWN",
            "violation_codes": [],
            "reason_codes": ["no_samples"],
            "verified_by_this_tool": False,
        }
    violations = []
    unknowns = []
    for sample in samples:
        if not sample["gate_present"]:
            unknowns.append(f"hard_gate_unknown:{sample['sample_id']}")
            continue
        for code in sample["violations"]:
            entry = {
                "sample_id": sample["sample_id"],
                "selection": sample["selection"],
                "code": code,
            }
            if entry not in violations:
                violations.append(entry)
    if violations:
        status = "VIOLATION"
        reason_codes = [entry["code"] for entry in violations]
    elif unknowns:
        status = "UNKNOWN"
        reason_codes = unknowns
    else:
        status = "CLEAR"
        reason_codes = []
    return {
        "status": status,
        "violation_codes": sorted({entry["code"] for entry in violations}),
        "reason_codes": _dedupe(reason_codes),
        "verified_by_this_tool": False,
    }


def _quality(samples):
    if not samples:
        return {
            "status": "UNKNOWN",
            "reasons": ["no_samples"],
            "goal_attainment_counts": {value: 0 for value in GOAL_STATES},
            "quality_constraint_counts": {value: 0 for value in QUALITY_STATES},
            "adjacent_regression_counts": {
                value: 0 for value in REGRESSION_STATES},
        }
    failures = []
    unknowns = []
    for sample in samples:
        sample_id = sample["sample_id"]
        if sample["quality_constraints"] == "fail":
            failures.append(f"quality_constraints_fail:{sample_id}")
        elif sample["quality_constraints"] in ("missing", "unknown"):
            unknowns.append(
                f"quality_constraints_{sample['quality_constraints']}:{sample_id}")
        if sample["goal_attainment"] == "degraded":
            failures.append(f"goal_attainment_degraded:{sample_id}")
        elif sample["goal_attainment"] == "missing":
            failures.append(f"goal_attainment_missing:{sample_id}")
        elif sample["goal_attainment"] == "unknown":
            unknowns.append(f"goal_attainment_unknown:{sample_id}")
        if sample["adjacent_regression"] == "fail":
            failures.append(f"adjacent_regression_fail:{sample_id}")
        elif sample["adjacent_regression"] == "unknown":
            unknowns.append(f"adjacent_regression_unknown:{sample_id}")
        severe = sample["metrics"]["severe_regressions"]
        if severe is None:
            unknowns.append(f"severe_regressions_unknown:{sample_id}")
        elif severe > 0:
            failures.append(f"severe_regressions_observed:{sample_id}")
    status = "FAIL" if failures else ("UNKNOWN" if unknowns else "PASS")
    return {
        "status": status,
        "reasons": _dedupe(failures + unknowns),
        "goal_attainment_counts": _counts(samples, "goal_attainment",
                                          GOAL_STATES),
        "quality_constraint_counts": _counts(samples, "quality_constraints",
                                             QUALITY_STATES),
        "adjacent_regression_counts": _counts(samples, "adjacent_regression",
                                              REGRESSION_STATES),
    }


def _global_quality(samples):
    if not samples:
        return {
            "hard_constraints": "UNKNOWN",
            "goal_attainment": "UNKNOWN",
            "adjacent_regression": "UNKNOWN",
            "reasons": ["no_samples"],
        }
    hard_fail, hard_unknown = [], []
    goal_fail, goal_unknown = [], []
    adjacent_fail, adjacent_unknown = [], []
    for sample in samples:
        sample_id = sample["sample_id"]
        if sample["quality_constraints"] == "fail":
            hard_fail.append(f"quality_constraints_fail:{sample_id}")
        elif sample["quality_constraints"] in ("missing", "unknown"):
            hard_unknown.append(
                f"quality_constraints_{sample['quality_constraints']}:{sample_id}")
        if sample["goal_attainment"] in ("degraded", "missing"):
            goal_fail.append(
                f"goal_attainment_{sample['goal_attainment']}:{sample_id}")
        elif sample["goal_attainment"] == "unknown":
            goal_unknown.append(f"goal_attainment_unknown:{sample_id}")
        severe = sample["metrics"]["severe_regressions"]
        if sample["adjacent_regression"] == "fail" or (
                severe is not None and severe > 0):
            adjacent_fail.append(f"adjacent_regression_fail:{sample_id}")
        elif sample["adjacent_regression"] == "unknown" or severe is None:
            adjacent_unknown.append(
                f"adjacent_regression_unknown:{sample_id}")

    hard_status = "FAIL" if hard_fail else (
        "UNKNOWN" if hard_unknown else "PASS")
    goal_status = "DEGRADED" if goal_fail else (
        "UNKNOWN" if goal_unknown else "PASS")
    adjacent_status = "FAIL" if adjacent_fail else (
        "UNKNOWN" if adjacent_unknown else "PASS")
    return {
        "hard_constraints": hard_status,
        "goal_attainment": goal_status,
        "adjacent_regression": adjacent_status,
        "reasons": _dedupe(hard_fail + hard_unknown + goal_fail
                           + goal_unknown + adjacent_fail + adjacent_unknown),
    }


def _metric_summary(samples):
    summary = {}
    for key in METRIC_KEYS:
        aggregation = "mean" if key in RATE_METRICS else "sum"
        if not samples:
            summary[key] = {"status": "UNKNOWN", "value": None,
                            "aggregation": aggregation}
            continue
        values = [sample["metrics"][key] for sample in samples]
        if any(value is None for value in values):
            summary[key] = {"status": "UNKNOWN", "value": None,
                            "aggregation": aggregation}
            continue
        if aggregation == "mean":
            value = sum(values) / len(values)
        else:
            value = sum(values)
        summary[key] = {"status": "KNOWN", "value": value,
                        "aggregation": aggregation}
    return summary


def _metrics_complete(summary):
    return all(metric["status"] == "KNOWN" for metric in summary.values())


def _sample_sufficiency(topology_id, samples, stratum_counts, strata,
                        baseline_id):
    reasons = []
    if len(samples) < MIN_SAMPLES_PER_TOPOLOGY:
        reasons.append(f"topology_below_minimum:{topology_id}")
    for stratum_id, count in stratum_counts.items():
        if count < MIN_SAMPLES_PER_STRATUM:
            reasons.append(f"stratum_below_minimum:{topology_id}:{stratum_id}")
    if topology_id != baseline_id:
        for stratum_id in stratum_counts:
            if _stratum_count(strata, stratum_id,
                              baseline_id) < MIN_SAMPLES_PER_STRATUM:
                reasons.append(f"baseline_below_minimum:{stratum_id}")
        baseline_strata = {
            stratum["stratum_id"] for stratum in strata
            if any(sample["selection"] == baseline_id
                   for sample in stratum["samples"])}
        for missing in sorted(baseline_strata - set(stratum_counts)):
            reasons.append(f"missing_stratum:{missing}")
    return not reasons, reasons


def _stratum_count(strata, stratum_id, topology_id):
    for stratum in strata:
        if stratum["stratum_id"] == stratum_id:
            return sum(1 for sample in stratum["samples"]
                       if sample["selection"] == topology_id)
    return 0


def _comparison(topology_id, strata, baseline_id):
    """Per-stratum candidate/baseline comparison. Never fills absent data."""
    if topology_id == baseline_id:
        return {"comparable": False, "degraded": False, "reason_codes": [],
                "strata": []}
    comparability_reasons = []
    degradation_reasons = []
    entries = []
    baseline_strata = {
        stratum["stratum_id"] for stratum in strata
        if any(sample["selection"] == baseline_id
               for sample in stratum["samples"])
    }
    candidate_strata = {
        stratum["stratum_id"] for stratum in strata
        if any(sample["selection"] == topology_id
               for sample in stratum["samples"])
    }
    for missing in sorted(baseline_strata - candidate_strata):
        comparability_reasons.append(f"missing_stratum:{missing}")
    for extra in sorted(candidate_strata - baseline_strata):
        comparability_reasons.append(f"baseline_missing_stratum:{extra}")

    for stratum in strata:
        stratum_id = stratum["stratum_id"]
        baseline_samples = [sample for sample in stratum["samples"]
                            if sample["selection"] == baseline_id]
        candidate_samples = [sample for sample in stratum["samples"]
                             if sample["selection"] == topology_id]
        if not candidate_samples:
            continue
        comparable = (len(baseline_samples) >= MIN_SAMPLES_PER_STRATUM
                      and len(candidate_samples) >= MIN_SAMPLES_PER_STRATUM)
        stratum_reasons = []
        stratum_degraded = False
        if not comparable:
            stratum_reasons.append(f"not_comparable:{stratum_id}")
            comparability_reasons.append(f"not_comparable:{stratum_id}")
        else:
            baseline_met = sum(1 for sample in baseline_samples
                               if sample["goal_attainment"] == "met")
            candidate_met = sum(1 for sample in candidate_samples
                                if sample["goal_attainment"] == "met")
            if (candidate_met / len(candidate_samples)
                    < baseline_met / len(baseline_samples)):
                stratum_degraded = True
                stratum_reasons.append(
                    f"goal_attainment_below_baseline:{stratum_id}")
                degradation_reasons.append(
                    f"goal_attainment_below_baseline:{stratum_id}")
        entries.append({
            "stratum_id": stratum_id,
            "samples": len(candidate_samples),
            "baseline_samples": len(baseline_samples),
            "comparable": comparable,
            "degraded": stratum_degraded,
            "goal_attainment_met": sum(
                1 for sample in candidate_samples
                if sample["goal_attainment"] == "met"),
            "baseline_goal_attainment_met": sum(
                1 for sample in baseline_samples
                if sample["goal_attainment"] == "met"),
            "quality_failures": sum(
                1 for sample in candidate_samples
                if sample["quality_constraints"] == "fail"),
            "reason_codes": stratum_reasons,
        })
    return {
        "comparable": not comparability_reasons and bool(entries),
        "degraded": bool(degradation_reasons),
        "reason_codes": _dedupe(comparability_reasons + degradation_reasons),
        "strata": entries,
    }


def _dedupe(items):
    result = []
    for item in items:
        _add(result, item)
    return result


# ---------------------------------------------------------------------------
# Output document
# ---------------------------------------------------------------------------

def _candidate(topology_id, samples, stratum_counts, strata, baseline_id,
               baseline_metrics):
    hard_gate = _hard_gate(samples)
    quality = _quality(samples)
    metrics = _metric_summary(samples)
    sufficient, sufficiency_reasons = _sample_sufficiency(
        topology_id, samples, stratum_counts, strata, baseline_id)
    comparison = _comparison(topology_id, strata, baseline_id)
    reasons = list(hard_gate["reason_codes"]) + list(quality["reasons"])
    reasons += sufficiency_reasons + list(comparison["reason_codes"])

    if not samples:
        status = "NO_DATA"
        reasons = ["no_samples"]
    elif hard_gate["status"] == "VIOLATION":
        status = "ROLLBACK_REQUIRED"
    elif quality["status"] == "FAIL":
        status = "QUALITY_DEGRADED"
    elif hard_gate["status"] == "UNKNOWN" or quality["status"] == "UNKNOWN":
        status = "UNKNOWN"
    elif not sufficient:
        status = "INSUFFICIENT_SAMPLES"
    elif topology_id != baseline_id and comparison["degraded"]:
        status = "QUALITY_DEGRADED"
    elif topology_id == baseline_id:
        status = "BASELINE"
    elif not _metrics_complete(metrics):
        status = "UNKNOWN"
        reasons.append("candidate_metrics_unknown")
    elif not _metrics_complete(baseline_metrics):
        status = "UNKNOWN"
        reasons.append("baseline_metrics_unknown")
    else:
        status = "COST_REVIEW_CANDIDATE"

    return {
        "id": topology_id,
        "role": "baseline" if topology_id == baseline_id else "candidate",
        "structure": STRUCTURES[topology_id],
        "status": status,
        "samples": len(samples),
        "stratum_counts": dict(stratum_counts),
        "hard_gate": hard_gate,
        "quality": quality,
        "outcome_counts": _counts(samples, "outcome", OUTCOMES),
        "metrics": metrics,
        "degraded_vs_baseline": bool(comparison["degraded"]),
        "sample_sufficient": bool(sufficient),
        "reason_codes": _dedupe(reasons),
    }


def build_plan(document):
    strata = document["strata"]
    baseline_id = document["baseline"]["selection"]
    grouped, counts = _samples_by_topology(strata)
    all_samples = [sample for stratum in strata for sample in stratum["samples"]]

    safety = _hard_gate(all_samples)
    quality = _global_quality(all_samples)

    baseline_samples = grouped[baseline_id]
    baseline_metrics = _metric_summary(baseline_samples)

    candidates = [
        _candidate(topology_id, grouped[topology_id], counts[topology_id],
                   strata, baseline_id, baseline_metrics)
        for topology_id in TOPOLOGIES
    ]

    sufficient_by_topology = {
        item["id"]: item["sample_sufficient"] for item in candidates}
    sample_reasons = []
    if not baseline_samples:
        sample_reasons.append("baseline_no_samples")
    non_baseline = [item for item in candidates if item["id"] != baseline_id]
    if not any(item["samples"] for item in non_baseline):
        sample_reasons.append("no_candidate_samples")
    for item in candidates:
        if not item["samples"] or item["sample_sufficient"]:
            continue
        _, reasons = _sample_sufficiency(
            item["id"], grouped[item["id"]], counts[item["id"]], strata,
            baseline_id)
        sample_reasons.extend(reasons)
    comparable_candidates = [
        item for item in non_baseline
        if item["samples"] and item["sample_sufficient"]]
    if non_baseline and not comparable_candidates:
        sample_reasons.append("no_sufficient_candidate")
    sample_sufficient = bool(
        baseline_samples and sufficient_by_topology[baseline_id]
        and comparable_candidates)

    statuses = {item["id"]: item["status"] for item in non_baseline}
    quality_hold = (
        quality["hard_constraints"] == "FAIL"
        or quality["goal_attainment"] == "DEGRADED"
        or quality["adjacent_regression"] == "FAIL"
        or "QUALITY_DEGRADED" in statuses.values()
    )
    quality_unknown = (
        quality["hard_constraints"] == "UNKNOWN"
        or quality["goal_attainment"] == "UNKNOWN"
        or quality["adjacent_regression"] == "UNKNOWN"
    )
    candidate_unknown = any(
        item["samples"] and item["status"] == "UNKNOWN"
        for item in non_baseline)

    if safety["status"] == "VIOLATION":
        recommendation = "ROLLBACK_TO_FULL_PROCESS"
    elif quality_hold:
        recommendation = "HOLD_FULL_PROCESS"
    elif safety["status"] == "UNKNOWN" or quality_unknown:
        recommendation = "UNKNOWN"
    elif not sample_sufficient:
        recommendation = "INSUFFICIENT_SAMPLES"
    elif candidate_unknown:
        recommendation = "UNKNOWN"
    elif "COST_REVIEW_CANDIDATE" in statuses.values():
        recommendation = "COST_REVIEW_CANDIDATE"
    else:
        recommendation = "UNKNOWN"

    if recommendation == "ROLLBACK_TO_FULL_PROCESS":
        rollback = {
            "required": True,
            "action": "revert_to_full_current_process",
            "rule": "zero_tolerance_violation_reverts_to_full_current_process",
            "reason_codes": list(safety["violation_codes"]),
        }
    elif recommendation == "HOLD_FULL_PROCESS":
        rollback = {
            "required": False,
            "action": "none",
            "rule": "quality_regression_keeps_full_current_process",
            "reason_codes": [reason for reason in quality["reasons"]],
        }
    else:
        rollback = {
            "required": False,
            "action": "none",
            "rule": "full_current_process_remains_the_default",
            "reason_codes": [],
        }

    if recommendation == "ROLLBACK_TO_FULL_PROCESS":
        decisions = [{
            "decision_id": "hard_gate_violation",
            "reason_codes": list(safety["violation_codes"]),
        }]
    elif recommendation == "HOLD_FULL_PROCESS":
        reasons = list(quality["reasons"])
        for item in non_baseline:
            if item["status"] == "QUALITY_DEGRADED":
                reasons.extend(item["reason_codes"])
        decisions = [{
            "decision_id": "quality_regression",
            "reason_codes": _dedupe(reasons),
        }]
    elif recommendation == "INSUFFICIENT_SAMPLES":
        decisions = [{
            "decision_id": "insufficient_samples",
            "reason_codes": _dedupe(sample_reasons),
        }]
    elif recommendation == "COST_REVIEW_CANDIDATE":
        decisions = [{
            "decision_id": "cost_review_requires_owner_decision",
            "reason_codes": [],
        }]
    else:
        reasons = list(safety["reason_codes"]) + list(quality["reasons"])
        for item in non_baseline:
            if item["status"] == "UNKNOWN":
                reasons.extend(item["reason_codes"])
        decisions = [{
            "decision_id": "missing_measurements",
            "reason_codes": _dedupe(reasons),
        }]

    limitations = list(BASE_LIMITATIONS)
    if recommendation == "ROLLBACK_TO_FULL_PROCESS":
        limitations.append("rollback_to_full_process_required")
    if not sample_sufficient:
        limitations.append("insufficient_samples_not_efficiency_proof")
    if any(metric["status"] == "UNKNOWN"
           for item in candidates if item["samples"]
           for metric in item["metrics"].values()):
        limitations.append("unknown_metrics_are_not_zero")
    if recommendation == "COST_REVIEW_CANDIDATE":
        limitations.append("cost_review_is_not_a_release_decision")

    comparison = []
    comparisons = {
        item["id"]: _comparison(item["id"], strata, baseline_id)
        for item in non_baseline}
    for stratum in strata:
        stratum_candidates = []
        for item in non_baseline:
            for entry in comparisons[item["id"]]["strata"]:
                if entry["stratum_id"] == stratum["stratum_id"]:
                    stratum_candidates.append(entry)
        comparison.append({
            "stratum_id": stratum["stratum_id"],
            "baseline_samples": sum(
                1 for sample in stratum["samples"]
                if sample["selection"] == baseline_id),
            "candidates": stratum_candidates,
        })

    failure_trajectories = []
    for sample in all_samples:
        if sample["outcome"] == "failed" or sample["trajectory_ref"] is not None:
            failure_trajectories.append({
                "sample_id": sample["sample_id"],
                "selection": sample["selection"],
                "task_ref": sample["task_ref"],
                "outcome": sample["outcome"],
                "trajectory_ref": sample["trajectory_ref"],
            })

    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "plan_usability": "not_a_quality_result",
        "savings_claimed": False,
        "efficiency_proven": False,
        "release_status": "not_released",
        "mode": "observe_only",
        "dispatched": False,
        "evaluation_ref": document["evaluation_ref"],
        "edition": document["edition"],
        "baseline": {
            "selection": baseline_id,
            "source_ref": document["baseline"]["source_ref"],
            "label": "caller_fixed_selection",
            "verified_by_this_tool": False,
        },
        "recommendation": recommendation,
        "rollback": rollback,
        "safety": {
            "status": safety["status"],
            "violations": _safety_violations(all_samples),
            "violation_codes": list(safety["violation_codes"]),
            "reason_codes": list(safety["reason_codes"]),
            "verified_by_this_tool": False,
        },
        "quality": quality,
        "sample": {
            "total": len(all_samples),
            "per_topology": {item["id"]: item["samples"]
                             for item in candidates},
            "minimum_per_topology": MIN_SAMPLES_PER_TOPOLOGY,
            "minimum_per_stratum": MIN_SAMPLES_PER_STRATUM,
            "sufficient": sample_sufficient,
            "reason_codes": _dedupe(sample_reasons),
        },
        "candidates": candidates,
        "comparison": comparison,
        "failure_trajectories": failure_trajectories,
        "required_human_decisions": decisions,
        "limitations": limitations,
        "evidence_refs": [
            {"kind": "evaluation_source", "ref": "caller:snapshot"},
            {"kind": "baseline", "ref": document["baseline"]["source_ref"]},
            {"kind": "samples", "ref": "caller:strata"},
            {"kind": "hard_gate", "ref": "caller:hard_gate"},
            {"kind": "quality", "ref": "caller:quality_constraints"},
            {"kind": "metrics", "ref": "caller:metrics"},
        ],
    }


def _safety_violations(samples):
    violations = []
    for sample in samples:
        if not sample["gate_present"]:
            continue
        for code in sample["violations"]:
            entry = {
                "sample_id": sample["sample_id"],
                "selection": sample["selection"],
                "code": code,
            }
            if entry not in violations:
                violations.append(entry)
    return violations


def diagnostic(code, path="$"):
    if code not in DIAGNOSTIC_CODES:
        code = "schema_invalid"
    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "plan_usability": "not_a_quality_result",
        "savings_claimed": False,
        "efficiency_proven": False,
        "release_status": "not_released",
        "dispatched": False,
        "diagnostic": {"code": code, "path": path},
    }


def build(raw):
    """Validate ``raw`` and return ``(exit_code, document)``. Never returns 0."""
    try:
        document = parse_input(raw)
    except EvaluationError as error:
        return 2, diagnostic(error.code, error.path)
    return 3, build_plan(document)
