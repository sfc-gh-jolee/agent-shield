"""Deploy additive team remediation support after account and idle checks.

Does not redeploy Cortex agents, tasks, fixtures, or apply any target changes.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from campaign_client import sql
from build_campaigns import build

ROOT = Path(__file__).resolve().parents[1]


def deploy(connection, expected_account):
    identity = sql(connection, 'SELECT CURRENT_ACCOUNT() AS ACCOUNT')[0]['ACCOUNT']
    if identity.upper() != expected_account.upper():
        raise ValueError('ACCOUNT_MISMATCH')
    active = sql(connection, "SELECT COUNT(*) AS N FROM AGENTSHIELD_DB.CORE.CAMPAIGNS "
                 "WHERE STATUS NOT IN ('COMPLETE','PARTIAL','FAILED','CANCELLED')")[0]['N']
    applying = sql(connection, "SELECT COUNT(*) AS N FROM AGENTSHIELD_DB.CORE.REMEDIATION_APPLIES "
                   "WHERE STATUS = 'APPLYING'")[0]['N']
    task_runs = sql(connection, "SELECT NAME, STATE FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.TASK_HISTORY("
                    "SCHEDULED_TIME_RANGE_START => DATEADD('hour', -24, CURRENT_TIMESTAMP()), RESULT_LIMIT => 10000)) "
                    "WHERE DATABASE_NAME = 'AGENTSHIELD_DB' AND SCHEMA_NAME = 'ORCH' "
                    "AND STATE IN ('SCHEDULED','EXECUTING')")
    if active or applying or task_runs:
        raise ValueError('WORKFLOW_BUSY_NO_DEPLOY')
    folder = ROOT / 'build' / 'team-deploy' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True, mode=0o700)
    folder.chmod(0o700)
    before = folder / 'before'
    before.mkdir(mode=0o700)
    sql(connection, "GET @AGENTSHIELD_DB.ORCH.CODE 'file://" + str(before).replace("'", "''") + "'")
    definitions = sql(connection, "SHOW PROCEDURES IN SCHEMA AGENTSHIELD_DB.ORCH")
    selected = [row for row in definitions if row.get('name') in {
        'CAMPAIGN_API', 'PREPARE_CAMPAIGN', 'CAMPAIGN_WORKER', 'REFRESH_CAMPAIGN_REPORT',
        'RERENDER_CAMPAIGN_REPORT', 'FINALIZE_CAMPAIGN', 'PREPARE_REMEDIATION', 'PREPARE_ROLLBACK',
        'APPLY_REMEDIATION', 'REMEDIATION_SELECTION_API'}]
    with (before / 'procedures.json').open('x') as output:
        json.dump(selected, output, indent=2)
    for row in selected:
        signature = row['arguments'].split(' RETURN ')[0]
        ddl = sql(connection, "SELECT GET_DDL('PROCEDURE','AGENTSHIELD_DB.ORCH." + signature + "') AS DDL")
        with (before / (row['name'] + '.sql')).open('x') as output:
            output.write(ddl[0]['DDL'])
    build(folder / 'proposed')
    # Maintenance window required: no new scan starts until this deployment finishes.
    for path in (ROOT / 'deploy' / '09_remediation_selections.sql', folder / 'proposed' / 'deploy_campaigns.sql'):
        result = subprocess.run(['snow', 'sql', '-c', connection, '-f', str(path), '--format', 'json'],
                                capture_output=True, text=True)
        log = folder / (path.stem + '-result.txt')
        with log.open('x') as output:
            log.chmod(0o600)
            output.write(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError('DEPLOY_FAILED: private details in ' + str(log))
    return {'status': 'DEPLOYED', 'snapshot': str(folder), 'targets_changed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--expected-account', required=True)
    args = parser.parse_args()
    print(json.dumps(deploy(args.connection, args.expected_account)))