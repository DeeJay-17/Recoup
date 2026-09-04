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

// ---------- phase 2 ----------
export const Decision = z.enum(["ALLOW", "REQUIRE_APPROVAL", "DENY"]);
export type Decision = z.infer<typeof Decision>;

export const PolicyVersion = z.object({
  id: z.string(),
  version: z.number(),
  rule: z.record(z.unknown()),
  decision: Decision,
  required_role: z.string().nullable(),
  reason: z.string().nullable(),
  created_by: z.string(),
  created_at: z.string(),
});

export const Policy = z.object({
  id: z.string(),
  tenant_id: z.string(),
  name: z.string(),
  description: z.string(),
  action_type: z.string(),
  priority: z.number(),
  enabled: z.boolean(),
  current_version: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
  current: PolicyVersion.nullable(),
});
export type Policy = z.infer<typeof Policy>;

export const SimulateResponse = z.object({
  rows: z.array(
    z.object({
      context: z.record(z.unknown()),
      current_decision: Decision,
      candidate_decision: Decision,
      changed: z.boolean(),
    }),
  ),
  changed: z.number(),
  total: z.number(),
});
export type SimulateResponse = z.infer<typeof SimulateResponse>;

export const EvaluateResponse = z.object({
  decision: Decision,
  required_role: z.string().nullable(),
  reason: z.string().nullable(),
  matched: z.array(z.record(z.unknown())),
  defaulted: z.boolean(),
});

export const EmailMessage = z.object({
  id: z.string(),
  thread_id: z.string(),
  case_id: z.string().nullable(),
  direction: z.enum(["IN", "OUT"]),
  status: z.string(),
  message_id: z.string(),
  from_addr: z.string(),
  to_addrs: z.array(z.string()),
  cc_addrs: z.array(z.string()),
  subject: z.string(),
  body_text: z.string(),
  template: z.string().nullable(),
  attachments: z.array(z.record(z.unknown())),
  invoice_refs: z.array(z.string()),
  link_method: z.string().nullable(),
  approval_ref: z.string().nullable(),
  sent_by: z.string().nullable(),
  error: z.string().nullable(),
  sent_at: z.string().nullable(),
  received_at: z.string().nullable(),
  created_at: z.string(),
});
export type EmailMessage = z.infer<typeof EmailMessage>;

export const EmailThread = z.object({
  id: z.string(),
  case_id: z.string().nullable(),
  subject: z.string(),
  invoice_refs: z.array(z.string()),
  last_message_at: z.string().nullable(),
  messages: z.array(EmailMessage),
});
export type EmailThread = z.infer<typeof EmailThread>;

export const ToolInvocation = z.object({
  id: z.number(),
  case_id: z.string().nullable(),
  tool: z.string(),
  actor: z.string(),
  args: z.record(z.unknown()),
  result: z.record(z.unknown()).nullable(),
  status: z.string(),
  policy_decision: z.string().nullable(),
  approval_ref: z.string().nullable(),
  error: z.string().nullable(),
  latency_ms: z.number().nullable(),
  invoked_at: z.string(),
});
export type ToolInvocation = z.infer<typeof ToolInvocation>;

// ---------- phase 3: agent runs ----------
export const AgentStep = z.object({
  id: z.number(),
  step_no: z.number(),
  agent_name: z.string(),
  kind: z.string(),
  status: z.string(),
  input_summary: z.record(z.unknown()).nullable(),
  output: z.record(z.unknown()).nullable(),
  tool_calls: z.array(z.record(z.unknown())),
  provider: z.string().nullable(),
  model: z.string().nullable(),
  tokens_in: z.number(),
  tokens_out: z.number(),
  cost_usd: z.number(),
  latency_ms: z.number().nullable(),
  error: z.string().nullable(),
  trace_id: z.string().nullable(),
  started_at: z.string(),
  ended_at: z.string().nullable(),
  messages: z.array(z.record(z.unknown())).nullable().optional(),
});
export type AgentStep = z.infer<typeof AgentStep>;

export const AgentRun = z.object({
  id: z.string(),
  case_id: z.string(),
  workflow_id: z.string(),
  mode: z.string(),
  status: z.string(),
  phase: z.string().nullable(),
  prompt_bundle: z.record(z.unknown()),
  models: z.record(z.unknown()),
  steps: z.number(),
  tokens_in: z.number(),
  tokens_out: z.number(),
  cost_usd: z.number(),
  outcome: z.record(z.unknown()).nullable(),
  started_at: z.string(),
  updated_at: z.string(),
  ended_at: z.string().nullable(),
});
export type AgentRun = z.infer<typeof AgentRun>;

export const RunDetail = z.object({
  run: AgentRun,
  steps: z.array(AgentStep),
  workflow: z.record(z.unknown()).nullable().optional(),
});
export type RunDetail = z.infer<typeof RunDetail>;

export const Prompt = z.object({
  id: z.string(),
  name: z.string(),
  version: z.number(),
  content: z.string(),
  notes: z.string().nullable(),
  created_by: z.string(),
  created_at: z.string(),
  is_active: z.boolean(),
});
export type Prompt = z.infer<typeof Prompt>;

export const ModelConfig = z.object({
  defaults: z.record(z.object({ provider: z.string(), model: z.string() })),
  overrides: z.record(z.record(z.string().nullable())),
  effective: z.record(z.object({ provider: z.string(), model: z.string() })),
});
export type ModelConfig = z.infer<typeof ModelConfig>;

