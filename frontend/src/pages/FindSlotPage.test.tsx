import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Reservation, Slot } from "@/lib/api-types";
import { fakeApi, makeLab, on, page, problem, signedInAs, type FakeRequest } from "@/test/fake-api";
import { initialZone } from "@/search/search-api";
import { renderRoute } from "@/test/render";

/** The item at `index` (negative counts from the end); fails the test if it is missing. */
function at<T>(items: readonly T[], index: number): T {
  const item = items.at(index);
  if (item === undefined) throw new Error(`No item at ${String(index)}`);
  return item;
}

// Monday 7 January 2030, 10:00 in Los Angeles.
const NOW = new Date("2030-01-07T18:00:00Z");

const labs = [
  makeLab({ id: "lab-1", name: "Vision Lab", timezone: "America/Los_Angeles" }),
  makeLab({ id: "lab-2", name: "NLP Lab", timezone: "Europe/Berlin" }),
];

function slot(overrides: Partial<Slot> = {}): Slot {
  return {
    server: {
      id: "s-1",
      hostname: "a100-node-01",
      gpu_model: "A100_80GB",
      gpu_memory_gb: 80,
      gpu_count: 8,
      requires_approval: false,
      max_booking_hours: 12,
    },
    lab: { id: "lab-1", name: "Vision Lab", timezone: "America/Los_Angeles" },
    starts_at: "2030-01-12T16:00:00Z",
    ends_at: "2030-01-12T19:00:00Z",
    free_gpus: 6,
    ...overrides,
  };
}

