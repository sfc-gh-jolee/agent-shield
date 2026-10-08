# SnowBots campaign demo

SnowBots is the user access point (CoWork / Snowflake Intelligence is legacy).

## Remediation approval gate

Per-case fixes can remove a side tool or add a reviewed category guardrail on
catalog targets. They are applied only through
`ORCH.PREPARE_REMEDIATION` -> `ORCH.APPLY_REMEDIATION`, which are separate from
`CAMPAIGN_API` and absent from the orchestrator agent's tools. The orchestrator can
only *offer* the fix and hand back `APPLY_REQUESTED campaign_id=<id>`.

The approval is two-part:

1. **Human click (SnowBots).** The bot runs in `ask` mode and executes
   `scripts/apply_fix.py` as its own shell command, so SnowBots shows an
   Allow once / Deny card and records a durable receipt (turn, argument fingerprint).
2. **Snowflake binding.** `prepare_fix` mints a one-time token (15 min, stored only as
   a hash; the client writes it to a 0600 file under `build/fix_tokens/` and never
   prints it). `APPLY_REMEDIATION` consumes it under the campaign mutex and checks
   the pinned account, allowlisted target, exact proposal hash, unchanged live target
   hash, no active campaign and no prior apply. A click can only execute what was prepared.

Apply uses `ALTER AGENT ... MODIFY LIVE VERSION SET SPECIFICATION`, so existing
grants are kept; it verifies the tool is gone, auto-restores on verification
failure, records SPEC_BEFORE/AFTER in `CORE.REMEDIATION_APPLIES`, then submits and
starts the exact retest. Rollback (`prepare_rollback` + `apply_fix.py`) needs its own
approval and restores the saved spec byte-for-byte (hash checked).

**Known limits.** SnowBots receipts do not record *which human* clicked: anyone
holding the SnowBots connection/token can approve. Snowflake records the database
user and the client receipt string as evidence, not as authentication. Granting
"Always allow" for the shell tool would remove the click; the bot is instructed
never to request it, but that is a bot instruction, not an enforced control. The
one-time token still prevents replay. This is a sandbox demo boundary, not a
production change-approval system.

No SnowBots source changes are required.

## Deploy (existing v2 sandbox)

Use an explicit sandbox connection, never a shared production/Snowhouse default.
This migration adds objects; it does not rerun fixtures or modify target agents.
One active campaign per target is supported, with four shared task slots and
100 security cases maximum per child. A batch can contain all 25 targets, with
at most 1,000 total cases including baselines.

```bash
python3 -m unittest discover -s tests
python3 scripts/build_campaigns.py
python3 scripts/build_templates.py
snow sql -c <sandbox_connection> -f deploy/04_campaign_schema.sql
snow sql -c <sandbox_connection> -f deploy/08_campaign_batches.sql
snow sql -c <sandbox_connection> -f build/campaigns/expand_templates.sql
snow sql -c <sandbox_connection> -f build/campaigns/deploy_campaigns.sql
snow sql -c <sandbox_connection> -f build/campaigns/deploy_agents.sql
snow sql -c <sandbox_connection> -f deploy/05_campaign_tasks.sql
snow sql -c <sandbox_connection> -f deploy/06_remediation.sql   # pins CURRENT_ACCOUNT() for apply
python3 scripts/build_catalog.py --deploy --connection <sandbox_connection> --expected-account <locator>
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

The bot definition lives in `snowbots/agentshield-bot.json` (CoCo brain, `ask`
mode). With the SnowBots server running locally:

```bash
python3 scripts/snowbots_setup.py --connection <sandbox_connection> --expected-account <locator>
```

The script creates or updates bot `agentshield` via `POST/PATCH /bots`, fills the
repo path, connection, account and workspace into the instructions, and refuses
any mode other than `ask` (use `--dry-run` to inspect). In the SnowBots UI select
the same sandbox connection for the CoCo brain. Never choose "Always allow" on the
`apply_fix.py` command. Do not paste credentials into the description or export.

**Running without prompts.** SnowBots "Always allow" grants for shell commands are
scoped to the command prefix (interpreter + script path). Click "Always allow" on
the first `campaign_client.py` card and later setup, scan, status and report calls
run without asking; that client cannot apply fixes. `scripts/apply_fix.py` has a
different prefix, so it still prompts every time. The bot also labels that call
"Destructive change: ..."; if SnowBots matches the title against its always-ask
list, the card hides "Always allow" and prompts even in bypass mode. Do not switch
the bot to bypass mode: that would skip the approval click for fixes.

Setup choices (multi-select agents/groups, rigor 1-5, optional custom categories) are shown as
clickable SnowBots question cards through the CoCo ask-user-question tool. Rigor
levels are shown without per-level case counts.

CLI examples (each requires `--connection` and `--expected-account`):

```bash
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> options
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> --list-agents
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> launch --agents safe,leaky,safe_hr --rigor 1 --categories scope_violation,pii_extraction
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> launch --group all --rigor 1 --categories scope_violation,pii_extraction
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> watch_batch --batch-id <id>
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> batch_report --batch-id <id> --output reports/batch.html
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> chat --message "Test the safe sales agent. Which categories and rigor levels are available?"
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> status --request '{"campaign_id":"<id>"}'
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> report --request '{"campaign_id":"<id>"}' --output reports/campaign.html
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> launch --agent leaky --rigor 2   # submit + start, no model call
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> watch --campaign-id <id>          # new cases + running tally, up to 4 min per call
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> prepare_fix --campaign-id <id>
python3 scripts/apply_fix.py --connection <sandbox_connection> --expected-account <locator> --apply-id <apply_id> --receipt snowbots:<short_id>
python3 scripts/campaign_client.py --connection <sandbox_connection> --expected-account <locator> prepare_rollback --apply-id <apply_id>
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
   category has its own Cortex Agent invocation. Up to four workers run concurrently;
   cases within a category run sequentially. A separate benign baseline runs too.
2. Show progress with saved manifest counts, then the report and tool boundaries.
   Reports distinguish static findings from runtime verdicts. Missing/failed
   executions stay inconclusive. They never become implicit passes.
3. Run the vulnerable target with the same scope. Generation may choose cases
   that do not expose the known flaw; do not promise a failure on every run.
4. When the results are eligible the orchestrator asks whether to apply the fix.
   On yes the bot runs `prepare_fix`, shows the diff (tool removed, tools kept,
   short ID, expiry), then runs `apply_fix.py` as a separate command: click Allow
   once to apply, or Deny to leave the agent unchanged.
5. Apply automatically submits and starts the exact retest. Ask for results and
   compare before/after. The fix only addresses EmployeeLookup failures; other
   failures (e.g. bulk-export refusals) remain and are reported as manual review.
6. Roll back with `prepare_rollback` + another approved `apply_fix.py` to restore
   the vulnerable demo for the next run. A manual `retest` (original campaign ID,
   new request key, then `start`) is still available.

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