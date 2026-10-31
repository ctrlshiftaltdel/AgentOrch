"""Tests for the read-only topology evaluation CLI (Issue #180).

The evaluator is an observe-only comparison CLI: it reads one caller snapshot,
compares the caller's fixed selection with the T0–T4 candidates and prints one
JSON document. It never executes a task, spawns an instance, writes a file,
contacts the network or changes task state. Exit code 2 is a structural / usage
diagnostic; every evaluation (including ``ROLLBACK_TO_FULL_PROCESS`` and
``UNKNOWN``) is exit code 3. Exit code 0 is deliberately unused: an evaluation is
not a quality result, a release gate, an approval or a ``VERIFIED`` signal.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "scripts" / "topology_eval"
CLI = EVALUATOR / "cli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "topology_eval"

OUTPUT_SCHEMA = "topology-eval-output/1"
DIAGNOSTIC_SCHEMA = "topology-eval-diagnostic/1"
INPUT_SCHEMA = "topology-eval-input/1"
EVALUATOR_VERSION = "180-draft-1"
TOPOLOGIES = ["T0", "T1", "T2", "T3", "T4"]
RECOMMENDATIONS = {
    "ROLLBACK_TO_FULL_PROCESS", "HOLD_FULL_PROCESS", "UNKNOWN",
    "INSUFFICIENT_SAMPLES", "COST_REVIEW_CANDIDATE",
}
CANDIDATE_STATUSES = {
    "BASELINE", "COST_REVIEW_CANDIDATE", "INSUFFICIENT_SAMPLES",
    "QUALITY_DEGRADED", "ROLLBACK_REQUIRED", "UNKNOWN", "NO_DATA",
}
METRIC_KEYS = (
    "tokens", "cost_usd", "latency_seconds", "handoffs", "false_positives",
    "false_negatives", "ac_pass_rate", "rework_rounds", "severe_regressions",
)
VIOLATION_CODES = {
    "safety_overreach", "self_approval", "identity_bypass",
    "lock_bypass", "dependency_bypass", "fabricated_evidence",
}

PLAN_KEYS = {
    "schema_version", "evaluator_version", "plan_usability", "savings_claimed",
    "efficiency_proven", "release_status", "mode", "dispatched",
    "evaluation_ref", "edition", "baseline", "recommendation", "rollback",
    "safety", "quality", "sample", "candidates", "comparison",
    "failure_trajectories", "required_human_decisions", "limitations",
    "evidence_refs",
}
CANDIDATE_KEYS = {
    "id", "role", "structure", "status", "samples", "stratum_counts",
    "hard_gate", "quality", "outcome_counts", "metrics",
    "degraded_vs_baseline", "sample_sufficient", "reason_codes",
}
# A caller snapshot may never pre-set a result, approval, release or savings field.
FORBIDDEN_CALLER_FIELDS = [
    "approved", "verified", "status", "selection_status", "selected_topology",
    "plan_usability", "recommendation", "rollback", "dispatched", "may_dispatch",
    "attempt_allowed", "hard_gate_passed", "quality_passed", "passed", "proof",
    "waiver", "override", "skip", "savings_claimed", "savings_percent",
    "measured_savings", "efficiency_proven", "released", "shipped", "deployed",
    "production",
]


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fixture_names():
    return sorted(
        path.name[: -len(".json")]
        for path in FIXTURES.glob("*.json")
        if not path.name.endswith(".expected.json")
    )


def base_payload(name="fx_comparable_control"):
    return load_json(FIXTURES / f"{name}.json")


def run_cli(*args, input_path=None):
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    argv = [sys.executable, str(CLI)]
    if input_path is not None:
        argv += ["--input", str(input_path)]
    argv += list(args)
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          cwd=str(ROOT))


def run_payload(payload):
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "input.json"
        input_path.write_text(json.dumps(payload, ensure_ascii=False),
                              encoding="utf-8")
        return run_cli(input_path=input_path)


def parse_stdout(proc):
    return json.loads(proc.stdout)


def walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


class EvalAssertions:
    maxDiff = None

    def assertDiagnostic(self, payload, code):
        self.assertEqual(payload["schema_version"], DIAGNOSTIC_SCHEMA)
        self.assertEqual(payload["plan_usability"], "not_a_quality_result")
        self.assertIs(payload["savings_claimed"], False)
        self.assertIs(payload["efficiency_proven"], False)
        self.assertEqual(payload["release_status"], "not_released")
        self.assertIs(payload["dispatched"], False)
        self.assertEqual(set(payload), {
            "schema_version", "plan_usability", "savings_claimed",
            "efficiency_proven", "release_status", "dispatched", "diagnostic",
        })
        self.assertEqual(payload["diagnostic"]["code"], code)
        self.assertIsInstance(payload["diagnostic"]["path"], str)
        self.assertNotIn("candidates", payload)
        self.assertNotIn("recommendation", payload)

    def assertPlan(self, payload, expected, name):
        self.assertEqual(set(payload), PLAN_KEYS)
        self.assertEqual(payload["schema_version"], OUTPUT_SCHEMA)
        self.assertEqual(payload["evaluator_version"], EVALUATOR_VERSION)
        self.assertEqual(payload["plan_usability"], "not_a_quality_result")
        self.assertIs(payload["savings_claimed"], False)
        self.assertIs(payload["efficiency_proven"], False)
        self.assertEqual(payload["release_status"], "not_released")
        self.assertEqual(payload["mode"], "observe_only")
        self.assertIs(payload["dispatched"], False)

        source = base_payload(name)
        self.assertEqual(payload["evaluation_ref"], source["evaluation_ref"])
        self.assertEqual(payload["edition"], source["edition"])
        self.assertEqual(payload["baseline"]["selection"],
                         source["baseline"]["selection"])
        self.assertEqual(payload["baseline"]["source_ref"],
                         source["baseline"]["source_ref"])
        self.assertIs(payload["baseline"]["verified_by_this_tool"], False)

        self.assertIn(payload["recommendation"], RECOMMENDATIONS)
        self.assertEqual(payload["recommendation"], expected["recommendation"])
        self.assertEqual(payload["safety"]["status"], expected["safety_status"])
        self.assertIs(payload["safety"]["verified_by_this_tool"], False)
        for key, value in expected["quality"].items():
            self.assertEqual(payload["quality"][key], value, key)
        self.assertIs(payload["sample"]["sufficient"],
                      expected["sample_sufficient"])

        candidates = payload["candidates"]
        self.assertEqual([item["id"] for item in candidates], TOPOLOGIES)
        for item in candidates:
            self.assertEqual(set(item), CANDIDATE_KEYS)
            self.assertIn(item["status"], CANDIDATE_STATUSES)
            self.assertIsInstance(item["stratum_counts"], dict)
            self.assertIsInstance(item["reason_codes"], list)
            self.assertIsInstance(item["degraded_vs_baseline"], bool)
            self.assertIsInstance(item["sample_sufficient"], bool)
            self.assertEqual(set(item["metrics"]), set(METRIC_KEYS))
            for metric in item["metrics"].values():
                self.assertEqual(set(metric),
                                 {"status", "value", "aggregation"})
                self.assertIn(metric["status"], {"KNOWN", "UNKNOWN"})
                if metric["status"] == "UNKNOWN":
                    self.assertIsNone(metric["value"])
                else:
                    self.assertIsInstance(metric["value"], (int, float))
            self.assertIn(item["hard_gate"]["status"],
                          {"CLEAR", "VIOLATION", "UNKNOWN"})
            self.assertIsInstance(item["hard_gate"]["violation_codes"], list)
        statuses = {item["id"]: item["status"] for item in candidates}
        self.assertEqual(statuses, expected["candidate_statuses"])

        self.assertIs(payload["rollback"]["required"],
                      expected["rollback_required"])
        if payload["rollback"]["required"]:
            self.assertEqual(payload["rollback"]["action"],
                             "revert_to_full_current_process")
        else:
            self.assertEqual(payload["rollback"]["action"], "none")
        self.assertIsInstance(payload["rollback"]["rule"], str)
        self.assertIsInstance(payload["rollback"]["reason_codes"], list)

        decision_ids = [item["decision_id"]
                        for item in payload["required_human_decisions"]]
        self.assertEqual(decision_ids, expected["required_human_decision_ids"])

        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for needle in expected["required_substrings"]:
            self.assertIn(needle, text, f"{name}: missing {needle!r}")
        for needle in expected["forbidden_substrings"]:
            self.assertNotIn(needle, text, f"{name}: unexpected {needle!r}")

        for limitation in (
            "caller_records_unverified",
            "evaluation_is_not_a_quality_result",
            "evaluation_is_not_a_dispatch",
            "savings_not_claimed",
            "not_a_release_gate",
            "no_execution_no_instance_no_network_no_writes",
        ):
            self.assertIn(limitation, payload["limitations"])
        self.assertIsInstance(payload["comparison"], list)
        self.assertIsInstance(payload["failure_trajectories"], list)
        self.assertIsInstance(payload["evidence_refs"], list)
        for ref in payload["evidence_refs"]:
            self.assertEqual(set(ref), {"kind", "ref"})


class EvalFixtureTests(EvalAssertions, unittest.TestCase):
    def test_fixture_inventory(self):
        names = fixture_names()
        self.assertEqual(len(names), 10)
        for name in names:
            expected = load_json(FIXTURES / f"{name}.expected.json")
            self.assertIn(expected["exit_code"], (2, 3))
            self.assertEqual(base_payload(name)["schema_version"], INPUT_SCHEMA)

    def test_every_fixture(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                expected = load_json(FIXTURES / f"{name}.expected.json")
                proc = run_cli(input_path=FIXTURES / f"{name}.json")
                self.assertEqual(proc.returncode, expected["exit_code"],
                                 proc.stderr or proc.stdout)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stderr, "", "stderr must stay empty")
                payload = parse_stdout(proc)
                self.assertEqual(payload["plan_usability"],
                                 "not_a_quality_result")
                self.assertIs(payload["savings_claimed"], False)
                if expected["exit_code"] == 2:
                    self.assertDiagnostic(payload, expected["diagnostic_code"])
                else:
                    self.assertPlan(payload, expected, name)

    def test_exit_code_is_never_zero(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                proc = run_cli(input_path=FIXTURES / f"{name}.json")
                self.assertIn(proc.returncode, (2, 3))


class EvalSafetyTests(EvalAssertions, unittest.TestCase):
    def test_every_zero_tolerance_violation_rolls_back(self):
        seen = set()
        for name in fixture_names():
            if not name.startswith("fx_"):
                continue
            payload = base_payload(name)
            declared = []
            for node in walk(payload):
                if isinstance(node, dict) and "violations" in node:
                    declared.extend(node["violations"])
            if not declared:
                continue
            seen.update(declared)
            with self.subTest(fixture=name, code=declared[0]):
                document = parse_stdout(
                    run_cli(input_path=FIXTURES / f"{name}.json"))
                self.assertEqual(document["recommendation"],
                                 "ROLLBACK_TO_FULL_PROCESS")
                self.assertIs(document["rollback"]["required"], True)
                self.assertEqual(document["rollback"]["action"],
                                 "revert_to_full_current_process")
                self.assertIn(declared[0], document["rollback"]["reason_codes"])
                self.assertIs(document["savings_claimed"], False)
                self.assertIs(document["efficiency_proven"], False)
                self.assertEqual(document["safety"]["status"], "VIOLATION")
                text = json.dumps(document, sort_keys=True)
                self.assertNotIn('"recommendation": "COST_REVIEW_CANDIDATE"',
                                 text)
                self.assertNotIn('"savings_claimed": true', text)
                self.assertIn("hard_gate_violation",
                              [item["decision_id"]
                               for item in document["required_human_decisions"]])
        self.assertEqual(seen, VIOLATION_CODES)

    def test_violation_beats_good_metrics(self):
        payload = base_payload()
        target = payload["strata"][0]["samples"][2]
        self.assertEqual(target["selection"], "T1")
        target["hard_gate"] = {"violations": ["self_approval"]}
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["recommendation"], "ROLLBACK_TO_FULL_PROCESS")
        self.assertEqual(document["safety"]["violations"][0]["selection"], "T1")
        self.assertIs(document["savings_claimed"], False)

    def test_unknown_hard_gate_is_not_a_pass(self):
        payload = base_payload()
        del payload["strata"][0]["samples"][2]["hard_gate"]
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["safety"]["status"], "UNKNOWN")
        self.assertEqual(document["recommendation"], "UNKNOWN")
        self.assertNotEqual(document["recommendation"], "COST_REVIEW_CANDIDATE")
        self.assertIs(document["savings_claimed"], False)
        text = json.dumps(document, sort_keys=True)
        self.assertNotIn('"savings_claimed": true', text)


class EvalDataHonestyTests(EvalAssertions, unittest.TestCase):
    def test_missing_metrics_stay_unknown_not_zero(self):
        document = parse_stdout(
            run_cli(input_path=FIXTURES / "fx_missing_data.json"))
        candidate = {item["id"]: item for item in document["candidates"]}["T1"]
        for key in ("tokens", "cost_usd", "latency_seconds", "handoffs",
                    "false_positives", "false_negatives"):
            metric = candidate["metrics"][key]
            self.assertEqual(metric["status"], "UNKNOWN", key)
            self.assertIsNone(metric["value"], key)
        self.assertEqual(candidate["status"], "UNKNOWN")
        self.assertEqual(document["recommendation"], "UNKNOWN")
        self.assertIs(document["savings_claimed"], False)
        self.assertIs(document["efficiency_proven"], False)
        text = json.dumps(document, sort_keys=True)
        self.assertNotIn('"recommendation": "COST_REVIEW_CANDIDATE"', text)
        self.assertNotIn('"efficiency_proven": true', text)

    def test_baseline_missing_metrics_block_cost_review(self):
        payload = base_payload()
        for sample in payload["strata"][0]["samples"]:
            if sample["selection"] == "T2":
                sample["metrics"]["tokens"] = None
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["recommendation"], "UNKNOWN")
        candidate = {item["id"]: item for item in document["candidates"]}["T1"]
        self.assertEqual(candidate["status"], "UNKNOWN")
        self.assertIs(document["savings_claimed"], False)
        text = json.dumps(document, sort_keys=True)
        self.assertNotIn('"recommendation": "COST_REVIEW_CANDIDATE"', text)

    def test_quality_degradation_blocks_cost_savings(self):
        document = parse_stdout(
            run_cli(input_path=FIXTURES / "fx_quality_degradation.json"))
        self.assertEqual(document["recommendation"], "HOLD_FULL_PROCESS")
        self.assertEqual(document["quality"]["hard_constraints"], "FAIL")
        self.assertEqual(document["quality"]["goal_attainment"], "DEGRADED")
        self.assertIs(document["savings_claimed"], False)
        self.assertIs(document["efficiency_proven"], False)
        text = json.dumps(document, sort_keys=True)
        self.assertNotIn('"recommendation": "COST_REVIEW_CANDIDATE"', text)
        self.assertNotIn('"savings_claimed": true', text)

    def test_relative_goal_degradation_blocks_cost_savings(self):
        payload = base_payload()
        target = payload["strata"][1]["samples"][2]
        self.assertEqual(target["selection"], "T1")
        target["goal_attainment"] = "degraded"
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["recommendation"], "HOLD_FULL_PROCESS")
        candidate = {item["id"]: item for item in document["candidates"]}["T1"]
        self.assertEqual(candidate["status"], "QUALITY_DEGRADED")
        self.assertIs(document["savings_claimed"], False)

    def test_insufficient_samples_never_prove_efficiency(self):
        document = parse_stdout(
            run_cli(input_path=FIXTURES / "fx_insufficient_samples.json"))
        self.assertEqual(document["recommendation"], "INSUFFICIENT_SAMPLES")
        self.assertIs(document["sample"]["sufficient"], False)
        self.assertIs(document["efficiency_proven"], False)
        self.assertIs(document["savings_claimed"], False)
        self.assertIn("insufficient_samples",
                      [item["decision_id"]
                       for item in document["required_human_decisions"]])
        text = json.dumps(document, sort_keys=True)
        self.assertNotIn('"recommendation": "COST_REVIEW_CANDIDATE"', text)

    def test_failure_trajectory_is_recorded(self):
        document = parse_stdout(
            run_cli(input_path=FIXTURES / "fx_quality_degradation.json"))
        refs = {item["trajectory_ref"]
                for item in document["failure_trajectories"]}
        self.assertIn("traj:issue-180/fail-1", refs)
        self.assertIs(document["savings_claimed"], False)


class EvalComparisonTests(EvalAssertions, unittest.TestCase):
    def test_comparable_control_is_not_a_release(self):
        document = parse_stdout(
            run_cli(input_path=FIXTURES / "fx_comparable_control.json"))
        self.assertEqual(document["recommendation"], "COST_REVIEW_CANDIDATE")
        self.assertIs(document["savings_claimed"], False)
        self.assertIs(document["efficiency_proven"], False)
        self.assertEqual(document["release_status"], "not_released")
        self.assertEqual(document["mode"], "observe_only")
        self.assertIn("cost_review_requires_owner_decision",
                      [item["decision_id"]
                       for item in document["required_human_decisions"]])
        text = json.dumps(document, sort_keys=True)
        self.assertNotIn('"savings_claimed": true', text)
        self.assertNotIn('"efficiency_proven": true', text)
        self.assertNotIn('"released": true', text)
        self.assertNotIn('"published": true', text)
        self.assertNotIn('"deployed": true', text)

    def test_baseline_and_candidate_metrics_are_compared(self):
        document = parse_stdout(
            run_cli(input_path=FIXTURES / "fx_comparable_control.json"))
        candidates = {item["id"]: item for item in document["candidates"]}
        baseline = candidates["T2"]
        candidate = candidates["T1"]
        self.assertEqual(baseline["role"], "baseline")
        self.assertEqual(candidate["role"], "candidate")
        self.assertEqual(candidate["metrics"]["tokens"]["value"], 300)
        self.assertEqual(baseline["metrics"]["tokens"]["value"], 520)
        self.assertAlmostEqual(candidate["metrics"]["cost_usd"]["value"], 0.30)
        self.assertAlmostEqual(baseline["metrics"]["cost_usd"]["value"], 0.52)
        self.assertEqual(candidate["metrics"]["handoffs"]["value"], 4)
        self.assertEqual(baseline["metrics"]["handoffs"]["value"], 8)
        self.assertEqual(candidate["metrics"]["latency_seconds"]["value"], 130)
        self.assertEqual(baseline["metrics"]["latency_seconds"]["value"], 220)
        self.assertIs(candidate["degraded_vs_baseline"], False)
        self.assertEqual(len(document["comparison"]), 2)
        for stratum in document["comparison"]:
            self.assertEqual(stratum["baseline_samples"], 2)
            self.assertTrue(stratum["candidates"])
            for item in stratum["candidates"]:
                self.assertIs(item["comparable"], True)
                self.assertIs(item["degraded"], False)

    def test_baseline_is_echoed_not_recomputed(self):
        payload = base_payload()
        payload["baseline"]["selection"] = "T1"
        payload["baseline"]["source_ref"] = "caller:manual/42"
        for stratum in payload["strata"]:
            stratum["samples"] = [sample for sample in stratum["samples"]
                                  if sample["selection"] != "T2"]
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["baseline"]["selection"], "T1")
        self.assertEqual(document["baseline"]["source_ref"], "caller:manual/42")
        statuses = {item["id"]: item["status"]
                    for item in document["candidates"]}
        self.assertEqual(statuses["T1"], "BASELINE")
        self.assertNotIn("COST_REVIEW_CANDIDATE", statuses.values())
        self.assertIs(document["savings_claimed"], False)

    def test_edition_twins_evaluate_identically(self):
        payload = base_payload()
        local = parse_stdout(run_payload(dict(payload, edition="local")))
        github = parse_stdout(run_payload(dict(payload, edition="github")))
        self.assertEqual(local["edition"], "local")
        self.assertEqual(github["edition"], "github")
        for key in ("baseline", "recommendation", "rollback", "safety",
                    "quality", "sample", "candidates", "comparison",
                    "required_human_decisions"):
            self.assertEqual(local[key], github[key], key)
        self.assertIs(local["savings_claimed"], False)
        self.assertIs(github["savings_claimed"], False)


class EvalNegativeTests(EvalAssertions, unittest.TestCase):
    def test_forbidden_caller_fields_are_rejected(self):
        for field in FORBIDDEN_CALLER_FIELDS:
            with self.subTest(field=field):
                payload = base_payload()
                payload[field] = True
                proc = run_payload(payload)
                self.assertEqual(proc.returncode, 2, proc.stderr or proc.stdout)
                self.assertEqual(proc.stderr, "")
                self.assertDiagnostic(parse_stdout(proc), "forbidden_field")
                self.assertIn(f"$.{field}", proc.stdout)

    def test_nested_forbidden_field_is_rejected(self):
        payload = base_payload()
        payload["strata"][0]["samples"][0]["hard_gate"]["passed"] = True
        proc = run_payload(payload)
        self.assertEqual(proc.returncode, 2)
        self.assertDiagnostic(parse_stdout(proc), "forbidden_field")
        self.assertIn("hard_gate.passed", proc.stdout)

    def test_unknown_field_is_rejected(self):
        payload = base_payload()
        payload["extra_advice"] = "ship it"
        proc = run_payload(payload)
        self.assertEqual(parse_stdout(proc)["diagnostic"]["code"],
                         "unknown_field")
        self.assertIn("$.extra_advice", proc.stdout)

    def test_unknown_metric_key_is_rejected(self):
        payload = base_payload()
        payload["strata"][0]["samples"][0]["metrics"]["wall_clock"] = 1
        proc = run_payload(payload)
        self.assertEqual(parse_stdout(proc)["diagnostic"]["code"],
                         "unknown_field")

    def test_unknown_violation_code_is_rejected(self):
        payload = base_payload()
        payload["strata"][0]["samples"][0]["hard_gate"]["violations"] = [
            "looks_fine"]
        proc = run_payload(payload)
        self.assertEqual(parse_stdout(proc)["diagnostic"]["code"],
                         "unknown_field")

    def test_bad_edition_is_rejected(self):
        payload = base_payload()
        payload["edition"] = "cloud"
        self.assertEqual(parse_stdout(run_payload(payload))["diagnostic"]["code"],
                         "edition_invalid")

    def test_bad_baseline_selection_is_rejected(self):
        payload = base_payload()
        payload["baseline"]["selection"] = "T9"
        self.assertEqual(parse_stdout(run_payload(payload))["diagnostic"]["code"],
                         "schema_invalid")

    def test_negative_metric_is_rejected(self):
        payload = base_payload()
        payload["strata"][0]["samples"][0]["metrics"]["tokens"] = -1
        self.assertEqual(parse_stdout(run_payload(payload))["diagnostic"]["code"],
                         "schema_invalid")

    def test_rate_out_of_range_is_rejected(self):
        payload = base_payload()
        payload["strata"][0]["samples"][0]["metrics"]["ac_pass_rate"] = 1.5
        self.assertEqual(parse_stdout(run_payload(payload))["diagnostic"]["code"],
                         "schema_invalid")

    def test_duplicate_sample_id_is_rejected(self):
        payload = base_payload()
        payload["strata"][1]["samples"][0]["sample_id"] = \
            payload["strata"][0]["samples"][0]["sample_id"]
        self.assertEqual(parse_stdout(run_payload(payload))["diagnostic"]["code"],
                         "schema_invalid")

    def test_duplicate_stratum_id_is_rejected(self):
        payload = base_payload()
        payload["strata"][1]["stratum_id"] = payload["strata"][0]["stratum_id"]
        self.assertEqual(parse_stdout(run_payload(payload))["diagnostic"]["code"],
                         "schema_invalid")

    def test_empty_strata_is_rejected(self):
        payload = base_payload()
        payload["strata"] = []
        self.assertEqual(parse_stdout(run_payload(payload))["diagnostic"]["code"],
                         "schema_invalid")

    def test_usage_errors(self):
        for args in ([], ["--input"], ["--input", "a.json", "--extra"],
                     ["--in", "a.json"], ["--input=a.json"]):
            with self.subTest(args=args):
                proc = subprocess.run([sys.executable, str(CLI), *args],
                                      capture_output=True, text=True,
                                      cwd=str(ROOT))
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stderr, "")
                self.assertDiagnostic(parse_stdout(proc), "usage")

    def test_missing_input_file_is_a_schema_diagnostic(self):
        proc = run_cli(input_path=ROOT / "does-not-exist-180.json")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        self.assertDiagnostic(parse_stdout(proc), "schema_invalid")

    def test_malformed_json_is_a_schema_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.json"
            input_path.write_text("{not json", encoding="utf-8")
            proc = run_cli(input_path=input_path)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        self.assertDiagnostic(parse_stdout(proc), "schema_invalid")

    def test_process_write_set_empty(self):
        def snapshot():
            files = set()
            for path in ROOT.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(ROOT)
                if rel.parts and rel.parts[0] == ".git":
                    continue
                files.add(str(rel))
            return files

        before = snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.json"
            input_path.write_text(
                (FIXTURES / "fx_comparable_control.json").read_text(
                    encoding="utf-8"), encoding="utf-8")
            proc = run_cli(input_path=input_path)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        after = snapshot()
        self.assertEqual(before, after, "evaluator wrote into the project tree")


class EvalContractTests(unittest.TestCase):
    def test_source_imports_only_stdlib(self):
        allowed = {
            "__future__", "json", "math", "os", "re", "sys", "pathlib",
            "cli", "evaluate",
        }
        for name in ("cli.py", "evaluate.py"):
            tree = ast.parse((EVALUATOR / name).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0]
                                    for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            self.assertTrue(imported <= allowed, (name, sorted(imported - allowed)))

    def test_documented_entry_point_exists(self):
        self.assertTrue(CLI.is_file())
        text = CLI.read_text(encoding="utf-8")
        self.assertIn("scripts/topology_eval/cli.py --input PATH", text)

    def test_new_files_are_not_added_to_required(self):
        validator = (ROOT / "scripts" / "validate_kit.py").read_text(
            encoding="utf-8")
        self.assertNotIn("scripts/topology_eval/", validator)
        self.assertNotIn("tests/test_topology_eval.py", validator)
        self.assertNotIn("tests/fixtures/topology_eval/", validator)

    def test_output_contract_markers_are_constant(self):
        text = (EVALUATOR / "evaluate.py").read_text(encoding="utf-8")
        self.assertIn('"not_a_quality_result"', text)
        self.assertIn("savings_claimed", text)
        self.assertIn("efficiency_proven", text)
        self.assertIn('"not_released"', text)


if __name__ == "__main__":
    unittest.main()
