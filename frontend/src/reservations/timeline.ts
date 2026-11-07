import type { Availability } from "@/lib/api-types";
import { labDateKey, labLocalToUtc, labTimeOfDay } from "@/lib/lab-time";

const MINUTE_MS = 60_000;

export interface TimelineSlot {
  start: Date;
  end: Date;
  free: number;
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
        day = { key, leadingSlots: leadingSlots(key, slot.start, timeZone, slotMs), slots: [] };
        days.push(day);
      }
      day.slots.push(slot);
    }
  }
  return days;
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
  if (runs.length === 1 && runs[0]?.free === gpuCount) return `All ${String(gpuCount)} GPUs free`;
  if (runs.length === 1 && runs[0]?.free === 0) return "Fully booked";
  const busy = runs
    .filter((run) => run.free < gpuCount)
    .map(
      (run) =>
        `${labTimeOfDay(run.start, timeZone)}–${labTimeOfDay(run.end, timeZone)}: ` +
        `${String(run.free)} of ${String(gpuCount)} free`,
    );
  return `${busy.join("; ")}; all free otherwise`;
}
