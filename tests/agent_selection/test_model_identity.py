"""Reviewer independence needs a trusted underlying model identity (#170).

Design contract: ``docs/design/agent-role-discovery-and-model-selection.md``
§3.3 and §7.1. A model-id difference and a model-family self-report are
*claims*; they become independence evidence only when the current scope's agent
registry carries a confirmed canonical identity declaration for both sides
(``canonical_model_id``, ``identity_verification=CONFIRMED``,
``identity_verified_until``, ``identity_evidence``).

Every fixture is synthetic: no real registry, no model API, no network and no
real ``.local`` directory.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.agent_selection import (  # noqa: E402
    discovery,
    roles,
    runtime_adapter,
)
from tests.agent_selection import support  # noqa: E402

NOW = support.NOW
FUTURE = support.VERIFIED_UNTIL
# Distinguishes "use the default Execution identity" from an explicit ``None``.
DEFAULT = object()


# --------------------------------------------------------------------------- #
# Candidates: the masked pair from the baseline reproduction
# --------------------------------------------------------------------------- #
def masked_candidates(model_family_reviewer='family-asserted-different'):
    """One Execution and one Reviewer that disagree only in their own claims.

    Neither side has a confirmed canonical identity mapping, which is exactly
    the baseline situation: the operator never registered one.
    """
    master, _, _ = support.standard_candidates()
    reviewer = support.candidate(
        'review-masked', model_id='provider/model-alias-2',
        model_family=model_family_reviewer, modalities=('text',),
        tools=('file-read',), permission=support.permissions(read_only=True),
        canonical_identity=None,
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
        canonical_identity=None,
        role_evidence=(support.evidence('execution', 'coding'),
                       support.evidence('execution', 'delivery-efficiency',
                                        'MODERATE', sample=4),
                       support.evidence('execution', 'tool-use', 'MODERATE',
                                        sample=3)))
    return master, execution, reviewer


def pair(*, execution_identity, reviewer_identity,
         execution_model_id='provider/model-alias-1',
         reviewer_model_id='provider/model-alias-2',
         execution_family='family-asserted-one',
         reviewer_family='family-asserted-different'):
    """A Master, an Execution and a Reviewer whose identities are explicit."""
    master, _, _ = support.standard_candidates()
    execution = support.candidate(
        'exec-1', model_id=execution_model_id, model_family=execution_family,
        canonical_identity=execution_identity,
        role_evidence=(support.evidence('execution', 'coding'),
                       support.evidence('execution', 'delivery-efficiency',
                                        'MODERATE', sample=4),
                       support.evidence('execution', 'tool-use', 'MODERATE',
                                        sample=3)))
    reviewer = support.candidate(
        'review-1', model_id=reviewer_model_id, model_family=reviewer_family,
        modalities=('text',), tools=('file-read',),
        permission=support.permissions(read_only=True),
        canonical_identity=reviewer_identity,
        role_evidence=(support.evidence('reviewer', 'defect-detection'),
                       support.evidence('reviewer', 'test-design', 'MODERATE',
                                        sample=4),
                       support.evidence('reviewer', 'security-review',
                                        'MODERATE', sample=3)))
    return master, execution, reviewer


def untrusted_pair():
    """The three roles exactly as a runtime enumeration reports them."""
    master, execution, reviewer = pair(execution_identity=None,
                                       reviewer_identity=None)
    master = support.candidate(
        'master-1', model_id='model-master', model_family='family-master',
        modalities=('text', 'image'), tools=('file-read',),
        permission=support.permissions(), capability_record_value=None,
        context_tokens=400000, canonical_identity=None,
        role_evidence=(support.evidence('master', 'planning'),
                       support.evidence('master', 'quality-judgement')))
    return master, execution, reviewer


def independence_of(evaluation):
    return next(item for item in evaluation.filters
                if item.filter_id == 'independence')


def evaluations(selection):
    result = list(selection.unverified) + list(selection.excluded)
    if selection.selected is not None:
        result.append(selection.selected)
    return result


def independence_for(selection, agent_id):
    """The independence filter of one named candidate in a selection."""
    for evaluation in evaluations(selection):
        if evaluation.candidate.agent_id == agent_id:
            return next(item for item in evaluation.filters
                        if item.filter_id == 'independence')
    raise AssertionError(f'{agent_id} was not evaluated for {selection.role}')


# --------------------------------------------------------------------------- #
# AC-1 / AC-3: a self-reported identity is not independence evidence
# --------------------------------------------------------------------------- #
class MaskedIdentityTests(unittest.TestCase):
    def test_masked_same_model_does_not_pass_independence(self):
        report = support.report_with(masked_candidates())
        selection = report.role(roles.ROLE_REVIEWER)
        # The masked candidate must not be selected on a self-reported identity.
        self.assertIsNone(selection.selected)
        self.assertFalse(selection.auto_allowed)
        self.assertFalse(report.automatic_dispatch_allowed)
        # The selector never claimed to run anything.
        self.assertFalse(report.executed)
        independence = independence_for(selection, 'review-masked')
        self.assertNotEqual(independence.status, 'PASS', independence.detail)
        self.assertIn('canonical', independence.detail)

    def test_masked_same_model_is_reported_as_an_explained_degradation(self):
        report = support.report_with(masked_candidates())
        self.assertIn('Reviewer', ' '.join(report.degradations))
        self.assertIn('executed=False', ' '.join(report.render_lines()))

    def test_a_masked_candidate_cannot_be_promoted_by_its_family_claim(self):
        for family in ('family-asserted-different', 'family-asserted-one',
                       None):
            with self.subTest(family=family):
                report = support.report_with(masked_candidates(family))
                self.assertIsNone(report.role(roles.ROLE_REVIEWER).selected)
                self.assertFalse(report.automatic_dispatch_allowed)


# --------------------------------------------------------------------------- #
# AC-4: an explicit alias of the same canonical identity must be refused
# --------------------------------------------------------------------------- #
class AliasTests(unittest.TestCase):
    def test_alias_of_the_execution_identity_is_refused(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/core-x'),
            reviewer_identity=support.identity('Vendor/Core-X'))
        report = support.report_with((master, execution, reviewer))
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        independence = independence_for(selection, 'review-1')
        self.assertEqual(independence.status, 'FAIL')
        self.assertIn('same as the selected Execution model', independence.detail)
        self.assertIn('canonical', independence.detail)
        self.assertFalse(selection.auto_allowed)
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_alias_declared_on_the_execution_record_is_refused(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity(
                'vendor/core-x', aliases=('provider/model-alias-2',)),
            reviewer_identity=support.identity('vendor/core-x'))
        report = support.report_with((master, execution, reviewer))
        self.assertIsNone(report.role(roles.ROLE_REVIEWER).selected)
        independence = independence_for(report.role(roles.ROLE_REVIEWER),
                                        'review-1')
        self.assertNotEqual(independence.status, 'PASS')
        self.assertEqual(independence.status, 'FAIL')

    def test_same_model_id_is_still_refused(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/core-x'),
            reviewer_identity=support.identity('vendor/core-x'),
            reviewer_model_id='provider/model-alias-1')
        report = support.report_with((master, execution, reviewer))
        independence = independence_for(report.role(roles.ROLE_REVIEWER),
                                        'review-1')
        self.assertEqual(independence.status, 'FAIL')
        self.assertIn('is the same as the selected Execution model',
                      independence.detail)


# --------------------------------------------------------------------------- #
# AC-6: two confirmed, different canonical identities stay independent
# --------------------------------------------------------------------------- #
class DistinctIdentityTests(unittest.TestCase):
    def test_two_confirmed_distinct_identities_pass_at_model_level(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/core-x'),
            reviewer_identity=support.identity('vendor/core-y'))
        report = support.report_with((master, execution, reviewer))
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNotNone(selection.selected)
        independence = independence_of(selection.selected)
        self.assertEqual(independence.status, 'PASS')
        self.assertIn('model-level independence established', independence.detail)
        self.assertIn('vendor/core-y', independence.detail)
        self.assertIn('vendor/core-x', independence.detail)
        self.assertTrue(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)

    def test_unknown_family_is_not_reported_as_an_observed_difference(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/core-x'),
            reviewer_identity=support.identity('vendor/core-y'),
            execution_family='family-a', reviewer_family=None)
        report = support.report_with((master, execution, reviewer))
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNotNone(selection.selected)
        independence = independence_of(selection.selected)
        self.assertEqual(independence.status, 'PASS')
        self.assertIn('family UNKNOWN', independence.detail)
        self.assertNotIn('different model family', independence.detail)

    def test_a_family_self_report_is_reported_but_not_claimed(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/core-x'),
            reviewer_identity=support.identity('vendor/core-y'))
        report = support.report_with((master, execution, reviewer))
        independence = independence_of(report.role(roles.ROLE_REVIEWER).selected)
        self.assertIn('self-declared', independence.detail)
        self.assertIn('not used as independence evidence', independence.detail)

    def test_a_shared_family_does_not_block_model_level_independence(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/core-x'),
            reviewer_identity=support.identity('vendor/core-y'),
            execution_family='family-shared', reviewer_family='family-shared')
        report = support.report_with((master, execution, reviewer))
        independence = independence_of(report.role(roles.ROLE_REVIEWER).selected)
        self.assertEqual(independence.status, 'PASS')
        self.assertIn('same model family', independence.detail)
        self.assertIn('only model-level independence is established',
                      independence.detail)


# --------------------------------------------------------------------------- #
# AC-5: missing / unconfirmed / conflicting / expired / invalid => UNKNOWN
# --------------------------------------------------------------------------- #
class UntrustedIdentityTests(unittest.TestCase):
    def assert_untrusted(self, reviewer_identity, expected_status,
                         execution_identity=DEFAULT):
        master, execution, reviewer = pair(
            execution_identity=(support.identity('vendor/core-x')
                                if execution_identity is DEFAULT
                                else execution_identity),
            reviewer_identity=reviewer_identity)
        report = support.report_with((master, execution, reviewer))
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        self.assertFalse(selection.auto_allowed)
        self.assertFalse(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)
        independence = independence_for(selection, 'review-1')
        self.assertEqual(independence.status, 'UNKNOWN')
        self.assertIn(expected_status, independence.detail)
        self.assertIn('automatic dispatch stays disabled', independence.detail)
        return independence

    def test_missing_mapping_is_unknown(self):
        self.assert_untrusted(None, 'ABSENT')

    def test_empty_declaration_is_unknown(self):
        self.assert_untrusted(support.absent_identity(), 'ABSENT')

    def test_unconfirmed_mapping_is_unknown(self):
        self.assert_untrusted(
            support.identity('vendor/core-y', status='UNCONFIRMED'),
            'UNCONFIRMED')

    def test_conflicting_mapping_is_unknown(self):
        identity = runtime_adapter.CanonicalIdentity(
            canonical_id='vendor/core-y', status='CONFLICT',
            detail='two records disagree about this alias')
        self.assert_untrusted(identity, 'CONFLICT')

    def test_expired_verification_is_unknown(self):
        identity = support.identity('vendor/core-y',
                                    verified_until=support.EXPIRED_UNTIL)
        independence = self.assert_untrusted(identity, 'EXPIRED')
        self.assertIn(support.EXPIRED_UNTIL, independence.detail)

    def test_verification_is_valid_until_its_own_deadline(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/core-x'),
            reviewer_identity=support.identity('vendor/core-y',
                                               verified_until=NOW))
        report = support.report_with((master, execution, reviewer))
        self.assertIsNotNone(report.role(roles.ROLE_REVIEWER).selected)

    def test_untrusted_execution_identity_blocks_the_reviewer(self):
        self.assert_untrusted(
            support.identity('vendor/core-y'), 'ABSENT',
            execution_identity=None)

    def test_the_caller_cannot_declare_the_execution_identity(self):
        """The reference comes from the selected candidate, not the caller."""
        master, execution, reviewer = pair(
            execution_identity=None,
            reviewer_identity=support.identity('vendor/core-y'))
        requirements = support.task_requirements()
        requirements[roles.ROLE_REVIEWER] = roles.reviewer_requirement(
            execution_model_id='provider/model-alias-1',
            execution_model_family='family-asserted-one',
            execution_canonical_identity=support.identity('vendor/core-x'),
            read_paths=('src',))
        report = support.report_with((master, execution, reviewer),
                                     requirements=requirements)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        self.assertIn('ABSENT',
                      independence_for(selection, 'review-1').detail)
        self.assertFalse(report.automatic_dispatch_allowed)


# --------------------------------------------------------------------------- #
# AC-2 / AC-7: registry schema, verification source, compatibility
# --------------------------------------------------------------------------- #
class Record:
    """The parts of a Markdown record the registry parser reads."""

    def __init__(self, details, *, record_id='agent-1', status='RECORDED'):
        self.record_id = record_id
        self.status = status
        self.details = dict(details)


def registry_details(**overrides):
    details = {
        'runtime_id': 'fixture-runtime', 'runtime_version': '1.0.0',
        'agent_id': 'exec-1', 'model_id': 'model-a',
        'model_family': 'family-a', 'modalities': 'text',
        'tools': 'file-read,file-write', 'read_paths': 'src',
        'write_paths': 'src', 'commands': 'python',
        'capability_ref': 'cap-1',
        'evidence_time': NOW, 'source_class': 'OBSERVED',
        'canonical_model_id': 'vendor/core-x',
        'model_aliases': 'provider/alias-1,provider/alias-2',
        'identity_verification': 'CONFIRMED',
        'identity_verified_until': FUTURE,
        'identity_evidence': 'operator:model-identity-check',
    }
    details.update(overrides)
    return {key: value for key, value in details.items() if value != ''}


def capabilities():
    return {'cap-1': support.capability_record()}


def parse(details, **kwargs):
    entries, issues = discovery.registry_candidates_from_records(
        [Record(details, **kwargs)], capability_records=capabilities())
    return entries, issues


class RegistryIdentitySchemaTests(unittest.TestCase):
    def test_confirmed_mapping_is_parsed_with_aliases_and_evidence(self):
        entries, issues = parse(registry_details())
        self.assertEqual(issues, ())
        identity = entries[0].canonical_identity
        self.assertEqual(identity.status, 'CONFIRMED')
        self.assertEqual(identity.canonical_id, 'vendor/core-x')
        self.assertEqual(identity.aliases,
                         ('provider/alias-1', 'provider/alias-2'))
        self.assertEqual(identity.evidence_ref,
                         'operator:model-identity-check')
        self.assertEqual(identity.verified_until, FUTURE)
        trusted, status, detail = identity.trusted_at(NOW)
        self.assertTrue(trusted, detail)
        self.assertEqual(status, 'CONFIRMED')

    def test_identity_keys_are_supported_registry_fields(self):
        for key in discovery.IDENTITY_DETAIL_KEYS:
            self.assertIn(key, discovery.REGISTRY_DETAIL_KEYS)

    def test_old_registry_without_identity_stays_readable_but_untrusted(self):
        details = registry_details()
        for key in discovery.IDENTITY_DETAIL_KEYS:
            details.pop(key, None)
        entries, issues = parse(details)
        self.assertEqual(issues, ())
        self.assertEqual(len(entries), 1)
        identity = entries[0].canonical_identity
        self.assertEqual(identity.status, 'ABSENT')
        self.assertIsNone(identity.canonical_id)
        trusted, status, detail = identity.trusted_at(NOW)
        self.assertFalse(trusted)
        self.assertIn('not', detail)

    def test_confirmed_mapping_without_expiry_is_invalid(self):
        details = registry_details(identity_verified_until='')
        entries, _ = parse(details)
        identity = entries[0].canonical_identity
        self.assertEqual(identity.status, 'INVALID')
        self.assertIn('no default validity window is invented', identity.detail)
        self.assertFalse(identity.trusted_at(NOW)[0])

    def test_confirmed_mapping_without_evidence_is_invalid(self):
        entries, _ = parse(registry_details(identity_evidence=''))
        identity = entries[0].canonical_identity
        self.assertEqual(identity.status, 'INVALID')
        self.assertIn('identity_evidence is missing', identity.detail)

    def test_unconfirmed_mapping_is_not_trusted(self):
        entries, _ = parse(registry_details(identity_verification='UNCONFIRMED'))
        identity = entries[0].canonical_identity
        self.assertEqual(identity.status, 'UNCONFIRMED')
        self.assertFalse(identity.trusted_at(NOW)[0])

    def test_unknown_verification_value_is_not_trusted(self):
        entries, _ = parse(registry_details(identity_verification='probably'))
        identity = entries[0].canonical_identity
        self.assertEqual(identity.status, 'UNCONFIRMED')
        self.assertIn('only an operator-confirmed mapping', identity.detail)

    def test_malformed_canonical_id_is_invalid(self):
        entries, _ = parse(registry_details(canonical_model_id='model a'))
        identity = entries[0].canonical_identity
        self.assertEqual(identity.status, 'INVALID')
        self.assertFalse(identity.trusted_at(NOW)[0])

    def test_canonical_id_is_required_when_other_identity_keys_are_present(self):
        details = registry_details(canonical_model_id='')
        entries, _ = parse(details)
        identity = entries[0].canonical_identity
        self.assertEqual(identity.status, 'INVALID')
        self.assertIn('canonical_model_id is required', identity.detail)

    def test_an_identity_declaration_does_not_bypass_the_other_required_keys(self):
        details = registry_details()
        del details['model_id']
        entries, issues = parse(details)
        self.assertEqual(entries, ())
        self.assertIn('missing model_id', ' '.join(issues))


class RegistryConflictTests(unittest.TestCase):
    def test_conflicting_mappings_are_both_refused(self):
        records = [
            Record(registry_details(agent_id='a', model_id='model-a',
                                    canonical_model_id='vendor/core-x',
                                    )),
            Record(registry_details(agent_id='b', model_id='model-b',
                                    canonical_model_id='vendor/core-y',
                                    model_aliases='model-a',
                                    )),
        ]
        entries, issues = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(entry.canonical_identity.status, 'CONFLICT',
                             entry.canonical_identity)
            self.assertFalse(entry.canonical_identity.trusted_at(NOW)[0])
        self.assertTrue(any('conflicting mapping' in issue for issue in issues),
                        issues)

    def test_a_conflict_is_read_from_the_declaration_not_the_candidate(self):
        """An unrelated invalid field must not hide one side of a conflict.

        The second record cannot become a candidate (``read_only`` is not a
        boolean), but it still declares ``model-a`` as a different canonical
        model. The surviving record must therefore lose its CONFIRMED status.
        """
        records = [
            Record(registry_details(agent_id='a', model_id='model-a',
                                    canonical_model_id='vendor/core-x',
                                    model_aliases='')),
            Record(registry_details(agent_id='b', model_id='model-b',
                                    canonical_model_id='vendor/core-y',
                                    model_aliases='model-a',
                                    read_only='maybe')),
        ]
        entries, issues = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        self.assertEqual(len(entries), 1, 'the invalid record must be skipped')
        self.assertIn('read_only', ' '.join(issues))
        self.assertEqual(entries[0].canonical_identity.status, 'CONFLICT',
                         entries[0].canonical_identity)
        self.assertFalse(entries[0].canonical_identity.trusted_at(NOW)[0])
        self.assertTrue(any('conflicting mapping' in issue for issue in issues),
                        issues)

    def test_an_invalid_alias_still_claims_the_canonical_identity(self):
        """A bad alias must not erase the canonical id the record did declare.

        The second record is unusable twice over (``read_only`` is not a boolean,
        and ``model_aliases`` contains a token with a space), but it still says
        that ``model-b`` is ``vendor/core-x``. The surviving record says
        ``model-b`` is ``vendor/core-y``, so the mapping is contradictory and
        neither may stay CONFIRMED.
        """
        records = [
            Record(registry_details(agent_id='review-1', model_id='model-b',
                                    canonical_model_id='vendor/core-y',
                                    model_aliases='', read_only='true',
                                    write_paths='', tools='file-read')),
            Record(registry_details(agent_id='review-1', model_id='model-b',
                                    canonical_model_id='vendor/core-x',
                                    model_aliases='bad alias',
                                    read_only='maybe')),
        ]
        entries, issues = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        self.assertEqual(len(entries), 1, 'the unusable record must be skipped')
        self.assertEqual(entries[0].canonical_identity.canonical_id,
                         'vendor/core-y')
        self.assertEqual(entries[0].canonical_identity.status, 'CONFLICT',
                         entries[0].canonical_identity)
        self.assertFalse(entries[0].canonical_identity.trusted_at(NOW)[0])

    def test_an_unresolvable_canonical_still_claims_the_exact_triple(self):
        """A record that names the same agent counts even if its id is garbage.

        The second record matches on runtime_id + agent_id + model_id and carries
        identity fields, but ``canonical_model_id`` cannot be parsed and
        ``read_only`` is invalid. It is unusable as a candidate and resolves to no
        canonical id — yet the registry still said something about this agent's
        identity, so the surviving record must not stay CONFIRMED.
        """
        records = [
            Record(registry_details(agent_id='review-1', model_id='model-b',
                                    canonical_model_id='vendor/core-y',
                                    model_aliases='', read_only='true',
                                    write_paths='', tools='file-read')),
            Record(registry_details(agent_id='review-1', model_id='model-b',
                                    canonical_model_id='bad canonical',
                                    model_aliases='', read_only='maybe')),
        ]
        entries, issues = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        self.assertEqual(len(entries), 1, 'the unusable record must be skipped')
        self.assertEqual(entries[0].canonical_identity.status, 'CONFLICT',
                         entries[0].canonical_identity)
        self.assertFalse(entries[0].canonical_identity.trusted_at(NOW)[0])
        self.assertTrue(any('model_id' in issue and 'cannot' in issue.lower()
                            or 'unresolvable' in issue.lower()
                            for issue in issues), issues)

    def test_two_records_for_one_exact_triple_with_the_same_identity_agree(self):
        records = [
            Record(registry_details(agent_id='review-1', model_id='model-b',
                                    canonical_model_id='vendor/core-y',
                                    model_aliases='', read_only='true',
                                    write_paths='', tools='file-read')),
            Record(registry_details(agent_id='review-1', model_id='model-b',
                                    canonical_model_id='vendor/core-y',
                                    model_aliases='', read_only='maybe')),
        ]
        entries, _ = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].canonical_identity.status, 'CONFIRMED')

    def test_an_invalid_alias_alone_is_invalid_but_keeps_the_canonical_id(self):
        entries, _ = parse(registry_details(model_aliases='bad alias'))
        identity = entries[0].canonical_identity
        self.assertEqual(identity.status, 'INVALID')
        self.assertEqual(identity.canonical_id, 'vendor/core-x')
        self.assertFalse(identity.trusted_at(NOW)[0])
        self.assertIn('model_aliases', identity.detail)

    def test_an_invalid_record_alone_does_not_poison_a_valid_mapping(self):
        records = [
            Record(registry_details(agent_id='a', model_id='model-a',
                                    canonical_model_id='vendor/core-x',
                                    model_aliases='')),
            Record(registry_details(agent_id='b', model_id='model-b',
                                    canonical_model_id='vendor/core-y',
                                    model_aliases='', read_only='maybe')),
        ]
        entries, _ = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].canonical_identity.status, 'CONFIRMED')

    def test_the_same_canonical_identity_declared_twice_is_not_a_conflict(self):
        records = [
            Record(registry_details(agent_id='a', model_id='model-a',
                                    )),
            Record(registry_details(agent_id='b', model_id='model-b',
                                    canonical_model_id='vendor/core-x',
                                    model_aliases='')),
        ]
        entries, issues = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        self.assertEqual(issues, ())
        self.assertEqual([entry.canonical_identity.status for entry in entries],
                         ['CONFIRMED', 'CONFIRMED'])

    def test_single_aliases_are_case_insensitive_for_conflicts(self):
        records = [
            Record(registry_details(agent_id='a', model_id='model-a',
                                    canonical_model_id='Vendor/Core-X',
                                    model_aliases='')),
            Record(registry_details(agent_id='b', model_id='vendor/core-x',
                                    canonical_model_id='vendor/core-y',
                                    model_aliases='')),
        ]
        entries, _ = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        self.assertEqual([entry.canonical_identity.status for entry in entries],
                         ['CONFLICT', 'CONFLICT'])


class RegistryToRoleTests(unittest.TestCase):
    """The schema is only useful if discovery's verdict reaches the filter."""

    def master_record(self):
        return Record(registry_details(
            agent_id='master-1', model_id='model-master',
            model_family='family-master', modalities='text,image',
            tools='file-read', read_paths='', write_paths='', commands='',
            capability_ref='', canonical_model_id='vendor/master-model',
            model_aliases='', ))

    def report_from_records(self, records):
        entries, issues = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        self.assertEqual(issues, ())
        discovered = discovery.discover_candidates(
            registry=discovery.ExplicitRegistry(entries=entries))
        return roles.select_roles(discovered, support.task_requirements(),
                                  now=NOW)

    def test_a_registry_alias_group_blocks_a_masked_reviewer(self):
        records = [self.master_record(),
            Record(registry_details(
                agent_id='exec-1', model_id='provider/model-alias-1',
                canonical_model_id='vendor/core-x',
                model_aliases='provider/model-alias-2,'
                              'provider/model-alias-3',
                tools='file-read,file-write,command')),
            Record(registry_details(
                agent_id='review-1', model_id='provider/model-alias-2',
                model_family='family-asserted-different',
                canonical_model_id='vendor/core-x',
                model_aliases='', tools='file-read', write_paths='',
                read_only='true')),
        ]
        report = self.report_from_records(records)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        self.assertFalse(report.automatic_dispatch_allowed)
        self.assertEqual(independence_for(selection, 'review-1').status,
                         'FAIL')

    def test_a_registry_without_identity_cannot_select_a_reviewer(self):
        records = [self.master_record(),
            Record(registry_details(
                agent_id='exec-1', model_id='model-a',
                tools='file-read,file-write,command')),
        ]
        details = registry_details(
            agent_id='review-1', model_id='model-b', model_family='family-b',
            tools='file-read', write_paths='', read_only='true',
            )
        for key in discovery.IDENTITY_DETAIL_KEYS:
            details.pop(key, None)
        records.append(Record(details))
        report = self.report_from_records(records)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        self.assertIn('ABSENT',
                      independence_for(selection, 'review-1').detail)
        self.assertFalse(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)

    def test_a_conflicting_invalid_alias_record_blocks_the_reviewer(self):
        """End to end: the survivor must not be selected on a poisoned mapping."""
        records = [
            self.master_record(),
            Record(registry_details(
                agent_id='exec-1', model_id='model-a',
                canonical_model_id='vendor/core-x', model_aliases='',
                tools='file-read,file-write,command')),
            Record(registry_details(
                agent_id='review-1', model_id='model-b', model_family='family-b',
                canonical_model_id='vendor/core-y', model_aliases='',
                tools='file-read', write_paths='', read_only='true')),
            Record(registry_details(
                agent_id='review-1', model_id='model-b', model_family='family-b',
                canonical_model_id='vendor/core-x', model_aliases='bad alias',
                read_only='maybe')),
        ]
        entries, issues = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        self.assertTrue(any('read_only' in issue for issue in issues), issues)
        discovered = discovery.discover_candidates(
            registry=discovery.ExplicitRegistry(entries=entries))
        report = roles.select_roles(discovered, support.task_requirements(),
                                    now=NOW)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        independence = independence_for(selection, 'review-1')
        self.assertEqual(independence.status, 'UNKNOWN')
        self.assertIn('CONFLICT', independence.detail)
        self.assertFalse(selection.auto_allowed)
        self.assertFalse(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)

    def test_an_unresolvable_canonical_on_the_exact_triple_blocks_reviewer(self):
        """End to end: an unconvertible canonical id on a twin record still counts."""
        records = [
            self.master_record(),
            Record(registry_details(
                agent_id='exec-1', model_id='model-a',
                canonical_model_id='vendor/core-x', model_aliases='',
                tools='file-read,file-write,command')),
            Record(registry_details(
                agent_id='review-1', model_id='model-b', model_family='family-b',
                canonical_model_id='vendor/core-y', model_aliases='',
                tools='file-read', write_paths='', read_only='true')),
            Record(registry_details(
                agent_id='review-1', model_id='model-b', model_family='family-b',
                canonical_model_id='bad canonical', model_aliases='',
                read_only='maybe')),
        ]
        entries, _ = discovery.registry_candidates_from_records(
            records, capability_records=capabilities())
        discovered = discovery.discover_candidates(
            registry=discovery.ExplicitRegistry(entries=entries))
        report = roles.select_roles(discovered, support.task_requirements(),
                                    now=NOW)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        independence = independence_for(selection, 'review-1')
        self.assertEqual(independence.status, 'UNKNOWN')
        self.assertIn('CONFLICT', independence.detail)
        self.assertFalse(selection.auto_allowed)
        self.assertFalse(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)

    def test_two_confirmed_registry_identities_select_the_reviewer(self):
        records = [self.master_record(),
            Record(registry_details(
                agent_id='exec-1', model_id='model-a',
                canonical_model_id='vendor/core-x', model_aliases='',
                tools='file-read,file-write,command')),
            Record(registry_details(
                agent_id='review-1', model_id='model-b', model_family='family-b',
                canonical_model_id='vendor/core-y', model_aliases='',
                tools='file-read', write_paths='', read_only='true',
                )),
        ]
        report = self.report_from_records(records)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNotNone(selection.selected)
        self.assertTrue(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)
        self.assertEqual(
            independence_of(selection.selected).status, 'PASS')


