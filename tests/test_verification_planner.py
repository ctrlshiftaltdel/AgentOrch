"""Tests for the read-only shadow verification planner (Issue #186).

Frozen contract: ``docs/design/verification-planner-b-contract.md``, transcribed
from https://github.com/ctrlshiftaltdel/AgentOrch/issues/186#issuecomment-6486678110.

The planner is a plan-only shadow CLI. It never executes a check, never writes
project state and never returns exit code 0. Structural violations are exit 2
with a diagnostic object; a shadow plan is exit 3 with ``closure: open``. Every
stdout document is scanned for the frozen contract's forbidden words so a
result-ish value can never be echoed as a qualification.
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
PLANNER = ROOT / "scripts" / "verification_planner"
CLI = PLANNER / "cli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verification_planner"
CONTRACT_DOC = ROOT / "docs" / "design" / "verification-planner-b-contract.md"

# Frozen by the contract, §5.1 item 3. Matching is case-insensitive and a word is
# a maximal run of letters/digits/underscore (so ``source_not_verified`` is one
# word and does not contain the standalone word ``verified``).
BANNED_WORDS = [
    "PASS", "FAIL", "NOT_RUN", "UNKNOWN", "BLOCKED", "NOT_APPLICABLE",
    "PASS_EXECUTED", "PASS_EQUIVALENCE_ATTESTED", "approved", "ready",
    "verified", "may_merge", "ok", "passed", "qualified", "success",
    "green", "attempt_allowed", "dispatch_ok", "may_dispatch", "skip",
    "identity_ok", "declared_risk_level",
]
BANNED_LOWER = {word.lower() for word in BANNED_WORDS}
# Underscore and hyphen are intra-word: the contract mandates identifiers such as
# ``R-DYN-UNKNOWN`` and ``not_a_verification_result`` that a stricter split would
# wrongly read as the standalone forbidden word.
WORD_RE = re.compile(r"[A-Za-z0-9_-]+")
DISPATCH_FIELDS = [
    "attempt_allowed", "dispatch_ok", "may_dispatch", "unlock",
    "handover_authorized", "skip",
]

GATE_IDS = ["G1", "G2", "G3", "G4", "G5", "G6"]
GATE_SOURCE = "kit/orchestration.md"
EXPECTED_KEYS = {
    "exit_code", "diagnostic_code", "floors", "level", "required_rule_ids",
    "forbidden_rule_ids", "required_closure_reasons", "counterfactual_diff_empty",
}


def _load(name, path):
    """Load a module by file path with stdlib importlib (contract §8.1)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if str(PLANNER) not in sys.path:
    sys.path.insert(0, str(PLANNER))
classify = _load("verification_planner_classify", PLANNER / "classify.py")
cli_module = _load("verification_planner_cli", CLI)


def fixture_names():
    return sorted(
        path.name[: -len(".json")]
        for path in FIXTURES.glob("*.json")
        if not path.name.endswith(".expected.json")
    )


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_cli(*args, input_path=None):
    # No PYTHONDONTWRITEBYTECODE here on purpose: the CLI itself must keep its
    # process write set empty (contract §8.2).
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


def walk(node, path="$"):
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")


def banned_hits(payload):
    """Return every key / string-value word banned by the frozen contract."""
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
        for word in WORD_RE.findall(raw_text):
            testcase.assertNotIn(word.lower(), BANNED_LOWER,
                                 f"forbidden word {word!r} in raw stdout")


