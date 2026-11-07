import type { Availability } from "@/lib/api-types";
import {
  labDateKey,
  labLocalToUtc,
  labTimeOfDay,
  offsetMinutes,
  zoneAbbreviation,
} from "@/lib/lab-time";

const MINUTE_MS = 60_000;

export interface TimelineSlot {
  start: Date;
  end: Date;
  free: number;
}

/** A daylight saving change inside a day. */
export interface ClockChange {
  /** Index of the first slot after the change. */
  slotIndex: number;
  kind: "back" | "forward";
  /** Abbreviations before and after, such as PDT and PST. */
  before: string;
  after: string;
  /** The wall-clock hour that repeats (back) or is skipped (forward): "01:00–02:00". */
  hours: string;
}

export interface TimelineDay {
  /** The lab-local date, "2030-01-12". */
  key: string;
  /** Empty cells before the first slot, so partial days line up with full ones. */
  leadingSlots: number;
  /**
   * The slots the API returned for this local day. A full day has 48, but 46 on a
   * spring-forward day and 50 on a fall-back day: never assume 48.
   */
  slots: TimelineSlot[];
  /**
   * Set on daylight saving days. Slots are drawn in time order, so after the change the
   * row no longer lines up with the hour axis; the change is marked instead.
   */
  clockChange: ClockChange | null;
}

/** Expand the API's runs into slots and group them by lab-local date. */
export function buildTimelineDays(availability: Availability, timeZone: string): TimelineDay[] {
  const slotMs = availability.slot_minutes * MINUTE_MS;
  const days: TimelineDay[] = [];
  for (const run of availability.runs) {
    const end = new Date(run.ends_at).getTime();
    for (let start = new Date(run.starts_at).getTime(); start < end; start += slotMs) {
      const slot = { start: new Date(start), end: new Date(start + slotMs), free: run.free };
      const key = labDateKey(slot.start, timeZone);
      let day = days.at(-1);
      if (day?.key !== key) {
        day = {
          key,
          leadingSlots: leadingSlots(key, slot.start, timeZone, slotMs),
          slots: [],
          clockChange: null,
        };
        days.push(day);
      }
      day.slots.push(slot);
    }
  }
  for (const day of days) day.clockChange = findClockChange(day.slots, timeZone);
  return days;
}

function minutesToClock(total: number): string {
  const minutes = ((total % 1440) + 1440) % 1440;
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function clockToMinutes(clock: string): number {
  const [hours, minutes] = clock.split(":").map(Number);
  return (hours ?? 0) * 60 + (minutes ?? 0);
}

function findClockChange(slots: TimelineSlot[], timeZone: string): ClockChange | null {
  for (let i = 1; i < slots.length; i++) {
    const previous = slots[i - 1];
    const current = slots[i];
    if (!previous || !current) continue;
    const shift = offsetMinutes(current.start, timeZone) - offsetMinutes(previous.start, timeZone);
    if (shift === 0) continue;
    const after = clockToMinutes(labTimeOfDay(current.start, timeZone));
    // Back: the wall clock jumps back by |shift|, so [after, after + |shift|) repeats.
    // Forward: the wall clock skips [after - shift, after).
    const [from, to] = shift < 0 ? [after, after - shift] : [after - shift, after];
    return {
      slotIndex: i,
      kind: shift < 0 ? "back" : "forward",
      before: zoneAbbreviation(previous.start, timeZone),
      after: zoneAbbreviation(current.start, timeZone),
      hours: `${minutesToClock(from)}–${minutesToClock(to)}`,
    };
  }
  return null;
}

/** "Clocks go back: 01:00–02:00 happens twice (PDT, then PST)." */
export function describeClockChange(change: ClockChange): string {
  return change.kind === "back"
    ? `Clocks go back: ${change.hours} happens twice (${change.before}, then ${change.after}).`
    : `Clocks go forward: ${change.hours} is skipped (${change.before} to ${change.after}).`;
}

function leadingSlots(key: string, firstStart: Date, timeZone: string, slotMs: number): number {
  const midnight = labLocalToUtc(key, "00:00", timeZone);
  if (!midnight.ok) return 0;
  return Math.max(0, Math.round((firstStart.getTime() - midnight.utc.getTime()) / slotMs));
}

/**
 * A sentence per day for screen readers: "All 4 GPUs free", "Fully booked", or the
 * stretches where fewer than all GPUs are free.
 */
export function describeDay(day: TimelineDay, gpuCount: number, timeZone: string): string {
  const runs: { start: Date; end: Date; free: number }[] = [];
  for (const slot of day.slots) {
    const last = runs.at(-1);
    if (last?.free === slot.free) last.end = slot.end;
    else runs.push({ ...slot });
  }
  const busy = runs
    .filter((run) => run.free < gpuCount)
    .map(
      (run) =>
        `${labTimeOfDay(run.start, timeZone)}–${labTimeOfDay(run.end, timeZone)}: ` +
        `${String(run.free)} of ${String(gpuCount)} free`,
    );
  const summary =
    runs.length === 1 && runs[0]?.free === gpuCount
      ? `All ${String(gpuCount)} GPUs free`
      : runs.length === 1 && runs[0]?.free === 0
        ? "Fully booked"
        : `${busy.join("; ")}; all free otherwise`;
  return day.clockChange ? `${summary}. ${describeClockChange(day.clockChange)}` : summary;
}
