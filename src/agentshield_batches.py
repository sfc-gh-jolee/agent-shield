"""Atomic bounded batches; child campaigns retain independent evidence and approval."""
import json
import re
import uuid
import agentshield_campaigns as campaigns
from agentshield_catalog import BY_FQN, resolve


def normalize(request, available):
    selected = resolve(request.get('targets', []), request.get('groups', []))
    specs = [campaigns.validate_request(item['fqn'], request.get('role') or item['persona'],
             request.get('categories'), request.get('rigor'), available) for item in selected]
    if sum(spec['expected_security_cases'] + 1 for spec in specs) > 1000:
        raise ValueError('BATCH_CASE_BUDGET_EXCEEDED')
    return sorted(specs, key=lambda item: item['target'])


def batch(session, batch_id):
    found = campaigns.rows(session, 'SELECT * FROM ' + campaigns.CORE +
                           'CAMPAIGN_BATCHES WHERE BATCH_ID = ?', [batch_id])
    if len(found) != 1:
        raise ValueError('BATCH_NOT_UNIQUE')
    return found[0]


def submit_batch(session, request):
    specs = normalize(request, [row['CATEGORY'] for row in campaigns.options(session)['categories']])
    key = request.get('request_key')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', key):
        raise ValueError('INVALID_REQUEST_KEY')
    request_hash = campaigns.digest(specs)
    batch_id = str(uuid.uuid4())
    children = []
    with campaigns.transaction(session):
        campaigns.lock(session)
        prior = campaigns.rows(session, 'SELECT * FROM ' + campaigns.CORE +
                                'CAMPAIGN_BATCHES WHERE REQUEST_KEY = ?', [key])
        if prior:
            if len(prior) != 1 or prior[0]['REQUEST_HASH'] != request_hash:
                raise ValueError('IDEMPOTENCY_CONFLICT')
            return {'batch_id': prior[0]['BATCH_ID'], 'reused': True, 'status': prior[0]['STATUS']}
        campaigns.execute(session, 'INSERT INTO ' + campaigns.CORE +
                          'CAMPAIGN_BATCHES (BATCH_ID,REQUEST_KEY,REQUEST_HASH,REQUEST,STATUS) '
                          "SELECT ?,?,?,PARSE_JSON(?),'QUEUED'", [batch_id, key, request_hash, json.dumps(specs)])
        for spec in specs:
            # Hashing avoids the 80-character request-key limit for long aliases/keys.
            child_key = 'batch-' + campaigns.digest([key, spec['target']])
            children.append(campaigns.submit(session, {**spec, 'request_key': child_key},
                                              _locked=True, _batch_id=batch_id))
    return {'batch_id': batch_id, 'status': 'QUEUED', 'campaigns': children,
            'total_cases': sum(spec['expected_security_cases'] + 1 for spec in specs), 'dispatch_required': True}


def aggregate(batch_id, children):
    states = [child['status'] for child in children]
    terminal = bool(states) and all(state in campaigns.TERMINAL for state in states)
    state = ('CANCELLED' if all(s == 'CANCELLED' for s in states) else
             'COMPLETE' if all(s == 'COMPLETE' for s in states) else 'PARTIAL') if terminal else 'RUNNING'
    if states and all(s == 'QUEUED' for s in states):
        state = 'QUEUED'
    totals = {name: 0 for name in ('PASS', 'FAIL', 'INCONCLUSIVE', 'unresolved')}
    baselines = dict(totals)
    for child in children:
        for name in totals:
            totals[name] += child['security']['counts'].get(name, 0)
        for case in child['cases']:
            if case['CATEGORY'] == 'baseline':
                baselines[case.get('VERDICT') if case.get('VERDICT') in baselines else 'unresolved'] += 1
    return {'batch_id': batch_id, 'status': state, 'agents': len(children),
            'finished_agents': sum(s in campaigns.TERMINAL for s in states),
            'security_counts': totals, 'baseline_counts': baselines, 'campaigns': children}


def batch_status(session, batch_id):
    saved = batch(session, batch_id)
    ids = campaigns.rows(session, 'SELECT CAMPAIGN_ID FROM ' + campaigns.CORE +
                         'CAMPAIGNS WHERE BATCH_ID = ? ORDER BY TARGET', [batch_id])
    children = [campaigns.status(session, item['CAMPAIGN_ID']) for item in ids]
    result = aggregate(batch_id, children)
    if len(children) != len(campaigns.decoded(saved['REQUEST'])):
        result['status'] = 'FAILED'
        result['reason'] = 'BATCH_MANIFEST_INCOMPLETE'
    return result


def finalize_batches(session):
    pending = campaigns.rows(session, 'SELECT BATCH_ID FROM ' + campaigns.CORE +
                             "CAMPAIGN_BATCHES WHERE STATUS NOT IN ('COMPLETE','PARTIAL','FAILED','CANCELLED')")
    for item in pending:
        save_report(session, item['BATCH_ID'])


def save_report(session, batch_id):
    from agentshield_batch_report import render
    summary = batch_status(session, batch_id)
    report = render(summary) if summary['status'] in campaigns.TERMINAL else None
    campaigns.execute(session, 'UPDATE ' + campaigns.CORE + 'CAMPAIGN_BATCHES SET STATUS = ?, '
                      'SUMMARY = PARSE_JSON(?), REPORT_HTML = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE BATCH_ID = ?',
                      [summary['status'], json.dumps(summary), report, batch_id])
    return {'batch_id': batch_id, 'status': summary['status'], 'report_available': bool(report)}


def run_batch(session, action, request):
    if action == 'submit_batch':
        return submit_batch(session, request)
    batch_id = request['batch_id']
    current = batch(session, batch_id)
    if action == 'refresh_batch_report':
        if current['STATUS'] not in campaigns.TERMINAL:
            raise ValueError('BATCH_STILL_ACTIVE')
        return save_report(session, batch_id)
    if action == 'batch_status':
        return batch_status(session, batch_id)
    if action in ('batch_report', 'batch_report_summary'):
        result = {'batch_id': batch_id, 'status': current['STATUS'],
                  'summary': campaigns.decoded(current['SUMMARY']), 'report_available': bool(current['REPORT_HTML'])}
        if action == 'batch_report':
            result['html'] = current['REPORT_HTML']
        return result
    if action == 'start_batch':
        if current['STATUS'] in campaigns.TERMINAL:
            return {'batch_id': batch_id, 'status': current['STATUS'], 'started': False}
        campaigns.execute(session, 'EXECUTE TASK ' + campaigns.ORCH + 'CAMPAIGN_ROOT')
        return {'batch_id': batch_id, 'status': 'DISPATCHED'}
    if action == 'cancel_batch':
        if current['STATUS'] in campaigns.TERMINAL:
            return {'batch_id': batch_id, 'status': current['STATUS'], 'cancelled': False}
        campaigns.execute(session, 'UPDATE ' + campaigns.CORE + "CAMPAIGNS SET STATUS = 'CANCEL_REQUESTED' "
                          "WHERE BATCH_ID = ? AND STATUS IN ('QUEUED','RUNNING')", [batch_id])
        # Dispatch also finalizes a batch cancelled before it was started.
        campaigns.execute(session, 'EXECUTE TASK ' + campaigns.ORCH + 'CAMPAIGN_ROOT')
        return {'batch_id': batch_id, 'status': 'CANCEL_REQUESTED'}
    raise ValueError('UNKNOWN_ACTION')