# --------------------------------------------------------------------------- #
# AC-2 / AC-3 integration: the scope registry reaches a runtime enumeration
# --------------------------------------------------------------------------- #
class RuntimeEnrichmentTests(unittest.TestCase):
    """Exact ``runtime_id`` + ``agent_id`` + ``model_id`` matching, nothing else."""

    def discovery(self, candidates, registry_entries=None):
        adapter = support.FixtureAdapter(support.ok_result(candidates))
        registry = (discovery.ExplicitRegistry(entries=tuple(registry_entries))
                    if registry_entries is not None else None)
        return discovery.discover_candidates(adapter=adapter,
                                             registry=registry)

    def registry_entry(self, candidate, *, identity=None, **overrides):
        values = dict(runtime_id=candidate.runtime_id,
                      runtime_version=candidate.runtime_version,
                      agent_id=candidate.agent_id,
                      model_id=candidate.model_id,
                      canonical_identity=(identity if identity is not None
                                          else support.identity(
                                              candidate.model_id)))
        values.update(overrides)
        return support.candidate(source='explicit-registry', **values)

    def report(self, candidates, registry_entries=None):
        return roles.select_roles(
            self.discovery(candidates, registry_entries),
            support.task_requirements(), now=NOW)

    def test_an_exact_registry_match_gives_the_reviewer_a_trusted_identity(self):
        master, execution, reviewer = untrusted_pair()
        entries = [self.registry_entry(execution),
                   self.registry_entry(reviewer)]
        discovered = self.discovery((master, execution, reviewer), entries)
        self.assertEqual(discovered.outcome, discovery.DISCOVERY_RUNTIME)
        by_agent = {item.agent_id: item for item in discovered.candidates}
        self.assertEqual(
            by_agent['review-1'].canonical_identity.canonical_id,
            'provider/model-alias-2')
        self.assertTrue(any('exact' in note and 'agent-registry.md' in note
                            for note in discovered.notes), discovered.notes)
        report = roles.select_roles(discovered, support.task_requirements(),
                                    now=NOW)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNotNone(selection.selected)
        self.assertTrue(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)

    def test_a_different_model_id_is_not_a_match(self):
        master, execution, reviewer = untrusted_pair()
        entries = [self.registry_entry(execution),
                   self.registry_entry(
                       reviewer, model_id='some/other-model',
                       identity=support.identity('some/other-model'))]
        report = self.report((master, execution, reviewer), entries)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        self.assertIn('ABSENT', independence_for(selection, 'review-1').detail)
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_a_different_agent_id_is_not_a_match(self):
        master, execution, reviewer = untrusted_pair()
        entries = [self.registry_entry(execution),
                   self.registry_entry(reviewer, agent_id='review-9')]
        report = self.report((master, execution, reviewer), entries)
        self.assertIsNone(report.role(roles.ROLE_REVIEWER).selected)

    def test_a_different_runtime_is_not_a_match(self):
        master, execution, reviewer = untrusted_pair()
        entries = [self.registry_entry(execution),
                   self.registry_entry(reviewer, runtime_id='other-runtime')]
        report = self.report((master, execution, reviewer), entries)
        self.assertIsNone(report.role(roles.ROLE_REVIEWER).selected)

    def test_a_matching_record_without_an_identity_stays_absent(self):
        master, execution, reviewer = untrusted_pair()
        entries = [self.registry_entry(execution),
                   self.registry_entry(reviewer,
                                       identity=support.absent_identity())]
        report = self.report((master, execution, reviewer), entries)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        self.assertIn('ABSENT', independence_for(selection, 'review-1').detail)

    def test_two_exact_matches_that_disagree_become_a_conflict(self):
        master, execution, reviewer = untrusted_pair()
        entries = [self.registry_entry(execution),
                   self.registry_entry(reviewer),
                   self.registry_entry(
                       reviewer,
                       identity=support.identity('vendor/somewhere-else'))]
        report = self.report((master, execution, reviewer), entries)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        independence = independence_for(selection, 'review-1')
        self.assertEqual(independence.status, 'UNKNOWN')
        self.assertIn('CONFLICT', independence.detail)
        self.assertFalse(selection.auto_allowed)
        self.assertFalse(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)

    def test_a_runtime_supplied_identity_is_discarded_without_a_registry(self):
        # A runtime can enumerate the agent and claim CONFIRMED for itself; with
        # no scope registry declaration that claim must not survive discovery.
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/claimed-exec'),
            reviewer_identity=support.identity('vendor/claimed-review'))
        discovered = discovery.discover_candidates(
            adapter=support.FixtureAdapter(
                support.ok_result((master, execution, reviewer))))
        for candidate in discovered.candidates:
            self.assertIsNone(candidate.canonical_identity, candidate.agent_id)
        self.assertTrue(any('discarded' in note for note in discovered.notes),
                        discovered.notes)
        # The runtime's raw claim stays visible as provenance, not as evidence.
        raw = {item.agent_id: item.canonical_identity
               for item in discovered.runtime_result.candidates}
        self.assertEqual(raw['review-1'].canonical_id, 'vendor/claimed-review')
        report = roles.select_roles(discovered, support.task_requirements(),
                                    now=NOW)
        selection = report.role(roles.ROLE_REVIEWER)
        self.assertIsNone(selection.selected)
        self.assertFalse(selection.auto_allowed)
        self.assertFalse(report.automatic_dispatch_allowed)
        self.assertFalse(report.executed)
        self.assertIn('ABSENT', independence_for(selection, 'review-1').detail)

    def test_a_runtime_claim_does_not_survive_a_registry_miss(self):
        # The registry exists but says nothing about this agent, so the runtime's
        # own claim is still worthless.
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/claimed-exec'),
            reviewer_identity=support.identity('vendor/claimed-review'))
        entries = [self.registry_entry(execution,
                                       identity=support.identity('vendor/other-exec'))]
        discovered = self.discovery((master, execution, reviewer), entries)
        by_agent = {item.agent_id: item for item in discovered.candidates}
        self.assertIsNone(by_agent['review-1'].canonical_identity)
        report = roles.select_roles(discovered, support.task_requirements(),
                                    now=NOW)
        self.assertIsNone(report.role(roles.ROLE_REVIEWER).selected)
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_the_registered_declaration_replaces_the_runtime_claim(self):
        # Same agent, same triple: the registry declaration wins, not the claim.
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/claimed-exec'),
            reviewer_identity=support.identity('vendor/claimed-review'))
        entries = [self.registry_entry(execution,
                                       identity=support.identity('vendor/real-exec')),
                   self.registry_entry(reviewer,
                                       identity=support.identity('vendor/real-review'))]
        discovered = self.discovery((master, execution, reviewer), entries)
        by_agent = {item.agent_id: item for item in discovered.candidates}
        self.assertEqual(by_agent['review-1'].canonical_identity.canonical_id,
                         'vendor/real-review')
        report = roles.select_roles(discovered, support.task_requirements(),
                                    now=NOW)
        self.assertIsNotNone(report.role(roles.ROLE_REVIEWER).selected)
        self.assertTrue(report.automatic_dispatch_allowed)

    def test_no_registry_leaves_every_runtime_candidate_unidentified(self):
        master, execution, reviewer = untrusted_pair()
        discovered = self.discovery((master, execution, reviewer))
        for candidate in discovered.candidates:
            self.assertIsNone(candidate.canonical_identity)

    def test_enrichment_does_not_change_any_other_reported_fact(self):
        master, execution, reviewer = untrusted_pair()
        baseline = self.discovery((master, execution, reviewer))
        enriched = self.discovery((master, execution, reviewer),
                                  [self.registry_entry(reviewer)])
        before = {item.agent_id: item.as_dict()
                  for item in baseline.candidates}
        for candidate in enriched.candidates:
            payload = candidate.as_dict()
            payload.pop('canonical_identity')
            payload.pop('notes')
            original = dict(before[candidate.agent_id])
            original.pop('canonical_identity')
            original.pop('notes')
            self.assertEqual(payload, original, candidate.agent_id)


