"""Report-format components adapted from the requested html-report-formatter.

Source: Snowflake-Solutions/us-west-agent-suite, commit
10ca153ab8195528bb852a9a8268816119abe7d5,
skills/html-report-formatter/scripts/html_kit.py.
Subset adapted for strict evidence escaping and script-disabled SnowBots previews.
See docs/HTML_FORMAT.md for provenance and deliberate differences.
"""
from html import escape
import json

FORMAT_VERSION = 'html-report-formatter/10ca153-agent-shield-v1'


def esc(value):
    return escape(str(value), quote=True)


def pill(state, label):
    if state not in ('done', 'pend', 'val', 'drop', 'blue'):
        raise ValueError('INVALID_DISPLAY_STATE')
    return '<span class="pill p-' + state + '">' + esc(label) + '</span>'


def section(key, number, title, content, subtitle=''):
    return ('<section class="section" id="' + esc(key) + '"><div class="sectionhead"><h2>'
            '<span class="sectionno">' + f'{number:02d}' + '</span>' + esc(title) + '</h2><p>' +
            esc(subtitle) + '</p></div>' + content + '</section>')


def table(headers, rows, table_id=None):
    identifier = ' id="' + esc(table_id) + '"' if table_id else ''
    output = ['<div class="tablewrap"><table' + identifier + '><thead><tr>']
    output.extend('<th scope="col">' + esc(header) + '</th>' for header in headers)
    output.append('</tr></thead><tbody>')
    # Cells are explicitly composed HTML; callers escape each dynamic value.
    for row in rows:
        output.append('<tr data-group="' + esc(row.get('group', '')) + '" data-action="' +
                      ('1' if row.get('action') else '0') + '">')
        output.extend('<td>' + cell + '</td>' for cell in row['cells'])
        output.append('</tr>')
    output.append('</tbody></table></div>')
    return ''.join(output)


def filters(groups, count):
    buttons = [('all', 'All')] + list(groups) + [('action', 'Needs review')]
    return ('<div class="filters interactive" role="group" aria-label="Filter cases">' + ''.join(
        '<button type="button" class="filter' + (' active' if key == 'all' else '') +
        '" data-filter="' + esc(key) + '" aria-pressed="' + ('true' if key == 'all' else 'false') +
        '">' + esc(label) + '</button>' for key, label in buttons) +
        '<span class="count" id="case-count">Showing ' + str(count) + ' of ' + str(count) + ' cases</span></div>')


def page(metadata, content):
    metadata['formatter'] = FORMAT_VERSION
    serialized = json.dumps(metadata, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="snowflake-source" content="cortex-agent-authored">'
            '<title>Shield Bot | Campaign report</title>'
            '<script type="application/json" id="snowflake-report-metadata">' + serialized + '</script>'
            '<style>' + CSS + '</style></head><body>'
            '<header class="topbar"><div class="wrap topinner"><a class="brand" href="#overview">'
            '<span class="brand-mark" aria-hidden="true">SB</span> Shield Bot / Report</a>'
            '<nav aria-label="Sections"><a href="#results">Results</a><a href="#surface">Surface</a>'
            '<a href="#remediation">Remediation</a><a href="#retest">Retest</a></nav>'
            '<div class="tools interactive"><button type="button" id="theme-button" aria-label="Switch to dark theme">Dark</button>'
            '<button type="button" id="print-button">Print</button></div></div></header>'
            '<main class="wrap">' + content + '</main><script>' + JS + '</script></body></html>')


