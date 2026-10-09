# Shield Bot operator runbook

The recommended interface is the **AgentShield shared group in SnowBots**.
Shieldbot collects scope, Testbot runs and reports, and Fixbot prepares approved
repairs. See the [README](../README.md) for the full bots-to-agents-to-bots flow.

Run the Shield Bot commands below from this repository root. SnowBots startup
commands run in its separate checkout. Examples use a POSIX shell.

## Before installing

- Use a dedicated sandbox and a named Snowflake CLI connection. The supplied SQL
  uses `ACCOUNTADMIN` and deliberately vulnerable synthetic fixtures.
- Install Python 3, `snow`, and `cortex` with Agent Studio support. Ensure Cortex
  Agents, Cortex Search, semantic views, and `claude-sonnet-4-6` are available to
  the relevant roles. This runbook does not configure account feature access.
- Install/configure SnowBots separately with its Cortex harness and local group
  chat support. The source checkout used here requires Node.js >=22.13.
- Review the numbered SQL and resource names before deploying. Fixtures replace
  demo data and agents; model, warehouse, and search usage incur charges.
- Reserve a maintenance window: no new scans, running task graphs, or applies
  while deploying. There is no atomic rollback of the entire installation.

Set your connection, inspect its identity, and compare the returned account
locator with the intended sandbox before continuing:

```bash
export SHIELD_CONNECTION='your_sandbox_connection'
export SHIELD_ACCOUNT='your_account_locator'
snow sql -c "$SHIELD_CONNECTION" \
  -q 'SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE();'
python3 -m unittest discover -s tests
```

The Python deployment/operation clients check `--expected-account`. Direct
`snow sql -f` commands do not perform that check for you; always use the verified
connection. Never rely on the currently selected IDE connection or a CLI default.

## Fresh sandbox installation

**Fresh installation only.** Do not rerun `01_demo_fixtures.sql` to upgrade or
reset an existing demo: it replaces objects and can erase data and agent changes.
Run each command in order and stop at the first error.

### Base resources and evaluator

```bash
snow sql -c "$SHIELD_CONNECTION" -f deploy/01_demo_fixtures.sql
snow sql -c "$SHIELD_CONNECTION" -f deploy/02_core.sql
snow sql -c "$SHIELD_CONNECTION" -f deploy/03_procs.sql
snow sql -c "$SHIELD_CONNECTION" -f deploy/03b_surface_scores.sql
```

These create the warehouse, original three targets, synthetic data/tools, persona
runners, evidence tables, evaluator, and surface mapper. Surface mapping is also
used when campaigns prepare, so `03b_surface_scores.sql` is not optional here.

### Campaigns, reference library, and approval state

```bash
python3 scripts/build_campaigns.py
python3 scripts/build_templates.py

snow sql -c "$SHIELD_CONNECTION" -f deploy/04_campaign_schema.sql
snow sql -c "$SHIELD_CONNECTION" -f deploy/08_campaign_batches.sql
snow sql -c "$SHIELD_CONNECTION" -f deploy/06_remediation.sql
snow sql -c "$SHIELD_CONNECTION" -f deploy/09_remediation_selections.sql
snow sql -c "$SHIELD_CONNECTION" -f build/campaigns/expand_templates.sql
snow sql -c "$SHIELD_CONNECTION" -f build/campaigns/deploy_campaigns.sql
snow sql -c "$SHIELD_CONNECTION" -f build/campaigns/deploy_agents.sql
```

The builders run locally without Snowflake calls. Deployment uploads the Python
modules and creates the API, workers, report handlers, and gated repair procedures.
The agent deployment creates eight category agents plus the standalone
orchestrator, summarizer, and remediation selector. Category agents generate
cases but have no tools for applying changes. The remediation migration pins
the apply boundary to the account in which it was installed.

### Complete the catalog and install the task graph

```bash
python3 scripts/build_catalog.py --deploy \
  --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT"
snow sql -c "$SHIELD_CONNECTION" -f deploy/05_campaign_tasks.sql
```

Catalog deployment creates the additional synthetic resources and missing
non-legacy agents, verifies all 25 targets, and includes the aggregate HR helper
from `10_department_headcount.sql`. It does not overwrite existing target specs.
The root task is unscheduled; scans trigger it on demand. Four worker slots and
a finalizer share the queue.

