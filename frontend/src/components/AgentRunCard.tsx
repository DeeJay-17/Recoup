import { useState } from "react";
import clsx from "clsx";
import { Link } from "@tanstack/react-router";
import type { AgentStep } from "@/api/schemas";
import { useCancelRun, useCaseRun, useStartRun } from "@/api/hooks";
import { Button, Empty, ErrorBox } from "./ui";
import { relTime } from "@/lib/format";
import { runTone } from "@/lib/runTone";

export function AgentRunCard({ caseId, closed }: { caseId: string; closed: boolean }) {
  const q = useCaseRun(caseId);
  const start = useStartRun();
  const cancel = useCancelRun();
  if (q.isLoading) return <Empty>Loading…</Empty>;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  const active = d && (d.run.status === "RUNNING" || d.run.status === "WAITING");
  return (
    <div className="space-y-3 text-sm">
      {!d && <p className="text-slate-500">No agent run on this case yet.</p>}
      {d && (
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={clsx("rounded px-1.5 py-0.5 text-xs font-medium", runTone[d.run.status])}>{d.run.status}</span>
            <span className="text-slate-600">{d.run.phase ?? ""}</span>
            <span className="ml-auto text-xs text-slate-500">{d.run.steps} steps · {d.run.tokens_in + d.run.tokens_out} tokens · ${d.run.cost_usd.toFixed(4)}</span>
          </div>
          <div className="mt-1 text-xs text-slate-500">
            {Object.entries(d.run.models).map(([tier, m]) => { const mm = m as { provider: string; model: string }; return `${tier}: ${mm.provider}/${mm.model}`; }).join(" · ")} · started {relTime(d.run.started_at)}
          </div>
          <StepList steps={d.steps} compact />
          {d.run.outcome && (
            <p className="mt-2 rounded bg-slate-50 p-2 text-xs text-slate-700">Outcome: {String(d.run.outcome.status)} — {String(d.run.outcome.reason)}</p>
          )}
          <Link to="/agents" search={{ run: d.run.id }} className="mt-2 inline-block text-xs text-slate-500 hover:underline">open full trace →</Link>
        </div>
      )}
      {!closed && !active && (
        <Button variant="secondary" onClick={() => start.mutate(caseId)} disabled={start.isPending}>{d ? "Run agents again" : "Start agents"}</Button>
      )}
      {active && (
        <Button variant="ghost" onClick={() => cancel.mutate(d.run.id)} disabled={cancel.isPending}>Cancel run</Button>
      )}
      {(start.error || cancel.error) && <ErrorBox error={start.error ?? cancel.error} />}
    </div>
  );
}

export function StepList({ steps, compact = false }: { steps: AgentStep[]; compact?: boolean }) {
  const [open, setOpen] = useState<number | null>(null);
  if (steps.length === 0) return <p className="mt-2 text-xs text-slate-400">No steps yet.</p>;
  return (
    <ol className="mt-2 space-y-1">
      {steps.map((s) => (
        <li key={s.id} className="rounded border border-slate-100 bg-white">
          <button className="flex w-full items-center gap-2 px-2 py-1 text-left" onClick={() => setOpen(open === s.id ? null : s.id)}>
            <span className="w-6 text-xs text-slate-400">{s.step_no}</span>
            <span className={clsx("rounded px-1.5 py-0.5 text-xs", s.kind === "supervisor" ? "bg-slate-800 text-white" : "bg-indigo-100 text-indigo-800")}>{s.agent_name}</span>
            <span className={clsx("text-xs", s.status === "ERROR" ? "text-rose-600" : "text-slate-600")}>{s.status}</span>
            <span className="truncate text-xs text-slate-500">{stepTitle(s)}</span>
            <span className="ml-auto whitespace-nowrap text-xs text-slate-400">{s.tool_calls.length} tools · {s.tokens_in + s.tokens_out} tok · {s.latency_ms ?? "?"} ms</span>
          </button>
          {open === s.id && (
            <div className={clsx("grid gap-2 border-t border-slate-100 p-2", compact ? "" : "md:grid-cols-2")}>
              <div>
                <h5 className="text-xs font-semibold uppercase text-slate-500">Output</h5>
                <pre className="max-h-64 overflow-auto rounded bg-slate-50 p-2 text-xs">{s.error ?? JSON.stringify(s.output, null, 2)}</pre>
              </div>
              <div>
                <h5 className="text-xs font-semibold uppercase text-slate-500">Tool calls ({s.provider ?? "?"}/{s.model ?? "?"})</h5>
                <ul className="max-h-64 overflow-auto text-xs">
                  {s.tool_calls.map((t, i) => (
                    <li key={i} className="border-b border-slate-100 py-1">
                      <code>{String(t.tool)}</code> <span className={String(t.status) === "SUCCESS" || String(t.status) === "ACCEPTED" ? "text-emerald-700" : "text-rose-700"}>{String(t.status)}</span>
                      {t.args ? <span className="text-slate-400"> {JSON.stringify(t.args).slice(0, 120)}</span> : null}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

function stepTitle(s: AgentStep): string {
  const o = s.output ?? {};
  if (s.kind === "supervisor") return `${String(o.effective_next ?? o.next ?? "")}: ${String(o.reasoning_summary ?? "")}`;
  if (s.agent_name === "triage") { const h = (o.root_cause_hypotheses as { cause: string; confidence: number }[] | undefined)?.[0]; return h ? `${h.cause} (${Math.round(h.confidence * 100)}%)` : ""; }
  if (s.agent_name === "investigator") return `${String(o.confirmed_cause ?? "")} (${Math.round(Number(o.confidence ?? 0) * 100)}%)${o.proposed_credit_memo ? ` credit ${String(o.proposed_credit_memo)}` : ""}`;
  return "";
}
