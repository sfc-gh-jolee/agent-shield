"""Live selection/preview/denial smoke test. Never invokes APPLY_REMEDIATION."""
import argparse
import json
import uuid
from campaign_client import selection_api, sql, literal
from agentshield_catalog import BY_FQN


def verify(connection, expected_account, batch_id):
    if sql(connection, 'SELECT CURRENT_ACCOUNT() AS ACCOUNT')[0]['ACCOUNT'] != expected_account:
        raise ValueError('ACCOUNT_MISMATCH')
    options = selection_api(connection, 'remediation_options', {'batch_id': batch_id})
    selected = next(agent for agent in options['agents'] if any(fix['eligibility'] == 'ELIGIBLE' for fix in agent['fixes']))
    fix = next(fix for fix in selected['fixes'] if fix['eligibility'] == 'ELIGIBLE')
    target = selected['target']
    assert target in BY_FQN
    statement = 'DESCRIBE AGENT ' + target
    before = sql(connection, statement)
    request = {'batch_id': batch_id, 'request_key': 'team-deny-' + uuid.uuid4().hex,
               'items': [{'campaign_id': selected['campaign_id'], 'proposal_hash': selected['proposal_hash'],
                          'case_ids': [fix['case_id']]}]}
    chosen = selection_api(connection, 'select_fixes', request)
    selection_id = chosen['selection_id']
    retry = selection_api(connection, 'select_fixes', request)
    assert retry['selection_id'] == selection_id and retry['reused']
    params = {'selection_id': selection_id, 'campaign_id': selected['campaign_id']}
    try:
        prepared = selection_api(connection, 'prepare_bundle', params)
    except RuntimeError as exc:
        if 'CAMPAIGN_BUSY' not in str(exc):
            raise
        selection_api(connection, 'skip_bundle', params)
        return {'status': 'BLOCKED_BY_ACTIVE_CAMPAIGN', 'selection_id': selection_id,
                'target_unchanged': sql(connection, statement) == before, 'apply_invoked': False}
    # Discard the secret without logging or saving; this rehearsal denies the proposal.
    prepared.pop('confirm_token', None)
    assert prepared['case_ids'] == [fix['case_id']]
    blocked = False
    try:
        selection_api(connection, 'finish_selection', {'selection_id': selection_id})
    except RuntimeError as exc:
        blocked = 'SELECTION_DECISIONS_PENDING' in str(exc)
    assert blocked
    skipped = selection_api(connection, 'skip_bundle', params)
    assert skipped['bundles'][0]['status'] == 'SKIPPED'
    completed = selection_api(connection, 'finish_selection', {'selection_id': selection_id})
    assert completed['status'] == 'COMPLETE'
    assert completed['bundles'][0]['retest_campaign_id'] is None
    assert sql(connection, statement) == before
    token_status = sql(connection, 'SELECT STATUS FROM AGENTSHIELD_DB.CORE.REMEDIATION_APPLIES WHERE APPLY_ID = ' +
                       literal(prepared['apply_id']))[0]['STATUS']
    assert token_status == 'SUPERSEDED'
    return {'status': 'PASS', 'selection_id': selection_id, 'target_unchanged': True,
            'idempotent_selection': True, 'pending_decisions_block_retest': True,
            'denial_invalidates_token': True, 'apply_invoked': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--expected-account', required=True)
    parser.add_argument('--batch-id', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.connection, args.expected_account, args.batch_id)))