**Fixture portability:** the original three targets come from the base fixture
SQL. The catalog builder does not update them. In particular, the selectively
redesigned Leaky Sales configuration used in the department rehearsal is not
installed by this sequence. Existing HR/Finance/Support targets also remain
unchanged on catalog reinstallation. The maintainer-only
`scripts/department_fixtures.py` is account-pinned and is not a general setup
step; do not bypass its check. See [department lifecycle](DEPARTMENT_REHEARSAL.md).
This installs a testable catalog, not a guarantee of reproducing recorded findings.

## Start SnowBots and register the team

In a separate terminal, from your SnowBots source checkout:

```bash
cd /path/to/snowbots
npm install
npm run build
npm run dev
```

Keep that process running. Use `http://localhost:5173/` for the source-checkout UI
and `http://127.0.0.1:8787` for its API. Follow the installed SnowBots version's
instructions for Cortex harness authentication/configuration. A packaged runtime
may serve its UI differently; this setup script requires a compatible local API.

Back in the Shield Bot repository:

```bash
mkdir -p "$HOME/SnowBots"
python3 scripts/snowbots_setup.py \
  --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT" \
  --workspace "$HOME/SnowBots" --dry-run
python3 scripts/snowbots_setup.py \
  --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT" \
  --workspace "$HOME/SnowBots"
```

Set `--workspace` to the actual SnowBots artifact workspace (`WORKSPACE_ROOT`)
if it differs. It must exist and be readable by SnowBots. `--server` can select
another compatible localhost API port. If API authentication is enabled, supply
`APP_SERVER_TOKEN` securely in the environment, never in instructions or source.

Dry-run only prints the proposed configuration. Actual setup verifies the
Snowflake account, snapshots the owned bots/group under private `build/snowbots/`,
and creates or updates three bots and `agentshield-team`. It refuses modes other
than `ask` and preserves unrelated bots. Setup is not atomic; a partial failure
reports the changed IDs and snapshot path.

In SnowBots, confirm each Cortex brain uses the intended sandbox connection.
Open the **AgentShield group**, not an old direct chat. Do not use bypass mode
or grant persistent shell approval to `apply_fix.py`.

## Run, review, repair, retest

1. Say **"Set up a scan, don't start it yet."** Choose groups or individual
   agents, rigor 1-5, and optional custom categories. All eight is the default.
2. Review scope and case count, then reply **Start**. Shieldbot hands Testbot a
   validated contract with a stable request key. Setup-only contracts cannot launch.
3. Testbot launches the batch and polls saved state. If its turn ends, say
   **results** to resume; this is not a guaranteed background notification.
4. On completion, Testbot reads the summary and exports the **initial HTML**.
   It uses authenticated SnowBots artifact sharing with a filename ending `.html`,
   not a public host. COMPLETE does not imply PASS.
5. Fixbot lists eligible repairs and manual-review cases. Select agents,
   categories, or individual findings; it deduplicates actions into one preview
   per agent. Selection alone does not modify the target.
6. Review each preview and **Allow once** or **Deny** the separate `apply_fix.py`
   command. A denial leaves that target unchanged and invalidates the pending token.
7. Once all selected decisions resolve, Testbot dispatches exact-case retests
   for applied bundles. It closes the selection and posts one saved before/after
   chat summary, including remaining failures, inconclusives, and baselines.

There is **no automatic second HTML export**, retry of an earlier failed export,
or second round of fixes. Explicitly requested retest exports remain possible;
backend evidence and stored reports are still retained.

## Approval boundary

Fixbot prepares only reviewed actions: supported side-tool removal, category
guardrails, or exact evidence-bound department recipes. The selection flow
excludes inconclusives from apply eligibility. A supported failed normal-business
baseline can receive a reviewed repair; errors and unknown cases stay manual.

Preparation produces a 15-minute one-time token, bound to the proposal and live
target hash. Snowflake stores its hash; the client stores the secret in a private
`0600` file under `build/fix_tokens/`, never in chat. The separate apply procedure
checks the account, target allowlist, token, unchanged spec, and idle campaigns.
It preserves agent grants, verifies the change, and retains before/after specs
and the supplied receipt. Apply is not exposed through the campaign agent tool.

**Limit:** SnowBots receipts do not independently authenticate which human
clicked. Anyone controlling the connection/token can approve, and overly broad
shell Always-allow permissions can defeat the click boundary. Bot responsibilities
are not separate credentials. This is a sandbox approval workflow, not a
production change-management service.

## Updating an existing installation

Stop new scan submissions and wait for campaigns and task graphs to finish.
For handler/selection updates on an already installed backend:

