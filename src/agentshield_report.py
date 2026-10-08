"""Evidence-derived reports in the pinned html-report-formatter design system."""
from collections import Counter
import agentshield_html_kit as kit

CASES_PER_RIGOR = 2
BASELINE_CASES = 1
# Display order, plain label and one-line purpose for every category the orchestrator can run.
CATEGORY_INFO = {
    'prompt_injection': ('Instruction manipulation', 'Tries to make the agent ignore, override or replace its own instructions.'),
    'scope_violation': ('Scope violations', 'Asks for help outside the job the agent was built for.'),
    'pii_extraction': ('Sensitive-data disclosure', 'Tries to get personal or confidential data the persona should not see.'),
    'social_engineering': ('Social engineering', 'Uses pretexts, urgency or claimed authority to talk the agent into an exception.'),
    'multi_turn': ('Multi-turn attacks', 'Builds pressure over several turns to wear down a rule the agent keeps at first.'),
    'data_exfiltration': ('Data exfiltration', 'Tries to export data in bulk or send it to an outside destination.'),
    'role_escalation': ('Privilege escalation', 'Tries to get the agent to act above the persona\'s privileges, such as admin or DDL actions.'),
    'indirect_injection': ('Malicious instructions in documents', 'Hides instructions inside documents or tool results to see if the agent follows them.'),
    'baseline': ('Baseline (normal question)', 'An ordinary in-scope request that checks the agent still answers normally. Not an attack.'),
}


def test_plan_html(campaigns, show_planned=True):
    """Category, purpose, and tests per agent implied by the chosen rigor, next to what was recorded."""
    text = kit.esc
    selected = list(dict.fromkeys(category for child in campaigns for category in child['request']['categories']))
    recorded = list(dict.fromkeys(row['CATEGORY'] for child in campaigns for row in child['cases']))
    order = list(CATEGORY_INFO)
    categories = sorted(set(selected + recorded) - {'baseline'},
                        key=lambda category: (order.index(category) if category in order else len(order), category))
    categories.append('baseline')
    rigors = sorted({child['request']['rigor'] for child in campaigns})
    rows, planned_total, recorded_total = [], 0, 0
    for category in categories:
        owners = [child for child in campaigns if category == 'baseline' or category in child['request']['categories']]
        per_agent = sorted({BASELINE_CASES if category == 'baseline' else CASES_PER_RIGOR * child['request']['rigor']
                            for child in owners})
        planned = sum(BASELINE_CASES if category == 'baseline' else CASES_PER_RIGOR * child['request']['rigor']
                      for child in owners)
        count = sum(row['CATEGORY'] == category for child in campaigns for row in child['cases'])
        planned_total, recorded_total = planned_total + planned, recorded_total + count
        name, purpose = CATEGORY_INFO.get(category, (category.replace('_', ' ').title(), 'No description recorded.'))
        cells = [text(name), text(purpose)]
        if show_planned:
            span = (str(per_agent[0]) if len(per_agent) == 1 else
                    str(per_agent[0]) + '-' + str(per_agent[-1]) if per_agent else '0')
            cells += [span, str(len(owners)), str(planned)]
        cells.append(str(count))
        rows.append({'cells': cells})
    headers = ['Category', 'What it tests']
    if show_planned:
        headers += ['Tests per agent', 'Agents', 'Planned tests']
        rows.append({'cells': ['<b>Total</b>', '', '', '', '<b>' + str(planned_total) + '</b>', '<b>' + str(recorded_total) + '</b>']})
    else:
        rows.append({'cells': ['<b>Total</b>', '', '<b>' + str(recorded_total) + '</b>']})
    headers.append('Recorded tests')
    rigor = ', '.join(str(value) for value in rigors)
    intro = ('<p>Rigor ' + text(rigor) + ' runs ' + ' / '.join(str(CASES_PER_RIGOR * value) for value in rigors) +
             ' test(s) per selected category for each agent, plus ' + str(BASELINE_CASES) +
             ' baseline question per agent. Recorded tests can be lower than planned if a run was cancelled or did not finish.</p>'
             if show_planned else
             '<p>This run replays saved cases, so counts show the recorded tests rather than a rigor-based plan.</p>')
    return intro + kit.table(headers, rows)


def state(row):
    return {'PASS': 'done', 'FAIL': 'pend'}.get(row.get('VERDICT'), 'val')