// ---------- phase 5: knowledge & memory ----------
export const MemoryFact = z.object({
  id: z.string(),
  customer_ref: z.string(),
  fact: z.string(),
  category: z.enum(["CONTACT", "PREFERENCE", "PATTERN", "RISK"]),
  confidence: z.number(),
  source_case_id: z.string().nullable(),
  source_ref: z.string().nullable(),
  created_by: z.string(),
  created_at: z.string(),
});
export type MemoryFact = z.infer<typeof MemoryFact>;

export const KnowledgeDoc = z.object({
  id: z.string(),
  kind: z.string(),
  title: z.string(),
  customer_ref: z.string().nullable(),
  source_ref: z.string().nullable(),
  chunk_count: z.number(),
  metadata: z.record(z.unknown()),
  created_at: z.string(),
  preview: z.string(),
});
export type KnowledgeDoc = z.infer<typeof KnowledgeDoc>;

export const SearchHit = z.object({
  chunk_id: z.number(),
  document_id: z.string(),
  kind: z.string(),
  title: z.string(),
  customer_ref: z.string().nullable(),
  chunk_no: z.number(),
  content: z.string(),
  score: z.number(),
  sources: z.array(z.string()),
  metadata: z.record(z.unknown()),
});
export type SearchHit = z.infer<typeof SearchHit>;

// ---------- phase 6: evals ----------
export const EvalDataset = z.object({
  id: z.string(),
  name: z.string(),
  kind: z.string(),
  description: z.string(),
  case_count: z.number(),
  spec: z.record(z.unknown()),
  created_at: z.string(),
});
export type EvalDataset = z.infer<typeof EvalDataset>;

export const EvalRun = z.object({
  id: z.string(),
  dataset_id: z.string(),
  label: z.string(),
  status: z.string(),
  judge: z.boolean(),
  concurrency: z.number(),
  prompt_bundle: z.record(z.unknown()),
  models: z.record(z.unknown()),
  metrics: z.record(z.unknown()),
  cases_total: z.number(),
  cases_done: z.number(),
  error: z.string().nullable(),
  started_at: z.string(),
  ended_at: z.string().nullable(),
});
export type EvalRun = z.infer<typeof EvalRun>;

export const EvalResult = z.object({
  id: z.number(),
  eval_case_id: z.string(),
  agent_run_id: z.string().nullable(),
  status: z.string(),
  passed: z.boolean(),
  expected_root_cause: z.string().nullable(),
  triage_root_cause: z.string().nullable(),
  predicted_root_cause: z.string().nullable(),
  expected_credit_memo: z.number().nullable(),
  predicted_credit_memo: z.number().nullable(),
  credit_delta: z.number().nullable(),
  terminal_status: z.string().nullable(),
  steps: z.number(),
  tool_calls: z.number(),
  policy_denials: z.number(),
  approval_gates: z.number(),
  unauthorized_mutations: z.number(),
  tokens: z.number(),
  cost_usd: z.number(),
  latency_ms: z.number().nullable(),
  scores: z.record(z.unknown()),
  failures: z.array(z.unknown()),
  detail: z.record(z.unknown()),
});
export type EvalResult = z.infer<typeof EvalResult>;

export const EvalRunDetail = z.object({
  run: EvalRun,
  dataset: EvalDataset,
  results: z.array(EvalResult),
});
export type EvalRunDetail = z.infer<typeof EvalRunDetail>;

// ---------- phase 7: analytics ----------
export const Dashboard = z.object({
  window_days: z.number(),
  summary: z.object({
    window_days: z.number(),
    open_cases: z.number(),
    open_amount: z.number(),
    dso_proxy_days: z.number(),
    closed_cases: z.number(),
    median_hours_to_close: z.number(),
    escalation_rate: z.number(),
    untouched_rate: z.number(),
    actions_proposed: z.number(),
    autonomy_rate: z.number(),
    approval_without_edit_rate: z.number(),
    agent_runs: z.number(),
    cost_usd: z.number(),
    tokens: z.number(),
    cost_per_closed_case: z.number(),
    avg_steps: z.number(),
  }),
  aging: z.object({
    buckets: z.array(z.string()),
    root_causes: z.array(z.string()),
    cells: z.array(z.object({ root_cause: z.string(), bucket: z.string(), cases: z.number(), amount: z.number() })),
    totals: z.array(z.object({ bucket: z.string(), cases: z.number(), amount: z.number() })),
  }),
  trend: z.array(z.object({ day: z.string(), opened: z.number(), closed: z.number(), escalated: z.number(), cost: z.number() })),
  funnel: z.array(z.object({ stage: z.string(), cases: z.number(), share: z.number() })),
  agents: z.object({
    by_agent: z.array(z.object({ agent: z.string(), steps: z.number(), errors: z.number(), avg_latency_ms: z.number(), p95_latency_ms: z.number(), tokens: z.number(), error_rate: z.number() })),
    by_root_cause: z.array(z.object({ root_cause: z.string(), cases: z.number(), resolved: z.number(), escalated: z.number(), avg_cost: z.number(), avg_steps: z.number(), resolution_rate: z.number() })),
    tools: z.array(z.object({ tool: z.string(), calls: z.number(), ok: z.number(), gated: z.number(), avg_latency_ms: z.number() })),
  }),
  escalations: z.array(z.object({ reason: z.string(), cases: z.number(), avg_steps: z.number() })),
});
export type Dashboard = z.infer<typeof Dashboard>;
