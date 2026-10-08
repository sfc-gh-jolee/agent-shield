"""Human-approved remediation apply/rollback. Never reachable from CAMPAIGN_API.

Approval model: a front end (SnowBots in ask mode) must show the prepared diff and
get a human "Allow once" click on the single APPLY_REMEDIATION call. Snowflake binds
that call to a one-time, short-lived token, the exact proposal hash and the exact
live target hash, so a click can only execute what was prepared and reviewed.

Each failed case has its own reviewed fix (see agentshield_fixes). Fixes for one
campaign stack: each prepare checks the live spec against the campaign snapshot or
the result of the campaign's latest applied change, so outside edits still fail closed.
"""
import hashlib
import hmac
import json
import re
import secrets
import uuid

from agentshield_campaigns import (CORE, ORCH, TARGETS, TERMINAL, active_campaign, campaign,
                                   decoded, digest, execute, lock, rows, scalar, submit,
                                   target_spec, transaction)
from agentshield_fixes import apply_actions, summarize_actions, tool_names, verify

TOKEN_MINUTES = 15


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def check_account(session):
    expected = rows(session, 'SELECT EXPECTED_ACCOUNT FROM ' + CORE + 'REMEDIATION_SETTINGS')
    if len(expected) != 1 or expected[0]['EXPECTED_ACCOUNT'] != scalar(session, 'SELECT CURRENT_ACCOUNT()'):
        raise ValueError('ACCOUNT_NOT_ALLOWLISTED')


def set_spec(session, target, spec):
    if target not in TARGETS:
        raise ValueError('TARGET_NOT_ALLOWLISTED')
    text = json.dumps(spec)
    if '$$' in text:
        raise ValueError('SPEC_NOT_QUOTABLE')
    # ALTER keeps existing grants on the agent; CREATE OR REPLACE would drop them.
    execute(session, 'ALTER AGENT ' + target + ' MODIFY LIVE VERSION SET SPECIFICATION = $$' + text + '$$')


def mint(session, campaign_id, kind, fix_id, target, case_id, actions, proposal_hash, before, after, rollback_of=None):
    token = secrets.token_urlsafe(24)
    apply_id = str(uuid.uuid4())
    execute(session, 'INSERT INTO ' + CORE + 'REMEDIATION_APPLIES (APPLY_ID, CAMPAIGN_ID, KIND, RECIPE_ID, TARGET, '
            'PROPOSAL_HASH, TARGET_HASH_BEFORE, STATUS, CONFIRM_TOKEN_HASH, TOKEN_EXPIRES_AT, PREPARED_BY, '
            "SPEC_BEFORE, SPEC_AFTER, ROLLBACK_OF, CASE_ID, ACTIONS) SELECT ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, "
            "DATEADD(minute, ?, CURRENT_TIMESTAMP()), CURRENT_USER(), PARSE_JSON(?), PARSE_JSON(?), NULLIF(?, ''), "
            "NULLIF(?, ''), PARSE_JSON(?)",
            [apply_id, campaign_id, kind, fix_id, target, proposal_hash, digest(before), token_hash(token),
             TOKEN_MINUTES, json.dumps(before), json.dumps(after), rollback_of or '', case_id or '',
             json.dumps(actions)])
    return apply_id, token


def expected_hash(session, campaign_id, snapshot_hash):
    """Hash the live spec must have: after this campaign's latest applied change, else the snapshot."""
    latest = rows(session, 'SELECT TARGET_HASH_AFTER FROM ' + CORE + "REMEDIATION_APPLIES WHERE CAMPAIGN_ID = ? "
                  "AND STATUS = 'APPLIED' AND TARGET_HASH_AFTER IS NOT NULL ORDER BY CONSUMED_AT DESC LIMIT 1",
                  [campaign_id])
    return latest[0]['TARGET_HASH_AFTER'] if latest else snapshot_hash


def applied_cases(session, campaign_id):
    found = rows(session, 'SELECT CASE_ID, CASE_IDS FROM ' + CORE + "REMEDIATION_APPLIES WHERE CAMPAIGN_ID = ? "
                 "AND KIND = 'APPLY' AND STATUS IN ('APPLYING','APPLIED')", [campaign_id])
    return {case_id for row in found for case_id in
            (decoded(row.get('CASE_IDS')) or ([row['CASE_ID']] if row.get('CASE_ID') else []))}


