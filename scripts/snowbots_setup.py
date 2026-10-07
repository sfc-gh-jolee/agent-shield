"""Create or update the AgentShield SnowBot through the SnowBots HTTP API.

Does not edit the SnowBots repo. Refuses any permission mode other than ask,
because the ask-mode "Allow once" click is the human approval for fixes.
"""
import argparse
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / 'snowbots' / 'agentshield-bot.json'


def bot_payload(definition, connection, account, workspace):
    if definition.get('permissionMode') != 'ask':
        raise ValueError('AgentShield bot must use permissionMode ask')
    for name, value in (('connection', connection), ('account', account)):
        if not value or any(char in value for char in ' \'"`$;&|<>\n'):
            raise ValueError('Unsafe or empty ' + name)
    description = definition['description_template'].format(
        repo=ROOT, connection=connection, account=account, workspace=workspace)
    if len(description) > 10000:
        raise ValueError('Bot instructions exceed SnowBots 10,000 character cap')
    payload = {key: value for key, value in definition.items() if key != 'description_template'}
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
    payload = bot_payload(json.loads(DEFINITION.read_text()), args.connection, args.expected_account, args.workspace)
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return
    existing = [bot for bot in call(args.server, 'GET', '/bots')['bots'] if bot['id'] == payload['id']]
    if existing:
        result = call(args.server, 'PATCH', '/bots/' + payload['id'],
                      {key: value for key, value in payload.items() if key != 'id'})
    else:
        result = call(args.server, 'POST', '/bots', payload)
    bot = result['bot']
    if bot.get('permissionMode') != 'ask':
        raise SystemExit('SnowBots did not keep permissionMode ask; fix the bot before use')
    print(json.dumps({'bot_id': bot['id'], 'permissionMode': bot['permissionMode'],
                      'action': 'updated' if existing else 'created'}))


if __name__ == '__main__':
    main()
