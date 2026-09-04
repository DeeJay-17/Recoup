import { useState } from "react";
import clsx from "clsx";
import { useCaseToolCalls } from "@/api/hooks";
import { Empty, ErrorBox } from "./ui";
import { relTime } from "@/lib/format";

const tone: Record<string, string> = {
  SUCCESS: "bg-emerald-100 text-emerald-800",
  DENIED: "bg-rose-100 text-rose-800",
  APPROVAL_REQUIRED: "bg-orange-100 text-orange-800",
  ERROR: "bg-rose-100 text-rose-800",
  REJECTED_ARGS: "bg-zinc-100 text-zinc-700",
  RATE_LIMITED: "bg-zinc-100 text-zinc-700",
};

export function ToolCallsPanel({ caseId }: { caseId: string }) {
  const q = useCaseToolCalls(caseId);
  const [open, setOpen] = useState<number | null>(null);
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data || q.data.length === 0) return <Empty>No tool calls recorded on this case.</Empty>;
  return (
    <ul className="divide-y divide-slate-100 text-sm">
      {q.data.map((t) => (
        <li key={t.id} className="py-2">
          <button className="flex w-full items-center gap-2 text-left" onClick={() => setOpen(open === t.id ? null : t.id)}>
            <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{t.tool}</code>
            <span className={clsx("rounded px-1.5 py-0.5 text-xs", tone[t.status] ?? "bg-slate-100")}>{t.status}</span>
            {t.policy_decision && <span className="text-xs text-slate-500">policy {t.policy_decision}</span>}
            <span className="ml-auto text-xs text-slate-400">{t.actor} · {t.latency_ms ?? "?"} ms · {relTime(t.invoked_at)}</span>
          </button>
          {open === t.id && (
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              <pre className="max-h-48 overflow-auto rounded bg-slate-50 p-2 text-xs">{JSON.stringify(t.args, null, 2)}</pre>
              <pre className="max-h-48 overflow-auto rounded bg-slate-50 p-2 text-xs">{t.error ?? JSON.stringify(t.result, null, 2)}</pre>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
