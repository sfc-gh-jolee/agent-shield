from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
import json
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import agentshield_selections as S
import agentshield_campaigns as C
from agentshield_fixes import fix_id


def finding(case_id, category='scope_violation'):
    actions = [{'type': 'add_guardrail', 'category': category}]
    return {'case_id': case_id, 'category': category, 'actions': actions, 'fix_id': fix_id('campaign', case_id, actions)}


class SelectionTests(unittest.TestCase):
    def test_normalization_and_empty(self):
        item = {'campaign_id': 'campaign', 'proposal_hash': 'hash', 'case_ids': ['b', 'a', 'a']}
        self.assertEqual(S.normalize_items([item])[0]['case_ids'], ['a', 'b'])
        for items in ([], [item, item], [{**item, 'case_ids': []}], [{**item, 'extra': True}],
                      [{**item, 'case_ids': [None]}]):
            with self.assertRaises(ValueError):
                S.normalize_items(items)

    def test_deduplicate_full_recipes(self):
        actions, ids = S.selected_actions('campaign', [finding('a'), finding('b')], ['b', 'a'])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(ids), 2)
        with self.assertRaisesRegex(ValueError, 'NO_FIX_FOR_CASE'):
            S.selected_actions('campaign', [finding('a')], ['other'])
        with self.assertRaisesRegex(ValueError, 'FIX_HASH_MISMATCH'):
            S.selected_actions('campaign', [{**finding('a'), 'fix_id': 'wrong'}], ['a'])

    def test_unknown_action_rejected(self):
        actions = [{'type': 'arbitrary_sql', 'sql': 'bad'}]
        with self.assertRaisesRegex(ValueError, 'UNKNOWN_FIX_ACTION'):
            S.selected_actions('campaign', [{'case_id': 'a', 'actions': actions,
                               'fix_id': fix_id('campaign', 'a', actions)}], ['a'])

    def test_selection_checks_membership_hash_and_eligibility(self):
        agent = {'campaign_id': 'campaign', 'proposal_hash': 'hash', 'fixes': [
                 {'case_id': 'a', 'eligibility': 'ELIGIBLE'}, {'case_id': 'b', 'eligibility': 'STALE'}]}
        base = {'campaign_id': 'campaign', 'proposal_hash': 'hash', 'case_ids': ['a']}
        for item, error in (({**base, 'campaign_id': 'other'}, 'BATCH_MEMBERSHIP_MISMATCH'),
                            ({**base, 'proposal_hash': 'old'}, 'PROPOSAL_HASH_MISMATCH'),
                            ({**base, 'case_ids': ['b']}, 'FINDING_NOT_ELIGIBLE')):
            with patch.object(C, 'transaction', return_value=nullcontext()), patch.object(C, 'lock'), \
                    patch.object(C, 'rows', return_value=[]), patch.object(C, 'execute') as execute, \
                    patch.object(S, 'remediation_options', return_value={'agents': [agent]}):
                with self.assertRaisesRegex(ValueError, error):
                    S.select_fixes(None, {'batch_id': 'batch', 'request_key': 'selection-key', 'items': [item]})
                execute.assert_not_called()

    def test_no_apply_api(self):
        with patch.object(S.R, 'check_account'):
            for action in ('apply', 'apply_fix', 'execute'):
                with self.assertRaisesRegex(ValueError, 'UNKNOWN_SELECTION_ACTION'):
                    S.run(None, action, '{}')

    def test_inconclusive_proposal_requires_manual_review(self):
        target = C.TARGETS[0]
        live = {'tools': [{'tool_spec': {'name': 'Sales'}}]}
        proposal = {'proposal_hash': 'hash', 'fixes': [finding('a')]}
        with patch.object(S, 'batch', return_value={'STATUS': 'PARTIAL', 'REQUEST': [{}]}), \
                patch.object(C, 'rows', side_effect=[[{'CAMPAIGN_ID': 'campaign'}],
                    [{'CASE_ID': 'a', 'CATEGORY': 'scope_violation', 'VERDICT': 'INCONCLUSIVE'}]]), \
                patch.object(S.R, 'checked_campaign', return_value=(
                    {'BATCH_ID': 'batch', 'TARGET_HASH': C.digest(live)}, proposal, target)), \
                patch.object(C, 'target_spec', return_value=live), \
                patch.object(S.R, 'expected_hash', return_value=C.digest(live)), \
                patch.object(S.R, 'applied_cases', return_value=set()):
            options = S.remediation_options(None, 'batch')
        self.assertEqual(options['agents'][0]['fixes'][0]['eligibility'], 'MANUAL_REVIEW')
        self.assertEqual(options['agents'][0]['manual_review'][0]['CASE_ID'], 'a')

    def test_comparison_keeps_unselected_and_baseline_results(self):
        row = {'CAMPAIGN_ID': 'parent', 'TARGET': C.TARGETS[0], 'STATUS': 'APPLIED',
               'CASE_IDS': ['a'], 'ACTIONS': finding('a')['actions'], 'RETEST_CAMPAIGN_ID': 'child'}
        before = {'security': {'counts': {'FAIL': 2}}, 'baseline': {'counts': {'PASS': 1}},
                  'cases': [{'CASE_ID': 'a', 'VERDICT': 'FAIL'}, {'CASE_ID': 'b', 'VERDICT': 'FAIL'},
                            {'CASE_ID': 'base', 'VERDICT': 'PASS'}]}
        after = {'status': 'COMPLETE', 'security': {'counts': {'FAIL': 1}}, 'baseline': {'counts': {'FAIL': 1}},
                 'cases': [{'CASE_ID': 'a2', 'PARENT_CASE_ID': 'a', 'CATEGORY': 'scope_violation', 'VERDICT': 'PASS'},
                           {'CASE_ID': 'b2', 'PARENT_CASE_ID': 'b', 'CATEGORY': 'pii_extraction', 'VERDICT': 'FAIL'},
                           {'CASE_ID': 'base2', 'PARENT_CASE_ID': 'base', 'CATEGORY': 'baseline', 'VERDICT': 'FAIL'}]}
        with patch.object(S, 'selection', return_value={'BATCH_ID': 'batch', 'STATUS': 'RETEST_QUEUED'}), \
                patch.object(S, 'bundles', return_value=[row]), \
                patch.object(C, 'campaign', return_value={'PROPOSAL': {'fixes': [finding('a'), finding('b')]}}), \
                patch.object(C, 'status', side_effect=[before, after]):
            result = S.selection_status(None, 'selection')['bundles'][0]
        self.assertEqual(result['unselected_case_ids'], ['b'])
        self.assertEqual([case['selected'] for case in result['comparison']['cases']], [True, False, False])
        self.assertEqual(result['comparison']['cases'][-1]['after'], 'FAIL')


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.target = C.TARGETS[0]
        self.live = {'tools': [{'tool_spec': {'name': 'Sales', 'type': 'cortex_analyst_text_to_sql'}}]}
        self.fixes = [finding('a'), finding('b', 'pii_extraction')]
        self.actions, self.fix_ids = S.selected_actions('campaign', self.fixes, ['a'])
        self.row = {'SELECTION_ID': 'selection', 'CAMPAIGN_ID': 'campaign', 'TARGET': self.target,
                    'STATUS': 'PENDING', 'SOURCE_HASH': 'hash', 'CASE_IDS': ['a'],
                    'FIX_IDS': self.fix_ids, 'ACTIONS': self.actions, 'APPLY_ID': None}

    def prepare(self, live=None, row=None):
        with patch.object(C, 'transaction', return_value=nullcontext()), patch.object(C, 'lock'), \
                patch.object(S, 'selection', return_value={'STATUS': 'OPEN'}), \
                patch.object(S, 'bundle', return_value=row or self.row), \
                patch.object(S.R, 'checked_campaign', return_value=({'TARGET_HASH': C.digest(self.live)},
                    {'proposal_hash': 'hash', 'fixes': self.fixes, 'impact': 'May restrict responses'}, self.target)), \
                patch.object(C, 'active_campaign', return_value=None), patch.object(C, 'scalar', return_value=0), \
                patch.object(C, 'target_spec', return_value=live or self.live), \
                patch.object(S.R, 'expected_hash', return_value=C.digest(self.live)), \
                patch.object(S.R, 'mint', return_value=('apply-id', 'secret')) as mint, \
                patch.object(C, 'execute') as execute:
            return S.prepare_bundle(None, 'selection', 'campaign'), mint, execute

    def test_selected_subset_combined_preview(self):
        result, mint, execute = self.prepare()
        self.assertEqual(result['case_ids'], ['a'])
        self.assertEqual(result['unselected_case_ids'], ['b'])
        self.assertEqual(result['tools_before'], result['tools_after'])
        self.assertEqual(mint.call_count, 1)
        self.assertEqual(result['confirm_token'], 'secret')
        self.assertEqual(mint.call_args.args[6], self.actions)

    def test_stale_and_resolved_rejected(self):
        with self.assertRaisesRegex(ValueError, 'TARGET_CONFIGURATION_CHANGED'):
            self.prepare(live={**self.live, 'instructions': {'response': 'changed'}})
        with self.assertRaisesRegex(ValueError, 'BUNDLE_ALREADY_RESOLVED'):
            self.prepare(row={**self.row, 'STATUS': 'SKIPPED'})

    def test_reprepare_invalidates_old_token(self):
        _, _, execute = self.prepare(row={**self.row, 'STATUS': 'PREPARED', 'APPLY_ID': 'old'})
        self.assertTrue(any('SUPERSEDED' in call.args[1] and call.args[2] == ['old']
                            for call in execute.call_args_list))

    def test_denial_invalidates_and_never_applies(self):
        row = {**self.row, 'STATUS': 'PREPARED', 'APPLY_ID': 'old'}
        with patch.object(C, 'transaction', return_value=nullcontext()), patch.object(C, 'lock'), \
                patch.object(S, 'selection', return_value={'STATUS': 'OPEN'}), \
                patch.object(S, 'bundle', return_value=row), patch.object(C, 'execute') as execute, \
                patch.object(S, 'selection_status', return_value={}), patch.object(S.R, 'set_spec') as alter:
            S.skip_bundle(None, 'selection', 'campaign')
            alter.assert_not_called()
            self.assertTrue(any('SUPERSEDED' in call.args[1] for call in execute.call_args_list))
            self.assertEqual(execute.call_args.args[2][0], 'SKIPPED')

    def test_wrong_agent_cannot_claim_prepared_bundle(self):
        pending = {'SELECTION_ID': 'selection', 'CAMPAIGN_ID': 'campaign', 'APPLY_ID': 'other'}
        with patch.object(S, 'selection', return_value={'STATUS': 'OPEN'}), \
                patch.object(S, 'bundle', return_value={**self.row, 'STATUS': 'PREPARED', 'APPLY_ID': 'prepared'}):
            with self.assertRaisesRegex(ValueError, 'BUNDLE_NOT_PREPARED'):
                S.claim_bundle(None, pending)


