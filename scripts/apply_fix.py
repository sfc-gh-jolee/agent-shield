"""Apply a prepared AgentShield fix or rollback. Run only after human approval.

Kept separate from campaign_client.py on purpose: SnowBots "Always allow" grants are
scoped by command prefix (interpreter + script path), so allowing the read-only
client never covers this script. Never grant "Always allow" to this command.
"""
import argparse
import json
import sys

from campaign_client import literal, load_token, sql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--expected-account', required=True)
    parser.add_argument('--apply-id', required=True, help='Full apply_id from prepare_fix / prepare_rollback')
    parser.add_argument('--receipt', default='', help='Front-end approval reference, recorded as evidence')
    args = parser.parse_args()
    identity = sql(args.connection, 'SELECT CURRENT_ACCOUNT() AS ACCOUNT')[0]['ACCOUNT']
    if identity.upper() != args.expected_account.upper():
        raise RuntimeError('ACCOUNT_MISMATCH: no fix call made')
    saved = load_token(args.apply_id)
    statement = ('CALL AGENTSHIELD_DB.ORCH.APPLY_REMEDIATION(' + ', '.join(literal(saved[key]) for key in (
        'apply_id', 'proposal_hash', 'confirm_token')) + ', ' + literal(args.receipt) + ')')
    response = sql(args.connection, 'USE WAREHOUSE AGENTSHIELD_WH; ' + statement)
    value = next(iter(response[0].values()))
    print(json.dumps(json.loads(value) if isinstance(value, str) else value, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
