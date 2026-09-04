import { Link } from "@tanstack/react-router";
import { useApprovals } from "@/api/hooks";
import { ActionCard } from "@/components/ActionCard";
import { Empty, ErrorBox } from "@/components/ui";

export function ApprovalsPage() {
  const q = useApprovals();
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Approval inbox</h1>
      {q.error && <ErrorBox error={q.error} />}
      {q.data?.length === 0 && <Empty>Nothing waiting for approval.</Empty>}
      <div className="space-y-3">
        {q.data?.map((a) => (
          <div key={a.id}>
            <Link to="/cases/$caseId" params={{ caseId: a.case_id }} className="text-xs text-slate-500 hover:underline">
              open case {a.case_id.slice(0, 8)} →
            </Link>
            <ActionCard action={a} />
          </div>
        ))}
      </div>
    </div>
  );
}
