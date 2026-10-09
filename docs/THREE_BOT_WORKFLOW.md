# AgentShield shared-chat team

Open the **AgentShield** group, not the old Shieldbot direct chat:

- **Shieldbot** collects agents/groups, rigor 1-5, and optional categories.
- **Testbot - Red Team Testing** validates the handoff, launches the existing Cortex campaign workers,
  monitors saved state, and posts the initial scan summary and HTML attachment.
  Post-fix retests end with a short chat summary, not another HTML attachment.
- **Fixbot** offers all eligible remedies, selected agents, or categories and
  individual findings, using paginated checkbox cards. It prepares one combined
  preview and requests one separate human approval per agent.

The three Snowbots coordinate the existing testing engine. They do not expand its
allowlist or create new testing agents on each run. Persona separation is not
credential or role isolation.

## Scope and approval

Selecting checkboxes records intent; it does not apply a change. Fixbot expands
the choices into immutable finding IDs, deduplicates their reviewed actions, and
shows the tools/instructions affected plus findings left unselected. The separate
`apply_fix.py` command must receive **Allow once** for each agent. Never allow it
permanently. A denied bundle is skipped and its pending token invalidated.

Partial remediation is supported: retest the saved cases after all selected
agents' decisions are resolved, even if unselected fixes remain. Retests do not
start between agent approvals. Applied configuration is not proof of a passing
test. Baseline regressions, manual-review cases, and inconclusives remain visible.

The initial HTML report remains part of the demo. After fixes, Testbot monitors
the exact-case retests, closes the selection with `finish_selection`, and reads
the saved comparison through `selection_status` (`report_summary` only if needed).
It posts one concise before/after chat summary with applied changes, remaining
failures, skipped findings, inconclusives and the baseline outcome, then stops.
The team must not offer, export, attach or retry a retest HTML report unless the
user explicitly asks for one. Backend evidence and stored report generation are
unchanged; only automatic chat-side export/attachment is suppressed.

Multiple agent changes are not atomic. If the chat stops, resume using the saved
selection ID; do not create a new selection to recover. An interrupted APPLYING
operation requires manual investigation rather than automatic replay. Expired or
stale previews require fresh preparation and human approval. Rollback restores
the previous per-agent bundle snapshot and needs its own approval.

## Install

For fresh setup, use the [operator runbook](SNOWBOTS_DEMO.md). The commands below
update an already installed sandbox during a maintenance window with no new scans.
Deployment checks the expected account, active campaigns, applying changes, and
task runs. It snapshots existing modules and procedure definitions privately and
adds selection tables/procedures without replacing Cortex agents or tasks.

```bash
python3 -m unittest discover -s tests
python3 scripts/deploy_team.py --connection <sandbox> --expected-account <locator>
python3 scripts/snowbots_setup.py --connection <sandbox> --expected-account <locator> --dry-run
python3 scripts/snowbots_setup.py --connection <sandbox> --expected-account <locator>
```

Setup creates/updates the existing `agentshield` plus `agentshield-test` and
`agentshield-fix`, and group `agentshield-team`. Existing unrelated settings are
preserved. All use `ask` mode. Ensure each CoCo brain uses the same sandbox
connection in SnowBots. Setup stores a private before-snapshot and reports partial
installation failures; it does not claim setup changes are atomic.

## Recovery interfaces

All client commands require `--connection <sandbox> --expected-account <locator>`.

- `intake --agents safe,leaky --rigor 1 --categories scope_violation --setup-only`
  validates without submitting. Omit `--setup-only` only for an authorized run.
- `launch_handoff --request '<returned JSON>'` validates the full contract;
  repeated deliveries reuse the same backend request key. The scope hash detects
  accidental changes, not authenticated user consent.
- `remediation_options --batch-id <id>` returns safe proposal metadata.
- `select_fixes --batch-id <id> --request '<selection JSON>'` records explicit
  `items` (`campaign_id`, `proposal_hash`, `case_ids`) and `request_key`.
- `selection_status --selection-id <id>` returns decisions and retest comparisons.
- `prepare_bundle --selection-id <id> --campaign-id <id>` produces the combined
  preview. Its token remains in a private local file, never the chat.
- `skip_bundle --selection-id <id> --campaign-id <id>` records human denial/skip.
- `finish_selection --selection-id <id>` refuses pending decisions, queues exact
  retests for successfully applied bundles, and resumes a failed dispatch without
  reapplying anything. Call again once retests finish to close the selection.

Generated handoffs, reports, snapshots and tokens remain under ignored `build/`.
Use SnowBots authenticated attachments, never a public file host.

## Limitations and release gate

The existing sandbox approval model is unchanged: receipt text does not
independently identify a human, and broad shell Always allow grants can defeat the
click boundary. Tokens bind the exact proposed scope and live specification and
prevent replay, but do not make this a production change-approval service.

The local runtime has demonstrated group handoffs, specialist-authored selection
cards, and HTML attachment delivery. Rehearse these again when changing SnowBots
versions. A positive apply and rollback rehearsal requires the user's concrete
per-agent approval; plan approval alone never authorizes changing a target.

## Verification checkpoint (2026-10-08)

This is historical evidence. Later approved backend apply/retest/rollback results
are recorded in [the department rehearsal](DEPARTMENT_REHEARSAL.md); those do not
imply every UI permission path or agent is qualified.

- 126 offline regression tests pass, covering the new handoffs, selection validation, bundle
  gates, denial, selective retest scheduling, and existing legacy behavior.
- Live shared-chat smoke: Shieldbot setup-only produced no scan; subsequent
  explicit launch delegated to Testbot and completed two security checks plus
  the benign baseline, all PASS. Testbot published the HTML attachment in the
  same group and handed off to Fixbot, which confirmed no eligible fixes.
- Saved-batch backend rehearsal verified idempotent selection, combined preview,
  pending-decision retest rejection, denial/token invalidation, and unchanged
  target configuration. No apply command was invoked.
- Fixbot's native cards were exercised for selection mode, eligible agents, and
  individual findings against an earlier completed batch. Its backend excludes
  inconclusive findings from the eligible list and lists them for manual review.
- The shared-chat preview reached the separate `apply_fix.py` permission card;
  **Deny** was exercised rather than Allow. The UI still offered Always allow
  despite the destructive-change title, so the documented approval limitation
  remains material: do not grant persistent approval to that script.
- Initial live routing produced redundant acknowledgements because both explicit
  crew handoffs and `@handle:` text could route work. Shared bot instructions now
  require one routing mechanism and suppress acknowledgement loops. Stable keys
  prevented duplicate campaigns during the rehearsal.
- Positive bundled apply, exact retest after that apply, and rollback were not
  exercised at this checkpoint. Later explicit approvals enabled the backend
  rehearsals linked above.