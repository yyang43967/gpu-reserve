import { zodResolver } from "@hookform/resolvers/zod";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router";
import { z } from "zod";

import { FormAlert } from "@/components/form-alert";
import { TextareaField } from "@/components/form-field";
import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/api";
import type { Reservation } from "@/lib/api-types";
import { applyServerErrors, describeError } from "@/lib/form-errors";
import { formatInLab, formatLabRange } from "@/lib/lab-time";
import { useLatestSearchParams } from "@/lib/use-search-params";
import {
  approveReservation,
  DECISION_REASON_MAX_LENGTH,
  listReservations,
  rejectReservation,
  reservationsKey,
  reservationsListKey,
} from "@/reservations/reservations-api";

const rejectSchema = z.object({
  reason: z
    .string()
    .trim()
    .min(1, { error: "Give a reason; the student sees it." })
    .max(DECISION_REASON_MAX_LENGTH, {
      error: `Use at most ${String(DECISION_REASON_MAX_LENGTH)} characters.`,
    }),
});

type RejectValues = z.infer<typeof rejectSchema>;

function RejectDialog({
  reservation,
  onOpenChange,
  onRejected,
  onConflict,
}: {
  reservation: Reservation | null;
  onOpenChange: (open: boolean) => void;
  onRejected: (reservation: Reservation) => void;
  /** Someone else decided first; the queue shows the error on the row. */
  onConflict: (reservation: Reservation, message: string) => void;
}) {
  return (
    <Dialog open={reservation !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Reject this request?</DialogTitle>
          <DialogDescription>
            {reservation
              ? `${reservation.user.full_name}'s ${String(reservation.gpu_count)} GPUs on ${reservation.server.hostname} are released.`
              : ""}
          </DialogDescription>
        </DialogHeader>
        {reservation && (
          <RejectForm reservation={reservation} onRejected={onRejected} onConflict={onConflict} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function RejectForm({
  reservation,
  onRejected,
  onConflict,
}: {
  reservation: Reservation;
  onRejected: (reservation: Reservation) => void;
  onConflict: (reservation: Reservation, message: string) => void;
}) {
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<RejectValues>({ resolver: zodResolver(rejectSchema), defaultValues: { reason: "" } });

  const submit = handleSubmit(async ({ reason }) => {
    setFormError(null);
    try {
      onRejected(await rejectReservation(reservation.id, reason));
    } catch (error) {
      if (isConflict(error)) {
        onConflict(reservation, describeError(error));
        return;
      }
      setFormError(applyServerErrors(error, setError, ["reason"]));
    }
  });

  return (
    <form noValidate onSubmit={(event) => void submit(event)} className="grid gap-4">
      <FormAlert message={formError} />
      <TextareaField
        label="Reason"
        rows={3}
        description="Shown to the student."
        error={errors.reason?.message}
        {...register("reason")}
      />
      <DialogFooter>
        <Button type="submit" variant="destructive" disabled={isSubmitting}>
          {isSubmitting ? "Rejecting…" : "Reject"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

function PendingItem({
  reservation,
  now,
  error,
  busy,
  onApprove,
  onReject,
}: {
  reservation: Reservation;
  now: Date;
  error: string | null;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const zone = reservation.lab.timezone;
  const started = new Date(reservation.starts_at) <= now;
  const explanationId = `started-${reservation.id}`;

  return (
    <li
      className="grid gap-2 rounded-xl border p-4"
      aria-label={`${reservation.user.full_name}, ${reservation.server.hostname}`}
    >
      <div className="grid gap-0.5">
        <p className="font-medium">{reservation.user.full_name}</p>
        <p className="text-sm [overflow-wrap:anywhere] text-muted-foreground">
          {reservation.user.email}
        </p>
        <p className="text-sm text-muted-foreground">
          {reservation.server.hostname} · {reservation.lab.name} · {reservation.gpu_count}{" "}
          {reservation.gpu_count === 1 ? "GPU" : "GPUs"}
        </p>
      </div>
      <p className="text-sm tabular-nums">
        {formatLabRange(reservation.starts_at, reservation.ends_at, zone)}
      </p>
      <p className="text-sm [overflow-wrap:anywhere]">{reservation.purpose}</p>
      {started && (
        <p id={explanationId} className="text-sm text-muted-foreground">
          Its start time has passed, so it can no longer be approved. Reject it to release the GPUs.
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={started || busy}
          aria-describedby={started ? explanationId : undefined}
          onClick={onApprove}
        >
          Approve
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={onReject}>
          Reject
        </Button>
      </div>
    </li>
  );
}

function usePage() {
  const [searchParams, updateSearchParams] = useLatestSearchParams();
  const raw = Number.parseInt(searchParams.get("page") ?? "", 10);
  const page = Number.isInteger(raw) && raw > 0 ? raw : 1;
  const setPage = (next: number) => {
    updateSearchParams(() => new URLSearchParams(next > 1 ? { page: String(next) } : {}));
  };
  return [page, setPage] as const;
}

export function AdminApprovalsPage() {
  const queryClient = useQueryClient();
  const [page, setPage] = usePage();
  const [rejecting, setRejecting] = useState<Reservation | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // Conflicts by reservation id, with whose request it was.
  const [errors, setErrors] = useState<Record<string, { name: string; message: string }>>({});
  const [notice, setNotice] = useState<string | null>(null);

  const params = { page, status: "pending" as const, order: "soonest" as const };
  const queue = useQuery({
    queryKey: reservationsListKey(params),
    queryFn: ({ signal }) => listReservations(params, signal),
    placeholderData: keepPreviousData,
  });
  const now = new Date();

  const setRowError = (reservation: Reservation, message: string | null) => {
    setErrors((current) => {
      const next = Object.fromEntries(
        Object.entries(current).filter(([key]) => key !== reservation.id),
      );
      return message
        ? { ...next, [reservation.id]: { name: reservation.user.full_name, message } }
        : next;
    });
  };
  // A request another admin already decided drops out of the refreshed queue, taking
  // its row with it; its error then shows above the list instead.
  const shownIds = new Set(queue.data?.items.map((r) => r.id) ?? []);
  const orphaned = Object.entries(errors)
    .filter(([id]) => queue.data && !shownIds.has(id))
    .map(([, error]) => `${error.name}'s request: ${error.message}`);

  /** After any decision, or a conflict, reload every reservation view. */
  const refresh = () => queryClient.invalidateQueries({ queryKey: reservationsKey });

  const approve = async (reservation: Reservation) => {
    setNotice(null);
    setBusyId(reservation.id);
    try {
      await approveReservation(reservation.id);
      setRowError(reservation, null);
      setNotice(
        `Approved ${reservation.user.full_name}'s booking from ${formatInLab(reservation.starts_at, reservation.lab.timezone)}.`,
      );
    } catch (error) {
      // 409: another admin decided first, or it started. Show why, then reload.
      setRowError(reservation, describeError(error));
    } finally {
      setBusyId(null);
      void refresh();
    }
  };

  let body;
  if (queue.isPending) {
    body = <LoadingState label="Loading requests" />;
  } else if (queue.isError) {
    body = (
      <ErrorState
        title="Could not load requests"
        message={queue.error.message}
        onRetry={() => {
          void queue.refetch();
        }}
      />
    );
  } else if (queue.data.items.length === 0) {
    body = (
      <EmptyState
        title="Nothing to review"
        description="New requests on restricted servers appear here."
      />
    );
  } else {
    body = (
      <>
        <ul aria-label="Pending requests" aria-busy={queue.isFetching} className="grid gap-3">
          {queue.data.items.map((reservation) => (
            <PendingItem
              key={reservation.id}
              reservation={reservation}
              now={now}
              error={errors[reservation.id]?.message ?? null}
              busy={busyId === reservation.id}
              onApprove={() => void approve(reservation)}
              onReject={() => {
                setNotice(null);
                setRejecting(reservation);
              }}
            />
          ))}
        </ul>
        <Pagination data={queue.data} onPage={setPage} />
      </>
    );
  }

  return (
    <>
      <Link
        to="/admin"
        className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronLeft className="size-4" aria-hidden="true" />
        Admin
      </Link>
      <PageHeader
        title="Approvals"
        description="Requests on servers that need approval, soonest start first."
      />
      <p role="status" className="mb-2 text-sm text-muted-foreground empty:hidden">
        {notice}
      </p>
      {orphaned.length > 0 && (
        <div className="mb-4">
          <FormAlert message={orphaned.join(" ")} />
        </div>
      )}
      {body}
      <RejectDialog
        reservation={rejecting}
        onOpenChange={(open) => {
          if (!open) setRejecting(null);
        }}
        onRejected={(reservation) => {
          setRejecting(null);
          setRowError(reservation, null);
          setNotice(`Rejected ${reservation.user.full_name}'s booking.`);
          void refresh();
        }}
        onConflict={(reservation, message) => {
          setRejecting(null);
          setRowError(reservation, message);
          void refresh();
        }}
      />
    </>
  );
}