def remaining_fixes(proposal, live, done):
    """Fixes not yet applied whose change is not already present on the live spec."""
    return [fix['case_id'] for fix in proposal.get('fixes', [])
            if fix['case_id'] not in done and apply_actions(live, fix['actions']) != live]


def checked_campaign(session, campaign_id):
    current = campaign(session, campaign_id)
    proposal = decoded(current['PROPOSAL']) or {}
    if current['STATUS'] not in TERMINAL:
        raise ValueError('CAMPAIGN_STILL_ACTIVE')
    target = (decoded(current['REQUEST']) or {}).get('target')
    if target not in TARGETS:
        raise ValueError('TARGET_NOT_ALLOWLISTED')
    fixes = proposal.get('fixes') or []
    if proposal.get('proposal_hash') != digest({'campaign': campaign_id, 'hash': current['TARGET_HASH'],
                                                 'fixes': [fix['fix_id'] for fix in fixes]}):
        raise ValueError('PROPOSAL_HASH_MISMATCH')
    return current, proposal, target


def prepare(session, campaign_id, case_id):
    check_account(session)
    current, proposal, target = checked_campaign(session, campaign_id)
    matches = [fix for fix in proposal.get('fixes', []) if fix['case_id'] == case_id]
    if len(matches) != 1:
        raise ValueError('NO_FIX_FOR_CASE')
    fix = matches[0]
    if case_id in applied_cases(session, campaign_id):
        raise ValueError('ALREADY_APPLIED')
    live = target_spec(session, target)
    if digest(live) != expected_hash(session, campaign_id, current['TARGET_HASH']):
        raise ValueError('TARGET_CONFIGURATION_CHANGED')
    after = apply_actions(live, fix['actions'])
    if after == live:
        return {'status': 'ALREADY_COVERED', 'case_id': case_id, 'fix_id': fix['fix_id'],
                'note': 'An earlier fix already made this change; nothing to apply for this case.',
                'remaining_fixes': remaining_fixes(proposal, live, applied_cases(session, campaign_id))}
    proposal_hash = digest({'fix': fix['fix_id'], 'hash': digest(live)})
    apply_id, token = mint(session, campaign_id, 'APPLY', fix['fix_id'], target, case_id, fix['actions'],
                           proposal_hash, live, after)
    return {'apply_id': apply_id, 'short_id': apply_id[:8], 'confirm_token': token,
            'proposal_hash': proposal_hash, 'kind': 'APPLY', 'target': target, 'case_id': case_id,
            'category': fix['category'], 'why': fix.get('why'), 'changes': summarize_actions(fix['actions']),
            'tools_before': tool_names(live), 'tools_after': tool_names(after),
            'instructions_after': after.get('instructions'),
            'expires_in_minutes': TOKEN_MINUTES, 'impact': proposal.get('impact'),
            'next': 'Show this diff to the human; only after they approve, CALL ' + ORCH +
                    'APPLY_REMEDIATION(apply_id, proposal_hash, confirm_token, client_receipt).'}


def prepare_rollback(session, apply_id):
    check_account(session)
    found = rows(session, 'SELECT * FROM ' + CORE + "REMEDIATION_APPLIES WHERE APPLY_ID = ? AND KIND = 'APPLY'", [apply_id])
    if len(found) != 1 or found[0]['STATUS'] != 'APPLIED':
        raise ValueError('NOTHING_TO_ROLL_BACK')
    applied = found[0]
    live = target_spec(session, applied['TARGET'])
    if digest(live) != applied['TARGET_HASH_AFTER']:
        # A later fix changed the agent again; roll back newest first.
        raise ValueError('TARGET_CONFIGURATION_CHANGED')
    original = decoded(applied['SPEC_BEFORE'])
    proposal_hash = digest({'rollback_of': apply_id, 'hash': digest(live)})
    rollback_id, token = mint(session, applied['CAMPAIGN_ID'], 'ROLLBACK', applied['RECIPE_ID'], applied['TARGET'],
                              applied.get('CASE_ID'), decoded(applied.get('ACTIONS')) or [], proposal_hash,
                              live, original, apply_id)
    return {'apply_id': rollback_id, 'short_id': rollback_id[:8], 'confirm_token': token,
            'proposal_hash': proposal_hash, 'kind': 'ROLLBACK', 'target': applied['TARGET'],
            'case_id': applied.get('CASE_ID'), 'restores_tools': tool_names(original),
            'expires_in_minutes': TOKEN_MINUTES,
            'impact': 'Restores the configuration from before this fix, including anything it removed.'}


