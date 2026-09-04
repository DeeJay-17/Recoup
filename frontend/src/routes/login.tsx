import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useLogin } from "@/api/hooks";
import { Button, ErrorBox } from "@/components/ui";

export function LoginPage() {
  const [email, setEmail] = useState("ava@acme-demo.com");
  const [password, setPassword] = useState("password");
  const login = useLogin();
  const navigate = useNavigate();
  return (
    <div className="flex min-h-screen items-center justify-center">
      <form
        className="w-80 space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
        onSubmit={(e) => {
          e.preventDefault();
          login.mutate({ email, password }, { onSuccess: () => void navigate({ to: "/cases" }) });
        }}
      >
        <div>
          <h1 className="text-lg font-bold">Recoup</h1>
          <p className="text-sm text-slate-500">Sign in to the AR ops console</p>
        </div>
        <label className="block text-sm">
          Email
          <input className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="block text-sm">
          Password
          <input type="password" className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {login.error && <ErrorBox error={login.error} />}
        <Button type="submit" className="w-full justify-center" disabled={login.isPending}>
          {login.isPending ? "Signing in…" : "Sign in"}
        </Button>
        <p className="text-xs text-slate-400">Demo users: ava@ (analyst), marcus@ (manager), admin@acme-demo.com · password</p>
      </form>
    </div>
  );
}
