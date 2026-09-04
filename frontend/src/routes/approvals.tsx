import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useApprovals } from "@/api/hooks";
import { ActionCard } from "@/components/ActionCard";
import { Empty, ErrorBox } from "@/components/ui";

export function ApprovalsPage() {
  const q = useApprovals();
  const [idx, setIdx] = useState(0);
  const items = q.data ?? [];
  useEffect(() => { if (idx >= items.length) setIdx(Math.max(0, items.length - 1)); }, [items.length, idx]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT") return;
      if (e.key === "j") setIdx((i) => Math.min(items.length - 1, i + 1));
      if (e.key === "k") setIdx((i) => Math.max(0, i - 1));
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [items.length]);
  return (
    <div className="space-y-4">
      <div className="flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">Approval inbox</h1>
        <span className="text-xs text-slate-500">{items.length} pending · keys: j/k navigate · a approve · e edit · r reject</span>
      </div>
      {q.error && <ErrorBox error={q.error} />}
      {items.length === 0 && <Empty>Nothing waiting for approval.</Empty>}
      <div className="space-y-3">
        {items.map((a, i) => (
          <div key={a.id} onClick={() => setIdx(i)}>
            <Link to="/cases/$caseId" params={{ caseId: a.case_id }} className="text-xs text-slate-500 hover:underline">open case {a.case_id.slice(0, 8)} →</Link>
            <ActionCard action={a} focused={i === idx} onDecided={() => setIdx((v) => Math.min(v, Math.max(0, items.length - 2)))} />
          </div>
        ))}
      </div>
    </div>
  );
}
