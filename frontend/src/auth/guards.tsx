import { Navigate, Outlet, useLocation, useSearchParams } from "react-router";

import { useAuth } from "@/auth/auth-context";
import { loginPathFor, safeNextPath } from "@/auth/next-path";
import { ErrorState, LoadingState } from "@/components/states";

// These guards shape navigation only. The API enforces every permission itself.

function SessionGate({ children }: { children: React.ReactNode }) {
  const auth = useAuth();
  if (auth.status === "loading") {
    return (
      <div className="mx-auto max-w-6xl px-4 py-6">
        <LoadingState label="Checking your session" />
      </div>
    );
  }
  if (auth.status === "error") {
    return (
      <div className="mx-auto max-w-md px-4 py-6">
        <ErrorState
          title="Could not reach the server"
          message={auth.error.message}
          onRetry={auth.retry}
        />
      </div>
    );
  }
  return children;
}

/** Pages for signed-in users. Others go to the login page and come back afterwards. */
export function RequireAuth() {
  return (
    <SessionGate>
      <RequireAuthInner />
    </SessionGate>
  );
}

function RequireAuthInner() {
  const auth = useAuth();
  const location = useLocation();
  if (auth.status !== "authenticated") {
    return <Navigate to={loginPathFor(location)} replace />;
  }
  return <Outlet />;
}

/** Admin pages. Must sit inside RequireAuth. */
export function RequireAdmin() {
  const auth = useAuth();
  if (auth.user?.role !== "admin") {
    return <Navigate to="/" replace />;
  }
  return <Outlet />;
}

/** Login and registration: a signed-in user goes straight to where they were headed. */
export function RedirectIfAuthenticated() {
  return (
    <SessionGate>
      <RedirectIfAuthenticatedInner />
    </SessionGate>
  );
}

function RedirectIfAuthenticatedInner() {
  const auth = useAuth();
  const [searchParams] = useSearchParams();
  if (auth.status === "authenticated") {
    return <Navigate to={safeNextPath(searchParams.get("next"))} replace />;
  }
  return <Outlet />;
}