# --------------------------------------------------------------------------- #
# AC-7: the report explains the decision and stays a report
# --------------------------------------------------------------------------- #
class IdentityReportTests(unittest.TestCase):
    def test_the_report_carries_the_canonical_identities(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/core-x'),
            reviewer_identity=support.identity('vendor/core-y'))
        report = support.report_with((master, execution, reviewer))
        payload = report.as_dict()
        reviewer_payload = payload['selections']['reviewer']
        self.assertEqual(
            reviewer_payload['requirement']['reference_canonical_identity']
            ['canonical_id'], 'vendor/core-x')
        self.assertEqual(
            reviewer_payload['selected']['candidate_id'],
            'fixture-runtime:review-1')
        candidates = payload['discovery']['candidates']
        by_agent = {item['agent_id']: item for item in candidates}
        self.assertEqual(
            by_agent['review-1']['canonical_identity']['canonical_id'],
            'vendor/core-y')

    def test_an_untrusted_report_says_why_and_stays_undispatched(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/core-x'),
            reviewer_identity=None)
        report = support.report_with((master, execution, reviewer))
        lines = '\n'.join(report.render_lines())
        self.assertIn('ABSENT', lines)
        self.assertIn('no independent Reviewer', lines)
        self.assertIn('executed=False', lines)
        self.assertFalse(report.automatic_dispatch_allowed)

    def test_the_selector_never_grants_a_dispatch(self):
        master, execution, reviewer = pair(
            execution_identity=support.identity('vendor/core-x'),
            reviewer_identity=support.identity('vendor/core-y'))
        report = support.report_with((master, execution, reviewer))
        self.assertFalse(report.executed)
        self.assertTrue(report.role(roles.ROLE_REVIEWER).selected is not None)
        self.assertNotIn('dispatched', ' '.join(report.render_lines()).lower())


if __name__ == '__main__':
    unittest.main()