CSS = """
:root{color-scheme:light;--bg:light-dark(#f8fafc,#0c1623);--surface:light-dark(#fff,#122132);--surface-2:light-dark(#f1f7fa,#16283a);--ink:light-dark(#142e48,#edf5ff);--muted:light-dark(#52677d,#a8bbcd);--line:light-dark(#d9e4ec,#304459);--blue:light-dark(#11567f,#60c7f0);--sf:#29b5e8;--blue-wash:light-dark(#eaf7fd,#142f44);--teal:light-dark(#06766d,#61d2bd);--teal-wash:light-dark(#edf9f6,#12332f);--amber:light-dark(#92600a,#f1c36f);--amber-wash:light-dark(#fff6e3,#342b1b);--violet:light-dark(#6856b5,#b9a8ff);--violet-wash:light-dark(#f3efff,#28243e);--grey:light-dark(#6f7f8c,#8a9cab);--grey-wash:light-dark(#eef1f4,#1c2a37)}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:84px}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}a{color:var(--blue);text-underline-offset:3px}button{font:inherit;cursor:pointer;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:6px 12px}button:hover{border-color:var(--sf)}:focus-visible{outline:3px solid var(--sf);outline-offset:3px}
.wrap{max-width:1200px;margin:auto;padding:0 clamp(18px,4vw,56px);min-width:0}.topbar{position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--line)}.topinner{display:flex;align-items:center;gap:24px;min-height:64px}.brand{display:flex;align-items:center;gap:10px;font-size:12px;font-weight:750;letter-spacing:.06em;white-space:nowrap;text-transform:uppercase;text-decoration:none;color:var(--ink)}.brand-mark{color:var(--blue);border:2px solid var(--sf);border-radius:8px;padding:4px}.tools{gap:7px;margin-left:auto}nav{display:flex;align-items:center;margin-left:auto}nav a{position:relative;font-size:13px;font-weight:600;text-decoration:none;color:var(--muted);padding:6px 14px;border-radius:999px}nav a+a{margin-left:13px}nav a+a::before{content:"";position:absolute;left:-7px;top:22%;height:56%;border-left:1px solid var(--line);pointer-events:none}nav a:hover{color:var(--ink);background:var(--surface-2)}nav a[aria-current="true"]{color:var(--blue);background:var(--blue-wash)}
.hero{padding:48px 0 28px;display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:40px;align-items:end}.eyebrow{font-size:11px;letter-spacing:.15em;text-transform:uppercase;font-weight:750;color:var(--blue);margin:0 0 14px}h1{font-size:clamp(32px,4.2vw,54px);line-height:1.08;letter-spacing:-.04em;margin:0 0 18px}h1 span{color:var(--sf)}.dek{font-size:17px;color:var(--muted);max-width:750px;margin:0}.heroaside{border-left:3px solid var(--sf);padding:4px 0 4px 20px}.heroaside strong{display:block;margin-top:12px;font-size:23px}.heroaside p{font-size:13px;color:var(--muted);margin-bottom:0}
.strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-block:1px solid var(--line);padding:14px 0;gap:8px}.chip{display:flex;gap:10px;align-items:center;color:inherit;text-decoration:none;min-width:0;padding:8px}.chip .n{display:grid;place-items:center;border:3px solid var(--sf);background:var(--blue);color:var(--bg);border-radius:50%;width:38px;height:38px;flex-shrink:0;font-weight:800}.chip b,.chip small{display:block}.chip small{font-size:11px;color:var(--muted)}.metaline{display:flex;flex-wrap:wrap;gap:8px 26px;padding:18px 0;color:var(--muted);font-size:13px;overflow-wrap:anywhere}.metaline b{color:var(--ink)}
.note,.risk{padding:14px 18px;border-radius:0 8px 8px 0;margin:0 0 12px;font-size:14px}.note{border-left:3px solid var(--sf);background:var(--blue-wash)}.risk{border-left:3px solid var(--amber);background:var(--amber-wash)}.section{padding:36px 0;border-bottom:1px solid var(--line);min-width:0}.sectionhead{display:flex;justify-content:space-between;gap:24px;align-items:baseline;margin-bottom:20px}.sectionhead h2{font-size:24px;line-height:1.25;margin:0;letter-spacing:-.02em}.sectionhead p{font-size:13px;color:var(--muted);margin:0;text-align:right}.sectionno{font-size:12px;color:var(--sf);margin-right:12px}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.kpi{padding:16px 18px;background:var(--surface);border:1px solid var(--line);border-radius:10px;min-width:0}.kpi .k{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-weight:750}.kpi .v{font-size:32px;font-weight:800;line-height:1.1;margin:8px 0}.kpi small{font-size:12px;color:var(--muted)}.bar{height:8px;border-radius:999px;background:var(--grey-wash);overflow:hidden;display:flex;margin:14px 0}.bar i{display:block;height:100%}.b-done{background:var(--teal)}.b-pend{background:var(--amber)}.b-val{background:var(--violet)}.b-drop{background:var(--grey)}.legend{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.pill{display:inline-block;font-size:11px;font-weight:700;padding:3px 10px;border-radius:999px;white-space:nowrap}.p-done{color:var(--teal);background:var(--teal-wash)}.p-pend{color:var(--amber);background:var(--amber-wash)}.p-val{color:var(--violet);background:var(--violet-wash)}.p-drop{color:var(--grey);background:var(--grey-wash)}.p-blue{color:var(--blue);background:var(--blue-wash)}
.tablewrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px;max-width:100%}table{width:100%;border-collapse:collapse;font-size:13px;text-align:left}th{font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);padding:11px 13px;white-space:nowrap;background:var(--surface-2);border-bottom:2px solid var(--line)}td{padding:12px 13px;border-bottom:1px solid var(--line);vertical-align:top}tbody tr:last-child td{border:0}td code{font-size:11px}td:last-child{min-width:180px;overflow-wrap:anywhere}code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.85em;overflow-wrap:anywhere}.filters{gap:8px;align-items:center;flex-wrap:wrap;margin:18px 0 12px}.filter{font-size:12px}.filter.active{background:var(--blue);color:var(--bg)}.count{margin-left:auto;font-size:12px;color:var(--muted)}.interactive{display:none}.js-enabled .interactive{display:flex}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,250px),1fr));gap:14px}.card{border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:18px;min-width:0;overflow-wrap:anywhere}.card h3{font-size:15px;margin:0 0 8px}.card p{font-size:13px;color:var(--muted);margin:8px 0}.card.gap{border-left:3px solid var(--amber)}details{margin-top:14px;border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:12px 16px}summary{cursor:pointer;font-weight:650}.footnote{font-size:12px;color:var(--muted)}.bottom{display:flex;justify-content:space-between;gap:18px;font-size:12px;color:var(--muted);padding:24px 0 36px}.faq{list-style:none;padding:0;display:grid;gap:10px}.faq li{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 18px}svg{max-width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:860px){nav{display:none}.hero{grid-template-columns:1fr;gap:22px;padding-top:30px}.sectionhead{display:block}.sectionhead p{text-align:left;margin-top:8px}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:600px){.strip{grid-template-columns:1fr 1fr}.topinner{gap:10px}.brand{font-size:10px}.brand-mark{display:none}.tools{gap:4px}.tools button{padding:5px 8px;font-size:12px}.bottom{flex-direction:column}.count{margin-left:0;width:100%}.kpi{padding:12px}.dek{font-size:15px}}
@media screen{.is-hidden{display:none}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media print{:root{color-scheme:light!important}.topbar,.filters,.interactive{display:none!important}body{background:#fff;font-size:11px}.wrap{max-width:none;padding:0}.hero{padding:0 0 16px}.hero h1{font-size:32px}.section{padding:20px 0}.kpi,.card,tr,.note,.risk{break-inside:avoid}.sectionhead{break-after:avoid}details>*{display:block}.tablewrap{overflow:visible}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}@page{size:A4;margin:14mm}}
"""

