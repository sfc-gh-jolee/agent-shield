"""Durable remediation scope and per-agent bundles, separate from the scan API.

This API prepares changes but cannot apply them. Chat selections are never approval.
All state transitions share the campaign mutex; standard-table keys alone are not unique.
"""
import json
import re
import uuid
import agentshield_campaigns as C
import agentshield_remediation as R
from agentshield_batches import batch
from agentshield_catalog import BY_FQN
from agentshield_fixes import GUARDRAILS, apply_actions, fix_id, summarize_actions, tool_names
from agentshield_department_recipes import action_valid

SELECTIONS = C.CORE + 'REMEDIATION_SELECTIONS'
BUNDLES = C.CORE + 'REMEDIATION_BUNDLES'
RESOLVED = ('APPLIED', 'ALREADY_COVERED', 'SKIPPED', 'FAILED', 'ROLLED_BACK')


def selection(session, selection_id):
    found = C.rows(session, 'SELECT * FROM ' + SELECTIONS + ' WHERE SELECTION_ID = ?', [selection_id])
    if len(found) != 1:
        raise ValueError('SELECTION_NOT_UNIQUE')
    return found[0]


def bundles(session, selection_id):
    return C.rows(session, 'SELECT * FROM ' + BUNDLES + ' WHERE SELECTION_ID = ? ORDER BY TARGET', [selection_id])


def bundle(session, selection_id, campaign_id):
    found = [row for row in bundles(session, selection_id) if row['CAMPAIGN_ID'] == campaign_id]
    if len(found) != 1:
        raise ValueError('BUNDLE_NOT_UNIQUE')
    return found[0]


def normalize_items(items):
    if not isinstance(items, list) or not items or len(items) > 25:
        raise ValueError('INVALID_SELECTION')
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {'campaign_id', 'proposal_hash', 'case_ids'}:
            raise ValueError('INVALID_SELECTION_ITEM')
        campaign_id, proposal_hash, ids = item['campaign_id'], item['proposal_hash'], item['case_ids']
        if (not isinstance(campaign_id, str) or not campaign_id or campaign_id in seen or
                not isinstance(proposal_hash, str) or not proposal_hash or not isinstance(ids, list) or
                not ids or len(ids) > 100 or any(not isinstance(value, str) or not value for value in ids)):
            raise ValueError('INVALID_SELECTION_ITEM')
        seen.add(campaign_id)
        result.append({'campaign_id': campaign_id, 'proposal_hash': proposal_hash, 'case_ids': sorted(set(ids))})
    return sorted(result, key=lambda item: item['campaign_id'])


def selected_actions(campaign_id, fixes, ids):
    by_case = {fix['case_id']: fix for fix in fixes}
    if len(by_case) != len(fixes) or any(case_id not in by_case for case_id in ids):
        raise ValueError('NO_FIX_FOR_CASE')
    actions, fix_ids = {}, []
    for case_id in sorted(ids):
        fix = by_case[case_id]
        if fix['fix_id'] != fix_id(campaign_id, case_id, fix['actions']):
            raise ValueError('FIX_HASH_MISMATCH')
        fix_ids.append(fix['fix_id'])
        for action in fix['actions']:
            if not isinstance(action, dict):
                raise ValueError('UNKNOWN_FIX_ACTION')
            if action.get('type') == 'remove_tool':
                valid = set(action) == {'type', 'tool'} and isinstance(action['tool'], str) and bool(action['tool'])
            elif action.get('type') == 'add_guardrail':
                valid = set(action) == {'type', 'category'} and action['category'] in GUARDRAILS
            elif action.get('type') == 'department_repair':
                valid = action_valid(action)
            else:
                valid = False
            if not valid:
                raise ValueError('UNKNOWN_FIX_ACTION')
            actions[C.digest(action)] = action
    if not actions:
        raise ValueError('NO_FIX_ACTIONS')
    return [actions[key] for key in sorted(actions)], fix_ids


