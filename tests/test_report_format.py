"""Formatter layout contracts, evidence totals, and offline export safety."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from html.parser import HTMLParser
import json

ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(ROOT / 'src')] + sys.path):
    import agentshield_report as report
    import agentshield_campaigns as campaigns


class Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links, self.external, self.handlers = set(), [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        if tag == 'a' and attrs.get('href', '').startswith('#'):
            self.links.append(attrs['href'][1:])
        if attrs.get('src') or (tag == 'link' and attrs.get('href')):
            self.external.append(attrs)
        self.handlers.extend(key for key in attrs if key.startswith('on'))


def fixture():
    cases = [{'CASE_ID': 'pass', 'CATEGORY': 'scope', 'VERDICT': 'PASS', 'REASON': 'WITHIN_SCOPE'},
             {'CASE_ID': 'fail', 'CATEGORY': 'scope', 'VERDICT': 'FAIL', 'REASON': 'POLICY_VIOLATION'},
             {'CASE_ID': 'unknown', 'CATEGORY': 'scope', 'VERDICT': 'INCONCLUSIVE', 'REASON': 'INCOMPLETE'},
             {'CASE_ID': 'base', 'CATEGORY': 'baseline', 'VERDICT': 'PASS', 'REASON': 'BASELINE_OK'}]
    return {'campaign_id': 'campaign', 'status': 'PARTIAL',
            'request': {'target': 'DEMO.AGENTS.A', 'role': 'RT_TEST', 'rigor': 2, 'categories': ['scope']},
            'security': {'expected': 4, 'attempted': 3, 'missing': 1,
                         'counts': {'PASS': 1, 'FAIL': 1, 'INCONCLUSIVE': 1, 'unresolved': 0}},
            'cases': cases}


PROPOSAL = {'status': 'MANUAL_REVIEW', 'impact': 'Review only', 'operation': None}


class FormatTests(unittest.TestCase):
    def test_structure_assets_actions_and_provenance(self):
        html = report.render(fixture(), {}, PROPOSAL)
        parsed = Structure()
        parsed.feed(html)
        self.assertTrue(set(parsed.links) <= parsed.ids)
        self.assertFalse(parsed.external)
        self.assertFalse(parsed.handlers)
        self.assertIn('html-report-formatter/10ca153-agent-shield-v1', html)
        self.assertIn('class="topbar"', html)
        self.assertIn('id="theme-button"', html)
        self.assertIn('@media print', html)
        for value in ('fetch(', 'XMLHttpRequest', 'localStorage', 'eval(', 'onclick='):
            self.assertNotIn(value, html)

    def test_action_first_baseline_separate_and_missing_visible(self):
        html = report.render(fixture(), {}, PROPOSAL)
        self.assertLess(html.index('<code>fail</code>'), html.index('<code>pass</code>'))
        self.assertIn('Baseline: 1 passing / 1 recorded', html)
        self.assertIn('3 security cases need review', html)
        self.assertIn('p-done', html)
        self.assertIn('p-pend', html)
        self.assertIn('p-val', html)
        self.assertIn('p-drop', html)

    def test_totals_and_duplicate_ids_fail_closed(self):
        for change in ('counts', 'missing', 'duplicate'):
            summary = fixture()
            if change == 'counts':
                summary['security']['counts']['PASS'] = 10
            elif change == 'missing':
                summary['security']['missing'] = 0
            else:
                summary['cases'][0]['CASE_ID'] = 'fail'
            with self.assertRaisesRegex(ValueError, 'REPORT_EVIDENCE_TOTALS_MISMATCH'):
                report.render(summary, {}, PROPOSAL)

    def test_scripts_disabled_still_show_content(self):
        html = report.render(fixture(), {}, PROPOSAL)
        self.assertIn('.interactive{display:none}', html)
        self.assertIn('.js-enabled .interactive{display:flex}', html)
        self.assertIn('@media screen{.is-hidden{display:none}}', html)
        self.assertIn('POLICY_VIOLATION', html)

    def test_metadata_and_categories_cannot_inject_markup(self):
        summary = fixture()
        summary['campaign_id'] = '</script><script>alert(1)</script>'
        summary['cases'][0]['CATEGORY'] = '" onmouseover="alert(1)'
        html = report.render(summary, {}, PROPOSAL)
        self.assertNotIn('<script>alert(1)', html)
        self.assertNotIn('data-group="\" onmouseover', html)
        data = html.split('id="snowflake-report-metadata">', 1)[1].split('</script>', 1)[0]
        self.assertEqual(json.loads(data)['campaign_id'], summary['campaign_id'])

    def test_nav_matches_section_titles_and_risks_list_methods(self):
        import re
        html = report.render(fixture(), {}, PROPOSAL)
        nav = re.findall(r'<a href="#([^"]+)">([^<]+)</a>', re.search(r'<nav[\s\S]*?</nav>', html).group(0))
        headings = dict(re.findall(r'<section class="section" id="([^"]+)"[\s\S]*?<span class="sectionno">\d+</span>([^<]+)</h2>', html))
        self.assertTrue(nav)
        for key, title in nav:
            self.assertEqual(headings.get(key), title)
        self.assertIn(('risks', 'Risks'), nav)
        self.assertNotIn('Remediation preview', html)
        risks = html[html.index('id="risks"'):html.index('id="retest"')]
        self.assertIn('Potential remediation', risks)
        self.assertIn('Not yet sent to Fixbot', risks)
        self.assertIn('Rerun the exact saved case', risks)
        self.assertLess(risks.index('<code>fail</code>'), risks.index('<code>unknown</code>'))
        self.assertNotIn('<code>pass</code>', risks)

    def test_test_plan_lists_categories_descriptions_and_rigor_counts(self):
        summary = fixture()
        summary['request']['categories'] = ['scope_violation']
        for row in summary['cases'][:3]:
            row['CATEGORY'] = 'scope_violation'
        html = report.render(summary, {}, PROPOSAL)
        plan = html[html.index('id="plan"'):html.index('id="results"')]
        self.assertLess(html.index('id="plan"'), html.index('id="results"'))
        self.assertIn('Scope violations', plan)
        self.assertIn('outside the job the agent was built for', plan)
        self.assertIn('Planned tests', plan)
        self.assertIn('Rigor 2 runs 4 test(s) per selected category', plan)
        self.assertIn('<td>Scope violations</td><td>' + report.CATEGORY_INFO['scope_violation'][1] +
                      '</td><td>4</td><td>1</td><td>4</td><td>3</td>', plan)
        self.assertIn('Baseline (normal question)', plan)
        self.assertIn('>Test plan</a>', html[html.index('<nav'):html.index('</nav>')])

    def test_rerender_does_not_call_models_or_change_results(self):
        current = {'STATUS': 'PARTIAL', 'SUMMARY': fixture(), 'PROPOSAL': PROPOSAL,
                   'SURFACE': {}, 'PARENT_CAMPAIGN_ID': None}
        with patch.object(campaigns, 'campaign', return_value=current), \
                patch.object(campaigns, 'agent_json') as agent, \
                patch.object(campaigns, 'execute') as execute:
            self.assertEqual(campaigns.rerender_report(object(), 'campaign')['model_calls'], 0)
        agent.assert_not_called()
        self.assertEqual(execute.call_count, 1)
        self.assertIn('SET REPORT_HTML = ?', execute.call_args.args[1])


if __name__ == '__main__':
    unittest.main()