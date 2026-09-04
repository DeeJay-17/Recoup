import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { CasePage, CaseWorkspace, Me, ProposedAction, type CaseStatus } from "./schemas";
import { z } from "zod";
import { useAuth } from "@/store/auth";

export const keys = {
  me: ["me"] as const,
  cases: (f: CaseFilter) => ["cases", f] as const,
  workspace: (id: string) => ["case", id] as const,
  approvals: ["approvals"] as const,
};

export interface CaseFilter {
  status?: CaseStatus[];
  min_days_overdue?: number;
  limit?: number;
  offset?: number;
}

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, String(x)));
    else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function useLogin() {
  const setToken = useAuth((s) => s.setToken);
  const setMe = useAuth((s) => s.setMe);
  return useMutation({
    mutationFn: async (body: { email: string; password: string; tenant_slug?: string }) => {
      const t = await api<{ access_token: string }>("/iam/auth/login", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setToken(t.access_token);
      const me = Me.parse(await api("/bff/me"));
      setMe(me);
      return me;
    },
  });
}

export function useCases(filter: CaseFilter) {
  return useQuery({
    queryKey: keys.cases(filter),
    queryFn: async () => CasePage.parse(await api(`/cases/cases${qs({ limit: 100, ...filter })}`)),
    refetchInterval: 15_000,
  });
}

export function useCaseWorkspace(id: string) {
  return useQuery({
    queryKey: keys.workspace(id),
    queryFn: async () => CaseWorkspace.parse(await api(`/bff/case/${id}`)),
    refetchInterval: 10_000,
  });
}

export function useApprovals() {
  return useQuery({
    queryKey: keys.approvals,
    queryFn: async () => z.array(ProposedAction).parse(await api("/cases/approvals")),
    refetchInterval: 15_000,
  });
}

type Decision = "approve" | "reject" | "edit";
export function useDecideAction(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      actionId: string;
      decision: Decision;
      feedback_code?: string;
      feedback_note?: string;
      human_final?: Record<string, unknown>;
      expected_version?: number;
    }) =>
      api<unknown>(`/cases/cases/${caseId}/actions/${args.actionId}/${args.decision}`, {
        method: "POST",
        body: JSON.stringify({
          feedback_code: args.feedback_code,
          feedback_note: args.feedback_note,
          human_final: args.human_final,
          expected_version: args.expected_version,
        }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.workspace(caseId) });
      void qc.invalidateQueries({ queryKey: keys.approvals });
      void qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });
}

export function useCaseMutation(caseId: string) {
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: keys.workspace(caseId) });
    void qc.invalidateQueries({ queryKey: ["cases"] });
  };
  const post = (path: string, body?: unknown) =>
    api<unknown>(`/cases/cases/${caseId}${path}`, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  return {
    takeover: useMutation({ mutationFn: (reason?: string) => post("/takeover", { reason }), onSuccess: invalidate }),
    release: useMutation({ mutationFn: () => post("/release"), onSuccess: invalidate }),
    note: useMutation({ mutationFn: (text: string) => post("/notes", { text }), onSuccess: invalidate }),
    transition: useMutation({
      mutationFn: (args: { to: CaseStatus; reason?: string; expected_version?: number }) =>
        post("/transition", args),
      onSuccess: invalidate,
    }),
  };
}

// ---------- phase 2: policies, email, tool audit ----------
import { EmailThread, EvaluateResponse, Policy, SimulateResponse, ToolInvocation } from "./schemas";

export function usePolicies() {
  return useQuery({
    queryKey: ["policies"],
    queryFn: async () => z.array(Policy).parse(await api("/policy/policies")),
  });
}

