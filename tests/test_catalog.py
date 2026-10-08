import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import agentshield_catalog as catalog


class CatalogTests(unittest.TestCase):
    def test_inventory(self):
        self.assertEqual(len(catalog.CATALOG), 25)
        self.assertEqual(len(catalog.BY_FQN), 25)
        self.assertEqual(len(catalog.BY_ALIAS), 25)
        self.assertEqual(len(catalog.groups()['safe']), 8)
        self.assertEqual(len(catalog.groups()['flawed']), 17)
        self.assertEqual(sum(item['legacy'] for item in catalog.CATALOG), 3)

    def test_groups_do_not_shadow_legacy_aliases(self):
        self.assertEqual(len(catalog.resolve(['safe'])), 1)
        self.assertEqual(len(catalog.resolve(selected_groups=['safe'])), 8)
        self.assertEqual(len(catalog.resolve(['leaky'], ['all'])), 25)
        self.assertEqual(len(catalog.resolve(selected_groups=['quick5'])), 5)
        for group in catalog.groups():
            self.assertTrue(catalog.resolve(selected_groups=[group]))

    def test_invalid_inputs(self):
        for names, groups in [([], []), (None, ['bad']), (['prod.agents.test'], []), ([None], []), ({}, [])]:
            with self.assertRaises(ValueError):
                catalog.resolve(names, groups)

    def test_specs_and_baselines(self):
        for item in catalog.CATALOG:
            self.assertIn(item['persona'], catalog.PERSONAS)
            self.assertTrue(catalog.baseline(item['fqn'])['prompt'])
            if item['kind'] == 'flawed':
                self.assertTrue(item['expected_failing_categories'])
            if not item['legacy']:
                spec = catalog.agent_spec(item)
                names = {tool['tool_spec']['name'] for tool in spec['tools']}
                self.assertEqual(names, set(spec['tool_resources']))
                for tool in item['tools']:
                    if tool['type'] == 'agent_toolset':
                        self.assertIn(tool['resource']['agent_name'], catalog.BY_FQN)


if __name__ == '__main__':
    unittest.main()