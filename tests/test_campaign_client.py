"""Client routing and subprocess safety without a Snowflake connection."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import mock_open, patch

SOURCE = Path(__file__).resolve().parents[1] / 'scripts' / 'campaign_client.py'
SPEC = importlib.util.spec_from_file_location('campaign_client', SOURCE)
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)


class ClientTests(unittest.TestCase):
    def test_account_mismatch_never_calls_campaign(self):
        argv = ['client', '--connection', 'sandbox', '--expected-account', 'EXPECTED', 'options']
        with patch('sys.argv', argv), patch.object(CLIENT, 'sql', return_value=[{'ACCOUNT': 'WRONG'}]) as sql:
            with self.assertRaisesRegex(RuntimeError, 'ACCOUNT_MISMATCH'):
                CLIENT.main()
            self.assertEqual(sql.call_count, 1)

    def test_cli_error_does_not_echo_confidential_output(self):
        response = SimpleNamespace(returncode=1, stdout='RAW_DATA', stderr='RAW_REQUEST')
        with patch.object(CLIENT.subprocess, 'run', return_value=response):
            with self.assertRaises(RuntimeError) as caught:
                CLIENT.sql('sandbox', 'SELECT 1')
        self.assertNotIn('RAW_', str(caught.exception))

    def test_subprocess_has_no_shell_and_explicit_connection(self):
        response = SimpleNamespace(returncode=0, stdout='[[{"RESULT": "ok"}]]')
        with patch.object(CLIENT.subprocess, 'run', return_value=response) as run:
            self.assertEqual(CLIENT.sql('sandbox', 'SELECT 1'), [{'RESULT': 'ok'}])
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertEqual(run.call_args.args[0][2:4], ['-c', 'sandbox'])

    def test_sql_string_escaping(self):
        self.assertEqual(CLIENT.literal("user's request"), "'user''s request'")
        self.assertEqual(CLIENT.literal('a\\nb'), "'a\\\\nb'")
        self.assertEqual(CLIENT.literal('quote: ' + chr(92) + chr(34)),
                         "'quote: " + chr(92) * 2 + chr(34) + "'")

    def test_batch_launch_canonical_selection_and_retry_key(self):
        def api(connection, action, request):
            if action == 'options':
                return {'categories': [{'CATEGORY': 'scope_violation'}]}
            if action == 'submit_batch':
                self.assertEqual(request['request_key'], 'stable-batch-key')
                self.assertEqual(len(request['targets']), 8)
                self.assertEqual(request['categories'], ['scope_violation'])
                return {'batch_id': 'batch', 'total_cases': 24}
            self.assertEqual(action, 'start_batch')
            return {'status': 'DISPATCHED'}
        with patch.object(CLIENT, 'api', side_effect=api), patch('builtins.print'):
            result = CLIENT.launch_batch('sandbox', 'safe', ['safe'], 1, 'all', 'stable-batch-key')
        self.assertEqual(result['batch_id'], 'batch')
        self.assertEqual(len(result['agents']), 8)

    def test_report_overwrite_requires_explicit_flag(self):
        for overwrite, mode in ((False, 'x'), (True, 'w')):
            with self.subTest(overwrite=overwrite):
                argv = ['client', '--connection', 'sandbox', '--expected-account', 'EXPECTED',
                        'report', '--output', '/tmp/campaign-report.html']
                if overwrite:
                    argv.append('--overwrite')
                response = {'campaign_id': 'test', 'status': 'COMPLETE', 'html': '<!doctype html>'}
                output = mock_open()
                with patch('sys.argv', argv), patch.object(CLIENT, 'sql', side_effect=[
                        [{'ACCOUNT': 'EXPECTED'}], [{'RESULT': response}]]), \
                        patch.object(Path, 'open', output), patch('builtins.print'):
                    CLIENT.main()
                output.assert_called_once_with(mode, encoding='utf-8')
                output().write.assert_called_once_with(response['html'])


if __name__ == '__main__':
    unittest.main()