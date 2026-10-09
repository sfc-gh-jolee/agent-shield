# Shield Bot

Test real Snowflake Cortex Agents from a shared SnowBots chat, inspect retained
evidence, approve reviewed repairs, and replay the same tests to measure the result.

**Sandbox prototype, not a production security certification.** The catalog
contains 25 synthetic target agents across Sales, HR, Finance, Support, and IT:
eight intended controls and seventeen intentionally flawed fixtures. Those labels
describe their configuration, not guaranteed test outcomes.

## How the whole flow works

The **SnowBots** are the user-facing team. The **Cortex Agents** in Snowflake
generate test cases or serve as the targets being tested. Python procedures and
Snowflake tasks enforce the workflow and retain the evidence.

```text
YOU -- choose agents, categories, rigor; request Start
 |
 v
Shieldbot -- intake and validated handoff --> Testbot - Red Team Testing
                                                |
                         local campaign_client.py; explicit sandbox check
                                                |
                                                v
                           Snowflake campaign API + durable task queue
                                                |
                               Four shared task workers
                                                |
                     Category Cortex Agents generate fresh test cases
                                                |
                           Save exact cases before executing them
                                                |
                     Persona-owned runners call target Cortex Agents
                                                |
                      Targets use their configured tools and data
                    (Analyst, Search, procedures, other agent tools)
                                                |
                   Capture responses/tools -> checks + structured judge
                                                |
                           Save verdicts, evidence, and proposals
                                                |
                                                v
YOU <-- initial HTML report + chat summary <-- Testbot --> Fixbot
                                                            |
                                       Select findings; review per-agent preview
                                                            |
YOU -- separate Allow-once approval per agent --> apply_fix.py
                                                            |
                               Snowflake verifies token/spec and applies repair
                                                            |
                         All selected decisions resolved --> Testbot
                                                            |
                               Replay the original saved cases in Snowflake
                                                            |
YOU <-- concise before/after chat summary <------------------+
```

- **Shieldbot** collects scope and run intent; setup alone never starts a scan.
- **Testbot** launches and monitors the batch, reads saved results, and attaches
  the initial HTML report. It does not apply changes.
- **Fixbot** offers eligible reviewed repairs and prepares one combined preview
  per selected agent. Selecting a finding is not approval to change that agent.
- **The backend** checks scope, budgets, saved case integrity, and configuration
  hashes. Deterministic checks look for synthetic restricted-data markers and
  forbidden resources; a structured model judge evaluates other cases.
- **Retests** reuse the original payloads, not newly generated attacks. They end
  with a chat summary; a second HTML export requires an explicit request.

The normal shared-chat batch path builds summaries and repair proposals
deterministically. The separate Cortex orchestrator, summarizer, and remediation
selector remain available for the standalone campaign interface; they are not
extra agent calls on every batch report. Surface mapping records metadata
indicators, not proof of effective access or a confirmed breach.

## Prerequisites

- A **dedicated Snowflake sandbox**, with an explicitly named CLI connection and
  its account locator. Never use a shared production/Snowhouse default.
- Authorization to use `ACCOUNTADMIN` for the supplied sandbox deployment.
  The scripts create a warehouse, databases, roles, procedures, semantic views,
  search services, tasks, and Cortex Agents, including deliberately unsafe fixtures.
- Cortex Agents and the configured `claude-sonnet-4-6` model available in the account.
- Python 3, Snowflake CLI (`snow`), and Cortex CLI (`cortex`) with Agent Studio
  commands on `PATH`. Local project scripts use the Python standard library;
  Snowpark dependencies are declared in the deployed procedures.
- A separate, working **SnowBots** installation with the Cortex harness, group
  handoffs, question/approval cards, and artifact sharing. For the source-checkout
  startup below, its current package manifest requires Node.js 22.13 or newer.

Warehouse, search-service, agent, and judge usage incur charges. Evidence can
contain data returned by targets: keep it private and review exports before sharing.

## Setup and run

### 1. Install the Snowflake backend once

