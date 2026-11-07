import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Reservation } from "@/lib/api-types";
import { fakeApi, on, page, problem, signedInAs, type FakeRequest } from "@/test/fake-api";
import { renderRoute } from "@/test/render";

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

const quota = {
  week_start: "2030-01-07T08:00:00Z",
  week_end: "2030-01-14T08:00:00Z",
  timezone: "America/Los_Angeles",
  used_hours: 12.5,
  quota_hours: 48,
  remaining_hours: 35.5,
};

const listRequests = (requests: FakeRequest[]) =>
  requests.filter((r) => r.method === "GET" && r.path === "/reservations");

beforeEach(() => {
  // Only Date is faked, so promises and timers in the UI run as usual.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

function api(list: () => Reservation[], extra: Parameters<typeof fakeApi> = []) {
  return fakeApi(
    ...signedInAs("student"),
    on("GET", "/me/quota", () => Response.json(quota)),
    on("GET", "/reservations", () => Response.json(page(list()))),
    ...extra,
  );
}

describe("my reservations page", () => {
  it("shows each reservation in its lab's time zone", async () => {
    api(() => [
      reservation(),
      reservation({
        id: "r-2",
        status: "pending",
        starts_at: "2030-01-13T00:00:00Z",
        ends_at: "2030-01-13T02:00:00Z",
        server: { id: "s-2", hostname: "h100-tokyo-01", gpu_model: "H100_80GB" },
        lab: { id: "lab-2", name: "Tokyo Lab", timezone: "Asia/Tokyo" },
        gpu_indexes: [3],
      }),
    ]);
    renderRoute("/reservations");

    const first = await screen.findByRole("listitem", { name: /a100-node-01/ });
    expect(within(first).getByText("Sat, Jan 12, 12:00 PST – 14:00 PST")).toBeInTheDocument();
    expect(within(first).getByText("Vision Lab · GPU 0, 1")).toBeInTheDocument();
    expect(within(first).getByText("Approved")).toBeInTheDocument();

    const tokyo = screen.getByRole("listitem", { name: /h100-tokyo-01/ });
    expect(within(tokyo).getByText("Sun, Jan 13, 09:00 GMT+9 – 11:00 GMT+9")).toBeInTheDocument();
    expect(within(tokyo).getByText("Pending approval")).toBeInTheDocument();
  });

  it("shows both dates for a reservation that crosses midnight", async () => {
    api(() => [
      reservation({ starts_at: "2030-01-13T06:00:00Z", ends_at: "2030-01-13T10:00:00Z" }),
    ]);
    renderRoute("/reservations");

    expect(
      await screen.findByText("Sat, Jan 12, 22:00 PST – Sun, Jan 13, 02:00 PST"),
    ).toBeInTheDocument();
  });

  it("shows the weekly quota", async () => {
    api(() => []);
    renderRoute("/reservations");

    const bar = await screen.findByRole("progressbar", { name: "Weekly GPU-hours used" });
    expect(bar).toHaveAttribute("aria-valuenow", "12.5");
    expect(screen.getByText("12.5 of 48 used · 35.5 left")).toBeInTheDocument();
  });

  it("keeps the status filter and page in the URL", async () => {
    const requests = api(() => [reservation()]);
    const { router } = renderRoute("/reservations?status=approved&page=2");

    await screen.findByRole("listitem", { name: /a100-node-01/ });
    expect(screen.getByLabelText("Status")).toHaveValue("approved");
    expect(listRequests(requests)[0]?.query.get("page")).toBe("2");
    expect(listRequests(requests)[0]?.query.get("status")).toBe("approved");

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "pending" } });

    await waitFor(() => {
      expect(router.state.location.search).toBe("?status=pending");
    });
    await waitFor(() => {
      expect(listRequests(requests).at(-1)?.query.get("status")).toBe("pending");
    });
  });

  it("asks for only the signed-in admin's own reservations", async () => {
    const requests = fakeApi(
      ...signedInAs("admin", { id: "admin-1" }),
      on("GET", "/me/quota", () => Response.json(quota)),
      on("GET", "/reservations", () => Response.json(page([]))),
    );
    renderRoute("/reservations");

    await screen.findByText("No reservations yet");
    expect(listRequests(requests)[0]?.query.get("user_id")).toBe("admin-1");
  });

  it("cancels an upcoming reservation after confirmation", async () => {
    let current = reservation();
    const requests = api(
      () => [current],
      [
        on("POST", "/reservations/r-1/cancel", () => {
          current = { ...current, status: "cancelled" };
          return Response.json(current);
        }),
      ],
    );
    renderRoute("/reservations");

    const item = await screen.findByRole("listitem", { name: /a100-node-01/ });
    fireEvent.click(within(item).getByRole("button", { name: "Cancel" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Cancel this reservation?" });
    expect(dialog).toHaveTextContent("2 GPUs on a100-node-01 from Sat, Jan 12, 12:00 PST");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel reservation" }));

    expect(await screen.findByText("Reservation cancelled.")).toBeInTheDocument();
    await waitFor(() => {
      expect(within(item).getByText("Cancelled")).toBeInTheDocument();
    });
    expect(within(item).queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(requests.filter((r) => r.path === "/me/quota").length).toBeGreaterThan(1);
  });

  it("offers to end a running reservation early, naming the new end", async () => {
    let current = reservation({
      starts_at: "2030-01-12T16:00:00Z",
      ends_at: "2030-01-12T19:00:00Z",
    });
    api(
      () => [current],
      [
        on("POST", "/reservations/r-1/cancel", () => {
          current = { ...current, status: "completed", ends_at: "2030-01-12T17:30:00Z" };
          return Response.json(current);
        }),
      ],
    );
    renderRoute("/reservations");

    const item = await screen.findByRole("listitem", { name: /a100-node-01/ });
    fireEvent.click(within(item).getByRole("button", { name: "End early" }));
    const dialog = await screen.findByRole("alertdialog", { name: "End this reservation early?" });
    // Now is 09:10 PST, so the end moves to the next half hour, 09:30.
    expect(dialog).toHaveTextContent(
      "It will end at Sat, Jan 12, 09:30 PST instead of Sat, Jan 12, 11:00 PST",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "End early" }));

    expect(await screen.findByText("Ended early at Sat, Jan 12, 09:30 PST.")).toBeInTheDocument();
  });

  it("recovers when the server refuses a cancel the client allowed", async () => {
    // The client still thinks the reservation is running, but it ended on the server.
    let current = reservation({
      starts_at: "2030-01-12T15:00:00Z",
      ends_at: "2030-01-12T17:30:00Z",
    });
    const requests = api(
      () => [current],
      [
        on("POST", "/reservations/r-1/cancel", () => {
          current = { ...current, status: "completed" };
          return problem(409, "invalid-reservation-state", {
            detail: "A approved reservation that has ended cannot be cancelled.",
          });
        }),
      ],
    );
    renderRoute("/reservations");

    const item = await screen.findByRole("listitem", { name: /a100-node-01/ });
    const listsBefore = listRequests(requests).length;
    fireEvent.click(within(item).getByRole("button", { name: "End early" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "End early" }));

    expect(await within(item).findByRole("alert")).toHaveTextContent(
      "A approved reservation that has ended cannot be cancelled.",
    );
    // The list is fetched again and shows the server's view.
    await waitFor(() => {
      expect(listRequests(requests).length).toBeGreaterThan(listsBefore);
    });
    expect(await within(item).findByText("Completed")).toBeInTheDocument();
    expect(within(item).queryByRole("button", { name: "End early" })).toBeNull();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("offers nothing to cancel for finished reservations", async () => {
    api(() => [
      reservation({ status: "rejected", decision_reason: "Class booking" }),
      reservation({
        id: "r-2",
        starts_at: "2030-01-12T10:00:00Z",
        ends_at: "2030-01-12T12:00:00Z",
        server: { id: "s-2", hostname: "past-node", gpu_model: "L40S" },
      }),
    ]);
    renderRoute("/reservations");

    const rejected = await screen.findByRole("listitem", { name: /a100-node-01/ });
    expect(within(rejected).getByText("Reason: Class booking")).toBeInTheDocument();
    expect(within(rejected).queryByRole("button", { name: /Cancel|End early/ })).toBeNull();
    // Approved but already over by the client's clock.
    const past = screen.getByRole("listitem", { name: /past-node/ });
    expect(within(past).queryByRole("button", { name: /Cancel|End early/ })).toBeNull();
    // A calendar file is still available for both.
    expect(within(past).getByRole("button", { name: "Add to calendar" })).toBeInTheDocument();
  });

  it("shows empty, filtered-empty, and error states", async () => {
    let fail = true;
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/me/quota", () => Response.json(quota)),
      on("GET", "/reservations", () =>
        fail
          ? problem(500, "internal-error", { detail: "Database is down." })
          : Response.json(page([])),
      ),
    );
    renderRoute("/reservations?status=pending");

    expect(await screen.findByText("Could not load reservations")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("No reservations match this filter")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(await screen.findByText("No reservations yet")).toBeInTheDocument();
  });

  it("downloads a reservation as a calendar file", async () => {
    const createObjectURL = vi.fn(() => "blob:calendar");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL }));
    const clicked: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this.download);
    });
    const requests = api(
      () => [reservation()],
      [
        on(
          "GET",
          "/reservations/r-1.ics",
          () =>
            new Response("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", {
              headers: { "Content-Type": "text/calendar; charset=utf-8" },
            }),
        ),
      ],
    );
    renderRoute("/reservations");

    const item = await screen.findByRole("listitem", { name: /a100-node-01/ });
    fireEvent.click(within(item).getByRole("button", { name: "Add to calendar" }));

    await waitFor(() => {
      expect(clicked).toEqual(["reservation-r-1.ics"]);
    });
    const download = requests.find((r) => r.path === "/reservations/r-1.ics");
    expect(download?.headers.get("Accept")).toBe("text/calendar");
    expect(download?.headers.get("Authorization")).toBe("Bearer access-token");
    expect(createObjectURL).toHaveBeenCalledOnce();
    click.mockRestore();
  });

  it("explains a failed calendar download on the reservation", async () => {
    api(
      () => [reservation()],
      [on("GET", "/reservations/r-1.ics", () => problem(404, "reservation-not-found"))],
    );
    renderRoute("/reservations");

    const item = await screen.findByRole("listitem", { name: /a100-node-01/ });
    fireEvent.click(within(item).getByRole("button", { name: "Add to calendar" }));

    expect(await within(item).findByRole("alert")).toHaveTextContent(
      "Could not download the calendar file.",
    );
  });
});
