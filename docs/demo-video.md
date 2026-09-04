# Demo video

`media/recoup-demo.mp4` — 3 minutes 18 seconds, 1440×900, no audio. The narration is burned in as
captions, so it plays anywhere and needs no sound.

It is recorded against the real stack, not a mockup: every screen is the running console reading
live data through the gateway.

| Time | Segment | What it shows |
|---|---|---|
| 0:00 | Title | |
| 0:06 | Work queue | 356 cases, all opened by the ingestion job, each with the agents' root cause |
| 0:32 | A worked case | a pricing dispute closed end to end: evidence, credit memo, the email, the customer's reply |
| 1:05 | Run trace | every step, its tools, the model, tokens and cost |
| 1:25 | Prompts | the live triage prompt, its versions, and who authored each |
| 1:45 | Models | fast and strong tiers routed per tenant |
| 1:58 | Policy | the JSON rule engine, a DENY rule, and a simulation against 25 past decisions |
| 2:15 | Approval inbox | three live proposals, including a $107k payment plan that crossed the manager threshold |
| 2:35 | Customer memory | durable facts, each naming the case it came from |
| 2:50 | Evals | 83% root-cause accuracy, 0 unauthorized mutations, and 8/8 red-team probes refused |
| 3:05 | Dashboard | exposure, funnel, escalation reasons |

## Re-recording it

The script that drives the recording is not committed: it depends on specific case, run and
customer ids from a seeded database, which change on every reseed. To make a new one, bring the
stack up, seed it, run agents over some cases, then drive the console with Playwright and record
the context. The captions are the part worth keeping: a silent screen recording of an ops console
is unreadable without them.

Two things to check before recording:

- **Filter state.** The work queue defaults to `Open`, which on a mature database is a handful of
  cases rather than the full queue. Click `All` if the point is scale.
- **Detail panes start empty.** The prompts, models and evals tabs all show "select something"
  until a row is clicked. Click one before the caption claims the pane is showing anything.

## One known blemish

At 2:35 the customer header reads `Bailey, Bailey and Williams` while the memory facts below it
talk about `Hopkins Inc`. Both refer to `CUST-0015`. The Mock ERP was reseeded after those cases
ran, which reassigned names to customer refs; the cases and memory facts kept the names they were
written with. The eval harness already detects this class of staleness and reports it as
`case_invoice_customer_mismatch`.

It is a seeded-data artifact, not a bug in the memory writer. Wiping the volumes and reseeding
realigns the names, at the cost of every case and agent run in the database.
