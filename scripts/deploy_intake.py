"""Apply the reviewed intake/library update to an explicitly checked sandbox.

Only the existing staged campaign module and orchestrator instructions change.
Private before/after snapshots are written below ignored build/campaigns.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from campaign_client import literal, sql
from build_templates import load_templates, migration

ROOT = Path(__file__).resolve().parents[1]
AGENT = 'AGENTSHIELD_DB.ORCH.AGENTSHIELD'
TABLE = 'AGENTSHIELD_DB.CORE.ATTACK_TEMPLATES'


def get_spec(connection):
    description = sql(connection, 'DESCRIBE AGENT ' + AGENT)
    value = description[0]['agent_spec']
    return json.loads(value) if isinstance(value, str) else value


def ready(connection, expected):
    if sql(connection, 'SELECT CURRENT_ACCOUNT() AS ACCOUNT')[0]['ACCOUNT'] != expected:
        raise ValueError('ACCOUNT_MISMATCH')
    active = sql(connection, "SELECT COUNT(*) AS N FROM AGENTSHIELD_DB.CORE.CAMPAIGNS "
                 "WHERE STATUS NOT IN ('COMPLETE','PARTIAL','FAILED','CANCELLED')")[0]['N']
    if active:
        raise ValueError('ACTIVE_CAMPAIGN_DEPLOYMENT_DEFERRED')


def run_file(connection, path):
    result = subprocess.run(['snow', 'sql', '-c', connection, '-f', str(path), '--format', 'json'],
                            capture_output=True, text=True)
    if result.returncode:
        # Do not echo SQL containing reference prompts or live specification.
        raise RuntimeError('DEPLOYMENT_FAILED; inspect Snowflake query history locally')


def update_instructions(connection, expected):
    # Workers never import the orchestrator spec, so this is safe mid-campaign.
    if sql(connection, 'SELECT CURRENT_ACCOUNT() AS ACCOUNT')[0]['ACCOUNT'] != expected:
        raise ValueError('ACCOUNT_MISMATCH')
    build = ROOT / 'build' / 'campaigns'
    desired = json.loads((build / 'orchestrator_spec.json').read_text())
    old_spec = get_spec(connection)
    grants = sql(connection, 'SHOW GRANTS ON AGENT ' + AGENT)
    snapshot = build / ('instructions-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    snapshot.mkdir()
    (snapshot / 'before.json').write_text(json.dumps({'spec': old_spec, 'grants': grants}, indent=2))
    proposed = json.loads(json.dumps(old_spec))
    proposed.setdefault('instructions', {}).update(desired['instructions'])
    sql(connection, 'ALTER AGENT ' + AGENT + ' MODIFY LIVE VERSION SET SPECIFICATION = ' + literal(json.dumps(proposed)))
    if get_spec(connection) != proposed:
        raise ValueError('SPEC_READBACK_MISMATCH')
    if sql(connection, 'SHOW GRANTS ON AGENT ' + AGENT) != grants:
        raise ValueError('AGENT_GRANTS_CHANGED')
    (snapshot / 'after.json').write_text(json.dumps({'spec': proposed}, indent=2))
    print(json.dumps({'instructions_updated': True, 'snapshot': str(snapshot)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--expected-account', required=True)
    parser.add_argument('--instructions-only', action='store_true',
                        help='Alter only orchestrator instructions; safe while a campaign runs')
    args = parser.parse_args()
    if args.instructions_only:
        return update_instructions(args.connection, args.expected_account)
    ready(args.connection, args.expected_account)
    build = ROOT / 'build' / 'campaigns'
    desired = json.loads((build / 'orchestrator_spec.json').read_text())
    templates = load_templates()
    before = sql(args.connection, 'SELECT * FROM ' + TABLE + ' ORDER BY ID')
    old_spec = get_spec(args.connection)
    grants = sql(args.connection, 'SHOW GRANTS ON AGENT ' + AGENT)
    snapshot = build / ('intake-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    snapshot.mkdir()
    (snapshot / 'before.json').write_text(json.dumps({'spec': old_spec, 'templates': before, 'grants': grants}, indent=2))
    proposed = json.loads(json.dumps(old_spec))
    proposed.setdefault('instructions', {}).update(desired['instructions'])
    (snapshot / 'proposed_spec.json').write_text(json.dumps(proposed, indent=2))
    deployment = snapshot / 'expand.sql'
    deployment.write_text(migration(templates))
    ready(args.connection, args.expected_account)
    run_file(args.connection, deployment)
    after = sql(args.connection, 'SELECT * FROM ' + TABLE + ' ORDER BY ID')
    after_by_id = {row['ID']: row for row in after}
    if any(after_by_id.get(row['ID']) != row for row in before):
        raise ValueError('EXISTING_REFERENCE_CHANGED')
    counts = {category: sum(row['CATEGORY'] == category for row in after)
              for category in {row['CATEGORY'] for row in templates}}
    if any(value != 15 for value in counts.values()) or len(after_by_id) != len(after):
        raise ValueError('UNEXPECTED_LIBRARY_TOTALS')
    run_file(args.connection, deployment)
    if sql(args.connection, 'SELECT * FROM ' + TABLE + ' ORDER BY ID') != after:
        raise ValueError('REAPPLICATION_NOT_IDEMPOTENT')
    ready(args.connection, args.expected_account)
    module = (build / 'agentshield_campaigns.py').resolve()
    sql(args.connection, 'PUT ' + literal('file://' + str(module)) +
        ' @AGENTSHIELD_DB.ORCH.CODE AUTO_COMPRESS=FALSE OVERWRITE=TRUE')
    # ALTER preserves object identity, grants, profile and all untouched spec fields.
    if get_spec(args.connection) != old_spec:
        raise ValueError('ORCHESTRATOR_CHANGED_SINCE_SNAPSHOT')
    sql(args.connection, 'ALTER AGENT ' + AGENT + ' MODIFY LIVE VERSION SET SPECIFICATION = ' +
        literal(json.dumps(proposed)))
    actual = get_spec(args.connection)
    if actual != proposed:
        raise ValueError('SPEC_READBACK_MISMATCH')
    if sql(args.connection, 'SHOW GRANTS ON AGENT ' + AGENT) != grants:
        raise ValueError('AGENT_GRANTS_CHANGED')
    (snapshot / 'after.json').write_text(json.dumps({'spec': actual, 'category_counts': counts,
                                                  'existing_rows_unchanged': len(before), 'idempotent': True}, indent=2))
    print(json.dumps({'category_counts': counts, 'existing_rows_unchanged': len(before),
                      'idempotent': True, 'snapshot': str(snapshot)}))


if __name__ == '__main__':
    main()