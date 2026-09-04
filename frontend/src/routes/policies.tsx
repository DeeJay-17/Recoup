import { useState } from "react";
import clsx from "clsx";
import { usePolicies, usePolicyMutations } from "@/api/hooks";
import type { Policy, SimulateResponse } from "@/api/schemas";
import { hasRole, useAuth } from "@/store/auth";
import { Button, Card, Empty, ErrorBox } from "@/components/ui";

const decisionTone: Record<string, string> = {
  ALLOW: "bg-emerald-100 text-emerald-800",
  REQUIRE_APPROVAL: "bg-orange-100 text-orange-800",
  DENY: "bg-rose-100 text-rose-800",
};

export function PoliciesPage() {
  const q = usePolicies();
  const m = usePolicyMutations();
  const me = useAuth((s) => s.me);
  const canEdit = hasRole(me, "manager");
  const [selected, setSelected] = useState<Policy | null>(null);
  const grouped = new Map<string, Policy[]>();
  for (const p of q.data ?? []) grouped.set(p.action_type, [...(grouped.get(p.action_type) ?? []), p]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">Policies</h1>
        <span className="text-sm text-slate-500">Deterministic guardrails. A DENY anywhere wins; otherwise the highest-priority match decides.</span>
        {canEdit && q.data?.length === 0 && (
          <Button className="ml-auto" onClick={() => m.installDefaults.mutate()}>Install default policies</Button>
        )}
      </div>
      {q.error && <ErrorBox error={q.error} />}
      <div className="grid gap-4 lg:grid-cols-5">
        <div className="space-y-4 lg:col-span-2">
          {[...grouped.entries()].map(([action, list]) => (
            <Card key={action} title={action}>
              <ul className="divide-y divide-slate-100">
                {list.map((p) => (
                  <li key={p.id} className={clsx("flex items-center gap-2 py-2", !p.enabled && "opacity-50")}>
                    <button className="flex-1 text-left" onClick={() => setSelected(p)}>
                      <div className="text-sm font-medium text-slate-900">{p.name} <span className="text-xs text-slate-400">· p{p.priority} · v{p.current_version}</span></div>
                      <div className="text-xs text-slate-500">{p.description}</div>
                    </button>
                    <span className={clsx("rounded px-1.5 py-0.5 text-xs", decisionTone[p.current?.decision ?? ""])}>
                      {p.current?.decision}{p.current?.required_role ? ` (${p.current.required_role})` : ""}
                    </span>
                    {canEdit && (
                      <input type="checkbox" checked={p.enabled} onChange={(e) => m.toggle.mutate({ id: p.id, enabled: e.target.checked })} title="enabled" />
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          ))}
          {q.data?.length === 0 && <Empty>No policies yet.</Empty>}
        </div>
        <div className="lg:col-span-3">
          {selected ? <PolicyEditor key={selected.id} policy={selected} canEdit={canEdit} /> : <Empty>Select a policy to inspect, edit and simulate.</Empty>}
        </div>
      </div>
    </div>
  );
}

function PolicyEditor({ policy, canEdit }: { policy: Policy; canEdit: boolean }) {
  const m = usePolicyMutations();
  const [rule, setRule] = useState(JSON.stringify(policy.current?.rule ?? {}, null, 2));
  const [decision, setDecision] = useState(policy.current?.decision ?? "REQUIRE_APPROVAL");
  const [role, setRole] = useState(policy.current?.required_role ?? "analyst");
  const [reason, setReason] = useState(policy.current?.reason ?? "");
  const [sim, setSim] = useState<SimulateResponse | null>(null);
  const [parseErr, setParseErr] = useState<string | null>(null);

  const parsed = () => {
    try {
      setParseErr(null);
      return JSON.parse(rule) as unknown;
    } catch (e) {
      setParseErr((e as Error).message);
      return null;
    }
  };
  const reqRole = decision === "REQUIRE_APPROVAL" ? role : null;

  return (
    <Card title={<span>{policy.name} <span className="font-normal text-slate-400">· {policy.action_type}</span></span>}>
      <p className="text-sm text-slate-600">{policy.description}</p>
      <label className="mt-3 block text-xs font-semibold uppercase text-slate-500">Rule (JSON)</label>
      <textarea className="mt-1 h-56 w-full rounded border border-slate-300 p-2 font-mono text-xs" value={rule} onChange={(e) => setRule(e.target.value)} readOnly={!canEdit} />
      {parseErr && <ErrorBox error={parseErr} />}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <label>Decision</label>
        <select className="rounded border border-slate-300 px-2 py-1" value={decision} onChange={(e) => setDecision(e.target.value as typeof decision)} disabled={!canEdit}>
          <option>ALLOW</option><option>REQUIRE_APPROVAL</option><option>DENY</option>
        </select>
        {decision === "REQUIRE_APPROVAL" && (
          <select className="rounded border border-slate-300 px-2 py-1" value={role} onChange={(e) => setRole(e.target.value)} disabled={!canEdit}>
            <option>analyst</option><option>manager</option><option>admin</option>
          </select>
        )}
        <input className="min-w-56 flex-1 rounded border border-slate-300 px-2 py-1" placeholder="Reason shown to agents/humans" value={reason} onChange={(e) => setReason(e.target.value)} readOnly={!canEdit} />
      </div>
      {canEdit && (
        <div className="mt-3 flex gap-2">
          <Button variant="secondary" onClick={() => { const r = parsed(); if (r) m.simulate.mutate({ id: policy.id, rule: r, decision, required_role: reqRole }, { onSuccess: setSim }); }} disabled={m.simulate.isPending}>
            Simulate against history
          </Button>
          <Button onClick={() => { const r = parsed(); if (r) m.newVersion.mutate({ id: policy.id, rule: r, decision, required_role: reqRole, reason: reason || null }); }} disabled={m.newVersion.isPending}>
            Save as v{policy.current_version + 1}
          </Button>
        </div>
      )}
      {(m.simulate.error || m.newVersion.error) && <div className="mt-2"><ErrorBox error={m.simulate.error ?? m.newVersion.error} /></div>}
      {sim && (
        <div className="mt-4">
          <h4 className="text-xs font-semibold uppercase text-slate-500">Simulation · {sim.changed} of {sim.total} past decisions would change</h4>
          {sim.total === 0 && <Empty>No historical evaluations for this action type yet.</Empty>}
          <ul className="mt-2 max-h-64 divide-y divide-slate-100 overflow-auto text-xs">
            {sim.rows.slice(0, 50).map((r, i) => (
              <li key={i} className={clsx("flex items-center gap-2 py-1", r.changed && "bg-amber-50")}>
                <span className={clsx("rounded px-1 py-0.5", decisionTone[r.current_decision])}>{r.current_decision}</span>
                <span>→</span>
                <span className={clsx("rounded px-1 py-0.5", decisionTone[r.candidate_decision])}>{r.candidate_decision}</span>
                <code className="ml-2 truncate text-slate-500">{JSON.stringify(r.context).slice(0, 120)}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
