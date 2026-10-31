"""Version and release-tag normalization.

Frozen rule (docs/self-improvement/update-safety.md §2): a release tag is
``v<major>.<minor>`` or ``v<major>.<minor>.<patch>``. A missing patch is
normalized to ``0`` for comparison; the original tag text is kept for display.
"""
from __future__ import annotations

import re

_TAG_RE = re.compile(r'^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?$')
_VERSION_RE = re.compile(r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?$')
_FORMAT_RE = re.compile(r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')


def _parts(match) -> tuple[int, int, int]:
    major, minor, patch = match.group(1), match.group(2), match.group(3)
    return int(major), int(minor), int(patch or 0)


def parse_tag(tag: object) -> tuple[int, int, int] | None:
    """Return ``(major, minor, patch)`` for a valid release tag, else ``None``."""
    if not isinstance(tag, str):
        return None
    match = _TAG_RE.match(tag)
    return _parts(match) if match else None


def parse_version(text: object) -> tuple[int, int, int] | None:
    """Return ``(major, minor, patch)`` for a version string (tag prefix allowed)."""
    if not isinstance(text, str):
        return None
    value = text[1:] if text.startswith('v') else text
    match = _VERSION_RE.match(value)
    return _parts(match) if match else None


def version_key(text: object) -> tuple[int, int, int]:
    """Sortable key for a version; raises ``ValueError`` for an invalid value."""
    parsed = parse_version(text)
    if parsed is None:
        raise ValueError(f'not a supported version: {text!r}')
    return parsed


def normalize_version(text: object) -> str | None:
    """Canonical ``x.y.z`` form; ``v1.6`` and ``1.6`` both become ``1.6.0``."""
    parsed = parse_version(text)
    if parsed is None:
        return None
    return '{}.{}.{}'.format(*parsed)


def tag_matches_format(tag: object, format_version: object) -> bool:
    """True when the release tag's major/minor equals a manifest format version.

    This is the historical rule the published v1.7.0 client applies before it
    installs anything. It is deliberately unchanged: it must keep rejecting
    ``v1.8.0`` against Format ``1.7`` so an old client fails closed instead of
    silently accepting a package whose parser it cannot run yet.
    """
    if not isinstance(format_version, str):
        return False
    match = _FORMAT_RE.match(format_version)
    if not match:
        return False
    parsed = parse_tag(tag)
    if parsed is None:
        return False
    major, minor, _ = parsed
    return (major, minor) == (int(match.group(1)), int(match.group(2)))


def parse_format(format_version: object) -> tuple[int, int] | None:
    """Return ``(major, minor)`` for ``<major>.<minor>``, else ``None``."""
    if not isinstance(format_version, str):
        return None
    match = _FORMAT_RE.match(format_version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


# The Format contracts this updater implementation is allowed to install when a
# release tag's major.minor no longer encodes the Format (SemVer release number
# and Format contract are separate sequences). This is the trusted, explicit
# compatibility matrix: it lives in the updater code, never in the downloaded
# package, and it must be extended deliberately when support for a new Format is
# implemented. A caller that supplies no matrix gets only the historical rule.
#
# Do not derive this from the target package's MANIFEST.json: that is attacker
# controlled input, and deriving it would make the check circular.
DECLARED_SUPPORTED_FORMATS = ('1.7',)

RULE_TAG_MATCHES_FORMAT = 'tag_matches_format'
RULE_DECLARED_SUPPORTED_FORMAT = 'declared_supported_format'


class FormatCompatibility:
    """The outcome of :func:`format_compatibility`, with the rule that decided it.

    A tiny value object rather than a dataclass: some tests load this module with
    a bare ``spec.loader.exec_module`` (no ``sys.modules`` entry), and dataclass
    field introspection requires the module to be registered.
    """

    __slots__ = ('accepted', 'rule', 'detail')

    def __init__(self, accepted: bool, rule: str | None, detail: str):
        self.accepted = accepted
        self.rule = rule
        self.detail = detail

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FormatCompatibility):
            return NotImplemented
        return (self.accepted, self.rule, self.detail) == \
               (other.accepted, other.rule, other.detail)

    def __repr__(self) -> str:
        return (f'FormatCompatibility(accepted={self.accepted!r}, rule={self.rule!r}, '
                f'detail={self.detail!r})')


def format_compatibility(tag: object, format_version: object,
                         supported_formats: object = None) -> FormatCompatibility:
    """Decide whether ``tag`` may install a package declaring ``format_version``.

    Two rules can accept a package:

    1. the historical rule: the tag's ``major.minor`` equals the Format;
    2. the bridge: ``supported_formats`` is an explicit list / tuple of Format
       contracts this updater declares it can install, and it contains
       ``format_version``.

    Everything else fails closed: a malformed tag, a malformed / undeclared
    Format, a missing, empty, non-sequence or malformed matrix, and a matrix that
    simply does not list the target Format. An empty matrix never means "any".
    """
    tag_parts = parse_tag(tag)
    if tag_parts is None:
        return FormatCompatibility(False, None,
                                   f'release tag {tag!r} is not vMAJOR.MINOR[.PATCH]')
    declared = parse_format(format_version)
    if declared is None:
        return FormatCompatibility(False, None,
                                   f'format_version {format_version!r} is not <major>.<minor>')
    if (tag_parts[0], tag_parts[1]) == declared:
        return FormatCompatibility(True, RULE_TAG_MATCHES_FORMAT,
                                   f'tag major.minor {tag_parts[0]}.{tag_parts[1]} equals '
                                   f'format {format_version}')

    if supported_formats is None:
        return FormatCompatibility(False, None,
                                   f'no explicit compatibility matrix supplied for release tag '
                                   f'{tag!r} / format {format_version}')
    if isinstance(supported_formats, (str, bytes)) or \
            not isinstance(supported_formats, (list, tuple)):
        return FormatCompatibility(False, None,
                                   'compatibility matrix must be a list or tuple of '
                                   '<major>.<minor> strings')
    normalized: list[tuple[int, int]] = []
    for entry in supported_formats:
        parsed = parse_format(entry)
        if parsed is None:
            return FormatCompatibility(False, None,
                                       f'compatibility matrix carries an invalid format {entry!r}')
        normalized.append(parsed)
    if not normalized:
        return FormatCompatibility(False, None,
                                   'compatibility matrix is empty; an empty matrix is not "any format"')
    if declared in normalized:
        return FormatCompatibility(True, RULE_DECLARED_SUPPORTED_FORMAT,
                                   f'format {format_version} is declared supported by this updater')
    return FormatCompatibility(False, None,
                               f'format {format_version} is not in the declared supported formats '
                               f'{list(supported_formats)!r}')
