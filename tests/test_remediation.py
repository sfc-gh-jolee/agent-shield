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
import build_campaigns  # noqa: E402
import snowbots_setup  # noqa: E402

SPEC = {'instructions': {'orchestration': 'sales'},
        'tools': [{'tool_spec': {'type': 'generic', 'name': 'Sales'}},
                  {'tool_spec': {'type': 'generic', 'name': 'EmployeeLookup'}}],
        'tool_resources': {'Sales': {'identifier': 'X.Y.SALES'},
                           'EmployeeLookup': {'identifier': 'AGENTSHIELD_DEMO.AGENTS.LOOKUP_EMPLOYEE'}}}


def pending(**changes):
    row = {'APPLY_ID': 'a', 'CAMPAIGN_ID': 'c', 'KIND': 'APPLY', 'TARGET': R.APPLY_TARGET, 'STATUS': 'PENDING',
           'CONSUMED_AT': None, 'EXPIRED': False, 'CONFIRM_TOKEN_HASH': R.token_hash('tok'),
           'PROPOSAL_HASH': 'p', 'TARGET_HASH_BEFORE': C.digest(SPEC), 'SPEC_BEFORE': json.dumps(SPEC),
           'SPEC_AFTER': json.dumps(R.without_tool(SPEC)), 'ROLLBACK_OF': None}
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
                        patch.object(R, 'scalar', lambda session, sql, params=None: self.applied),
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
    def test_removes_only_employee_lookup(self):
        after = R.without_tool(SPEC)
        self.assertEqual(R.tool_names(after), ['Sales'])
        self.assertEqual(set(after['tool_resources']), {'Sales'})
        self.assertEqual(after['instructions'], SPEC['instructions'])
        self.assertIn('EmployeeLookup', SPEC['tool_resources'])  # input untouched

    def test_precondition(self):
        for spec in (R.without_tool(SPEC), {**SPEC, 'tool_resources': {'Sales': {}}},
                     {**SPEC, 'tools': SPEC['tools'] + [SPEC['tools'][1]]}):
            with self.assertRaises(ValueError):
                R.without_tool(spec)

    def test_set_spec_allowlist_and_quoting(self):
        with self.assertRaises(ValueError):
            R.set_spec(None, C.TARGETS[0], SPEC)
        with self.assertRaises(ValueError):
            R.set_spec(None, R.APPLY_TARGET, {'instructions': {'response': 'a$$b'}})
        with patch.object(R, 'execute') as execute:
            R.set_spec(None, R.APPLY_TARGET, SPEC)
        self.assertTrue(execute.call_args[0][1].startswith('ALTER AGENT ' + R.APPLY_TARGET + ' MODIFY LIVE VERSION'))


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
                 (pending(TARGET=C.TARGETS[0]), {}, 'TARGET_NOT_ALLOWLISTED'),
                 (pending(), {'active': {'CAMPAIGN_ID': 'x'}}, 'CAMPAIGN_BUSY'),
                 (pending(), {'applied': 1}, 'ALREADY_APPLIED'),
                 (pending(), {'updated': 0}, 'TOKEN_ALREADY_USED'))
        for row, options, reason in cases:
            with self.subTest(reason=reason), Gate(row, options.get('active'), options.get('applied', 0),
                                                   options.get('updated', 1)):
                with self.assertRaisesRegex(ValueError, reason):
                    R.claim(None, 'a', options.get('proposal', 'p'), options.get('token', 'tok'), '')


class ApplyTests(unittest.TestCase):
    def run_apply(self, live_sequence, row=None):
        live = iter(live_sequence)
        with Gate(row or pending()) as gate, \
                patch.object(R, 'target_spec', lambda session, target: next(live)), \
                patch.object(R, 'set_spec') as set_spec, \
                patch.object(R, 'campaign', lambda session, cid: {'REQUEST': json.dumps({'target': R.APPLY_TARGET})}), \
                patch.object(R, 'submit', lambda session, request: {'campaign_id': 'retest-1'}):
            result = R.apply(None, 'a', 'p', 'tok', 'snowbots:abc')
        return result, set_spec, gate

    def test_apply_then_retest(self):
        result, set_spec, gate = self.run_apply([SPEC, R.without_tool(SPEC)])
        self.assertEqual(result['status'], 'APPLIED')
        self.assertEqual(result['retest_campaign_id'], 'retest-1')
        self.assertEqual(set_spec.call_args[0][2], R.without_tool(SPEC))
        self.assertTrue(any('EXECUTE TASK' in sql for sql in gate.sql))

    def test_drift_fails_without_touching_spec(self):
        result, set_spec, _ = self.run_apply([R.without_tool(SPEC)])
        self.assertEqual((result['status'], result['reason']), ('FAILED', 'TARGET_CONFIGURATION_CHANGED'))
        set_spec.assert_not_called()

    def test_verify_failure_restores_original(self):
        result, set_spec, _ = self.run_apply([SPEC, SPEC, SPEC])
        self.assertEqual(result['reason'], 'VERIFY_TOOL_STILL_PRESENT')
        self.assertTrue(result['restored'])
        self.assertEqual(set_spec.call_args[0][2], SPEC)

    def test_rollback_restores_exact_spec(self):
        fixed = R.without_tool(SPEC)
        row = pending(KIND='ROLLBACK', TARGET_HASH_BEFORE=C.digest(fixed), SPEC_BEFORE=json.dumps(fixed),
                      SPEC_AFTER=json.dumps(SPEC), ROLLBACK_OF='orig')
        result, set_spec, gate = self.run_apply([fixed, SPEC], row)
        self.assertEqual(result['status'], 'APPLIED')
        self.assertNotIn('retest_campaign_id', result)
        self.assertTrue(any("'ROLLED_BACK'" in sql for sql in gate.sql))

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
        text = json.loads((ROOT / 'snowbots' / 'agentshield-bot.json').read_text())['description_template']
        self.assertIn('LIVE MONITORING', text)
        self.assertIn('launch --agent', text)


if __name__ == '__main__':
    unittest.main()
