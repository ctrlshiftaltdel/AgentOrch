#!/usr/bin/env python3
"""Plan-only shadow CLI for the verification planner (Issue #186).

Usage::

    python scripts/verification_planner/cli.py --input PATH

The process reads one JSON snapshot from ``PATH`` and writes one JSON document
to stdout. It performs no writes (no output file, no ``.orchestration/`` state,
no SQLite, no network) and keeps stderr empty.

Exit codes: 2 for a structural / usage diagnostic, 3 for a shadow plan. Exit
code 0 is intentionally unused: the output is ``not_a_verification_result`` and
``shadow_only``, never an approval, a dispatch permission or a ``VERIFIED``.
"""
from __future__ import annotations

import json
import os
import sys

if __package__:
    from . import classify
else:  # executed as a script: `python scripts/verification_planner/cli.py`
    # The contract requires an empty process write set. CPython would otherwise
    # cache the imported module under scripts/verification_planner/__pycache__.
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import classify  # type: ignore[no-redef]


def _emit(document):
    sys.stdout.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def run(argv):
    """Return ``(exit_code, document)`` for a raw argument vector."""
    if len(argv) != 2 or argv[0] != "--input" or not argv[1]:
        return 2, classify.diagnostic("usage", "$")
    try:
        with open(argv[1], "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return 2, classify.diagnostic("schema_invalid", "$")
    try:
        raw = json.loads(text)
    except ValueError:
        return 2, classify.diagnostic("schema_invalid", "$")
    return classify.build(raw)


def main(argv=None):
    code, document = run(sys.argv[1:] if argv is None else list(argv))
    _emit(document)
    return code


if __name__ == "__main__":
    sys.exit(main())
