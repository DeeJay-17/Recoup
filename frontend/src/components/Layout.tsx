import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { useAuth } from "@/store/auth";
import { Button } from "./ui";

const nav = [
  { to: "/cases", label: "Cases" },
  { to: "/approvals", label: "Approvals" },
];

export function Layout() {
  const me = useAuth((s) => s.me);
  const logout = useAuth((s) => s.logout);
  const navigate = useNavigate();
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
