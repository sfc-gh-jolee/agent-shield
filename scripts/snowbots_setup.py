"""Create or update the Shield Bot SnowBot through the SnowBots HTTP API.

Does not edit the SnowBots repo. Refuses any permission mode other than ask,
because the ask-mode "Allow once" click is the human approval for fixes.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / 'snowbots' / 'agentshield-bot.json'
DEFINITIONS = [DEFINITION, ROOT / 'snowbots' / 'testbot.json', ROOT / 'snowbots' / 'fixbot.json']
TEAM_CONTRACT = (
    '\n\nSHARED CHAT DELIVERY: Delegate exactly once with snowbots_crew_control; do not also emit '
    '@handle: lines, since those trigger another delivery. After the tool accepts a task, a short '
    'non-addressed acknowledgement is enough. Do not re-delegate an accepted task unless it failed. '
    'An incoming acknowledgement, no-fixes result, or completed report does not require another '
    'handoff or acknowledgement. If there are no eligible fixes, Fixbot states that once and stops; '
    'do not send Testbot a retest request without a selection_id containing applied changes. '
    'Refer to teammates in plain prose, not routing syntax. Do not repeat an already published summary. '
    'Read existing answers before asking again. Ask missing selection mode and rigor together in '
    'one ask-user-question call; preserve the exact selected option values and do not substitute groups '
    'for individual-agent choices. Use friendly option labels with IDs kept internal. '
    'Fixbot finding checkbox labels must describe the issue and proposed remedy in plain language; '
    'put stable fix/case IDs in option descriptions for traceability, never as the only label. '
    'If every bundle was denied/skipped, Fixbot calls finish_selection itself to close the audit '
    'without dispatching retests; do not leave an OPEN selection or message Testbot unnecessarily. '
    'Cards: never combine a question with a later question that only applies to some answers; '
    'branch with a new card instead. A Skipped/declined card is not cancellation for any bot: keep '
    'prior answers and re-ask the missing step. For Fixbot, a skipped apply-permission card still '
    'means no change; a skipped selection card means ask again, not no fixes. '
    'A setup-intent handoff cannot be launched unchanged: when the user later requests Start, create '
    'a new launch-intent intake with the same scope and a new key, then delegate that contract.'
    '\n\nREPORT DELIVERY: Keep the initial scan HTML attachment. Post-fix retests are chat-only '
    'unless the user explicitly requests a retest report. Fixbot includes "chat-only results; no HTML export" '
    'in its retest handoff. Testbot posts one concise saved before/after summary and stops. Shieldbot '
    'and Fixbot must not request, offer, export, attach or retry a second HTML report, including an '
    'earlier failed retest export. Continue reporting actual apply/retest failures, inconclusives and '
    'baseline regressions; chat-only delivery never changes verdicts or hides testing errors.'
)


def bot_payload(definition, connection, account, workspace):
    if definition.get('permissionMode') != 'ask':
        raise ValueError('Shield Bot bot must use permissionMode ask')
    for name, value in (('connection', connection), ('account', account)):
        if not value or any(char in value for char in ' \'"`$;&|<>\n'):
            raise ValueError('Unsafe or empty ' + name)
    description = (definition.get('batch_instructions', '') + '\n\n' + definition['description_template']).format(
        repo=shlex.quote(str(ROOT)), connection=connection, account=account, workspace=shlex.quote(str(workspace))).strip()
    description += TEAM_CONTRACT
    if len(description) > 10000:
        raise ValueError('Bot instructions exceed SnowBots 10,000 character cap')
    payload = {key: value for key, value in definition.items() if key not in ('description_template', 'batch_instructions')}
    payload['description'] = description
    return payload


def call(base, method, path, body=None):
    request = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'content-type': 'application/json'})
    token = os.environ.get('APP_SERVER_TOKEN')
    if token:
        request.add_header('authorization', 'Bearer ' + token)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def setup_payloads(connection, account, workspace):
    bots = [bot_payload(json.loads(path.read_text()), connection, account, workspace) for path in DEFINITIONS]
    group = json.loads((ROOT / 'snowbots' / 'group.json').read_text())
    if {member['botId'] for member in group['members']} != {bot['id'] for bot in bots}:
        raise ValueError('Group membership mismatch')
    if [member['botId'] for member in group['members'] if member['lead']] != ['agentshield']:
        raise ValueError('Shieldbot must be the only lead')
    return {'bots': bots, 'group': group}


def install(base, payloads):
    before = call(base, 'GET', '/bots')
    # GET /bots includes groups in the supported SnowBots API.
    if 'groups' not in before:
        raise ValueError('SnowBots server lacks group roster support; nothing changed')
    existing = {bot['id']: bot for bot in before['bots']}
    groups = {group['id']: group for group in before['groups']}
    for bot in payloads['bots']:
        if existing.get(bot['id'], {}).get('sealed'):
            raise ValueError('A workflow bot is private; unseal it explicitly before group setup')
    snapshot = ROOT / 'build' / 'snowbots' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    snapshot.mkdir(parents=True, mode=0o700)
    snapshot.chmod(0o700)
    # Snapshot only the objects this installation owns, never unrelated bot memory/config.
    prior = {'bots': [existing[bot['id']] for bot in payloads['bots'] if bot['id'] in existing],
             'groups': [groups[payloads['group']['id']]] if payloads['group']['id'] in groups else []}
    path = snapshot / 'before.json'
    with path.open('x') as output:
        path.chmod(0o600)
        json.dump(prior, output, indent=2)
    changed = []
    try:
        for kind, payload in [('bots', bot) for bot in payloads['bots']] + [('groups', payloads['group'])]:
            collection = existing if kind == 'bots' else groups
            present = payload['id'] in collection
            body = {key: value for key, value in payload.items() if key != 'id'} if present else payload
            result = call(base, 'PATCH' if present else 'POST', '/' + kind + ('/' + payload['id'] if present else ''), body)
            changed.append(payload['id'])
            actual = result['bot' if kind == 'bots' else 'group']
            if any(actual.get(key) != value for key, value in payload.items()):
                raise ValueError('Configuration read-back mismatch: ' + payload['id'])
        return {'bots': [bot['id'] for bot in payloads['bots']], 'group_id': payloads['group']['id'],
                'snapshot': str(path), 'permissionMode': 'ask'}
    except Exception as exc:
        raise RuntimeError('Partial setup; changed=' + ','.join(changed) + '; snapshot=' + str(path) +
                           '; error=' + str(exc)) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', required=True, help='Sandbox snow CLI connection name')
    parser.add_argument('--expected-account', required=True, help='CURRENT_ACCOUNT() of the sandbox')
    parser.add_argument('--server', default='http://127.0.0.1:8787')
    parser.add_argument('--workspace', default=str(Path.home() / 'SnowBots'),
                        help='SnowBots WORKSPACE_ROOT; reports are written here for artifact_share')
    parser.add_argument('--dry-run', action='store_true', help='Print the payload only')
    args = parser.parse_args()
    if urllib.parse.urlparse(args.server).hostname not in ('127.0.0.1', 'localhost'):
        raise SystemExit('Only a local SnowBots server is supported by this script')
    payload = setup_payloads(args.connection, args.expected_account, args.workspace)
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return
    from campaign_client import sql
    identity = sql(args.connection, 'SELECT CURRENT_ACCOUNT() AS ACCOUNT')[0]['ACCOUNT']
    if identity.upper() != args.expected_account.upper():
        raise ValueError('ACCOUNT_MISMATCH: no bot updated')
    print(json.dumps(install(args.server, payload)))


if __name__ == '__main__':
    main()