def reason_cell(row):
    """Plain sentence first, original judge reason code underneath for traceability."""
    if not row.get('VERDICT') and not row.get('REASON'):
        return 'Not yet evaluated'
    from agentshield_fixes import describe
    sentence = row.get('REASON_TEXT') or describe(row['CATEGORY'], row.get('VERDICT'), row.get('REASON'))
    code = row.get('REASON')
    return kit.esc(sentence) + ('<br><code class="reasoncode">' + kit.esc(code) + '</code>' if code else '')


def risk_cards(rows, labels, prefix=''):
    """One card per non-passing case with candidate remediation approaches (advisory only)."""
    from agentshield_fixes import methods_for
    if not rows:
        return '<p>' + kit.pill('done', 'No risks observed') + ' Every recorded case passed. This covers only the observed cases.</p>'
    cards = ''.join(
        '<article class="card gap"><h3>' + kit.esc(prefix) + kit.esc(labels.get(row['CATEGORY'], row['CATEGORY'])) + ' ' +
        kit.pill(state(row), row.get('VERDICT') or 'UNRESOLVED') + '</h3><p><code>' + kit.esc(row['CASE_ID']) + '</code></p>'
        '<p><b>What happened:</b> ' + reason_cell(row) + '</p><p><b>Potential remediation:</b></p><ul>' +
        ''.join('<li>' + kit.esc(method) + '</li>' for method in methods_for(row['CATEGORY'], row.get('VERDICT'))) +
        '</ul>' + kit.pill('drop', 'Not yet sent to Fixbot') + '</article>'
        for row in sorted(rows, key=lambda row: row.get('VERDICT') != 'FAIL'))
    return ('<p class="note">Potential remediation lists candidate approaches for each risk. Nothing is sent to Fixbot '
            'or applied from this report; Fixbot prepares reviewed changes only after you select them, and each '
            'needs human approval.</p><div class="grid">' + cards + '</div>')


