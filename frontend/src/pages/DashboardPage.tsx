import { useQuery } from "@tanstack/react-query";
import { CalendarClock, CalendarSearch, ClipboardCheck, Gauge, Server } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { AdminOverview, Overview, Reservation } from "@/lib/api-types";
import { formatInLab, formatLabRange } from "@/lib/lab-time";
import { QuotaSummary } from "@/reservations/QuotaSummary";
import { RESERVATION_STATUS_LABELS } from "@/reservations/reservations-api";
import { getOverview, overviewKey } from "@/stats/stats-api";

const QUICK_LINKS = [
  { to: "/find-slot", label: "Find a slot", icon: CalendarSearch },
  { to: "/servers", label: "Browse servers", icon: Server },
  { to: "/reservations", label: "My reservations", icon: CalendarClock },
] as const;

function percent(fraction: number): string {
  if (fraction > 0 && fraction < 0.001) return "<0.1%";
  return `${(fraction * 100).toFixed(1)}%`;
}

function UpcomingItem({ reservation, now }: { reservation: Reservation; now: Date }) {
  const running = new Date(reservation.starts_at) <= now;
  return (
    <li className="grid gap-1 border-b py-3 last:border-b-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link
          to={`/servers/${reservation.server.id}`}
          className="font-medium underline-offset-4 hover:underline"
        >
          {reservation.server.hostname}
        </Link>
        <Badge variant={reservation.status === "pending" ? "secondary" : "default"}>
          {running ? "Running" : RESERVATION_STATUS_LABELS[reservation.status]}
        </Badge>
      </div>
      <p className="text-sm tabular-nums">
        {formatLabRange(reservation.starts_at, reservation.ends_at, reservation.lab.timezone)}
      </p>
      <p className="text-sm text-muted-foreground">
        {reservation.lab.name} · {reservation.gpu_count}{" "}
        {reservation.gpu_count === 1 ? "GPU" : "GPUs"}
      </p>
    </li>
  );
}

function StatTile({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children?: ReactNode;
}) {
  return (
    <div className="grid gap-1 rounded-lg border p-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
      {children}
    </div>
  );
}

function AdminCards({ admin, timeZone }: { admin: AdminOverview; timeZone: string }) {
  return (
    <Card className="sm:col-span-2">
      <CardHeader>
        <CardTitle>Lab activity</CardTitle>
        <CardDescription>
          Across every server that is not retired, in the quota week starting{" "}
          {formatInLab(admin.week_start, timeZone)}.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <dl className="grid gap-3 sm:grid-cols-3">
          <StatTile label="Waiting for approval" value={String(admin.pending_approvals)} />
          <StatTile
            label="GPUs in use now"
            value={`${String(admin.gpus_in_use)} of ${String(admin.gpus_total)}`}
          />
          <StatTile label="Booked this week" value={percent(admin.utilization_this_week)} />
        </dl>
        <div className="flex flex-wrap gap-4 text-sm">
          <Link
            to="/admin/approvals"
            className="inline-flex items-center gap-1.5 underline underline-offset-4"
          >
            <ClipboardCheck className="size-4" aria-hidden="true" />
            Review approvals
          </Link>
          <Link
            to="/admin/utilization"
            className="inline-flex items-center gap-1.5 underline underline-offset-4"
          >
            <Gauge className="size-4" aria-hidden="true" />
            Utilization by server
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

function DashboardContent({ overview }: { overview: Overview }) {
  const now = new Date();
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Card>
        <CardHeader className="sr-only">
          <CardTitle>Quota</CardTitle>
        </CardHeader>
        <CardContent>
          <section aria-label="Weekly quota">
            <QuotaSummary quota={overview.quota} />
          </section>
          {overview.my_pending > 0 && (
            <p className="mt-3 text-sm text-muted-foreground">
              {overview.my_pending} {overview.my_pending === 1 ? "request" : "requests"} waiting for
              approval.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Quick links</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="grid gap-2">
            {QUICK_LINKS.map(({ to, label, icon: Icon }) => (
              <li key={to}>
                <Link
                  to={to}
                  className="inline-flex items-center gap-2 text-sm underline-offset-4 hover:underline"
                >
                  <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                  {label}
                </Link>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <Card className="sm:col-span-2">
        <CardHeader>
          <CardTitle>Upcoming reservations</CardTitle>
          <CardDescription>Your next bookings, in each lab&apos;s time zone.</CardDescription>
        </CardHeader>
        <CardContent>
          {overview.upcoming.length === 0 ? (
            <EmptyState
              title="Nothing booked"
              description="Find a time that fits your job, or book from a server's page."
              action={
                <Link to="/find-slot" className="text-sm underline underline-offset-4">
                  Find a slot
                </Link>
              }
            />
          ) : (
            <>
              <ul aria-label="Upcoming reservations">
                {overview.upcoming.map((reservation) => (
                  <UpcomingItem key={reservation.id} reservation={reservation} now={now} />
                ))}
              </ul>
              <Link
                to="/reservations"
                className="mt-2 inline-block text-sm underline underline-offset-4"
              >
                All reservations
              </Link>
            </>
          )}
        </CardContent>
      </Card>

      {overview.admin && <AdminCards admin={overview.admin} timeZone={overview.quota.timezone} />}
    </div>
  );
}

export function DashboardPage() {
  const overview = useQuery({
    queryKey: overviewKey,
    queryFn: ({ signal }) => getOverview(signal),
  });

  let body;
  if (overview.isPending) {
    body = <LoadingState label="Loading your dashboard" />;
  } else if (overview.isError) {
    body = (
      <ErrorState
        title="Could not load your dashboard"
        message={overview.error.message}
        onRetry={() => {
          void overview.refetch();
        }}
      />
    );
  } else {
    body = <DashboardContent overview={overview.data} />;
  }

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Your quota, upcoming reservations, and quick links."
      />
      {body}
    </>
  );
}
