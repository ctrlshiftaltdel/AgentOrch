#!/usr/bin/env python3
"""Post-merge verification profile (Issue #188).

The merge gate G4 requires the configured checks on the current head, and
``docs/orchestration.md`` requires ``docs/PROJECT.md``'s commands after the merge.
The default post-merge set is the full one::

    python scripts/validate_kit.py                          # quick
    python -m unittest discover -s tests                    # full regression
    python scripts/orchestrator.py backup                   # backup
    python -m py_compile scripts/orchestrator.py scripts/validate_kit.py  # static

This module answers one question, read-only: which post-merge commands apply to
this checkout? Only the exact ``verification_profile: risk_pilot`` value in
``docs/PROJECT.md`` may remove the full regression, and only when the caller
supplies proof that the change is L0. Everything else - an absent profile,
``strict``, any other value, missing or malformed proof, a semantic marker that
is true, or a path in the orchestration / contract / release surface - falls
back to the full set. A reduction is never inferred from silence.

The caller's proof is a JSON document::

    {
      "schema_version": "verification-l0-evidence/1",
      "risk_level": "L0",
      "changed_paths": ["docs/README.md"],
      "executable_doc": false,
      "policy": false,
      "contract": false,
      "uncertainty_l3": false
    }

The four markers above are mandatory booleans and must be ``false``. Optional
``identity``, ``migration``, ``release``, ``security``, ``permission``,
``lock`` and ``state_machine`` markers are honoured when present: any true value
also keeps the full set. A missing evidence file is not an error by itself - it
simply proves nothing, so the full set is returned. A present but unreadable or
non-JSON evidence file is an input error (exit 2) rather than a silent pass.

The tool never runs a check, never touches ``.orchestration/`` and never writes
project state. Only the standard library is used.

Usage::

    python scripts/verification_profile.py [--project PATH] [--evidence PATH] [--json]

Without ``--json`` the commands are printed one per line, ready to paste. With
``--json`` the decision document is printed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 'verification-profile/1'
PROFILE_KEY = 'verification_profile'
PROFILE_RISK_PILOT = 'risk_pilot'
PROFILE_SOURCE = 'docs/PROJECT.md'

# Same declaration shape as ``edition`` in scripts/orchestrator.py: a Markdown
# list item with the key optionally backticked.
PROFILE_LINE = re.compile(
    r'^\s*[-*]\s*`?' + PROFILE_KEY + r'`?\s*[:：]\s*(?P<value>.*)$', re.M)
# The value must be a single token. Anything else (prose, a second value, an
# empty declaration) is not the exact opt-in and therefore keeps the full set.
VALUE_TOKEN = re.compile(r'^`?([A-Za-z0-9_-]+)`?$')

FULL_COMMAND_IDS = ('quick', 'full_regression', 'backup', 'static')
RISK_PILOT_COMMAND_IDS = ('quick', 'backup', 'static')

COMMANDS = {
    'quick': 'python scripts/validate_kit.py',
    'full_regression': 'python -m unittest discover -s tests',
    'backup': 'python scripts/orchestrator.py backup',
    'static': 'python -m py_compile scripts/orchestrator.py scripts/validate_kit.py',
}

# Paths that may never lose the full regression, regardless of the caller's
# claims: orchestration, contracts, CI, identity / release surfaces. ``scripts/``
# already covers ``scripts/orchestrator.py``; the explicit entry documents the
# named high-risk file from Issue #188.
RESTRICTED_PATHS = frozenset({
    'AGENTS.md',
    'VERSION.md',
    # The core contract moved out of kit/; it is still a high-risk path and must
    # keep the full regression under a risk_pilot claim.
    'docs/orchestration.md',
    'docs/PROJECT.md',
    'docs/PLAN.md',
    'scripts/orchestrator.py',
})
RESTRICTED_PREFIXES = (
    # The legacy kit/ directory is kept restricted too: if it is ever put back
    # (partial migration, old checkout), it must not lose the full regression.
    'kit/',
    '.github/workflows/',
    'editions/',
    'scripts/',
    'release/',
    'templates/',
)

REQUIRED_FALSE_MARKERS = ('executable_doc', 'policy', 'contract', 'uncertainty_l3')
OPTIONAL_FALSE_MARKERS = (
    'identity', 'migration', 'release', 'security', 'permission', 'lock',
    'state_machine',
)

EXIT_OK = 0
EXIT_INVALID = 2


class EvidenceError(Exception):
    """The caller supplied an evidence document that cannot be read or parsed."""


def project_config_path(root):
    return Path(root) / 'docs' / 'PROJECT.md'


def read_profile(root):
    """Return the declared verification profile, or ``None`` when unusable.

    Absent file, absent key, duplicated key and a value that is not a single
    token all return ``None``. They are not errors: the caller falls back to the
    full set, which is the safe default.
    """
    try:
        text = project_config_path(root).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    matches = list(PROFILE_LINE.finditer(text))
    if len(matches) != 1:
        return None
    match = VALUE_TOKEN.match(matches[0].group('value').strip())
    return match.group(1) if match else None


def normalize_changed_path(value):
    """Return a clean relative POSIX path, or ``None`` when it cannot be one."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace('\\', '/')
    if text.startswith('./'):
        text = text[2:]
    if ':' in text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts:
        return None
    if '..' in path.parts:
        return None
    return path.as_posix()


