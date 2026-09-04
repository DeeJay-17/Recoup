import clsx from "clsx";
import type { ReactNode } from "react";
import type { CaseStatus } from "@/api/schemas";

const statusTone: Record<CaseStatus, string> = {
  NEW: "bg-slate-100 text-slate-700",
  TRIAGED: "bg-sky-100 text-sky-800",
  INVESTIGATING: "bg-indigo-100 text-indigo-800",
  AWAITING_CUSTOMER: "bg-amber-100 text-amber-800",
  NEGOTIATING: "bg-violet-100 text-violet-800",
  PENDING_APPROVAL: "bg-orange-100 text-orange-800",
  ACTION_TAKEN: "bg-teal-100 text-teal-800",
  RESOLVED: "bg-emerald-100 text-emerald-800",
  ESCALATED: "bg-rose-100 text-rose-800",
  WRITTEN_OFF: "bg-zinc-200 text-zinc-700",
};

export function StatusBadge({ status }: { status: CaseStatus }) {
  return (
    <span className={clsx("rounded px-2 py-0.5 text-xs font-medium tracking-wide", statusTone[status])}>
      {status.replace("_", " ")}
    </span>
  );
}

export function Pill({ children, tone = "slate" }: { children: ReactNode; tone?: string }) {
  return (
    <span className={clsx("rounded-full px-2 py-0.5 text-xs font-medium", `bg-${tone}-100 text-${tone}-800`)}>
      {children}
    </span>
  );
}

export function PriorityDot({ p }: { p: number }) {
  const tone = ["", "bg-rose-500", "bg-orange-500", "bg-amber-400", "bg-sky-400", "bg-slate-300"][p] ?? "bg-slate-300";
  return (
    <span className="inline-flex items-center gap-1 text-xs text-slate-600">
      <span className={clsx("inline-block h-2 w-2 rounded-full", tone)} /> P{p}
    </span>
  );
}

export function Button({
  children,
  variant = "primary",
  className,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const base = "inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-sm font-medium transition disabled:opacity-50";
  const tones = {
    primary: "bg-slate-900 text-white hover:bg-slate-700",
    secondary: "border border-slate-300 bg-white text-slate-800 hover:bg-slate-100",
    danger: "bg-rose-600 text-white hover:bg-rose-500",
    ghost: "text-slate-700 hover:bg-slate-100",
  };
  return (
    <button className={clsx(base, tones[variant], className)} {...rest}>
      {children}
    </button>
  );
}

export function Card({ title, children, right }: { title?: ReactNode; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
      {title && (
        <header className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
          <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-8 text-center text-sm text-slate-500">{children}</p>;
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="rounded border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{msg}</div>;
}
