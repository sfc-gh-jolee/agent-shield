"""Evidence-only batch matrix. Expected weakness categories are not proof of a catch."""
from collections import Counter
import agentshield_html_kit as kit
from agentshield_catalog import BY_FQN
from agentshield_report import reason_cell, risk_cards, test_plan_html


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


MEANING = {
    'Relevant-category finding': 'A test in a category this fixture is built to fail did fail. A screening signal, not a verified catch.',
    'No relevant finding observed': 'The categories this fixture should fail were tested, but no case failed at this sample size.',
    'Inconclusive coverage': 'Some expected-weakness cases did not produce a clear verdict; treat as untested until rerun.',
    'Out of tested scope': 'None of the categories this fixture should fail were selected, so its planted weakness was not exercised.',
    'Control finding - review': 'A safe control agent failed a case. Review it; it may be a real gap or a strict judge.',
    'No control finding': 'The safe control agent passed every recorded case.',
}


def label(category):
    return category.replace('_', ' ').title()


def coverage_html(children, categories):
    text = kit.esc
    rows, untested, pending = [], [], 0
    for child in children:
        item = BY_FQN[child['request']['target']]
        expected = item['expected_failing_categories']
        cases = [row for row in child['cases'] if row['CATEGORY'] != 'baseline']
        pending += sum(row.get('VERDICT') not in ('PASS', 'FAIL') for row in cases)
        tested = [category for category in expected if any(row['CATEGORY'] == category for row in cases)]
        untested += [(item['title'], category) for category in expected if category not in tested]
        result = coverage(child)
        rows.append({'cells': [
            text(item['title']), text(item['kind'].title() + (' / ' + item['flaw'].replace('_', ' ') if item['flaw'] else '')),
            text(', '.join(label(category) for category in expected) or 'None (control)'),
            text(', '.join(label(category) + ' (' + str(sum(row['CATEGORY'] == category for row in cases)) + ')'
                           for category in tested) or 'None'),
            kit.pill('pend' if 'finding' in result and not result.startswith('No') else
                     'val' if result in ('Inconclusive coverage', 'Out of tested scope') else 'done', result),
            text(MEANING.get(result, ''))]})
    rigor = children[0]['request']['rigor'] if children else 0
    skipped = sorted(set(kit_categories()) - set(categories))
    limits = [
        'Each selected category ran ' + str(2 * rigor) + ' test(s) per agent at rigor ' + str(rigor) +
        '. A small sample can miss a weakness; a pass applies only to the observed cases.',
        'Categories not selected in this batch: ' + (', '.join(label(c) for c in skipped) if skipped else 'none') + '.',
        str(len(untested)) + ' expected-weakness categor' + ('y was' if len(untested) == 1 else 'ies were') +
        ' not tested' + (': ' + '; '.join(title + ' - ' + label(c) for title, c in untested) if untested else '') + '.',
        str(pending) + ' security case(s) are inconclusive or pending and are not counted as passes.',
        'Model outputs vary between runs; the same saved case can produce a different verdict on retest.',
        'Results come from synthetic sandbox fixtures and a single persona per agent; they do not certify production agents.',
        'Expected-weakness categories describe how a fixture was built, not proof that a failure was caught for that reason.']
    return (kit.table(['Agent', 'Fixture', 'Expected weak categories', 'Tested (cases)', 'Coverage', 'What it means'], rows) +
            '<h3>Limitations</h3><ul>' + ''.join('<li>' + text(item) + '</li>' for item in limits) + '</ul>')


def kit_categories():
    """Every security category the orchestrator can run, for listing what a batch skipped."""
    from agentshield_fixes import GUARDRAILS
    return list(GUARDRAILS)


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
            cell = ' / '.join(str(counts[value]) + ' ' + value for value in ('PASS', 'FAIL', 'INCONCLUSIVE', 'PENDING')
                              if counts[value]) or 'Not selected'
            state = 'pend' if counts['FAIL'] else 'val' if counts['INCONCLUSIVE'] or counts['PENDING'] else 'done'
            cells.append(kit.pill(state, cell))
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
    body.append(kit.section('plan', 1, 'Test plan', test_plan_html(children),
                            'Categories tested and how many tests each received'))
    body.append(kit.section('results', 2, 'Agent by category', kit.table(
        ['Agent', 'Fixture', 'Workflow'] + [label(c) for c in categories] + ['Baseline', 'Coverage'], matrix)))
    body.append(kit.section('surface', 3, 'Coverage limitations', coverage_html(children, categories),
                            '; '.join(str(count) + ' ' + name for name, count in sorted(labels.items()))))
    risky = [(BY_FQN[child['request']['target']]['title'], row) for child in children
             for row in child['cases'] if row.get('VERDICT') != 'PASS']
    risks = ''.join(
        '<h3>' + text(title) + '</h3>' + risk_cards([row for owner, row in risky if owner == title],
                                                       {c: label(c) for c in categories + ['baseline']})
        for title in dict.fromkeys(title for title, _ in risky))
    body.append(kit.section('risks', 4, 'Risks', (risks or risk_cards([], {})) +
        '<p class="footnote">To act on a risk, ask Fixbot for remediation choices. Each agent bundle needs its '
        'own preview and approval with a one-time token; there is no batch-wide apply and active testing blocks changes.</p>',
        str(len(risky)) + ' non-passing case(s) across ' + str(len({t for t, _ in risky})) + ' agent(s)'))
    body.append(kit.section('retest', 5, 'Per-agent results', ''.join(detail),
                            'Use each campaign ID to download its full report or replay its exact saved cases.'))
    return kit.page({'intent': 'Parallel sandbox campaign batch evidence', 'batch_id': summary['batch_id'],
                     'dataSources': [{'type': 'table', 'name': 'AGENTSHIELD_DB.CORE.CAMPAIGN_BATCHES'},
                                     {'type': 'table', 'name': 'AGENTSHIELD_DB.CORE.CAMPAIGN_CASES'}],
                     'sections': [{'id': key, 'title': title} for key, title in
                                  [('plan', 'Test plan'), ('results', 'Agent by category'), ('surface', 'Coverage limitations'),
                                   ('risks', 'Risks'), ('retest', 'Per-agent results')]]}, ''.join(body))