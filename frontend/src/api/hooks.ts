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
