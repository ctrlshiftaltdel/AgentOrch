"""Version normalization and official-source allowlist tests.

The source tests never touch the network: they exercise the pure selection and
parsing functions with synthetic GitHub release payloads.
"""
from __future__ import annotations

import inspect
import unittest

from . import support

sources = support.sources
versions = support.versions


class VersionTests(unittest.TestCase):
    def test_two_part_tag_is_accepted_and_patch_defaults_to_zero(self):
        self.assertEqual(versions.parse_tag('v1.6'), (1, 6, 0))
        self.assertEqual(versions.parse_tag('v1.6.2'), (1, 6, 2))
        self.assertEqual(versions.normalize_version('v1.6'), '1.6.0')
        self.assertEqual(versions.normalize_version('1.6.2'), '1.6.2')

    def test_rejects_other_tag_shapes(self):
        for tag in ('1.6', 'v1', 'v1.6.0.1', 'v1.6-rc1', 'release-1.6', '', 'v01.6'):
            with self.subTest(tag=tag):
                self.assertIsNone(versions.parse_tag(tag))

    def test_version_order(self):
        self.assertLess(versions.version_key('1.5'), versions.version_key('1.6'))
        self.assertEqual(versions.version_key('v1.6'), versions.version_key('1.6.0'))
        self.assertLess(versions.version_key('1.6'), versions.version_key('1.6.1'))

    def test_tag_matches_manifest_format(self):
        self.assertTrue(versions.tag_matches_format('v1.6', '1.6'))
        self.assertTrue(versions.tag_matches_format('v1.6.3', '1.6'))
        self.assertFalse(versions.tag_matches_format('v1.7', '1.6'))
        self.assertFalse(versions.tag_matches_format('nope', '1.6'))

    def test_legacy_helper_still_rejects_a_minor_that_keeps_the_format(self):
        # The published v1.7.0 client runs exactly this function before install;
        # it must keep failing closed for v1.8.0 + Format 1.7.
        self.assertFalse(versions.tag_matches_format('v1.8.0', '1.7'))

    def test_legacy_helper_keeps_historical_acceptance(self):
        cases = (
            ('v1.7.0', '1.7'),
            ('v1.7.1', '1.7'),
            ('v1.7', '1.7'),
            ('v1.6', '1.6'),
            ('v1.6.3', '1.6'),
            ('v1.5', '1.5'),
        )
        for tag, format_version in cases:
            with self.subTest(tag=tag, format_version=format_version):
                self.assertTrue(versions.tag_matches_format(tag, format_version))


class FormatCompatibilityTests(unittest.TestCase):
    """The new bridge: an explicit, caller-supplied compatibility matrix."""

    def test_declared_matrix_accepts_minor_without_format_bump(self):
        decision = versions.format_compatibility('v1.8.0', '1.7', ('1.7',))
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.rule, 'declared_supported_format')

    def test_legacy_rule_is_accepted_without_a_matrix(self):
        decision = versions.format_compatibility('v1.7.1', '1.7', None)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.rule, 'tag_matches_format')

    def test_missing_or_empty_matrix_fails_closed(self):
        for matrix in (None, (), [], '1.7'):
            with self.subTest(matrix=matrix):
                decision = versions.format_compatibility('v1.8.0', '1.7', matrix)
                self.assertFalse(decision.accepted)
                self.assertIsNone(decision.rule)

    def test_unknown_target_format_fails_closed(self):
        # A well-formed Format 1.8 is not declared by this updater; the tag no
        # longer encodes it (v1.9.0), so the bridge must reject it.
        decision = versions.format_compatibility('v1.9.0', '1.8', ('1.7',))
        self.assertFalse(decision.accepted)
        # A malformed Format is rejected regardless of tag or matrix.
        for bad_format in ('1', '1.8.0', 'latest', '', None):
            with self.subTest(bad_format=bad_format):
                decision = versions.format_compatibility('v1.9.0', bad_format, ('1.7',))
                self.assertFalse(decision.accepted)

    def test_wrong_tag_fails_closed(self):
        for tag in ('1.8.0', 'v1', 'v1.8.0.1', 'v1.8.0-rc1', 'release-1.8', '', None):
            with self.subTest(tag=tag):
                decision = versions.format_compatibility(tag, '1.7', ('1.7',))
                self.assertFalse(decision.accepted)

    def test_invalid_matrix_entry_fails_closed(self):
        decision = versions.format_compatibility('v1.8.0', '1.7', ('1.7', 'latest'))
        self.assertFalse(decision.accepted)

    def test_undeclared_format_fails_closed(self):
        decision = versions.format_compatibility('v1.8.0', '1.7', ('1.6',))
        self.assertFalse(decision.accepted)


