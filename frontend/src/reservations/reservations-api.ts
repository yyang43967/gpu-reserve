import { apiFetch } from "@/lib/api";
import type { Availability, Page, Quota, Reservation, ReservationStatus } from "@/lib/api-types";

export const RESERVATIONS_PAGE_SIZE = 20;
export const PURPOSE_MAX_LENGTH = 500;
export const DECISION_REASON_MAX_LENGTH = 500;
export const SLOT_MINUTES = 30;
/** How far ahead the server page shows availability. */
export const AVAILABILITY_DAYS = 7;
export const BOOKING_HORIZON_DAYS = 14;

export const RESERVATION_STATUS_LABELS: Record<ReservationStatus, string> = {
  pending: "Pending approval",
  approved: "Approved",
  rejected: "Rejected",
  cancelled: "Cancelled",
  expired: "Expired",
  completed: "Completed",
};

export interface CreateReservationInput {
  server_id: string;
  gpu_count: number;
  /** ISO 8601 with an offset, on the hour or half hour. */
  starts_at: string;
  ends_at: string;
  purpose: string;
}

export interface ReservationListParams {
  page: number;
  status: ReservationStatus | null;
  serverId?: string | null;
  /** Admins see everyone's reservations unless they ask for one user's. */
  userId?: string | null;
  /** Newest start first by default; "soonest" for queues. */
  order?: "newest" | "soonest";
}

/** Every reservation query starts with this key, so one invalidation refreshes them all. */
export const reservationsKey = ["reservations"] as const;

export function reservationsListKey(params: ReservationListParams) {
  return [...reservationsKey, "list", params] as const;
}

export function availabilityKey(serverId: string) {
  return [...reservationsKey, "availability", serverId] as const;
}

export const quotaKey = [...reservationsKey, "quota"] as const;

export function listReservations(
  params: ReservationListParams,
  signal?: AbortSignal,
): Promise<Page<Reservation>> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(RESERVATIONS_PAGE_SIZE),
  });
  if (params.status) query.set("status", params.status);
  if (params.serverId) query.set("server_id", params.serverId);
  if (params.userId) query.set("user_id", params.userId);
  if (params.order === "soonest") query.set("order", "starts_at");
  return apiFetch<Page<Reservation>>(`/reservations?${query.toString()}`, { signal });
}

export function createReservation(input: CreateReservationInput): Promise<Reservation> {
  return apiFetch<Reservation>("/reservations", { method: "POST", body: input });
}

function action(id: string, verb: "cancel" | "approve" | "reject", body?: unknown) {
  return apiFetch<Reservation>(`/reservations/${encodeURIComponent(id)}/${verb}`, {
    method: "POST",
    body,
  });
}

/** Cancels, or ends early if the reservation is running. */
export function cancelReservation(id: string): Promise<Reservation> {
  return action(id, "cancel");
}

export function approveReservation(id: string): Promise<Reservation> {
  return action(id, "approve");
}

export function rejectReservation(id: string, reason: string): Promise<Reservation> {
  return action(id, "reject", { reason });
}

export function getAvailability(
  serverId: string,
  from: Date,
  to: Date,
  signal?: AbortSignal,
): Promise<Availability> {
  const query = new URLSearchParams({ from: from.toISOString(), to: to.toISOString() });
  return apiFetch<Availability>(
    `/servers/${encodeURIComponent(serverId)}/availability?${query.toString()}`,
    { signal },
  );
}

export function getQuota(signal?: AbortSignal): Promise<Quota> {
  return apiFetch<Quota>("/me/quota", { signal });
}

/** Whether the reservation can still be cancelled or ended early, judged at `now`. */
export function canCancel(reservation: Reservation, now: Date): boolean {
  if (reservation.status === "pending") return true;
  return reservation.status === "approved" && new Date(reservation.ends_at) > now;
}

/** True when cancelling now would end a running reservation early rather than cancel it. */
export function wouldEndEarly(reservation: Reservation, now: Date): boolean {
  return (
    reservation.status === "approved" &&
    new Date(reservation.starts_at) < now &&
    now < new Date(reservation.ends_at)
  );
}
