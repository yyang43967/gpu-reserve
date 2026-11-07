import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { useAuth } from "@/auth/auth-context";
import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import type { Reservation, ReservationStatus } from "@/lib/api-types";
import { describeError } from "@/lib/form-errors";
import { formatInLab, formatLabRange } from "@/lib/lab-time";
import { useLatestSearchParams } from "@/lib/use-search-params";
import { earlyEnd } from "@/reservations/booking";
import {
  canCancel,
  cancelReservation,
  getQuota,
  listReservations,
  quotaKey,
  RESERVATION_STATUS_LABELS,
  reservationsKey,
  reservationsListKey,
  wouldEndEarly,
} from "@/reservations/reservations-api";

const STATUSES = Object.keys(RESERVATION_STATUS_LABELS) as ReservationStatus[];

const STATUS_VARIANTS: Record<
  ReservationStatus,
  "default" | "secondary" | "outline" | "destructive"
> = {
  pending: "secondary",
  approved: "default",
  rejected: "destructive",
  cancelled: "outline",
  expired: "outline",
  completed: "outline",
};

interface ListParams {
  page: number;
  status: ReservationStatus | null;
}

function readParams(searchParams: URLSearchParams): ListParams {
  const page = Number.parseInt(searchParams.get("page") ?? "", 10);
  const status = searchParams.get("status");
  return {
    page: Number.isInteger(page) && page > 0 ? page : 1,
    status: STATUSES.find((s) => s === status) ?? null,
  };
}

function writeParams(params: ListParams): URLSearchParams {
  const query = new URLSearchParams();
  if (params.page > 1) query.set("page", String(params.page));
  if (params.status) query.set("status", params.status);
  return query;
}

/** Status filter and page live in the URL, so reload, back, and shared links keep them. */
function useListParams() {
  const [searchParams, updateSearchParams] = useLatestSearchParams();
  const params = readParams(searchParams);
  const update = (changes: Partial<ListParams>, { push = false } = {}) => {
    updateSearchParams((current) => writeParams({ ...readParams(current), page: 1, ...changes }), {
      replace: !push,
    });
  };
  return [params, update] as const;
}

function QuotaBar() {
  const quota = useQuery({ queryKey: quotaKey, queryFn: ({ signal }) => getQuota(signal) });

  if (quota.isPending) return <LoadingState label="Loading quota" />;
  if (quota.isError) {
    return (
      <ErrorState
        title="Could not load your quota"
        message={quota.error.message}
        onRetry={() => {
          void quota.refetch();
        }}
      />
    );
  }
  const { used_hours: used, quota_hours: total, remaining_hours: remaining } = quota.data;
  const percent = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 100;
  const weekStart = formatInLab(quota.data.week_start, quota.data.timezone);
  return (
    <section aria-labelledby="quota-heading" className="mb-6 grid gap-2 rounded-xl border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="quota-heading" className="font-medium">
          GPU-hours this week
        </h2>
        <p className="text-sm text-muted-foreground tabular-nums">
          {used} of {total} used · {remaining} left
        </p>
      </div>
      <div
        role="progressbar"
        aria-label="Weekly GPU-hours used"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={used}
        className="h-2 overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full rounded-full bg-primary" style={{ width: `${String(percent)}%` }} />
      </div>
      <p className="text-xs text-muted-foreground">Week starting {weekStart}.</p>
    </section>
  );
}

