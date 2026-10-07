"""Human-approved remediation apply/rollback. Never reachable from CAMPAIGN_API.

Approval model: a front end (SnowBots in ask mode) must show the prepared diff and
get a human "Allow once" click on the single APPLY_REMEDIATION call. Snowflake binds
that call to a one-time, short-lived token, the exact proposal hash and the exact
live target hash, so a click can only execute what was prepared and reviewed.
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

RECIPE = 'REMOVE_EMPLOYEE_LOOKUP'
RECIPE_TOOL = 'EmployeeLookup'
APPLY_TARGET = TARGETS[1]
TOKEN_MINUTES = 15


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def tool_names(spec):
    return [(tool.get('tool_spec') or {}).get('name') for tool in spec.get('tools', [])]


def without_tool(spec, name=RECIPE_TOOL):
    """Remove exactly one named tool and its resource; everything else unchanged."""
    if tool_names(spec).count(name) != 1 or name not in (spec.get('tool_resources') or {}):
        raise ValueError('RECIPE_PRECONDITION_FAILED')
    result = json.loads(json.dumps(spec))
    result['tools'] = [tool for tool in result['tools'] if (tool.get('tool_spec') or {}).get('name') != name]
    del result['tool_resources'][name]
    return result


def check_account(session):
    expected = rows(session, 'SELECT EXPECTED_ACCOUNT FROM ' + CORE + 'REMEDIATION_SETTINGS')
    if len(expected) != 1 or expected[0]['EXPECTED_ACCOUNT'] != scalar(session, 'SELECT CURRENT_ACCOUNT()'):
        raise ValueError('ACCOUNT_NOT_ALLOWLISTED')


def set_spec(session, target, spec):
    if target != APPLY_TARGET:
        raise ValueError('TARGET_NOT_ALLOWLISTED')
    text = json.dumps(spec)
    if '$$' in text:
        raise ValueError('SPEC_NOT_QUOTABLE')
    # ALTER keeps existing grants on the agent; CREATE OR REPLACE would drop them.
    execute(session, 'ALTER AGENT ' + target + ' MODIFY LIVE VERSION SET SPECIFICATION = $$' + text + '$$')


def mint(session, campaign_id, kind, proposal_hash, before, after, rollback_of=None):
    token = secrets.token_urlsafe(24)
    apply_id = str(uuid.uuid4())
    execute(session, 'INSERT INTO ' + CORE + 'REMEDIATION_APPLIES (APPLY_ID, CAMPAIGN_ID, KIND, RECIPE_ID, TARGET, '
            'PROPOSAL_HASH, TARGET_HASH_BEFORE, STATUS, CONFIRM_TOKEN_HASH, TOKEN_EXPIRES_AT, PREPARED_BY, '
            "SPEC_BEFORE, SPEC_AFTER, ROLLBACK_OF) SELECT ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, "
            "DATEADD(minute, ?, CURRENT_TIMESTAMP()), CURRENT_USER(), PARSE_JSON(?), PARSE_JSON(?), NULLIF(?, '')",
            [apply_id, campaign_id, kind, RECIPE, APPLY_TARGET, proposal_hash, digest(before), token_hash(token),
             TOKEN_MINUTES, json.dumps(before), json.dumps(after), rollback_of or ''])
    return apply_id, token


def prepare(session, campaign_id):
    check_account(session)
    current = campaign(session, campaign_id)
    proposal = decoded(current['PROPOSAL']) or {}
    if current['STATUS'] not in TERMINAL:
        raise ValueError('CAMPAIGN_STILL_ACTIVE')
    if (decoded(current['REQUEST']) or {}).get('target') != APPLY_TARGET or proposal.get('recipe_id') != RECIPE:
        raise ValueError('NO_ELIGIBLE_RECIPE')
    if proposal.get('proposal_hash') != digest({'campaign': campaign_id, 'hash': current['TARGET_HASH'], 'recipe': RECIPE}):
        raise ValueError('PROPOSAL_HASH_MISMATCH')
    if scalar(session, 'SELECT COUNT(*) FROM ' + CORE + "REMEDIATION_APPLIES WHERE CAMPAIGN_ID = ? "
              "AND KIND = 'APPLY' AND STATUS IN ('APPLYING','APPLIED')", [campaign_id]):
        raise ValueError('ALREADY_APPLIED')
    live = target_spec(session, APPLY_TARGET)
    if digest(live) != current['TARGET_HASH']:
        raise ValueError('TARGET_CONFIGURATION_CHANGED')
    after = without_tool(live)
    apply_id, token = mint(session, campaign_id, 'APPLY', proposal['proposal_hash'], live, after)
    return {'apply_id': apply_id, 'short_id': apply_id[:8], 'confirm_token': token,
            'proposal_hash': proposal['proposal_hash'], 'kind': 'APPLY', 'target': APPLY_TARGET,
            'removes_tool': RECIPE_TOOL, 'remaining_tools': tool_names(after),
            'expires_in_minutes': TOKEN_MINUTES, 'impact': proposal.get('impact'),
            'next': 'Show this diff to the human; only after they approve, CALL ' + ORCH +
                    'APPLY_REMEDIATION(apply_id, proposal_hash, confirm_token, client_receipt).'}


def prepare_rollback(session, apply_id):
    check_account(session)
    found = rows(session, 'SELECT * FROM ' + CORE + "REMEDIATION_APPLIES WHERE APPLY_ID = ? AND KIND = 'APPLY'", [apply_id])
    if len(found) != 1 or found[0]['STATUS'] != 'APPLIED':
        raise ValueError('NOTHING_TO_ROLL_BACK')
    applied = found[0]
    live = target_spec(session, APPLY_TARGET)
    if digest(live) != applied['TARGET_HASH_AFTER']:
        raise ValueError('TARGET_CONFIGURATION_CHANGED')
    original = decoded(applied['SPEC_BEFORE'])
    proposal_hash = digest({'rollback_of': apply_id, 'hash': digest(live)})
    rollback_id, token = mint(session, applied['CAMPAIGN_ID'], 'ROLLBACK', proposal_hash, live, original, apply_id)
    return {'apply_id': rollback_id, 'short_id': rollback_id[:8], 'confirm_token': token,
            'proposal_hash': proposal_hash, 'kind': 'ROLLBACK', 'target': APPLY_TARGET,
            'restores_tools': tool_names(original), 'expires_in_minutes': TOKEN_MINUTES,
            'impact': 'Restores the vulnerable pre-fix configuration, including ' + RECIPE_TOOL + '.'}


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
        if pending['TARGET'] != APPLY_TARGET:
            raise ValueError('TARGET_NOT_ALLOWLISTED')
        if active_campaign(session):
            raise ValueError('CAMPAIGN_BUSY')
        if pending['KIND'] == 'APPLY' and scalar(session, 'SELECT COUNT(*) FROM ' + CORE +
                "REMEDIATION_APPLIES WHERE CAMPAIGN_ID = ? AND KIND = 'APPLY' AND STATUS IN ('APPLYING','APPLIED')",
                [pending['CAMPAIGN_ID']]):
            raise ValueError('ALREADY_APPLIED')
        updated = execute(session, 'UPDATE ' + CORE + "REMEDIATION_APPLIES SET STATUS = 'APPLYING', "
                          'CONSUMED_AT = CURRENT_TIMESTAMP(), APPLIED_BY = CURRENT_USER(), APPLIED_ROLE = CURRENT_ROLE(), '
                          "CLIENT_RECEIPT = NULLIF(?, ''), UPDATED_AT = CURRENT_TIMESTAMP() "
                          "WHERE APPLY_ID = ? AND STATUS = 'PENDING' AND CONSUMED_AT IS NULL", [receipt, apply_id])
        if updated[0][0] != 1:
            raise ValueError('TOKEN_ALREADY_USED')
    return pending


def finish(session, apply_id, state, reason=None, after=None):
    execute(session, 'UPDATE ' + CORE + 'REMEDIATION_APPLIES SET STATUS = ?, REASON = ?, TARGET_HASH_AFTER = ?, '
            'UPDATED_AT = CURRENT_TIMESTAMP() WHERE APPLY_ID = ?',
            [state, reason, digest(after) if after is not None else None, apply_id])


def apply(session, apply_id, proposal_hash, token, client_receipt=''):
    check_account(session)
    receipt = client_receipt or ''
    if not re.fullmatch(r'[A-Za-z0-9:_./-]{0,200}', receipt):
        raise ValueError('INVALID_CLIENT_RECEIPT')
    pending = claim(session, apply_id, proposal_hash, token, receipt)
    before, desired = decoded(pending['SPEC_BEFORE']), decoded(pending['SPEC_AFTER'])
    try:
        if digest(target_spec(session, APPLY_TARGET)) != pending['TARGET_HASH_BEFORE']:
            raise ValueError('TARGET_CONFIGURATION_CHANGED')
        set_spec(session, APPLY_TARGET, desired)
        actual = target_spec(session, APPLY_TARGET)
        if pending['KIND'] == 'APPLY' and (RECIPE_TOOL in tool_names(actual) or
                                           RECIPE_TOOL in (actual.get('tool_resources') or {})):
            raise ValueError('VERIFY_TOOL_STILL_PRESENT')
        if pending['KIND'] == 'ROLLBACK' and digest(actual) != digest(desired):
            raise ValueError('VERIFY_RESTORE_MISMATCH')
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch('[A-Z_]{1,80}', str(exc)) else type(exc).__name__.upper()
        restored = False
        if reason != 'TARGET_CONFIGURATION_CHANGED':
            try:
                set_spec(session, APPLY_TARGET, before)
                restored = digest(target_spec(session, APPLY_TARGET)) == pending['TARGET_HASH_BEFORE']
            except Exception:
                restored = False
        finish(session, apply_id, 'FAILED', reason + ('' if restored else '_NOT_RESTORED'))
        return {'apply_id': apply_id, 'status': 'FAILED', 'reason': reason, 'restored': restored}
    finish(session, apply_id, 'APPLIED', None, actual)
    result = {'apply_id': apply_id, 'status': 'APPLIED', 'kind': pending['KIND'], 'target': APPLY_TARGET,
              'tools_now': tool_names(actual), 'spec_matches_preview': digest(actual) == digest(desired)}
    if pending['KIND'] == 'ROLLBACK':
        finish_rolled = 'UPDATE ' + CORE + "REMEDIATION_APPLIES SET STATUS = 'ROLLED_BACK', UPDATED_AT = CURRENT_TIMESTAMP() WHERE APPLY_ID = ?"
        execute(session, finish_rolled, [pending['ROLLBACK_OF']])
        return result
    try:
        original = campaign(session, pending['CAMPAIGN_ID'])
        retest = submit(session, {**decoded(original['REQUEST']), 'request_key': 'fix-' + apply_id,
                                  'parent_campaign_id': pending['CAMPAIGN_ID']})
        execute(session, 'UPDATE ' + CORE + 'REMEDIATION_APPLIES SET RETEST_CAMPAIGN_ID = ? WHERE APPLY_ID = ?',
                [retest['campaign_id'], apply_id])
        execute(session, 'EXECUTE TASK ' + ORCH + 'CAMPAIGN_ROOT')
        result.update({'retest_campaign_id': retest['campaign_id'], 'retest_status': 'DISPATCHED'})
    except Exception as exc:
        # The fix stands; retest can be started explicitly via CAMPAIGN_API retest.
        result.update({'retest_status': 'NOT_STARTED', 'retest_error': type(exc).__name__})
    return result


def history(session, campaign_id):
    found = rows(session, 'SELECT APPLY_ID, KIND, STATUS, REASON, APPLIED_BY, CLIENT_RECEIPT, RETEST_CAMPAIGN_ID, '
                 'CREATED_AT, CONSUMED_AT FROM ' + CORE +
                 'REMEDIATION_APPLIES WHERE CAMPAIGN_ID = ? ORDER BY CREATED_AT', [campaign_id])
    return [{key: (str(value) if key in ('CREATED_AT', 'CONSUMED_AT') and value is not None else value)
             for key, value in row.items()} for row in found]
