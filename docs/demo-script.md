# Demo script (phase 1)

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
