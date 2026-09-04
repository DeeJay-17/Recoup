# Demo script (phases 1–3)

1. `make up && make seed` — ~400 invoices; ~45% are overdue with a scripted root cause.
2. Open http://localhost:3000, sign in as `ava@acme-demo.com / password`.
3. **Work queue** shows one case per overdue invoice, prioritised by amount × age.
4. Open a case: customer panel (contacts, PO requirement, credit hold, risk), invoice lines,
   append-only activity timeline.
5. Simulate an agent proposal (until phase 3 agents exist):
   ```bash
   CASE=<case id>; TENANT=<tenant id>
   curl -X POST "localhost:8003/internal/cases/$CASE/actions?tenant_id=$TENANT" -H 'content-type: application/json' -d '{
     "action_type":"CREATE_CREDIT_MEMO","payload":{"invoice_ref":"INV-100123","amount":"312.40","reason_code":"PRICING"},
     "rationale":"Lines 1 and 3 invoiced above contract price (SKU-0012 $48.10 vs $41.00).",
     "evidence_refs":["po:PO-50012","contract:CUST-0007"],
     "policy_decision":"REQUIRE_APPROVAL","policy_rule":"credit_memo_over_250_requires_manager","required_role":"manager",
     "proposed_by":"agent:reconciler"}'
   ```
   The case flips to `PENDING APPROVAL`; the card appears in **Approvals**.
6. As `ava` (analyst) the approve button is hidden (requires manager). Sign in as `marcus@acme-demo.com`
   and approve / edit / reject with a feedback note → timeline + `case.action.*` event on Kafka
   (see Redpanda console :8090, topic `recoup.case`).
7. **Take over** pauses the agent (`agent_mode=HUMAN_CONTROL`), exposes manual transitions;
   **Return to agent** resumes.
8. Grafana :3001 → Explore → Tempo: one trace spans gateway → case → mock-erp.

## Phase 2: tools, policy, email

9. **Policies** page (sign in as `marcus@`): the default rule set is grouped by action type. Open
   `small_credit_memo_autonomy`, change the amount cap, click *Simulate against history* to see
   which past decisions would flip, then *Save as v2*.
10. Invoke tools the way an agent will (internal network, port 8006):
    ```bash
    T=<tenant id>; C=<case id>
    curl -s "localhost:8006/tools/manifest?tenant_id=$T&case_id=$C" | jq '.tools[].name, .hidden'
    curl -s -X POST localhost:8006/tools/reconcile_lines/invoke -H 'content-type: application/json' \
      -d "{\"tenant_id\":\"$T\",\"case_id\":\"$C\",\"actor\":\"agent:reconciler\",\"args\":{\"invoice_ref\":\"INV-100107\"}}" | jq .result.summary
    ```
11. Try to issue the credit memo directly → `403 approval_required`. Call `propose_action` →
    the card lands in **Approvals**; approve as manager; retry `create_credit_memo` with
    `approval_ref` → it executes, the ERP invoice balance drops, the action shows *executed*.
12. `send_email` with the `po_request` template to the active AP contact is ALLOWed by policy and
    lands in Mailpit (:8025). `make simulate-reply` answers as the customer; the poller links the
    reply to the case; it appears under **Email** on the case page and in the timeline.
13. Case page → **Tool calls** shows every invocation with args, result, policy decision and
    latency; the same rows are on Kafka as `tool.invoked` / `tool.blocked`.

## Phase 3: agents

14. Set `LLM_PROVIDER=google_genai` and `LLM_API_KEY` in `.env` (or keep `heuristic`), then
    `make up && make seed`. Every ingested case starts a `CaseWorkflow`; watch them in Temporal
    UI (:8233) and on the **Agents** page.
15. Open a case: the **Agents** card shows the live run (supervisor → triage → investigator →
    escalation), and the timeline fills in as steps complete over the WebSocket.
16. **Agents → Runs**: click a run for the full trace: each step's structured output, tool
    calls with args, provider/model, tokens and cost. Prompts and model versions are recorded
    per run.
17. **Agents → Prompts**: edit the triage prompt, save as v2; the next run records v2 in its
    bundle. **Agents → Models**: switch the strong tier to another provider/model per tenant.
18. Take over a case from its page: the run shows `WAITING / HUMAN_CONTROL`; return it and the
    supervisor resumes.

## Phase 4: resolution and the closed loop

19. `make demo` in a second terminal (the customer persona). New cases now run end to end:
    dispute → Reconciler proposes a credit memo → **Approvals** (approve, or *Edit* the amount
    and watch the diff) → executor issues the memo in the ERP → Communicator sends the
    dispute_resolution email → persona confirms payment → Intent extractor reads it → RESOLVED.
20. MISSING_PO cases: po_request email → persona replies with the PO → resend_invoice → RESOLVED.
    CASH_FLOW cases: Negotiator proposes a plan (auto-allowed within policy) → offer email →
    persona accepts → RESOLVED.
21. Reject a proposal with a feedback code: the run escalates with the rejection in the brief.
22. Stop `make demo`: after the wait timer (72h by default; set `CUSTOMER_WAIT_HOURS_DEFAULT`
    lower for demos) the Communicator sends reminders, then escalates after three.
