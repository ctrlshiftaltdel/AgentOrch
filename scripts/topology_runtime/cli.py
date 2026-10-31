#!/usr/bin/env python3
"""Observe-only runtime handoff decision CLI (Issue #179).

Usage::

    python scripts/topology_runtime/cli.py --input PATH

The process reads one caller snapshot from ``PATH`` and writes one JSON document
to stdout. It performs no writes (no output file, no ``.orchestration/`` state, no
SQLite, no ``__pycache__``, no network), creates no instance, spawns / waits /
collects nothing and leaves stderr empty.

Exit codes: 2 for a structural / usage diagnostic, 3 for a decision (including
every manual handoff). Exit code 0 is intentionally unused: the output is
``not_a_dispatch`` with ``dispatched: false``, never an approval, a dispatch
permission or a ``VERIFIED`` signal. A runtime name is not evidence: without
``OBSERVED`` facts the capability stays ``UNKNOWN``.
"""
from __future__ import annotations

import json
import os
import sys

if __package__:
    from . import decide
else:  # executed as a script: `python scripts/topology_runtime/cli.py`
    # The contract requires an empty process write set. CPython would otherwise
    # cache the imported module under scripts/topology_runtime/__pycache__.
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import decide  # type: ignore[no-redef]


def _emit(document):
    sys.stdout.write(json.dumps(document, ensure_ascii=False, sort_keys=True)
                     + "\n")
    sys.stdout.flush()


def run(argv):
    """Return ``(exit_code, document)`` for a raw argument vector."""
    if len(argv) != 2 or argv[0] != "--input" or not argv[1]:
        return 2, decide.diagnostic("usage", "$")
    try:
        with open(argv[1], "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return 2, decide.diagnostic("schema_invalid", "$")
    try:
        raw = json.loads(text)
    except ValueError:
        return 2, decide.diagnostic("schema_invalid", "$")
    return decide.build(raw)


def main(argv=None):
    code, document = run(sys.argv[1:] if argv is None else list(argv))
    _emit(document)
    return code


if __name__ == "__main__":
    sys.exit(main())
