import json
import unittest

from scripts import opencode_probe


class OpenCodeProbeTests(unittest.TestCase):
    def test_complete_terminal_and_session_are_required(self):
        events = [
            {'type': 'step_start', 'sessionID': 'ses_test', 'part': {}},
            {'type': 'text', 'sessionID': 'ses_test', 'part': {'text': 'hello'}},
            {'type': 'step_finish', 'sessionID': 'ses_test',
             'part': {'reason': 'stop'}},
        ]
        result = opencode_probe.classify(0, '\n'.join(map(json.dumps, events)))
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['session_id'], 'ses_test')
        self.assertEqual(result['text'], 'hello')

    def test_failure_or_uncertain_result_is_not_success(self):
        good = json.dumps({'type': 'step_finish', 'sessionID': 'ses_a',
                           'part': {'reason': 'stop'}})
        for code, output in [(1, good), (0, ''), (0, 'not json'),
                             (0, good + '\n' + json.dumps({'type': 'error'})),
                             (0, json.dumps({'type': 'step_finish', 'sessionID': 'ses_a',
                                             'part': {'reason': 'error'}}))]:
            with self.subTest(code=code, output=output):
                self.assertNotEqual(opencode_probe.classify(code, output)['status'], 'OBSERVED')

    def test_mixed_sessions_and_tool_use_are_not_observed(self):
        for event in [
            {'type': 'text', 'sessionID': 'ses_b', 'part': {'text': 'x'}},
            {'type': 'tool_use', 'sessionID': 'ses_a', 'part': {}},
        ]:
            output = '\n'.join(map(json.dumps, [
                {'type': 'step_start', 'sessionID': 'ses_a', 'part': {}}, event,
                {'type': 'step_finish', 'sessionID': 'ses_a', 'part': {'reason': 'stop'}},
            ]))
            self.assertNotEqual(opencode_probe.classify(0, output)['status'], 'OBSERVED')


if __name__ == '__main__':
    unittest.main()
