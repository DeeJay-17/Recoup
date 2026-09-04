import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import type { ProposedAction } from "@/api/schemas";
import { useDecideAction } from "@/api/hooks";
import { hasRole, useAuth } from "@/store/auth";
import { Button, ErrorBox } from "./ui";
import { DiffView } from "./DiffView";
import { relTime } from "@/lib/format";

const decisionTone = {
  ALLOW: "bg-emerald-100 text-emerald-800",
  REQUIRE_APPROVAL: "bg-orange-100 text-orange-800",
  DENY: "bg-rose-100 text-rose-800",
};
const statusTone: Record<string, string> = {
  PENDING: "bg-orange-50 text-orange-700 ring-orange-200",
  APPROVED: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  EDITED: "bg-sky-50 text-sky-700 ring-sky-200",
  REJECTED: "bg-rose-50 text-rose-700 ring-rose-200",
  EXECUTED: "bg-teal-50 text-teal-700 ring-teal-200",
  AUTO_EXECUTED: "bg-teal-50 text-teal-700 ring-teal-200",
  DENIED: "bg-zinc-100 text-zinc-700 ring-zinc-200",
};
const FEEDBACK = ["", "wrong_amount", "wrong_recipient", "tone", "missing_evidence", "policy", "other"];

type Draft = Record<string, unknown>;

/** Field-level editor per action type; falls back to raw JSON. */
function fieldsFor(actionType: string, payload: Draft): { key: string; label: string; multiline?: boolean }[] {
  if (actionType === "SEND_EMAIL") {
    const f = [{ key: "to", label: "To (comma separated)" }];
    if (payload.template) f.push({ key: "template", label: "Template" });
    else f.push({ key: "subject", label: "Subject" }, { key: "body_text", label: "Body", multiline: true } as { key: string; label: string; multiline?: boolean });
    return f;
  }
  if (actionType === "CREATE_CREDIT_MEMO") return [{ key: "invoice_ref", label: "Invoice" }, { key: "amount", label: "Amount" }, { key: "reason_code", label: "Reason code" }, { key: "memo", label: "Memo", multiline: true }];
  if (actionType === "PAYMENT_PLAN") return [{ key: "invoice_refs", label: "Invoices (comma separated)" }, { key: "installments", label: "Installments" }, { key: "first_due", label: "First due (YYYY-MM-DD)" }, { key: "discount_pct", label: "Discount %" }];
  return [];
}

function toText(v: unknown): string {
  if (Array.isArray(v)) return v.join(", ");
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? JSON.stringify(v, null, 2) : String(v);
}
function fromText(original: unknown, text: string): unknown {
  if (Array.isArray(original)) return text.split(",").map((s) => s.trim()).filter(Boolean);
  if (typeof original === "number") return Number(text);
  return text;
}
function render(actionType: string, p: Draft): string {
  if (actionType === "SEND_EMAIL") return p.template ? `template: ${String(p.template)}\nto: ${toText(p.to)}\nvariables: ${JSON.stringify(p.variables ?? {}, null, 2)}` : `to: ${toText(p.to)}\nsubject: ${toText(p.subject)}\n\n${toText(p.body_text)}`;
  return JSON.stringify(p, null, 2);
}

