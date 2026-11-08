import { describe, expect, it } from "vitest";

import type { ReservationStatus } from "@/lib/api-types";

import { DISPLAY_STATUS_LABELS, describeGpus, displayStatus } from "./reservation-status";

const START = "2030-01-12T17:00:00Z";
const END = "2030-01-12T19:00:00Z";
const at = (iso: string) => new Date(iso);

describe("displayStatus", () => {
  it("calls an approved booking running from its start until its end", () => {
    const booking = { status: "approved" as const, starts_at: START, ends_at: END };

    expect(displayStatus(booking, at("2030-01-12T16:59:59Z"))).toBe("approved");
    expect(displayStatus(booking, at(START))).toBe("running");
    expect(displayStatus(booking, at("2030-01-12T18:59:59Z"))).toBe("running");
    expect(displayStatus(booking, at(END))).toBe("approved");
    expect(DISPLAY_STATUS_LABELS.running).toBe("Running");
  });

  it.each<ReservationStatus>(["pending", "rejected", "cancelled", "expired", "completed"])(
    "keeps a %s booking's own status inside its window",
    (status) => {
      expect(displayStatus({ status, starts_at: START, ends_at: END }, at(START))).toBe(status);
    },
  );
});

describe("describeGpus", () => {
  it.each<ReservationStatus>(["pending", "approved", "completed"])(
    "names the GPUs of a %s booking",
    (status) => {
      expect(describeGpus({ status, gpu_count: 2, gpu_indexes: [0, 3] })).toBe("GPU 0, 3");
    },
  );

  it.each<ReservationStatus>(["rejected", "expired", "cancelled"])(
    "only counts the GPUs of a %s booking, which never used them",
    (status) => {
      expect(describeGpus({ status, gpu_count: 2, gpu_indexes: [0, 3] })).toBe("2 GPUs");
      expect(describeGpus({ status, gpu_count: 1, gpu_indexes: [5] })).toBe("1 GPU");
    },
  );

  it("counts the GPUs when no numbers are known", () => {
    expect(describeGpus({ status: "approved", gpu_count: 1, gpu_indexes: [] })).toBe("1 GPU");
  });
});
