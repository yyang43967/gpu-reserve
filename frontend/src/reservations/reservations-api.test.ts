import { describe, expect, it } from "vitest";

import type { Reservation } from "@/lib/api-types";
import {
  canCancel,
  getAvailability,
  listReservations,
  rejectReservation,
  wouldEndEarly,
} from "@/reservations/reservations-api";
import { fakeApi, on, page } from "@/test/fake-api";

function reservation(overrides: Partial<Reservation> = {}): Reservation {
  return {
    id: "r-1",
    status: "approved",
    gpu_count: 1,
    starts_at: "2030-01-12T16:00:00Z",
    ends_at: "2030-01-12T18:00:00Z",
    purpose: "Training",
    created_via: "web",
    decided_at: null,
    decision_reason: null,
    created_at: "2030-01-07T18:00:00Z",
    server: { id: "s-1", hostname: "a100-node-01", gpu_model: "A100_80GB" },
    lab: { id: "lab-1", name: "Vision Lab", timezone: "America/Los_Angeles" },
    user: { id: "u-1", full_name: "Ada", email: "ada@example.edu" },
    gpu_indexes: [0],
    ...overrides,
  };
}

describe("cancel rules", () => {
  const before = new Date("2030-01-12T15:00:00Z");
  const during = new Date("2030-01-12T17:00:00Z");
  const after = new Date("2030-01-12T18:00:00Z");

  it("allows cancelling until an approved reservation ends", () => {
    expect(canCancel(reservation(), before)).toBe(true);
    expect(canCancel(reservation(), during)).toBe(true);
    expect(canCancel(reservation(), after)).toBe(false);
  });

  it("allows cancelling pending reservations at any time", () => {
    expect(canCancel(reservation({ status: "pending" }), after)).toBe(true);
  });

  it("never allows cancelling a finished status", () => {
    for (const status of ["rejected", "cancelled", "expired", "completed"] as const) {
      expect(canCancel(reservation({ status }), before)).toBe(false);
    }
  });

  it("ends early only while an approved reservation is running", () => {
    expect(wouldEndEarly(reservation(), before)).toBe(false);
    expect(wouldEndEarly(reservation(), new Date("2030-01-12T16:00:00Z"))).toBe(false);
    expect(wouldEndEarly(reservation(), during)).toBe(true);
    expect(wouldEndEarly(reservation({ status: "pending" }), during)).toBe(false);
  });
});

describe("requests", () => {
  it("sends list filters and the availability window", async () => {
    const requests = fakeApi(
      on("GET", "/reservations", () => Response.json(page([]))),
      on("GET", "/servers/s-1/availability", () => Response.json({ runs: [] })),
    );

    await listReservations({ page: 2, status: "pending", serverId: "s-1" });
    await getAvailability(
      "s-1",
      new Date("2030-01-12T00:00:00Z"),
      new Date("2030-01-19T00:00:00Z"),
    );

    expect(Object.fromEntries(requests[0]?.query ?? [])).toEqual({
      page: "2",
      page_size: "20",
      status: "pending",
      server_id: "s-1",
    });
    expect(Object.fromEntries(requests[1]?.query ?? [])).toEqual({
      from: "2030-01-12T00:00:00.000Z",
      to: "2030-01-19T00:00:00.000Z",
    });
  });

  it("sends the rejection reason", async () => {
    const requests = fakeApi(
      on("POST", "/reservations/r-1/reject", () => Response.json(reservation())),
    );

    await rejectReservation("r-1", "Booked for a class");

    expect(requests[0]?.body).toEqual({ reason: "Booked for a class" });
  });
});
