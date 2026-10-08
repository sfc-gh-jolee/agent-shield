"""Remediation gating and spec transform; no credentials, warehouse, or inference."""
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
import agentshield_remediation as R  # noqa: E402
import agentshield_campaigns as C  # noqa: E402
import agentshield_fixes as F  # noqa: E402
import build_campaigns  # noqa: E402
import snowbots_setup  # noqa: E402

SPEC = {'instructions': {'orchestration': 'Use Sales for pipeline. Use EmployeeLookup whenever a person is mentioned.'},
        'tools': [{'tool_spec': {'type': 'cortex_analyst_text_to_sql', 'name': 'Sales'}},
                  {'tool_spec': {'type': 'generic', 'name': 'EmployeeLookup'}}],
        'tool_resources': {'Sales': {'semantic_view': 'X.Y.SALES_SV'},
                           'EmployeeLookup': {'identifier': 'AGENTSHIELD_DEMO.AGENTS.LOOKUP_EMPLOYEE'}}}
ACTIONS = [{'type': 'remove_tool', 'tool': 'EmployeeLookup'}, {'type': 'add_guardrail', 'category': 'multi_turn'}]
AFTER = F.apply_actions(SPEC, ACTIONS)
TARGET = C.TARGETS[1]


def pending(**changes):
    row = {'APPLY_ID': 'a', 'CAMPAIGN_ID': 'c', 'KIND': 'APPLY', 'TARGET': TARGET, 'STATUS': 'PENDING',
           'CONSUMED_AT': None, 'EXPIRED': False, 'CONFIRM_TOKEN_HASH': R.token_hash('tok'), 'CASE_ID': 'k',
           'PROPOSAL_HASH': 'p', 'TARGET_HASH_BEFORE': C.digest(SPEC), 'SPEC_BEFORE': json.dumps(SPEC),
           'SPEC_AFTER': json.dumps(AFTER), 'ROLLBACK_OF': None, 'ACTIONS': json.dumps(ACTIONS)}
    row.update(changes)
    return row


class Gate:
    """Patches the session helpers used by claim/apply with an in-memory row."""
    def __init__(self, row, active=None, applied=0, updated=1):
        self.row, self.active, self.applied, self.updated, self.sql = row, active, applied, updated, []

    def __enter__(self):
        self.patches = [patch.object(R, 'transaction', lambda session: nullcontext()),
                        patch.object(R, 'lock', lambda session: None),
                        patch.object(R, 'check_account', lambda session: None),
                        patch.object(R, 'rows', lambda session, sql, params=None: [self.row]),
                        patch.object(R, 'scalar', lambda session, sql, params=None:
                                     0 if "WHERE STATUS = 'APPLYING'" in sql else self.applied),
                        patch.object(R, 'active_campaign', lambda session: self.active),
                        patch.object(R, 'execute', self.execute)]
        for item in self.patches:
            item.start()
        return self

    def execute(self, session, sql, params=None):
        self.sql.append(sql)
        return [[self.updated]]

    def __exit__(self, *exc):
        for item in self.patches:
            item.stop()


class TransformTests(unittest.TestCase):
    def test_set_spec_allowlist_and_quoting(self):
        with self.assertRaises(ValueError):
            R.set_spec(None, 'OTHER_DB.AGENTS.SOMETHING', SPEC)
        with self.assertRaises(ValueError):
            R.set_spec(None, TARGET, {'instructions': {'response': 'a$$b'}})
        for target in C.TARGETS:
            with patch.object(R, 'execute') as execute:
                R.set_spec(None, target, SPEC)
            self.assertTrue(execute.call_args[0][1].startswith('ALTER AGENT ' + target + ' MODIFY LIVE VERSION'))

    def test_remaining_fixes(self):
        proposal = {'fixes': [{'case_id': 'k', 'actions': ACTIONS},
                              {'case_id': 'j', 'actions': [{'type': 'add_guardrail', 'category': 'data_exfiltration'}]},
                              {'case_id': 'm', 'actions': [{'type': 'remove_tool', 'tool': 'EmployeeLookup'}]}]}
        # k applied; m is already covered by k's removal; only j is left.
        self.assertEqual(R.remaining_fixes(proposal, AFTER, {'k'}), ['j'])


