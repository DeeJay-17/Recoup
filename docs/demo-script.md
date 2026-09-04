# Demo script (phases 1–2)

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