class PlannerFixtureTests(unittest.TestCase):
    maxDiff = None

    def test_fixture_inventory(self):
        names = fixture_names()
        self.assertEqual(len(names), 26)
        for name in names:
            expected = load_json(FIXTURES / f"{name}.expected.json")
            self.assertTrue(EXPECTED_KEYS.issubset(expected), name)
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
                payload = parse_stdout(proc)
                self.assertEqual(payload["plan_usability"], "not_a_verification_result")
                assert_clean(self, payload, proc.stdout)
                if expected["exit_code"] == 2:
                    self.assertDiagnostic(payload, expected)
                else:
                    self.assertPlan(name, payload, expected)

    def assertDiagnostic(self, payload, expected):
        self.assertEqual(payload["schema_version"], "verification-planner-diagnostic/1")
        self.assertEqual(payload["diagnostic"]["code"], expected["diagnostic_code"])
        self.assertIsInstance(payload["diagnostic"]["path"], str)
        for absent in ("classification", "checks", "gates", "ac_map", "identity"):
            self.assertNotIn(absent, payload)
        self.assertEqual(set(payload), {"schema_version", "plan_usability", "diagnostic"})

    def assertPlan(self, name, payload, expected):
        self.assertEqual(payload["schema_version"], "verification-planner-output/1")
        self.assertEqual(payload["planner_version"], "186-draft-2")
        self.assertEqual(payload["mode"], "shadow_only")
        self.assertEqual(payload["closure"], "open")
        self.assertEqual(payload["edition"], expected["expected_edition"])
        self.assertEqual(payload["identity"], expected["identity"])

        classification = payload["classification"]
        self.assertEqual(classification["level"], expected["level"])
        self.assertEqual(classification["floors"], expected["floors"])
        for rule in expected["required_rule_ids"]:
            self.assertIn(rule, classification["rule_ids"])
        for rule in expected["forbidden_rule_ids"]:
            self.assertNotIn(rule, classification["rule_ids"])
        for reason in expected["required_closure_reasons"]:
            self.assertIn(reason, payload["closure_reasons"])
            self.assertIn(reason, classification["unknown_items"])
        self.assertTrue(set(payload["closure_reasons"]).issubset(
            {"source_not_verified", "mandatory_baseline_unfrozen",
             "dynamic_dependency_unknown", "declared_uncertainty",
             "semantic_review_not_current", "profile_not_current",
             "negative_self_report", "identity_unproven",
             "profile_result_field_stripped"}))

        self.assertEqual(payload["policy"], {
            "effect": "current_rules_unchanged",
            "refs": ["kit/orchestration.md", "editions/local/master.md",
                     "editions/github/master.md"],
        })

        gates = payload["gates"]
        self.assertEqual([gate["gate_id"] for gate in gates], GATE_IDS)
        for gate in gates:
            self.assertEqual(gate["source_path"], GATE_SOURCE)
            self.assertEqual(set(gate), {"gate_id", "source_path"})

        acceptance = load_json(FIXTURES / f"{name}.json")["acceptance"]
        self.assertEqual(len(payload["ac_map"]), len(acceptance))
        for item in payload["ac_map"]:
            self.assertEqual(set(item), {"ac_id", "matched_rule_ids", "gate_ids", "check_ids"})
            self.assertEqual(item["gate_ids"], GATE_IDS)

        checks = payload["checks"]
        self.assertEqual(checks["authority"], "unverified_snapshot")
        self.assertEqual(checks["counterfactual_diff"], [])
        caller_ids = [item["check_id"] for item in checks["caller_snapshot"]]
        shadow_ids = [item["check_id"] for item in checks["shadow_candidates"]]
        self.assertEqual(caller_ids, expected["expected_check_ids"])
        self.assertEqual(shadow_ids, caller_ids)
        for item in checks["caller_snapshot"] + checks["shadow_candidates"]:
            self.assertEqual(set(item), {"check_id", "scope", "source_ref"})

        observations = payload["observations"]
        self.assertEqual(set(observations), {"runtime_probe"})
        probe = observations["runtime_probe"]
        self.assertEqual(set(probe), {"items", "tool_absence_claims"})
        self.assertIsInstance(probe["items"], list)
        self.assertIsInstance(probe["tool_absence_claims"], list)
        self.assertNotIn("requirement", probe)
        self.assertNotIn("assessment", probe)

        self.assertFalse(key_present(payload, "status"))
        for forbidden in DISPATCH_FIELDS:
            self.assertFalse(key_present(payload, forbidden), forbidden)
        if expected.get("counterfactual_diff_empty"):
            self.assertEqual(checks["counterfactual_diff"], [])


