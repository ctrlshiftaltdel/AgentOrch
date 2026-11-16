"""Deterministic, read-only delivery-risk classifier.

The classifier answers one question for a batch delivery: how much review and
verification does a diff between two commits need? It reads only
``git diff --name-status --no-renames <base>...<head>`` and writes nothing, so
it can run in CI for every pull request and inside the batch coordinator before
a push or merge.

Limitations, stated honestly:

* classification is path-based. A harmless edit to ``AGENTS.md`` and a
  completely rewritten contract are both L2; a new exploit in a file under
  ``docs/`` is L0. The level is a scheduling hint, not a correctness proof.
* a path this module does not recognize is L2, never L0. That is fail-closed
  but still only a *guess* about which checks exist; it cannot invent a
  verification command.
* the result describes the diff Git reports. A clean/smudge filter, a commit
  hook, or a writable object store can change what a later checkout sees; this
  module has no sandbox and no signature over the objects.
* no network, no third-party imports, no shell.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
import re
import subprocess
import sys

SCHEMA = 'delivery-risk/1'
VERSION = '250'
LEVELS = ('L0', 'L1', 'L2', 'L3')
STATUS_OK = 0
STATUS_INVALID = 2

# Credential-shaped paths are L3: a human has to authorize those paths, and no
# automated runner may treat a secret-bearing diff as ordinary code.
SECRET_PATTERNS = ('.env', '.env.*', '*secret*', '*token*', '*.pem', '*.key',
                   'credentials*')
SECRET_EXCEPTIONS = ('.env.example',)

# Contract, policy, release and runtime-control paths need full validation and
# an independent review. Prefix entries are directories; exact entries are one
# file (or one file pattern handled below).
L2_EXACT = frozenset({
    'AGENTS.md',
    'VERSION.md',
    'docs/PROJECT.md',
    'docs/PLAN.md',
    'docs/orchestration.md',
    'scripts/orchestrator.py',
    'scripts/validate_kit.py',
    'scripts/build_release.py',
    'scripts/check_drift.py',
    'scripts/check_scope.py',
    'scripts/verification_profile.py',
    'scripts/autonomous_delivery.py',
    'scripts/batch_delivery.py',
    'scripts/delivery_risk.py',
})
L2_PREFIX = (
    'kit/',
    'editions/',
    'templates/',
    '.github/workflows/',
    'scripts/agent_selection/',
    'scripts/update_agentorch/',
    'scripts/self_improvement/',
)
# release/ is L2 as a whole except its README, which is documentation.
L2_RELEASE_PREFIX = 'release/'
L2_RELEASE_EXCEPTION = 'release/README.md'

# Everything below is still executable or configurable by the delivery
# machinery, so it gets incremental checks and one batch review.
L1_EXACT = frozenset({'README.md', 'pyproject.toml', 'Dockerfile', 'Makefile',
                      'package.json'})
L1_PREFIX = ('scripts/', 'tests/', '.github/')
# Ordinary application source and configuration outside the high-risk set is
# L1, not "unclassified L2": the tier exists so downstream projects do not pay
# full validation for every source edit. The list is conservative; anything
# else stays L2.
SOURCE_SUFFIXES = ('.py', '.js', '.jsx', '.ts', '.tsx', '.go', '.rs', '.java',
                   '.kt', '.c', '.h', '.cc', '.cpp', '.cs', '.rb', '.php',
                   '.swift', '.sh', '.sql', '.html', '.css', '.scss', '.vue',
                   '.svelte')
CONFIG_SUFFIXES = ('.toml', '.yaml', '.yml', '.json', '.ini', '.cfg')
CONFIG_NAMES = frozenset({'Dockerfile', 'Makefile', 'package.json',
                          'pyproject.toml'})

# Documentation, text and images only. This list is intentionally explicit:
# an unrecognized extension under docs/ makes the path unclassified (L2), not
# silently L0.
DOC_SUFFIXES = ('.md', '.markdown', '.txt', '.rst', '.png', '.jpg', '.jpeg',
                '.gif', '.svg', '.webp')
L0_EXCEPTIONS = frozenset({'AGENTS.md', 'VERSION.md', 'LICENSE.md'})


class RiskError(ValueError):
    """Invalid input or a Git failure; the CLI maps this to exit 2."""


def _run(repo, *args):
    return subprocess.run(['git', '--no-optional-locks', *args], cwd=str(repo),
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)


def _text(repo, *args):
    result = _run(repo, *args)
    if result.returncode != 0:
        return None
    return result.stdout.decode('utf-8', 'replace').strip()


def _resolve(repo, ref):
    """Resolve one ref to a full commit SHA; unknown refs are invalid input."""
    if not isinstance(ref, str) or not ref.strip():
        raise RiskError('base and head must be nonempty refs')
    resolved = _text(repo, 'rev-parse', '--verify', '--quiet', ref + '^{commit}')
    if not resolved:
        raise RiskError(f'unknown ref {ref!r}')
    if not re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', resolved):
        raise RiskError(f'ref {ref!r} did not resolve to a commit')
    return resolved


def _merge_base(repo, base, head):
    result = _run(repo, 'merge-base', base, head)
    if result.returncode != 0:
        raise RiskError(f'no merge base between {base} and {head}')
    value = result.stdout.decode('utf-8', 'replace').strip()
    if not value:
        raise RiskError(f'no merge base between {base} and {head}')
    return value


def _safe_name(raw):
    """Normalize one changed path into the POSIX form the rules use.

    Git should never produce an absolute path, a backslash or a ``..``
    component here. If one appears anyway the tree is not something this
    classifier understands, so the caller fails closed with L2 rather than
    guessing.
    """
    name = raw.replace('\\', '/') if '\\' in raw else raw
    if '\\' in raw:
        raise RiskError('changed path contains a backslash separator')
    if name.startswith('/') or re.match(r'^[A-Za-z]:', name):
        raise RiskError('changed path is absolute')
    parts = name.split('/')
    if not name or any(part in ('', '.', '..') for part in parts):
        raise RiskError('changed path contains an empty, "." or ".." component')
    return name


def _secret(name):
    basename = name.rsplit('/', 1)[-1]
    if basename in SECRET_EXCEPTIONS:
        return False
    return any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(basename, pattern)
               for pattern in SECRET_PATTERNS)


def _under(name, prefix):
    return name == prefix.rstrip('/') or name.startswith(prefix)


def _l2(name):
    if name in L2_EXACT or any(_under(name, prefix) for prefix in L2_PREFIX):
        return True
    if name.startswith(L2_RELEASE_PREFIX):
        return name != L2_RELEASE_EXCEPTION
    return False


def _l1(name):
    if name in L1_EXACT or any(_under(name, prefix) for prefix in L1_PREFIX):
        return True
    basename = name.rsplit('/', 1)[-1]
    if basename in CONFIG_NAMES:
        return True
    return name.endswith(SOURCE_SUFFIXES + CONFIG_SUFFIXES)


def _l0(name):
    if name == L2_RELEASE_EXCEPTION:
        return True
    if name.startswith('assets/'):
        return True
    if '/' not in name:
        # Root-level Markdown only, except the contract and the version/licence
        # files. README.md is named as L1 above, so it never reaches here.
        return name.endswith(('.md', '.markdown')) and name not in L0_EXCEPTIONS
    if name.startswith('docs/') and name.endswith(DOC_SUFFIXES):
        return True
    return False


def _classify_one(name, status):
    """Return ``(level, reason)`` for one changed path."""
    if _secret(name):
        return 'L3', f'secret-shaped path: {name}'
    if status == 'D' and name == 'LICENSE.md':
        return 'L3', 'deletion of LICENSE.md needs human authorization'
    if _l2(name):
        return 'L2', f'contract, policy or runtime-control path: {name}'
    if _l1(name):
        return 'L1', f'executable or configuration path: {name}'
    if _l0(name):
        return 'L0', f'documentation or asset path: {name}'
    return 'L2', f'unclassified path: {name}'


def _diff(repo, base, head):
    # -z keeps Git's raw bytes: without it Git quotes unusual paths and a
    # quoted ``"docs/\303\251/.env"`` would look like an unknown path instead of
    # a secret-shaped one. --name-status -z emits ``<status>\0<path>\0`` pairs.
    result = _run(repo, 'diff', '--name-status', '-z', '--no-renames',
                  f'{base}...{head}')
    if result.returncode != 0:
        raise RiskError('git diff failed')
    fields = result.stdout.split(b'\0')
    changed = []
    index = 0
    while index + 1 < len(fields):
        status_token = fields[index]
        raw_path = fields[index + 1]
        index += 2
        if not status_token:
            continue
        status = status_token.decode('ascii', 'replace')[:1]
        raw = raw_path.decode('utf-8', 'surrogateescape')
        try:
            name = _safe_name(raw)
        except RiskError as exc:
            # Git produced something this classifier cannot normalize. A path
            # that could still be secret-shaped is never downgraded to L2 just
            # because a backslash made it invalid.
            if _secret(raw) or _secret(raw.replace('\\', '/')):
                changed.append((raw, 'L3', f'secret-shaped path: {raw}'))
            else:
                changed.append((raw, 'L2', f'invalid changed path: {exc}'))
            continue
        changed.append((name, *_classify_one(name, status)))
    changed.sort()
    return changed


def _result(base, head, changed):
    levels = [level for _, level, _ in changed]
    if not changed:
        level = 'L0'
        reasons = ['empty diff']
    else:
        level = max(levels, key=LEVELS.index)
        reasons = [reason for _, _, reason in changed]
        reasons.append(f'highest level {level}')
        if any(level == 'L2' and reason.startswith('unclassified path')
               or reason.startswith('invalid changed path')
               for _, level, reason in changed):
            reasons.append('fail-closed on an unknown path')
    full = level in ('L2', 'L3')
    review = {'L0': 'none', 'L1': 'batch', 'L2': 'required', 'L3': 'required'}[level]
    return {
        'schema': SCHEMA,
        'base': base,
        'head': head,
        'level': level,
        'full_validation': full,
        'independent_review': review,
        'reasons': reasons,
        'changed_paths': [name for name, _, _ in changed],
        'version': VERSION,
    }


def classify(repo, base, head):
    """Classify ``base...head`` in ``repo``; raise :class:`RiskError` on input.

    The return value uses the stable ``delivery-risk/1`` schema. The function
    is read-only: it starts ``git rev-parse``, ``git merge-base`` and
    ``git diff`` only.
    """
    root = Path(repo).resolve()
    if not root.is_dir():
        raise RiskError(f'repository path is not a directory: {repo}')
    top = _text(root, 'rev-parse', '--show-toplevel')
    if not top or Path(top).resolve() != root:
        raise RiskError(f'not a Git worktree root: {repo}')
    base_sha = _resolve(root, base)
    head_sha = _resolve(root, head)
    merge_base = _merge_base(root, base_sha, head_sha)
    if base_sha == head_sha:
        return _result(base_sha, head_sha, [])
    # The three-dot diff is the review surface: what the head adds relative to
    # the merge base. Comparing the resolved SHAs keeps a moving branch from
    # changing the answer between the two reads.
    changed = _diff(root, merge_base, head_sha)
    return _result(base_sha, head_sha, changed)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Classify the delivery risk of a Git diff (read-only).')
    parser.add_argument('--repo', required=True, help='Git worktree root')
    parser.add_argument('--base', required=True, help='base ref')
    parser.add_argument('--head', required=True, help='head ref')
    parser.add_argument('--json', action='store_true', help='print one JSON object')
    args = parser.parse_args(argv)
    try:
        result = classify(args.repo, args.base, args.head)
    except (RiskError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return STATUS_INVALID
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"LEVEL {result['level']}")
        for reason in result['reasons']:
            print(reason)
    return STATUS_OK


if __name__ == '__main__':
    raise SystemExit(main())
