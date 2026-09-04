import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { useAuth } from "@/store/auth";
import { Button } from "./ui";
import { useLiveEvents } from "@/hooks/useLiveEvents";

const nav = [
  { to: "/cases", label: "Cases" },
  { to: "/approvals", label: "Approvals" },
  { to: "/policies", label: "Policies" },
  { to: "/agents", label: "Agents" },
];

export function Layout() {
  const me = useAuth((s) => s.me);
  const logout = useAuth((s) => s.logout);
  const navigate = useNavigate();
  const live = useLiveEvents();
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-2.5">
          <Link to="/cases" className="text-base font-bold tracking-tight">
            Recoup <span className="font-normal text-slate-500">/ AR Ops</span>
          </Link>
          <nav className="flex gap-1">
            {nav.map((n) => (
              <Link
                key={n.to}
                to={n.to}
                className="rounded px-2.5 py-1 text-sm text-slate-600 hover:bg-slate-100"
                activeProps={{ className: "bg-slate-900 text-white hover:bg-slate-900" }}
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm text-slate-600">
            <span className="flex items-center gap-1 text-xs" title={live.connected ? "Live updates connected" : "Live updates disconnected"}>
              <span className={live.connected ? "inline-block h-2 w-2 rounded-full bg-emerald-500" : "inline-block h-2 w-2 rounded-full bg-slate-300"} />
              {live.connected ? "live" : "offline"}
            </span>
            {me && (
              <span>
                {me.full_name} <span className="text-slate-400">· {me.roles.join(", ")} · {me.tenant_slug}</span>
              </span>
            )}
            <Button
              variant="ghost"
              onClick={() => {
                logout();
                void navigate({ to: "/login" });
              }}
            >
              Sign out
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-5">
        <Outlet />
      </main>
    </div>
  );
}