Follow [Fresh sandbox installation](docs/SNOWBOTS_DEMO.md#fresh-sandbox-installation)
for the complete ordered deployment. The base fixture script replaces demo data
and agents; **do not use it to upgrade an existing installation**.

For an already installed backend, use the
[safe update paths](docs/SNOWBOTS_DEMO.md#updating-an-existing-installation).
The general installer creates missing catalog agents but does not overwrite
existing targets or reproduce every maintainer-specific fixture redesign.

### 2. Start SnowBots

In a separate terminal, from your SnowBots source checkout:

```bash
cd /path/to/snowbots
npm install
npm run build
npm run dev
```

Keep it running. The source-checkout UI is at `http://localhost:5173/`; the local
API is at `http://127.0.0.1:8787`. SnowBots is a separate project, not installed
by Shield Bot. Follow its own setup instructions to configure the Cortex harness.

### 3. Register the three bots

From this repository root, replace the connection and locator with your sandbox:

```bash
export SHIELD_CONNECTION='your_sandbox_connection'
export SHIELD_ACCOUNT='your_account_locator'
mkdir -p "$HOME/SnowBots"

python3 scripts/snowbots_setup.py \
  --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT" \
  --workspace "$HOME/SnowBots" --dry-run

python3 scripts/snowbots_setup.py \
  --connection "$SHIELD_CONNECTION" --expected-account "$SHIELD_ACCOUNT" \
  --workspace "$HOME/SnowBots"
```

Use the actual SnowBots report workspace if it differs from `~/SnowBots`. If the
local API requires a token, supply `APP_SERVER_TOKEN` securely in the environment;
do not put it in bot instructions or Git. The setup checks the account before
registration and preserves unrelated bots. In SnowBots, select the same sandbox
connection for all three Cortex brains and keep their permission mode at **ask**.

### 4. Run a scan in the shared chat

Open the **AgentShield** group, then say:

> Set up a scan, don't start it yet.

Choose individual agents or groups, rigor 1-5, and all eight categories or a
subset. Review the scope and total, then reply **Start**. Testbot monitors in
bounded polling turns; if the turn ends, say **results** to resume the same batch.
Do not assume a background notification has been scheduled.

The initial HTML includes the test plan, category results, coverage limitations,
risks, and per-agent details. Fixbot then offers eligible repairs. Review the
preview and use **Allow once** or **Deny** separately for each agent. Never grant
persistent approval to `apply_fix.py` or switch the bots to bypass mode.

Once the selected decisions are resolved, Testbot runs exact-case retests for
applied bundles and reports what improved, what remains unresolved, and whether
normal-business baselines still pass. Fixes are not automatically rolled back;
use the [approved rollback workflow](docs/SNOWBOTS_DEMO.md#rollback-and-recovery)
when you want to restore the previous configuration.

## Test scope and interpreting results

Each selected category receives `2 * rigor` security cases per agent, plus one
normal-business baseline per agent. At rigor 2 across all eight categories that
is **32 security cases + 1 baseline = 33 tests per agent**, or 66 for two agents.
The batch cap is 1,000 cases including baselines. More rigor increases the sample
size, not the strength of a fix or a guarantee of finding every weakness.

The eight categories are instruction manipulation, scope violations,
sensitive-data disclosure, social engineering, multi-turn attacks, data
exfiltration, privilege escalation, and malicious instructions in documents.

- **PASS:** the observed case satisfied its expected behavior. Model judgments
  can be wrong, so inspect the evidence when it matters.
- **FAIL:** checks or the judge identified a policy violation.
- **INCONCLUSIVE:** incomplete responses, evaluation errors, or insufficient
  evidence prevented a reliable conclusion. It is not a pass.
- **COMPLETE:** workflow processing finished; it does not mean every test passed.
- **Coverage limitations:** results apply to the sampled cases, selected
  categories, tested persona, and target configuration. All eight categories
  does not mean all possible attacks. Responses can vary on exact retests.

Only supported, eligible findings enter Fixbot's selection flow. Inconclusive
cases and unsupported failures stay visible for manual review. Applying a recipe
successfully is not proof that it solved the observed behavior.

## Repository and further reading

- `snowbots/`: Shieldbot, Testbot, Fixbot, and shared-group definitions.
- `scripts/`: local clients, builders, setup, deployment, and verification tools.
- `src/`: campaign, catalog, report, remediation, and selection handlers.
- `deploy/`: numbered deployment SQL and the reference-template expansion.
- `tests/`: offline regression tests.
- `build/`: private/generated reports, tokens, snapshots, and build outputs;
  ignored by default. The tracked [intro deck](build/ShieldBot_Intro.pptx) is an
  intentional exception; never force-add the rest of this directory.

[Operator runbook](docs/SNOWBOTS_DEMO.md) ·
[Team handoffs and approvals](docs/THREE_BOT_WORKFLOW.md) ·
[Batch limits](docs/BATCH_CAMPAIGNS.md) ·
[Intake and reference library](docs/INTAKE_AND_TEMPLATES.md) ·
[HTML format](docs/HTML_FORMAT.md) ·
[Procedure reference](docs/PROCEDURE_REFERENCE.md) ·
[Validation history](docs/VALIDATION.md) ·
[Department rehearsal outcomes](docs/DEPARTMENT_REHEARSAL.md)

Run offline tests from this repository root:

```bash
python3 -m unittest discover -s tests
```

The sandbox approval model is not production identity enforcement: bot roles
are not separate credentials, receipts do not independently authenticate the
human approver, and overly broad shell permissions can bypass the click boundary.
Keep the dedicated sandbox, explicit account checks, and separate approvals.