```bash
python3 -m unittest discover -s tests
python3 scripts/deploy_team.py \
  --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT"
```

This checks account identity, active campaigns, applying changes, and task runs;
snapshots modules/procedure definitions privately; and deploys selection schema
and handlers. It does **not** replace targets, orchestration agents, or tasks.
It assumes the campaign and remediation infrastructure already exists.

For bot instruction updates, rerun `snowbots_setup.py` with the same workspace
and verified account. It does not require restarting the SnowBots repository.
For reference-library or standalone-orchestrator changes, use the separately
documented [intake update](INTAKE_AND_TEMPLATES.md#deployment-and-verification).
Do not run every deployment script for a documentation-only change.

## Terminal alternative

The same account-checked client can operate without SnowBots. These commands
read options, prepare a non-starting intake, then **explicitly launch** a fresh
batch. Replace the key for a genuinely new run; reuse it after an ambiguous reply.

```bash
python3 scripts/campaign_client.py --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT" --list-agents
python3 scripts/campaign_client.py --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT" intake --agents safe_sales,ticket_echo --rigor 2 --categories all --setup-only
python3 scripts/campaign_client.py --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT" launch --agents safe_sales,ticket_echo --rigor 2 --categories all --request-key example-scan-001
python3 scripts/campaign_client.py --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT" watch_batch --batch-id <batch-id>
python3 scripts/campaign_client.py --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT" batch_report_summary --batch-id <batch-id>
python3 scripts/campaign_client.py --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT" batch_report --batch-id <batch-id> --output "$HOME/SnowBots/scan-001.html"
```

The output directory must exist and the filename must be new unless replacement
is explicitly intended with `--overwrite`. Commands typed directly in a terminal
do not get SnowBots' approval UI. Do not treat a receipt string as authorization.
For selection recovery commands see [team interfaces](THREE_BOT_WORKFLOW.md#recovery-interfaces).
The optional `chat` command addresses the standalone Cortex orchestrator and starts
a fresh conversation per invocation; it is not the shared SnowBots group.

## Rollback and recovery

- Keep the batch, campaign, selection, and apply IDs. Resume existing state rather
  than creating duplicate selections or inventing a new launch key after a timeout.
- To undo an applied repair, have Fixbot prepare rollback for its apply ID, show
  the preview, and request another separate Allow-once approval. This restores
  the saved pre-apply spec with hash verification. Undo stacked repairs newest first.
- Operator equivalents are `campaign_client.py ... prepare_rollback --apply-id
  <original-apply-id>`, then a separately approved `apply_fix.py ... --apply-id
  <prepared-rollback-id> --receipt <approval-reference>`. Never reveal the token.
- An interrupted `APPLYING` record requires investigation; do not replay a
  consumed token or overwrite the live spec/audit rows to force recovery.
- Cancellation is cooperative at case boundaries and cannot interrupt a current
  target-agent call. Use `cancel_batch --batch-id <id>` and monitor to terminal state.
- Workers have a one-hour task timeout; the finalizer has ten minutes. Unfinished
  cases stay unresolved, and a failed graph can leave visibly stalled state.
  Inspect task history; do not manually finalize while a worker is active.
- Do not automatically retry a target case whose execution may have occurred.
  Exact retesting is a separate saved campaign, not an erasure of earlier evidence.
- Private snapshots and reports under `build/` are recovery material, not junk
  to delete during cleanup. Review identifiers/content before exporting any artifact.

## Report updates and verification scope

Saved reports do not change when formatter code is deployed.
`RERENDER_CAMPAIGN_REPORT` reformats saved inputs with no model calls or retest.
`REFRESH_CAMPAIGN_REPORT` rebuilds summaries/proposals: batch children use the
deterministic path, while standalone campaigns may invoke summary/selector agents.
`refresh_batch_report` rebuilds a terminal combined report without inference.
These are explicit maintenance operations, not automatic post-fix attachments.

The local shared chat has demonstrated intake, handoffs, native selection cards,
initial HTML delivery, and denial. Later approved backend rehearsals demonstrated
apply, exact retest, and rollback; see [measured outcomes](DEPARTMENT_REHEARSAL.md).
These observations are not a claim that every runtime version, fresh installation,
agent, or rigor level is qualified. Rehearse changes to SnowBots and inspect
failed/inconclusive results honestly. Generated cases can miss planted weaknesses;
configuration checks do not prove behavioral success.