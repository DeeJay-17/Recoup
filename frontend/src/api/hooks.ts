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