class PlannerInvariantTests(unittest.TestCase):
    maxDiff = None

    def test_probe_observation_does_not_change_floors(self):
        base = parse_stdout(run_cli(input_path=FIXTURES / "fx_l0_plain_doc.json"))
        probe = parse_stdout(run_cli(input_path=FIXTURES / "fx_probe_observation.json"))
        self.assertEqual(probe["classification"]["floors"], base["classification"]["floors"])
        self.assertEqual(probe["classification"]["level"], base["classification"]["level"])
        self.assertNotIn("R-IDENTITY-UNPROVEN", probe["classification"]["rule_ids"])
        self.assertEqual(probe["identity"]["assessment"], "not_assessed")
        self.assertEqual(probe["policy"]["effect"], "current_rules_unchanged")
        self.assertEqual(len(probe["observations"]["runtime_probe"]["items"]), 4)

    def test_identity_is_separate_from_probe(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_identity_required.json"))
        self.assertEqual(payload["identity"], {"requirement": "required",
                                               "assessment": "unproven"})
        probe = payload["observations"]["runtime_probe"]
        self.assertEqual(probe["items"], [{"probe_id": "C1", "caller_code": "missing"}])
        self.assertNotIn("requirement", probe)
        self.assertNotIn("assessment", probe)
        self.assertIn("R-IDENTITY-UNPROVEN", payload["classification"]["rule_ids"])
        self.assertIn("R-HIGH-AFTER-MAX", payload["classification"]["rule_ids"])

    def test_tool_absence_claim_is_observation_only(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_tool_absent.json"))
        claims = payload["observations"]["runtime_probe"]["tool_absence_claims"]
        self.assertEqual(claims, [{
            "probe_id": "C1",
            "evidence_ref": "caller:tool-absence/1",
            "level_effect": "none",
            "dispatch_effect": "none",
        }])
        self.assertNotIn("caller:tool-absence/1", json.dumps(payload["identity"]))

    def test_monotonic_upgrade(self):
        before = parse_stdout(run_cli(input_path=FIXTURES / "fx_monotonic_before.json"))
        after = parse_stdout(run_cli(input_path=FIXTURES / "fx_monotonic_after.json"))
        order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
        self.assertGreaterEqual(order[after["classification"]["floors"]["path_floor"]],
                                order[before["classification"]["floors"]["path_floor"]])
        self.assertGreaterEqual(order[after["classification"]["level"]],
                                order[before["classification"]["level"]])
        self.assertIn("R-PATH-ORCHESTRATOR", after["classification"]["rule_ids"])
        after_ids = [item["check_id"] for item in after["checks"]["shadow_candidates"]]
        before_ids = [item["check_id"] for item in before["checks"]["shadow_candidates"]]
        self.assertEqual(len(after_ids), len(after["checks"]["caller_snapshot"]))
        self.assertGreaterEqual(len(after_ids), len(before_ids))
        self.assertEqual(before["checks"]["counterfactual_diff"], [])
        self.assertEqual(after["checks"]["counterfactual_diff"], [])

    def test_edition_twins_classify_identically(self):
        local = parse_stdout(run_cli(input_path=FIXTURES / "fx_edition_local.json"))
        github = parse_stdout(run_cli(input_path=FIXTURES / "fx_edition_github.json"))
        self.assertEqual(local["edition"], "local")
        self.assertEqual(github["edition"], "github")
        self.assertEqual(local["classification"]["level"], github["classification"]["level"])
        self.assertEqual(local["classification"]["rule_ids"], github["classification"]["rule_ids"])
        self.assertEqual(local["classification"]["unknown_items"],
                         github["classification"]["unknown_items"])
        self.assertEqual([item["check_id"] for item in github["checks"]["caller_snapshot"]],
                         [item["check_id"] for item in local["checks"]["caller_snapshot"]])

    def test_short_snapshot_is_not_padded(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_short_snapshot.json"))
        self.assertEqual([item["check_id"] for item in payload["checks"]["shadow_candidates"]],
                         ["project.validate_kit"])
        self.assertEqual(payload["checks"]["authority"], "unverified_snapshot")
        self.assertIn("mandatory_baseline_unfrozen", payload["closure_reasons"])

    def test_profile_result_word_is_not_echoed(self):
        proc = run_cli(input_path=FIXTURES / "fx_profile_status_stripped.json")
        self.assertEqual(proc.returncode, 3)
        self.assertNotIn("PASS", proc.stdout)
        self.assertNotIn('"status"', proc.stdout)
        payload = parse_stdout(proc)
        self.assertIn("profile_result_field_stripped", payload["closure_reasons"])

    def test_exit_code_is_never_zero(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                proc = run_cli(input_path=FIXTURES / f"{name}.json")
                self.assertIn(proc.returncode, (2, 3))

    def test_usage_errors(self):
        cases = [[], ["--input"], ["--input", "a.json", "--extra"], ["--in", "a.json"],
                 ["--input=a.json"]]
        for args in cases:
            with self.subTest(args=args):
                proc = subprocess.run([sys.executable, str(CLI), *args],
                                      capture_output=True, text=True, cwd=str(ROOT))
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stderr, "")
                payload = json.loads(proc.stdout)
                self.assertEqual(payload["diagnostic"]["code"], "usage")
                self.assertEqual(payload["plan_usability"], "not_a_verification_result")

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
            input_path.write_text((FIXTURES / "fx_l0_plain_doc.json").read_text(encoding="utf-8"),
                                  encoding="utf-8")
            proc = run_cli(input_path=input_path)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        after = snapshot()
        self.assertEqual(before, after, "planner wrote into the project tree")


class PlannerReportingTests(unittest.TestCase):
    maxDiff = None

    def test_classification_consistency_report(self):
        rows = []
        for name in fixture_names():
            expected = load_json(FIXTURES / f"{name}.expected.json")
            proc = run_cli(input_path=FIXTURES / f"{name}.json")
            payload = parse_stdout(proc)
            has_classification = "classification" in payload
            rows.append({
                "fixture": name,
                "exit_code": proc.returncode,
                "has_classification": has_classification,
                "actual_level": payload["classification"]["level"] if has_classification else None,
            })
            if expected["exit_code"] == 2:
                self.assertFalse(has_classification, name)
            else:
                self.assertEqual(rows[-1]["actual_level"], expected["level"], name)

        report = {
            "report_role": "classification_consistency",
            "plan_usability": "not_a_verification_result",
            "rows": rows,
        }
        assert_clean(self, report)
        self.assertEqual(len(report["rows"]), len(fixture_names()))

    def test_contract_doc_marks_freeze(self):
        text = CONTRACT_DOC.read_text(encoding="utf-8")
        self.assertIn("6486678110", text)
        self.assertIn("FROZEN", text)
        self.assertNotIn("请 Owner 审阅", text)

    def test_schema_copy_matches_contract_ids(self):
        schema = load_json(PLANNER / "schema.json")
        self.assertEqual(schema["input"]["$id"], "verification-planner-input/1")
        self.assertEqual(schema["plan_output"]["$id"], "verification-planner-output/1")
        self.assertEqual(schema["diagnostic"]["$id"], "verification-planner-diagnostic/1")
        self.assertEqual(schema["plan_output"]["properties"]["checks"]["properties"]
                         ["counterfactual_diff"]["maxItems"], 0)


if __name__ == "__main__":
    unittest.main()
