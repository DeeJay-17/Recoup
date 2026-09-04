import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface Me {
  id: string;
  tenant_id: string;
  tenant_slug: string;
  email: string;
  full_name: string;
  roles: string[];
}

interface AuthState {
  token: string | null;
  me: Me | null;
  setToken: (token: string | null) => void;
  setMe: (me: Me | null) => void;
  logout: () => void;
}

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      me: null,
      setToken: (token) => set({ token }),
      setMe: (me) => set({ me }),
      logout: () => set({ token: null, me: null }),
    }),
    { name: "recoup.auth" },
  ),
);

export function hasRole(me: Me | null, role: "viewer" | "analyst" | "manager" | "admin"): boolean {
  if (!me) return false;
  const rank = { viewer: 0, analyst: 1, manager: 2, admin: 3 } as const;
  return me.roles.some((r) => (rank as Record<string, number>)[r] >= rank[role]);
}
