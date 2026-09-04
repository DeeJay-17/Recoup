import { z } from "zod";

export const CaseStatus = z.enum([
  "NEW",
  "TRIAGED",
  "INVESTIGATING",
  "AWAITING_CUSTOMER",
  "NEGOTIATING",
  "PENDING_APPROVAL",
  "ACTION_TAKEN",
  "RESOLVED",
  "ESCALATED",
  "WRITTEN_OFF",
]);
export type CaseStatus = z.infer<typeof CaseStatus>;

export const Case = z.object({
  id: z.string().uuid(),
  tenant_id: z.string().uuid(),
  customer_ref: z.string(),
  customer_name: z.string().nullable(),
  invoice_refs: z.array(z.string()),
  status: CaseStatus,
  root_cause: z.string().nullable(),
  root_cause_conf: z.union([z.string(), z.number()]).nullable(),
  priority: z.number(),
  amount_open: z.union([z.string(), z.number()]),
  currency: z.string(),
  days_overdue: z.number(),
  assignee_id: z.string().nullable(),
  agent_mode: z.enum(["AUTONOMOUS", "HUMAN_CONTROL"]),
  workflow_id: z.string().nullable(),
  opened_at: z.string(),
  updated_at: z.string(),
  resolved_at: z.string().nullable(),
  resolution: z.record(z.unknown()).nullable(),
  version: z.number(),
});
export type Case = z.infer<typeof Case>;

export const CasePage = z.object({
  items: z.array(Case),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
});

export const TimelineEvent = z.object({
  id: z.number(),
  case_id: z.string(),
  kind: z.string(),
  actor_type: z.enum(["agent", "human", "system"]),
  actor_id: z.string(),
  title: z.string(),
  payload: z.record(z.unknown()),
  trace_id: z.string().nullable(),
  occurred_at: z.string(),
});
export type TimelineEvent = z.infer<typeof TimelineEvent>;

export const ActionStatus = z.enum([
  "PENDING",
  "APPROVED",
  "EDITED",
  "REJECTED",
  "EXECUTED",
  "AUTO_EXECUTED",
  "DENIED",
]);

export const ProposedAction = z.object({
  id: z.string(),
  case_id: z.string(),
  action_type: z.string(),
  payload: z.record(z.unknown()),
  rationale: z.string(),
  evidence_refs: z.array(z.unknown()),
  policy_decision: z.enum(["ALLOW", "REQUIRE_APPROVAL", "DENY"]),
  policy_rule: z.string().nullable(),
  required_role: z.string().nullable(),
  status: ActionStatus,
  proposed_by: z.string(),
  run_id: z.string().nullable(),
  human_final: z.record(z.unknown()).nullable(),
  decided_by: z.string().nullable(),
  decided_at: z.string().nullable(),
  feedback_code: z.string().nullable(),
  feedback_note: z.string().nullable(),
  executed_at: z.string().nullable(),
  execution_result: z.record(z.unknown()).nullable(),
  created_at: z.string(),
});
export type ProposedAction = z.infer<typeof ProposedAction>;

export const InvoiceLine = z.object({
  line_no: z.number(),
  sku: z.string(),
  description: z.string(),
  qty: z.union([z.string(), z.number()]),
  unit_price: z.union([z.string(), z.number()]),
  amount: z.union([z.string(), z.number()]),
});

export const Invoice = z.object({
  invoice_ref: z.string(),
  customer_ref: z.string(),
  po_number: z.string().nullable(),
  status: z.string(),
  issue_date: z.string(),
  due_date: z.string(),
  currency: z.string(),
  subtotal: z.union([z.string(), z.number()]),
  freight: z.union([z.string(), z.number()]),
  tax: z.union([z.string(), z.number()]),
  total: z.union([z.string(), z.number()]),
  amount_paid: z.union([z.string(), z.number()]),
  amount_open: z.union([z.string(), z.number()]),
  billed_to_email: z.string().nullable(),
  lines: z.array(InvoiceLine),
});
export type Invoice = z.infer<typeof Invoice>;

export const Customer = z.object({
  customer_ref: z.string(),
  name: z.string(),
  payment_terms_days: z.number(),
  requires_po: z.boolean(),
  credit_hold: z.boolean(),
  credit_risk_score: z.number(),
  contacts: z.array(
    z.object({ name: z.string(), email: z.string(), role: z.string(), active: z.boolean() }),
  ),
  billing_address: z.string().nullable(),
});
export type Customer = z.infer<typeof Customer>;

export const CaseWorkspace = z.object({
  case: Case,
  timeline: z.array(TimelineEvent),
  actions: z.array(ProposedAction),
  erp: z.object({ invoices: z.array(Invoice), customer: Customer.nullable() }),
});
export type CaseWorkspace = z.infer<typeof CaseWorkspace>;

export const Me = z.object({
  id: z.string(),
  tenant_id: z.string(),
  tenant_slug: z.string(),
  email: z.string(),
  full_name: z.string(),
  roles: z.array(z.string()),
});
