"""Classify a *single* OpenCode JSON probe stream, not a delivery result.

The caller must actually run OpenCode and provide its exit code. This module
does not launch agents, grant permissions, validate isolation, approve changes,
or advance task state. An observed response is not a successful task or review.
"""
from __future__ import annotations

import argparse
import json
import sys


def classify(returncode, output):
    """Conservatively recognize a completed, single-session, tool-free probe."""
    unknown = {'status': 'UNKNOWN', 'session_id': None, 'text': None}
    if returncode != 0 or not output.strip():
        return unknown
    session = None
    text = []
    started = False
    finished = False
    try:
        for line in output.splitlines():
            event = json.loads(line)
            if not isinstance(event, dict):
                return unknown
            kind = event.get('type')
            event_session = event.get('sessionID')
            if not isinstance(event_session, str) or not event_session.startswith('ses_'):
                return unknown
            if session is not None and session != event_session:
                return unknown
            session = event_session
            part = event.get('part')
            if not isinstance(part, dict) or finished:
                return unknown
            if kind == 'step_start' and not started:
                started = True
            elif kind == 'text' and started and isinstance(part.get('text'), str):
                text.append(part['text'])
            elif kind == 'step_finish' and started and part.get('reason') == 'stop':
                finished = True
            else:
                return unknown
    except (ValueError, TypeError):
        return unknown
    if not started or not finished or not text:
        return unknown
    return {'status': 'OBSERVED', 'session_id': session, 'text': ''.join(text)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exit-code', type=int, required=True,
                        help='actual exit code of the OpenCode process')
    args = parser.parse_args(argv)
    result = classify(args.exit_code, sys.stdin.read())
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'OBSERVED' else 1


if __name__ == '__main__':
    sys.exit(main())