export function ActionCard({ action, caseVersion, focused, onDecided }: { action: ProposedAction; caseVersion?: number; focused?: boolean; onDecided?: () => void }) {
  const me = useAuth((s) => s.me);
  const decide = useDecideAction(action.case_id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>({ ...action.payload });
  const [raw, setRaw] = useState(JSON.stringify(action.payload, null, 2));
  const [note, setNote] = useState("");
  const [code, setCode] = useState("");
  const fields = useMemo(() => fieldsFor(action.action_type, action.payload), [action]);
  const canDecide = action.status === "PENDING" && hasRole(me, (action.required_role as "analyst" | "manager" | "admin" | null) ?? "analyst");
  const shown = action.human_final ? { ...action.payload, ...action.human_final } : action.payload;

  const submit = (decision: "approve" | "reject" | "edit") => {
    let human_final: Draft | undefined;
    if (decision === "edit") {
      if (fields.length === 0) {
        try { human_final = JSON.parse(raw) as Draft; } catch { alert("Edited payload must be valid JSON"); return; }
      } else {
        human_final = Object.fromEntries(Object.entries(draft).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(action.payload[k])));
        if (Object.keys(human_final).length === 0) { submit("approve"); return; }
      }
    }
    decide.mutate({ actionId: action.id, decision, human_final, feedback_code: code || undefined, feedback_note: note || undefined, expected_version: caseVersion }, { onSuccess: onDecided });
  };

  // keyboard shortcuts when this card is focused in the queue
  useEffect(() => {
    if (!focused || !canDecide) return;
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT") return;
      if (e.key === "a") submit("approve");
      else if (e.key === "r") submit("reject");
      else if (e.key === "e") setEditing((v) => !v);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focused, canDecide, draft, raw, note, code]);

  return (
    <div className={clsx("rounded-lg border bg-white p-4 shadow-sm ring-1", statusTone[action.status], focused && "outline outline-2 outline-slate-900")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-900">{action.action_type.replace("_", " ")}</span>
        <span className={clsx("rounded px-1.5 py-0.5 text-xs", decisionTone[action.policy_decision])}>policy: {action.policy_decision}{action.required_role ? ` (${action.required_role})` : ""}</span>
        <span className="rounded bg-white/70 px-1.5 py-0.5 text-xs ring-1 ring-inset ring-slate-200">{action.status}</span>
        <span className="ml-auto text-xs text-slate-500">{action.proposed_by} · {relTime(action.created_at)}</span>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-3">
        <div className="md:col-span-2">
          <h4 className="text-xs font-semibold uppercase text-slate-500">{editing ? "Edit proposal" : "Proposal"}</h4>
          {!editing && <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 font-sans text-xs">{render(action.action_type, shown)}</pre>}
          {!editing && action.human_final && (
            <div className="mt-2"><h5 className="text-xs uppercase text-slate-400">Human edit vs agent draft</h5><DiffView before={render(action.action_type, action.payload)} after={render(action.action_type, shown)} /></div>
          )}
          {editing && fields.length > 0 && (
            <div className="mt-1 space-y-2">
              {fields.map((f) => (
                <label key={f.key} className="block text-xs text-slate-600">{f.label}
                  {f.multiline ? (
                    <textarea className="mt-0.5 h-40 w-full rounded border border-slate-300 p-2 text-sm text-slate-900" value={toText(draft[f.key])} onChange={(e) => setDraft({ ...draft, [f.key]: fromText(action.payload[f.key], e.target.value) })} />
                  ) : (
                    <input className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-sm text-slate-900" value={toText(draft[f.key])} onChange={(e) => setDraft({ ...draft, [f.key]: fromText(action.payload[f.key], e.target.value) })} />
                  )}
                </label>
              ))}
              <h5 className="text-xs uppercase text-slate-400">Diff</h5>
              <DiffView before={render(action.action_type, action.payload)} after={render(action.action_type, draft)} />
            </div>
          )}
          {editing && fields.length === 0 && <textarea className="mt-1 h-40 w-full rounded border border-slate-300 p-2 font-mono text-xs" value={raw} onChange={(e) => setRaw(e.target.value)} />}
        </div>
        <div>
          <h4 className="text-xs font-semibold uppercase text-slate-500">Why</h4>
          <p className="mt-1 text-sm text-slate-800">{action.rationale}</p>
          {action.evidence_refs.length > 0 && <ul className="mt-2 list-disc pl-4 text-xs text-slate-600">{action.evidence_refs.map((r, i) => <li key={i}>{typeof r === "string" ? r : JSON.stringify(r)}</li>)}</ul>}
          <h4 className="mt-3 text-xs font-semibold uppercase text-slate-500">Policy</h4>
          <p className="mt-1 text-sm text-slate-800">{action.policy_rule ?? "—"}</p>
          {action.decided_at && <p className="mt-2 text-xs text-slate-500">{action.status.toLowerCase()} {relTime(action.decided_at)}{action.feedback_code ? ` · ${action.feedback_code}` : ""}{action.feedback_note ? ` — "${action.feedback_note}"` : ""}</p>}
          {action.executed_at && <p className="mt-1 text-xs text-teal-700">executed {relTime(action.executed_at)}</p>}
        </div>
      </div>

      {canDecide && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
          <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={code} onChange={(e) => setCode(e.target.value)}>
            {FEEDBACK.map((f) => <option key={f} value={f}>{f || "feedback code"}</option>)}
          </select>
          <input className="min-w-56 flex-1 rounded border border-slate-300 px-2 py-1 text-sm" placeholder="Feedback note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          {editing ? (
            <>
              <Button onClick={() => submit("edit")} disabled={decide.isPending}>Save edit & approve</Button>
              <Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            </>
          ) : (
            <>
              <Button onClick={() => submit("approve")} disabled={decide.isPending} title="a">Approve</Button>
              <Button variant="secondary" onClick={() => setEditing(true)} title="e">Edit</Button>
              <Button variant="danger" onClick={() => submit("reject")} disabled={decide.isPending} title="r">Reject</Button>
            </>
          )}
        </div>
      )}
      {decide.error && <div className="mt-2"><ErrorBox error={decide.error} /></div>}
    </div>
  );
}