export function usePolicyMutations() {
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["policies"] });
  return {
    toggle: useMutation({
      mutationFn: (args: { id: string; enabled: boolean }) =>
        api(`/policy/policies/${args.id}`, { method: "PATCH", body: JSON.stringify({ enabled: args.enabled }) }),
      onSuccess: invalidate,
    }),
    newVersion: useMutation({
      mutationFn: (args: { id: string; rule: unknown; decision: string; required_role: string | null; reason: string | null }) =>
        api(`/policy/policies/${args.id}/versions`, {
          method: "POST",
          body: JSON.stringify({ rule: args.rule, decision: args.decision, required_role: args.required_role, reason: args.reason }),
        }),
      onSuccess: invalidate,
    }),
    simulate: useMutation({
      mutationFn: async (args: { id: string; rule: unknown; decision: string; required_role: string | null }) =>
        SimulateResponse.parse(
          await api(`/policy/policies/${args.id}/simulate`, {
            method: "POST",
            body: JSON.stringify({ rule: args.rule, decision: args.decision, required_role: args.required_role, use_history: true }),
          }),
        ),
    }),
    evaluate: useMutation({
      mutationFn: async (args: { action_type: string; context: unknown }) =>
        EvaluateResponse.parse(
          await api(`/policy/policies/evaluate`, {
            method: "POST",
            body: JSON.stringify({ tenant_id: "00000000-0000-0000-0000-000000000000", action_type: args.action_type, context: args.context }),
          }),
        ),
    }),
    installDefaults: useMutation({
      mutationFn: () => api(`/policy/policies/install-defaults`, { method: "POST", body: "{}" }),
      onSuccess: invalidate,
    }),
  };
}

export function useCaseThreads(caseId: string) {
  return useQuery({
    queryKey: ["threads", caseId],
    queryFn: async () => z.array(EmailThread).parse(await api(`/comm/cases/${caseId}/threads`)),
    refetchInterval: 10_000,
  });
}

export function useCaseToolCalls(caseId: string) {
  return useQuery({
    queryKey: ["toolcalls", caseId],
    queryFn: async () => z.array(ToolInvocation).parse(await api(`/tools/invocations?case_id=${caseId}&limit=50`)),
    refetchInterval: 10_000,
  });
}

export function useSendEmail(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { to: string; subject: string; body_text: string; in_reply_to?: string | null }) =>
      api(`/comm/emails/send`, {
        method: "POST",
        body: JSON.stringify({
          case_id: caseId,
          to: [args.to],
          subject: args.subject,
          body_text: args.body_text,
          in_reply_to: args.in_reply_to ?? null,
          idempotency_key: crypto.randomUUID(),
        }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["threads", caseId] });
      void qc.invalidateQueries({ queryKey: keys.workspace(caseId) });
    },
  });
}

// ---------- phase 3: agents ----------
import { AgentRun, ModelConfig, Prompt, RunDetail } from "./schemas";

export function useRuns(filter: { case_id?: string; status?: string } = {}) {
  return useQuery({
    queryKey: ["runs", filter],
    queryFn: async () => z.array(AgentRun).parse(await api(`/agents/runs${qs({ limit: 100, ...filter })}`)),
    refetchInterval: 10_000,
  });
}

export function useRun(runId: string | null, withMessages = false) {
  return useQuery({
    queryKey: ["run", runId, withMessages],
    queryFn: async () => RunDetail.parse(await api(`/agents/runs/${runId}${qs({ with_messages: withMessages || undefined })}`)),
    enabled: !!runId,
    refetchInterval: 5_000,
  });
}

export function useCaseRun(caseId: string) {
  return useQuery({
    queryKey: ["caserun", caseId],
    queryFn: async () => {
      const r = await api<unknown>(`/agents/cases/${caseId}/run`);
      return r === null ? null : RunDetail.parse(r);
    },
    refetchInterval: 5_000,
  });
}

export function useStartRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (caseId: string) => api<unknown>(`/agents/runs`, { method: "POST", body: JSON.stringify({ case_id: caseId }) }),
    onSuccess: (_d, caseId) => {
      void qc.invalidateQueries({ queryKey: ["caserun", caseId] });
      void qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

export function useCancelRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => api<unknown>(`/agents/runs/${runId}/cancel`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["runs"] }),
  });
}

export function usePrompts() {
  return useQuery({ queryKey: ["prompts"], queryFn: async () => z.array(Prompt).parse(await api("/agents/prompts")) });
}

export function usePromptMutations() {
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["prompts"] });
  return {
    addVersion: useMutation({
      mutationFn: (a: { name: string; content: string; notes?: string; activate: boolean }) =>
        api(`/agents/prompts/${a.name}/versions`, { method: "POST", body: JSON.stringify(a) }),
      onSuccess: invalidate,
    }),
    activate: useMutation({
      mutationFn: (a: { name: string; version: number }) => api(`/agents/prompts/${a.name}/activate/${a.version}`, { method: "POST" }),
      onSuccess: invalidate,
    }),
  };
}

