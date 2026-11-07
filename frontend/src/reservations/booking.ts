import { ApiError } from "@/lib/api";
import { describeError } from "@/lib/form-errors";
import { addDaysToKey, labLocalToUtc, zoneAbbreviation } from "@/lib/lab-time";

const SLOT_MS = 30 * 60_000;

/** The next half hour from now: the earliest start the API accepts. */
export function nextBookableStart(now: Date): Date {
  return new Date(Math.floor(now.getTime() / SLOT_MS) * SLOT_MS + SLOT_MS);
}

/** The first half-hour boundary at or after `at`: where an early end takes effect. */
export function ceilToHalfHour(at: Date): Date {
  return new Date(Math.ceil(at.getTime() / SLOT_MS) * SLOT_MS);
}

/** The end a running reservation gets if it is ended early at `now`. */
export function earlyEnd(endsAt: Date, now: Date): Date {
  const boundary = ceilToHalfHour(now);
  return boundary < endsAt ? boundary : endsAt;
}

export type BookingField =
  "start_date" | "start_time" | "end_date" | "end_time" | "gpu_count" | "purpose";

/** Half-hour wall times, "00:00" to "23:30". Lab zones have offsets on this grid. */
export const HALF_HOURS: readonly string[] = Array.from({ length: 48 }, (_, i) => {
  const hour = String(Math.floor(i / 2)).padStart(2, "0");
  return `${hour}:${i % 2 === 0 ? "00" : "30"}`;
});

/**
 * The end date implied by the start and the chosen end time. Unless the person picked
 * the end date themselves, it follows the start date, moving to the next day when the
 * end time is at or before the start time (an overnight booking, or ending at 00:00).
 * A picked end date only moves when it equals the start date and the end time is not
 * after the start time.
 */
export function impliedEndDate(input: {
  startDate: string;
  startTime: string;
  endDate: string;
  endTime: string;
  endDatePicked: boolean;
}): string {
  const base = input.endDatePicked ? input.endDate : input.startDate;
  if (base === input.startDate && input.endTime <= input.startTime) {
    return addDaysToKey(input.startDate, 1);
  }
  return base;
}

export const NONEXISTENT_TIME = "Clocks skip this time on that day. Pick another time.";

/** A note for a wall time that happens twice, saying which one is booked. */
export function ambiguityNote(date: string, time: string, timeZone: string): string | undefined {
  const result = labLocalToUtc(date, time, timeZone);
  if (!result.ok || !result.ambiguous) return undefined;
  return `${time} happens twice that day; this is the first one (${zoneAbbreviation(result.utc, timeZone)}).`;
}

// Where each problem type is shown in the booking form. Types not listed here, and
// unknown ones, appear above the form with the API's detail text.
const FIELD_FOR_TYPE: Record<string, BookingField> = {
  "invalid-time-grid": "start_time",
  "start-in-past": "start_time",
  "start-too-far": "start_date",
  "invalid-time-range": "end_time",
  "booking-too-short": "end_time",
  "booking-too-long": "end_time",
  "too-many-gpus": "gpu_count",
  "peak-hours-limit": "gpu_count",
  "gpu-unavailable": "gpu_count",
};

// Request body fields named in 422 validation errors.
const FIELD_FOR_API_FIELD: Record<string, BookingField> = {
  starts_at: "start_time",
  ends_at: "end_time",
  gpu_count: "gpu_count",
  purpose: "purpose",
};

export interface PlacedError {
  fields: { field: BookingField; message: string }[];
  /** For the alert above the form, or null when every message has a field. */
  form: string | null;
}

/** Decide where a failed booking's error messages go. */
export function placeBookingError(error: unknown): PlacedError {
  if (!(error instanceof ApiError)) return { fields: [], form: describeError(error) };
  const message = error.detail ?? error.title ?? error.message;
  const typed = error.code ? FIELD_FOR_TYPE[error.code] : undefined;
  if (typed) return { fields: [{ field: typed, message }], form: null };

  const fields: PlacedError["fields"] = [];
  const leftovers: string[] = [];
  for (const fieldError of error.fieldErrors) {
    const field = FIELD_FOR_API_FIELD[fieldError.field];
    if (field) fields.push({ field, message: fieldError.message });
    else leftovers.push(fieldError.message);
  }
  if (error.fieldErrors.length > 0) {
    return { fields, form: leftovers.length > 0 ? leftovers.join(" ") : null };
  }
  return { fields: [], form: message };
}