JS = """
(() => {
  const root = document.documentElement;
  root.classList.add('js-enabled');
  let theme = 'light';
  document.getElementById('theme-button').addEventListener('click', () => {
    theme = theme === 'light' ? 'dark' : 'light';
    root.style.colorScheme = theme;
    const button = document.getElementById('theme-button');
    button.textContent = theme === 'light' ? 'Dark' : 'Light';
    button.setAttribute('aria-label', 'Switch to ' + (theme === 'light' ? 'dark' : 'light') + ' theme');
  });
  document.getElementById('print-button').addEventListener('click', () => window.print());
  const rows = Array.from(document.querySelectorAll('#case-table tbody tr'));
  const buttons = Array.from(document.querySelectorAll('[data-filter]'));
  buttons.forEach(button => button.addEventListener('click', () => {
    const filter = button.dataset.filter;
    buttons.forEach(other => { other.classList.toggle('active', other === button); other.setAttribute('aria-pressed', String(other === button)); });
    let shown = 0;
    rows.forEach(row => {
      const show = filter === 'all' || (filter === 'action' ? row.dataset.action === '1' : row.dataset.group === filter);
      row.classList.toggle('is-hidden', !show);
      if (show) shown++;
    });
    document.getElementById('case-count').textContent = 'Showing ' + shown + ' of ' + rows.length + ' cases';
  }));
  const links = Array.from(document.querySelectorAll('nav a[href^="#"]'));
  const sections = links.map(link => document.getElementById(link.getAttribute('href').slice(1))).filter(Boolean);
  const mark = () => {
    let current = null;
    const line = 140;
    sections.forEach(section => { if (section.getBoundingClientRect().top <= line) current = section.id; });
    if (sections.length && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) current = sections[sections.length - 1].id;
    links.forEach(link => link.setAttribute('aria-current', String(link.getAttribute('href') === '#' + current)));
  };
  links.forEach(link => link.addEventListener('click', () => {
    links.forEach(other => other.setAttribute('aria-current', String(other === link)));
  }));
  window.addEventListener('scroll', mark, { passive: true });
  mark();
})();
"""