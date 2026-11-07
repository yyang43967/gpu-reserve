import { describe, expect, it } from "vitest";

import type { Availability, FreeRun } from "@/lib/api-types";
import { buildTimelineDays, describeDay } from "@/reservations/timeline";

const LA = "America/Los_Angeles";

function availability(runs: FreeRun[], gpuCount = 4): Availability {
  return {
    server_id: "s-1",
    timezone: LA,
    gpu_count: gpuCount,
    slot_minutes: 30,
    starts_at: runs[0]?.starts_at ?? "",
    ends_at: runs.at(-1)?.ends_at ?? "",
    runs,
  };
}

/** One free run covering a whole lab-local day, from local midnight to local midnight. */
function wholeDay(midnightUtc: string, nextMidnightUtc: string): Availability {
  return availability([{ starts_at: midnightUtc, ends_at: nextMidnightUtc, free: 4 }]);
}

describe("buildTimelineDays", () => {
  it("gives an ordinary day 48 slots", () => {
    const [day] = buildTimelineDays(wholeDay("2030-01-12T08:00:00Z", "2030-01-13T08:00:00Z"), LA);
    expect(day?.key).toBe("2030-01-12");
    expect(day?.slots).toHaveLength(48);
    expect(day?.leadingSlots).toBe(0);
  });

  it("gives the spring-forward day 46 slots", () => {
    // Sunday 10 March 2030: midnight is PST (08:00 UTC), the next midnight PDT (07:00 UTC).
    const days = buildTimelineDays(wholeDay("2030-03-10T08:00:00Z", "2030-03-11T07:00:00Z"), LA);
    expect(days.map((d) => [d.key, d.slots.length])).toEqual([["2030-03-10", 46]]);
  });

  it("gives the fall-back day 50 slots", () => {
    // Sunday 3 November 2030: midnight is PDT (07:00 UTC), the next midnight PST (08:00 UTC).
    const days = buildTimelineDays(wholeDay("2030-11-03T07:00:00Z", "2030-11-04T08:00:00Z"), LA);
    expect(days.map((d) => [d.key, d.slots.length])).toEqual([["2030-11-03", 50]]);
    // 01:00 and 01:30 appear twice, in order.
    const second = days[0]?.slots[4];
    expect(second?.start.toISOString()).toBe("2030-11-03T09:00:00.000Z");
  });

  it("splits runs at local midnight and pads a partial first day", () => {
    const days = buildTimelineDays(
      availability([
        { starts_at: "2030-01-12T20:00:00Z", ends_at: "2030-01-13T09:00:00Z", free: 2 },
        { starts_at: "2030-01-13T09:00:00Z", ends_at: "2030-01-13T10:00:00Z", free: 0 },
      ]),
      LA,
    );

    // 12:00 to midnight on the 12th, then midnight to 02:00 on the 13th.
    expect(days.map((d) => [d.key, d.leadingSlots, d.slots.length])).toEqual([
      ["2030-01-12", 24, 24],
      ["2030-01-13", 0, 4],
    ]);
    expect(days[1]?.slots.map((s) => s.free)).toEqual([2, 2, 0, 0]);
  });
});

describe("describeDay", () => {
  it("summarizes free, booked, and mixed days", () => {
    const [free] = buildTimelineDays(wholeDay("2030-01-12T08:00:00Z", "2030-01-13T08:00:00Z"), LA);
    expect(free && describeDay(free, 4, LA)).toBe("All 4 GPUs free");

    const [mixed] = buildTimelineDays(
      availability([
        { starts_at: "2030-01-12T16:00:00Z", ends_at: "2030-01-12T18:00:00Z", free: 1 },
        { starts_at: "2030-01-12T18:00:00Z", ends_at: "2030-01-12T19:00:00Z", free: 4 },
      ]),
      LA,
    );
    expect(mixed && describeDay(mixed, 4, LA)).toBe("08:00–10:00: 1 of 4 free; all free otherwise");
  });
});
