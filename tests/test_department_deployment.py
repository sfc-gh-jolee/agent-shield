import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import department_fixtures as deploy


class DeploymentTests(unittest.TestCase):
    def test_wrong_account_and_busy_workflow_stop(self):
        with patch.object(deploy, 'sql') as sql:
            with self.assertRaisesRegex(ValueError, 'NOT_THE_APPROVED_SANDBOX'):
                deploy.check_idle('connection', 'OTHER')
            sql.assert_not_called()
        for results, reason in (([{'ACCOUNT': 'OTHER'}], 'ACCOUNT_MISMATCH'),):
            with patch.object(deploy, 'sql', return_value=results):
                with self.assertRaisesRegex(ValueError, reason):
                    deploy.check_idle('connection', 'ZSA35582')
        for position, reason in ((1, 'CAMPAIGN_BUSY'), (2, 'REMEDIATION_OUTSTANDING'), (3, 'TASK_BUSY')):
            responses = [[{'ACCOUNT': 'ZSA35582'}], [], [], []]
            responses[position] = [{'id': 'busy'}]
            with patch.object(deploy, 'sql', side_effect=responses):
                with self.assertRaisesRegex(ValueError, reason):
                    deploy.check_idle('connection', 'ZSA35582')

    def test_install_rejects_unexpected_live_configuration_before_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            manifest = {'account': 'ZSA35582', 'targets': {}}
            for alias in deploy.ALIASES:
                target = deploy.BY_ALIAS[alias]['fqn']
                spec = {'instructions': {'response': 'original'}}
                manifest['targets'][alias] = {'target': target, 'original_hash': deploy.digest(spec)}
                deploy.private_json(folder / (alias + '-original.json'), {'spec': spec, 'grants': []})
            deploy.private_json(folder / 'manifest.json', manifest)
            with patch.object(deploy, 'check_idle'), patch.object(deploy, 'live_spec', return_value={}), \
                    patch.object(deploy, 'studio') as studio, patch.object(deploy.subprocess, 'run') as process:
                with self.assertRaisesRegex(ValueError, 'TARGET_CONFIGURATION_CHANGED'):
                    deploy.install('connection', 'ZSA35582', folder)
                studio.assert_not_called()
                process.assert_not_called()

    def test_private_snapshot_write_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'snapshot.json'
            deploy.private_json(path, {'saved': True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                deploy.private_json(path, {})
            self.assertEqual(json.loads(path.read_text()), {'saved': True})


if __name__ == '__main__':
    unittest.main()