import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Reservation } from "@/lib/api-types";
import { fakeApi, makeOverview, on, problem, signedInAs } from "@/test/fake-api";
import { renderRoute } from "@/test/render";

/** The item at `index` (negative counts from the end); fails the test if it is missing. */
function at<T>(items: readonly T[], index: number): T {
  const item = items.at(index);
  if (item === undefined) throw new Error(`No item at ${String(index)}`);
  return item;
}

// Saturday 12 January 2030, 09:10 in Los Angeles.
const NOW = new Date("2030-01-12T17:10:00Z");

function reservation(overrides: Partial<Reservation> = {}): Reservation {
  return {
    id: "r-1",
    status: "approved",
    gpu_count: 2,
    starts_at: "2030-01-12T20:00:00Z",
    ends_at: "2030-01-12T22:00:00Z",
    purpose: "Fine-tuning",
    created_via: "web",
    decided_at: null,
    decision_reason: null,
    created_at: "2030-01-07T18:00:00Z",
    server: { id: "s-1", hostname: "a100-node-01", gpu_model: "A100_80GB" },
    lab: { id: "lab-1", name: "Vision Lab", timezone: "America/Los_Angeles" },
    user: { id: "user-1", full_name: "Ada Lovelace", email: "ada@example.edu" },
    gpu_indexes: [0, 1],
    ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("dashboard", () => {
  it("shows a student's quota, next bookings, and pending requests", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/stats/overview", () =>
        Response.json(
          makeOverview({
            quota: { ...makeOverview().quota, used_hours: 12.5, remaining_hours: 35.5 },
            upcoming: [
              reservation({
                id: "r-running",
                starts_at: "2030-01-12T17:00:00Z",
                ends_at: "2030-01-12T19:00:00Z",
              }),
              reservation({
                id: "r-pending",
                status: "pending",
                server: { id: "s-2", hostname: "h100-node-01", gpu_model: "H100_80GB" },
                gpu_count: 1,
              }),
            ],
            my_pending: 1,
          }),
        ),
      ),
    );
    renderRoute("/");

    expect(await screen.findByText("12.5 of 48 used · 35.5 left")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Weekly GPU-hours used" })).toHaveAttribute(
      "aria-valuenow",
      "12.5",
    );
    expect(screen.getByText("1 request waiting for approval.")).toBeInTheDocument();
    const upcoming = screen.getByRole("list", { name: "Upcoming reservations" });
    const items = within(upcoming).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    const [running, pending] = [at(items, 0), at(items, 1)];
    expect(within(running).getByText("Running")).toBeInTheDocument();
    expect(within(running).getByText(/Sat, Jan 12, 09:00 PST – 11:00 PST/)).toBeInTheDocument();
    expect(within(pending).getByText("Pending approval")).toBeInTheDocument();
    expect(within(pending).getByText("Vision Lab · 1 GPU")).toBeInTheDocument();
    expect(screen.queryByText("Lab activity")).toBeNull();
  });

  it("does not call a pending booking running after its start", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/stats/overview", () =>
        Response.json(
          makeOverview({
            upcoming: [
              reservation({
                status: "pending",
                starts_at: "2030-01-12T17:00:00Z",
                ends_at: "2030-01-12T19:00:00Z",
              }),
            ],
          }),
        ),
      ),
    );
    renderRoute("/");

    const upcoming = await screen.findByRole("list", { name: "Upcoming reservations" });
    expect(within(upcoming).getByText("Pending approval")).toBeInTheDocument();
    expect(within(upcoming).queryByText("Running")).toBeNull();
  });

  it("points to slot search when nothing is booked", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/stats/overview", () => Response.json(makeOverview())),
    );
    renderRoute("/");

    expect(await screen.findByText("Nothing booked")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Find a slot" }).length).toBeGreaterThan(0);
    expect(screen.queryByText(/waiting for approval/)).toBeNull();
  });

  it("shows lab activity to admins", async () => {
    fakeApi(
      ...signedInAs("admin"),
      on("GET", "/stats/overview", () =>
        Response.json(
          makeOverview({
            admin: {
              pending_approvals: 3,
              gpus_in_use: 5,
              gpus_total: 24,
              week_start: "2030-01-07T08:00:00Z",
              week_end: "2030-01-14T08:00:00Z",
              utilization_this_week: 0.1234,
            },
          }),
        ),
      ),
    );
    renderRoute("/");

    expect(await screen.findByText("Lab activity")).toBeInTheDocument();
    expect(screen.getByText(/quota week starting Mon, Jan 7, 00:00 PST/)).toBeInTheDocument();
    const tile = (label: string) => screen.getByText(label).nextElementSibling?.textContent;
    expect(tile("Waiting for approval")).toBe("3");
    expect(tile("GPUs in use now")).toBe("5 of 24");
    expect(tile("Booked this week")).toBe("12.3%");
    expect(screen.getByRole("link", { name: "Review approvals" })).toHaveAttribute(
      "href",
      "/admin/approvals",
    );
    expect(screen.getByRole("link", { name: "Utilization by server" })).toHaveAttribute(
      "href",
      "/admin/utilization",
    );
  });

  it("shows an error with a retry", async () => {
    let calls = 0;
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/stats/overview", () => {
        calls += 1;
        return calls === 1 ? problem(500, "internal-error") : Response.json(makeOverview());
      }),
    );
    renderRoute("/");

    expect(await screen.findByText("Could not load your dashboard")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Nothing booked")).toBeInTheDocument();
  });
});
