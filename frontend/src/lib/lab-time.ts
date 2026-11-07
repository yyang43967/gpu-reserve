/**
 * Times in a lab's time zone, using only Intl.
 *
 * The API stores and returns UTC instants. People pick and read times in the lab's zone,
 * so the booking form converts wall-clock times to UTC and every time shown carries the
 * zone abbreviation.
 */

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;
/** Bookings sit on a 30-minute grid in UTC; lab zones must have offsets on that grid. */
const OFFSET_STEP_MINUTES = 30;

interface WallTime {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
}

const partFormatters = new Map<string, Intl.DateTimeFormat>();

function partsFormatter(timeZone: string): Intl.DateTimeFormat {
  let formatter = partFormatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    partFormatters.set(timeZone, formatter);
  }
  return formatter;
}

function wallTimeAt(utcMs: number, timeZone: string): WallTime {
  const values: Record<string, number> = {};
  for (const part of partsFormatter(timeZone).formatToParts(new Date(utcMs))) {
    if (part.type !== "literal") values[part.type] = Number(part.value);
  }
  return {
    year: values.year ?? 0,
    month: values.month ?? 0,
    day: values.day ?? 0,
    hour: values.hour ?? 0,
    minute: values.minute ?? 0,
  };
}

/** A wall time written as if it were UTC, to compare and subtract wall times. */
function wallAsUtcMs(wall: WallTime): number {
  return Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute);
}

/** The zone's offset from UTC at an instant, in minutes (Los Angeles in winter: -480). */
export function offsetMinutes(at: Date, timeZone: string): number {
  const utcMs = Math.floor(at.getTime() / MINUTE_MS) * MINUTE_MS;
  return (wallAsUtcMs(wallTimeAt(utcMs, timeZone)) - utcMs) / MINUTE_MS;
}

export type LocalToUtc =
  { ok: true; utc: Date; ambiguous: boolean } | { ok: false; error: "invalid" | "nonexistent" };

const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIME_RE = /^(\d{2}):(\d{2})$/;

/**
 * Convert a wall-clock date ("2030-03-10") and time ("02:30") in `timeZone` to UTC.
 *
 * - A time skipped by a daylight saving change (the spring-forward gap) does not exist:
 *   `{ ok: false, error: "nonexistent" }`.
 * - A time that happens twice (the fall-back hour) resolves to its first occurrence,
 *   with `ambiguous: true` so the form can show which one was chosen.
 */
export function labLocalToUtc(date: string, time: string, timeZone: string): LocalToUtc {
  const d = DATE_RE.exec(date);
  const t = TIME_RE.exec(time);
  if (!d || !t) return { ok: false, error: "invalid" };
  const wall: WallTime = {
    year: Number(d[1]),
    month: Number(d[2]),
    day: Number(d[3]),
    hour: Number(t[1]),
    minute: Number(t[2]),
  };
  const wallMs = wallAsUtcMs(wall);
  const check = new Date(wallMs);
  if (
    check.getUTCFullYear() !== wall.year ||
    check.getUTCMonth() !== wall.month - 1 ||
    check.getUTCDate() !== wall.day ||
    wall.hour > 23 ||
    wall.minute > 59
  ) {
    return { ok: false, error: "invalid" };
  }

  // The offsets a day before and after cover any single daylight saving change. Each
  // candidate is kept only if it really shows this wall time in the zone.
  const offsets = new Set([
    offsetMinutes(new Date(wallMs - DAY_MS), timeZone),
    offsetMinutes(new Date(wallMs + DAY_MS), timeZone),
  ]);
  const matches = [...offsets]
    .map((offset) => wallMs - offset * MINUTE_MS)
    .filter((utcMs) => wallAsUtcMs(wallTimeAt(utcMs, timeZone)) === wallMs)
    .sort((a, b) => a - b);

  const first = matches[0];
  if (first === undefined) return { ok: false, error: "nonexistent" };
  return { ok: true, utc: new Date(first), ambiguous: matches.length > 1 };
}

/** "2030-01-12": the calendar date of an instant in the zone. */
export function labDateKey(at: Date, timeZone: string): string {
  const wall = wallTimeAt(at.getTime(), timeZone);
  return `${String(wall.year)}-${pad(wall.month)}-${pad(wall.day)}`;
}

/** "08:30": the wall-clock time of an instant in the zone, 24-hour. */
export function labTimeOfDay(at: Date, timeZone: string): string {
  const wall = wallTimeAt(at.getTime(), timeZone);
  return `${pad(wall.hour)}:${pad(wall.minute)}`;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** "PST", "PDT", or "GMT+9" where the zone has no common abbreviation. */
export function zoneAbbreviation(at: Date, timeZone: string): string {
  const part = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "short" })
    .formatToParts(at)
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? timeZone;
}

const dateTimeFormatters = new Map<string, Intl.DateTimeFormat>();

/** "Sat, Jan 12, 08:00 PST": an instant as people in the lab read it. */
export function formatInLab(at: Date | string, timeZone: string): string {
  let formatter = dateTimeFormatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone,
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZoneName: "short",
    });
    dateTimeFormatters.set(timeZone, formatter);
  }
  return formatter.format(typeof at === "string" ? new Date(at) : at);
}

/** "Sat, Jan 12": a lab-local date key as a short heading. */
export function formatDateKey(key: string): string {
  const [year, month, day] = key.split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    weekday: "short",
    month: "short",
    day: "numeric",
  }).format(new Date(Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1)));
}

/**
 * Whether every offset the zone uses (checked on 1 January and 1 July of this year and
 * next) is a whole number of half hours. The API applies the same rule and is the final
 * authority; this only keeps unsupported zones out of the picker.
 */
export function hasHalfHourOffsets(timeZone: string, now: Date = new Date()): boolean {
  const year = now.getUTCFullYear();
  try {
    return [year, year + 1].every((y) =>
      [0, 6].every(
        (month) =>
          offsetMinutes(new Date(Date.UTC(y, month, 1)), timeZone) % OFFSET_STEP_MINUTES === 0,
      ),
    );
  } catch {
    return false;
  }
}

/** "2030-01-13" from "2030-01-12" and 1. Calendar arithmetic, independent of zones. */
export function addDaysToKey(key: string, days: number): string {
  const [year, month, day] = key.split("-").map(Number);
  const date = new Date(Date.UTC(year ?? 0, (month ?? 1) - 1, (day ?? 1) + days));
  return date.toISOString().slice(0, 10);
}

/** "Sat, Jan 12, 08:00 PST – 10:00 PST", or both dates when it crosses midnight. */
export function formatLabRange(startsAt: string, endsAt: string, timeZone: string): string {
  const start = new Date(startsAt);
  const end = new Date(endsAt);
  const endText =
    labDateKey(start, timeZone) === labDateKey(end, timeZone)
      ? `${labTimeOfDay(end, timeZone)} ${zoneAbbreviation(end, timeZone)}`
      : formatInLab(end, timeZone);
  return `${formatInLab(start, timeZone)} – ${endText}`;
}
