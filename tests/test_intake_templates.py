"""Intake contract and additive reference-library regression tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


CAMPAIGNS = module('intake_campaigns', 'src/agentshield_campaigns.py')
BUILDER = module('intake_builder', 'scripts/build_campaigns.py')
LIBRARY = module('template_builder', 'scripts/build_templates.py')


class IntakeTests(unittest.TestCase):
    def options(self, missing=()):
        categories = json.loads(LIBRARY.SOURCE.read_text())['categories']
        with patch.object(CAMPAIGNS, 'rows', return_value=[{'CATEGORY': key} for key in categories if key not in missing]):
            return CAMPAIGNS.options(object())

    def test_options_no_template_counts_and_stable_numbers(self):
        result = self.options()
        self.assertNotIn('TEMPLATES', json.dumps(result))
        self.assertEqual([row['choice'] for row in result['categories']], list(range(1, 9)))
        self.assertEqual(result['categories'][-1]['CATEGORY'], 'indirect_injection')
        self.assertEqual(self.options(['scope_violation'])['categories'][-1]['choice'], 8)
        self.assertEqual(set(result['target_aliases'].values()), set(CAMPAIGNS.TARGETS))

    def test_rigor_choices_and_custom_budget(self):
        result = self.options()
        categories = [row['CATEGORY'] for row in result['categories']]
        self.assertNotIn('presets', result)
        choices = result['rigor_choices_all_categories']
        self.assertEqual([row['rigor'] for row in choices], list(range(1, 6)))
        self.assertEqual([row['total_cases'] for row in choices], [17, 33, 49, 65, 81])
        for choice in choices:
            validated = CAMPAIGNS.validate_request(CAMPAIGNS.TARGETS[0], 'RT_SALES_REP', categories, choice['rigor'], categories)
            self.assertEqual(validated['expected_security_cases'] + 1, choice['total_cases'])
        CAMPAIGNS.validate_request(CAMPAIGNS.TARGETS[0], 'RT_SALES_REP', categories, 5, categories)
        with self.assertRaisesRegex(ValueError, 'RIGOR_MUST_BE_INTEGER_1_TO_5'):
            CAMPAIGNS.validate_request(CAMPAIGNS.TARGETS[0], 'RT_SALES_REP', categories, 6, categories)
        with self.assertRaisesRegex(ValueError, 'CASE_BUDGET_EXCEEDED'):
            CAMPAIGNS.validate_request(CAMPAIGNS.TARGETS[0], 'RT_SALES_REP', categories + list('abc'), 5,
                                       categories + list('abc'))

    def test_build_has_intake_guards_and_nonstarting_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('builtins.print'):
                BUILDER.build(Path(directory))
            spec = json.loads((Path(directory) / 'orchestrator_spec.json').read_text())
        instructions = spec['instructions']
        self.assertEqual(len(instructions['sample_questions']), 3)
        self.assertTrue(all('do not start' in item['question'] for item in instructions['sample_questions']))
        self.assertNotIn('Quick', json.dumps(instructions['sample_questions']) + instructions['response'])
        self.assertIn('never mention Quick', instructions['orchestration'])
        self.assertIn('ask for BOTH missing', instructions['orchestration'])
        self.assertIn('must NEVER call submit or start', instructions['orchestration'])
        self.assertIn('Never display template counts', instructions['orchestration'])
        self.assertIn('two numbered questions', instructions['orchestration'])
        self.assertIn('immediately call report_summary in the same turn', instructions['orchestration'])


class LibraryTests(unittest.TestCase):
    def test_exact_expansion_preserves_demo_default(self):
        templates = LIBRARY.load_templates()
        self.assertEqual(len(templates), 65)
        self.assertEqual(len({row['ID'] for row in templates}), 65)
        self.assertTrue(all(row['DEMO'] is False for row in templates))
        self.assertTrue(all(row['SEVERITY'] == 'high' and row['EXPECTED_BEHAVIOR'] for row in templates))
        for category, group in json.loads(LIBRARY.SOURCE.read_text())['categories'].items():
            self.assertEqual(group['existing_count'] + sum(row['CATEGORY'] == category for row in templates), 15)

    def test_multi_turn_and_indirect_surfaces(self):
        templates = LIBRARY.load_templates()
        for row in templates:
            if row['CATEGORY'] == 'multi_turn':
                self.assertTrue(2 <= len(row['TURNS']) <= 3)
                self.assertEqual(row['PROMPT'], row['TURNS'][0])
        indirect = json.loads(LIBRARY.SOURCE.read_text())['categories']['indirect_injection']['cases']
        self.assertEqual(sum(row['surface'] == 'existing_poisoned_playbook' for row in indirect), 3)
        self.assertEqual(sum(row['surface'] == 'quoted_document' for row in indirect), 11)

    def test_duplicate_or_bad_library_fails_before_sql(self):
        for mutation in ('duplicate', 'short_multiturn', 'count'):
            source = json.loads(LIBRARY.SOURCE.read_text())
            if mutation == 'duplicate':
                source['categories']['prompt_injection']['cases'][1]['turns'] = source['categories']['prompt_injection']['cases'][0]['turns']
            elif mutation == 'short_multiturn':
                source['categories']['multi_turn']['cases'][0]['turns'] = ['one']
            else:
                source['categories']['prompt_injection']['cases'].pop()
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'invalid.json'
                path.write_text(json.dumps(source))
                with self.assertRaises(ValueError):
                    LIBRARY.load_templates(path)

    def test_migration_is_insert_only_conflict_checked_and_locked(self):
        sql = LIBRARY.migration(LIBRARY.load_templates())
        self.assertNotIn('WHEN MATCHED', sql)
        self.assertNotIn('DELETE FROM', sql)
        self.assertIn('NOT EQUAL_NULL', sql)
        self.assertIn('HAVING COUNT(*) > 1', sql)
        self.assertIn('BEGIN TRANSACTION', sql)
        self.assertIn('CAMPAIGN_MUTEX SET VERSION', sql)
        self.assertIn('ROLLBACK', sql)
        self.assertIn('INTO :conflicts', sql)

    def test_reference_payload_remains_bounded(self):
        templates = LIBRARY.load_templates()
        for category in {row['CATEGORY'] for row in templates}:
            payload = json.dumps([row for row in templates if row['CATEGORY'] == category])
            # Reserve half the current input limit for inherited references.
            self.assertLess(len(payload), 22500)


if __name__ == '__main__':
    unittest.main()