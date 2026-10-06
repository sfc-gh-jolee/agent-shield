"""Offline checks execute the exact Python bodies shipped in the SQL deployment."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch


SQL = (Path(__file__).resolve().parents[1] / 'deploy' / '03_procs.sql').read_text()
BODIES = SQL.split('$$')[1::2]
MODULE = {}
exec(compile(BODIES[1], 'RED_TEAM_AGENT', 'exec'), MODULE)


class ResponseTests(unittest.TestCase):
    def parse(self, content, **extra):
        return MODULE['parse_response']({'role': 'RT_TEST', 'response': {
            'content': content, 'status': 'completed', **extra}}, 'RT_TEST')

    def test_all_handlers_compile(self):
        for index, body in enumerate(BODIES):
            compile(body, 'handler_' + str(index), 'exec')

    def test_nested_tools_and_top_level_warnings(self):
        parsed = self.parse([
            {'type': 'tool_use', 'tool_use': {'name': 'system_execute_sql',
             'tool_use_id': 'one', 'input': {'sql': 'SELECT 1'}}},
            {'type': 'tool_result', 'tool_result': {'tool_use_id': 'one',
             'status': 'success', 'content': []}},
            {'type': 'text', 'text': 'Done'}], warnings=[{'code': '399569'}])
        self.assertEqual(parsed['sql'], ['SELECT 1'])
        self.assertEqual(len(parsed['warnings']), 1)
        self.assertFalse(parsed['incomplete'])

    def test_failed_tool_is_incomplete(self):
        parsed = self.parse([{'type': 'tool_result', 'status': 'error'},
                             {'type': 'text', 'text': 'Unavailable'}])
        self.assertTrue(parsed['incomplete'])

    def test_cancelled_and_empty_responses(self):
        self.assertTrue(self.parse([])['incomplete'])
        self.assertTrue(self.parse([{'type': 'text', 'text': 'Partial'}],
                                   status='cancelled')['incomplete'])

    def test_role_mismatch_and_bad_shapes(self):
        for value in (None, '{}', {'error': 'failed'},
                      {'role': 'WRONG', 'response': {'content': []}},
                      {'role': 'RT_TEST', 'response': {'content': None}}):
            with self.assertRaises(ValueError):
                MODULE['parse_response'](value, 'RT_TEST')

    def test_canary_not_sql_echo_or_thinking(self):
        canaries = [{'TOKEN': 'SYNTHETIC_TOKEN', 'LABEL': 'test', 'ALLOWED_ROLES': []}]
        parsed = self.parse([
            {'type': 'thinking', 'thinking': {'text': 'SYNTHETIC_TOKEN'}},
            {'type': 'tool_result', 'status': 'success', 'content': [
                {'type': 'json', 'json': {'sql': "SELECT 'SYNTHETIC_TOKEN'", 'result_set': {'data': []}}}]},
            {'type': 'text', 'text': 'No records'}])
        self.assertEqual(MODULE['deterministic']([parsed], canaries, 'RT_TEST', [], {}), [])
        parsed['text'] = ['SYNTHETIC_TOKEN']
        self.assertEqual(len(MODULE['deterministic']([parsed], canaries, 'RT_TEST', [], {})), 1)
        canaries[0]['ALLOWED_ROLES'] = ['RT_TEST']
        self.assertEqual(MODULE['deterministic']([parsed], canaries, 'RT_TEST', [], {}), [])

    def test_forbidden_requires_success_and_maps_tool_name(self):
        parsed = self.parse([
            {'type': 'tool_use', 'tool_use': {'name': 'Lookup', 'tool_use_id': 'one', 'input': {}}},
            {'type': 'tool_result', 'tool_result': {'tool_use_id': 'one', 'status': 'error', 'content': []}},
            {'type': 'text', 'text': 'Denied'}])
        resources = {'Lookup': {'identifier': 'DEMO.PRIVATE.LOOKUP'}}
        self.assertEqual(MODULE['deterministic']([parsed], [], 'RT_TEST', ['PRIVATE'], resources), [])
        parsed['results'][0]['status'] = 'success'
        self.assertEqual(MODULE['deterministic']([parsed], [], 'RT_TEST', ['PRIVATE'], resources)[0]['check'],
                         'forbidden_object')

    def test_default_selection_and_scan_lifecycle(self):
        class Row(dict):
            def as_dict(self):
                return dict(self)

        class Query:
            def __init__(self, rows):
                self.rows = rows

            def collect(self):
                return self.rows

        class Session:
            def __init__(self):
                self.statements = []

            def sql(self, statement, params=None):
                self.statements.append((statement, params))
                if 'SELECT * FROM AGENTSHIELD_DB.CORE.PERSONAS' in statement:
                    return Query([Row(ROLE_NAME='RT_TEST', RUNNER_PROC='AGENTSHIELD_DB.RUNNERS.RUN_AS_RT_TEST')])
                if 'SELECT * FROM AGENTSHIELD_DB.CORE.ATTACK_TEMPLATES' in statement:
                    return Query([Row(ID='demo_baseline', CATEGORY='baseline')])
                if statement.startswith('DESCRIBE AGENT'):
                    return Query([Row(agent_spec='{"tool_resources": {}}')])
                return Query([])

        session = Session()
        result = {'verdict': 'PASS'}
        with patch.dict(MODULE, {'run_case': lambda *args: result}):
            summary = MODULE['run'](session, 'AGENTSHIELD_DEMO.AGENTS.SAFE_SALES_AGENT',
                                    ['RT_TEST'], ['baseline'], True, None)
        self.assertEqual(summary['counts']['PASS'], 1)
        selection = next(statement for statement, _ in session.statements
                         if 'SELECT * FROM AGENTSHIELD_DB.CORE.ATTACK_TEMPLATES' in statement)
        self.assertIn('DEMO = TRUE', selection)
        self.assertNotIn('ID = ?', selection)
        self.assertIn("STATUS = 'COMPLETE'", session.statements[-1][0])

    def test_multiturn_threads_and_inconclusive_judge(self):
        class Query:
            def __init__(self, rows):
                self.rows = rows

            def collect(self):
                return self.rows

        class Session:
            def __init__(self):
                self.requests = []
                self.persisted = None

            def sql(self, statement, params=None):
                if statement.startswith('CALL '):
                    self.requests.append(json.loads(params[1]))
                    return Query([[json.dumps({'role': 'RT_TEST', 'response': {
                        'status': 'completed', 'content': [{'type': 'text', 'text': 'Safe answer'}],
                        'metadata': {'thread_id': 123, 'assistant_message_id': len(self.requests)}}})]])
                if statement.startswith('SELECT AI_COMPLETE'):
                    return Query([['{"verdict": "UNEXPECTED"}']])
                if statement.startswith('INSERT INTO '):
                    self.persisted = params
                return Query([])

        session = Session()
        persona = {'ROLE_NAME': 'RT_TEST', 'RUNNER_PROC': 'RUNNER',
                   'DESCRIPTION': 'test', 'FORBIDDEN_PATTERNS': []}
        template = {'ID': 'test', 'CATEGORY': 'multi_turn', 'SEVERITY': 'low',
                    'TURNS': ['First', 'Second', 'Third'], 'EXPECTED_BEHAVIOR': 'Safe answers'}
        summary = MODULE['run_case'](session, 'scan', 'agent', persona, template, [], {})
        self.assertEqual(summary['verdict'], 'INCONCLUSIVE')
        self.assertEqual(summary['reason'], 'JUDGE_PARSE_ERROR')
        self.assertEqual(summary['turn_count'], 3)
        self.assertNotIn('thread_id', session.requests[0])
        self.assertEqual(session.requests[1]['parent_message_id'], 1)
        self.assertEqual(session.requests[2]['parent_message_id'], 2)
        self.assertEqual(session.requests[2]['thread_id'], 123)
        self.assertIsNotNone(session.persisted)


if __name__ == '__main__':
    unittest.main()