def is_restricted(path):
    if path in RESTRICTED_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in RESTRICTED_PREFIXES)


def load_evidence(path):
    """Read one evidence JSON object. Raises ``EvidenceError`` on bad input."""
    try:
        raw = Path(path).read_text(encoding='utf-8')
    except OSError as exc:
        raise EvidenceError(f'cannot read evidence file {path}: {exc}') from None
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise EvidenceError(f'evidence file {path} is not valid JSON: {exc}') from None
    if not isinstance(document, dict):
        raise EvidenceError(f'evidence file {path} must contain a JSON object')
    return document


def assess_evidence(evidence):
    """Return ``(proven, reasons, restricted_paths)`` for one evidence object."""
    reasons = []
    restricted = []

    if evidence.get('risk_level') != 'L0':
        reasons.append('risk_level_not_l0')

    for marker in REQUIRED_FALSE_MARKERS:
        if marker not in evidence:
            reasons.append(f'marker_missing:{marker}')
        elif evidence[marker] is not False:
            code = 'marker_true' if evidence[marker] is True else 'marker_invalid'
            reasons.append(f'{code}:{marker}')

    for marker in OPTIONAL_FALSE_MARKERS:
        if marker in evidence and evidence[marker] is not False:
            code = 'marker_true' if evidence[marker] is True else 'marker_invalid'
            reasons.append(f'{code}:{marker}')

    changed = evidence.get('changed_paths')
    if not isinstance(changed, list) or not changed:
        reasons.append('changed_paths_missing')
    else:
        for value in changed:
            path = normalize_changed_path(value)
            if path is None:
                reasons.append('changed_path_invalid')
            elif is_restricted(path):
                restricted.append(path)
        if restricted:
            reasons.append('changed_path_restricted')

    return (not reasons), reasons, restricted


def _document(profile, reduced, reasons, restricted):
    ids = RISK_PILOT_COMMAND_IDS if reduced else FULL_COMMAND_IDS
    return {
        'schema_version': SCHEMA_VERSION,
        'profile': profile,
        'profile_source': PROFILE_SOURCE,
        'decision': 'reduced' if reduced else 'full',
        'reasons': list(reasons),
        'restricted_paths': sorted(restricted),
        'commands': [{'id': identifier, 'command': COMMANDS[identifier]}
                     for identifier in ids],
    }


def resolve(root, evidence=None):
    """Return the post-merge decision document for ``root``.

    ``evidence`` is an already-parsed mapping (or ``None``). A missing evidence
    document closes the reduction, not the run: the full set is returned.
    """
    profile = read_profile(root)
    if profile != PROFILE_RISK_PILOT:
        reason = 'profile_absent' if profile is None else 'profile_not_risk_pilot'
        return _document(profile, False, [reason], [])
    if evidence is None:
        return _document(profile, False, ['evidence_missing'], [])

    proven, reasons, restricted = assess_evidence(evidence)
    if not proven:
        return _document(profile, False, reasons, restricted)
    return _document(profile, True, ['risk_pilot_l0_proven'], [])


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.', metavar='PATH',
                        help='project root containing docs/PROJECT.md (default: .)')
    parser.add_argument('--evidence', metavar='PATH',
                        help='caller-supplied L0 evidence JSON document')
    parser.add_argument('--json', action='store_true',
                        help='print the decision document instead of bare commands')
    args = parser.parse_args(argv)

    try:
        evidence = load_evidence(args.evidence) if args.evidence else None
    except EvidenceError as exc:
        print(json.dumps({'schema_version': SCHEMA_VERSION, 'error': str(exc)},
                         ensure_ascii=False, sort_keys=True))
        return EXIT_INVALID

    document = resolve(args.project, evidence)
    if args.json:
        print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    else:
        for item in document['commands']:
            print(item['command'])
    return EXIT_OK


def main(argv=None):
    return run(sys.argv[1:] if argv is None else list(argv))


if __name__ == '__main__':
    sys.exit(main())
