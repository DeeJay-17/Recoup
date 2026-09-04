import { useState } from "react";
import clsx from "clsx";
import type { ProposedAction } from "@/api/schemas";
import { useDecideAction } from "@/api/hooks";
import { hasRole, useAuth } from "@/store/auth";
import { Button, ErrorBox } from "./ui";
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

export function ActionCard({ action, caseVersion }: { action: ProposedAction; caseVersion?: number }) {
  const me = useAuth((s) => s.me);
  const decide = useDecideAction(action.case_id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(JSON.stringify(action.payload, null, 2));
  const [note, setNote] = useState("");
  const canDecide =
    action.status === "PENDING" &&
    hasRole(me, (action.required_role as "analyst" | "manager" | "admin" | null) ?? "analyst");

  const submit = (decision: "approve" | "reject" | "edit") => {
    let human_final: Record<string, unknown> | undefined;
    if (decision === "edit") {
      try {
        human_final = JSON.parse(draft) as Record<string, unknown>;
      } catch {
        alert("Edited payload must be valid JSON");
        return;
      }
    }
    decide.mutate({
      actionId: action.id,
      decision,
      human_final,
      feedback_note: note || undefined,
      expected_version: caseVersion,
    });
  };

  return (
    <div className={clsx("rounded-lg border bg-white p-4 shadow-sm ring-1", statusTone[action.status])}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-900">{action.action_type.replace("_", " ")}</span>
        <span className={clsx("rounded px-1.5 py-0.5 text-xs", decisionTone[action.policy_decision])}>
          policy: {action.policy_decision}
          {action.required_role ? ` (${action.required_role})` : ""}
        </span>
        <span className="rounded bg-white/70 px-1.5 py-0.5 text-xs ring-1 ring-inset ring-slate-200">{action.status}</span>
        <span className="ml-auto text-xs text-slate-500">
          {action.proposed_by} · {relTime(action.created_at)}
        </span>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-3">
        <div>
          <h4 className="text-xs font-semibold uppercase text-slate-500">Proposal</h4>
          {editing ? (
            <textarea
              className="mt-1 h-40 w-full rounded border border-slate-300 p-2 font-mono text-xs"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          ) : (
            <pre className="mt-1 max-h-40 overflow-auto rounded bg-slate-50 p-2 text-xs">
              {JSON.stringify(action.human_final ?? action.payload, null, 2)}
            </pre>
          )}
        </div>
        <div>
          <h4 className="text-xs font-semibold uppercase text-slate-500">Why</h4>
          <p className="mt-1 text-sm text-slate-800">{action.rationale}</p>
          {action.evidence_refs.length > 0 && (
            <ul className="mt-2 list-disc pl-4 text-xs text-slate-600">
              {action.evidence_refs.map((r, i) => (
                <li key={i}>{typeof r === "string" ? r : JSON.stringify(r)}</li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h4 className="text-xs font-semibold uppercase text-slate-500">Policy</h4>
          <p className="mt-1 text-sm text-slate-800">{action.policy_rule ?? "—"}</p>
          {action.decided_at && (
            <p className="mt-2 text-xs text-slate-500">
              {action.status.toLowerCase()} {relTime(action.decided_at)}
              {action.feedback_note ? ` — "${action.feedback_note}"` : ""}
            </p>
          )}
        </div>
      </div>

      {canDecide && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
          <input
            className="min-w-64 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="Feedback note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          {editing ? (
            <>
              <Button onClick={() => submit("edit")} disabled={decide.isPending}>
                Save edit & approve
              </Button>
              <Button variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <Button onClick={() => submit("approve")} disabled={decide.isPending}>
                Approve
              </Button>
              <Button variant="secondary" onClick={() => setEditing(true)}>
                Edit
              </Button>
              <Button variant="danger" onClick={() => submit("reject")} disabled={decide.isPending}>
                Reject
              </Button>
            </>
          )}
        </div>
      )}
      {decide.error && (
        <div className="mt-2">
          <ErrorBox error={decide.error} />
        </div>
      )}
    </div>
  );
}