def remediation_options(session, batch_id, campaign_ids=None):
    saved = batch(session, batch_id)
    if saved['STATUS'] not in C.TERMINAL:
        raise ValueError('BATCH_STILL_ACTIVE')
    children = C.rows(session, 'SELECT CAMPAIGN_ID FROM ' + C.CORE +
                      'CAMPAIGNS WHERE BATCH_ID = ? ORDER BY TARGET', [batch_id])
    if len(children) != len(C.decoded(saved['REQUEST'])):
        raise ValueError('BATCH_MANIFEST_INCOMPLETE')
    agents = []
    for child in children:
        campaign_id = child['CAMPAIGN_ID']
        if campaign_ids is not None and campaign_id not in campaign_ids:
            continue
        current, proposal, target = R.checked_campaign(session, campaign_id)
        if current.get('BATCH_ID') != batch_id:
            raise ValueError('BATCH_MEMBERSHIP_MISMATCH')
        live = C.target_spec(session, target)
        drifted = C.digest(live) != R.expected_hash(session, campaign_id, current['TARGET_HASH'])
        done = R.applied_cases(session, campaign_id)
        cases = C.rows(session, 'SELECT CASE_ID, CATEGORY, VERDICT FROM ' + C.CORE +
                       "CAMPAIGN_CASES WHERE CAMPAIGN_ID = ? AND VERDICT IN ('FAIL','INCONCLUSIVE')", [campaign_id])
        verdicts = {case['CASE_ID']: case['VERDICT'] for case in cases}
        fixes = []
        for fix in proposal.get('fixes', []):
            eligibility = ('MANUAL_REVIEW' if verdicts.get(fix['case_id']) != 'FAIL' else
                           'STALE' if drifted else 'APPLIED' if fix['case_id'] in done else
                           'ALREADY_COVERED' if apply_actions(live, fix['actions'], target) == live else 'ELIGIBLE')
            fixes.append({key: fix.get(key) for key in ('case_id', 'fix_id', 'category', 'why')} |
                         {'changes': summarize_actions(fix['actions']), 'eligibility': eligibility})
        known = {fix['case_id'] for fix in fixes if fix['eligibility'] != 'MANUAL_REVIEW'}
        agents.append({'campaign_id': campaign_id, 'target': target, 'label': BY_FQN[target]['title'],
                       'proposal_hash': proposal['proposal_hash'], 'fixes': fixes,
                       'manual_review': [row for row in cases if row['CASE_ID'] not in known]})
    return {'batch_id': batch_id, 'agents': agents}


def select_fixes(session, request):
    items = normalize_items(request.get('items'))
    key = request.get('request_key')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', key):
        raise ValueError('INVALID_REQUEST_KEY')
    batch_id = request.get('batch_id')
    fingerprint = C.digest({'batch_id': batch_id, 'items': items})
    with C.transaction(session):
        C.lock(session)
        prior = C.rows(session, 'SELECT * FROM ' + SELECTIONS + ' WHERE REQUEST_KEY = ?', [key])
        if prior:
            if len(prior) != 1 or prior[0]['REQUEST_HASH'] != fingerprint:
                raise ValueError('IDEMPOTENCY_CONFLICT')
            return {'selection_id': prior[0]['SELECTION_ID'], 'status': prior[0]['STATUS'], 'reused': True}
        available = {agent['campaign_id']: agent for agent in
                     remediation_options(session, batch_id, {item['campaign_id'] for item in items})['agents']}
        prepared = []
        for item in items:
            agent = available.get(item['campaign_id'])
            if agent is None:
                raise ValueError('BATCH_MEMBERSHIP_MISMATCH')
            if agent['proposal_hash'] != item['proposal_hash']:
                raise ValueError('PROPOSAL_HASH_MISMATCH')
            eligible = {fix['case_id'] for fix in agent['fixes'] if fix['eligibility'] == 'ELIGIBLE'}
            if not set(item['case_ids']) <= eligible:
                raise ValueError('FINDING_NOT_ELIGIBLE')
            _, proposal, target = R.checked_campaign(session, item['campaign_id'])
            actions, fix_ids = selected_actions(item['campaign_id'], proposal['fixes'], item['case_ids'])
            prepared.append((item, target, actions, fix_ids))
        selection_id = str(uuid.uuid4())
        C.execute(session, 'INSERT INTO ' + SELECTIONS +
                  ' (SELECTION_ID,REQUEST_KEY,REQUEST_HASH,BATCH_ID,ITEMS,STATUS,CREATED_BY) '
                  "SELECT ?,?,?,?,PARSE_JSON(?),'OPEN',CURRENT_USER()",
                  [selection_id, key, fingerprint, batch_id, json.dumps(items)])
        for item, target, actions, fix_ids in prepared:
            C.execute(session, 'INSERT INTO ' + BUNDLES +
                      ' (SELECTION_ID,CAMPAIGN_ID,TARGET,SOURCE_HASH,CASE_IDS,FIX_IDS,ACTIONS,STATUS) '
                      "SELECT ?,?,?,?,PARSE_JSON(?),PARSE_JSON(?),PARSE_JSON(?),'PENDING'",
                      [selection_id, item['campaign_id'], target, item['proposal_hash'], json.dumps(item['case_ids']),
                       json.dumps(fix_ids), json.dumps(actions)])
    return selection_status(session, selection_id)


