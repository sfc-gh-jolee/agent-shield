# Multi-agent sandbox batches

The catalog contains 25 targets: 8 intended controls and 17 primary-weakness
fixtures. These labels describe configuration intent, not measured security.
The original three agents are preserved, including any previously applied fixes.

## Selection and launch

`campaign_client.py` accepts `launch --agents leaky,safe_hr` and repeated
`--group` flags. Groups are `all`, `safe`, `flawed`, `quick5`, and `domain:sales`,
`domain:hr`, `domain:finance`, `domain:support`, `domain:it`. `--agent safe` remains
the original single-target command; `--group safe` selects all eight controls.
Use `--list-agents` for the authoritative catalog. Every call still requires an
explicit connection and expected account locator.

The SnowBot asks for groups or individual agents and rigor. Individual selection
is grouped by domain, using at most six options per question. The SnowBots ACP
adapter supports multi-select arrays; exact runtime card presentation remains a
UI rehearsal check. The bot definition is updated through the local API, without
editing SnowBots source. Existing approval instructions are retained.

## Bounds and concurrency

Each batch creates one child campaign per unique target in one transaction.
Each target uses its catalog persona unless an explicit batch role overrides it.
The case budget is `agents * (2 * rigor * categories + 1)`, at most 1,000.
For 25 agents, rigor 1, two categories, that is 125 cases. All eight categories
at rigor 1 is 425 cases; at rigor 3 it would exceed the cap and is rejected.

Submission is idempotent on the request key and canonical expanded specification.
The CLI prints the key before submission; reuse it with `--request-key` after a
lost response. Child keys are hashed to remain within the 80-character limit.
A duplicate active target is rejected; unrelated targets may run together.

Four task slots share the queue. Claims are serialized under the existing mutex,
ordered by number of jobs already claimed for each campaign and then age. This
spreads workers across targets. `CAMPAIGN_SETTINGS.WORKER_COUNT` may lower the
active slot count to 1-4; increasing beyond four needs more task definitions.
Task graph overlap is disabled. Workers stop accepting jobs after 40 minutes;
the finalizer leaves unclaimed work queued and requests another graph run.
Jobs interrupted mid-execution are not retried or silently passed.

The root remains unscheduled. `EXECUTE TASK` during an active graph queues a later
run. New queued campaigns are prepared in that later run, not included in the
current run's cleanup. Cancellation is cooperative at case boundaries.

Remediation remains deliberately more conservative than admission: *any* active
campaign blocks mutation, because an agent can delegate to another catalog agent.
Submission also blocks while a remediation is APPLYING. There is no batch apply.
Existing per-case approvals, tokens, rollback and exact-case retest remain.

## Evidence and reports

Baselines are domain-specific; exact retests reuse the original saved payloads.
Batch child reports use deterministic formatting instead of serial summarizer
model calls in the finalizer. Standalone campaigns retain their existing flow.

The combined HTML contains an agent/category matrix and child details/IDs.
Download each full report through `report` with its child campaign ID.
`refresh_batch_report --batch-id <id>` rebuilds a terminal batch report from saved
case summaries, without inference, retesting or target changes.
Findings in an expected category are labeled *relevant-category findings*, not
proof that the planted weakness was exploited. Control findings require review,
not automatic classification as false positives. Out-of-scope and inconclusive
coverage stay distinct from observed passes. No validated catch rate is claimed.

## Fixture containment and limitations

New data is synthetic. Restricted values are visibly fake canaries. New owner
procedures belong to a fixture reader role rather than ACCOUNTADMIN and use bound
parameters. The SQL-fragment fixture supports a finite filter grammar; it simulates
over-broad filtering, not a general SQL injection vulnerability. Tracking URLs use
the reserved `.invalid` domain and are never fetched. No external integrations
or network access are created. Existing legacy owner-rights lookup is unchanged.

Deliberate grants are limited to the demo database and existing RT_ test personas.
Some new grants are shared across agents using the same persona; isolation relies
on configured tools as well as instruction scope and is not a production guarantee.
Generic category references can miss a domain-specific planted flaw. A completed
workflow is not a security certification. Model and warehouse calls incur cost;
case caps bound volume but are not a dollar/credit estimate.

`build_catalog.py` produces ignored deployment artifacts and per-agent spec
snapshots under `build/catalog`. It creates missing targets only, preserving
remediated agents on repeat deployment. Run deployments only when campaigns and
their task graph are idle. Never rerun the original fixture reset script to upgrade.

## Live validation (2026-10-08)

- Three-agent smoke: 15 cases, all 12 security cases passed; two baselines passed
  and one failed due to inherited sales-only judge context. Saved timestamps show
  16 overlapping job pairs belonging to different targets.
- Full 25-agent run: all 125 cases recorded. Security: 93 PASS, 6 FAIL,
  1 INCONCLUSIVE. Baselines: 16 PASS, 5 FAIL, 4 INCONCLUSIVE. All four workers
  and the finalizer succeeded, with per-child reports and a combined report.
- Same-key full-batch retry reused the original batch. An overlapping retest of
  an active target returned CAMPAIGN_BUSY without inserting a new campaign.
- Exact child retest completed with all five prompt hashes identical to its
  parent. Its HR baseline passed after the judge context clarification; security
  results were three PASS and one FAIL. No target fix was applied.
- A two-agent queued batch was cancelled during another graph run. Both children
  became CANCELLED, all six cases had zero attempts, and a combined report saved.
- A failed child's per-case remediation preview successfully minted an expiring
  approval token. It was not applied; target mutation still requires human approval.
- Baseline outcomes include judge misclassification of normal HR analyst tools
  and incomplete target responses. The judge context was clarified for future
  runs; historical outcomes were not rewritten. Generated security cases did not
  expose every planted weakness and do not establish a detection rate.
- Combined HTML checked in the local browser at 359px: no page overflow,
  embedded frames, or external assets. Actual SnowBots multi-select/attachment
  end-to-end user interaction remains unverified.