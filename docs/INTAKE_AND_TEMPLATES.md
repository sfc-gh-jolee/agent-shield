# Concise intake and expanded reference library

## Low-typing setup

Use the AgentShield group in SnowBots. Shieldbot lists all 25 targets in domain
pages for individual selection, or offers catalog groups. Legacy aliases `safe`,
`leaky`, and `hr` still resolve, but are not the whole catalog. Examples:

- `Set up a scan, don't start it yet.` — the agent asks for target and rigor together.
- `Set up leaky, rigor 3, all categories; do not start yet.`
- `Customize: safe, categories 2 and 8, rigor 1; do not start yet.`

There are no named rigor presets. Shieldbot asks selection mode (groups or
individual agents) and rigor together, then shows the applicable selection cards.
Rigor labels show tests per category, for example `2 (4 tests per category)`.
All eight categories give `16 * rigor + 1` total cases per agent: 17, 33, 49, 65,
81 for rigor 1-5. Each category gets `2 * rigor` cases. Rigor is capped at 5 so the
largest all-category scan (80 security cases) stays under the self-imposed
100-case guardrail, which bounds the one-hour worker timeout, single-call case
generation budget, and cost. The cap is Shield Bot code, not a CoCo or Snowflake
limit. `options` returns `rigor_choices_all_categories`.

Stable custom-category numbers:
1. Instruction manipulation
2. Scope violations
3. Sensitive-data disclosure
4. Social engineering
5. Multi-turn attacks
6. Data exfiltration
7. Privilege escalation
8. Malicious instructions in documents

Shieldbot maps these choices to a versioned handoff; the client validates the
canonical identifiers, budget, and intent again before dispatch. Direct API callers
must still supply canonical target/category names and integer rigor. A bare
number means rigor unless answering a category-selection question. Setup and
options-only requests do not authorize dispatch; an explicit Start or complete
run request does. The standalone CLI `chat` starts a fresh conversation, so
include the prior choices when issuing Start through that client.

The normal options payload does not expose reference-template counts. SnowBots
question cards and shared-chat handoffs have been exercised locally. Exact prose
remains model-generated. The standalone Cortex orchestrator's sample questions
and `chat` interface are compatibility options, not substitutes for the three-bot
group or evidence of native forms on other clients.

## Library expansion

`deploy/template_expansion.json` contains 65 new synthetic references with stable
IDs, scenario names, source surfaces, turn sequences and policy expectations.
`scripts/build_templates.py` validates that source and builds an insert-only SQL
migration. The original 56 rows remain unchanged: 55 security references and one
baseline. The resulting library has 120 security references (15 in each of eight
categories) and one baseline. All new rows have `DEMO = FALSE`, retaining the six
legacy demo-only cases. Existing saved campaigns/retest payloads are not edited.

The new indirect-injection references include 11 quoted-content scenarios and
three requests against the existing poisoned Q4 playbook. Together with the
original playbook reference, these make 15 references, **not 15 independently
poisoned source documents**. No search corpus, target tools or target instructions
were modified. Unavailable data and missing source retrieval do not demonstrate
successful enforcement; live evidence still needs review.

New multi-turn references contain two or three turns. Structural validation
rejects duplicate normalized new prompts, missing policy expectations and invalid
turn shapes; it does not prove semantic novelty, generated-case equivalence or
security coverage. All new references currently use high severity; this is a
reference policy classification, not a claim that a vulnerability was observed.

## Deployment and verification

For fresh deployment, follow [the runbook](SNOWBOTS_DEMO.md). The updater below
is for the existing reference library and standalone Cortex orchestrator, not
SnowBots instructions. First build the artifacts:

```bash
python3 scripts/build_campaigns.py
python3 scripts/build_templates.py
python3 scripts/deploy_intake.py --connection <sandbox_connection> --expected-account <locator>
# Instruction-only changes (safe while a campaign runs; no module upload or migration):
python3 scripts/deploy_intake.py --connection <sandbox_connection> --expected-account <locator> --instructions-only
```

The updater checks account identity and no active campaigns, snapshots the live
orchestrator spec/library/grants under ignored `build/campaigns`, applies the
insert-only migration twice to verify idempotency, checks original rows, uploads
the changed campaign module, and alters only orchestrator instructions/sample
questions while retaining other spec fields and grants. Do not run it during a
task graph, or allow concurrent starts/deployments during the maintenance window.
The migration locks the existing admission mutex, rejects conflicting IDs/content,
and rolls back on failure. It never removes extra rows to force a count; the
updater stops if final counts differ from the reviewed baseline.

The updater expects existing campaign procedures importing the staged module;
it is not a replacement for initial deployment. No whole-deployment rollback is
claimed: the additive migration, module upload and agent alteration are separate
operations. Private snapshots support recovery; rerunning is idempotent for the
unchanged library. Keep snapshots out of public Git.

`scripts/check_template_generation.py` performs one bounded, generation-only
indirect-injection check, validates two cases and reports reference IDs/input
sizes without printing generated prompts. It does not create campaigns or call
test-target agents. It incurs a category-agent inference call.

Version fields and existing template IDs are stable migration/evidence keys,
not product branding. Do not rename them to clean up documentation: saved
campaigns and insert-only migrations depend on those identities.

References:
- [Agent configuration and sample questions](https://docs.snowflake.com/en/user-guide/snowflake-cortex/snowflake-cowork/build-agents)
- [ALTER AGENT specification replacement](https://docs.snowflake.com/en/sql-reference/sql/alter-agent)