def selection_status(session, selection_id):
    current = selection(session, selection_id)
    output = []
    for row in bundles(session, selection_id):
        item = {key.lower(): row.get(key) for key in ('CAMPAIGN_ID', 'TARGET', 'STATUS', 'APPLY_ID',
                                                     'RETEST_CAMPAIGN_ID', 'REASON')}
        item['case_ids'] = C.decoded(row['CASE_IDS'])
        item['changes'] = summarize_actions(C.decoded(row['ACTIONS']))
        original = C.campaign(session, row['CAMPAIGN_ID'])
        proposed = (C.decoded(original['PROPOSAL']) or {}).get('fixes', [])
        item['unselected_case_ids'] = [fix['case_id'] for fix in proposed if fix['case_id'] not in item['case_ids']]
        if row['STATUS'] == 'PREPARED' and row.get('APPLY_ID'):
            pending = C.rows(session, 'SELECT TOKEN_EXPIRES_AT <= CURRENT_TIMESTAMP() AS EXPIRED FROM ' + C.CORE +
                             'REMEDIATION_APPLIES WHERE APPLY_ID = ?', [row['APPLY_ID']])
            item['preview_expired'] = len(pending) != 1 or bool(pending[0]['EXPIRED'])
        if row.get('RETEST_CAMPAIGN_ID'):
            before = C.status(session, row['CAMPAIGN_ID'])
            after = C.status(session, row['RETEST_CAMPAIGN_ID'])
            item['comparison'] = {'before': {'security': before['security'], 'baseline': before['baseline']},
                                  'after': {'security': after['security'], 'baseline': after['baseline']},
                                  'retest_status': after['status']}
            previous = {case['CASE_ID']: case for case in before['cases']}
            item['comparison']['cases'] = [{'original_case_id': case['PARENT_CASE_ID'],
                'retest_case_id': case['CASE_ID'], 'category': case['CATEGORY'],
                'selected': case['PARENT_CASE_ID'] in item['case_ids'],
                'before': previous.get(case['PARENT_CASE_ID'], {}).get('VERDICT', 'MISSING'),
                'after': case['VERDICT']} for case in after['cases']]
        output.append(item)
    return {'selection_id': selection_id, 'batch_id': current['BATCH_ID'], 'status': current['STATUS'],
            'bundles': output, 'decisions_complete': all(item['status'] in RESOLVED for item in output)}


def update_bundle(session, selection_id, campaign_id, status, reason=None):
    C.execute(session, 'UPDATE ' + BUNDLES +
              ' SET STATUS = ?, REASON = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE SELECTION_ID = ? AND CAMPAIGN_ID = ?',
              [status, reason, selection_id, campaign_id])