class SourceTests(unittest.TestCase):
    def test_allowlist_constant_is_the_official_repository(self):
        self.assertEqual(sources.OFFICIAL_REPOSITORY, 'ctrlshiftaltdel/AgentOrch')
        # The repository must not be overridable through the production client.
        self.assertNotIn('repository', inspect.signature(sources.GitHubReleaseSource).parameters)

    def test_selects_the_single_zip_asset(self):
        payload = b'zip bytes'
        release = support.release_info('v1.6', payload)
        asset = sources.select_asset(release)
        self.assertEqual(asset.name, 'agentorch-20260101.zip')
        self.assertTrue(asset.digest.startswith('sha256:'))

    def test_rejects_non_official_repository(self):
        release = support.release_info('v1.6', b'x', repository='someone/AgentOrch')
        with self.assertRaises(sources.SourceError) as caught:
            sources.select_asset(release)
        self.assertEqual(caught.exception.check, 'SOURCE_ALLOWLIST')

    def test_rejects_non_github_download_url(self):
        release = support.release_info('v1.6', b'x')
        bad = sources.ReleaseAsset(name=release.assets[0].name, size=1,
                                   digest=release.assets[0].digest,
                                   download_url='https://evil.example/agentorch.zip')
        release = sources.ReleaseInfo(repository=release.repository, tag_name=release.tag_name,
                                      assets=(bad,), html_url=release.html_url)
        with self.assertRaises(sources.SourceError) as caught:
            sources.select_asset(release)
        self.assertEqual(caught.exception.check, 'SOURCE_ALLOWLIST')

    def test_rejects_prerelease_and_draft(self):
        for kwargs in ({'prerelease': True}, {'draft': True}):
            with self.subTest(**kwargs):
                release = support.release_info('v1.6', b'x', **kwargs)
                with self.assertRaises(sources.SourceError) as caught:
                    sources.select_asset(release)
                self.assertEqual(caught.exception.check, 'RELEASE_CHANNEL')

    def test_rejects_zero_or_multiple_zip_assets(self):
        release = support.release_info('v1.6', b'x')
        no_zip = sources.ReleaseInfo(repository=release.repository, tag_name='v1.6', assets=())
        with self.assertRaises(sources.SourceError) as caught:
            sources.select_asset(no_zip)
        self.assertEqual(caught.exception.check, 'SOURCE_ALLOWLIST')

        extra = support.release_info(
            'v1.6', b'x',
            extra_assets=({'name': 'agentorch-20260102.zip', 'size': 1,
                           'digest': 'sha256:' + '0' * 64,
                           'download_url': 'https://github.com/x/y/releases/download/v1.6/b.zip'},))
        with self.assertRaises(sources.SourceError) as caught:
            sources.select_asset(extra)
        self.assertEqual(caught.exception.check, 'SOURCE_ALLOWLIST')

    def test_rejects_asset_name_with_path_or_missing_extension(self):
        release = support.release_info('v1.6', b'x')
        bad_asset = sources.ReleaseAsset(name='nested/agentorch.zip', size=1,
                                         digest=release.assets[0].digest,
                                         download_url=release.assets[0].download_url)
        custom = sources.ReleaseInfo(repository=release.repository, tag_name='v1.6',
                                     assets=(bad_asset,))
        with self.assertRaises(sources.SourceError):
            sources.select_asset(custom)

    def test_rejects_invalid_tag_shape(self):
        release = support.release_info('1.6', b'x')
        with self.assertRaises(sources.SourceError) as caught:
            sources.select_asset(release)
        self.assertEqual(caught.exception.check, 'SOURCE_ALLOWLIST')

    def test_parse_release_payload(self):
        payload = {
            'tag_name': 'v1.6.1',
            'prerelease': False,
            'draft': False,
            'html_url': 'https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.1',
            'assets': [{
                'name': 'agentorch-20260101.zip',
                'size': 1234,
                'digest': 'sha256:' + 'a' * 64,
                'browser_download_url': 'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.6.1/agentorch-20260101.zip',
            }],
        }
        release = sources.parse_release_payload(payload)
        self.assertEqual(release.repository, sources.OFFICIAL_REPOSITORY)
        self.assertEqual(release.tag_name, 'v1.6.1')
        self.assertEqual(release.assets[0].digest, 'sha256:' + 'a' * 64)
        self.assertEqual(release.assets[0].size, 1234)

    def test_download_size_limit_is_enforced(self):
        import io

        self.assertEqual(sources.read_limited(io.BytesIO(b'x' * 10), 10), b'x' * 10)
        with self.assertRaises(sources.SourceError) as caught:
            sources.read_limited(io.BytesIO(b'x' * 11), 10)
        self.assertEqual(caught.exception.check, 'ASSET_SHA256')
        self.assertLess(sources.MAX_ASSET_BYTES, 1 << 30)

    def test_parse_missing_digest_is_kept_as_none(self):
        payload = {
            'tag_name': 'v1.6',
            'html_url': 'https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6',
            'assets': [{'name': 'agentorch-20260101.zip', 'size': 1,
                        'browser_download_url': 'https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.6/agentorch-20260101.zip'}],
        }
        release = sources.parse_release_payload(payload)
        self.assertIsNone(release.assets[0].digest)


