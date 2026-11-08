import type { Reservation, ReservationStatus } from "@/lib/api-types";

import { RESERVATION_STATUS_LABELS } from "./reservations-api";

/** A reservation's stored status, or "running" for an approved one that is in progress. */
export type DisplayStatus = ReservationStatus | "running";

export function displayStatus(
  reservation: Pick<Reservation, "status" | "starts_at" | "ends_at">,
  now: Date,
): DisplayStatus {
  if (
    reservation.status === "approved" &&
    new Date(reservation.starts_at) <= now &&
    now < new Date(reservation.ends_at)
  ) {
    return "running";
  }
  return reservation.status;
}

export const DISPLAY_STATUS_LABELS: Record<DisplayStatus, string> = {
  ...RESERVATION_STATUS_LABELS,
  running: "Running",
};

export const DISPLAY_STATUS_VARIANTS: Record<
  DisplayStatus,
  "default" | "secondary" | "outline" | "destructive"
> = {
  pending: "secondary",
  approved: "default",
  running: "default",
  rejected: "destructive",
  cancelled: "outline",
  expired: "outline",
  completed: "outline",
};

/**
 * Statuses whose GPUs were never in use: the request was refused, ran out of time
 * while pending, or was cancelled before its start (a running booking that is
 * stopped becomes completed instead). Their GPU numbers would only mislead.
 */
const NEVER_USED: ReadonlySet<ReservationStatus> = new Set(["rejected", "expired", "cancelled"]);

/** "GPU 0, 1" when the numbers mean something, otherwise "2 GPUs". */
export function describeGpus(
  reservation: Pick<Reservation, "status" | "gpu_count" | "gpu_indexes">,
): string {
  if (reservation.gpu_indexes.length && !NEVER_USED.has(reservation.status)) {
    return `GPU ${reservation.gpu_indexes.join(", ")}`;
  }
  return `${String(reservation.gpu_count)} ${reservation.gpu_count === 1 ? "GPU" : "GPUs"}`;
}
