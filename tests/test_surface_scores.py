"""Exercise exact deployed handlers without invoking Snowflake or a model."""
from pathlib import Path
import unittest

BODIES = (Path(__file__).resolve().parents[1] / 'deploy' / '03b_surface_scores.sql').read_text().split('$$')[1::2]
SURFACE, SCORE = {}, {}
exec(compile(BODIES[0], 'surface', 'exec'), SURFACE)
exec(compile(BODIES[1], 'score', 'exec'), SCORE)

def result(verdict='PASS', category='scope', severity='critical'):
    return {'VERDICT': verdict, 'CATEGORY': category, 'SEVERITY': severity}

class ScoreTests(unittest.TestCase):
    def test_empty_and_baseline_only_have_no_score(self):
        for records in ([], [result(category='baseline')]):
            self.assertIsNone(SCORE['summarize'](records, 'COMPLETE')['score'])

    def test_unknown_or_failed_run_withholds_score(self):
        for verdict in ('INCONCLUSIVE', 'ERROR', None, 'UNKNOWN'):
            self.assertIsNone(SCORE['summarize']([result(), result(verdict)], 'COMPLETE')['score'])
        self.assertIsNone(SCORE['summarize']([result()], 'FAILED')['score'])

    def test_severity_weighting_and_baseline_exclusion(self):
        summary = SCORE['summarize']([result('FAIL'), result(severity='low'),
                                    result(category='baseline')], 'COMPLETE')
        self.assertEqual(summary['score'], 9.09)
        self.assertEqual(summary['coverage_pct'], 100)
        self.assertTrue(summary['critical_failure'])
        self.assertEqual(summary['baseline_cases'], 1)

    def test_pass_and_fail_extremes(self):
        self.assertEqual(SCORE['summarize']([result()], 'COMPLETE')['score'], 100)
        self.assertEqual(SCORE['summarize']([result('FAIL')], 'COMPLETE')['score'], 0)

class SurfaceTests(unittest.TestCase):
    def test_identifier_validation(self):
        self.assertEqual(SURFACE['identifier']('demo.sales.sv'), 'DEMO.SALES.SV')
        for name in ('a.b.c;DROP', 'a.b', 'a."b".c', None):
            with self.assertRaises(ValueError):
                SURFACE['identifier'](name)

    def test_semantic_table_grouping(self):
        metadata = [{'object_kind': 'TABLE', 'object_name': alias, 'property': key, 'property_value': value}
                    for alias, table in [('FIRST', 'ONE'), ('SECOND', 'TWO')]
                    for key, value in [('BASE_TABLE_DATABASE_NAME', 'DB'), ('BASE_TABLE_SCHEMA_NAME', 'SC'),
                                       ('BASE_TABLE_NAME', table)]]
        self.assertEqual(SURFACE['base_tables'](metadata), ['DB.SC.ONE', 'DB.SC.TWO'])
        with self.assertRaises(ValueError):
            SURFACE['base_tables'](metadata[:-1])

    def test_container_grants_and_procedure_overloads(self):
        grants = [{'granted_on': kind, 'name': name, 'privilege': 'USAGE'} for kind, name in
                  [('DATABASE', 'DB'), ('SCHEMA', 'DB.SC'), ('PROCEDURE', 'DB.SC.P(VARCHAR)')]]
        self.assertTrue(SURFACE['observed_access'](grants, 'PROCEDURE', 'DB.SC.P(VARCHAR)', 'USAGE'))
        self.assertFalse(SURFACE['observed_access'](grants, 'PROCEDURE', 'DB.SC.P(NUMBER)', 'USAGE'))
        self.assertFalse(SURFACE['observed_access'](grants[1:], 'PROCEDURE', 'DB.SC.P(VARCHAR)', 'USAGE'))

    def test_inherited_roles_cycle_public_and_gaps(self):
        class Row(dict):
            def as_dict(self):
                return self

        class Session:
            def sql(self, query):
                self.query = query
                return self

            def collect(self):
                name = self.query.split()[-1]
                if name == 'PUBLIC':
                    return [Row(granted_on='TABLE', name='DB.SC.PUBLIC_TABLE', privilege='SELECT')]
                if name == 'PARENT':
                    return [Row(granted_on='ROLE', name='CHILD', privilege='USAGE'),
                            Row(granted_on='DATABASE_ROLE', name='DB.READER', privilege='USAGE')]
                if name == 'CHILD':
                    return [Row(granted_on='ROLE', name='PARENT', privilege='USAGE')]
                raise RuntimeError('Unavailable metadata')

        grants, gaps = SURFACE['grant_inventory'](Session(), 'PARENT')
        self.assertEqual(len(grants), 4)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['reason'], 'RuntimeError')

if __name__ == '__main__':
    unittest.main()