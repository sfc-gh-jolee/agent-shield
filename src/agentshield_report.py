"""Evidence-derived reports in the pinned html-report-formatter design system."""
from collections import Counter
import agentshield_html_kit as kit


def state(row):
    return {'PASS': 'done', 'FAIL': 'pend'}.get(row.get('VERDICT'), 'val')


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
    sections = [('results', 'Observed results'), ('surface', 'Attack surface'),
                ('remediation', 'Remediation preview'), ('retest', 'Exact-case retest'), ('faq', 'Reading this report')]
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
                                               ('remediation', 'Review', 'Human approval'), ('retest', 'Retest', 'Saved cases')), 1):
        body.append('<a class="chip" href="#' + key + '"><span class="n">' + str(index) + '</span><span><b>' +
                    label + '</b><small>' + owner + '</small></span></a>')
    body.append('</div><div class="metaline"><span><b>Target</b> ' + text(request['target']) + '</span>'
                '<span><b>Persona</b> ' + text(request['role']) + '</span><span><b>Rigor</b> ' + text(request['rigor']) +
                ' / 10</span><span><b>Created</b> ' + text(summary.get('created_at', 'Unknown')) + '</span></div>'
                '<p class="note"><strong>Interpretation:</strong> COMPLETE describes workflow completion, not a '
                'security certification. A pass applies only to the observed case.</p>'
                '<p class="risk"><strong>Fix application is disabled.</strong> No target change has been applied. '
                'Human approval integration and post-fix validation remain required.</p>')
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
        '<code>' + text(row['CASE_ID']) + '</code>', text(row.get('REASON') or 'Not yet evaluated')]}
        for row in sorted(cases, key=lambda row: state(row) == 'done')]
    status_html += kit.table(['Verdict', 'Category', 'Evidence reference', 'Reason'], table_rows, 'case-table')
    body.append(kit.section('results', 1, 'Observed results', status_html, 'Action-first cases / baseline separate'))

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
    body.append(kit.section('surface', 2, 'Attack surface', surface_html, str(len(tools)) + ' configured tool observations'))
    body.append(kit.section('remediation', 3, 'Remediation preview',
        '<div class="grid"><article class="card gap"><h3>Review proposal</h3>' + kit.pill('val', proposal['status']) +
        '<p>' + text(proposal['impact']) + '</p></article><article class="card"><h3>Application gate</h3>' +
        kit.pill('drop', 'Not applied') + '<p>No executable fix control or credentials are embedded in this file.</p></article></div>'
        '<details open><summary>Proposed operation</summary><p>' +
        text(proposal.get('operation') or 'No eligible automatic recipe.') + '</p></details>', 'Human approval required'))
    if comparison:
        comparison_html = '<p>Parent campaign: <code>' + text(comparison['parent_campaign_id']) + '</code></p>' + kit.table(
            ['Case', 'Before', 'After'], [{'cells': ['<code>' + text(row['case_id']) + '</code>',
                                                   text(row['before']), text(row['after'])]} for row in comparison['cases']])
        comparison_html += '<p class="note">Identical inputs can produce different model outputs. A retest does not prove a fix was applied.</p>'
    else:
        comparison_html = '<p>' + kit.pill('drop', 'No retest attached') + ' No before/after claim is made.</p>'
    body.append(kit.section('retest', 4, 'Exact-case retest', comparison_html, 'Replay persisted prompts, not new generations'))
    body.append(kit.section('faq', 5, 'Reading this report', '<ul class="faq">'
        '<li><strong>Is this a security score?</strong><br>No. Counts summarize only this campaign and do not certify the target.</li>'
        '<li><strong>Where is the raw evidence?</strong><br>Raw responses, prompts and records remain in restricted sandbox tables. '
        'This export contains only selected metadata and reason codes.</li>'
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