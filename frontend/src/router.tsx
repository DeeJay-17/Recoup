import { Outlet, createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { Layout } from "@/components/Layout";
import { LoginPage } from "@/routes/login";
import { CasesPage } from "@/routes/cases";
import { CaseDetailPage } from "@/routes/case-detail";
import { ApprovalsPage } from "@/routes/approvals";
import { PoliciesPage } from "@/routes/policies";
import { useAuth } from "@/store/auth";

const rootRoute = createRootRoute({ component: () => <Outlet /> });

const loginRoute = createRoute({ getParentRoute: () => rootRoute, path: "/login", component: LoginPage });

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  beforeLoad: () => {
    if (!useAuth.getState().token) throw redirect({ to: "/login" });
  },
  component: Layout,
});

const indexRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/cases" });
  },
});
const casesRoute = createRoute({ getParentRoute: () => appRoute, path: "/cases", component: CasesPage });
const caseDetailRoute = createRoute({ getParentRoute: () => appRoute, path: "/cases/$caseId", component: CaseDetailPage });
const approvalsRoute = createRoute({ getParentRoute: () => appRoute, path: "/approvals", component: ApprovalsPage });
const policiesRoute = createRoute({ getParentRoute: () => appRoute, path: "/policies", component: PoliciesPage });

const routeTree = rootRoute.addChildren([
  loginRoute,
  appRoute.addChildren([indexRoute, casesRoute, caseDetailRoute, approvalsRoute, policiesRoute]),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