def _entry(tag, *, prerelease=False, draft=False):
    return {'tag_name': tag, 'prerelease': prerelease, 'draft': draft}


class StableReleaseListingTests(unittest.TestCase):
    """The read-only enumeration used to discover patch release baselines.

    Every page is synthetic bytes handed to the parser; no case opens a socket.
    """

    def _client(self, pages):
        client = sources.GitHubReleaseSource(token=None, timeout=1.0)
        calls = []

        def page(url):
            calls.append(url)
            return pages[len(calls) - 1]

        client._listing_page = page
        return client, calls

    def test_listing_returns_only_stable_release_tags(self):
        import json
        body = json.dumps([
            _entry('v1.7.0'),
            _entry('v1.6.3'),
            _entry('v1.6.2', prerelease=True),
            _entry('v1.6.1', draft=True),
        ]).encode('utf-8')
        client, calls = self._client([body])
        self.assertEqual(client.list_stable_release_tags(), ('v1.7.0', 'v1.6.3'))
        self.assertEqual(len(calls), 1)
        self.assertIn('/repos/ctrlshiftaltdel/AgentOrch/releases', calls[0])
        self.assertTrue(calls[0].startswith('https://api.github.com/'))

    def test_listing_follows_pages_and_stops_on_a_short_page(self):
        import json
        full = json.dumps([_entry('v1.6.3')] * sources.RELEASE_PAGE_SIZE).encode('utf-8')
        short = json.dumps([_entry('v1.6.2')]).encode('utf-8')
        client, calls = self._client([full, short])
        tags = client.list_stable_release_tags()
        self.assertEqual(tags, ('v1.6.3', 'v1.6.2'))
        self.assertEqual(len(calls), 2)

    def test_listing_refuses_to_return_a_truncated_enumeration(self):
        import json
        full = json.dumps([_entry('v1.6.3')] * sources.RELEASE_PAGE_SIZE).encode('utf-8')
        client, calls = self._client([full] * sources.MAX_RELEASE_PAGES)
        with self.assertRaises(sources.SourceError) as caught:
            client.list_stable_release_tags()
        self.assertEqual(caught.exception.check, 'SOURCE_ALLOWLIST')
        self.assertEqual(len(calls), sources.MAX_RELEASE_PAGES)

    def test_listing_rejects_malformed_payloads(self):
        bodies = (
            b'{',
            b'{}',
            b'null',
            b'"v1.6.3"',
            b'[1]',
            b'[{"prerelease": false, "draft": false}]',
            b'[{"tag_name": 5, "prerelease": false, "draft": false}]',
            b'[{"tag_name": "v1.6.3", "prerelease": "no", "draft": false}]',
            b'\xff\xfe',
        )
        for body in bodies:
            with self.subTest(body=body):
                client, _calls = self._client([body])
                with self.assertRaises(sources.SourceError) as caught:
                    client.list_stable_release_tags()
                self.assertEqual(caught.exception.check, 'SOURCE_ALLOWLIST')

    def test_listing_urls_must_stay_on_the_official_host_and_repository(self):
        for url in (
            'http://api.github.com/repos/ctrlshiftaltdel/AgentOrch/releases',
            'https://evil.example/repos/ctrlshiftaltdel/AgentOrch/releases',
            'https://api.github.com/repos/someone/AgentOrch/releases',
            'https://api.github.com/repos/ctrlshiftaltdel/AgentOrch/tarball',
        ):
            with self.subTest(url=url):
                with self.assertRaises(sources.SourceError) as caught:
                    sources._validate_listing_url(url)
                self.assertEqual(caught.exception.check, 'SOURCE_ALLOWLIST')

    def test_listing_refuses_a_redirect_instead_of_following_it(self):
        with self.assertRaises(sources.SourceError) as caught:
            sources._RefuseRedirects().redirect_request(
                None, None, 302, 'Found', {}, 'https://evil.example/steal')
        self.assertEqual(caught.exception.check, 'SOURCE_ALLOWLIST')

    def test_listing_page_rejects_a_non_official_final_url(self):
        from unittest.mock import patch

        class Response:
            def __init__(self, body, url):
                self._body = body
                self._url = url

            def read(self, size=-1):
                return self._body if size < 0 else self._body[:size]

            def geturl(self):
                return self._url

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class Opener:
            def __init__(self, response):
                self.response = response
                self.calls = 0

            def open(self, request, timeout=None):
                self.calls += 1
                return self.response

        url = ('https://api.github.com/repos/ctrlshiftaltdel/AgentOrch/releases'
               '?per_page=100&page=1')
        client = sources.GitHubReleaseSource(token=None, timeout=1.0)
        opener = Opener(Response(b'[]',
                                 'https://evil.example/repos/ctrlshiftaltdel/'
                                 'AgentOrch/releases'))
        with patch.object(sources.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(sources.SourceError) as caught:
                client._listing_page(url)
        self.assertEqual(caught.exception.check, 'SOURCE_ALLOWLIST')
        self.assertEqual(opener.calls, 1)

    def test_listing_page_never_requests_a_non_official_url(self):
        from unittest.mock import patch

        client = sources.GitHubReleaseSource(token=None, timeout=1.0)
        with patch.object(sources.urllib.request, 'build_opener',
                          side_effect=AssertionError('no request may be made')):
            with self.assertRaises(sources.SourceError):
                client._listing_page('https://evil.example/repos/ctrlshiftaltdel/'
                                     'AgentOrch/releases')

    def test_transport_failure_is_not_swallowed(self):
        client = sources.GitHubReleaseSource(token=None, timeout=1.0)

        def page(_url):
            raise OSError('synthetic transport failure')

        client._listing_page = page
        with self.assertRaises(OSError):
            client.list_stable_release_tags()


if __name__ == '__main__':
    unittest.main()
