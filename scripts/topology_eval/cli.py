#!/usr/bin/env python3
"""Observe-only task topology evaluation CLI (Issue #180).

Usage::

    python scripts/topology_eval/cli.py --input PATH

The process reads one caller snapshot from ``PATH`` and writes one JSON document
to stdout. It performs no writes (no output file, no ``.orchestration/`` state,
no SQLite, no ``__pycache__``, no network), creates no instance and leaves stderr
empty.

Exit codes: 2 for a structural / usage diagnostic, 3 for an evaluation. Exit
code 0 is intentionally unused: the output is ``not_a_quality_result`` with
``savings_claimed: false``, never a quality verdict, a release gate, a dispatch
permission or a ``VERIFIED`` signal.
"""
from __future__ import annotations

import json
import os
import sys

if __package__:
    from . import evaluate
else:  # executed as a script: `python scripts/topology_eval/cli.py`
    # The contract requires an empty process write set. CPython would otherwise
    # cache the imported module under scripts/topology_eval/__pycache__.
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import evaluate  # type: ignore[no-redef]


def _emit(document):
    sys.stdout.write(json.dumps(document, ensure_ascii=False, sort_keys=True)
                     + "\n")
    sys.stdout.flush()


def run(argv):
    """Return ``(exit_code, document)`` for a raw argument vector."""
    if len(argv) != 2 or argv[0] != "--input" or not argv[1]:
        return 2, evaluate.diagnostic("usage", "$")
    try:
        with open(argv[1], "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return 2, evaluate.diagnostic("schema_invalid", "$")
    try:
        raw = json.loads(text)
    except ValueError:
        return 2, evaluate.diagnostic("schema_invalid", "$")
    return evaluate.build(raw)


def main(argv=None):
    code, document = run(sys.argv[1:] if argv is None else list(argv))
    _emit(document)
    return code


if __name__ == "__main__":
    sys.exit(main())
