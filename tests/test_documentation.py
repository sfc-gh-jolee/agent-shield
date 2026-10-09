"""Offline checks for operator documentation; never connect to Snowflake."""
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'


def prose(path):
    return re.sub(r'```.*?```', '', path.read_text(), flags=re.DOTALL)


def anchors(path):
    headings = re.findall(r'^#{1,6}\s+(.+)$', prose(path), flags=re.MULTILINE)
    return {re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-') for heading in headings}


class DocumentationTests(unittest.TestCase):
    def documents(self):
        return [ROOT / 'README.md', *DOCS.glob('*.md')]

    def test_local_links_and_fragments_resolve(self):
        for document in self.documents():
            for destination in re.findall(r'\[[^\]]+\]\(([^\s)]+)\)', prose(document)):
                link = urlsplit(destination)
                if link.scheme or link.netloc:
                    continue
                target = (document.parent / unquote(link.path)).resolve() if link.path else document
                with self.subTest(document=document.name, link=destination):
                    self.assertTrue(target.exists(), str(target))
                    if link.fragment and target.suffix == '.md':
                        self.assertIn(unquote(link.fragment), anchors(target))

    def test_retired_entry_points_and_branding_are_absent(self):
        for name in ('deploy.sql', 'agent_spec.yaml', 'docs/AGENTSHIELD_V2_CHANGES.html',
                     'docs/V2_VALIDATION.md'):
            self.assertFalse((ROOT / name).exists(), name)
        for document in self.documents():
            with self.subTest(document=document.name):
                self.assertNotRegex(document.read_text(), r'(?i)Shield Bot v2|AGENTSHIELD_V2_CHANGES|V2_VALIDATION')

    def test_fresh_install_dependency_order(self):
        runbook = (DOCS / 'SNOWBOTS_DEMO.md').read_text()
        fresh = runbook.split('## Fresh sandbox installation', 1)[1].split('## Start SnowBots', 1)[0]
        steps = ('-f deploy/01_demo_fixtures.sql', '-f deploy/02_core.sql',
                 '-f deploy/03_procs.sql', '-f deploy/03b_surface_scores.sql',
                 'python3 scripts/build_campaigns.py', 'python3 scripts/build_templates.py',
                 '-f deploy/04_campaign_schema.sql', '-f deploy/08_campaign_batches.sql',
                 '-f deploy/06_remediation.sql', '-f deploy/09_remediation_selections.sql',
                 '-f build/campaigns/expand_templates.sql', '-f build/campaigns/deploy_campaigns.sql',
                 '-f build/campaigns/deploy_agents.sql', 'python3 scripts/build_catalog.py --deploy',
                 '-f deploy/05_campaign_tasks.sql')
        positions = [fresh.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))

    def test_documented_command_files_exist(self):
        generated = {'build/campaigns/expand_templates.sql', 'build/campaigns/deploy_campaigns.sql',
                     'build/campaigns/deploy_agents.sql'}
        for document in self.documents():
            commands = document.read_text()
            paths = re.findall(r'python3 ((?:scripts/)[\w/]+\.py)', commands)
            paths += re.findall(r'-f ((?:deploy/|build/campaigns/)[\w/]+\.sql)', commands)
            for path in paths:
                with self.subTest(document=document.name, path=path):
                    self.assertTrue(path in generated or (ROOT / path).is_file(), path)


if __name__ == '__main__':
    unittest.main()