import { useState } from "react";
import { Link } from "@tanstack/react-router";
import clsx from "clsx";
import { useCases } from "@/api/hooks";
import type { CaseStatus } from "@/api/schemas";
import { Empty, ErrorBox, PriorityDot, StatusBadge } from "@/components/ui";
import { money, relTime } from "@/lib/format";

const views: { key: string; label: string; status?: CaseStatus[] }[] = [
  { key: "open", label: "Open", status: ["NEW", "TRIAGED", "INVESTIGATING", "AWAITING_CUSTOMER", "NEGOTIATING", "PENDING_APPROVAL", "ACTION_TAKEN"] },
  { key: "approval", label: "Pending approval", status: ["PENDING_APPROVAL"] },
  { key: "escalated", label: "Escalated", status: ["ESCALATED"] },
  { key: "closed", label: "Closed", status: ["RESOLVED", "WRITTEN_OFF"] },
  { key: "all", label: "All" },
];

export function CasesPage() {
  const [view, setView] = useState("open");
  const current = views.find((v) => v.key === view) ?? views[0];
  const q = useCases({ status: current.status });
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">Work queue</h1>
        <span className="text-sm text-slate-500">{q.data ? `${q.data.total} cases` : ""}</span>
        <div className="ml-auto flex gap-1">
          {views.map((v) => (
            <button
              key={v.key}
              onClick={() => setView(v.key)}
              className={clsx("rounded px-2.5 py-1 text-sm", view === v.key ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100")}
            >
              {v.label}
            </button>
          ))}
        </div>
      </div>
      {q.error && <ErrorBox error={q.error} />}
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2">Pri</th>
              <th className="px-3 py-2">Customer</th>
              <th className="px-3 py-2">Invoices</th>
              <th className="px-3 py-2 text-right">Open</th>
              <th className="px-3 py-2 text-right">Overdue</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Root cause</th>
              <th className="px-3 py-2">Mode</th>
              <th className="px-3 py-2">Updated</th>
            </tr>
          </thead>
          <tbody>
            {q.data?.items.map((c) => (
              <tr key={c.id} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="px-3 py-2"><PriorityDot p={c.priority} /></td>
                <td className="px-3 py-2">
                  <Link to="/cases/$caseId" params={{ caseId: c.id }} className="font-medium text-slate-900 hover:underline">
                    {c.customer_name ?? c.customer_ref}
                  </Link>
                  <div className="text-xs text-slate-400">{c.customer_ref}</div>
                </td>
                <td className="px-3 py-2 font-mono text-xs">{c.invoice_refs.join(", ")}</td>
                <td className="px-3 py-2 text-right tabular-nums">{money(c.amount_open, c.currency)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{c.days_overdue}d</td>
                <td className="px-3 py-2"><StatusBadge status={c.status} /></td>
                <td className="px-3 py-2 text-xs">{c.root_cause ?? <span className="text-slate-400">—</span>}</td>
                <td className="px-3 py-2 text-xs">{c.agent_mode === "HUMAN_CONTROL" ? "👤 human" : "🤖 agent"}</td>
                <td className="px-3 py-2 text-xs text-slate-500">{relTime(c.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {q.isLoading && <Empty>Loading…</Empty>}
        {q.data && q.data.items.length === 0 && <Empty>No cases in this view. Run `make ingest` to pull overdue invoices.</Empty>}
      </div>
    </div>
  );
}