def invalidate_pending(session, row):
    if row.get('APPLY_ID'):
        C.execute(session, 'UPDATE ' + C.CORE + "REMEDIATION_APPLIES SET STATUS = 'SUPERSEDED', "
                  "UPDATED_AT = CURRENT_TIMESTAMP() WHERE APPLY_ID = ? AND STATUS = 'PENDING'", [row['APPLY_ID']])


def prepare_bundle(session, selection_id, campaign_id):
    with C.transaction(session):
        C.lock(session)
        if selection(session, selection_id)['STATUS'] != 'OPEN':
            raise ValueError('SELECTION_CLOSED')
        row = bundle(session, selection_id, campaign_id)
        if row['STATUS'] not in ('PENDING', 'PREPARED', 'STALE'):
            raise ValueError('BUNDLE_ALREADY_RESOLVED')
        current, proposal, target = R.checked_campaign(session, campaign_id)
        if row['SOURCE_HASH'] != proposal['proposal_hash']:
            raise ValueError('PROPOSAL_HASH_MISMATCH')
        ids = C.decoded(row['CASE_IDS'])
        actions, fix_ids = selected_actions(campaign_id, proposal['fixes'], ids)
        if actions != C.decoded(row['ACTIONS']) or fix_ids != C.decoded(row['FIX_IDS']):
            raise ValueError('BUNDLE_SCOPE_MISMATCH')
        if C.active_campaign(session):
            raise ValueError('CAMPAIGN_BUSY')
        if C.scalar(session, 'SELECT COUNT(*) FROM ' + C.CORE + "REMEDIATION_APPLIES WHERE STATUS = 'APPLYING'"):
            raise ValueError('REMEDIATION_BUSY')
        live = C.target_spec(session, target)
        if C.digest(live) != R.expected_hash(session, campaign_id, current['TARGET_HASH']):
            raise ValueError('TARGET_CONFIGURATION_CHANGED')
        # Enforce reviewed side-tool removal and keep the core tools even for a corrupt proposal.
        types = {(tool.get('tool_spec') or {}).get('name'): (tool.get('tool_spec') or {}).get('type')
                 for tool in live.get('tools', [])}
        if any(action['type'] == 'remove_tool' and action['tool'] in types and
               types[action['tool']] not in ('generic', 'agent_toolset') for action in actions):
            raise ValueError('CORE_TOOL_REMOVAL_REJECTED')
        after = apply_actions(live, actions, target)
        if not after.get('tools'):
            raise ValueError('LAST_TOOL_REMOVAL_REJECTED')
        invalidate_pending(session, row)
        if after == live:
            update_bundle(session, selection_id, campaign_id, 'ALREADY_COVERED')
            return {'status': 'ALREADY_COVERED', 'selection_id': selection_id, 'campaign_id': campaign_id}
        fingerprint = C.digest({'selection': selection_id, 'campaign': campaign_id, 'source': row['SOURCE_HASH'],
                                'case_ids': ids, 'actions': actions, 'before': live, 'after': after})
        apply_id, token = R.mint(session, campaign_id, 'APPLY', 'BUNDLE_' + fingerprint[:16], target,
                                None, actions, fingerprint, live, after)
        C.execute(session, 'UPDATE ' + C.CORE +
                  'REMEDIATION_APPLIES SET SELECTION_ID = ?, CASE_IDS = PARSE_JSON(?) WHERE APPLY_ID = ?',
                  [selection_id, json.dumps(ids), apply_id])
        C.execute(session, 'UPDATE ' + BUNDLES +
                  " SET STATUS = 'PREPARED', APPLY_ID = ?, REASON = NULL, UPDATED_AT = CURRENT_TIMESTAMP() "
                  'WHERE SELECTION_ID = ? AND CAMPAIGN_ID = ?', [apply_id, selection_id, campaign_id])
        return {'apply_id': apply_id, 'short_id': apply_id[:8], 'confirm_token': token,
                'proposal_hash': fingerprint, 'selection_id': selection_id, 'campaign_id': campaign_id,
                'target': target, 'case_ids': ids, 'changes': summarize_actions(actions),
                'tools_before': tool_names(live), 'tools_after': tool_names(after),
                'instructions_before': live.get('instructions'), 'instructions_after': after.get('instructions'),
                'unselected_case_ids': [fix['case_id'] for fix in proposal['fixes'] if fix['case_id'] not in ids],
                'expires_in_minutes': R.TOKEN_MINUTES, 'impact': proposal.get('impact'),
                'status': 'PREPARED', 'next': 'Show combined preview; human Allow once required for this agent.'}


