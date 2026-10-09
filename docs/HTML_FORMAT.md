# Campaign HTML export format

Campaign reports follow the requested
[HTML Report Formatter](https://github.com/Snowflake-Solutions/us-west-agent-suite/tree/main/skills/html-report-formatter),
pinned to source revision `10ca153ab8195528bb852a9a8268816119abe7d5`.
Reviewed inputs: `SKILL.md`, `scripts/html_kit.py`, `scripts/example_build.py`.

`src/agentshield_html_kit.py` is a locally adapted subset, not an installed plugin
or a runtime download. It retains the upstream status vocabulary, light-first
design tokens, sticky navigation, hero/aside, step strip, KPI bars, action-first
filterable table, numbered sections, cards, FAQ, theme toggle and print styles.
`src/agentshield_report.py` maps the saved campaign evidence to those components.
Both modules are included in the Snowflake deployment by `build_campaigns.py`.

## Evidence and safety adaptations

- PASS = teal (`done`), FAIL = amber (`pend`), INCONCLUSIVE and unresolved =
  violet (`val`), not applied/no retest = grey (`drop`). These are per-case
  observations, not an overall security rating.
- Counts are derived from saved case rows and checked against the saved summary.
  Duplicate IDs or mismatched totals reject the render instead of publishing
  contradictory metrics. Missing cases stay visible; baseline is separate.
- Dynamic text/attributes are escaped; metadata JSON escapes script delimiters.
  No raw response, request prompt, SQL execution control or credential is added.
- HTML is self-contained. Small, inspected inline JavaScript only controls the
  theme, filters and browser printing; it has no network calls, storage or eval.
- With scripts disabled, all report content is visible and interactive controls
  are hidden. Downloaded HTML offers the controls when the browser permits scripts.
  Local SnowBots attachment delivery and the exported report have been reviewed;
  embedded-preview behavior still depends on the runtime's sanitizer.
- Printing shows all rows even if a filter was active and forces light colors.
- Timeline dates and completion ticks are not invented. The strip links to
  report sections; only recorded timestamps and states are presented.
- Target/persona/campaign IDs are intentional internal demo provenance. This
  is not a customer-sanitized report; review identifiers before external sharing.

## Refresh behavior

The initial scan is exported and attached automatically. Post-fix retests end
with chat-only comparisons unless an export is explicitly requested; this does
not disable backend report generation or evidence retention.

New campaign reports use the deployed formatter automatically. Existing reports
are snapshots and do not silently change. `RERENDER_CAMPAIGN_REPORT` rebuilds an
existing terminal campaign from its saved summary, surface, proposal and parent
case references, with **no new agent calls, testing or remediation**. It retains
the previously saved HTML if validation fails. Download again to obtain the new
format. Never manually restyle a generated export instead of its renderer.