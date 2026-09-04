import { useState } from "react";
import { useParams } from "@tanstack/react-router";
import { useCaseWorkspace, useCaseMutation } from "@/api/hooks";
import { ActionCard } from "@/components/ActionCard";
import { Timeline } from "@/components/Timeline";
import { EmailThreadPanel } from "@/components/EmailThread";
import { ToolCallsPanel } from "@/components/ToolCalls";
import { AgentRunCard } from "@/components/AgentRunCard";
import { Button, Card, Empty, ErrorBox, PriorityDot, StatusBadge } from "@/components/ui";
import { money, shortId } from "@/lib/format";
import type { CaseStatus } from "@/api/schemas";

export function CaseDetailPage() {
  const { caseId } = useParams({ from: "/app/cases/$caseId" });
  const q = useCaseWorkspace(caseId);
  const m = useCaseMutation(caseId);
  const [note, setNote] = useState("");

  if (q.isLoading) return <Empty>Loading case…</Empty>;
  if (q.error || !q.data) return <ErrorBox error={q.error ?? "not found"} />;
  const { case: c, timeline, actions, erp } = q.data;
  const closed = c.status === "RESOLVED" || c.status === "WRITTEN_OFF";
  const pending = actions.filter((a) => a.status === "PENDING");
  const manualTargets: CaseStatus[] = ["INVESTIGATING", "AWAITING_CUSTOMER", "NEGOTIATING", "ESCALATED", "RESOLVED"];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold">{c.customer_name ?? c.customer_ref}</h1>
            <StatusBadge status={c.status} />
            <PriorityDot p={c.priority} />
          </div>
          <p className="text-sm text-slate-500">
            case {shortId(c.id)} · {c.customer_ref} · {c.invoice_refs.join(", ")} · v{c.version}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-2xl font-semibold tabular-nums">{money(c.amount_open, c.currency)}</span>
          <span className="text-sm text-slate-500">{c.days_overdue}d overdue</span>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">
        <span className="text-slate-600">
          Mode: <strong>{c.agent_mode === "HUMAN_CONTROL" ? "Human control (agent paused)" : "Autonomous"}</strong>
        </span>
        {c.root_cause && (
          <span className="text-slate-600">
            · Root cause: <strong>{c.root_cause}</strong>{c.root_cause_conf ? ` (${Math.round(Number(c.root_cause_conf) * 100)}%)` : ""}
          </span>
        )}
        <div className="ml-auto flex gap-2">
          {!closed && c.agent_mode === "AUTONOMOUS" && (
            <Button variant="secondary" onClick={() => m.takeover.mutate(undefined)} disabled={m.takeover.isPending}>Take over</Button>
          )}
          {!closed && c.agent_mode === "HUMAN_CONTROL" && (
            <>
              <select
                className="rounded border border-slate-300 px-2 py-1 text-sm"
                defaultValue=""
                onChange={(e) => {
                  const to = e.target.value as CaseStatus;
                  if (to) m.transition.mutate({ to, reason: "manual", expected_version: c.version });
                  e.target.value = "";
                }}
              >
                <option value="" disabled>Move to…</option>
                {manualTargets.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <Button variant="secondary" onClick={() => m.release.mutate()} disabled={m.release.isPending}>Return to agent</Button>
            </>
          )}
        </div>
      </div>
      {(m.takeover.error || m.release.error || m.transition.error) && (
        <ErrorBox error={m.takeover.error ?? m.release.error ?? m.transition.error} />
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title={`Proposed actions${pending.length ? ` · ${pending.length} pending` : ""}`}>
            {actions.length === 0 ? (
              <Empty>No proposed actions yet. Agents will post proposals here as they work the case.</Empty>
            ) : (
              <div className="space-y-3">{actions.map((a) => <ActionCard key={a.id} action={a} caseVersion={c.version} />)}</div>
            )}
          </Card>

          <Card title="Agents">
            <AgentRunCard caseId={c.id} closed={closed} />
          </Card>

          <Card title="Email">
            <EmailThreadPanel caseId={c.id} defaultTo={erp.customer?.contacts.find((ct) => ct.active && ct.role === "AP")?.email ?? erp.customer?.contacts.find((ct) => ct.active)?.email} />
          </Card>

          <Card title="Tool calls">
            <ToolCallsPanel caseId={c.id} />
          </Card>

          <Card title="Activity">
            <Timeline events={timeline} />
            <form
              className="mt-4 flex gap-2 border-t border-slate-100 pt-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (note.trim()) m.note.mutate(note, { onSuccess: () => setNote("") });
              }}
            >
              <input className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm" placeholder="Add a note…" value={note} onChange={(e) => setNote(e.target.value)} />
              <Button type="submit" variant="secondary" disabled={m.note.isPending}>Add</Button>
            </form>
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Customer">
            {erp.customer ? (
              <dl className="space-y-1 text-sm">
                <Row k="Terms" v={`Net ${erp.customer.payment_terms_days}`} />
                <Row k="Requires PO" v={erp.customer.requires_po ? "yes" : "no"} />
                <Row k="Credit hold" v={erp.customer.credit_hold ? "YES" : "no"} />
                <Row k="Risk score" v={erp.customer.credit_risk_score.toFixed(2)} />
                <div className="pt-2">
                  <dt className="text-xs uppercase text-slate-500">Contacts</dt>
                  {erp.customer.contacts.map((ct) => (
                    <dd key={ct.email} className={ct.active ? "" : "text-slate-400 line-through"}>
                      {ct.name} · {ct.role} · <span className="font-mono text-xs">{ct.email}</span>
                    </dd>
                  ))}
                </div>
              </dl>
            ) : (
              <Empty>ERP unavailable</Empty>
            )}
          </Card>
          {erp.invoices.map((inv) => (
            <Card key={inv.invoice_ref} title={inv.invoice_ref} right={<span className="text-xs text-slate-500">{inv.status}</span>}>
              <dl className="space-y-1 text-sm">
                <Row k="PO" v={inv.po_number ?? <span className="text-rose-600">missing</span>} />
                <Row k="Issued / due" v={`${inv.issue_date} → ${inv.due_date}`} />
                <Row k="Billed to" v={<span className="font-mono text-xs">{inv.billed_to_email ?? "—"}</span>} />
                <Row k="Subtotal" v={money(inv.subtotal)} />
                <Row k="Freight / tax" v={`${money(inv.freight)} / ${money(inv.tax)}`} />
                <Row k="Total / paid" v={`${money(inv.total)} / ${money(inv.amount_paid)}`} />
              </dl>
              <table className="mt-3 w-full text-xs">
                <thead className="text-left text-slate-500"><tr><th>#</th><th>SKU</th><th className="text-right">Qty</th><th className="text-right">Price</th><th className="text-right">Amt</th></tr></thead>
                <tbody>
                  {inv.lines.map((l) => (
                    <tr key={l.line_no} className="border-t border-slate-100">
                      <td>{l.line_no}</td><td>{l.sku} <span className="text-slate-400">{l.description}</span></td>
                      <td className="text-right tabular-nums">{l.qty}</td>
                      <td className="text-right tabular-nums">{money(l.unit_price)}</td>
                      <td className="text-right tabular-nums">{money(l.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-slate-500">{k}</dt>
      <dd className="text-right">{v}</dd>
    </div>
  );
}
