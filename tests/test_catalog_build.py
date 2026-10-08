import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('catalog_builder', ROOT / 'scripts' / 'build_catalog.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class FixtureTests(unittest.TestCase):
    def test_fixture_deploy_is_additive_and_has_no_dynamic_sql(self):
        sql = builder.fixture_sql()
        self.assertNotIn('CREATE OR REPLACE', sql)
        self.assertNotIn('EXECUTE IMMEDIATE', sql)
        self.assertNotIn('EXTERNAL ACCESS', sql)
        self.assertNotIn('TO ROLE PUBLIC', sql)
        self.assertIn('AGENTSHIELD_FIXTURE_READER COPY CURRENT GRANTS', sql)
        self.assertIn("Unsupported fixture filter", sql)

    def test_all_new_tool_resources_are_defined(self):
        sql = builder.fixture_sql()
        for item in builder.CATALOG:
            for tool in item['tools']:
                for key in ('semantic_view', 'search_service', 'identifier'):
                    resource = tool['resource'].get(key)
                    if resource and not any(legacy in resource for legacy in ('.SALES.', '.DOCS.', '.HR.HR_SV', '.AGENTS.LOOKUP_EMPLOYEE')):
                        self.assertIn(resource, sql)


if __name__ == '__main__':
    unittest.main()