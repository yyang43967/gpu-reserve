import { useQuery } from "@tanstack/react-query";

import { ErrorState, LoadingState } from "@/components/states";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getHealth } from "@/lib/api";

const healthQueryKey = ["health"] as const;

function ApiStatusCard() {
  const health = useQuery({
    queryKey: healthQueryKey,
    queryFn: ({ signal }) => getHealth(signal),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>API status</CardTitle>
        <CardDescription>Connection to the backend service.</CardDescription>
      </CardHeader>
      <CardContent>
        {health.isPending ? (
          <LoadingState label="Checking API status" />
        ) : health.isError ? (
          <ErrorState
            title="API unreachable"
            message={health.error.message}
            onRetry={() => {
              void health.refetch();
            }}
          />
        ) : (
          <p className="text-sm">
            The API is <span className="font-medium">online</span>.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function DashboardPage() {
  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your quota, upcoming reservations, and quick links.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <ApiStatusCard />
      </div>
    </>
  );
}
