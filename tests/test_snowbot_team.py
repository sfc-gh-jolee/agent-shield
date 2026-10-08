import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import snowbots_setup as setup


class TeamTests(unittest.TestCase):
    def test_roles_and_limits(self):
        payload = setup.setup_payloads('sandbox', 'ABC123', '/tmp/work space')
        self.assertEqual([bot['name'] for bot in payload['bots']], ['Shieldbot', 'Testbot', 'Fixbot'])
        for bot in payload['bots']:
            self.assertEqual(bot['permissionMode'], 'ask')
            self.assertLessEqual(len(bot['description']), 10000)
            self.assertIn('snowbots_crew_control', bot['description'])
            self.assertIn('Delegate exactly once', bot['description'])
            self.assertIn('do not also emit', bot['description'])
        self.assertEqual(sum(member['lead'] for member in payload['group']['members']), 1)
        shield = payload['bots'][0]['description']
        self.assertIn('never show a group question in this branch', shield)
        self.assertIn('NEVER the end of intake', shield)
        self.assertIn('agent_pages', shield)
        for rigor in range(1, 6):
            self.assertIn("'%d (%d tests per category)'" % (rigor, 2 * rigor), shield)
        test = payload['bots'][1]['description']
        self.assertIn('label exactly "shieldbot-<batch_id>.html"', test)
        self.assertIn('ending in .html', test)

    def test_agent_pages_cover_catalog_within_card_limits(self):
        sys.path.insert(0, str(ROOT / 'src'))
        import campaign_client
        from agentshield_catalog import public_listing
        listing = public_listing()
        pages = campaign_client.agent_pages(listing)
        labels = [option['label'] for page in pages for question in page['questions']
                  for option in question['options'] if option['label'] != campaign_client.NONE_OPTION]
        self.assertEqual(sorted(labels), sorted(item['title'] for item in listing))
        questions = [question for page in pages for question in page['questions']]
        for page in pages:
            self.assertLessEqual(len(page['questions']), campaign_client.CARD_QUESTIONS)
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(questions), 5)
        for number, question in enumerate(questions, 1):
            labels = [option['label'] for option in question['options']]
            self.assertLessEqual(len(labels), campaign_client.CARD_AGENTS + 1)
            self.assertEqual(labels[-1], campaign_client.NONE_OPTION)
            self.assertLessEqual(len(question['header']), 12)
            self.assertTrue(question['question'].endswith('(page %d of %d)' % (number, len(questions))))
        for page in pages:
            self.assertEqual(set(page['aliases']) | {campaign_client.NONE_OPTION},
                             {o['label'] for q in page['questions'] for o in q['options']})
        big = [{'alias': 'a%d' % i, 'title': 'T%d' % i, 'domain': 'sales'} for i in range(10)]
        split = campaign_client.agent_pages(big)
        self.assertEqual([q['question'] for p in split for q in p['questions']],
                         ['Sales agents 1/2 (page 1 of 2)', 'Sales agents 2/2 (page 2 of 2)'])
        self.assertEqual(len(split), 1)
        self.assertEqual(len(split[0]['aliases']), 10)

    def test_install_idempotent_preserves_unrelated(self):
        payload = setup.setup_payloads('sandbox', 'ABC123', '/tmp/ws')
        stored = {'bots': [{'id': 'unrelated', 'name': 'Other'}], 'groups': []}

        def api(base, method, path, body=None):
            if method == 'GET':
                return copy.deepcopy(stored)
            kind = path.split('/')[1]
            if method == 'POST':
                actual = {**body, 'notify': True}
                stored[kind].append(actual)
            else:
                actual = next(item for item in stored[kind] if item['id'] == path.split('/')[2])
                actual.update(body)
            return {'bot' if kind == 'bots' else 'group': actual}

        with tempfile.TemporaryDirectory() as folder, patch.object(setup, 'ROOT', Path(folder)), patch.object(setup, 'call', api):
            setup.install('http://localhost', payload)
            setup.install('http://localhost', payload)
            self.assertEqual(len(stored['bots']), 4)
            self.assertEqual(len(stored['groups']), 1)
            self.assertEqual(stored['bots'][0], {'id': 'unrelated', 'name': 'Other'})
            self.assertTrue(stored['bots'][1]['notify'])

    def test_missing_groups_fails_before_mutation(self):
        with patch.object(setup, 'call', return_value={'bots': []}) as api:
            with self.assertRaisesRegex(ValueError, 'nothing changed'):
                setup.install('http://localhost', setup.setup_payloads('sandbox', 'ABC123', '/tmp/ws'))
            self.assertEqual(api.call_count, 1)

    def test_partial_setup_reported(self):
        payload = setup.setup_payloads('sandbox', 'ABC123', '/tmp/ws')
        with tempfile.TemporaryDirectory() as folder, patch.object(setup, 'ROOT', Path(folder)), \
                patch.object(setup, 'call', side_effect=[{'bots': [], 'groups': []}, {'bot': payload['bots'][0]}, OSError('offline')]):
            with self.assertRaisesRegex(RuntimeError, 'changed=agentshield; snapshot='):
                setup.install('http://localhost', payload)