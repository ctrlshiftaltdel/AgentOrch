"""Reviewer independence needs a trusted underlying model identity (#170).

Design contract: ``docs/design/agent-role-discovery-and-model-selection.md``
§3.3 and §7.1. A model-id difference and a model-family self-report are
*claims*; they become independence evidence only when the current scope's agent
registry carries a verified canonical identity declaration for both sides.

Every fixture is synthetic: no real registry, no model API, no network and no
real ``.local`` directory.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent_selection import (  # noqa: E402
    discovery,
    local_store,
    roles,
    runtime_adapter,
)
from tests.agent_selection import support  # noqa: E402

NOW = support.NOW


def masked_candidates(model_family_reviewer='family-asserted-different'):
    """One Execution and one Reviewer that disagree only in their own claims."""
    master, execution, _ = support.standard_candidates()
    reviewer = support.candidate(
        'review-masked', model_id='provider/model-alias-2',
        model_family=model_family_reviewer, modalities=('text',),
        tools=('file-read',), permission=support.permissions(read_only=True),
        role_evidence=(support.evidence('reviewer', 'defect-detection'),
                       support.evidence('reviewer', 'test-design', 'MODERATE',
                                        sample=4),
                       support.evidence('reviewer', 'security-review',
                                        'MODERATE', sample=3)))
    execution = support.candidate(
        'exec-masked', model_id='provider/model-alias-1',
        model_family='family-asserted-one', modalities=('text',),
        tools=('file-read', 'file-write', 'command'),
        permission=support.permissions(write_paths=('src',),
                                       commands=('python',)),
        role_evidence=(support.evidence('execution', 'coding'),
                       support.evidence('execution', 'delivery-efficiency',
                                        'MODERATE', sample=4),
                       support.evidence('execution', 'tool-use', 'MODERATE',
                                        sample=3)))
    return master, execution, reviewer


def independence_of(selection):
    return next(item for item in selection.selected.filters
                if item.filter_id == 'independence')


class MaskedIdentityTests(unittest.TestCase):
    """AC-1 / AC-3: a different self-declared model id and family is not proof."""

    def test_masked_same_model_does_not_pass_independence(self):
        report = support.report_with(masked_candidates())
        selection = report.role(roles.ROLE_REVIEWER)
        # The masked candidate must not be selected on a self-reported identity.
        self.assertIsNone(selection.selected)
        self.assertFalse(selection.auto_allowed)
        self.assertFalse(report.automatic_dispatch_allowed)
        # The selector never claimed to run anything.
        self.assertFalse(report.executed)
        independence = [
            item for evaluation in selection.unverified + selection.excluded
            for item in evaluation.filters if item.filter_id == 'independence'
        ]
        self.assertTrue(independence, 'independence was not evaluated at all')
        self.assertNotEqual(independence[0].status, 'PASS', independence[0].detail)
        self.assertIn('canonical', independence[0].detail)

    def test_masked_same_model_is_reported_as_an_explained_degradation(self):
        report = support.report_with(masked_candidates())
        self.assertIn('Reviewer', ' '.join(report.degradations))
        self.assertIn('executed=False', ' '.join(report.render_lines()))


if __name__ == '__main__':
    unittest.main()
