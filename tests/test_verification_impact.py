"""Tests for the read-only shadow verification-impact CLI (Issue #187).

Frozen contract: ``docs/design/verification-impact-c-contract.md``, transcribed
from https://github.com/ctrlshiftaltdel/AgentOrch/issues/187#issuecomment-6537003302.

The CLI is plan-only and shadow-only. It never executes a check, never writes
project state, never scans the repository, never touches ``.orchestration/`` and
never contacts the network. Structural violations are exit 2 with a diagnostic
object; a shadow report is exit 3 with ``closure: open``. Exit code 0 is never
used. ``UNKNOWN`` is a legitimate absence marker in this contract (false
negatives and time saved when the caller gave no data), so it is *not* in the
forbidden-word set; every qualification-shaped word still is.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "scripts" / "verification_impact"
CLI = PACKAGE / "cli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verification_impact"
CONTRACT_DOC = ROOT / "docs" / "design" / "verification-impact-c-contract.md"
SCHEMA = PACKAGE / "schema.json"

# Qualification-shaped words a shadow report must never echo. ``UNKNOWN`` is
# deliberately absent: contract §4 requires it as the false-negative / timing
# marker, and it is an absence signal, not a qualification.
BANNED_WORDS = [
    "PASS", "FAIL", "NOT_RUN", "BLOCKED", "NOT_APPLICABLE",
    "PASS_EXECUTED", "PASS_EQUIVALENCE_ATTESTED", "approved", "ready",
    "verified", "may_merge", "ok", "passed", "qualified", "success",
    "green", "attempt_allowed", "dispatch_ok", "may_dispatch", "skip",
    "identity_ok", "declared_risk_level",
]
BANNED_LOWER = {word.lower() for word in BANNED_WORDS}
# Underscore and hyphen are intra-word: identifiers such as
# ``not_a_verification_result`` and ``path_without_coverage_edge`` are single
# words and must not be read as standalone forbidden words.
WORD_RE = re.compile(r"[A-Za-z0-9_-]+")

REUSE_DECISIONS = {"not_reused", "rejected", "applicable_on_head"}
COVERAGE_STATES = {"complete", "incomplete"}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))
classify = _load("verification_impact_classify", PACKAGE / "classify.py")
cli_module = _load("verification_impact_cli", CLI)


def fixture_names():
    return sorted(
        path.name[: -len(".json")]
        for path in FIXTURES.glob("*.json")
        if not path.name.endswith(".expected.json")
    )


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_cli(*args, input_path=None):
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    argv = [sys.executable, str(CLI)]
    if input_path is not None:
        argv += ["--input", str(input_path)]
    argv += list(args)
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          cwd=str(ROOT))


def parse_stdout(proc):
    return json.loads(proc.stdout)


def run_payload(payload):
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "input.json"
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return run_cli(input_path=input_path)


def base_payload(name="fx_clean_complete"):
    return load_json(FIXTURES / f"{name}.json")


def walk(node, path="$"):
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")


def banned_hits(payload):
    hits = []
    for path, node in walk(payload):
        if isinstance(node, dict):
            for key in node:
                if key.lower() in BANNED_LOWER:
                    hits.append(f"{path}: key {key!r}")
        elif isinstance(node, str):
            for word in WORD_RE.findall(node):
                if word.lower() in BANNED_LOWER:
                    hits.append(f"{path}: word {word!r}")
    return hits


def key_present(payload, name):
    return any(isinstance(node, dict) and name in node for _, node in walk(payload))


def assert_clean(testcase, payload, raw_text=None):
    testcase.assertEqual(banned_hits(payload), [], "forbidden word escaped into stdout")
    if raw_text is not None:
        testcase.assertNotIn("PASS_EXECUTED", raw_text)
        testcase.assertNotIn("PASS_EQUIVALENCE_ATTESTED", raw_text)


class ImpactFixtureTests(unittest.TestCase):
    maxDiff = None

    def test_fixture_inventory(self):
        names = fixture_names()
        self.assertEqual(len(names), 15)
        for name in names:
            expected = load_json(FIXTURES / f"{name}.expected.json")
            self.assertIn(expected["exit_code"], (2, 3), name)
            self.assertNotEqual(expected["exit_code"], 0)

    def test_every_fixture(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                expected = load_json(FIXTURES / f"{name}.expected.json")
                proc = run_cli(input_path=FIXTURES / f"{name}.json")
                self.assertEqual(proc.returncode, expected["exit_code"],
                                 proc.stderr or proc.stdout)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stderr, "", "stderr must stay empty")
                self.assertNotIn("Traceback", proc.stderr)
                payload = parse_stdout(proc)
                self.assertEqual(payload["plan_usability"], "not_a_verification_result")
                assert_clean(self, payload, proc.stdout)
                if expected["exit_code"] == 2:
                    self.assertDiagnostic(payload, expected)
                else:
                    self.assertReport(name, payload, expected)

    def assertDiagnostic(self, payload, expected):
        self.assertEqual(payload["schema_version"], "verification-impact-diagnostic/1")
        self.assertEqual(payload["diagnostic"]["code"], expected["diagnostic_code"])
        self.assertEqual(payload["diagnostic"]["path"], expected["diagnostic_path"])
        self.assertEqual(set(payload),
                         {"schema_version", "plan_usability", "diagnostic"})
        for absent in ("impact", "checks", "reuse", "comparison", "review_package"):
            self.assertNotIn(absent, payload)

    def assertReport(self, name, payload, expected):
        self.assertEqual(payload["schema_version"], "verification-impact-output/1")
        self.assertEqual(payload["mode"], "shadow_only")
        self.assertEqual(payload["closure"], "open")

        impact = payload["impact"]
        self.assertIn(impact["coverage_status"], COVERAGE_STATES)
        self.assertEqual(impact["coverage_status"], expected["coverage_status"])
        self.assertFalse(impact["repository_scanned"])
        for reason in expected["required_coverage_reasons"]:
            self.assertIn(reason, impact["coverage_reasons"])
        for path in expected.get("high_risk_paths", []):
            self.assertIn(path, impact["high_risk_paths"])

        checks = payload["checks"]
        self.assertEqual(checks["authority"], "unverified_snapshot")
        source = load_json(FIXTURES / f"{name}.json")
        self.assertEqual(checks["counterfactual_removed"], [])
        self.assertFalse(checks["subset_claimed_sufficient"])
        self.assertEqual(checks["suggested_checks"], checks["caller_snapshot"])
        self.assertEqual([item["check_id"] for item in checks["caller_snapshot"]],
                         [item["check_id"] for item in source["checks"]])

        for entry in expected.get("required_reuse", []):
            matches = [item for item in payload["reuse"]
                       if item["evidence_id"] == entry["evidence_id"]]
            self.assertEqual(len(matches), 1, entry["evidence_id"])
            item = matches[0]
            self.assertIn(item["reuse_decision"], REUSE_DECISIONS)
            self.assertEqual(item["reuse_decision"], entry["reuse_decision"])
            for reason in entry.get("required_reasons", []):
                self.assertIn(reason, item["reasons"])
            self.assertFalse(item["head_rerun"])

        if "identity" in expected:
            self.assertEqual(payload["identity"], expected["identity"])
        for unknown in expected.get("required_unknowns", []):
            self.assertIn(unknown, payload["review_package"]["unknowns"])

        comparison = payload["comparison"]
        self.assertEqual(comparison["false_negatives"], expected["false_negatives"])
        self.assertEqual(comparison["time_saved"], expected["time_saved"])
        self.assertEqual(comparison["mapping_authority"], "observation_only")
        observed = [item["failure_id"] for item in comparison["out_of_mapping_failures"]]
        self.assertEqual(observed, expected.get("out_of_mapping_failure_ids", []))

        review = payload["review_package"]
        self.assertEqual([item["ac_id"] for item in review["acceptance"]],
                         [item["ac_id"] for item in source["acceptance"]])
        self.assertIn("mandatory_checks", review)
        self.assertIn("unknowns", review)
        self.assertIn("rejected_reuse", review)
        self.assertIn("failures", review)
        mandatory = [item["check_id"] for item in review["mandatory_checks"]]
        for check in source["checks"]:
            if check["mandatory"]:
                self.assertIn(check["check_id"], mandatory)
        for failure in source.get("comparison", {}).get("suite_failures", []):
            self.assertIn(failure["failure_id"],
                          [item["failure_id"] for item in review["failures"]])

        self.assertEqual(payload["policy"]["effect"], "current_rules_unchanged")
        self.assertFalse(key_present(payload, "status"))
        for dispatch in ("attempt_allowed", "dispatch_ok", "may_dispatch", "skip"):
            self.assertFalse(key_present(payload, dispatch), dispatch)


class ImpactInvariantTests(unittest.TestCase):
    maxDiff = None

    def test_empty_edges_are_not_success(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_empty_edges.json"))
        self.assertEqual(payload["impact"]["coverage_status"], "incomplete")
        self.assertIn("empty_mapping", payload["impact"]["coverage_reasons"])
        self.assertTrue(payload["impact"]["uncovered_paths"])

    def test_uncovered_path_is_incomplete(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_uncovered_path.json"))
        self.assertEqual(payload["impact"]["coverage_status"], "incomplete")
        self.assertIn("tests/test_verification_impact.py",
                      payload["impact"]["uncovered_paths"])
        self.assertIn("coverage:path_without_coverage_edge:tests/test_verification_impact.py",
                      payload["review_package"]["unknowns"])

    def test_high_risk_path_stays_incomplete_without_subset_claim(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_high_risk_path.json"))
        self.assertEqual(payload["impact"]["coverage_status"], "incomplete")
        self.assertIn("high_risk_path", payload["impact"]["coverage_reasons"])
        self.assertIn("scripts/orchestrator.py", payload["impact"]["high_risk_paths"])
        self.assertEqual(payload["checks"]["suggested_checks"],
                         payload["checks"]["caller_snapshot"])
        self.assertEqual(payload["checks"]["counterfactual_removed"], [])

    def test_empty_dynamic_references_are_not_success(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_empty_dynamic.json"))
        self.assertEqual(payload["impact"]["coverage_status"], "incomplete")
        self.assertIn("dynamic_references_absent",
                      payload["impact"]["coverage_reasons"])

    def test_old_sha_is_not_a_head_rerun(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_old_sha_reuse.json"))
        entry = next(item for item in payload["reuse"]
                     if item["evidence_id"] == "ev_old_head")
        self.assertEqual(entry["reuse_decision"], "applicable_on_head")
        self.assertEqual(entry["ran_on_sha"], "3333333333333333333333333333333333333333")
        self.assertEqual(entry["head_sha"], payload["head_sha"])
        self.assertNotEqual(entry["ran_on_sha"], entry["head_sha"])
        self.assertFalse(entry["head_rerun"])
        self.assertFalse(entry["same_head"])
        # ``applicable_on_head`` is explicitly not a qualification.
        self.assertNotIn("qualified", json.dumps(entry).lower())

    def test_ci_approval_security_are_never_reused(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_ci_not_reused.json"))
        decisions = {item["evidence_id"]: item for item in payload["reuse"]}
        self.assertEqual(decisions["ev_ci"]["reuse_decision"], "not_reused")
        self.assertIn("ci_required", decisions["ev_ci"]["reasons"])
        self.assertEqual(decisions["ev_approval"]["reuse_decision"], "not_reused")
        self.assertIn("approval_class", decisions["ev_approval"]["reasons"])
        self.assertEqual(decisions["ev_security"]["reuse_decision"], "not_reused")
        self.assertIn("security_class", decisions["ev_security"]["reasons"])

    def test_environment_drift_is_rejected(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_env_drift.json"))
        entry = next(item for item in payload["reuse"]
                     if item["evidence_id"] == "ev_env_drift")
        self.assertEqual(entry["reuse_decision"], "rejected")
        self.assertIn("environment_drift", entry["reasons"])
        self.assertIn(entry["evidence_id"],
                      [item["evidence_id"]
                       for item in payload["review_package"]["rejected_reuse"]])

    def test_modified_test_rejects_old_evidence(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_modified_test.json"))
        entry = next(item for item in payload["reuse"]
                     if item["evidence_id"] == "ev_modified_test")
        self.assertEqual(entry["reuse_decision"], "rejected")
        self.assertIn("modified_test", entry["reasons"])

    def test_family_self_report_is_unproven(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_family_self_report.json"))
        self.assertEqual(payload["identity"],
                         {"requirement": "required", "assessment": "unproven"})
        self.assertIn("identity:reviewer_unproven",
                      payload["review_package"]["unknowns"])

    def test_missing_full_suite_makes_false_negatives_unknown(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_false_negative_unknown.json"))
        self.assertEqual(payload["comparison"]["false_negatives"], "UNKNOWN")
        self.assertNotEqual(payload["comparison"]["false_negatives"], [])
        self.assertEqual(payload["comparison"]["time_saved"], "UNKNOWN")
        self.assertNotEqual(payload["comparison"]["time_saved"], 0)

    def test_out_of_mapping_failure_is_surfaced(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_mapping_missed_failure.json"))
        self.assertEqual([item["failure_id"]
                          for item in payload["comparison"]["out_of_mapping_failures"]],
                         ["f_outside"])
        self.assertEqual(payload["comparison"]["false_negatives"], ["f_outside"])
        failed = [item["failure_id"] for item in payload["review_package"]["failures"]]
        self.assertIn("f_inside", failed)
        self.assertIn("f_outside", failed)

    def test_dot_segment_is_path_invalid(self):
        proc = run_cli(input_path=FIXTURES / "fx_path_invalid_dot.json")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        payload = parse_stdout(proc)
        self.assertEqual(payload["diagnostic"]["code"], "path_invalid")
        self.assertEqual(payload["diagnostic"]["path"], "$.changed_paths[0].path")

    def test_task_ref_qualification_word_is_rejected(self):
        proc = run_cli(input_path=FIXTURES / "fx_task_ref_pass.json")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        self.assertNotIn("Traceback", proc.stderr)
        payload = parse_stdout(proc)
        self.assertEqual(payload["diagnostic"]["code"], "schema_invalid")
        self.assertEqual(payload["diagnostic"]["path"], "$.task_ref")
        self.assertNotIn("PASS", proc.stdout)

    def test_identity_field_is_rejected_not_used(self):
        proc = run_cli(input_path=FIXTURES / "fx_identity_field_rejected.json")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        payload = parse_stdout(proc)
        self.assertEqual(payload["diagnostic"]["code"], "forbidden_field")

    def test_usage_errors(self):
        cases = [[], ["--input"], ["--input", "a.json", "--extra"],
                 ["--in", "a.json"], ["--input=a.json"]]
        for args in cases:
            with self.subTest(args=args):
                proc = subprocess.run([sys.executable, str(CLI), *args],
                                      capture_output=True, text=True, cwd=str(ROOT))
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stderr, "")
                payload = json.loads(proc.stdout)
                self.assertEqual(payload["diagnostic"]["code"], "usage")
                self.assertEqual(payload["plan_usability"],
                                 "not_a_verification_result")

    def test_exit_code_is_never_zero(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                proc = run_cli(input_path=FIXTURES / f"{name}.json")
                self.assertIn(proc.returncode, (2, 3))


class ImpactReviewPackageTests(unittest.TestCase):
    maxDiff = None

    def test_duplicate_evidence_id_is_deduplicated(self):
        payload = base_payload("fx_old_sha_reuse")
        duplicate = json.loads(json.dumps(payload["old_evidence"][0]))
        payload["old_evidence"].append(duplicate)
        document = parse_stdout(run_payload(payload))
        ids = [item["evidence_id"] for item in document["reuse"]]
        self.assertEqual(ids.count("ev_old_head"), 1)

    def test_malformed_duplicate_is_still_rejected(self):
        payload = base_payload("fx_old_sha_reuse")
        duplicate = json.loads(json.dumps(payload["old_evidence"][0]))
        duplicate["ran_on_sha"] = "abc"
        payload["old_evidence"].append(duplicate)
        proc = run_payload(payload)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(parse_stdout(proc)["diagnostic"]["code"], "sha_invalid")

    def test_unknowns_survive_a_summary(self):
        payload = base_payload("fx_false_negative_unknown")
        document = parse_stdout(run_payload(payload))
        unknowns = document["review_package"]["unknowns"]
        self.assertIn("comparison:false_negatives_unknown", unknowns)
        self.assertIn("comparison:time_saved_unknown", unknowns)

    def test_mandatory_check_is_listed_even_without_evidence(self):
        payload = base_payload("fx_clean_complete")
        document = parse_stdout(run_payload(payload))
        mandatory = [item["check_id"]
                     for item in document["review_package"]["mandatory_checks"]]
        self.assertIn("project.validate_kit", mandatory)

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
            input_path.write_text((FIXTURES / "fx_clean_complete.json").read_text(
                encoding="utf-8"), encoding="utf-8")
            proc = run_cli(input_path=input_path)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        after = snapshot()
        self.assertEqual(before, after, "CLI wrote into the project tree")
        self.assertEqual(proc.stderr, "")

    def test_standard_library_only_boundary(self):
        source = (PACKAGE / "classify.py").read_text(encoding="utf-8")
        cli_source = CLI.read_text(encoding="utf-8")
        combined = source + cli_source
        self.assertNotIn("scripts.agent_selection", combined)
        self.assertNotIn("scripts.verification_planner", combined)
        self.assertNotIn("import subprocess", combined)
        self.assertNotIn("import socket", combined)
        self.assertNotIn("urllib", combined)

    def test_contract_doc_marks_owner_direct_implementation(self):
        text = CONTRACT_DOC.read_text(encoding="utf-8")
        self.assertIn("6537003302", text)
        self.assertIn("2026-10-31", text)
        self.assertIn("直接实施", text)
        self.assertIn("PROPOSED", text)
        self.assertIn("NOT IMPLEMENTED", text)

    def test_new_files_are_not_required(self):
        text = (ROOT / "scripts" / "validate_kit.py").read_text(encoding="utf-8")
        self.assertNotIn("verification_impact", text)

    def test_schema_ids(self):
        schema = load_json(SCHEMA)
        self.assertEqual(schema["input"]["$id"], "verification-impact-input/1")
        self.assertEqual(schema["report"]["$id"], "verification-impact-output/1")
        self.assertEqual(schema["diagnostic"]["$id"],
                         "verification-impact-diagnostic/1")
        self.assertEqual(schema["report"]["properties"]["checks"]["properties"]
                         ["counterfactual_removed"]["maxItems"], 0)


if __name__ == "__main__":
    unittest.main()
