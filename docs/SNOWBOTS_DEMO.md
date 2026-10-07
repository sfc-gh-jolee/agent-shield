# SnowBots campaign demo

## Current safety gate

**Remediation is preview-only. There is no apply procedure or apply client command.**
The existing SnowBots source has permission cards, argument-bound grants and an
always-ask keyword list, but a generic stored-procedure CALL is not inherently a
human-approval boundary. Both an agent and a human can use the same database role.
An `approved=true` argument or a suggestive procedure name would not solve that.
Do not enable fixes until actual allow-once, deny, cached-grant and bypass-mode
behavior has been verified with an independently authorized apply identity.

No SnowBots source changes are required for the testing/reporting setup below.
The end-to-end SnowBots rehearsal is a separate acceptance gate; SQL validation
alone does not establish it works in the messenger.

## Deploy (existing v2 sandbox)

Use an explicit sandbox connection, never a shared production/Snowhouse default.
This migration adds objects; it does not rerun fixtures or modify target agents.
One active campaign is supported, with two task slots and 100 security cases max.

```bash
python3 -m unittest discover -s tests
python3 scripts/build_campaigns.py
python3 scripts/build_templates.py
snow sql -c <sandbox_connection> -f deploy/04_campaign_schema.sql
snow sql -c <sandbox_connection> -f build/campaigns/expand_templates.sql
snow sql -c <sandbox_connection> -f build/campaigns/deploy_campaigns.sql
snow sql -c <sandbox_connection> -f build/campaigns/deploy_agents.sql
snow sql -c <sandbox_connection> -f deploy/05_campaign_tasks.sql
```

The build extracts the existing evaluation implementation from `03_procs.sql`,
then packages it with the new campaign handlers. The original procedure API and
its tests remain intact. Generated deployment artifacts are ignored by Git.
Deploy only while no campaign/task graph is running. Category/coordination agents
are replaced by the build; the three target agents are not.

Tasks use the existing warehouse and are manually triggered, not scheduled.
Administrative privileges are retained for this dedicated sandbox prototype;
this is not a least-privilege production deployment. No account-wide grants,
external integrations, or credentials are installed. Model and warehouse usage
incur charges. Rigor controls case count, not a guarantee of attack quality.

## Configure the SnowBot

Create a bot with the CoCo brain and select the sandbox connection. Confirm the
actual account locator using SQL; do not rely only on the sidebar label. Use
Ask permission mode. Do not paste credentials into the description or export.

Suggested instructions (replace local path/connection/account placeholders):

> You are the AgentShield demo front end. Specialist agents run in Snowflake.
> Use the local `scripts/campaign_client.py` with the explicitly configured
> sandbox connection and expected account locator. Use `chat` to reach
> `AGENTSHIELD_DB.ORCH.AGENTSHIELD`; relay its questions about target, categories,
> and rigor. Include already collected choices on subsequent chat calls (the
> client starts a fresh orchestration conversation each time). Track the campaign
> ID. Say results (or status) to read saved state; never resubmit a campaign to check progress.
> Download a completed report using `report --output <new workspace file>.html`
> and share it with the existing artifact_share tool. Confirm sharing succeeded
> before saying the attachment is available. Report PASS/FAIL/INCONCLUSIVE and
> static surface indicators, never raw records or prompts. Discovery and scoring
> are outside this demo. Remediation is draft-only; no apply operation exists.
> Never change the target, use other accounts, or run generated SQL as a fix.

CLI examples (each requires `--connection` and `--expected-account`):

```bash
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> options
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> chat --message "Test the safe sales agent. Which categories and rigor levels are available?"
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> status --request '{"campaign_id":"<id>"}'
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> report --request '{"campaign_id":"<id>"}' --output reports/campaign.html
```

The output directory must already exist. The report filename must be new unless
you explicitly add `--overwrite` to replace an existing export.
The client verifies `CURRENT_ACCOUNT()` before issuing the requested operation.
Report content is written locally without dumping the HTML into the chat.
Report sharing is an authenticated SnowBots attachment, not a public upload.

## HTML format and saved-report updates

Exports use the pinned [HTML Report Formatter adaptation](HTML_FORMAT.md), with
status-colored KPIs, action-first case filters, light/dark themes and print styles.
Inline controls make no network calls. All content stays visible if scripts are
disabled; theme, filter and print buttons are hidden in that case. Compatibility
with the actual SnowBots preview remains an uncompleted rehearsal gate.

For a formatter-only deployment, rebuild and deploy `build/campaigns/deploy_campaigns.sql`
using the explicit sandbox connection while no campaign/task graph is active.
There is no need to recreate the agents or tasks. Existing HTML remains a saved
snapshot: use `AGENTSHIELD_DB.ORCH.RERENDER_CAMPAIGN_REPORT` with a terminal
campaign ID to update only its saved HTML from existing inputs. This makes zero
model calls and does not retest or apply fixes. Download again with `report`;
use `--overwrite` only when replacing the intended local report file.
`REFRESH_CAMPAIGN_REPORT` is different: it reruns the summary/selector agents.

## Rehearsal

1. Start with rigor 1 and two categories against the safe demo target. Each
   category has its own Cortex Agent invocation. Two workers run concurrently;
   cases within a category run sequentially. A separate benign baseline runs too.
2. Show progress with saved manifest counts, then the report and tool boundaries.
   Reports distinguish static findings from runtime verdicts. Missing/failed
   executions stay inconclusive. They never become implicit passes.
3. Run the vulnerable target with the same scope. Generation may choose cases
   that do not expose the known flaw; do not promise a failure on every run.
4. Show the allowlisted remediation preview if eligible. Make clear nothing was
   changed and one-click execution is not yet enabled.
5. `retest` with the original campaign ID and a new request key creates another
   queued campaign using exact saved cases and a new baseline conversation.
   Then call `start` with the returned campaign ID. This is not a claim that a
   fix was applied; model output may vary with unchanged inputs.

## Recovery and constraints

- `submit` is idempotent by request key plus request hash. A conflicting request
  with the same key is rejected; an unrelated concurrent campaign is rejected.
- `submit` and `start` are distinct operations. A queued campaign can be started
  again if dispatch failed; `start` refuses to redispatch a running campaign.
- Cancellation is cooperative at case boundaries. It cannot cancel a current
  target-agent call. For a queued campaign, dispatch the graph after cancellation
  so its finalizer records cancelled/incomplete cases.
- Task timeout is one hour per worker, finalizer timeout ten minutes. A finalizer
  marks unfinished cases inconclusive and stores a partial report. If the entire
  graph/finalizer fails, status remains visibly stalled after an hour; inspect
  task history. Do not manually finalize while a worker is still running.
- Never automatically retry a case whose execution may already have occurred.
  Its attempt ID and retained evidence support manual investigation.
- HTML is generated from trusted fields with escaping; model output only orders
  known findings and selects an already eligible recipe. Raw target outputs do
  not become HTML, executable code, or remediation instructions.
- Snapshot checking covers the agent specification, not every underlying policy,
  table or procedure change. Mapping's existing metadata limitations still apply.
- Prompt generation validates format, counts and reference membership; it does
  not prove semantic equivalence to a template. Generated-case quality needs
  review. The same inherited judge limitations remain.
- Do not export a `.snowbot` until the real integration has been rehearsed and
  its exported memory/configuration has been reviewed for confidential data.