def skip_bundle(session, selection_id, campaign_id):
    with C.transaction(session):
        C.lock(session)
        if selection(session, selection_id)['STATUS'] != 'OPEN':
            raise ValueError('SELECTION_CLOSED')
        row = bundle(session, selection_id, campaign_id)
        if row['STATUS'] == 'SKIPPED':
            return {'status': 'SKIPPED'}
        if row['STATUS'] not in ('PENDING', 'PREPARED', 'STALE'):
            raise ValueError('BUNDLE_ALREADY_RESOLVED')
        invalidate_pending(session, row)
        update_bundle(session, selection_id, campaign_id, 'SKIPPED', 'USER_DECLINED')
    return selection_status(session, selection_id)


def claim_bundle(session, pending):
    """Called inside the existing apply claim transaction, before consuming its token."""
    selection_id = pending['SELECTION_ID']
    if selection(session, selection_id)['STATUS'] != 'OPEN':
        raise ValueError('SELECTION_CLOSED')
    row = bundle(session, selection_id, pending['CAMPAIGN_ID'])
    if row['STATUS'] != 'PREPARED' or row['APPLY_ID'] != pending['APPLY_ID']:
        raise ValueError('BUNDLE_NOT_PREPARED')
    if (C.decoded(row['CASE_IDS']) != C.decoded(pending['CASE_IDS']) or
            C.decoded(row['ACTIONS']) != C.decoded(pending['ACTIONS']) or row['TARGET'] != pending['TARGET']):
        raise ValueError('BUNDLE_SCOPE_MISMATCH')
    _, proposal, _ = R.checked_campaign(session, pending['CAMPAIGN_ID'])
    if row['SOURCE_HASH'] != proposal['proposal_hash']:
        raise ValueError('PROPOSAL_HASH_MISMATCH')
    actions, fix_ids = selected_actions(pending['CAMPAIGN_ID'], proposal['fixes'], C.decoded(row['CASE_IDS']))
    fingerprint = C.digest({'selection': selection_id, 'campaign': pending['CAMPAIGN_ID'],
                            'source': row['SOURCE_HASH'], 'case_ids': C.decoded(row['CASE_IDS']),
                            'actions': actions, 'before': C.decoded(pending['SPEC_BEFORE']),
                            'after': C.decoded(pending['SPEC_AFTER'])})
    if (actions != C.decoded(row['ACTIONS']) or fix_ids != C.decoded(row['FIX_IDS']) or
            fingerprint != pending['PROPOSAL_HASH']):
        raise ValueError('BUNDLE_SCOPE_MISMATCH')
    update_bundle(session, selection_id, pending['CAMPAIGN_ID'], 'APPLYING')


