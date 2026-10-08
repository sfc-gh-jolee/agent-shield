"""Explicit-account client for SnowBots or a terminal.

Fix actions: prepare_fix / prepare_rollback mint a one-time token that is written to
a private local file and never printed; they change nothing. Applying is done only by
the separate scripts/apply_fix.py, so an "Always allow" grant for this read-only
client in SnowBots never covers applying a fix.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agentshield_catalog import BY_ALIAS, LEGACY_ALIASES, resolve, public_listing, groups

TOKENS = Path(__file__).resolve().parents[1] / 'build' / 'fix_tokens'
WATCH = Path(__file__).resolve().parents[1] / 'build' / 'watch'
TERMINAL = ('COMPLETE', 'PARTIAL', 'FAILED', 'CANCELLED')
LABELS = {'prompt_injection': 'Instruction manipulation', 'scope_violation': 'Scope violations',
          'pii_extraction': 'Sensitive-data disclosure', 'social_engineering': 'Social engineering',
          'multi_turn': 'Multi-turn attacks', 'data_exfiltration': 'Data exfiltration',
          'role_escalation': 'Privilege escalation', 'indirect_injection': 'Malicious instructions in documents',
          'baseline': 'Baseline (normal question)'}
AGENTS = {name: item['name'] for name, item in BY_ALIAS.items()}
AGENTS.update({name: BY_ALIAS[alias]['name'] for name, alias in LEGACY_ALIASES.items()})


def literal(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "''") + "'"


def sql(connection, statement):
    result = subprocess.run(['snow', 'sql', '-c', connection, '--format', 'json', '-q', statement],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        # SQL errors can echo raw requests. Keep stderr out of the chat; surface only
        # the procedure's own upper-case reason code (e.g. CAMPAIGN_BUSY).
        code = re.search(r'(?:ValueError|RuntimeError): ([A-Z][A-Z_]{2,79})\b', result.stderr + result.stdout)
        raise RuntimeError((code.group(1) if code else 'SNOWFLAKE_COMMAND_FAILED') +
                           ': details suppressed; inspect the CLI locally if needed')
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


def api(connection, action, request):
    response = sql(connection, 'USE WAREHOUSE AGENTSHIELD_WH; CALL AGENTSHIELD_DB.ORCH.CAMPAIGN_API(' +
                   literal(action) + ', ' + literal(json.dumps(request)) + ')')
    value = next(iter(response[0].values()))
    return json.loads(value) if isinstance(value, str) else value


def launch(connection, agent, rigor, categories):
    """Submit and start directly; same validation as the orchestrator path, no model call."""
    if agent not in AGENTS:
        raise ValueError('Unknown agent; use --list-agents')
    available = [item['CATEGORY'] for item in api(connection, 'options', {})['categories']]
    chosen = available if categories in (None, '', 'all') else [item.strip() for item in categories.split(',')]
    submitted = api(connection, 'submit', {'target': 'AGENTSHIELD_DEMO.AGENTS.' + AGENTS[agent],
                                           'role': resolve([agent])[0]['persona'],
                                           'categories': chosen, 'rigor': rigor,
                                           'request_key': 'sb-' + uuid.uuid4().hex})
    started = api(connection, 'start', {'campaign_id': submitted['campaign_id']})
    return {'campaign_id': submitted['campaign_id'], 'status': started['status'], 'agent': agent, 'rigor': rigor,
            'categories': [LABELS.get(item, item) for item in chosen],
            'security_cases': submitted.get('expected_security_cases'), 'baseline_cases': 1}


def launch_batch(connection, agents, selected_groups, rigor, categories, request_key=None):
    selected = resolve(agents.split(',') if agents else [], selected_groups or [])
    available = [item['CATEGORY'] for item in api(connection, 'options', {})['categories']]
    chosen = available if categories in (None, '', 'all') else [item.strip() for item in categories.split(',')]
    key = request_key or 'sb-' + uuid.uuid4().hex
    # Print before the network call so a lost response can be retried with the same key.
    print('REQUEST_KEY: ' + key, flush=True)
    submitted = api(connection, 'submit_batch', {'targets': [item['alias'] for item in selected],
                    'categories': chosen, 'rigor': rigor, 'request_key': key})
    started = api(connection, 'start_batch', {'batch_id': submitted['batch_id']})
    return {**submitted, 'status': started['status'], 'request_key': key,
            'agents': [item['alias'] for item in selected]}


def watch_batch(connection, batch_id, max_seconds, interval):
    deadline = time.monotonic() + max_seconds
    while True:
        current = api(connection, 'batch_status', {'batch_id': batch_id})
        print(json.dumps({key: current.get(key) for key in
                          ('batch_id', 'status', 'agents', 'finished_agents', 'security_counts', 'baseline_counts')}), flush=True)
        if current['status'] in TERMINAL or time.monotonic() + interval > deadline:
            return
        time.sleep(interval)


def tally_lines(current):
    security, baseline = current['security'], current['baseline']['counts']
    counts = security['counts']
    done = counts['PASS'] + counts['FAIL'] + counts['INCONCLUSIVE']
    lines = ['TALLY: ' + str(done) + '/' + str(security['expected']) + ' security cases done | PASS ' +
             str(counts['PASS']) + ' | FAIL ' + str(counts['FAIL']) + ' | INCONCLUSIVE ' + str(counts['INCONCLUSIVE']) +
             ' | baseline ' + next((name for name in ('PASS', 'FAIL', 'INCONCLUSIVE') if baseline[name]), 'pending')]
    per = {}
    for case in current['cases']:
        if case['CATEGORY'] != 'baseline':
            row = per.setdefault(case['CATEGORY'], {'PASS': 0, 'FAIL': 0, 'INCONCLUSIVE': 0, 'pending': 0})
            row[case['VERDICT'] if case['VERDICT'] in row else 'pending'] += 1
    for category, row in sorted(per.items(), key=lambda item: LABELS.get(item[0], item[0])):
        lines.append('  ' + LABELS.get(category, category) + ': ' + ', '.join(
            str(value) + ' ' + key for key, value in row.items() if value))
    return lines


def watch(connection, campaign_id, max_seconds, interval):
    """Print newly finished cases and a running tally until terminal or max_seconds."""
    if not re.fullmatch(r'[0-9a-f-]{36}', campaign_id or ''):
        raise ValueError('--campaign-id must be a full campaign ID')
    WATCH.mkdir(parents=True, exist_ok=True)
    seen_path = WATCH / (campaign_id + '.json')
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
    deadline = time.monotonic() + max_seconds
    while True:
        current = api(connection, 'status', {'campaign_id': campaign_id})
        for case in current['cases']:
            if case['VERDICT'] and case['CASE_ID'] not in seen:
                seen.add(case['CASE_ID'])
                print('CASE ' + case['VERDICT'] + ' | ' + LABELS.get(case['CATEGORY'], case['CATEGORY']) +
                      ('' if case['VERDICT'] == 'PASS' else ' | ' + str(case.get('REASON_TEXT') or case['REASON'] or '') +
                       (' [' + case['REASON'] + ']' if case.get('REASON_TEXT') and case['REASON'] else '')), flush=True)
        seen_path.write_text(json.dumps(sorted(seen)))
        terminal = current['status'] in TERMINAL
        if terminal or time.monotonic() + interval > deadline:
            break
        time.sleep(interval)
    running = sum(case['STATE'] == 'RUNNING' for case in current['cases'])
    print('STATUS: ' + current['status'] + (' | ' + str(running) + ' case(s) running now' if running else ''))
    print('\n'.join(tally_lines(current)))
    print('NEXT: ' + ('campaign finished; fetch report_summary and the report' if terminal
                      else 'still running; run watch again'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--expected-account', required=True, help='CURRENT_ACCOUNT() locator, not a guessed connection label')
    parser.add_argument('action', choices=['options', 'submit', 'start', 'status', 'report', 'report_summary', 'retest', 'cancel', 'chat',
                                           'prepare_fix', 'prepare_rollback', 'launch', 'watch',
                                           'submit_batch', 'start_batch', 'batch_status', 'batch_report',
                                           'batch_report_summary', 'cancel_batch', 'watch_batch',
                                           'refresh_batch_report'], nargs='?', default='options')
    parser.add_argument('--agent', help='launch: one catalog alias (legacy safe/leaky/hr supported)')
    parser.add_argument('--agents', help='launch: comma-separated agent aliases')
    parser.add_argument('--group', action='append', help='launch: group name; repeat to combine groups')
    parser.add_argument('--list-agents', action='store_true')
    parser.add_argument('--request-key', help='Stable retry key for batch launch')
    parser.add_argument('--batch-id')
    parser.add_argument('--rigor', type=int, help='launch: 1 to 5')
    parser.add_argument('--categories', help='launch: all (default) or comma-separated category IDs')
    parser.add_argument('--max-seconds', type=int, default=240, help='watch: stop polling after this long')
    parser.add_argument('--interval', type=int, default=20, help='watch: seconds between polls')
    parser.add_argument('--request', default='{}', help='JSON request object; never credentials')
    parser.add_argument('--campaign-id', help='Campaign to fix (prepare_fix)')
    parser.add_argument('--case-id', help='Failed case whose fix to prepare (prepare_fix)')
    parser.add_argument('--apply-id', help='Prepared apply_id (prepare_rollback)')
    parser.add_argument('--output', type=Path, help='New local HTML file for report download')
    parser.add_argument('--overwrite', action='store_true', help='Explicitly replace an existing HTML export')
    parser.add_argument('--message', help='Orchestrator request (chat only)')
    args = parser.parse_args()
    identity = sql(args.connection, 'SELECT CURRENT_ACCOUNT() AS ACCOUNT')[0]['ACCOUNT']
    if identity.upper() != args.expected_account.upper():
        raise RuntimeError('ACCOUNT_MISMATCH: no campaign call made')
    if args.list_agents:
        print(json.dumps({'agents': public_listing(), 'groups': groups()}, indent=2))
        return
    if args.action == 'launch':
        if args.agents or args.group:
            if args.agent:
                raise ValueError('Use --agent or --agents/--group, not both')
            print(json.dumps(launch_batch(args.connection, args.agents, args.group, args.rigor,
                                          args.categories, args.request_key), indent=2))
            return
        print(json.dumps(launch(args.connection, args.agent, args.rigor, args.categories), indent=2))
        return
    if args.action == 'watch_batch':
        watch_batch(args.connection, args.batch_id, max(20, min(args.max_seconds, 600)), max(5, args.interval))
        return
    if args.action == 'watch':
        watch(args.connection, args.campaign_id, max(20, min(args.max_seconds, 600)), max(5, args.interval))
        return
    if args.action == 'chat':
        if not args.message:
            raise ValueError('--message required')
        request = {'stream': False, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': args.message}]}]}
        statement = "SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN('AGENTSHIELD_DB.ORCH.AGENTSHIELD', " + literal(json.dumps(request)) + ', TRUE) AS RESPONSE'
    elif args.action == 'prepare_fix':
        statement = ('CALL AGENTSHIELD_DB.ORCH.PREPARE_REMEDIATION(' + literal(args.campaign_id or '') + ', ' +
                     literal(args.case_id or '') + ')')
    elif args.action == 'prepare_rollback':
        statement = 'CALL AGENTSHIELD_DB.ORCH.PREPARE_ROLLBACK(' + literal(args.apply_id or '') + ')'
    else:
        request = json.loads(args.request)
        if not isinstance(request, dict):
            raise ValueError('Request must be a JSON object')
        if args.batch_id:
            request['batch_id'] = args.batch_id
        statement = 'CALL AGENTSHIELD_DB.ORCH.CAMPAIGN_API(' + literal(args.action) + ', ' + literal(json.dumps(request)) + ')'
    response = sql(args.connection, 'USE WAREHOUSE AGENTSHIELD_WH; ' + statement)
    value = next(iter(response[0].values()))
    value = json.loads(value) if isinstance(value, str) else value
    if args.action in ('report', 'batch_report'):
        if not args.output or args.output.suffix.lower() != '.html' or not value.get('html'):
            raise ValueError('A completed report and --output new-file.html are required')
        # Existing reports are only replaced when explicitly requested.
        with args.output.open('w' if args.overwrite else 'x', encoding='utf-8') as output:
            output.write(value['html'])
        print(json.dumps({'id': value.get('campaign_id', value.get('batch_id')), 'report_path': str(args.output.resolve()),
                          'status': value['status']}))
    elif args.action == 'chat':
        print(json.dumps({'status': value.get('status'), 'messages': [item['text'] for item in value.get('content', [])
              if item.get('type') == 'text' and isinstance(item.get('text'), str)]}))
    elif args.action in ('prepare_fix', 'prepare_rollback'):
        # ALREADY_COVERED returns no token: nothing to approve for that case.
        print(json.dumps(save_token(value) if value.get('confirm_token') else value, indent=2))
    else:
        print(json.dumps(value, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)