class RetestTests(unittest.TestCase):
    def test_all_skipped_can_close_during_unrelated_scan(self):
        with patch.object(C, 'transaction', side_effect=lambda session: nullcontext()), patch.object(C, 'lock'), \
                patch.object(S, 'selection', return_value={'STATUS': 'OPEN'}), \
                patch.object(S, 'bundles', return_value=[{'STATUS': 'SKIPPED'}]), \
                patch.object(C, 'active_campaign', return_value={'CAMPAIGN_ID': 'unrelated'}) as active, \
                patch.object(C, 'execute'), patch.object(C, 'submit') as submit, \
                patch.object(S, 'selection_status', return_value={}):
            self.assertEqual(S.finish_selection(None, 'selection')['retest_dispatch'], 'NOT_NEEDED')
            active.assert_not_called()
            submit.assert_not_called()

    def test_pending_decisions_never_launch(self):
        with patch.object(C, 'transaction', return_value=nullcontext()), patch.object(C, 'lock'), \
                patch.object(S, 'selection', return_value={'STATUS': 'OPEN'}), \
                patch.object(S, 'bundles', return_value=[{'STATUS': 'APPLIED'}, {'STATUS': 'PREPARED'}]), \
                patch.object(C, 'submit') as submit:
            with self.assertRaisesRegex(ValueError, 'SELECTION_DECISIONS_PENDING'):
                S.finish_selection(None, 'selection')
            submit.assert_not_called()

    def test_only_applied_retested_and_retry_does_not_submit(self):
        live = {'tools': ['unchanged']}
        members = [{'STATUS': 'APPLIED', 'CAMPAIGN_ID': 'parent', 'TARGET': 'target', 'APPLY_ID': 'apply'},
                   {'STATUS': 'SKIPPED', 'CAMPAIGN_ID': 'skipped'}]
        updated = [{**members[0], 'RETEST_CAMPAIGN_ID': 'child'}, members[1]]
        with patch.object(C, 'transaction', side_effect=lambda session: nullcontext()), patch.object(C, 'lock'), \
                patch.object(S, 'selection', return_value={'STATUS': 'OPEN'}) as selection, \
                patch.object(S, 'bundles', side_effect=[members, updated]), \
                patch.object(C, 'active_campaign', return_value=None), patch.object(C, 'scalar', return_value=0), \
                patch.object(C, 'rows', return_value=[{'TARGET_HASH_AFTER': C.digest(live)}]), \
                patch.object(C, 'target_spec', return_value=live), \
                patch.object(C, 'campaign', return_value={'STATUS': 'QUEUED', 'REQUEST': {'target': 'target'}}), \
                patch.object(C, 'submit', return_value={'campaign_id': 'child'}) as submit, \
                patch.object(C, 'execute') as execute, patch.object(S, 'selection_status', return_value={}):
            result = S.finish_selection(None, 'selection')
            self.assertEqual(result['retest_dispatch'], 'DISPATCHED')
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(submit.call_args.args[1]['parent_campaign_id'], 'parent')
            self.assertTrue(submit.call_args.kwargs['_locked'])
            selection.return_value = {'STATUS': 'RETEST_QUEUED'}
            with patch.object(S, 'bundles', return_value=updated):
                S.finish_selection(None, 'selection')
            self.assertEqual(submit.call_count, 1)

    def test_dispatch_failure_is_resumable(self):
        members = [{'STATUS': 'APPLIED', 'RETEST_CAMPAIGN_ID': 'child'}]
        with patch.object(C, 'transaction', return_value=nullcontext()), patch.object(C, 'lock'), \
                patch.object(S, 'selection', return_value={'STATUS': 'RETEST_QUEUED'}), \
                patch.object(S, 'bundles', return_value=members), patch.object(C, 'submit') as submit, \
                patch.object(C, 'campaign', return_value={'STATUS': 'QUEUED'}), \
                patch.object(C, 'execute', side_effect=RuntimeError('dispatch failed')), \
                patch.object(S, 'selection_status', return_value={}):
            result = S.finish_selection(None, 'selection')
            self.assertEqual(result['retest_dispatch'], 'NOT_STARTED_RETRY_FINISH_SELECTION')
            submit.assert_not_called()