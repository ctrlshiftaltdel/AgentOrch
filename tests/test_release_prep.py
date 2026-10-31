"""Tests for the read-only release preparation report (Issue #194).

The tool answers one question, read-only: given the caller's latest published
tag and a snapshot of merged changes plus evidence, which single next version (if
any) may be *prepared*? A report is never a release, never a tag, never an Owner
authorization and never a ``VERIFIED`` claim.

Exit codes are only 2 (structural / usage diagnostic) and 3 (report written).
Exit code 0 is deliberately unused. stdout carries exactly one JSON document and
stderr stays empty.

Every rule from Issue #194 has its own fixture under
``tests/fixtures/release_prep/``; the fixture name describes the rule.
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
PACKAGE = ROOT / "scripts" / "release_prep"
CLI = PACKAGE / "cli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "release_prep"

EXPECTED_FIXTURES = {
    "fx_blocked_assets_unreliable": "blocked: asset evidence unreliable",
    "fx_blocked_ci_unreliable": "blocked: CI evidence unreliable",
    "fx_blocked_identity_missing": "blocked: identity evidence missing",
    "fx_blocked_permissions_missing": "blocked: permission evidence missing",
    "fx_duplicate_ids": "unpublished items deduplicated by id",
    "fx_legacy_two_part_tag": "historical two-part tag normalised",
    "fx_local_snapshot": "edition is a snapshot field only",
    "fx_major_breaking": "MAJOR breaking change",
    "fx_minor_feature": "MINOR backward-compatible capability",
    "fx_mixed_max_bump": "mixed changes take the maximum bump",
    "fx_no_eligible": "no eligible change means no release",
    "fx_patch_batch": "PATCH fix batch",
    "fx_structural_missing_tag": "structural: missing baseline tag",
    "fx_structural_unknown_field": "structural: unknown field",
    "fx_urgent_fix": "urgent fix preempts batching",
    "fx_urgent_patch_beside_minor": "urgency never lowers the bump",
    "fx_version_mismatch": "baseline disagreeing with VERSION.md blocks",
}


def load_module(name, path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

report = load_module("release_prep_report", PACKAGE / "report.py")
cli_module = load_module("release_prep_cli", CLI)


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
        path = Path(tmp) / "input.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return run_cli(input_path=path)


def base_payload(name="fx_patch_batch"):
    return load_json(FIXTURES / f"{name}.json")


class FixtureTests(unittest.TestCase):
    maxDiff = None

    def test_fixture_inventory(self):
        self.assertEqual(fixture_names(), sorted(EXPECTED_FIXTURES))
        for name in fixture_names():
            expected = load_json(FIXTURES / f"{name}.expected.json")
            self.assertIn(expected["exit_code"], (2, 3), name)

    def test_every_fixture(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                expected = load_json(FIXTURES / f"{name}.expected.json")
                proc = run_cli(input_path=FIXTURES / f"{name}.json")
                self.assertEqual(proc.returncode, expected["exit_code"],
                                 proc.stderr or proc.stdout)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stderr, "", "stderr must stay empty")
                self.assertEqual(proc.stdout.count("\n"), 1,
                                 "stdout must be exactly one JSON line")
                payload = parse_stdout(proc)
                self.assertEqual(payload["plan_usability"], "not_a_release")
                self.assertIs(payload["release_authorized"], False)
                if expected["exit_code"] == 2:
                    self.assertDiagnostic(payload, expected)
                else:
                    self.assertReport(name, payload, expected)

    def assertDiagnostic(self, payload, expected):
        self.assertEqual(payload["schema_version"], "release-prep-diagnostic/1")
        self.assertEqual(payload["diagnostic"], {
            "code": expected["diagnostic_code"],
            "path": expected["diagnostic_path"],
        })
        self.assertEqual(set(payload), {
            "schema_version", "plan_usability", "release_authorized", "diagnostic",
        })

    def assertReport(self, name, payload, expected):
        self.assertEqual(payload["schema_version"], "release-prep-report/1")
        self.assertEqual(payload["tool_version"], "194-draft-1")
        self.assertEqual(payload["mode"], "on_demand_read_only")
        self.assertIs(payload["persistent_background_service"], False)
        self.assertEqual(payload["edition_role"], "snapshot_only")
        self.assertEqual(payload["edition"], expected["edition"])

        baseline = payload["baseline"]
        self.assertEqual(baseline["tag_form"], expected["tag_form"])
        self.assertEqual(baseline["consistent"], not any(
            reason in ("version_record_missing", "version_baseline_mismatch")
            for reason in expected["blocked_reasons"]))
        self.assertEqual(baseline["normalized_tag"],
                         "v" + baseline["version"])

        self.assertEqual(payload["status"], expected["status"])
        self.assertEqual(payload["blocked_reasons"], expected["blocked_reasons"])

        changes = payload["changes"]
        raw_items = load_json(FIXTURES / f"{name}.json")["items"]
        self.assertEqual(changes["duplicate_occurrences"],
                         len(raw_items) - len({item["id"] for item in raw_items}))
        self.assertEqual(len(changes["eligible"]), expected["eligible_count"])
        self.assertEqual([item["id"] for item in changes["excluded"]],
                         [entry[0] for entry in expected["excluded"]])
        self.assertEqual([item["reason"] for item in changes["excluded"]],
                         [entry[1] for entry in expected["excluded"]])
        self.assertEqual(changes["duplicate_ids"], expected["duplicate_ids"])

        recommendation = payload["recommendation"]
        self.assertEqual(recommendation["bump"], expected["bump"])
        self.assertEqual(recommendation["next_version"], expected["next_version"])
        self.assertEqual(recommendation["tag"], expected["next_version"])
        self.assertEqual(recommendation["trigger"], expected["trigger"])
        self.assertEqual(recommendation["urgent_ids"], expected["urgent_ids"])
        self.assertEqual(recommendation["reason"], expected["reason"])
        self.assertIs(recommendation["single_candidate"], True)
        if expected["next_version"] is None:
            self.assertIsNone(recommendation["bump"])
            self.assertIsNone(recommendation["tag"])

        self.assertEqual(set(payload["evidence"]), {"identity", "ci", "permissions", "assets"})
        for name_, entry in payload["evidence"].items():
            self.assertEqual(set(entry), {"status", "reliable"})
            self.assertIs(entry["reliable"], entry["status"] == "reliable", name_)

        self.assertEqual(payload["authorization"], {
            "owner_authorization_required": True,
            "standing_authorization": "empty",
            "bypasses_owner_authorization": False,
        })
        self.assertEqual(payload["actions"], {
            "creates_tag": False,
            "creates_release": False,
            "writes_project_files": False,
            "accesses_network": False,
        })
        verification = payload["release_verification"]
        self.assertIs(verification["executed_by_tool"], False)
        self.assertTrue(verification["commands"])
        for command in verification["commands"]:
            self.assertTrue(command.startswith("uv run python "), command)
        build_commands = [c for c in verification["commands"]
                          if "scripts/build_release.py" in c]
        if expected["next_version"] is None:
            self.assertEqual(build_commands, [])
        else:
            version_arg = "--release-version " + expected["next_version"][1:]
            self.assertEqual(len(build_commands), 2)
            self.assertTrue(all(version_arg in c for c in build_commands))
            self.assertTrue(any("--output-dir" in c for c in build_commands))

    def test_eligible_items_keep_change_list_risks_and_dependencies(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_patch_batch.json"))
        first = payload["changes"]["eligible"][0]
        self.assertEqual(first["id"], "PR-201")
        self.assertEqual(first["kind"], "patch")
        self.assertIs(first["urgent"], False)
        self.assertTrue(first["summary"])
        self.assertEqual(first["risks"], ["low: update entry only"])
        self.assertEqual(first["dependencies"], ["#193"])

    def test_no_eligible_change_suggests_no_version(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_no_eligible.json"))
        self.assertEqual(payload["status"], "no_release")
        self.assertIsNone(payload["recommendation"]["next_version"])
        self.assertEqual(payload["recommendation"]["reason"],
                         "empty_release_not_allowed")
        self.assertEqual(payload["changes"]["eligible"], [])

    def test_blocked_report_never_suggests_a_version(self):
        for name in ("fx_blocked_identity_missing", "fx_blocked_ci_unreliable",
                     "fx_blocked_permissions_missing", "fx_blocked_assets_unreliable",
                     "fx_version_mismatch"):
            with self.subTest(fixture=name):
                payload = parse_stdout(run_cli(input_path=FIXTURES / f"{name}.json"))
                self.assertEqual(payload["status"], "blocked")
                self.assertIsNone(payload["recommendation"]["next_version"])
                self.assertIsNone(payload["recommendation"]["bump"])
                self.assertTrue(payload["blocked_reasons"])


class InvariantTests(unittest.TestCase):
    maxDiff = None

    def test_exit_code_is_never_zero(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                proc = run_cli(input_path=FIXTURES / f"{name}.json")
                self.assertIn(proc.returncode, (2, 3))
                self.assertEqual(proc.stderr, "")

    def test_report_never_claims_a_release_or_authorization(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                proc = run_cli(input_path=FIXTURES / f"{name}.json")
                payload = parse_stdout(proc)
                self.assertEqual(payload["plan_usability"], "not_a_release")
                self.assertIs(payload["release_authorized"], False)
                if proc.returncode == 3:
                    self.assertIs(payload["actions"]["creates_tag"], False)
                    self.assertIs(payload["actions"]["creates_release"], False)
                    self.assertIs(payload["authorization"]["bypasses_owner_authorization"],
                                  False)
                    self.assertEqual(payload["authorization"]["standing_authorization"],
                                     "empty")

    def test_urgency_does_not_lower_the_bump(self):
        urgent = parse_stdout(run_cli(input_path=FIXTURES / "fx_urgent_patch_beside_minor.json"))
        plain = parse_stdout(run_cli(input_path=FIXTURES / "fx_minor_feature.json"))
        self.assertEqual(urgent["recommendation"]["bump"], "minor")
        self.assertEqual(urgent["recommendation"]["next_version"],
                         plain["recommendation"]["next_version"])
        self.assertEqual(urgent["recommendation"]["trigger"], "urgent_fix")

    def test_urgent_fix_yields_exactly_one_suggestion(self):
        payload = parse_stdout(run_cli(input_path=FIXTURES / "fx_urgent_fix.json"))
        self.assertEqual(payload["recommendation"]["trigger"], "urgent_fix")
        self.assertEqual(payload["recommendation"]["urgent_ids"], ["PR-241"])
        self.assertEqual(payload["recommendation"]["next_version"], "v1.7.1")
        self.assertIs(payload["recommendation"]["single_candidate"], True)

    def test_edition_is_only_a_snapshot_field(self):
        local = parse_stdout(run_cli(input_path=FIXTURES / "fx_local_snapshot.json"))
        github = parse_stdout(run_cli(input_path=FIXTURES / "fx_minor_feature.json"))
        self.assertEqual(local["edition"], "local")
        self.assertEqual(github["edition"], "github")
        self.assertEqual(local["recommendation"]["next_version"],
                         github["recommendation"]["next_version"])
        self.assertEqual(local["mode"], github["mode"])
        self.assertEqual(local["edition_role"], "snapshot_only")

    def test_new_files_are_not_required_for_distribution(self):
        from scripts import validate_kit

        required = set(validate_kit.REQUIRED)
        self.assertNotIn("scripts/release_prep/cli.py", required)
        self.assertNotIn("scripts/release_prep/report.py", required)
        self.assertNotIn("tests/test_release_prep.py", required)
        self.assertFalse(any(path.startswith("scripts/release_prep/") for path in required))
        self.assertFalse(any(path.startswith("tests/fixtures/release_prep/")
                             for path in required))

    def test_release_readme_states_reports_are_not_releases(self):
        text = (ROOT / "release" / "README.md").read_text(encoding="utf-8")
        self.assertIn("准备报告不是发布", text)
        self.assertIn("常设自动发版授权仍为空", text)

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
                self.assertEqual(payload["diagnostic"]["path"], "$")
                self.assertEqual(payload["plan_usability"], "not_a_release")
                self.assertIs(payload["release_authorized"], False)

    def test_unreadable_and_malformed_input_is_structural(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = run_cli(input_path=Path(tmp) / "absent.json")
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(missing.stderr, "")
            self.assertEqual(json.loads(missing.stdout)["diagnostic"]["code"],
                             "schema_invalid")

            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            proc = run_cli(input_path=broken)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stderr, "")
            self.assertEqual(json.loads(proc.stdout)["diagnostic"]["code"],
                             "schema_invalid")

            array = Path(tmp) / "array.json"
            array.write_text("[1, 2, 3]", encoding="utf-8")
            proc = run_cli(input_path=array)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(json.loads(proc.stdout)["diagnostic"]["code"],
                             "schema_invalid")

    def test_paths_are_reported_for_structural_errors(self):
        payload = base_payload()
        payload["items"][0]["kind"] = "feature"
        proc = run_payload(payload)
        self.assertEqual(proc.returncode, 2)
        document = json.loads(proc.stdout)
        self.assertEqual(document["diagnostic"],
                         {"code": "schema_invalid", "path": "$.items[0].kind"})

    def test_duplicate_items_are_merged_pessimistically(self):
        payload = base_payload()
        payload["items"].append({
            "id": "PR-201", "kind": "patch", "state": "merged_unverified",
            "summary": "fix empty tag crash in update entry",
        })
        proc = run_payload(payload)
        self.assertEqual(proc.returncode, 3)
        document = json.loads(proc.stdout)
        self.assertEqual(document["changes"]["duplicate_ids"], ["PR-201"])
        self.assertEqual(document["changes"]["duplicate_occurrences"], 1)
        self.assertEqual(document["changes"]["received"], 3)
        self.assertEqual(document["changes"]["unique"], 2)
        self.assertEqual([item["id"] for item in document["changes"]["eligible"]],
                         ["PR-202"])
        self.assertEqual([item["id"] for item in document["changes"]["excluded"]],
                         ["PR-201"])

    def test_empty_change_list_is_not_a_release(self):
        payload = base_payload()
        payload["items"] = []
        proc = run_payload(payload)
        self.assertEqual(proc.returncode, 3)
        document = json.loads(proc.stdout)
        self.assertEqual(document["status"], "no_release")
        self.assertIsNone(document["recommendation"]["next_version"])
        self.assertEqual(document["recommendation"]["reason"],
                         "empty_release_not_allowed")

    def test_absent_evidence_blocks_every_category(self):
        payload = base_payload()
        del payload["evidence"]
        proc = run_payload(payload)
        self.assertEqual(proc.returncode, 3)
        document = json.loads(proc.stdout)
        self.assertEqual(document["status"], "blocked")
        self.assertEqual(document["blocked_reasons"], [
            "identity_missing", "ci_missing", "permissions_missing",
            "assets_missing",
        ])
        self.assertIsNone(document["recommendation"]["next_version"])

    def test_absent_version_record_blocks(self):
        payload = base_payload()
        del payload["release_version_record"]
        proc = run_payload(payload)
        self.assertEqual(proc.returncode, 3)
        document = json.loads(proc.stdout)
        self.assertEqual(document["status"], "blocked")
        self.assertEqual(document["blocked_reasons"], ["version_record_missing"])
        self.assertEqual(document["recommendation"]["reason"],
                         "version_record_missing")

    def test_urgent_excluded_item_does_not_trigger(self):
        payload = base_payload()
        payload["items"][0]["urgent"] = True
        payload["items"][0]["state"] = "merged_unverified"
        proc = run_payload(payload)
        self.assertEqual(proc.returncode, 3)
        document = json.loads(proc.stdout)
        self.assertEqual(document["recommendation"]["urgent_ids"], [])
        self.assertEqual(document["recommendation"]["trigger"], "batch")

    def test_process_write_set_is_empty(self):
        def snapshot():
            files = {}
            for path in sorted(ROOT.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(ROOT)
                if rel.parts and rel.parts[0] in (".git", "__pycache__"):
                    continue
                if "__pycache__" in rel.parts or path.suffix == ".pyc":
                    continue
                files[str(rel)] = (path.stat().st_size, path.stat().st_mtime_ns)
            return files

        before = snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text(json.dumps(base_payload(), ensure_ascii=False),
                            encoding="utf-8")
            proc = run_cli(input_path=path)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(before, snapshot(), "cli wrote into the project tree")


class ModuleContractTests(unittest.TestCase):
    def test_run_returns_two_or_three(self):
        code, document = report.build(base_payload())
        self.assertEqual(code, 3)
        self.assertEqual(document["plan_usability"], "not_a_release")
        code, document = report.build({})
        self.assertEqual(code, 2)
        self.assertEqual(document["diagnostic"]["code"], "schema_invalid")

    def test_cli_main_uses_the_input_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text(json.dumps(base_payload()), encoding="utf-8")
            code, document = cli_module.run(["--input", str(path)])
        self.assertEqual(code, 3)
        self.assertEqual(document["status"], "candidate")

    def test_schema_declares_the_output_contract(self):
        schema = load_json(PACKAGE / "schema.json")
        self.assertEqual(schema["input"]["$id"], "release-prep-input/1")
        self.assertEqual(schema["report"]["$id"], "release-prep-report/1")
        self.assertEqual(schema["diagnostic"]["$id"], "release-prep-diagnostic/1")
        self.assertFalse(schema["input"]["additionalProperties"])
        self.assertFalse(schema["report"]["additionalProperties"])

    def test_cli_and_report_are_stdlib_only(self):
        text = CLI.read_text(encoding="utf-8")
        report_text = (PACKAGE / "report.py").read_text(encoding="utf-8")
        self.assertIn("def main", text)
        for source in (text, report_text):
            for module in ("requests", "urllib.request", "http.client",
                           "socket", "subprocess"):
                self.assertNotIn("import " + module, source)


if __name__ == "__main__":
    unittest.main()
