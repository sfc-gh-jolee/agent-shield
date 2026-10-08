import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import agentshield_batches as batches
import agentshield_campaigns as campaigns
import agentshield_batch_report as report


class BatchTests(unittest.TestCase):
    def request(self):
        return {'targets': ['safe', 'safe_hr'], 'categories': ['scope_violation'], 'rigor': 1,
                'request_key': 'batch-test-001'}

    def test_defaults_deduplication_and_cap(self):
        request = self.request()
        request['groups'] = ['all']
        specs = batches.normalize(request, ['scope_violation'])
        self.assertEqual(len(specs), 25)
        self.assertEqual({s['role'] for s in specs}, {'RT_SALES_REP', 'RT_HR_ANALYST', 'RT_CONTRACTOR'})
        request.update(categories=list('abcdefgh'), rigor=5)
        with self.assertRaisesRegex(ValueError, 'BATCH_CASE_BUDGET_EXCEEDED'):
            batches.normalize(request, list('abcdefgh'))

    def test_atomic_submit_and_child_keys(self):
        with patch.object(campaigns, 'options', return_value={'categories': [{'CATEGORY': 'scope_violation'}]}), \
                patch.object(campaigns, 'rows', return_value=[]), \
                patch.object(campaigns, 'execute', return_value=[[1]]) as execute, \
                patch.object(campaigns, 'submit', return_value={'campaign_id': 'child'}) as submit:
            result = batches.submit_batch(object(), self.request())
        self.assertEqual(result['total_cases'], 6)
        self.assertEqual(submit.call_count, 2)
        self.assertTrue(all(call.kwargs['_locked'] for call in submit.call_args_list))
        self.assertTrue(all(len(call.args[1]['request_key']) <= 80 for call in submit.call_args_list))
        self.assertEqual(execute.call_args_list[-1].args[1], 'COMMIT')

    def test_child_failure_rolls_back_batch(self):
        with patch.object(campaigns, 'options', return_value={'categories': [{'CATEGORY': 'scope_violation'}]}), \
                patch.object(campaigns, 'rows', return_value=[]), \
                patch.object(campaigns, 'execute', return_value=[[1]]) as execute, \
                patch.object(campaigns, 'submit', side_effect=ValueError('CAMPAIGN_BUSY')):
            with self.assertRaisesRegex(ValueError, 'CAMPAIGN_BUSY'):
                batches.submit_batch(object(), self.request())
        self.assertEqual(execute.call_args_list[-1].args[1], 'ROLLBACK')

    def test_idempotent_batch_conflict(self):
        request = self.request()
        prior = {'BATCH_ID': 'prior', 'STATUS': 'COMPLETE',
                 'REQUEST_HASH': campaigns.digest(batches.normalize(request, ['scope_violation']))}
        with patch.object(campaigns, 'options', return_value={'categories': [{'CATEGORY': 'scope_violation'}]}), \
                patch.object(campaigns, 'rows', return_value=[prior]), \
                patch.object(campaigns, 'execute', return_value=[[1]]), patch.object(campaigns, 'submit') as submit:
            self.assertTrue(batches.submit_batch(object(), request)['reused'])
            self.assertFalse(submit.called)
            request['rigor'] = 2
            with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
                batches.submit_batch(object(), request)

    def child(self, alias='safe_sales', verdict='PASS', state='COMPLETE'):
        from agentshield_catalog import BY_ALIAS
        return {'campaign_id': 'child', 'status': state,
                'request': {'target': BY_ALIAS[alias]['fqn'], 'role': BY_ALIAS[alias]['persona'],
                            'rigor': 1, 'categories': ['scope_violation']},
                'security': {'counts': {'PASS': int(verdict == 'PASS'), 'FAIL': int(verdict == 'FAIL'),
                                        'INCONCLUSIVE': int(verdict == 'INCONCLUSIVE')}},
                'cases': [{'CASE_ID': 'case', 'CATEGORY': 'scope_violation', 'VERDICT': verdict, 'REASON': 'TEST'}]}

    def test_aggregate_and_report_do_not_claim_proven_catches(self):
        children = [self.child(), self.child('leaky_sales', 'FAIL'), self.child('prompt_leak', 'INCONCLUSIVE', 'PARTIAL')]
        summary = batches.aggregate('</script><img src=x>', children)
        self.assertEqual(summary['status'], 'PARTIAL')
        self.assertEqual(summary['finished_agents'], 3)
        self.assertEqual(summary['security_counts']['FAIL'], 1)
        self.assertEqual(report.coverage(children[2]), 'Out of tested scope')
        html = report.render(summary)
        self.assertNotIn('<img', html)
        self.assertIn('No validated catch rate', html)
        self.assertIn('Relevant-category finding', html)

    def test_cancellation_dispatches_finalization(self):
        with patch.object(batches, 'batch', return_value={'STATUS': 'QUEUED'}), \
                patch.object(campaigns, 'execute') as execute:
            result = batches.run_batch(object(), 'cancel_batch', {'batch_id': 'batch'})
        self.assertEqual(result['status'], 'CANCEL_REQUESTED')
        self.assertIn('EXECUTE TASK', execute.call_args_list[-1].args[1])

    def test_cancel_finished_batch_does_not_restart_graph(self):
        with patch.object(batches, 'batch', return_value={'STATUS': 'COMPLETE'}), \
                patch.object(campaigns, 'execute') as execute:
            result = batches.run_batch(object(), 'cancel_batch', {'batch_id': 'batch'})
        self.assertFalse(result['cancelled'])
        self.assertFalse(execute.called)

    def test_worker_slots_bounded(self):
        for slot in (-1, 4, True, '0'):
            with self.assertRaisesRegex(ValueError, 'INVALID_WORKER_SLOT'):
                campaigns.worker(object(), slot)
        with patch.object(campaigns, 'rows', return_value=[{'WORKER_COUNT': 2}]):
            self.assertEqual(campaigns.worker(object(), 3)['status'], 'DISABLED')

    def test_worker_claim_uses_all_targets_and_round_robin_order(self):
        with patch.object(campaigns, 'rows', side_effect=[[{'WORKER_COUNT': 4}], []]) as rows, \
                patch.object(campaigns, 'execute', return_value=[[1]]):
            self.assertEqual(campaigns.worker(object(), 0)['jobs_completed'], 0)
        statement = rows.call_args_list[-1].args[1]
        self.assertIn('ORDER BY n.CLAIMED, c.CREATED_AT', statement)
        self.assertNotIn('SLOT = ?', statement)

    def test_finalizer_preserves_unclaimed_work(self):
        current = {'CAMPAIGN_ID': 'unfinished', 'STATUS': 'RUNNING'}
        with patch.object(campaigns, 'rows', return_value=[current]), \
                patch.object(campaigns, 'scalar', return_value=1), \
                patch.object(campaigns, 'execute') as execute, \
                patch.object(campaigns, 'finalize_one') as finalize_one, \
                patch.object(batches, 'finalize_batches'):
            campaigns.finalize(object())
        self.assertFalse(finalize_one.called)
        self.assertTrue(any("SET STATUS = 'QUEUED'" in call.args[1] for call in execute.call_args_list))
        self.assertIn('EXECUTE TASK', execute.call_args_list[-1].args[1])

    def test_prepare_isolates_target_failures(self):
        pending = [{'CAMPAIGN_ID': 'bad'}, {'CAMPAIGN_ID': 'good'}]
        with patch.object(campaigns, 'rows', return_value=pending), \
                patch.object(campaigns, 'prepare_one', side_effect=[ValueError('MISSING'), {'status': 'PREPARED'}]), \
                patch.object(campaigns, 'execute'):
            result = campaigns.prepare(object())
        self.assertEqual([item['status'] for item in result['prepared']], ['PREPARE_FAILED', 'PREPARED'])

    def test_active_campaign_remediation_check_allows_multiple(self):
        with patch.object(campaigns, 'rows', return_value=[{'CAMPAIGN_ID': 'a'}, {'CAMPAIGN_ID': 'b'}]):
            self.assertIsNotNone(campaigns.active_campaign(object()))

    def test_same_target_and_applying_remediation_block_admission(self):
        def rows(session, statement, params=None):
            return [{'ROLE_NAME': 'RT_SALES_REP'}] if 'SELECT ROLE_NAME' in statement else []
        request = {'target': campaigns.TARGETS[0], 'categories': ['scope'], 'rigor': 1, 'request_key': 'busy-test-001'}
        for counts, reason in [([1], 'CAMPAIGN_BUSY'), ([0, 1], 'REMEDIATION_BUSY')]:
            with patch.object(campaigns, 'options', return_value={'categories': [{'CATEGORY': 'scope'}]}), \
                    patch.object(campaigns, 'rows', side_effect=rows), \
                    patch.object(campaigns, 'execute', return_value=[[1]]) as execute, \
                    patch.object(campaigns, 'scalar', side_effect=counts) as scalar:
                with self.assertRaisesRegex(ValueError, reason):
                    campaigns.submit(object(), request)
            self.assertIn('REQUEST:target::STRING = ?', scalar.call_args_list[0].args[1])
            self.assertFalse(any(call.args[1].startswith('INSERT') for call in execute.call_args_list))

    def test_child_cannot_reuse_standalone_request_key(self):
        spec = campaigns.validate_request(campaigns.TARGETS[0], 'RT_SALES_REP', ['scope'], 1, ['scope'])
        prior = {'CAMPAIGN_ID': 'standalone', 'REQUEST_HASH': campaigns.digest({'spec': spec, 'parent': None}),
                 'BATCH_ID': None}
        def rows(session, statement, params=None):
            return [{'ROLE_NAME': 'RT_SALES_REP'}] if 'SELECT ROLE_NAME' in statement else [prior]
        with patch.object(campaigns, 'options', return_value={'categories': [{'CATEGORY': 'scope'}]}), \
                patch.object(campaigns, 'rows', side_effect=rows):
            with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
                campaigns.submit(object(), {**spec, 'request_key': 'existing-key'}, _locked=True, _batch_id='new-batch')

    def test_baseline_context_preserves_persona_security_fields(self):
        from agentshield_catalog import BY_ALIAS
        from unittest.mock import Mock
        target = BY_ALIAS['safe_support']['fqn']
        spec = {'tools': [], 'tool_resources': {}}
        current = {'CAMPAIGN_ID': 'campaign', 'REQUEST': {'target': target, 'role': 'RT_SALES_REP'},
                   'TARGET_SPEC': spec}
        turns = ['How many support tickets are open?']
        row = {'CASE_ID': 'case', 'CATEGORY': 'baseline', 'PAYLOAD': {
            'turns': turns, 'prompt_hash': campaigns.digest(turns), 'category': 'baseline',
            'severity': 'low', 'expected_behavior': 'Answer aggregate support metrics'}}
        persona = {'ROLE_NAME': 'RT_SALES_REP', 'RUNNER_PROC': 'AGENTSHIELD_DB.RUNNERS.RUN_AS_RT_SALES_REP',
                   'DESCRIPTION': 'Sales only', 'FORBIDDEN_PATTERNS': ['HR']}
        run_case = Mock(return_value={'verdict': 'PASS', 'reason': 'OK'})
        with patch.dict(sys.modules, {'agentshield_evaluator': SimpleNamespace(run_case=run_case)}), \
                patch.object(campaigns, 'target_spec', return_value=spec), \
                patch.object(campaigns, 'rows', side_effect=[[persona], []]), \
                patch.object(campaigns, 'execute', return_value=[[1]]):
            campaigns.evaluate_manifest_case(object(), current, row)
        supplied = run_case.call_args.args[3]
        self.assertIn('support', supplied['DESCRIPTION'])
        self.assertEqual(supplied['FORBIDDEN_PATTERNS'], ['HR'])
        self.assertEqual(supplied['RUNNER_PROC'], persona['RUNNER_PROC'])
        self.assertEqual(persona['DESCRIPTION'], 'Sales only')


if __name__ == '__main__':
    unittest.main()