"""Explicit-account client for SnowBots or a terminal.

Fix actions: prepare_fix / prepare_rollback mint a one-time token that is written to
a private local file and never printed; apply_fix consumes it. In SnowBots, run
apply_fix only as its own command so the ask-mode "Allow once" click gates it.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

TOKENS = Path(__file__).resolve().parents[1] / 'build' / 'fix_tokens'


def literal(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "''") + "'"


def sql(connection, statement):
    result = subprocess.run(['snow', 'sql', '-c', connection, '--format', 'json', '-q', statement],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        # SQL errors can echo raw requests. Keep stderr out of the chat.
        raise RuntimeError('SNOWFLAKE_COMMAND_FAILED: inspect the CLI locally; output suppressed')
    value = json.loads(result.stdout)
    if value and isinstance(value[0], list):
        value = value[-1]
    return value


def save_token(prepared):
    TOKENS.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = TOKENS / (prepared['apply_id'] + '.json')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump({key: prepared[key] for key in ('apply_id', 'proposal_hash', 'confirm_token')}, handle)
    return {key: value for key, value in prepared.items() if key != 'confirm_token'}


def load_token(apply_id):
    if not re.fullmatch(r'[0-9a-f-]{36}', apply_id or ''):
        raise ValueError('--apply-id must be the full prepared apply_id')
    path = TOKENS / (apply_id + '.json')
    saved = json.loads(path.read_text())
    path.unlink()  # One attempt per prepared token, success or failure.
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--expected-account', required=True, help='CURRENT_ACCOUNT() locator, not a guessed connection label')
    parser.add_argument('action', choices=['options', 'submit', 'start', 'status', 'report', 'report_summary', 'retest', 'cancel', 'chat',
                                           'prepare_fix', 'prepare_rollback', 'apply_fix'])
    parser.add_argument('--request', default='{}', help='JSON request object; never credentials')
    parser.add_argument('--campaign-id', help='Campaign to fix (prepare_fix)')
    parser.add_argument('--apply-id', help='Prepared apply_id (apply_fix, prepare_rollback)')
    parser.add_argument('--receipt', default='', help='Front-end approval reference, recorded as evidence')
    parser.add_argument('--output', type=Path, help='New local HTML file for report download')
    parser.add_argument('--overwrite', action='store_true', help='Explicitly replace an existing HTML export')
    parser.add_argument('--message', help='Orchestrator request (chat only)')
    args = parser.parse_args()
    identity = sql(args.connection, 'SELECT CURRENT_ACCOUNT() AS ACCOUNT')[0]['ACCOUNT']
    if identity.upper() != args.expected_account.upper():
        raise RuntimeError('ACCOUNT_MISMATCH: no campaign call made')
    if args.action == 'chat':
        if not args.message:
            raise ValueError('--message required')
        request = {'stream': False, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': args.message}]}]}
        statement = "SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN('AGENTSHIELD_DB.ORCH.AGENTSHIELD', " + literal(json.dumps(request)) + ', TRUE) AS RESPONSE'
    elif args.action == 'prepare_fix':
        statement = 'CALL AGENTSHIELD_DB.ORCH.PREPARE_REMEDIATION(' + literal(args.campaign_id or '') + ')'
    elif args.action == 'prepare_rollback':
        statement = 'CALL AGENTSHIELD_DB.ORCH.PREPARE_ROLLBACK(' + literal(args.apply_id or '') + ')'
    elif args.action == 'apply_fix':
        saved = load_token(args.apply_id)
        statement = ('CALL AGENTSHIELD_DB.ORCH.APPLY_REMEDIATION(' + ', '.join(literal(saved[key]) for key in (
            'apply_id', 'proposal_hash', 'confirm_token')) + ', ' + literal(args.receipt) + ')')
    else:
        request = json.loads(args.request)
        if not isinstance(request, dict):
            raise ValueError('Request must be a JSON object')
        statement = 'CALL AGENTSHIELD_DB.ORCH.CAMPAIGN_API(' + literal(args.action) + ', ' + literal(json.dumps(request)) + ')'
    response = sql(args.connection, 'USE WAREHOUSE AGENTSHIELD_WH; ' + statement)
    value = next(iter(response[0].values()))
    value = json.loads(value) if isinstance(value, str) else value
    if args.action == 'report':
        if not args.output or args.output.suffix.lower() != '.html' or not value.get('html'):
            raise ValueError('A completed report and --output new-file.html are required')
        # Existing reports are only replaced when explicitly requested.
        with args.output.open('w' if args.overwrite else 'x', encoding='utf-8') as output:
            output.write(value['html'])
        print(json.dumps({'campaign_id': value['campaign_id'], 'report_path': str(args.output.resolve()),
                          'status': value['status']}))
    elif args.action == 'chat':
        print(json.dumps({'status': value.get('status'), 'messages': [item['text'] for item in value.get('content', [])
              if item.get('type') == 'text' and isinstance(item.get('text'), str)]}))
    elif args.action in ('prepare_fix', 'prepare_rollback'):
        print(json.dumps(save_token(value), indent=2))
    else:
        print(json.dumps(value, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)