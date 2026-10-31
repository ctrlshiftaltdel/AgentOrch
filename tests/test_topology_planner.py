"""Tests for the read-only task topology planner (Issue #178).

The planner is an observe-only recommendation CLI: it reads one caller snapshot,
prints one JSON document and never executes a task, spawns an instance, writes a
file or changes task state. Exit code 2 is a structural / usage diagnostic; every
plan (including ``BLOCKED`` and ``MANUAL_HANDOFF``) is exit code 3. Exit code 0 is
deliberately unused: a recommendation is not a dispatch, an approval or a
``VERIFIED`` signal.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "scripts" / "topology_planner"
CLI = PLANNER / "cli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "topology_planner"

OUTPUT_SCHEMA = "topology-planner-output/1"
DIAGNOSTIC_SCHEMA = "topology-planner-diagnostic/1"
INPUT_SCHEMA = "topology-planner-input/1"
PLANNER_VERSION = "178-draft-1"
CANDIDATE_IDS = ["T0", "T1", "T2", "T3", "T4"]
STATUSES = {"RECOMMENDED", "BLOCKED", "MANUAL_HANDOFF"}
# A caller snapshot may never pre-set a dispatch, approval or result field.
FORBIDDEN_CALLER_FIELDS = [
    "dispatched", "dispatch_ok", "may_dispatch", "attempt_allowed",
    "approved", "verified", "status", "selection_status", "selected_topology",
    "plan_usability",
]


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fixture_names():
    return sorted(
        path.name[: -len(".json")]
        for path in FIXTURES.glob("*.json")
        if not path.name.endswith(".expected.json")
    )


def base_payload(name="fx_tool_task"):
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
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
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


class PlanAssertions:
    maxDiff = None

    def assertDiagnostic(self, payload, code):
        self.assertEqual(payload["schema_version"], DIAGNOSTIC_SCHEMA)
        self.assertEqual(payload["plan_usability"], "not_a_dispatch")
        self.assertIs(payload["dispatched"], False)
        self.assertEqual(set(payload),
                         {"schema_version", "plan_usability", "dispatched", "diagnostic"})
        self.assertEqual(payload["diagnostic"]["code"], code)
        self.assertIsInstance(payload["diagnostic"]["path"], str)
        self.assertNotIn("candidates", payload)
        self.assertNotIn("risk", payload)

    def assertPlan(self, payload, expected, name):
        self.assertEqual(payload["schema_version"], OUTPUT_SCHEMA)
        self.assertEqual(payload["planner_version"], PLANNER_VERSION)
        self.assertEqual(payload["plan_usability"], "not_a_dispatch")
        self.assertEqual(payload["mode"], "observe_only")
        self.assertIs(payload["dispatched"], False)
        self.assertIn(payload["selection_status"], STATUSES)
        self.assertEqual(payload["selection_status"], expected["selection_status"])
        self.assertEqual(payload["selected_topology"], expected["selected_topology"])
        if payload["selection_status"] != "RECOMMENDED":
            self.assertIsNone(payload["selected_topology"])
        else:
            self.assertIn(payload["selected_topology"], CANDIDATE_IDS)

        self.assertEqual(payload["risk"]["level"], expected["risk_level"])
        self.assertIs(payload["independence"]["verified_by_this_tool"], False)
        self.assertIn(payload["independence"]["assessment"],
                      {"not_required", "unproven"})

        candidates = payload["candidates"]
        self.assertEqual([item["id"] for item in candidates], CANDIDATE_IDS)
        eligibility = {item["id"]: item["eligible"] for item in candidates}
        self.assertEqual(eligibility, expected["candidate_eligibility"])
        for item in candidates:
            self.assertEqual(
                set(item),
                {"id", "structure", "roles", "eligible", "hard_constraints",
                 "excluded_reasons", "tradeoffs"},
            )
            for constraint in item["hard_constraints"]:
                self.assertEqual(set(constraint),
                                 {"constraint_id", "result", "reason_codes"})
                self.assertIn(constraint["result"],
                              {"pass", "fail", "not_applicable"})
            self.assertIsInstance(item["excluded_reasons"], list)
            self.assertIsInstance(item["tradeoffs"], list)

        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for needle in expected["required_substrings"]:
            self.assertIn(needle, text, f"{name}: missing {needle!r}")
        for needle in expected["forbidden_substrings"]:
            self.assertNotIn(needle, text, f"{name}: unexpected {needle!r}")

        decision_ids = [item["decision_id"]
                        for item in payload["required_human_decisions"]]
        self.assertEqual(decision_ids, expected["required_human_decision_ids"])

        self.assertIn("selection_is_not_dispatch", payload["limitations"])
        self.assertEqual(payload["acceptance_refs"],
                         [item["ac_id"] for item in base_payload(name)["acceptance"]])
        self.assertIsInstance(payload["decision_basis"], list)
        self.assertIsInstance(payload["alternatives"], list)
        self.assertIsInstance(payload["role_assignments"], list)
        for role in payload["role_assignments"]:
            self.assertEqual(set(role), {"role", "assignment"})
            self.assertEqual(role["assignment"], "unassigned")


class PlannerFixtureTests(PlanAssertions, unittest.TestCase):
    def test_fixture_inventory(self):
        names = fixture_names()
        self.assertEqual(len(names), 7)
        for name in names:
            expected = load_json(FIXTURES / f"{name}.expected.json")
            self.assertIn(expected["exit_code"], (2, 3))

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
                self.assertEqual(payload["plan_usability"], "not_a_dispatch")
                self.assertIs(payload["dispatched"], False)
                if expected["exit_code"] == 2:
                    self.assertDiagnostic(payload, expected["diagnostic_code"])
                else:
                    self.assertPlan(payload, expected, name)

    def test_exit_code_is_never_zero(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                proc = run_cli(input_path=FIXTURES / f"{name}.json")
                self.assertIn(proc.returncode, (2, 3))


class PlannerInvariantTests(PlanAssertions, unittest.TestCase):
    def test_high_risk_claim_cannot_downgrade(self):
        payload = base_payload()
        payload["implementation"]["required"] = True
        payload["risk"] = {"signals": ["security"], "claimed_level": "low"}
        proc = run_payload(payload)
        self.assertEqual(proc.returncode, 3, proc.stderr or proc.stdout)
        self.assertEqual(proc.stderr, "")
        document = parse_stdout(proc)
        self.assertEqual(document["risk"]["level"], "high")
        self.assertEqual(document["selection_status"], "MANUAL_HANDOFF")
        self.assertIsNone(document["selected_topology"])
        self.assertIs(document["dispatched"], False)
        self.assertTrue(document["risk"]["downgrade_attempts"])
        self.assertEqual(document["risk"]["downgrade_attempts"][0]["claimed"], "low")
        self.assertEqual(document["risk"]["downgrade_attempts"][0]["effective"], "high")
        self.assertIn("caller_claim_lower_than_signals",
                      document["risk"]["downgrade_attempts"][0]["reason_codes"])

    def test_claimed_high_risk_is_kept(self):
        payload = base_payload()
        payload["implementation"]["required"] = True
        payload["risk"] = {"signals": [], "claimed_level": "high"}
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["risk"]["level"], "high")
        self.assertEqual(document["selection_status"], "MANUAL_HANDOFF")
        self.assertIsNone(document["selected_topology"])

    def test_ambiguity_forces_manual_handoff(self):
        document = parse_stdout(run_cli(input_path=FIXTURES / "fx_ambiguity.json"))
        self.assertEqual(document["selection_status"], "MANUAL_HANDOFF")
        self.assertIsNone(document["selected_topology"])
        eligibility = {item["id"]: item["eligible"]
                       for item in document["candidates"]}
        # T0 stays structurally eligible: the handoff is a governance result,
        # not a silent downgrade or a fabricated dispatch.
        self.assertTrue(eligibility["T0"])
        self.assertIs(document["dispatched"], False)

    def test_unknown_capability_never_recommends(self):
        payload = base_payload()
        payload["capabilities"] = {key: "unknown" for key in ("C1", "C2", "C3", "C4")}
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["selection_status"], "MANUAL_HANDOFF")
        self.assertIsNone(document["selected_topology"])
        self.assertIs(document["dispatched"], False)
        eligibility = {item["id"]: item["eligible"]
                       for item in document["candidates"]}
        self.assertFalse(eligibility["T1"])
        self.assertFalse(eligibility["T2"])
        self.assertFalse(eligibility["T3"])
        self.assertTrue(eligibility["T4"])
        self.assertIn("capability_C1_unknown", json.dumps(document))

    def test_stale_capability_never_recommends(self):
        payload = base_payload()
        payload["implementation"]["required"] = True
        payload["capabilities"]["C3"] = "stale"
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["selection_status"], "MANUAL_HANDOFF")
        self.assertIsNone(document["selected_topology"])
        self.assertIn("capability_C3_stale", json.dumps(document))
        self.assertIn("runtime_capability_gap",
                      [item["decision_id"]
                       for item in document["required_human_decisions"]])

    def test_reviewer_self_report_is_not_evidence(self):
        document = parse_stdout(run_cli(input_path=FIXTURES / "fx_wrong_identity.json"))
        independence = document["independence"]
        self.assertTrue(independence["required"])
        self.assertEqual(independence["assessment"], "unproven")
        self.assertIs(independence["verified_by_this_tool"], False)
        rejected = json.dumps(independence, sort_keys=True)
        for claim in ("model_family", "model_alias", "registry_status"):
            self.assertIn(claim, rejected)
        self.assertEqual(document["selection_status"], "MANUAL_HANDOFF")
        self.assertIsNone(document["selected_topology"])
        self.assertIs(document["dispatched"], False)

    def test_path_conflict_blocks_every_candidate(self):
        payload = base_payload()
        payload["locks"]["path_conflicts"] = ["scripts/foo.py"]
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["selection_status"], "BLOCKED")
        self.assertIsNone(document["selected_topology"])
        for item in document["candidates"]:
            self.assertFalse(item["eligible"], item["id"])
        self.assertIn("path_conflict:scripts/foo.py", json.dumps(document))

    def test_resource_conflict_blocks_every_candidate(self):
        payload = base_payload()
        payload["locks"]["resource_conflicts"] = ["db:main"]
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["selection_status"], "BLOCKED")
        self.assertIn("resource_conflict:db:main", json.dumps(document))

    def test_missing_tool_permission_hands_off(self):
        payload = base_payload()
        payload["tools"] = {"required": ["read_file"], "available": []}
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["selection_status"], "MANUAL_HANDOFF")
        self.assertIsNone(document["selected_topology"])
        self.assertIn("tool_permission_missing:read_file", json.dumps(document))
        eligibility = {item["id"]: item["eligible"]
                       for item in document["candidates"]}
        self.assertTrue(eligibility["T4"])

    def test_parallel_task_selects_t3(self):
        payload = base_payload()
        payload["implementation"]["required"] = True
        payload["context"] = {"load": "medium", "isolation_required": True,
                              "parallelizable": True, "subtask_count": 2}
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["selection_status"], "RECOMMENDED")
        self.assertEqual(document["selected_topology"], "T3")
        self.assertIs(document["dispatched"], False)

    def test_master_exception_keeps_t0_available(self):
        payload = base_payload()
        payload["implementation"] = {"required": True, "master_exception": True}
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["selection_status"], "RECOMMENDED")
        self.assertEqual(document["selected_topology"], "T0")

    def test_missing_dependency_blocks_every_candidate(self):
        document = parse_stdout(
            run_cli(input_path=FIXTURES / "fx_missing_dependency.json"))
        self.assertEqual(document["selection_status"], "BLOCKED")
        self.assertIsNone(document["selected_topology"])
        for item in document["candidates"]:
            self.assertFalse(item["eligible"], item["id"])
        self.assertIn("dependency_not_verified:#999", json.dumps(document))

    def test_ordinary_tool_task_selects_t0_without_dispatch(self):
        document = parse_stdout(run_cli(input_path=FIXTURES / "fx_tool_task.json"))
        self.assertEqual(document["selection_status"], "RECOMMENDED")
        self.assertEqual(document["selected_topology"], "T0")
        self.assertIs(document["dispatched"], False)
        self.assertIn("selection_is_not_dispatch", document["limitations"])
        for role in document["role_assignments"]:
            self.assertEqual(role["assignment"], "unassigned")

    def test_no_subagent_capability_never_fabricates_dispatch(self):
        document = parse_stdout(run_cli(input_path=FIXTURES / "fx_no_subagent.json"))
        self.assertEqual(document["selection_status"], "MANUAL_HANDOFF")
        self.assertIsNone(document["selected_topology"])
        self.assertIs(document["dispatched"], False)
        self.assertFalse(any(item["eligible"] for item in document["candidates"]
                             if item["id"] != "T4"))

    def test_edition_twins_recommend_identically(self):
        payload = base_payload()
        local = parse_stdout(run_payload(dict(payload, edition="local")))
        github = parse_stdout(run_payload(dict(payload, edition="github")))
        self.assertEqual(local["edition"], "local")
        self.assertEqual(github["edition"], "github")
        for key in ("risk", "independence", "candidates", "selection_status",
                    "selected_topology", "required_human_decisions",
                    "role_assignments"):
            self.assertEqual(local[key], github[key], key)
        self.assertIs(local["dispatched"], False)
        self.assertIs(github["dispatched"], False)

    def test_model_matching_is_deferred_not_reimplemented(self):
        document = parse_stdout(run_cli(input_path=FIXTURES / "fx_tool_task.json"))
        self.assertEqual(document["model_matching"], {
            "status": "deferred",
            "authority": "existing_s2_s4_model_selection",
            "reason": "topology_selection_is_upstream_of_model_matching",
        })
        self.assertIs(document["dispatched"], False)

    def test_candidate_inventory_and_shape(self):
        document = parse_stdout(run_cli(input_path=FIXTURES / "fx_tool_task.json"))
        self.assertEqual([item["id"] for item in document["candidates"]],
                         CANDIDATE_IDS)
        roles = {role for item in document["candidates"] for role in item["roles"]}
        self.assertTrue({"main_loop", "tool", "skill"} <= roles)
        self.assertTrue({"execution", "independent_reviewer"} <= roles)
        self.assertTrue({"parallel_execution", "manual_operator"} <= roles)


class PlannerNegativeTests(PlanAssertions, unittest.TestCase):
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

    def test_unknown_field_is_rejected(self):
        payload = base_payload()
        payload["extra_advice"] = "dispatch now"
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["diagnostic"]["code"], "unknown_field")
        self.assertIn("$.extra_advice", json.dumps(document))

    def test_unknown_risk_signal_is_rejected(self):
        payload = base_payload()
        payload["risk"]["signals"] = ["looks_fine"]
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["diagnostic"]["code"], "unknown_field")

    def test_bad_edition_is_rejected(self):
        payload = base_payload()
        payload["edition"] = "cloud"
        self.assertEqual(parse_stdout(run_payload(payload))["diagnostic"]["code"],
                         "edition_invalid")

    def test_bad_path_is_rejected(self):
        for path in ("../escape.py", "/abs.py", "docs/./charter.md", "docs/*.md"):
            with self.subTest(path=path):
                payload = base_payload()
                payload["changed_paths"] = [{"path": path, "source_ref": "caller:diff/1"}]
                document = parse_stdout(run_payload(payload))
                self.assertEqual(document["diagnostic"]["code"], "path_invalid")

    def test_usage_errors(self):
        cases = [[], ["--input"], ["--input", "a.json", "--extra"],
                 ["--in", "a.json"], ["--input=a.json"]]
        for args in cases:
            with self.subTest(args=args):
                proc = subprocess.run([sys.executable, str(CLI), *args],
                                      capture_output=True, text=True, cwd=str(ROOT))
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stderr, "")
                self.assertDiagnostic(parse_stdout(proc), "usage")

    def test_missing_input_file_is_a_schema_diagnostic(self):
        proc = run_cli(input_path=ROOT / "does-not-exist-178.json")
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
                (FIXTURES / "fx_tool_task.json").read_text(encoding="utf-8"),
                encoding="utf-8")
            proc = run_cli(input_path=input_path)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        after = snapshot()
        self.assertEqual(before, after, "planner wrote into the project tree")


class PlannerContractTests(unittest.TestCase):
    def test_source_imports_only_stdlib(self):
        import ast

        allowed = {
            "__future__", "json", "os", "re", "sys", "pathlib", "classify",
        }
        for name in ("cli.py", "classify.py"):
            tree = ast.parse((PLANNER / name).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            self.assertTrue(imported <= allowed, (name, sorted(imported - allowed)))

    def test_documented_entry_point_exists(self):
        self.assertTrue(CLI.is_file())
        text = CLI.read_text(encoding="utf-8")
        self.assertIn("scripts/topology_planner/cli.py --input PATH", text)


if __name__ == "__main__":
    unittest.main()