function created(body: Record<string, unknown>): Reservation {
  return {
    id: "r-1",
    status: "approved",
    gpu_count: body.gpu_count as number,
    starts_at: body.starts_at as string,
    ends_at: body.ends_at as string,
    purpose: body.purpose as string,
    created_via: "web",
    decided_at: null,
    decision_reason: null,
    created_at: "2030-01-07T18:00:00Z",
    server: { id: "s-1", hostname: "a100-node-01", gpu_model: "A100_80GB" },
    lab: { id: "lab-1", name: "Vision Lab", timezone: "America/Los_Angeles" },
    user: { id: "user-1", full_name: "Ada Lovelace", email: "ada@example.edu" },
    gpu_indexes: [0, 1],
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  // A browser zone no lab uses, so the form starts in the first lab's zone whatever
  // zone the test machine is in.
  const options = new Intl.DateTimeFormat().resolvedOptions();
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
    ...options,
    timeZone: "Asia/Tokyo",
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function api(search: (request: FakeRequest) => Response) {
  return fakeApi(
    ...signedInAs("student"),
    on("GET", "/labs", () => Response.json(page(labs))),
    on("POST", "/search/slots", search),
    on("GET", "/me/quota", () =>
      Response.json({
        week_start: "2030-01-07T08:00:00Z",
        week_end: "2030-01-14T08:00:00Z",
        timezone: "America/Los_Angeles",
        used_hours: 0,
        quota_hours: 48,
        remaining_hours: 48,
      }),
    ),
    on("POST", "/reservations", (request) =>
      Response.json(created(request.body as Record<string, unknown>), { status: 201 }),
    ),
  );
}

async function form() {
  return screen.findByRole("form", { name: "Search for a slot" });
}

function set(scope: HTMLElement, label: string, value: string) {
  fireEvent.change(within(scope).getByLabelText(label), { target: { value } });
}

function fillWindow(scope: HTMLElement) {
  set(scope, "From date", "2030-01-12");
  set(scope, "From time", "08:00");
  set(scope, "To date", "2030-01-12");
  set(scope, "To time", "20:00");
}

const searches = (requests: FakeRequest[]) =>
  requests.filter((r) => r.method === "POST" && r.path === "/search/slots");

describe("find a slot", () => {
  it.each([
    [["America/Los_Angeles", "Europe/Berlin"], "Europe/Berlin", "Europe/Berlin"],
    [["America/Los_Angeles", "Europe/Berlin"], "Asia/Tokyo", "America/Los_Angeles"],
    [[], "Asia/Tokyo", "America/Los_Angeles"],
  ])("starts in %j with browser zone %s: %s", (zones, browser, expected) => {
    expect(initialZone(zones, browser)).toBe(expected);
  });

  it("defaults the window to the next week", async () => {
    api(() => Response.json({ items: [] }));
    renderRoute("/find-slot");
    const search = await form();

    expect(within(search).getByLabelText("From date")).toHaveValue("2030-01-07");
    expect(within(search).getByLabelText("From time")).toHaveValue("10:30");
    expect(within(search).getByLabelText("To date")).toHaveValue("2030-01-14");
    expect(within(search).getByLabelText("Time zone")).toHaveValue("America/Los_Angeles");
    const zones = within(within(search).getByLabelText("Time zone")).getAllByRole("option");
    expect(zones.map((o) => o.textContent)).toEqual(["America/Los_Angeles", "Europe/Berlin"]);
  });

  it("searches with the window in the chosen zone and lists ranked results", async () => {
    const requests = api(() =>
      Response.json({
        items: [
          slot(),
          slot({
            server: {
              ...slot().server,
              id: "s-2",
              hostname: "h100-node-01",
              requires_approval: true,
            },
            starts_at: "2030-01-12T17:00:00Z",
            ends_at: "2030-01-12T20:00:00Z",
          }),
        ],
      }),
    );
    renderRoute("/find-slot");
    const search = await form();

    set(search, "GPUs", "2");
    set(search, "Hours", "3");
    set(search, "GPU model", "A100_80GB");
    set(search, "Memory per GPU", "80");
    fillWindow(search);
    fireEvent.click(within(search).getByRole("button", { name: "Search" }));

    const results = await screen.findByRole("list", { name: "Matching slots" });
    expect(searches(requests)[0]?.body).toEqual({
      gpu_count: 2,
      duration_minutes: 180,
      earliest: "2030-01-12T16:00:00.000Z",
      latest: "2030-01-13T04:00:00.000Z",
      gpu_model: "A100_80GB",
      min_gpu_memory_gb: 80,
    });
    const items = within(results).getAllByRole("listitem");
    const first = at(items, 0);
    const second = at(items, 1);
    expect(within(first).getByText(/Sat, Jan 12, 08:00 PST – 11:00 PST/)).toBeInTheDocument();
    expect(within(first).getByText(/6 of 8 GPUs free/)).toBeInTheDocument();
    expect(within(first).queryByText("Needs approval")).toBeNull();
    expect(within(second).getByText("Needs approval")).toBeInTheDocument();
  });

  it("reads the window in another lab's zone when it is chosen", async () => {
    const requests = api(() => Response.json({ items: [] }));
    renderRoute("/find-slot");
    const search = await form();

    fillWindow(search);
    set(search, "Time zone", "Europe/Berlin");
    fireEvent.click(within(search).getByRole("button", { name: "Search" }));

    await screen.findByText("Nothing fits");
    expect(searches(requests)[0]?.body).toMatchObject({
      earliest: "2030-01-12T07:00:00.000Z",
      latest: "2030-01-12T19:00:00.000Z",
      gpu_model: null,
      min_gpu_memory_gb: null,
    });
  });

  it("books a result through the dialog, prefilled", async () => {
    const requests = api(() => Response.json({ items: [slot()] }));
    renderRoute("/find-slot");
    const search = await form();
    set(search, "GPUs", "2");
    set(search, "Hours", "3");
    fillWindow(search);
    fireEvent.click(within(search).getByRole("button", { name: "Search" }));
    fireEvent.click(await screen.findByRole("button", { name: "Book" }));

    const dialog = await screen.findByRole("dialog", { name: "Book a100-node-01" });
    expect(within(dialog).getByLabelText("Start date")).toHaveValue("2030-01-12");
    expect(within(dialog).getByLabelText("Start time")).toHaveValue("08:00");
    expect(within(dialog).getByLabelText("End time")).toHaveValue("11:00");
    expect(within(dialog).getByLabelText("GPUs")).toHaveValue(2);
    set(dialog, "Purpose", "Evaluation run");
    fireEvent.click(within(dialog).getByRole("button", { name: "Book" }));

    expect(
      await screen.findByText(/Booked a100-node-01 from Sat, Jan 12, 08:00 PST/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See my reservations" })).toBeInTheDocument();
    const posted = requests.filter((r) => r.method === "POST" && r.path === "/reservations");
    expect(posted[0]?.body).toEqual({
      server_id: "s-1",
      gpu_count: 2,
      starts_at: "2030-01-12T16:00:00.000Z",
      ends_at: "2030-01-12T19:00:00.000Z",
      purpose: "Evaluation run",
    });
    // Booking refreshes the results, which may no longer include the booked time.
    await waitFor(() => {
      expect(searches(requests)).toHaveLength(2);
    });
  });

  it("checks the window before searching", async () => {
    const requests = api(() => Response.json({ items: [] }));
    renderRoute("/find-slot");
    const search = await form();

    set(search, "Hours", "4");
    set(search, "From date", "2030-01-12");
    set(search, "From time", "08:00");
    set(search, "To date", "2030-01-12");
    set(search, "To time", "10:00");
    fireEvent.click(within(search).getByRole("button", { name: "Search" }));
    expect(
      await within(search).findByText("The window must be at least as long as the job."),
    ).toBeInTheDocument();

    set(search, "To date", "2030-01-30");
    fireEvent.click(within(search).getByRole("button", { name: "Search" }));
    expect(await within(search).findByText("Search at most 14 days at once.")).toBeInTheDocument();

    set(search, "Hours", "2.25");
    fireEvent.click(within(search).getByRole("button", { name: "Search" }));
    expect(
      await within(search).findByText("Use whole or half hours, such as 2 or 2.5."),
    ).toBeInTheDocument();
    expect(searches(requests)).toHaveLength(0);
  });

  it("explains why it cannot search when the user holds too many reservations", async () => {
    api(() =>
      problem(409, "too-many-reservations", {
        detail: "You already hold 4 upcoming or running reservations, the most allowed.",
      }),
    );
    renderRoute("/find-slot");
    const search = await form();
    fillWindow(search);
    fireEvent.click(within(search).getByRole("button", { name: "Search" }));

    expect(await screen.findByText("No search possible")).toBeInTheDocument();
    expect(screen.getByText(/already hold 4 upcoming or running/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("offers a retry when labs cannot be loaded", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/labs", () => problem(500, "internal-error")),
    );
    renderRoute("/find-slot");

    expect(await screen.findByText("Could not load labs")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