export function useModelConfig() {
  return useQuery({ queryKey: ["modelconfig"], queryFn: async () => ModelConfig.parse(await api("/agents/model-config")) });
}

export function useSaveModelConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (overrides: Record<string, Record<string, string | null>>) =>
      api(`/agents/model-config`, { method: "PUT", body: JSON.stringify({ overrides }) }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["modelconfig"] }),
  });
}

// ---------- phase 5: customer 360 ----------
import { Customer, KnowledgeDoc, MemoryFact, SearchHit } from "./schemas";

export function useCustomer(ref: string) {
  return useQuery({ queryKey: ["customer", ref], queryFn: async () => Customer.parse(await api(`/erp/customers/${ref}`)) });
}

export function useCustomerCases(ref: string) {
  return useQuery({
    queryKey: ["cases", { customer_ref: ref }],
    queryFn: async () => CasePage.parse(await api(`/cases/cases${qs({ customer_ref: ref, limit: 100 })}`)),
  });
}

export function useCustomerMemory(ref: string) {
  return useQuery({ queryKey: ["memory", ref], queryFn: async () => z.array(MemoryFact).parse(await api(`/knowledge/customers/${ref}/memory`)) });
}

export function useMemoryMutations(ref: string) {
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["memory", ref] });
  return {
    add: useMutation({
      mutationFn: (body: { fact: string; category: string; confidence: number }) =>
        api(`/knowledge/customers/${ref}/memory`, { method: "POST", body: JSON.stringify(body) }),
      onSuccess: invalidate,
    }),
    retire: useMutation({ mutationFn: (id: string) => api(`/knowledge/customers/${ref}/memory/${id}`, { method: "DELETE" }), onSuccess: invalidate }),
  };
}

export function useCustomerDocs(ref: string) {
  return useQuery({ queryKey: ["docs", ref], queryFn: async () => z.array(KnowledgeDoc).parse(await api(`/knowledge/documents${qs({ customer_ref: ref, limit: 50 })}`)) });
}

export function useKnowledgeSearch() {
  return useMutation({
    mutationFn: async (body: { query: string; customer_ref?: string; kinds?: string[]; k?: number }) =>
      z.array(SearchHit).parse(await api(`/knowledge/search`, { method: "POST", body: JSON.stringify({ k: 6, ...body }) })),
  });
}

// ---------- phase 6: evals ----------
import { EvalDataset, EvalRun, EvalRunDetail } from "./schemas";

export function useEvalDatasets() {
  return useQuery({ queryKey: ["evaldatasets"], queryFn: async () => z.array(EvalDataset).parse(await api("/evals/datasets")) });
}

export function useEvalRuns() {
  return useQuery({
    queryKey: ["evalruns"],
    queryFn: async () => z.array(EvalRun).parse(await api("/evals/runs")),
    refetchInterval: 10_000,
  });
}

export function useEvalRun(runId: string | null) {
  return useQuery({
    queryKey: ["evalrun", runId],
    queryFn: async () => EvalRunDetail.parse(await api(`/evals/runs/${runId}`)),
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data?.run.status === "RUNNING" ? 5_000 : false),
  });
}

export function useEvalMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["evalruns"] });
    void qc.invalidateQueries({ queryKey: ["evaldatasets"] });
  };
  return {
    build: useMutation({
      mutationFn: (body: { name: string; size: number; kind: string }) =>
        api(`/evals/datasets/build`, { method: "POST", body: JSON.stringify(body) }),
      onSuccess: invalidate,
    }),
    start: useMutation({
      mutationFn: (body: { dataset_id: string; label: string; judge: boolean; limit?: number }) =>
        api(`/evals/runs`, { method: "POST", body: JSON.stringify(body) }),
      onSuccess: invalidate,
    }),
    cancel: useMutation({ mutationFn: (runId: string) => api(`/evals/runs/${runId}/cancel`, { method: "POST" }), onSuccess: invalidate }),
  };
}

// ---------- phase 7: dashboard ----------
import { Dashboard } from "./schemas";

export function useDashboard(days: number) {
  return useQuery({
    queryKey: ["dashboard", days],
    queryFn: async () => Dashboard.parse(await api(`/analytics/metrics/dashboard?days=${days}`)),
    refetchInterval: 30_000,
  });
}
