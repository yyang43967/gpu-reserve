import { useEffect, useRef } from "react";

import { formatDateKey, formatInLab } from "@/lib/lab-time";
import { cn } from "@/lib/utils";
import {
  buildTimelineDays,
  describeClockChange,
  describeDay,
  type TimelineSlot,
} from "@/reservations/timeline";
import type { Availability } from "@/lib/api-types";

// Green, amber, and slate stay distinguishable in light and dark themes, and the legend
// and screen-reader summaries carry the same information as text.
const FREE_CLASS = "bg-emerald-500/70";
const SOME_CLASS = "bg-amber-400";
const BOOKED_CLASS = "bg-slate-500 dark:bg-slate-400";

function cellClass(free: number, gpuCount: number): string {
  if (free === 0) return BOOKED_CLASS;
  if (free < gpuCount) return SOME_CLASS;
  return FREE_CLASS;
}

// One cell is w-2.5 (10px) plus a 1px gap.
const CELL_PX = 11;
const SLOTS_PER_HOUR = 2;
const AXIS_HOURS = [0, 6, 12, 18] as const;

function slotLabel(slot: TimelineSlot, gpuCount: number, timeZone: string): string {
  return `${formatInLab(slot.start, timeZone)}: ${String(slot.free)} of ${String(gpuCount)} GPUs free`;
}

/**
 * Free GPUs per half hour, one row per lab-local day. Each row has exactly the slots the
 * API returned for that day, so daylight saving days have 46 or 50 cells. Rows scroll
 * sideways inside the card on narrow screens.
 */
export function AvailabilityTimeline({
  availability,
  now,
  onPickSlot,
}: {
  availability: Availability;
  now: Date;
  /** Called with the start of a free slot the person clicked; omit to disable picking. */
  onPickSlot?: (start: Date) => void;
}) {
  const { timezone, gpu_count: gpuCount } = availability;
  const days = buildTimelineDays(availability, timezone);
  const firstLeading = days[0]?.leadingSlots ?? 0;
  const scroller = useRef<HTMLDivElement>(null);

  // Rows line up by time of day, so today's remaining hours can start far to the right.
  // Start scrolled to them, an hour early, so a narrow screen does not open on blanks.
  useEffect(() => {
    if (scroller.current) {
      scroller.current.scrollLeft = Math.max(0, (firstLeading - SLOTS_PER_HOUR) * CELL_PX);
    }
  }, [firstLeading]);

  return (
    <div className="grid gap-3">
      <div ref={scroller} className="overflow-x-auto pb-1" data-testid="timeline-scroller">
        <div className="flex gap-2 pb-1" aria-hidden="true">
          <span className="w-24 shrink-0" />
          <div className="relative h-4" style={{ width: 48 * CELL_PX }}>
            {AXIS_HOURS.map((hour) => (
              <span
                key={hour}
                className="absolute text-[10px] text-muted-foreground tabular-nums"
                style={{ left: hour * SLOTS_PER_HOUR * CELL_PX }}
              >
                {String(hour).padStart(2, "0")}:00
              </span>
            ))}
          </div>
        </div>
        <ul className="grid w-max gap-1.5" aria-label="Free GPUs by day">
          {days.map((day) => (
            <li key={day.key} className="flex items-start gap-2" data-testid="timeline-day">
              <span className="sticky left-0 z-10 flex h-6 w-24 shrink-0 items-center bg-card text-xs font-medium text-muted-foreground">
                {formatDateKey(day.key)}
              </span>
              <span className="sr-only">{describeDay(day, gpuCount, timezone)}</span>
              <div className="grid gap-0.5">
                <div className="flex gap-px" aria-hidden={onPickSlot ? undefined : true}>
                  {Array.from({ length: day.leadingSlots }, (_, i) => (
                    <span key={`pad-${String(i)}`} className="h-6 w-2.5" />
                  ))}
                  {day.slots.map((slot, index) => {
                    const label = slotLabel(slot, gpuCount, timezone);
                    const change = day.clockChange;
                    const divider =
                      change?.slotIndex === index ? (
                        <span
                          key={`change-${String(index)}`}
                          data-testid="clock-change"
                          title={describeClockChange(change)}
                          className="mx-0.5 h-6 w-0.5 rounded-full bg-foreground"
                        />
                      ) : null;
                    const className = cn("h-6 w-2.5 rounded-[2px]", cellClass(slot.free, gpuCount));
                    const pickable = onPickSlot && slot.free > 0 && slot.start > now;
                    const cell = pickable ? (
                      <button
                        key={slot.start.toISOString()}
                        type="button"
                        title={label}
                        aria-label={`Book from ${label}`}
                        data-testid="timeline-slot"
                        className={cn(
                          className,
                          "outline-none hover:ring-2 hover:ring-ring focus-visible:ring-2 focus-visible:ring-ring",
                        )}
                        onClick={() => {
                          onPickSlot(slot.start);
                        }}
                      />
                    ) : (
                      <span
                        key={slot.start.toISOString()}
                        title={label}
                        aria-hidden="true"
                        data-testid="timeline-slot"
                        className={className}
                      />
                    );
                    return divider ? [divider, cell] : cell;
                  })}
                </div>
                {day.clockChange && (
                  <p
                    className="w-0 min-w-full text-[10px] text-muted-foreground"
                    style={{ paddingLeft: day.leadingSlots * CELL_PX }}
                    aria-hidden="true"
                  >
                    {describeClockChange(day.clockChange)} This row is{" "}
                    {day.clockChange.kind === "back" ? "an hour longer" : "an hour shorter"} than
                    the hour scale.
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <li className="flex items-center gap-1.5">
          <span className={cn("size-3 rounded-[2px]", FREE_CLASS)} aria-hidden="true" />
          All free
        </li>
        <li className="flex items-center gap-1.5">
          <span className={cn("size-3 rounded-[2px]", SOME_CLASS)} aria-hidden="true" />
          Some free
        </li>
        <li className="flex items-center gap-1.5">
          <span className={cn("size-3 rounded-[2px]", BOOKED_CLASS)} aria-hidden="true" />
          Fully booked
        </li>
        <li>
          Times in {timezone}.{onPickSlot ? " Select a free half hour to book from it." : ""}
        </li>
      </ul>
    </div>
  );
}
