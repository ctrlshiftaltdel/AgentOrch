"""Tests for the read-only runtime handoff decision (Issue #179, Stage C).

The decision CLI consumes one caller snapshot of observed runtime facts and
prints one JSON document. It never executes a task, spawns an instance, waits,
collects, writes a file, contacts the network or changes task state. Exit code 2
is a structural / usage diagnostic; every decision (including a complete
capability set and every handoff) is exit code 3. Exit code 0 is deliberately
unused: a complete capability observation is not a dispatch, an authorization or
a ``VERIFIED`` signal.

A runtime name is never evidence. ``opencode``, ``codex`` and ``dsh`` have no
built-in support here: a capability only counts as observed when the caller
supplies ``OBSERVED`` evidence, and everything else stays ``UNKNOWN``.
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
RUNTIME = ROOT / "scripts" / "topology_runtime"
CLI = RUNTIME / "cli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "topology_runtime"

OUTPUT_SCHEMA = "topology-runtime-output/1"
DIAGNOSTIC_SCHEMA = "topology-runtime-diagnostic/1"
INPUT_SCHEMA = "topology-runtime-input/1"
ADAPTER_VERSION = "179-draft-1"
RUNTIME_STATUSES = {"CAPABLE", "MANUAL_HANDOFF"}
CAPABILITY_NAMES = ("spawn", "wait", "collect", "context_isolation",
                    "tool_permissions")
IDENTITY_STATUSES = {"CONFIRMED", "UNCONFIRMED", "UNKNOWN", "ABSENT",
                     "EXPIRED", "CONFLICT", "INVALID"}

OUTPUT_KEYS = {
    "schema_version", "adapter_version", "plan_usability", "mode", "dispatched",
    "task_ref", "edition", "as_of", "runtime", "runtime_status", "identity",
    "locks", "handoff", "required_human_decisions", "limitations",
    "evidence_refs", "runtime_support_inferred", "dispatch_authority",
}
RUNTIME_KEYS = {
    "runtime_id", "capability_facts", "tool_permissions", "capability_source",
}
CAPABILITY_KEYS = {
    "declared_state", "effective_state", "evidence_class", "evidence_ref",
    "observed_at", "downgrade_reasons",
}
TOOL_PERMISSION_KEYS = {
    "declared_state", "effective_state", "granted_tools", "required_tools",
    "missing_tools", "evidence_class", "evidence_ref", "observed_at",
    "downgrade_reasons",
}
IDENTITY_KEYS = {
    "declared_status", "status", "canonical_identity", "verified_until",
    "source_ref", "trusted", "reason_codes",
}
LOCK_KEYS = {"path_conflicts", "resource_conflicts", "clear"}
HANDOFF_KEYS = {
    "required", "mode", "reason_codes", "missing_capabilities", "missing_tools",
    "operator_actions",
}

# A caller snapshot may never pre-set a dispatch, capability or result field.
FORBIDDEN_CALLER_FIELDS = [
    "dispatched", "dispatch_ok", "may_dispatch", "attempt_allowed", "approved",
    "verified", "status", "runtime_status", "handoff_required", "plan_usability",
    "identity_ok", "identity_trusted", "capable", "supported", "skip", "waiver",
    "override", "ready",
]


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fixture_names():
    return sorted(
        path.name[: -len(".json")]
        for path in FIXTURES.glob("*.json")
        if not path.name.endswith(".expected.json")
    )


def base_payload(name="fx_normal"):
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


class RuntimeAssertions:
    maxDiff = None

    def assertNeverDispatched(self, payload):
        self.assertEqual(payload["plan_usability"], "not_a_dispatch")
        self.assertIs(payload["dispatched"], False)
        self.assertIs(payload["runtime_support_inferred"], False)
        for node in walk(payload):
            if isinstance(node, dict) and "dispatched" in node:
                self.assertIs(node["dispatched"], False)

    def assertDiagnostic(self, payload, code):
        self.assertEqual(payload["schema_version"], DIAGNOSTIC_SCHEMA)
        self.assertEqual(payload["plan_usability"], "not_a_dispatch")
        self.assertIs(payload["dispatched"], False)
        self.assertEqual(set(payload),
                         {"schema_version", "plan_usability", "dispatched",
                          "diagnostic"})
        self.assertEqual(payload["diagnostic"]["code"], code)
        self.assertIsInstance(payload["diagnostic"]["path"], str)
        self.assertNotIn("runtime", payload)
        self.assertNotIn("handoff", payload)

    def assertDecision(self, payload, expected, name):
        self.assertEqual(payload["schema_version"], OUTPUT_SCHEMA)
        self.assertEqual(payload["adapter_version"], ADAPTER_VERSION)
        self.assertEqual(payload["mode"], "observe_only")
        self.assertNeverDispatched(payload)
        self.assertEqual(set(payload), OUTPUT_KEYS)
        self.assertEqual(payload["runtime_status"], expected["runtime_status"])
        self.assertIn(payload["runtime_status"], RUNTIME_STATUSES)
        self.assertEqual(payload["handoff"]["required"],
                         expected["handoff_required"])
        self.assertEqual(payload["identity"]["trusted"],
                         expected["trusted_identity"])
        self.assertEqual(payload["handoff"]["missing_capabilities"],
                         expected["missing_capabilities"])
        self.assertEqual(
            [item["decision_id"]
             for item in payload["required_human_decisions"]],
            expected["required_human_decision_ids"],
        )
        if payload["handoff"]["required"]:
            self.assertEqual(payload["runtime_status"], "MANUAL_HANDOFF")
            self.assertEqual(payload["handoff"]["mode"], "manual_session")
            self.assertTrue(payload["handoff"]["operator_actions"])
        else:
            self.assertEqual(payload["runtime_status"], "CAPABLE")
            self.assertEqual(payload["handoff"]["mode"], "none")
            self.assertEqual(payload["handoff"]["operator_actions"], [])

        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for needle in expected["required_substrings"]:
            self.assertIn(needle, text, f"{name}: missing {needle!r}")
        for needle in expected["forbidden_substrings"]:
            self.assertNotIn(needle, text, f"{name}: unexpected {needle!r}")

        # Structural shape.
        self.assertEqual(set(payload["runtime"]), RUNTIME_KEYS)
        self.assertEqual(set(payload["runtime"]["capability_facts"]),
                         {"spawn", "wait", "collect", "context_isolation"})
        for fact in payload["runtime"]["capability_facts"].values():
            self.assertEqual(set(fact), CAPABILITY_KEYS)
        self.assertEqual(set(payload["runtime"]["tool_permissions"]),
                         TOOL_PERMISSION_KEYS)
        self.assertEqual(set(payload["identity"]), IDENTITY_KEYS)
        self.assertIn(payload["identity"]["status"], IDENTITY_STATUSES)
        self.assertEqual(set(payload["locks"]), LOCK_KEYS)
        self.assertEqual(set(payload["handoff"]), HANDOFF_KEYS)
        for decision in payload["required_human_decisions"]:
            self.assertEqual(set(decision), {"decision_id", "reason_codes"})
        self.assertTrue(payload["limitations"])
        self.assertTrue(payload["evidence_refs"])


class RuntimeFixtureTests(RuntimeAssertions, unittest.TestCase):
    def test_fixture_inventory(self):
        names = fixture_names()
        self.assertEqual(names, [
            "fx_concurrency_conflict", "fx_identity_untrusted",
            "fx_interrupted", "fx_normal", "fx_unavailable",
        ])
        for name in names:
            expected = load_json(FIXTURES / f"{name}.expected.json")
            self.assertEqual(expected["exit_code"], 3)

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
                self.assertDecision(payload, expected, name)

    def test_exit_code_is_never_zero(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                proc = run_cli(input_path=FIXTURES / f"{name}.json")
                self.assertIn(proc.returncode, (2, 3))

    def test_normal_observation_is_capable_but_not_a_dispatch(self):
        doc = parse_stdout(run_cli(input_path=FIXTURES / "fx_normal.json"))
        self.assertEqual(doc["runtime"]["runtime_id"], "fixture-runtime")
        self.assertEqual(doc["runtime_status"], "CAPABLE")
        self.assertEqual(doc["handoff"]["required"], False)
        self.assertEqual(doc["required_human_decisions"], [])
        self.assertEqual(doc["dispatch_authority"],
                         "existing_task_lock_and_master_review")
        for name in ("spawn", "wait", "collect", "context_isolation"):
            self.assertEqual(
                doc["runtime"]["capability_facts"][name]["effective_state"],
                "pass", name)
        self.assertEqual(
            doc["runtime"]["tool_permissions"]["effective_state"], "pass")

    def test_interrupted_collect_hands_off(self):
        doc = parse_stdout(run_cli(input_path=FIXTURES / "fx_interrupted.json"))
        self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")
        self.assertEqual(doc["handoff"]["missing_capabilities"], ["collect"])
        self.assertIn("capability_collect_fail",
                      json.dumps(doc["handoff"], sort_keys=True))
        self.assertIn("collect",
                      doc["runtime"]["capability_facts"])
        self.assertEqual(
            doc["runtime"]["capability_facts"]["collect"]["effective_state"],
            "fail")

    def test_unavailable_runtime_never_becomes_pass(self):
        doc = parse_stdout(run_cli(input_path=FIXTURES / "fx_unavailable.json"))
        self.assertEqual(doc["runtime"]["runtime_id"], "codex")
        self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")
        self.assertEqual(set(doc["handoff"]["missing_capabilities"]),
                         set(CAPABILITY_NAMES))
        text = json.dumps(doc, sort_keys=True)
        self.assertNotIn('"effective_state": "pass"', text)
        self.assertIn("runtime_support_not_inferred_from_name", text)

    def test_identity_untrusted_hands_off_without_capability_gap(self):
        doc = parse_stdout(
            run_cli(input_path=FIXTURES / "fx_identity_untrusted.json"))
        self.assertFalse(doc["identity"]["trusted"])
        self.assertEqual(doc["identity"]["status"], "UNKNOWN")
        self.assertEqual(doc["handoff"]["missing_capabilities"], [])
        self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")
        self.assertIn("identity_status_unknown",
                      json.dumps(doc["handoff"], sort_keys=True))

    def test_concurrency_conflict_hands_off(self):
        doc = parse_stdout(
            run_cli(input_path=FIXTURES / "fx_concurrency_conflict.json"))
        self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")
        self.assertFalse(doc["locks"]["clear"])
        self.assertIn("scripts/orchestrator.py", doc["locks"]["path_conflicts"])
        self.assertIn("db:main", doc["locks"]["resource_conflicts"])
        self.assertEqual(doc["handoff"]["missing_capabilities"], [])


class RuntimeInvariantTests(RuntimeAssertions, unittest.TestCase):
    def test_runtime_name_is_not_support_evidence(self):
        for runtime_id in ("opencode", "codex", "dsh"):
            with self.subTest(runtime_id=runtime_id):
                payload = base_payload("fx_unavailable")
                payload["runtime"]["runtime_id"] = runtime_id
                doc = parse_stdout(run_payload(payload))
                self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")
                self.assertEqual(set(doc["handoff"]["missing_capabilities"]),
                                 set(CAPABILITY_NAMES))
                text = json.dumps(doc, sort_keys=True)
                self.assertNotIn('"effective_state": "pass"', text)
                self.assertIs(doc["runtime_support_inferred"], False)

    def test_pass_requires_observed_evidence(self):
        for evidence_class in ("INFERRED", "UNKNOWN", "STALE"):
            with self.subTest(evidence_class=evidence_class):
                payload = base_payload()
                payload["runtime"]["capabilities"]["spawn"][
                    "evidence_class"] = evidence_class
                doc = parse_stdout(run_payload(payload))
                fact = doc["runtime"]["capability_facts"]["spawn"]
                self.assertEqual(fact["declared_state"], "pass")
                self.assertEqual(fact["effective_state"], "unknown")
                self.assertIn("evidence_not_observed", fact["downgrade_reasons"])
                self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")
                self.assertIn("spawn", doc["handoff"]["missing_capabilities"])

    def test_stale_observation_never_stays_pass(self):
        payload = base_payload()
        payload["runtime"]["capabilities"]["wait"]["observed_at"] = \
            "2026-01-01T00:00:00Z"
        doc = parse_stdout(run_payload(payload))
        fact = doc["runtime"]["capability_facts"]["wait"]
        self.assertEqual(fact["effective_state"], "stale")
        self.assertIn("evidence_stale", fact["downgrade_reasons"])
        self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")

    def test_future_observation_never_stays_pass(self):
        payload = base_payload()
        payload["runtime"]["capabilities"]["collect"]["observed_at"] = \
            "2026-12-01T00:00:00Z"
        doc = parse_stdout(run_payload(payload))
        fact = doc["runtime"]["capability_facts"]["collect"]
        self.assertEqual(fact["effective_state"], "unknown")
        self.assertIn("evidence_from_future", fact["downgrade_reasons"])

    def test_identity_must_be_confirmed_and_unexpired(self):
        cases = [
            ("UNCONFIRMED", "provider:model:2026-10", "2026-12-01T00:00:00Z",
             "UNCONFIRMED"),
            ("CONFIRMED", None, "2026-12-01T00:00:00Z", "UNCONFIRMED"),
            ("CONFIRMED", "provider:model:2026-10", None, "UNCONFIRMED"),
            ("CONFIRMED", "provider:model:2026-10", "2026-10-01T00:00:00Z",
             "EXPIRED"),
        ]
        for status, canonical, until, effective in cases:
            with self.subTest(status=status, canonical=canonical, until=until):
                payload = base_payload()
                payload["identity"] = {
                    "declared_status": status,
                    "canonical_identity": canonical,
                    "verified_until": until,
                    "source_ref": "caller:identity",
                }
                doc = parse_stdout(run_payload(payload))
                self.assertFalse(doc["identity"]["trusted"])
                self.assertEqual(doc["identity"]["status"], effective)
                self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")
                self.assertIn("identity_untrusted",
                              [item["decision_id"] for item in
                               doc["required_human_decisions"]])

    def test_missing_tool_permission_hands_off(self):
        payload = base_payload()
        payload["runtime"]["tool_permissions"]["granted_tools"] = ["read_file"]
        doc = parse_stdout(run_payload(payload))
        tool = doc["runtime"]["tool_permissions"]
        self.assertEqual(tool["missing_tools"], ["write_file"])
        self.assertEqual(tool["effective_state"], "fail")
        self.assertIn("required_tool_missing", tool["downgrade_reasons"])
        self.assertIn("tool_permissions",
                      doc["handoff"]["missing_capabilities"])
        self.assertIn("write_file", doc["handoff"]["missing_tools"])
        self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")

    def test_context_isolation_failure_hands_off(self):
        payload = base_payload()
        payload["runtime"]["capabilities"]["context_isolation"]["state"] = "fail"
        doc = parse_stdout(run_payload(payload))
        self.assertEqual(
            doc["runtime"]["capability_facts"]["context_isolation"][
                "effective_state"], "fail")
        self.assertIn("context_isolation",
                      doc["handoff"]["missing_capabilities"])
        self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")

    def test_resource_conflict_alone_hands_off(self):
        payload = base_payload()
        payload["locks"]["resource_conflicts"] = ["db:main"]
        doc = parse_stdout(run_payload(payload))
        self.assertEqual(doc["runtime_status"], "MANUAL_HANDOFF")
        self.assertEqual(doc["handoff"]["missing_capabilities"], [])
        self.assertIn("resource_conflict:db:main",
                      json.dumps(doc["handoff"], sort_keys=True))

    def test_edition_twins_decide_identically(self):
        payload = base_payload()
        local = parse_stdout(run_payload(dict(payload, edition="local")))
        github = parse_stdout(run_payload(dict(payload, edition="github")))
        self.assertEqual(local["edition"], "local")
        self.assertEqual(github["edition"], "github")
        for key in ("runtime", "identity", "locks", "handoff",
                    "required_human_decisions", "runtime_status"):
            self.assertEqual(local[key], github[key], key)
        self.assertIs(local["dispatched"], False)
        self.assertIs(github["dispatched"], False)

    def test_evidence_refs_carry_caller_observations(self):
        doc = parse_stdout(run_cli(input_path=FIXTURES / "fx_normal.json"))
        refs = {(item["kind"], item["ref"]) for item in doc["evidence_refs"]}
        self.assertIn(("runtime_capability",
                       "probe:fixture-runtime/spawn/20261031"), refs)
        self.assertIn(("identity", "agent-registry:scope/fixture-runtime"), refs)
        self.assertIn(("locks", "caller:locks"), refs)


class RuntimeNegativeTests(RuntimeAssertions, unittest.TestCase):
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

    def test_unknown_nested_field_is_rejected(self):
        payload = base_payload()
        payload["runtime"]["capabilities"]["spawn"]["confidence"] = 1
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["diagnostic"]["code"], "unknown_field")
        self.assertIn("$.runtime.capabilities.spawn.confidence",
                      json.dumps(document))

    def test_missing_capability_is_a_schema_diagnostic(self):
        payload = base_payload()
        del payload["runtime"]["capabilities"]["collect"]
        proc = run_payload(payload)
        self.assertEqual(proc.returncode, 2)
        self.assertDiagnostic(parse_stdout(proc), "schema_invalid")

    def test_bad_capability_state_is_rejected(self):
        payload = base_payload()
        payload["runtime"]["capabilities"]["spawn"]["state"] = "supported"
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["diagnostic"]["code"], "schema_invalid")
        self.assertIn("$.runtime.capabilities.spawn.state",
                      json.dumps(document))

    def test_bad_evidence_class_is_rejected(self):
        payload = base_payload()
        payload["runtime"]["capabilities"]["spawn"]["evidence_class"] = "TRUST_ME"
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["diagnostic"]["code"], "schema_invalid")

    def test_bad_edition_is_rejected(self):
        payload = base_payload()
        payload["edition"] = "cloud"
        self.assertEqual(parse_stdout(run_payload(payload))["diagnostic"]["code"],
                         "edition_invalid")

    def test_bad_path_is_rejected(self):
        for path in ("../escape.py", "/abs.py", "docs/./charter.md",
                     "docs/*.md"):
            with self.subTest(path=path):
                payload = base_payload()
                payload["locks"]["path_conflicts"] = [path]
                document = parse_stdout(run_payload(payload))
                self.assertEqual(document["diagnostic"]["code"], "path_invalid")

    def test_bad_timestamp_is_rejected(self):
        for value in ("2026-11-01", "2026-11-01T00:00:00", "not-a-time"):
            with self.subTest(value=value):
                payload = base_payload()
                payload["as_of"] = value
                document = parse_stdout(run_payload(payload))
                self.assertEqual(document["diagnostic"]["code"], "schema_invalid")

    def test_bad_identity_status_is_rejected(self):
        payload = base_payload()
        payload["identity"]["declared_status"] = "PROBABLY_FINE"
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["diagnostic"]["code"], "schema_invalid")

    def test_duplicate_tool_ids_are_rejected(self):
        payload = base_payload()
        payload["task"]["required_tools"] = ["read_file", "read_file"]
        document = parse_stdout(run_payload(payload))
        self.assertEqual(document["diagnostic"]["code"], "schema_invalid")

    def test_usage_errors(self):
        cases = [[], ["--input"], ["--input", "a.json", "--extra"],
                 ["--in", "a.json"], ["--input=a.json"]]
        for args in cases:
            with self.subTest(args=args):
                proc = subprocess.run([sys.executable, str(CLI), *args],
                                      capture_output=True, text=True,
                                      cwd=str(ROOT))
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stderr, "")
                self.assertDiagnostic(parse_stdout(proc), "usage")

    def test_missing_input_file_is_a_schema_diagnostic(self):
        proc = run_cli(input_path=ROOT / "does-not-exist-179.json")
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
                (FIXTURES / "fx_normal.json").read_text(encoding="utf-8"),
                encoding="utf-8")
            proc = run_cli(input_path=input_path)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        after = snapshot()
        self.assertEqual(before, after, "runtime decision wrote into the tree")


class RuntimeContractTests(unittest.TestCase):
    def test_source_imports_only_stdlib(self):
        import ast

        allowed = {
            "__future__", "datetime", "json", "os", "re", "sys", "pathlib",
            "decide",
        }
        for name in ("cli.py", "decide.py"):
            tree = ast.parse((RUNTIME / name).read_text(encoding="utf-8"))
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
        self.assertIn("scripts/topology_runtime/cli.py --input PATH", text)

    def test_no_dispatch_symbol_is_emitted(self):
        text = (RUNTIME / "decide.py").read_text(encoding="utf-8")
        self.assertIn('"plan_usability": "not_a_dispatch"', text)
        self.assertIn('"dispatched": False', text)


if __name__ == "__main__":
    unittest.main()
