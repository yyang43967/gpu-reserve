import { isRouteErrorResponse, useRouteError } from "react-router";

import { ErrorState } from "@/components/states";

export function RouteErrorPage() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${String(error.status)} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : "An unexpected error occurred.";

  return (
    <main className="mx-auto max-w-xl px-4 py-12">
      <ErrorState
        message={message}
        onRetry={() => {
          window.location.reload();
        }}
      />
    </main>
  );
}
