"""Private snapshots and explicit lifecycle for the four approved sandbox fixtures.

Snapshot is read-only in Snowflake. Install and restore never run through intake.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from campaign_client import sql
from agentshield_campaigns import digest
from agentshield_catalog import BY_ALIAS, CATALOG
from agentshield_department_recipes import fixture_spec, PROFILES, VERSION

ALIASES = ('leaky_sales', 'salary_weak_refusal', 'stale_guardrail', 'ticket_echo')


def private_json(path, value):
    with path.open('x') as output:
        path.chmod(0o600)
        json.dump(value, output, indent=2)


def check_idle(connection, expected_account):
    if expected_account.upper() != 'ZSA35582':
        raise ValueError('NOT_THE_APPROVED_SANDBOX')
    if sql(connection, 'SELECT CURRENT_ACCOUNT() AS ACCOUNT')[0]['ACCOUNT'] != expected_account.upper():
        raise ValueError('ACCOUNT_MISMATCH')
    if sql(connection, "SELECT CAMPAIGN_ID FROM AGENTSHIELD_DB.CORE.CAMPAIGNS WHERE STATUS NOT IN "
           "('COMPLETE','PARTIAL','FAILED','CANCELLED')"):
        raise ValueError('CAMPAIGN_BUSY')
    if sql(connection, "SELECT APPLY_ID FROM AGENTSHIELD_DB.CORE.REMEDIATION_APPLIES "
           "WHERE KIND='APPLY' AND STATUS IN ('APPLIED','APPLYING')"):
        raise ValueError('REMEDIATION_OUTSTANDING')
    if sql(connection, "SELECT NAME FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.TASK_HISTORY("
           "SCHEDULED_TIME_RANGE_START=>DATEADD(hour,-24,CURRENT_TIMESTAMP()),RESULT_LIMIT=>10000)) "
           "WHERE DATABASE_NAME='AGENTSHIELD_DB' AND SCHEMA_NAME='ORCH' AND STATE IN ('EXECUTING','SCHEDULED')"):
        raise ValueError('TASK_BUSY')


def studio(connection, command, *args, cwd=None):
    result = subprocess.run(['cortex', 'agent-studio', command, '--connection', connection, *args],
                            capture_output=True, text=True, check=True, cwd=cwd)
    value = json.loads(result.stdout)
    if value.get('success') is not True:
        raise ValueError('AGENT_STUDIO_FAILED')
    return value


def snapshot(connection, expected_account):
    check_idle(connection, expected_account)
    folder = ROOT / 'build' / 'department-fixtures' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True, mode=0o700)
    folder.chmod(0o700)
    manifest = {'account': expected_account, 'targets': {}}
    for alias in ALIASES:
        target = BY_ALIAS[alias]['fqn']
        agent = studio(connection, 'agent-read', '--fqn', target)
        # Retain the official read output and an independent canonical hash for restore verification.
        observed = sql(connection, 'DESCRIBE AGENT ' + target)[0]
        spec = json.loads(observed['agent_spec'])
        grants = sql(connection, 'SHOW GRANTS ON AGENT ' + target)
        private_json(folder / (alias + '-original.json'), {'spec': spec, 'studio': agent, 'grants': grants})
        manifest['targets'][alias] = {'target': target, 'original_hash': digest(spec)}
        print(json.dumps({'snapshotted': alias, 'hash': digest(spec)}), flush=True)
    private_json(folder / 'manifest.json', manifest)
    print(json.dumps({'snapshot': str(folder), 'targets_changed': False}), flush=True)
    return folder


def live_spec(connection, target):
    observed = sql(connection, 'DESCRIBE AGENT ' + target)[0]['agent_spec']
    return json.loads(observed) if isinstance(observed, str) else observed


def grant_keys(grants):
    return sorted((row['privilege'], row['granted_to'], row['grantee_name'], row['grant_option']) for row in grants)


def install(connection, expected_account, folder):
    check_idle(connection, expected_account)
    manifest = json.loads((folder / 'manifest.json').read_text())
    if manifest['account'] != expected_account or set(manifest['targets']) != set(ALIASES):
        raise ValueError('INVALID_SNAPSHOT')
    proposed = {}
    for alias in ALIASES:
        target = BY_ALIAS[alias]['fqn']
        original = json.loads((folder / (alias + '-original.json')).read_text())
        if manifest['targets'][alias]['target'] != target or digest(original['spec']) != manifest['targets'][alias]['original_hash']:
            raise ValueError('SNAPSHOT_HASH_MISMATCH')
        if digest(live_spec(connection, target)) != manifest['targets'][alias]['original_hash']:
            raise ValueError('TARGET_CONFIGURATION_CHANGED')
        if grant_keys(sql(connection, 'SHOW GRANTS ON AGENT ' + target)) != grant_keys(original['grants']):
            raise ValueError('TARGET_GRANTS_CHANGED')
        proposed[alias] = fixture_spec(target, original['spec'])
    # Abort on an existing resource rather than overwrite an unrelated procedure.
    if sql(connection, "SHOW PROCEDURES LIKE 'DEPARTMENT_HEADCOUNT' IN SCHEMA AGENTSHIELD_DEMO.AGENTS"):
        raise ValueError('HEADCOUNT_RESOURCE_ALREADY_EXISTS')
    before_others = {item['fqn']: digest(live_spec(connection, item['fqn'])) for item in CATALOG
                     if item['alias'] not in ALIASES}
    private_json(folder / 'other-agents-before.json', before_others)
    private_json(folder / 'proposed-specs.json', proposed)
    private_json(folder / 'change-manifest.json', {
        'recipe_version': VERSION, 'targets': {alias: {
            'original_hash': manifest['targets'][alias]['original_hash'],
            'proposed_hash': digest(spec), 'removable_tool': PROFILES[BY_ALIAS[alias]['fqn']]['side_tool']}
            for alias, spec in proposed.items()}})
    subprocess.run(['snow', 'sql', '-c', connection, '-f', str(ROOT / 'deploy' / '10_department_headcount.sql')],
                   capture_output=True, text=True, check=True)
    resource = sql(connection, "SELECT GET_DDL('PROCEDURE','AGENTSHIELD_DEMO.AGENTS.DEPARTMENT_HEADCOUNT(VARCHAR)') AS DDL")
    private_json(folder / 'headcount-resource.json', resource)
    for alias, spec in proposed.items():
        target = BY_ALIAS[alias]['fqn']
        original = json.loads((folder / (alias + '-original.json')).read_text())
        if digest(live_spec(connection, target)) != manifest['targets'][alias]['original_hash']:
            raise ValueError('TARGET_CONFIGURATION_CHANGED')
        path = folder / (alias + '.agent.yaml')
        studio(connection, 'agent-write', '--yaml-content', json.dumps(spec), '--source-object', target,
               '--file-path', str(path), cwd=folder)
        studio(connection, 'agent-save', '--file-path', str(path), '--fqn', target, cwd=folder)
        actual = live_spec(connection, target)
        if digest(actual) != digest(spec):
            raise ValueError('LIVE_SPEC_MISMATCH_REVIEW_SNAPSHOT')
        if grant_keys(sql(connection, 'SHOW GRANTS ON AGENT ' + target)) != grant_keys(original['grants']):
            raise ValueError('LIVE_GRANTS_MISMATCH_REVIEW_SNAPSHOT')
        private_json(folder / (alias + '-installed.json'), {'target': target, 'spec': actual, 'hash': digest(actual)})
        print(json.dumps({'installed': target, 'hash': digest(actual), 'grants_unchanged': True}), flush=True)
    for target, before_hash in before_others.items():
        if digest(live_spec(connection, target)) != before_hash:
            raise ValueError('UNRELATED_AGENT_CHANGED')
    print(json.dumps({'status': 'INSTALLED', 'snapshot': str(folder), 'unrelated_agents_unchanged': 21}), flush=True)


def restore(connection, expected_account, folder):
    """Undo this redesign, not an applied remediation (which uses prepare_rollback)."""
    check_idle(connection, expected_account)
    manifest = json.loads((folder / 'manifest.json').read_text())
    if manifest['account'] != expected_account or set(manifest['targets']) != set(ALIASES):
        raise ValueError('INVALID_SNAPSHOT')
    originals = {}
    for alias in ALIASES:
        original = json.loads((folder / (alias + '-original.json')).read_text())
        target = BY_ALIAS[alias]['fqn']
        if manifest['targets'][alias]['target'] != target or digest(original['spec']) != manifest['targets'][alias]['original_hash']:
            raise ValueError('SNAPSHOT_HASH_MISMATCH')
        live = live_spec(connection, target)
        if digest(live) not in (digest(original['spec']), digest(fixture_spec(target, original['spec']))):
            raise ValueError('TARGET_CONFIGURATION_CHANGED')
        originals[alias] = original
    audit = folder / ('restore-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    audit.mkdir(mode=0o700)
    for alias, original in originals.items():
        target = BY_ALIAS[alias]['fqn']
        path = audit / (alias + '.agent.yaml')
        studio(connection, 'agent-write', '--yaml-content', json.dumps(original['spec']),
               '--source-object', target, '--file-path', str(path), cwd=audit)
        studio(connection, 'agent-save', '--file-path', str(path), '--fqn', target, cwd=audit)
        if digest(live_spec(connection, target)) != manifest['targets'][alias]['original_hash']:
            raise ValueError('RESTORE_HASH_MISMATCH')
        if grant_keys(sql(connection, 'SHOW GRANTS ON AGENT ' + target)) != grant_keys(original['grants']):
            raise ValueError('RESTORE_GRANTS_MISMATCH')
        private_json(audit / (alias + '-verified.json'), {'target': target, 'hash': manifest['targets'][alias]['original_hash']})
        print(json.dumps({'restored_original': target}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--expected-account', required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--install-from', type=Path, help='Install reviewed profiles from a private original snapshot')
    action.add_argument('--restore-from', type=Path, help='Restore original pre-redesign profiles, preserving grants')
    args = parser.parse_args()
    if args.install_from:
        install(args.connection, args.expected_account, args.install_from.resolve())
    elif args.restore_from:
        restore(args.connection, args.expected_account, args.restore_from.resolve())
    else:
        snapshot(args.connection, args.expected_account)