def finish_selection(session, selection_id):
    """Commit the complete retest manifest before dispatch; repeatable after lost replies."""
    with C.transaction(session):
        C.lock(session)
        current = selection(session, selection_id)
        members = bundles(session, selection_id)
        if not members or any(row['STATUS'] not in RESOLVED for row in members):
            raise ValueError('SELECTION_DECISIONS_PENDING')
        if current['STATUS'] == 'OPEN':
            has_applied = any(row['STATUS'] == 'APPLIED' for row in members)
            if has_applied and C.active_campaign(session):
                raise ValueError('CAMPAIGN_BUSY')
            if has_applied and C.scalar(session, 'SELECT COUNT(*) FROM ' + C.CORE + "REMEDIATION_APPLIES WHERE STATUS = 'APPLYING'"):
                raise ValueError('REMEDIATION_BUSY')
            for row in members:
                if row['STATUS'] != 'APPLIED':
                    continue
                applied = C.rows(session, 'SELECT TARGET_HASH_AFTER FROM ' + C.CORE +
                                 "REMEDIATION_APPLIES WHERE APPLY_ID = ? AND STATUS = 'APPLIED'", [row['APPLY_ID']])
                if len(applied) != 1 or C.digest(C.target_spec(session, row['TARGET'])) != applied[0]['TARGET_HASH_AFTER']:
                    raise ValueError('TARGET_CONFIGURATION_CHANGED')
                original = C.campaign(session, row['CAMPAIGN_ID'])
                retest = C.submit(session, {**C.decoded(original['REQUEST']),
                                  'request_key': 'selection-' + C.digest([selection_id, row['CAMPAIGN_ID']]),
                                  'parent_campaign_id': row['CAMPAIGN_ID']}, _locked=True)
                C.execute(session, 'UPDATE ' + BUNDLES + ' SET RETEST_CAMPAIGN_ID = ?, '
                          'UPDATED_AT = CURRENT_TIMESTAMP() WHERE SELECTION_ID = ? AND CAMPAIGN_ID = ?',
                          [retest['campaign_id'], selection_id, row['CAMPAIGN_ID']])
                C.execute(session, 'UPDATE ' + C.CORE + 'REMEDIATION_APPLIES SET RETEST_CAMPAIGN_ID = ? '
                          'WHERE APPLY_ID = ?', [retest['campaign_id'], row['APPLY_ID']])
            C.execute(session, 'UPDATE ' + SELECTIONS + " SET STATUS = 'RETEST_QUEUED', "
                      'UPDATED_AT = CURRENT_TIMESTAMP() WHERE SELECTION_ID = ?', [selection_id])
    # EXECUTE TASK is outside the transaction. A failure leaves durable QUEUED IDs.
    members = bundles(session, selection_id)
    ids = [row['RETEST_CAMPAIGN_ID'] for row in members if row.get('RETEST_CAMPAIGN_ID')]
    states = [C.campaign(session, campaign_id)['STATUS'] for campaign_id in ids]
    dispatch = 'NOT_NEEDED'
    if any(state == 'QUEUED' for state in states):
        try:
            C.execute(session, 'EXECUTE TASK ' + C.ORCH + 'CAMPAIGN_ROOT')
            dispatch = 'DISPATCHED'
        except Exception:
            dispatch = 'NOT_STARTED_RETRY_FINISH_SELECTION'
    complete = all(state in C.TERMINAL for state in states)
    if complete:
        with C.transaction(session):
            C.lock(session)
            C.execute(session, 'UPDATE ' + SELECTIONS + " SET STATUS = 'COMPLETE', "
                      "UPDATED_AT = CURRENT_TIMESTAMP() WHERE SELECTION_ID = ? AND STATUS = 'RETEST_QUEUED'", [selection_id])
    return {**selection_status(session, selection_id), 'retest_dispatch': dispatch}


def run(session, action, request_json):
    R.check_account(session)
    request = C.decoded(request_json)
    if not isinstance(request, dict):
        raise ValueError('INVALID_REQUEST')
    if action == 'remediation_options':
        return remediation_options(session, request['batch_id'])
    if action == 'select_fixes':
        return select_fixes(session, request)
    if action == 'selection_status':
        return selection_status(session, request['selection_id'])
    if action == 'prepare_bundle':
        return prepare_bundle(session, request['selection_id'], request['campaign_id'])
    if action == 'skip_bundle':
        return skip_bundle(session, request['selection_id'], request['campaign_id'])
    if action == 'finish_selection':
        return finish_selection(session, request['selection_id'])
    raise ValueError('UNKNOWN_SELECTION_ACTION')