function CancelDialog({
  reservation,
  onOpenChange,
  onFinished,
}: {
  reservation: Reservation | null;
  onOpenChange: (open: boolean) => void;
  /** Called with the outcome; on failure, the error message. */
  onFinished: (reservation: Reservation, error: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  // Read once when the dialog opens; the confirmation names the exact new end.
  const [now] = useState(() => new Date());
  if (!reservation) return <AlertDialog open={false} onOpenChange={onOpenChange} />;

  const zone = reservation.lab.timezone;
  const endingEarly = wouldEndEarly(reservation, now);
  const newEnd = earlyEnd(new Date(reservation.ends_at), now);

  const confirm = async () => {
    setBusy(true);
    try {
      onFinished(await cancelReservation(reservation.id), null);
    } catch (error) {
      onFinished(reservation, describeError(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AlertDialog open onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {endingEarly ? "End this reservation early?" : "Cancel this reservation?"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {endingEarly
              ? `It will end at ${formatInLab(newEnd, zone)} instead of ${formatInLab(reservation.ends_at, zone)}, and the rest of the time is released.`
              : `${String(reservation.gpu_count)} GPUs on ${reservation.server.hostname} from ${formatInLab(reservation.starts_at, zone)} are released. This cannot be undone.`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Keep it</AlertDialogCancel>
          <Button variant="destructive" disabled={busy} onClick={() => void confirm()}>
            {busy ? "Working…" : endingEarly ? "End early" : "Cancel reservation"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ReservationItem({
  reservation,
  now,
  error,
  onCancel,
}: {
  reservation: Reservation;
  now: Date;
  error: string | null;
  onCancel: () => void;
}) {
  const zone = reservation.lab.timezone;
  const cancellable = canCancel(reservation, now);
  const gpus = reservation.gpu_indexes.length
    ? `GPU ${reservation.gpu_indexes.join(", ")}`
    : `${String(reservation.gpu_count)} GPUs`;

  return (
    <li
      className="grid gap-2 rounded-xl border p-4"
      aria-label={`${reservation.server.hostname}, ${formatInLab(reservation.starts_at, zone)}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="grid gap-0.5">
          <Link
            to={`/servers/${reservation.server.id}`}
            className="font-medium underline-offset-4 hover:underline"
          >
            {reservation.server.hostname}
          </Link>
          <p className="text-sm text-muted-foreground">
            {reservation.lab.name} · {gpus}
          </p>
        </div>
        <Badge variant={STATUS_VARIANTS[reservation.status]}>
          {RESERVATION_STATUS_LABELS[reservation.status]}
        </Badge>
      </div>
      <p className="text-sm tabular-nums">
        {formatLabRange(reservation.starts_at, reservation.ends_at, zone)}
      </p>
      <p className="text-sm [overflow-wrap:anywhere] text-muted-foreground">
        {reservation.purpose}
      </p>
      {reservation.status === "rejected" && reservation.decision_reason && (
        <p className="text-sm">Reason: {reservation.decision_reason}</p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {cancellable && (
        <div>
          <Button variant="outline" size="sm" onClick={onCancel}>
            {wouldEndEarly(reservation, now) ? "End early" : "Cancel"}
          </Button>
        </div>
      )}
    </li>
  );
}

export function MyReservationsPage() {
  const auth = useAuth();
  const userId = auth.status === "authenticated" ? auth.user.id : null;
  const queryClient = useQueryClient();
  const [params, updateParams] = useListParams();
  const [cancelling, setCancelling] = useState<Reservation | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);

  // Admins can see everyone's reservations; this page is always one's own.
  const query = { ...params, userId };
  const reservations = useQuery({
    queryKey: reservationsListKey(query),
    queryFn: ({ signal }) => listReservations(query, signal),
    placeholderData: keepPreviousData,
  });
  const now = new Date();

  const finished = (reservation: Reservation, error: string | null) => {
    setCancelling(null);
    setErrors((current) => {
      const next = Object.fromEntries(
        Object.entries(current).filter(([id]) => id !== reservation.id),
      );
      return error ? { ...next, [reservation.id]: error } : next;
    });
    setNotice(
      error
        ? null
        : reservation.status === "completed"
          ? `Ended early at ${formatInLab(reservation.ends_at, reservation.lab.timezone)}.`
          : "Reservation cancelled.",
    );
    // On success the GPUs are free again; on failure the reservation changed under us
    // (it started, ended, or was cancelled elsewhere). Either way, reload what is shown.
    void queryClient.invalidateQueries({ queryKey: reservationsKey });
  };

  let body;
  if (reservations.isPending) {
    body = <LoadingState label="Loading reservations" />;
  } else if (reservations.isError) {
    body = (
      <ErrorState
        title="Could not load reservations"
        message={reservations.error.message}
        onRetry={() => {
          void reservations.refetch();
        }}
      />
    );
  } else if (reservations.data.items.length === 0) {
    body = params.status ? (
      <EmptyState
        title="No reservations match this filter"
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              updateParams({ status: null });
            }}
          >
            Show all
          </Button>
        }
      />
    ) : (
      <EmptyState
        title="No reservations yet"
        description="Book GPUs from a server's page."
        action={
          <Link to="/servers" className="text-sm underline underline-offset-4">
            Browse servers
          </Link>
        }
      />
    );
  } else {
    body = (
      <>
        <ul aria-label="Reservations" aria-busy={reservations.isFetching} className="grid gap-3">
          {reservations.data.items.map((reservation) => (
            <ReservationItem
              key={reservation.id}
              reservation={reservation}
              now={now}
              error={errors[reservation.id] ?? null}
              onCancel={() => {
                setNotice(null);
                setCancelling(reservation);
              }}
            />
          ))}
        </ul>
        <Pagination
          data={reservations.data}
          onPage={(page) => {
            updateParams({ page }, { push: true });
          }}
        />
      </>
    );
  }

  return (
    <>
      <PageHeader title="My reservations" description="Upcoming and past reservations." />
      <QuotaBar />
      <div className="mb-4 grid max-w-xs gap-2">
        <Label htmlFor="status-filter">Status</Label>
        <NativeSelect
          id="status-filter"
          value={params.status ?? ""}
          onChange={(event) => {
            updateParams({ status: STATUSES.find((s) => s === event.target.value) ?? null });
          }}
        >
          <option value="">All statuses</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {RESERVATION_STATUS_LABELS[status]}
            </option>
          ))}
        </NativeSelect>
      </div>
      <p role="status" className="mb-2 text-sm text-muted-foreground empty:hidden">
        {notice}
      </p>
      {body}
      <CancelDialog
        key={cancelling?.id ?? "closed"}
        reservation={cancelling}
        onOpenChange={(open) => {
          if (!open) setCancelling(null);
        }}
        onFinished={finished}
      />
    </>
  );
}
