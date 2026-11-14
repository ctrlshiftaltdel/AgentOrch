import json
import unittest

from scripts import opencode_probe


class OpenCodeProbeTests(unittest.TestCase):
    def events(self):
        return [
            {'type': 'step_start', 'sessionID': 'ses_test', 'part': {'type': 'step-start', 'sessionID': 'ses_test'}},
            {'type': 'text', 'sessionID': 'ses_test', 'part': {'type': 'text', 'sessionID': 'ses_test', 'text': 'hello'}},
            {'type': 'step_finish', 'sessionID': 'ses_test', 'part': {'type': 'step-finish', 'sessionID': 'ses_test', 'reason': 'stop'}},
        ]

    def classify(self, events, code=0):
        return opencode_probe.classify(code, '\n'.join(map(json.dumps, events)))['status']

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

    def test_rejections_from_complete_valid_stream(self):
        good = self.events()
        self.assertEqual(self.classify(good), 'OBSERVED')
        cases = []
        for value in ('', '   '):
            events = self.events(); events[1]['part']['text'] = value; cases.append(events)
        events = self.events(); events.insert(2, {'type': 'error', 'sessionID': 'ses_test', 'part': {}}); cases.append(events)
        events = self.events(); events.insert(2, {'type': 'tool_use', 'sessionID': 'ses_test', 'part': {}}); cases.append(events)
        events = self.events(); events[1]['sessionID'] = 'ses_other'; cases.append(events)
        events = self.events(); events[1]['part']['sessionID'] = 'ses_other'; cases.append(events)
        events = self.events(); events[1]['part']['type'] = 'step-start'; cases.append(events)
        for events in cases:
            with self.subTest(events=events):
                self.assertEqual(self.classify(events), 'UNKNOWN')
        self.assertEqual(self.classify(good, code=1), 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
