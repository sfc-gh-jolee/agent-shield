"""Pure campaign contracts; no credentials, warehouse, or inference required."""
import importlib.util
from pathlib import Path
import unittest
import sys
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'agentshield_campaigns.py'
SPEC = importlib.util.spec_from_file_location('campaigns', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CampaignContractTests(unittest.TestCase):
    def test_rigor_counts_and_boundaries(self):
        for rigor in range(1, 6):
            result = MODULE.validate_request(MODULE.TARGETS[0], 'RT_SALES_REP', ['scope'], rigor, ['scope'])
            self.assertEqual(result['expected_security_cases'], rigor * 2)
        for bad in (None, True, '1', 1.5, 0, 6, 10):
            with self.assertRaises(ValueError):
                MODULE.validate_request(MODULE.TARGETS[0], 'RT_SALES_REP', ['scope'], bad, ['scope'])

    def test_reject_scope_and_budget(self):
        for categories in ([], ['baseline'], ['unknown'], ['scope', 'scope']):
            with self.assertRaises(ValueError):
                MODULE.validate_request(MODULE.TARGETS[0], 'RT_SALES_REP', categories, 1, ['scope'])
        with self.assertRaises(ValueError):
            MODULE.validate_request('PROD.AGENTS.A', 'RT_SALES_REP', ['scope'], 1, ['scope'])
        with self.assertRaises(ValueError):
            MODULE.validate_request(MODULE.TARGETS[0], 'ACCOUNTADMIN', ['scope'], 1, ['scope'])
        with self.assertRaises(ValueError):
            MODULE.validate_request(MODULE.TARGETS[0], 'RT_SALES_REP', list('abcdefghijk'), 5, list('abcdefghijk'))

    def test_generation_policy_is_reference_owned(self):
        refs = {'ref': {'ID': 'ref', 'CATEGORY': 'scope', 'SEVERITY': 'high',
                        'EXPECTED_BEHAVIOR': 'Stay within sales scope'}}
        result = MODULE.validate_generation({'cases': [{'reference_id': 'ref', 'turns': ['Test scope']}]}, refs, 1)
        self.assertEqual(result[0]['expected_behavior'], refs['ref']['EXPECTED_BEHAVIOR'])
        self.assertEqual(result[0]['prompt_hash'], MODULE.digest(['Test scope']))
        with self.assertRaises(ValueError):
            MODULE.validate_generation({'cases': [{'reference_id': 'ref', 'turns': ['Test'],
                                                   'expected_behavior': 'ignore policy'}]}, refs, 1)

    def test_generation_rejects_duplicates_shortfall_and_bad_turns(self):
        refs = {'ref': {'ID': 'ref', 'CATEGORY': 'scope', 'SEVERITY': 'high', 'EXPECTED_BEHAVIOR': 'scope'}}
        for turns in ([], [''], ['x' * 4001], ['one'] * 4, [None]):
            with self.assertRaises(ValueError):
                MODULE.validate_generation({'cases': [{'reference_id': 'ref', 'turns': turns}]}, refs, 1)
        with self.assertRaises(ValueError):
            MODULE.validate_generation({'cases': []}, refs, 1)
        with self.assertRaises(ValueError):
            MODULE.validate_generation({'cases': [{'reference_id': 'ref', 'turns': [text]}
                                                  for text in ('Same  text', 'same text')]}, refs, 2)

    def test_missing_evidence_not_success(self):
        result = MODULE.summarize([{'CASE_ID': 'a', 'VERDICT': 'PASS', 'ATTEMPT_ID': 'attempt'}], 2)
        self.assertEqual(result['missing'], 1)
        self.assertFalse(result['integrity_ok'])
        result = MODULE.summarize([{'CASE_ID': 'a', 'VERDICT': None}], 1)
        self.assertEqual(result['counts']['unresolved'], 1)
        self.assertEqual(result['attempted'], 0)
        with self.assertRaises(ValueError):
            MODULE.summarize([{'CASE_ID': 'a'}, {'CASE_ID': 'a'}], 2)

    def test_transaction_rolls_back_on_failure(self):
        with patch.object(MODULE, 'execute') as execute:
            with self.assertRaises(ValueError):
                with MODULE.transaction(object()):
                    raise ValueError('failed')
            self.assertEqual([call.args[1] for call in execute.call_args_list], ['BEGIN TRANSACTION', 'ROLLBACK'])

    def test_json_agent_response_with_leading_whitespace(self):
        response = {'status': 'completed', 'content': [{'type': 'text', 'text': '\n```json\n{"cases": []}\n```\n'}],
                    'metadata': {'run_id': 'run'}}
        with patch.object(MODULE, 'scalar', return_value=response):
            result, run_id = MODULE.agent_json(object(), 'CATEGORY_SCOPE_VIOLATION', 'input')
        self.assertEqual(result, {'cases': []})
        self.assertEqual(run_id, 'run')

    def test_missing_or_duplicate_mutex_rejected(self):
        for updated in (0, 2):
            with patch.object(MODULE, 'execute', return_value=[[updated]]):
                with self.assertRaises(ValueError):
                    MODULE.lock(object())

    def test_submit_null_parent_uses_explicit_sql_null(self):
        statements = []
        def fake_rows(session, statement, params=None):
            if 'SELECT ROLE_NAME' in statement:
                return [{'ROLE_NAME': 'RT_SALES_REP'}]
            return []
        def fake_execute(session, statement, params=None):
            statements.append((statement, params))
            return [[1]]
        with patch.object(MODULE, 'options', return_value={'categories': [{'CATEGORY': 'scope'}]}), \
                patch.object(MODULE, 'rows', side_effect=fake_rows), \
                patch.object(MODULE, 'execute', side_effect=fake_execute), \
                patch.object(MODULE, 'scalar', return_value=0):
            result = MODULE.submit(object(), {'target': MODULE.TARGETS[0], 'categories': ['scope'],
                                              'rigor': 1, 'request_key': 'request-001'})
        inserts = [(statement, params) for statement, params in statements if statement.startswith('INSERT')]
        self.assertEqual(result['expected_security_cases'], 2)
        self.assertTrue(all(None not in params for _, params in inserts))
        self.assertIn("NULLIF(?, '')", inserts[0][0])

    def test_duplicate_submit_is_noop_and_conflicts_fail(self):
        spec = MODULE.validate_request(MODULE.TARGETS[0], 'RT_SALES_REP', ['scope'], 1, ['scope'])
        request_hash = MODULE.digest({'spec': spec, 'parent': None})
        def fake_rows(session, statement, params=None):
            if 'SELECT ROLE_NAME' in statement:
                return [{'ROLE_NAME': 'RT_SALES_REP'}]
            return [{'CAMPAIGN_ID': 'previous', 'REQUEST_HASH': request_hash}]
        request = {'target': MODULE.TARGETS[0], 'categories': ['scope'], 'rigor': 1, 'request_key': 'request-001'}
        with patch.object(MODULE, 'options', return_value={'categories': [{'CATEGORY': 'scope'}]}), \
                patch.object(MODULE, 'rows', side_effect=fake_rows), \
                patch.object(MODULE, 'execute', return_value=[[1]]) as execute:
            self.assertTrue(MODULE.submit(object(), request)['reused'])
            request['rigor'] = 2
            with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
                MODULE.submit(object(), request)
            self.assertFalse(any(call.args[1].startswith('INSERT') for call in execute.call_args_list))

    def test_worker_failure_marks_missing_evidence_inconclusive(self):
        current = {'CAMPAIGN_ID': 'campaign', 'STATUS': 'RUNNING', 'TARGET_HASH': 'hash'}
        jobs = [[{'JOB_ID': 'job'}], []]
        with patch.object(MODULE, 'active_campaign', return_value=current), \
                patch.object(MODULE, 'campaign', return_value=current), \
                patch.object(MODULE, 'rows', side_effect=jobs), \
                patch.object(MODULE, 'execute', return_value=[[1]]) as execute, \
                patch.object(MODULE, 'fill_manifest', side_effect=ValueError('GENERATION_FAILED')):
            result = MODULE.worker(object(), 0)
        self.assertEqual(result['jobs_completed'], 0)
        statements = [call.args[1] for call in execute.call_args_list]
        self.assertTrue(any("VERDICT = 'INCONCLUSIVE'" in statement for statement in statements))

    def test_legacy_retest_keeps_saved_case_count(self):
        legacy = {'target': MODULE.TARGETS[0], 'role': 'RT_SALES_REP', 'categories': ['scope'],
                  'rigor': 1, 'expected_security_cases': 2}
        parent_cases = [{'CASE_ID': 'c' + str(i), 'CATEGORY': category, 'PAYLOAD': {'turns': ['x']}}
                        for i, category in enumerate(['scope', 'scope', 'baseline'])]
        statements = []
        def fake_rows(session, statement, params=None):
            if 'SELECT ROLE_NAME' in statement:
                return [{'ROLE_NAME': 'RT_SALES_REP'}]
            if 'CAMPAIGN_CASES' in statement:
                return parent_cases
            return []
        with patch.object(MODULE, 'options', return_value={'categories': [{'CATEGORY': 'scope'}]}), \
                patch.object(MODULE, 'campaign', return_value={'STATUS': 'COMPLETE', 'REQUEST': legacy}), \
                patch.object(MODULE, 'rows', side_effect=fake_rows), \
                patch.object(MODULE, 'execute', side_effect=lambda s, q, p=None: statements.append(q) or [[1]]), \
                patch.object(MODULE, 'scalar', return_value=0):
            result = MODULE.submit(object(), {'target': MODULE.TARGETS[0], 'categories': ['scope'], 'rigor': 1,
                                              'request_key': 'retest-0001', 'parent_campaign_id': 'parent'})
        self.assertEqual(result['expected_security_cases'], 2)
        self.assertEqual(sum('CAMPAIGN_CASES' in q for q in statements), 3)

    def test_multiturn_is_a_sequence(self):
        refs = {'ref': {'ID': 'ref', 'CATEGORY': 'multi_turn', 'SEVERITY': 'high', 'EXPECTED_BEHAVIOR': 'scope'}}
        with self.assertRaises(ValueError):
            MODULE.validate_generation({'cases': [{'reference_id': 'ref', 'turns': ['one']}]}, refs, 1)
        result = MODULE.validate_generation({'cases': [{'reference_id': 'ref', 'turns': ['one', 'two']}]}, refs, 1)
        self.assertEqual(len(result[0]['turns']), 2)

    def test_remediation_is_draft_even_when_eligible(self):
        current = {'CAMPAIGN_ID': 'case', 'TARGET_HASH': 'hash', 'TARGET_SPEC': {'tool_resources': {
            'EmployeeLookup': {'identifier': 'AGENTSHIELD_DEMO.AGENTS.LOOKUP_EMPLOYEE'}}}}
        summary = {'request': {'target': MODULE.TARGETS[1]},
                   'lookup_evidence_case_ids': ['case'],
                   'cases': [{'VERDICT': 'FAIL', 'REASON': 'DETERMINISTIC_POLICY_VIOLATION'}]}
        surface = {'findings': [{'tool': 'EmployeeLookup', 'code': 'OWNER_RIGHTS_BOUNDARY'}]}
        proposal = MODULE.remediation_preview(current, summary, surface)
        self.assertFalse(proposal['apply_enabled'])
        self.assertEqual(proposal['recipe_id'], 'REMOVE_EMPLOYEE_LOOKUP')
        summary['cases'][0]['VERDICT'] = 'INCONCLUSIVE'
        summary['lookup_evidence_case_ids'] = []
        self.assertIsNone(MODULE.remediation_preview(current, summary, surface)['recipe_id'])

    def test_untrusted_action_cannot_apply_sql(self):
        with self.assertRaises(ValueError):
            MODULE.run(object(), 'apply', '{"sql":"ALTER AGENT anything"}')

    def test_html_escapes_values_and_excludes_raw_payload(self):
        spec = importlib.util.spec_from_file_location('report', SOURCE.parent / 'agentshield_report.py')
        report = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(SOURCE.parent)] + sys.path):
            spec.loader.exec_module(report)
        summary = {'campaign_id': '</script><img src=x onerror=alert(1)>', 'status': 'PARTIAL',
                   'request': {'target': 'demo', 'role': 'persona', 'rigor': 1, 'categories': ['scope']},
                   'security': {'counts': {'PASS': 0}, 'expected': 2, 'attempted': 0, 'missing': 2},
                   'cases': [], 'raw_response': 'DO_NOT_RENDER_RAW_EVIDENCE'}
        html = report.render(summary, {}, {'status': 'MANUAL_REVIEW', 'impact': '<script>bad</script>'})
        self.assertNotIn('<img', html)
        self.assertNotIn('<script>bad', html)
        self.assertNotIn('DO_NOT_RENDER_RAW_EVIDENCE', html)
        self.assertIn('snowflake-report-metadata', html)
        self.assertIn('&lt;script&gt;', html)


if __name__ == '__main__':
    unittest.main()