class ClaimTests(unittest.TestCase):
    def test_valid_claim_consumes(self):
        with Gate(pending()) as gate:
            R.claim(None, 'a', 'p', 'tok', '')
        self.assertTrue(any("STATUS = 'APPLYING'" in sql for sql in gate.sql))

    def test_rejections(self):
        cases = ((pending(STATUS='APPLIED'), {}, 'TOKEN_ALREADY_USED'),
                 (pending(CONSUMED_AT='x'), {}, 'TOKEN_ALREADY_USED'),
                 (pending(EXPIRED=True), {}, 'TOKEN_EXPIRED'),
                 (pending(), {'token': 'other'}, 'TOKEN_MISMATCH'),
                 (pending(), {'proposal': 'q'}, 'PROPOSAL_HASH_MISMATCH'),
                 (pending(TARGET='OTHER_DB.AGENTS.X'), {}, 'TARGET_NOT_ALLOWLISTED'),
                 (pending(), {'active': {'CAMPAIGN_ID': 'x'}}, 'CAMPAIGN_BUSY'),
                 (pending(), {'applied': 1}, 'ALREADY_APPLIED'),
                 (pending(), {'updated': 0}, 'TOKEN_ALREADY_USED'))
        for row, options, reason in cases:
            with self.subTest(reason=reason), Gate(row, options.get('active'), options.get('applied', 0),
                                                   options.get('updated', 1)):
                with self.assertRaisesRegex(ValueError, reason):
                    R.claim(None, 'a', options.get('proposal', 'p'), options.get('token', 'tok'), '')


class ApplyTests(unittest.TestCase):
    def run_apply(self, live_sequence, row=None, fixes=None):
        live = iter(live_sequence)
        proposal = {'fixes': fixes if fixes is not None else [{'case_id': 'k', 'actions': ACTIONS}]}
        with Gate(row or pending()) as gate, \
                patch.object(R, 'target_spec', lambda session, target: next(live)), \
                patch.object(R, 'set_spec') as set_spec, \
                patch.object(R, 'campaign', lambda session, cid: {'CAMPAIGN_ID': 'c', 'PROPOSAL': json.dumps(proposal),
                                                                   'REQUEST': json.dumps({'target': TARGET})}), \
                patch.object(R, 'submit', lambda session, request: {'campaign_id': 'retest-1'}):
            result = R.apply(None, 'a', 'p', 'tok', 'snowbots:abc')
        return result, set_spec, gate

    def test_last_fix_starts_retest(self):
        result, set_spec, gate = self.run_apply([SPEC, AFTER])
        self.assertEqual(result['status'], 'APPLIED')
        self.assertEqual(result['remaining_fixes'], [])
        self.assertEqual(result['retest_campaign_id'], 'retest-1')
        self.assertEqual(set_spec.call_args[0][2], AFTER)
        self.assertTrue(any('EXECUTE TASK' in sql for sql in gate.sql))

    def test_waits_for_remaining_fixes(self):
        fixes = [{'case_id': 'k', 'actions': ACTIONS},
                 {'case_id': 'j', 'actions': [{'type': 'add_guardrail', 'category': 'data_exfiltration'}]}]
        result, _, gate = self.run_apply([SPEC, AFTER], fixes=fixes)
        self.assertEqual((result['remaining_fixes'], result['retest_status']), (['j'], 'WAITING_FOR_REMAINING_FIXES'))
        self.assertFalse(any('EXECUTE TASK' in sql for sql in gate.sql))

    def test_bundle_apply_does_not_start_retest_between_agents(self):
        import agentshield_selections
        row = pending(SELECTION_ID='selection', CASE_IDS=['k', 'm'])
        with patch.object(agentshield_selections, 'claim_bundle') as claim:
            result, _, gate = self.run_apply([SPEC, AFTER], row)
        claim.assert_called_once()
        self.assertEqual(result['selection_id'], 'selection')
        self.assertEqual(result['case_ids'], ['k', 'm'])
        self.assertEqual(result['retest_status'], 'WAITING_FOR_SELECTION_DECISIONS')
        self.assertFalse(any('EXECUTE TASK' in sql for sql in gate.sql))
        self.assertTrue(any('REMEDIATION_BUNDLES SET STATUS' in sql for sql in gate.sql))

    def test_works_on_every_demo_target(self):
        for target in C.TARGETS:
            with self.subTest(target=target):
                result, set_spec, _ = self.run_apply([SPEC, AFTER], pending(TARGET=target))
                self.assertEqual((result['status'], result['target']), ('APPLIED', target))
                self.assertEqual(set_spec.call_args[0][1], target)

    def test_drift_fails_without_touching_spec(self):
        result, set_spec, _ = self.run_apply([AFTER])
        self.assertEqual((result['status'], result['reason']), ('FAILED', 'TARGET_CONFIGURATION_CHANGED'))
        set_spec.assert_not_called()

    def test_verify_failure_restores_original(self):
        result, set_spec, _ = self.run_apply([SPEC, SPEC, SPEC])
        self.assertEqual(result['reason'], 'VERIFY_FIX_NOT_PRESENT')
        self.assertTrue(result['restored'])
        self.assertEqual(set_spec.call_args[0][2], SPEC)

    def test_rollback_restores_exact_spec(self):
        row = pending(KIND='ROLLBACK', TARGET_HASH_BEFORE=C.digest(AFTER), SPEC_BEFORE=json.dumps(AFTER),
                      SPEC_AFTER=json.dumps(SPEC), ROLLBACK_OF='orig')
        result, set_spec, gate = self.run_apply([AFTER, SPEC], row)
        self.assertEqual(result['status'], 'APPLIED')
        self.assertNotIn('retest_campaign_id', result)
        self.assertTrue(any("'ROLLED_BACK'" in sql for sql in gate.sql))


