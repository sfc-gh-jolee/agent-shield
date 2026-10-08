"""Evidence-only batch matrix. Expected weakness categories are not proof of a catch."""
from collections import Counter
import agentshield_html_kit as kit
from agentshield_catalog import BY_FQN
from agentshield_report import reason_cell


def coverage(child):
    item = BY_FQN[child['request']['target']]
    cases = [row for row in child['cases'] if row['CATEGORY'] != 'baseline']
    expected = set(item['expected_failing_categories'])
    relevant = [row for row in cases if row['CATEGORY'] in expected]
    if item['kind'] == 'safe':
        return 'Control finding - review' if any(row.get('VERDICT') == 'FAIL' for row in cases) else 'No control finding'
    if not relevant:
        return 'Out of tested scope'
    if any(row.get('VERDICT') == 'FAIL' for row in relevant):
        return 'Relevant-category finding'
    if any(row.get('VERDICT') != 'PASS' for row in relevant):
        return 'Inconclusive coverage'
    return 'No relevant finding observed'


def render(summary):
    text = kit.esc
    children = summary['campaigns']
    categories = sorted({category for child in children for category in child['request']['categories']})
    matrix, detail = [], []
    labels = Counter(coverage(child) for child in children)
    body = ['<div class="hero" id="overview"><div><p class="eyebrow">Multi-agent sandbox evaluation</p>'
            '<h1>One batch.<br><span>Independent evidence.</span></h1><p class="dek">' +
            text(summary['agents']) + ' agents / ' + text(summary['finished_agents']) + ' finished</p></div></div>',
            '<p>Batch <code>' + text(summary['batch_id']) + '</code> / ' + text(summary['status']) + '</p>',
            '<p class="note">Relevant-category findings are screening signals, not verified catches of the planted '
            'weakness. Control findings need review, not automatic labeling as false positives. Missing or '
            'inconclusive evidence is never a pass. No validated catch rate is computed.</p>']
    for index, child in enumerate(children):
        item = BY_FQN[child['request']['target']]
        cells = ['<a href="#agent-' + str(index) + '">' + text(item['alias']) + '</a>',
                 text(item['kind']), text(child['status'])]
        for category in categories:
            cases = [row for row in child['cases'] if row['CATEGORY'] == category]
            counts = Counter(row.get('VERDICT') or 'PENDING' for row in cases)
            label = ' / '.join(str(counts[value]) + ' ' + value for value in ('PASS', 'FAIL', 'INCONCLUSIVE', 'PENDING')
                               if counts[value]) or 'Not selected'
            state = 'pend' if counts['FAIL'] else 'val' if counts['INCONCLUSIVE'] or counts['PENDING'] else 'done'
            cells.append(kit.pill(state, label))
        baseline_cases = [row for row in child['cases'] if row['CATEGORY'] == 'baseline']
        baseline_label = next((row.get('VERDICT') for row in baseline_cases if row.get('VERDICT')), 'PENDING')
        cells.append(kit.pill('done' if baseline_label == 'PASS' else 'pend' if baseline_label == 'FAIL' else 'val',
                              baseline_label))
        cells.append(text(coverage(child)))
        matrix.append({'cells': cells})
        detail.append('<section id="agent-' + str(index) + '"><h3>' + text(item['title']) + '</h3>'
                      '<p>Campaign <code>' + text(child['campaign_id']) + '</code> / persona ' +
                      text(child['request']['role']) + '</p><p>Primary fixture weakness: ' + text(item['flaw'] or 'Control') + '</p>' +
                      kit.table(['Category', 'Verdict', 'Reason'], [{'cells': [text(row['CATEGORY']),
                                text(row.get('VERDICT') or 'PENDING'), reason_cell(row)]} for row in child['cases']]) + '</section>')
    body.append(kit.section('results', 1, 'Agent by category', kit.table(
        ['Agent', 'Fixture', 'Workflow'] + [c.replace('_', ' ').title() for c in categories] + ['Baseline', 'Coverage'], matrix)))
    body.append(kit.section('surface', 2, 'Coverage limitations', '<p>' + text('; '.join(
        str(count) + ' ' + label for label, count in sorted(labels.items()))) + '</p>'))
    body.append(kit.section('remediation', 3, 'Separate approval per child',
        '<p>Open the child campaign report to review its fixes. Applying a fix still requires its own approval '
        'and one-time token. No batch-wide apply is provided; active testing blocks mutation.</p>'))
    body.append(kit.section('retest', 4, 'Per-agent results', ''.join(detail),
                            'Use each campaign ID to download its full report or replay its exact saved cases.'))
    return kit.page({'intent': 'Parallel sandbox campaign batch evidence', 'batch_id': summary['batch_id'],
                     'dataSources': [{'type': 'table', 'name': 'AGENTSHIELD_DB.CORE.CAMPAIGN_BATCHES'},
                                     {'type': 'table', 'name': 'AGENTSHIELD_DB.CORE.CAMPAIGN_CASES'}],
                     'sections': [{'id': key, 'title': label} for key, label in
                                  [('results', 'Matrix'), ('surface', 'Coverage'), ('remediation', 'Approval'),
                                   ('retest', 'Children')]]}, ''.join(body))