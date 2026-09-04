import { useState } from "react";
import clsx from "clsx";
import { Link, useParams } from "@tanstack/react-router";
import { useCustomer, useCustomerCases, useCustomerDocs, useCustomerMemory, useKnowledgeSearch, useMemoryMutations } from "@/api/hooks";
import { hasRole, useAuth } from "@/store/auth";
import { Button, Card, Empty, ErrorBox, PriorityDot, StatusBadge } from "@/components/ui";
import { money, relTime, shortId } from "@/lib/format";

const catTone: Record<string, string> = {
  CONTACT: "bg-sky-100 text-sky-800",
  PREFERENCE: "bg-violet-100 text-violet-800",
  PATTERN: "bg-amber-100 text-amber-800",
  RISK: "bg-rose-100 text-rose-800",
};

export function CustomerPage() {
  const { customerRef } = useParams({ from: "/app/customers/$customerRef" });
  const cust = useCustomer(customerRef);
  const cases = useCustomerCases(customerRef);
  const mem = useCustomerMemory(customerRef);
  const docs = useCustomerDocs(customerRef);
  const m = useMemoryMutations(customerRef);
  const search = useKnowledgeSearch();
  const me = useAuth((s) => s.me);
  const canEdit = hasRole(me, "analyst");
  const [fact, setFact] = useState("");
  const [cat, setCat] = useState("PREFERENCE");
  const [q, setQ] = useState("");

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-lg font-semibold">{cust.data?.name ?? customerRef}</h1>
        <span className="text-sm text-slate-500">{customerRef}</span>
        {cust.data && (
          <span className="text-sm text-slate-600">· net {cust.data.payment_terms_days} · PO {cust.data.requires_po ? "required" : "optional"} · risk {cust.data.credit_risk_score.toFixed(2)}{cust.data.credit_hold ? " · CREDIT HOLD" : ""}</span>
        )}
      </header>
      {cust.error && <ErrorBox error={cust.error} />}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title={`Memory${mem.data ? ` · ${mem.data.length} facts` : ""}`}>
            {mem.error && <ErrorBox error={mem.error} />}
            {mem.data?.length === 0 && <Empty>Nothing learned about this customer yet. Facts appear here after cases resolve.</Empty>}
            <ul className="divide-y divide-slate-100">
              {mem.data?.map((f) => (
                <li key={f.id} className="flex items-start gap-2 py-2 text-sm">
                  <span className={clsx("mt-0.5 rounded px-1.5 py-0.5 text-xs font-medium", catTone[f.category])}>{f.category}</span>
                  <div className="flex-1">
                    <p className="text-slate-900">{f.fact}</p>
                    <p className="text-xs text-slate-500">
                      {Math.round(f.confidence * 100)}% · {f.created_by} · {relTime(f.created_at)}
                      {f.source_case_id && <> · from case <Link to="/cases/$caseId" params={{ caseId: f.source_case_id }} className="hover:underline">{shortId(f.source_case_id)}</Link></>}
                      {f.source_ref && f.source_ref !== "agent" && f.source_ref !== "console" ? <> · "{f.source_ref}"</> : null}
                    </p>
                  </div>
                  {canEdit && <button className="text-xs text-slate-400 hover:text-rose-600" title="retire this fact" onClick={() => m.retire.mutate(f.id)}>retire</button>}
                </li>
              ))}
            </ul>
            {canEdit && (
              <form className="mt-3 flex flex-wrap gap-2 border-t border-slate-100 pt-3" onSubmit={(e) => { e.preventDefault(); if (fact.trim()) m.add.mutate({ fact, category: cat, confidence: 0.9 }, { onSuccess: () => setFact("") }); }}>
                <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={cat} onChange={(e) => setCat(e.target.value)}>
                  {Object.keys(catTone).map((c) => <option key={c}>{c}</option>)}
                </select>
                <input className="min-w-64 flex-1 rounded border border-slate-300 px-2 py-1 text-sm" placeholder="Add a durable fact about this customer…" value={fact} onChange={(e) => setFact(e.target.value)} />
                <Button type="submit" variant="secondary" disabled={m.add.isPending}>Remember</Button>
              </form>
            )}
            {(m.add.error || m.retire.error) && <div className="mt-2"><ErrorBox error={m.add.error ?? m.retire.error} /></div>}
          </Card>

          <Card title="Ask the knowledge base">
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (q.trim()) search.mutate({ query: q, customer_ref: customerRef }); }}>
              <input className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm" placeholder="e.g. is freight billable for this customer? how did we resolve their last dispute?" value={q} onChange={(e) => setQ(e.target.value)} />
              <Button type="submit" disabled={search.isPending}>Search</Button>
            </form>
            {search.error && <ErrorBox error={search.error} />}
            <ul className="mt-3 space-y-2">
              {search.data?.map((h) => (
                <li key={h.chunk_id} className="rounded border border-slate-100 bg-slate-50 p-2 text-sm">
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <span className="rounded bg-white px-1.5 py-0.5 ring-1 ring-slate-200">{h.kind}</span>
                    <span className="font-medium text-slate-700">{h.title}</span>
                    <span className="ml-auto">{h.sources.join("+")} · {h.score.toFixed(3)}</span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-slate-800">{h.content.slice(0, 600)}{h.content.length > 600 ? "…" : ""}</p>
                </li>
              ))}
              {search.data?.length === 0 && <Empty>No matches.</Empty>}
            </ul>
          </Card>

          <Card title={`Case history${cases.data ? ` · ${cases.data.total}` : ""}`}>
            <table className="w-full text-sm">
              <tbody>
                {cases.data?.items.map((c) => (
                  <tr key={c.id} className="border-t border-slate-100">
                    <td className="py-1.5"><PriorityDot p={c.priority} /></td>
                    <td className="py-1.5"><Link to="/cases/$caseId" params={{ caseId: c.id }} className="font-mono text-xs hover:underline">{c.invoice_refs.join(", ")}</Link></td>
                    <td className="py-1.5 text-right tabular-nums">{money(c.amount_open, c.currency)}</td>
                    <td className="py-1.5"><StatusBadge status={c.status} /></td>
                    <td className="py-1.5 text-xs text-slate-500">{c.root_cause ?? "—"}</td>
                    <td className="py-1.5 text-right text-xs text-slate-400">{relTime(c.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {cases.data?.items.length === 0 && <Empty>No cases for this customer.</Empty>}
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Contacts">
            {cust.data ? (
              <ul className="space-y-1 text-sm">
                {cust.data.contacts.map((ct) => (
                  <li key={ct.email} className={ct.active ? "" : "text-slate-400 line-through"}>{ct.name} · {ct.role} · <span className="font-mono text-xs">{ct.email}</span></li>
                ))}
              </ul>
            ) : <Empty>—</Empty>}
          </Card>
          <Card title={`Documents${docs.data ? ` · ${docs.data.length}` : ""}`}>
            <ul className="divide-y divide-slate-100 text-sm">
              {docs.data?.map((d) => (
                <li key={d.id} className="py-2">
                  <div className="flex items-center gap-2"><span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{d.kind}</span><span className="font-medium text-slate-800">{d.title}</span></div>
                  <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">{d.preview}</p>
                </li>
              ))}
            </ul>
            {docs.data?.length === 0 && <Empty>No documents.</Empty>}
          </Card>
        </div>
      </div>
    </div>
  );
}