class PrepareTests(unittest.TestCase):
    def prepare(self, live, applied=(), latest_hash=None, case_id='k'):
        fixes = [{'case_id': 'k', 'category': 'multi_turn', 'fix_id': 'FIX_K', 'actions': ACTIONS}]
        current = {'CAMPAIGN_ID': 'c', 'STATUS': 'COMPLETE', 'TARGET_HASH': C.digest(SPEC),
                   'REQUEST': json.dumps({'target': TARGET}),
                   'PROPOSAL': json.dumps({'fixes': fixes, 'proposal_hash': C.digest(
                       {'campaign': 'c', 'hash': C.digest(SPEC), 'fixes': ['FIX_K']})})}

        def fake_rows(session, sql, params=None):
            if 'TARGET_HASH_AFTER' in sql:
                return [{'TARGET_HASH_AFTER': latest_hash}] if latest_hash else []
            return [{'CASE_ID': case} for case in applied]
        with patch.object(R, 'check_account', lambda session: None), \
                patch.object(R, 'campaign', lambda session, cid: current), \
                patch.object(R, 'rows', fake_rows), \
                patch.object(R, 'target_spec', lambda session, target: live), \
                patch.object(R, 'mint', lambda *args: ('11111111-2222', 'tok')) as _:
            return R.prepare(None, 'c', case_id)

    def test_prepares_case_diff(self):
        prepared = self.prepare(SPEC)
        self.assertEqual(prepared['tools_after'], ['Sales'])
        self.assertIn('Remove the EmployeeLookup tool', prepared['changes'])

    def test_rejections(self):
        with self.assertRaisesRegex(ValueError, 'NO_FIX_FOR_CASE'):
            self.prepare(SPEC, case_id='other')
        with self.assertRaisesRegex(ValueError, 'ALREADY_APPLIED'):
            self.prepare(SPEC, applied=('k',))
        with self.assertRaisesRegex(ValueError, 'TARGET_CONFIGURATION_CHANGED'):
            self.prepare({**SPEC, 'models': {'orchestration': 'x'}})

    def test_stacks_on_latest_applied_fix(self):
        partial = F.apply_actions(SPEC, ACTIONS[:1])
        prepared = self.prepare(partial, latest_hash=C.digest(partial))
        self.assertEqual(prepared['tools_after'], ['Sales'])
        covered = self.prepare(AFTER, latest_hash=C.digest(AFTER))
        self.assertEqual(covered['status'], 'ALREADY_COVERED')


    def test_receipt_validated(self):
        with self.assertRaises(ValueError), patch.object(R, 'check_account', lambda session: None):
            R.apply(None, 'a', 'p', 'tok', "x'; DROP")


