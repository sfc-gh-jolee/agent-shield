import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'src'))
import agentshield_handoffs as H
import campaign_client as client

AVAILABLE = ['scope_violation', 'pii_extraction']


class HandoffTests(unittest.TestCase):
    def handoff(self, intent='launch'):
        return H.create(['safe', 'leaky'], [], 1, AVAILABLE, AVAILABLE, 'test-handoff-key', intent)

    def test_canonical_and_tamper(self):
        value = self.handoff()
        self.assertEqual(value['total_cases'], 10)
        self.assertEqual(value, H.validate(value, AVAILABLE))
        for changed in ({'total_cases': 1}, {'rigor': 2}, {'targets': ['OTHER.AGENTS.X']},
                        {'intent': 'apply'}, {'version': True}, {'extra': 'unexpected'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                H.validate({**value, **changed}, AVAILABLE)

    def test_setup_never_submits(self):
        with patch.object(client, 'api', return_value={'categories': [{'CATEGORY': name} for name in AVAILABLE]}) as api:
            self.assertFalse(client.launch_handoff('sandbox', self.handoff('setup'))['started'])
            self.assertEqual(api.call_count, 1)

    def test_retry_running_does_not_redispatch(self):
        with patch.object(client, 'api', side_effect=[{'categories': [{'CATEGORY': name} for name in AVAILABLE]},
                         {'batch_id': 'batch', 'reused': True}, {'status': 'RUNNING'}]) as api:
            self.assertEqual(client.launch_handoff('sandbox', self.handoff())['status'], 'RUNNING')
            self.assertEqual(api.call_args_list[1].args[2]['request_key'], 'test-handoff-key')
            self.assertNotIn('start_batch', [call.args[1] for call in api.call_args_list])

    def test_intake_key_persisted_and_conflict(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(client, 'HANDOFFS', Path(folder)), \
                patch.object(client, 'api', return_value={'categories': [{'CATEGORY': name} for name in AVAILABLE]}):
            result = client.intake('sandbox', 'safe', [], 1, None, 'intake-key')
            self.assertEqual(result, json.loads((Path(folder) / 'intake-key.json').read_text()))
            self.assertEqual(result, client.intake('sandbox', 'safe', [], 1, None, 'intake-key'))
            with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
                client.intake('sandbox', 'safe', [], 2, None, 'intake-key')

    def test_budget(self):
        with self.assertRaisesRegex(ValueError, 'BATCH_CASE_BUDGET_EXCEEDED'):
            H.create([], ['all'], 5, list(client.LABELS)[:-1], list(client.LABELS)[:-1], 'budget-key')