def claim(session, apply_id, proposal_hash, token, receipt):
    """Atomically consume the one-time token; every gate is checked under the campaign mutex."""
    with transaction(session):
        lock(session)
        found = rows(session, 'SELECT *, TOKEN_EXPIRES_AT <= CURRENT_TIMESTAMP() AS EXPIRED FROM ' + CORE +
                     'REMEDIATION_APPLIES WHERE APPLY_ID = ?', [apply_id])
        if len(found) != 1:
            raise ValueError('UNKNOWN_APPLY_ID')
        pending = found[0]
        if pending['STATUS'] != 'PENDING' or pending['CONSUMED_AT'] is not None:
            raise ValueError('TOKEN_ALREADY_USED')
        if pending['EXPIRED']:
            raise ValueError('TOKEN_EXPIRED')
        if not hmac.compare_digest(pending['CONFIRM_TOKEN_HASH'], token_hash(token)):
            raise ValueError('TOKEN_MISMATCH')
        if not hmac.compare_digest(pending['PROPOSAL_HASH'], proposal_hash):
            raise ValueError('PROPOSAL_HASH_MISMATCH')
        if pending['TARGET'] not in TARGETS:
            raise ValueError('TARGET_NOT_ALLOWLISTED')
        if active_campaign(session):
            raise ValueError('CAMPAIGN_BUSY')
        if scalar(session, 'SELECT COUNT(*) FROM ' + CORE + "REMEDIATION_APPLIES WHERE STATUS = 'APPLYING'"):
            raise ValueError('REMEDIATION_BUSY')
        if pending.get('SELECTION_ID'):
            from agentshield_selections import claim_bundle
            claim_bundle(session, pending)
        if pending['KIND'] == 'APPLY' and scalar(session, 'SELECT COUNT(*) FROM ' + CORE +
                "REMEDIATION_APPLIES WHERE CAMPAIGN_ID = ? AND CASE_ID = ? AND KIND = 'APPLY' "
                "AND STATUS IN ('APPLYING','APPLIED')", [pending['CAMPAIGN_ID'], pending.get('CASE_ID')]):
            raise ValueError('ALREADY_APPLIED')
        updated = execute(session, 'UPDATE ' + CORE + "REMEDIATION_APPLIES SET STATUS = 'APPLYING', "
                          'CONSUMED_AT = CURRENT_TIMESTAMP(), APPLIED_BY = CURRENT_USER(), APPLIED_ROLE = CURRENT_ROLE(), '
                          "CLIENT_RECEIPT = NULLIF(?, ''), UPDATED_AT = CURRENT_TIMESTAMP() "
                          "WHERE APPLY_ID = ? AND STATUS = 'PENDING' AND CONSUMED_AT IS NULL", [receipt, apply_id])
        if updated[0][0] != 1:
            raise ValueError('TOKEN_ALREADY_USED')
    return pending


def finish(session, apply_id, state, reason=None, after=None):
    with transaction(session):
        lock(session)
        execute(session, 'UPDATE ' + CORE + 'REMEDIATION_APPLIES SET STATUS = ?, REASON = ?, TARGET_HASH_AFTER = ?, '
                'UPDATED_AT = CURRENT_TIMESTAMP() WHERE APPLY_ID = ?',
                [state, reason, digest(after) if after is not None else None, apply_id])
        execute(session, 'UPDATE ' + CORE + 'REMEDIATION_BUNDLES SET STATUS = ?, REASON = ?, '
                'UPDATED_AT = CURRENT_TIMESTAMP() WHERE APPLY_ID = ?', [state, reason, apply_id])


