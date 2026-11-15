"""Text-level contract tests for `.github/workflows/ci.yml`.

The workflow is the carrier of this repository's G4 evidence, but it cannot be
imported and cannot run on a developer machine. These tests therefore read it as
text and pin the parts a review must not regress:

* the two required status-check contexts keep their exact names (the repository
  ruleset binds `checks (Python 3.12)` and `checks (Python 3.13)`);
* the risk tier is derived from the Git range of the run, never from a label or
  any other contributor-controlled value;
* doc-only (`L0`), code-only (`L1`) and full (`L2`/`L3`) changes really get
  different validation, and the expensive/cross-platform jobs are gated on it;
* the escape hatches that would let a failure pass (`continue-on-error`,
  `|| true`, path filters, an unclassified tier) are absent.

No PyYAML: the repository takes no third-party dependency, and a textual
assertion is exactly what catches a renamed job, a dropped `if:` or a condition
written on the wrong step. Whole-line comments are stripped before the
negative assertions so that a comment *explaining* why an escape hatch is
forbidden does not read as one.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'ci.yml'

REQUIRED_JOB_NAMES = (
    'name: checks (Python ${{ matrix.python-version }})',
    'name: checks-macos (Python ${{ matrix.python-version }})',
    'name: checks-windows (Python ${{ matrix.python-version }})',
)


def workflow_text():
    return WORKFLOW.read_text(encoding='utf-8')


def workflow_lines():
    return workflow_text().splitlines()


def active_text():
    """The workflow with whole-line comments removed.

    Inline comments survive; none of them mentions a forbidden token, so a
    leftover inline comment would still be caught by the negative assertions.
    """
    return '\n'.join(
        line for line in workflow_lines() if not line.strip().startswith('#'))


def job_block(job_id):
    """The text of one job, from its `  <id>:` line to the next job key."""
    lines = workflow_lines()
    header = f'  {job_id}:'
    start = None
    for index, line in enumerate(lines):
        if line.rstrip() == header:
            start = index
            break
    if start is None:
        raise AssertionError(f'job {job_id!r} is missing from {WORKFLOW.name}')
    block = [lines[start]]
    for line in lines[start + 1:]:
        indented = len(line) - len(line.lstrip())
        if line.strip() and indented <= 2:
            break
        block.append(line)
    return '\n'.join(block)


def step_block(name):
    """The text of the step whose `name:` contains *name*."""
    lines = workflow_lines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().startswith('- name:') and name in line:
            start = index
            break
    if start is None:
        raise AssertionError(f'step {name!r} is missing from {WORKFLOW.name}')
    block = [lines[start]]
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith('- name:'):
            break
        if stripped and (len(line) - len(line.lstrip())) <= 4:
            break
        block.append(line)
    return '\n'.join(block)


class WorkflowFileTests(unittest.TestCase):
    def test_workflow_exists(self):
        self.assertTrue(WORKFLOW.is_file(), f'{WORKFLOW} is missing')

    def test_triggers_are_unchanged(self):
        text = active_text()
        for trigger in ('pull_request:', 'push:', 'workflow_dispatch:'):
            with self.subTest(trigger=trigger):
                self.assertIn(trigger, text)

    def test_triggers_have_no_path_filters(self):
        lines = workflow_lines()
        start = lines.index('on:')
        end = lines.index('permissions:')
        trigger_block = '\n'.join(lines[start:end])
        self.assertNotIn('paths', trigger_block)
        self.assertIn('branches: [main]', trigger_block)

    def test_concurrency_and_encoding_env_are_kept(self):
        text = workflow_text()
        self.assertIn('concurrency:', text)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", text)
        self.assertIn('PYTHONUTF8: "1"', text)
        self.assertIn('PYTHONIOENCODING: "utf-8"', text)
        self.assertIn('permissions:\n  contents: read', text)


class RequiredStatusCheckTests(unittest.TestCase):
    """The ruleset requires these contexts; renaming them blocks every PR."""

    def test_required_job_names_are_unchanged(self):
        lines = [line.strip() for line in workflow_lines()]
        for name in REQUIRED_JOB_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, lines)

    def test_required_checks_job_keeps_its_runner_and_matrix(self):
        block = job_block('checks')
        self.assertIn('runs-on: ubuntu-latest', block)
        self.assertIn('python-version: ["3.12", "3.13"]', block)

    def test_required_checks_job_is_not_gated_on_the_risk_tier(self):
        block = job_block('checks')
        self.assertIn('needs: [risk]', block)
        # A tier condition here would let the required context disappear; the
        # tier is only allowed to select steps.
        self.assertNotIn('needs.risk.outputs.full', block.split('steps:')[0])
        self.assertNotIn('needs.risk.outputs.level', block.split('steps:')[0])


class RiskJobTests(unittest.TestCase):
    def test_risk_job_exists_and_reads_full_history(self):
        block = job_block('risk')
        self.assertIn('runs-on: ubuntu-latest', block)
        self.assertIn('fetch-depth: 0', block)

    def test_risk_job_runs_the_classifier_on_the_run_range(self):
        block = job_block('risk')
        self.assertIn('scripts/delivery_risk.py', block)
        self.assertIn('--repo .', block)
        self.assertIn('--base "$base"', block)
        self.assertIn('--head "$head"', block)
        self.assertIn('--json', block)

    def test_risk_job_takes_its_range_from_the_event_payload(self):
        block = job_block('risk')
        # pull_request: base/head SHAs. push: before/sha, with the all-zeros
        # "no before" case falling back to HEAD^.
        self.assertIn('PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}', block)
        self.assertIn('PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}', block)
        self.assertIn('PUSH_BEFORE_SHA: ${{ github.event.before }}', block)
        self.assertIn('RUN_HEAD_SHA: ${{ github.sha }}', block)
        self.assertIn('0000000000000000000000000000000000000000', block)
        self.assertIn('"${head}^"', block)
        self.assertIn('workflow_dispatch', block)

    def test_risk_job_exposes_level_and_full_outputs(self):
        block = job_block('risk')
        self.assertIn('level: ${{ steps.classify.outputs.level }}', block)
        self.assertIn('full: ${{ steps.classify.outputs.full }}', block)
        self.assertIn('classified: ${{ steps.classify.outputs.classified }}', block)

    def test_no_caller_supplied_risk_label_is_accepted(self):
        # A label is editable after the fact, so it cannot select the tier.
        text = active_text()
        self.assertNotIn('labels', text)
        self.assertNotIn('github.event.pull_request.title', text)
        self.assertNotIn('--risk', text)

    def test_full_is_forced_for_non_pull_request_runs(self):
        block = job_block('risk')
        self.assertIn('full="true"', block)
        # push to main, workflow_dispatch and an unclassified range all force
        # full validation; only a classified pull_request run may skip it.
        self.assertIn(
            'if [ "$classified" != "true" ] || [ "$event" != "pull_request" ]; then',
            block)

    def test_only_l0_and_l1_can_skip_release_packaging(self):
        # `full` is recomputed from the tier, so an L2/L3 report that claimed
        # `full_validation: false` cannot skip the packaging self-check.
        block = job_block('risk')
        self.assertIn('case "$level" in', block)
        self.assertIn('L0|L1) ;;', block)
        self.assertIn('*) full="true" ;;', block)

    def test_classification_defaults_fail_closed(self):
        block = job_block('risk')
        # The defaults are L2 + full + not classified: an unreported tier never
        # reads as L0.
        self.assertIn('level="L2"', block)
        self.assertIn('classified="false"', block)
        self.assertIn('::warning::', block)

    def test_classification_failure_reddens_the_required_context(self):
        block = step_block('Fail closed when delivery risk could not be classified')
        self.assertIn("if: needs.risk.outputs.classified != 'true'", block)
        self.assertIn('exit 1', block)


class CheckLayeringTests(unittest.TestCase):
    def test_checks_depends_on_risk(self):
        self.assertIn('needs: [risk]', job_block('checks'))

    def test_required_checks_always_report_a_conclusion(self):
        # `!cancelled()` (not a success() gate) is what keeps the required
        # contexts reporting a real result even when the classifier job failed.
        self.assertIn('if: ${{ !cancelled() }}', job_block('checks'))

    def test_always_steps_stay_unconditional(self):
        for name, needle in (
            ('Validate kit structure', 'scripts/validate_kit.py'),
            ('Whitespace check', 'git diff --check'),
            ('Architecture contract check', 'scripts/architecture/check.py --project . --json'),
            ('Syntax check', 'scripts/check_drift.py'),
        ):
            with self.subTest(step=name):
                block = step_block(name)
                self.assertIn(needle, block)
                self.assertNotIn('if:', block)

    def test_syntax_check_covers_the_shipped_delivery_scripts(self):
        # The batch delivery code is shipped and executed by CI itself: the risk
        # job runs scripts/delivery_risk.py, and scripts/batch_delivery.py is
        # the batch coordinator. Both must be in the explicit py_compile list,
        # because neither lives in a wildcard directory that the step expands.
        syntax = step_block('Syntax check')
        for path in ('scripts/delivery_risk.py', 'scripts/batch_delivery.py'):
            with self.subTest(path=path):
                self.assertIn(path, syntax)

    def test_whitespace_check_cannot_pass_on_a_missing_base(self):
        block = step_block('Whitespace check')
        self.assertIn('needs.risk.outputs.base', block)
        self.assertIn('exit 1', block)
        self.assertIn('git cat-file -e', block)

    def test_l0_runs_only_targeted_structural_tests(self):
        l0 = step_block('Targeted structural tests (risk L0)')
        # Reachable only for a positively classified L0 pull_request: `full` is
        # false exactly there, so a docs-only push to main cannot take it.
        self.assertIn(
            "if: needs.risk.outputs.level == 'L0' && needs.risk.outputs.full == 'false'",
            l0)
        self.assertIn('tests.test_validate_kit', l0)
        # The full suite must not be reachable from the L0 step.
        self.assertNotIn('discover -s tests', l0)

    def test_l0_skips_the_full_suite_scenarios_metrics_and_packaging(self):
        # The full-validation steps run for every tier except `L0` with
        # `full=false`. The `full == 'true'` arm keeps them running on a push to
        # main or a manual run of a docs-only tree, where the classifier still
        # reports L0 but the merged tree must be proven.
        full_condition = "if: needs.risk.outputs.full == 'true' || needs.risk.outputs.level != 'L0'"
        for name in (
            'Run unit tests',
            'Synthetic delivery scenarios',
            'Delivery metrics (gold sample)',
        ):
            with self.subTest(step=name):
                self.assertIn(full_condition, step_block(name))
        packaging = step_block('Release self-check')
        self.assertIn("if: needs.risk.outputs.full != 'false'", packaging)
        self.assertNotIn("level == 'L0'", packaging)

    def test_full_true_always_runs_the_whole_suite(self):
        # A tier the classifier did not vouch for (or a push/manual run) forces
        # `full=true`; nothing may then reduce the validation that runs.
        for name in (
            'Run unit tests',
            'Synthetic delivery scenarios',
            'Delivery metrics (gold sample)',
        ):
            with self.subTest(step=name):
                self.assertIn("needs.risk.outputs.full == 'true'", step_block(name))
        self.assertIn(
            'if: needs.risk.outputs.full != \'false\'', step_block('Release self-check'))

    def test_l1_runs_the_full_suite_without_packaging(self):
        self.assertIn('discover -s tests', step_block('Run unit tests'))
        self.assertIn(
            'scripts/run_delivery_scenarios.py', step_block('Synthetic delivery scenarios'))
        self.assertIn(
            'scripts/report_metrics.py --gold-sample',
            step_block('Delivery metrics'))
        self.assertIn(
            "if: needs.risk.outputs.full != 'false'",
            step_block('Release self-check'))

    def test_full_tier_runs_packaging_on_both_required_jobs(self):
        # The release self-check is inside the shared step list, so it runs on
        # both matrix entries of `checks` (and on the gated cross-platform jobs).
        self.assertIn('steps: &checks-steps', job_block('checks'))
        self.assertIn('steps: *checks-steps', job_block('checks-macos'))
        self.assertIn('steps: *checks-steps', job_block('checks-windows'))
        self.assertIn(
            'uv run python scripts/build_release.py',
            step_block('Release self-check'))

    def test_cross_platform_jobs_are_gated_by_full(self):
        for job_id in ('checks-macos', 'checks-windows'):
            with self.subTest(job=job_id):
                block = job_block(job_id)
                self.assertIn('needs: [risk]', block)
                self.assertIn("if: needs.risk.outputs.full == 'true'", block)
        self.assertIn('runs-on: macos-latest', job_block('checks-macos'))
        self.assertIn('runs-on: windows-latest', job_block('checks-windows'))

    def test_uv_is_still_the_runner_for_python(self):
        text = active_text()
        self.assertIn('astral-sh/setup-uv@v6', text)
        for command in (
            'uv run python scripts/validate_kit.py',
            'uv run python -m unittest discover -s tests',
            'uv run python scripts/run_delivery_scenarios.py',
            'uv run python scripts/report_metrics.py',
            'uv run python scripts/build_release.py',
        ):
            with self.subTest(command=command):
                self.assertIn(command, text)


class TierPartitionTests(unittest.TestCase):
    """The tier conditions must partition the run: no gap, no overlap.

    Every tier/`full` combination selects exactly one of the two test paths, so
    no run can end up with neither (a silently unvalidated tree) or with both
    (paying for everything while claiming L0).
    """

    CLAUSE = re.compile(
        r"^needs\.risk\.outputs\.(level|full)\s*(==|!=)\s*'([^']*)'$")

    def evaluate(self, expression, level, full):
        values = {'level': level, 'full': full}
        for disjunct in expression.split(' || '):
            outcomes = []
            for clause in disjunct.split(' && '):
                match = self.CLAUSE.match(clause.strip())
                self.assertIsNotNone(match, f'unexpected condition clause: {clause!r}')
                name, operator, expected = match.groups()
                outcomes.append(
                    (values[name] == expected) if operator == '==' else (values[name] != expected))
            if all(outcomes):
                return True
        return False

    def condition_of(self, step):
        for line in step.splitlines():
            stripped = line.strip()
            if stripped.startswith('if: '):
                return stripped[len('if: '):]
        raise AssertionError(f'step has no `if:` condition: {step.splitlines()[0]!r}')

    def test_exactly_one_test_path_runs_per_tier(self):
        targeted = self.condition_of(step_block('Targeted structural tests (risk L0)'))
        suite = self.condition_of(step_block('Run unit tests'))
        for level in ('L0', 'L1', 'L2', 'L3'):
            for full in ('true', 'false'):
                with self.subTest(level=level, full=full):
                    runs_targeted = self.evaluate(targeted, level, full)
                    runs_suite = self.evaluate(suite, level, full)
                    self.assertNotEqual(
                        runs_targeted, runs_suite,
                        'exactly one of the targeted/full test paths must run')
                    # The docs-only shortcut exists only for a classified
                    # L0 pull_request (`full=false`).
                    self.assertEqual(
                        runs_targeted, level == 'L0' and full == 'false')

    def test_packaging_follows_full_validation(self):
        packaging = self.condition_of(step_block('Release self-check'))
        for level in ('L0', 'L1', 'L2', 'L3'):
            for full in ('true', 'false'):
                with self.subTest(level=level, full=full):
                    # Packaging is gated on `full` alone. The risk job forces
                    # `full=true` for every tier outside L0/L1, so an L2/L3
                    # report cannot skip it; that override is pinned in
                    # RiskJobTests.
                    self.assertEqual(
                        self.evaluate(packaging, level, full),
                        full != 'false')


class NoEscapeHatchTests(unittest.TestCase):
    def test_no_continue_on_error(self):
        self.assertNotIn('continue-on-error', active_text())

    def test_no_shell_true_fallback(self):
        self.assertNotIn('|| true', active_text())

    def test_no_path_filters(self):
        # `paths` (and `paths-ignore`) under `on:` would let a change skip CI
        # entirely; the trigger block is asserted separately above. The bare
        # `paths-ignore` token is also banned anywhere else.
        self.assertNotIn('paths-ignore', active_text())

    def test_no_skipped_required_job(self):
        for job_id in ('checks', 'checks-macos', 'checks-windows'):
            with self.subTest(job=job_id):
                block = job_block(job_id)
                self.assertNotIn('if: false', block)
                self.assertNotIn('if: ${{ false }}', block)


if __name__ == '__main__':
    unittest.main()
