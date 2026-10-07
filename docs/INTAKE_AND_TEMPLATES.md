# Concise intake and expanded reference library

## Low-typing setup

Use `safe`, `leaky`, or `hr` as target aliases. Examples:

- `Set up a scan, don't start it yet.` — the agent asks for target and rigor together.
- `Set up leaky, rigor 3, all categories; do not start yet.`
- `Customize: safe, categories 2 and 8, rigor 1; do not start yet.`

There are no named presets (Quick/Standard/Thorough were removed 2026-10-07 at
user request). When target or rigor is missing, the orchestrator asks both as
two questions; CoWork rendered the target question as a selectable choice in
user testing. Rigor options are labeled with total cases for the current scope:
all eight categories give 8 × rigor + 1, evenly spaced from 9 (rigor 1) to 81
(rigor 10). Each category gets `rigor` cases (changed 2026-10-07 from
`2 * rigor` so all ten levels fit the 100-security-case cap with every
category). Retests of earlier campaigns replay their saved case counts.
`options` returns `rigor_choices_all_categories`.

Stable custom-category numbers:
1. Instruction manipulation
2. Scope violations
3. Sensitive-data disclosure
4. Social engineering
5. Multi-turn attacks
6. Data exfiltration
7. Privilege escalation
8. Malicious instructions in documents

The model maps these choices to canonical API identifiers. Direct API callers
must still supply canonical target/category names and integer rigor. A bare
number means rigor unless answering a category-selection question. Setup and
options-only requests do not authorize dispatch; an explicit Start or complete
run request does. The standalone CLI `chat` starts a fresh conversation, so
include the prior choices when issuing Start through that client.

The normal options payload no longer exposes template counts. Instructions
request short coverage prose instead of an inventory table, reuse supplied
choices and include only missing questions. Exact wording remains model-generated.
Three non-starting sample questions are configured in the agent specification.
Native custom radio buttons/checkbox inputs in CoWork are not verified or
implemented. Starter questions and text choices are not multi-select forms.
Open a fresh CoWork conversation to avoid the earlier inventory response shaping
the next answer. Actual CoWork rendering of starter questions is not yet tested.

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

For a fresh campaign deployment, follow `SNOWBOTS_DEMO.md`. For the existing
sandbox intake/library update, first build the campaign and template artifacts,
then run:

```bash
python3 scripts/deploy_intake.py --connection <sandbox_connection> --expected-account <locator>
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

References:
- [Agent configuration and sample questions](https://docs.snowflake.com/en/user-guide/snowflake-cortex/snowflake-cowork/build-agents)
- [ALTER AGENT specification replacement](https://docs.snowflake.com/en/sql-reference/sql/alter-agent)