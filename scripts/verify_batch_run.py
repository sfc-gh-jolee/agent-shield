"""Wait for a submitted batch; export only summary evidence and self-contained HTML.

No target mutation, fixture reset or network access other than the named Snowflake
connection. The caller provides the batch ID; this script never starts new tests.
"""
import argparse
import json
from pathlib import Path
import time
from campaign_client import api, sql, literal, TERMINAL


def wait(connection, account, batch_id, output, max_seconds):
    if sql(connection, 'SELECT CURRENT_ACCOUNT() AS ACCOUNT')[0]['ACCOUNT'].upper() != account.upper():
        raise RuntimeError('ACCOUNT_MISMATCH')
    deadline = time.monotonic() + max_seconds
    while True:
        batch = sql(connection, 'SELECT STATUS FROM AGENTSHIELD_DB.CORE.CAMPAIGN_BATCHES WHERE BATCH_ID = ' + literal(batch_id))
        if len(batch) != 1:
            raise RuntimeError('BATCH_NOT_UNIQUE')
        states = sql(connection, 'SELECT c.STATUS, COUNT(*) AS AGENTS FROM AGENTSHIELD_DB.CORE.CAMPAIGNS c '
                     'WHERE c.BATCH_ID = ' + literal(batch_id) + ' GROUP BY c.STATUS')
        counts = sql(connection, 'SELECT x.CATEGORY, x.VERDICT, COUNT(*) AS N FROM AGENTSHIELD_DB.CORE.CAMPAIGN_CASES x '
                     'JOIN AGENTSHIELD_DB.CORE.CAMPAIGNS c ON c.CAMPAIGN_ID = x.CAMPAIGN_ID WHERE c.BATCH_ID = ' +
                     literal(batch_id) + ' GROUP BY x.CATEGORY, x.VERDICT')
        print(json.dumps({'status': batch[0]['STATUS'], 'agents': states, 'cases': counts}), flush=True)
        if batch[0]['STATUS'] in TERMINAL:
            break
        if time.monotonic() > deadline:
            raise RuntimeError('VERIFICATION_WAIT_EXPIRED')
        time.sleep(45)
    report = api(connection, 'batch_report', {'batch_id': batch_id})
    if not report.get('html'):
        raise RuntimeError('BATCH_REPORT_MISSING')
    output.mkdir(parents=True, exist_ok=True)
    (output / ('batch-' + batch_id + '.html')).write_text(report['html'])
    (output / ('batch-' + batch_id + '.json')).write_text(json.dumps(report['summary'], indent=2))
    print(json.dumps({'verified_batch': batch_id, 'report': str(output / ('batch-' + batch_id + '.html'))}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--expected-account', required=True)
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--max-seconds', type=int, default=2700)
    args = parser.parse_args()
    wait(args.connection, args.expected_account, args.batch_id, args.output, args.max_seconds)