def render(summary, surface, proposal, comparison=None):
    text = kit.esc
    request = summary['request']
    cases = summary['cases']
    security = [row for row in cases if row['CATEGORY'] != 'baseline']
    baseline = [row for row in cases if row['CATEGORY'] == 'baseline']
    counts = Counter(row.get('VERDICT') if row.get('VERDICT') in ('PASS', 'FAIL', 'INCONCLUSIVE')
                     else 'unresolved' for row in security)
    expected = summary['security']['expected']
    missing = max(0, expected - len(security))
    if (len({row['CASE_ID'] for row in cases}) != len(cases) or
            any(summary['security']['counts'].get(key, 0) != counts[key]
                for key in ('PASS', 'FAIL', 'INCONCLUSIVE', 'unresolved')) or
            summary['security']['missing'] != missing):
        raise ValueError('REPORT_EVIDENCE_TOTALS_MISMATCH')
    attention = counts['FAIL'] + counts['INCONCLUSIVE'] + counts['unresolved'] + missing
    groups = list(dict.fromkeys(row['CATEGORY'] for row in cases))
    group_ids = {group: 'group-' + str(index) for index, group in enumerate(groups)}
    labels = {group: group.replace('_', ' ').title() for group in groups}
    sections = [('plan', 'Test plan'), ('results', 'Observed results'), ('surface', 'Attack surface'),
                ('risks', 'Risks'), ('retest', 'Exact-case retest'), ('faq', 'Reading this report')]
    metadata = {'generated': summary.get('updated_at', summary.get('created_at', 'Unknown')),
                'intent': 'Shield Bot sandbox campaign evidence summary', 'campaign_id': summary['campaign_id'],
                'dataSources': [{'type': 'table', 'name': 'AGENTSHIELD_DB.CORE.CAMPAIGNS'},
                                {'type': 'table', 'name': 'AGENTSHIELD_DB.CORE.CAMPAIGN_CASES'}],
                'sections': [{'id': key, 'title': title, 'producerNotes': 'Saved metadata only; rebuild from campaign state.'}
                             for key, title in sections]}
    workflow_state = 'done' if summary['status'] == 'COMPLETE' else 'drop' if summary['status'] == 'CANCELLED' else 'val'
    body = ['<div class="hero" id="overview"><div><p class="eyebrow">Sandbox security evaluation</p>'
            '<h1>Observed behavior.<br><span>Evidence you can review.</span></h1>'
            '<p class="dek">Category agents test a real target under an explicit persona. '
            'This report separates runtime results, static indicators, and proposed remediation.</p></div>'
            '<aside class="heroaside">' + kit.pill(workflow_state, summary['status']) +
            '<strong>' + str(attention) + ' security cases need review</strong><p>' + str(counts['PASS']) +
            ' recorded passes / ' + str(expected) + ' planned security cases. Baseline tracked separately.</p></aside></div>',
            '<div class="strip" aria-label="Report workflow">']
    for index, (key, label, owner) in enumerate((('results', 'Test', 'Category agents'), ('surface', 'Inspect', 'Surface mapper'),
                                               ('risks', 'Risks', 'Human approval'), ('retest', 'Retest', 'Saved cases')), 1):
        body.append('<a class="chip" href="#' + key + '"><span class="n">' + str(index) + '</span><span><b>' +
                    label + '</b><small>' + owner + '</small></span></a>')
    body.append('</div><div class="metaline"><span><b>Target</b> ' + text(request['target']) + '</span>'
                '<span><b>Persona</b> ' + text(request['role']) + '</span><span><b>Rigor</b> ' + text(request['rigor']) +
                ' / 10</span><span><b>Created</b> ' + text(summary.get('created_at', 'Unknown')) + '</span></div>'
                '<p class="note"><strong>Interpretation:</strong> COMPLETE describes workflow completion, not a '
                'security certification. A pass applies only to the observed case.</p>'
                '<p class="risk"><strong>Fix application is disabled.</strong> No target change has been applied. '
                'Human approval integration and post-fix validation remain required.</p>')
    show_planned = comparison is None and expected == CASES_PER_RIGOR * request['rigor'] * len(request['categories'])
    body.append(kit.section('plan', 1, 'Test plan', test_plan_html([summary], show_planned),
                            'Categories tested and how many tests each received'))
    status_html = '<div class="kpis">'
    for label, value, kind, detail in (
            ('PASS', counts['PASS'], 'done', 'Observed expected behavior'),
            ('FAIL', counts['FAIL'], 'pend', 'Review observed violations'),
            ('INCONCLUSIVE', counts['INCONCLUSIVE'], 'val', 'Evidence needs review'),
            ('UNRESOLVED / MISSING', counts['unresolved'] + missing, 'val', 'Not counted as passes')):
        width = round(100 * value / max(expected, len(security), 1), 1)
        status_html += ('<div class="kpi"><div class="k">' + label + '</div><div class="v">' + str(value) +
                        '</div><div class="bar"><i class="b-' + kind + '" style="width:' + str(width) +
                        '%"></i></div><small>' + detail + '</small></div>')
    status_html += ('</div><div class="legend">' + kit.pill('done', 'Pass') + kit.pill('pend', 'Needs action') +
        kit.pill('val', 'Validate') + kit.pill('drop', 'Out of scope / not applied') + '</div>')
    status_html += ('<p class="footnote">Planned security cases: ' + str(expected) + '; attempted: ' + text(summary['security']['attempted']) +
        '; missing: ' + str(missing) + '. Baseline: ' + str(sum(row.get('VERDICT') == 'PASS' for row in baseline)) +
        ' passing / ' + str(len(baseline)) + ' recorded.</p>')
    status_html += kit.filters([(group_ids[group], labels[group]) for group in groups], len(cases))
    table_rows = [{'group': group_ids[row['CATEGORY']], 'action': state(row) != 'done', 'cells': [
        kit.pill(state(row), row.get('VERDICT') or 'UNRESOLVED'), text(labels[row['CATEGORY']]),
        '<code>' + text(row['CASE_ID']) + '</code>', reason_cell(row)]}
        for row in sorted(cases, key=lambda row: state(row) == 'done')]
    status_html += kit.table(['Verdict', 'Category', 'Evidence reference', 'Reason'], table_rows, 'case-table')
    body.append(kit.section('results', 2, 'Observed results', status_html, 'Action-first cases / baseline separate'))

    tools = surface.get('tools', [])
    surface_html = '<p>Static metadata is not proof of effective authorization or disclosure. Missing grants are not an access-denied verdict.</p><div class="grid">'
    for tool in tools:
        surface_html += ('<article class="card"><h3>' + text(tool.get('tool', 'Unknown tool')) + '</h3>' +
                         kit.pill('val' if tool.get('execute_as') == 'OWNER' else 'blue', tool.get('execute_as', tool.get('type', 'Unknown'))) +
                         '<p>' + text(tool.get('resource', 'Resource not observed')) + '</p><p>Access: ' +
                         text(tool.get('access', 'UNKNOWN')) + '</p></article>')
    surface_html += '</div>'
    if not tools:
        surface_html += '<p>No tool snapshot is available; this is a gap, not evidence of no exposure.</p>'
    surface_html += '<ul>' + ''.join('<li>' + kit.pill('val', 'Static indicator') + ' ' +
        text(finding.get('code')) + ' / ' + text(finding.get('tool')) + '</li>' for finding in surface.get('findings', [])) + '</ul>'
    surface_html += '<p class="footnote">Metadata gaps: ' + str(len(surface.get('gaps', []))) + '.</p>'
    body.append(kit.section('surface', 3, 'Attack surface', surface_html, str(len(tools)) + ' configured tool observations'))
    fixes = proposal.get('fixes') or []
    fix_cards = ''.join(
        '<article class="card gap"><h3>' + text(labels.get(fix['category'], fix['category'])) + ' ' +
        kit.pill('pend' if fix.get('verdict') == 'FAIL' else 'val', fix.get('verdict', '')) + '</h3>'
        '<p><code>' + text(fix['case_id']) + '</code></p><p><b>Why:</b> ' + text(fix.get('why') or '') + '</p>'
        '<ul>' + ''.join('<li>' + text(change) + '</li>' for change in fix.get('changes', [])) + '</ul>' +
        kit.pill('blue', 'Ready for approval') + '</article>' for fix in fixes)
    risky = [row for row in cases if row.get('VERDICT') != 'PASS']
    body.append(kit.section('risks', 4, 'Risks',
        risk_cards(risky, labels) +
        '<div class="grid" style="margin-top:14px"><article class="card"><h3>Reviewed fix proposal</h3>' +
        kit.pill('val', proposal['status']) +
        '<p>' + text(proposal['impact']) + '</p></article><article class="card"><h3>Application gate</h3>' +
        kit.pill('drop', 'Not applied') + '<p>Each fix needs its own human approval. No executable fix control or '
        'credentials are embedded in this file.</p></article></div>' +
        ('<div class="grid" style="margin-top:14px">' + fix_cards + '</div>' if fixes else ''),
        str(len(risky)) + ' risk(s) / ' + str(len(fixes)) + ' reviewed fix(es)'))
    if comparison:
        comparison_html = '<p>Parent campaign: <code>' + text(comparison['parent_campaign_id']) + '</code></p>' + kit.table(
            ['Case', 'Before', 'After'], [{'cells': ['<code>' + text(row['case_id']) + '</code>',
                                                   text(row['before']), text(row['after'])]} for row in comparison['cases']])
        comparison_html += '<p class="note">Identical inputs can produce different model outputs. A retest does not prove a fix was applied.</p>'
    else:
        comparison_html = '<p>' + kit.pill('drop', 'No retest attached') + ' No before/after claim is made.</p>'
    body.append(kit.section('retest', 5, 'Exact-case retest', comparison_html, 'Replay persisted prompts, not new generations'))
    body.append(kit.section('faq', 6, 'Reading this report', '<ul class="faq">'
        '<li><strong>Is this a security score?</strong><br>No. Counts summarize only this campaign and do not certify the target.</li>'
        '<li><strong>Where is the raw evidence?</strong><br>Raw responses, prompts and records remain in restricted sandbox tables. '
        'This export contains only selected metadata, plain-language reasons and reason codes.</li>'
        '<li><strong>Will it work without JavaScript?</strong><br>All results remain visible. Theme, filter and print buttons '
        'appear only when scripts run; use browser printing otherwise.</li></ul>'
        '<details><summary>Provenance and processing</summary><p>Campaign: <code>' + text(summary['campaign_id']) +
        '</code></p><p>Target configuration: <code>' + text(summary.get('target_hash', 'Unknown')) + '</code></p>'
        '<p>Summary ordering: ' + text(summary.get('summarizer_status', 'AGENT_VALIDATED' if summary.get('summarizer_run_id') else 'Not recorded')) +
        '; remediation selection: ' + text(proposal.get('agent_status', 'AGENT_VALIDATED' if proposal.get('agent_run_id') else 'Not recorded')) +
        '.</p></details>'))
    body.append('<footer class="bottom"><span>Shield Bot / Sandbox evidence report</span>'
                '<span>HTML Report Formatter / Offline-ready / No automatic fixes</span></footer>')
    return kit.page(metadata, ''.join(body))