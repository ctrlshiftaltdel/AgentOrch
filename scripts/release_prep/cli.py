#!/usr/bin/env python3
"""Read-only release preparation CLI (Issue #194).

Usage::

    python scripts/release_prep/cli.py --input PATH

The process reads one JSON snapshot from ``PATH`` and writes exactly one JSON
document to stdout. It performs no writes (no output file, no
``.orchestration/`` state, no bytes on disk, no network), starts no subprocess
and keeps stderr empty. The report is not a release: ``plan_usability`` is
``not_a_release`` and ``release_authorized`` is ``false``.

Exit codes: 2 for a structural / usage diagnostic, 3 for a written report.
Exit code 0 is intentionally unused.
"""
from __future__ import annotations

import json
import os
import sys

if __package__:
    from . import report
else:  # executed as a script: `python scripts/release_prep/cli.py`
    # The contract requires an empty process write set. CPython would otherwise
    # cache the imported module under scripts/release_prep/__pycache__.
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import report  # type: ignore[no-redef]


def _emit(document):
    sys.stdout.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def run(argv):
    """Return ``(exit_code, document)`` for a raw argument vector."""
    if len(argv) != 2 or argv[0] != "--input" or not argv[1]:
        return 2, report.diagnostic("usage", "$")
    try:
        with open(argv[1], "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return 2, report.diagnostic("schema_invalid", "$")
    return report.build(raw)


def main(argv=None):
    code, document = run(sys.argv[1:] if argv is None else list(argv))
    _emit(document)
    return code


if __name__ == "__main__":
    sys.exit(main())