def apply(session, apply_id, proposal_hash, token, client_receipt=''):
    check_account(session)
    receipt = client_receipt or ''
    if not re.fullmatch(r'[A-Za-z0-9:_./-]{0,200}', receipt):
        raise ValueError('INVALID_CLIENT_RECEIPT')
    pending = claim(session, apply_id, proposal_hash, token, receipt)
    target = pending['TARGET']
    before, desired = decoded(pending['SPEC_BEFORE']), decoded(pending['SPEC_AFTER'])
    actions = decoded(pending.get('ACTIONS')) or []
    try:
        if digest(target_spec(session, target)) != pending['TARGET_HASH_BEFORE']:
            raise ValueError('TARGET_CONFIGURATION_CHANGED')
        set_spec(session, target, desired)
        actual = target_spec(session, target)
        if pending['KIND'] == 'APPLY' and not verify(actual, actions):
            raise ValueError('VERIFY_FIX_NOT_PRESENT')
        if pending.get('SELECTION_ID') and digest(actual) != digest(desired):
            raise ValueError('VERIFY_BUNDLE_MISMATCH')
        if pending['KIND'] == 'ROLLBACK' and digest(actual) != digest(desired):
            raise ValueError('VERIFY_RESTORE_MISMATCH')
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch('[A-Z_]{1,80}', str(exc)) else type(exc).__name__.upper()
        restored = False
        if reason != 'TARGET_CONFIGURATION_CHANGED':
            try:
                set_spec(session, target, before)
                restored = digest(target_spec(session, target)) == pending['TARGET_HASH_BEFORE']
            except Exception:
                restored = False
        finish(session, apply_id, 'FAILED', reason + ('' if restored else '_NOT_RESTORED'))
        return {'apply_id': apply_id, 'status': 'FAILED', 'reason': reason, 'restored': restored}
    finish(session, apply_id, 'APPLIED', None, actual)
    result = {'apply_id': apply_id, 'status': 'APPLIED', 'kind': pending['KIND'], 'target': target,
              'case_id': pending.get('CASE_ID'), 'tools_now': tool_names(actual),
              'spec_matches_preview': digest(actual) == digest(desired)}
    if pending.get('SELECTION_ID'):
        result.update(selection_id=pending['SELECTION_ID'], case_ids=decoded(pending['CASE_IDS']),
                      retest_status='WAITING_FOR_SELECTION_DECISIONS')
        return result
    if pending['KIND'] == 'ROLLBACK':
        finish_rolled = 'UPDATE ' + CORE + "REMEDIATION_APPLIES SET STATUS = 'ROLLED_BACK', UPDATED_AT = CURRENT_TIMESTAMP() WHERE APPLY_ID = ?"
        execute(session, finish_rolled, [pending['ROLLBACK_OF']])
        execute(session, 'UPDATE ' + CORE + "REMEDIATION_BUNDLES SET STATUS = 'ROLLED_BACK', "
                'UPDATED_AT = CURRENT_TIMESTAMP() WHERE APPLY_ID = ?', [pending['ROLLBACK_OF']])
        return result
    original = campaign(session, pending['CAMPAIGN_ID'])
    remaining = remaining_fixes(decoded(original['PROPOSAL']) or {}, actual,
                                applied_cases(session, pending['CAMPAIGN_ID']))
    result['remaining_fixes'] = remaining
    if remaining:
        result['retest_status'] = 'WAITING_FOR_REMAINING_FIXES'
        return result
    result.update(start_retest(session, original, 'fix-' + apply_id))
    return result


def start_retest(session, original, request_key):
    try:
        retest = submit(session, {**decoded(original['REQUEST']), 'request_key': request_key,
                                  'parent_campaign_id': original['CAMPAIGN_ID']})
        execute(session, 'UPDATE ' + CORE + 'REMEDIATION_APPLIES SET RETEST_CAMPAIGN_ID = ? WHERE CAMPAIGN_ID = ? '
                "AND KIND = 'APPLY' AND STATUS = 'APPLIED' AND RETEST_CAMPAIGN_ID IS NULL",
                [retest['campaign_id'], original['CAMPAIGN_ID']])
        execute(session, 'EXECUTE TASK ' + ORCH + 'CAMPAIGN_ROOT')
        return {'retest_campaign_id': retest['campaign_id'], 'retest_status': 'DISPATCHED'}
    except Exception as exc:
        # The fixes stand; retest can be started explicitly via CAMPAIGN_API retest.
        return {'retest_status': 'NOT_STARTED', 'retest_error': type(exc).__name__}


def history(session, campaign_id):
    found = rows(session, 'SELECT APPLY_ID, KIND, CASE_ID, CASE_IDS, SELECTION_ID, RECIPE_ID, STATUS, REASON, APPLIED_BY, CLIENT_RECEIPT, '
                 'RETEST_CAMPAIGN_ID, CREATED_AT, CONSUMED_AT FROM ' + CORE +
                 'REMEDIATION_APPLIES WHERE CAMPAIGN_ID = ? ORDER BY CREATED_AT', [campaign_id])
    return [{key: (str(value) if key in ('CREATED_AT', 'CONSUMED_AT') and value is not None else value)
             for key, value in row.items()} for row in found]