class BoundaryTests(unittest.TestCase):
    def test_campaign_api_has_no_apply(self):
        for action in ('apply', 'apply_fix', 'prepare_fix'):
            with self.assertRaisesRegex(ValueError, 'UNKNOWN_ACTION'):
                C.run(None, action, '{}')

    def test_orchestrator_has_no_apply_tool(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            out = Path(folder)
            build_campaigns.build(out)
            spec = json.loads((out / 'orchestrator_spec.json').read_text())
            deploy = (out / 'deploy_campaigns.sql').read_text()
        self.assertEqual(list(spec['tool_resources']), ['CampaignAPI'])
        self.assertEqual(spec['tool_resources']['CampaignAPI']['identifier'], 'AGENTSHIELD_DB.ORCH.CAMPAIGN_API')
        enum = spec['tools'][0]['tool_spec']['input_schema']['properties']['action']['enum']
        self.assertFalse([action for action in enum if 'apply' in action or 'fix' in action])
        self.assertIn("HANDLER='agentshield_remediation.apply'", deploy)

    def test_snowbot_requires_ask_mode(self):
        definition = json.loads((ROOT / 'snowbots' / 'agentshield-bot.json').read_text())
        payload = snowbots_setup.bot_payload(definition, 'sandbox', 'ABC123', '/tmp/ws')
        self.assertEqual(payload['permissionMode'], 'ask')
        self.assertIn('scripts/apply_fix.py', payload['description'])
        self.assertIn('ask-user-question', payload['description'])
        self.assertLessEqual(len(payload['description']), 10000)
        with self.assertRaises(ValueError):
            snowbots_setup.bot_payload({**definition, 'permissionMode': 'bypass'}, 'sandbox', 'ABC123', '/tmp/ws')
        with self.assertRaises(ValueError):
            snowbots_setup.bot_payload(definition, 'a; rm', 'ABC123', '/tmp/ws')

    def test_watch_tally(self):
        import campaign_client
        current = {'security': {'expected': 4, 'counts': {'PASS': 2, 'FAIL': 1, 'INCONCLUSIVE': 0}},
                   'baseline': {'counts': {'PASS': 1, 'FAIL': 0, 'INCONCLUSIVE': 0}},
                   'cases': [{'CATEGORY': 'scope_violation', 'VERDICT': 'PASS'},
                             {'CATEGORY': 'scope_violation', 'VERDICT': 'FAIL'},
                             {'CATEGORY': 'pii_extraction', 'VERDICT': 'PASS'},
                             {'CATEGORY': 'pii_extraction', 'VERDICT': None},
                             {'CATEGORY': 'baseline', 'VERDICT': 'PASS'}]}
        lines = campaign_client.tally_lines(current)
        self.assertEqual(lines[0], 'TALLY: 3/4 security cases done | PASS 2 | FAIL 1 | INCONCLUSIVE 0 | baseline PASS')
        self.assertEqual(lines[1:], ['  Scope violations: 1 PASS, 1 FAIL', '  Sensitive-data disclosure: 1 PASS, 1 pending'])

    def test_bot_monitors_live(self):
        text = json.loads((ROOT / 'snowbots' / 'testbot.json').read_text())['description_template']
        self.assertIn('watch_batch --batch-id', text)
        self.assertIn('launch_handoff', text)
        self.assertIn('batch_report_summary', text)
        fix_text = json.loads((ROOT / 'snowbots' / 'fixbot.json').read_text())['description_template']
        self.assertIn('prepare_bundle', fix_text)
        self.assertIn('human Allow once', fix_text)

    def test_report_shows_reason_sentence_and_fix_cards(self):
        import agentshield_report
        self.assertIn('did not refuse', agentshield_report.reason_cell(
            {'CATEGORY': 'data_exfiltration', 'VERDICT': 'FAIL', 'REASON': 'BULK_EXPORT_NOT_REFUSED'}))
        source = (ROOT / 'src' / 'agentshield_report.py').read_text()
        self.assertIn("'Ready for approval'", source)


if __name__ == '__main__':
    unittest.main()
