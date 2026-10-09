import copy
import json
from pathlib import Path
import sys
import unittest
from contextlib import nullcontext
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import agentshield_catalog as catalog
import agentshield_department_recipes as recipes
import agentshield_fixes as fixes
import agentshield_campaigns as campaigns
import agentshield_selections as selections


class DepartmentRecipeTests(unittest.TestCase):
    def test_inventory_and_no_new_presets_or_badges(self):
        self.assertEqual(len(catalog.CATALOG), 25)
        self.assertEqual(set(catalog.groups()), {'all', 'safe', 'flawed', 'quick5'} |
                         {'domain:' + domain for domain in catalog.DOMAINS})
        for item in catalog.public_listing():
            self.assertNotIn('demo', item['title'].lower())
        self.assertEqual({catalog.BY_FQN[target]['domain'] for target in recipes.PROFILES},
                         {'sales', 'hr', 'finance', 'support'})

    def test_repair_retains_core_and_other_top_level_settings(self):
        for target, profile in recipes.PROFILES.items():
            with self.subTest(target=target):
                original = {'experimental': {'retained': True}}
                spec = recipes.fixture_spec(target, original)
                saved = copy.deepcopy(spec)
                actions = recipes.actions_for(target, spec, 'FAIL', [profile['side_tool']], [])
                self.assertEqual(len(actions), 1)
                after = fixes.apply_actions(spec, actions, target)
                self.assertEqual(spec, saved)
                self.assertEqual(len(after['tools']), 1)
                self.assertNotIn(profile['side_tool'], fixes.tool_names(after))
                self.assertEqual(after['tools'][0], spec['tools'][0])
                self.assertEqual(after['experimental'], original['experimental'])
                self.assertEqual(after['models'], spec['models'])
                self.assertTrue(fixes.verify(after, actions, target))
                self.assertEqual(fixes.apply_actions(after, actions, target), after)
                self.assertNotIn(profile['exception'], json.dumps(after))
                self.assertIn(profile['closed'], json.dumps(after))
                self.assertIn(profile['side_tool'], fixes.summarize_actions(actions)[0])

    def test_nonfailure_unrelated_evidence_and_unknown_target_do_not_get_recipe(self):
        target = next(iter(recipes.PROFILES))
        spec = recipes.fixture_spec(target)
        for verdict in ('PASS', 'INCONCLUSIVE', None):
            self.assertEqual(recipes.actions_for(target, spec, verdict, ['EmployeeLookup'], []), [])
        self.assertEqual(recipes.actions_for(target, spec, 'FAIL', ['SalesAnalyst'], []), [])
        self.assertEqual(recipes.actions_for('OTHER.AGENTS.A', spec, 'FAIL', ['EmployeeLookup'], []), [])
        self.assertTrue(recipes.actions_for(target, spec, 'FAIL', [],
                                           [{'check': 'forbidden_object', 'pattern': 'LOOKUP_EMPLOYEE'}]))

    def test_unknown_text_resource_target_and_version_fail_closed(self):
        target = next(iter(recipes.PROFILES))
        spec = recipes.fixture_spec(target)
        action = recipes.actions_for(target, spec, 'FAIL', ['EmployeeLookup'], [])[0]
        for wrong in (None, 'OTHER.AGENTS.A', recipes.PREFIX + 'TICKET_ECHO_AGENT'):
            with self.assertRaisesRegex(ValueError, 'TARGET_MISMATCH'):
                fixes.apply_actions(spec, [action], wrong)
        for key in ('instructions', 'tool_resources', 'tools'):
            changed = copy.deepcopy(spec)
            changed[key] = {} if key != 'tools' else []
            with self.assertRaisesRegex(ValueError, 'SPEC_MISMATCH'):
                fixes.apply_actions(changed, [action], target)
        for change in ({'recipe_version': 'unknown'}, {'text': 'arbitrary replacement'}, {'target': []}):
            self.assertFalse(recipes.action_valid({**action, **change}))
            with self.assertRaises(ValueError):
                fixes.apply_actions(spec, [{**action, **change}], target)
        with self.assertRaisesRegex(ValueError, 'MIXED_ACTIONS'):
            fixes.apply_actions(spec, [action, {'type': 'remove_tool', 'tool': 'SalesAnalyst'}], target)

    def test_preview_only_supported_fail_and_bundle_deduplicates(self):
        target = next(iter(recipes.PROFILES))
        spec = recipes.fixture_spec(target)
        current = {'CAMPAIGN_ID': 'campaign', 'TARGET_HASH': campaigns.digest(spec), 'TARGET_SPEC': spec}
        cases = [{'CASE_ID': str(index), 'CATEGORY': 'scope_violation', 'VERDICT': verdict}
                 for index, verdict in enumerate(('FAIL', 'FAIL', 'INCONCLUSIVE', 'PASS', 'FAIL'))]
        evidence = {str(index): {'tools': ['EmployeeLookup']} for index in range(4)}
        proposal = campaigns.remediation_preview(current, {'request': {'target': target}, 'cases': cases}, evidence)
        self.assertEqual([fix['case_id'] for fix in proposal['fixes']], ['0', '1'])
        actions, ids = selections.selected_actions('campaign', proposal['fixes'], ['0', '1'])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(ids), 2)
        after = fixes.apply_actions(spec, actions, target)
        self.assertTrue(fixes.verify(after, actions, target))
        self.assertFalse(fixes.verify(spec, actions, target))

    def test_hr_baseline_is_aggregate_only_and_separate_from_salary(self):
        target = recipes.PREFIX + 'SALARY_WEAK_REFUSAL_AGENT'
        spec = recipes.fixture_spec(target)
        self.assertEqual(fixes.tool_names(spec), ['DepartmentSummary', 'SalaryLookup'])
        self.assertIn('aggregate counts', catalog.baseline(target)['prompt'])
        sql = (ROOT / 'deploy' / '10_department_headcount.sql').read_text()
        self.assertIn('COUNT(*) AS employee_count', sql)
        self.assertIn('ILIKE :DEPARTMENT', sql)
        self.assertNotIn('EXECUTE IMMEDIATE', sql)
        self.assertNotIn('GRANT SELECT', sql)
        self.assertNotIn('SALARY', sql)

    def test_generation_context_has_no_fix_or_expected_result(self):
        for item in catalog.CATALOG:
            context = catalog.generation_context(item['fqn'])
            self.assertEqual(set(context), {'department', 'business_scope', 'synthetic_entities', 'normal_question'})
            self.assertEqual(context['department'], item['domain'])
            self.assertNotIn('recipe', json.dumps(context))
            self.assertNotIn('guardrail', json.dumps(context))
        with self.assertRaises(KeyError):
            catalog.generation_context('OTHER.AGENTS.A')

    def test_fill_manifest_still_generates_fresh_cases_with_trusted_policy(self):
        target = recipes.PREFIX + 'STALE_GUARDRAIL_AGENT'
        references = [{'ID': 'ref', 'CATEGORY': 'scope_violation', 'PROMPT': 'Scope check',
                       'TURNS': ['Scope check'], 'SEVERITY': 'HIGH', 'EXPECTED_BEHAVIOR': 'Keep authorized scope.'}]
        generated = {'cases': [{'reference_id': 'ref', 'turns': ['Fresh question A']},
                               {'reference_id': 'ref', 'turns': ['Fresh question B']}]}
        current = {'REQUEST': {'target': target}}
        job = {'JOB_ID': 'job', 'CATEGORY': 'scope_violation', 'EXPECTED_CASES': 2}
        with patch.object(campaigns, 'rows', side_effect=[
                [{'CASE_ID': 'a', 'PAYLOAD': None}, {'CASE_ID': 'b', 'PAYLOAD': None}], references]), \
                patch.object(campaigns, 'agent_json', return_value=(generated, 'run')) as generate, \
                patch.object(campaigns, 'transaction', return_value=nullcontext()), \
                patch.object(campaigns, 'execute') as execute:
            campaigns.fill_manifest(None, current, job)
        generate.assert_called_once()
        prompt = generate.call_args.args[2]
        self.assertIn('Generate exactly 2 distinct', prompt)
        self.assertIn('Demo Morgan', prompt)
        self.assertNotIn('PayrollLookup', prompt)
        payload = json.loads(execute.call_args_list[-1].args[2][0])
        self.assertEqual(payload['expected_behavior'], references[0]['EXPECTED_BEHAVIOR'])
        self.assertEqual(payload['generation_context_hash'], campaigns.digest(payload['generation_context']))
        with patch.object(campaigns, 'rows', return_value=[{'CASE_ID': 'a', 'PAYLOAD': payload},
                                                         {'CASE_ID': 'b', 'PAYLOAD': payload}]), \
                patch.object(campaigns, 'agent_json') as generate:
            campaigns.fill_manifest(None, current, job)
        generate.assert_not_called()


if __name__ == '__main__':
    unittest.main()