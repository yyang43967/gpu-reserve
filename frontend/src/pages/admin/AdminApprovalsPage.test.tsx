import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Reservation } from "@/lib/api-types";
import { fakeApi, on, page, problem, signedInAs, type FakeRequest } from "@/test/fake-api";
import { renderRoute } from "@/test/render";

// Saturday 12 January 2030, 09:10 in Los Angeles.
const NOW = new Date("2030-01-12T17:10:00Z");

function pending(overrides: Partial<Reservation> = {}): Reservation {
  return {
    id: "r-1",
    status: "pending",
    gpu_count: 4,
    starts_at: "2030-01-13T17:00:00Z",
    ends_at: "2030-01-13T19:00:00Z",
    purpose: "Pretraining a 1B model",
    created_via: "web",
    decided_at: null,
    decision_reason: null,
    created_at: "2030-01-07T18:00:00Z",
    server: { id: "s-1", hostname: "h100-node-01", gpu_model: "H100_80GB" },
    lab: { id: "lab-1", name: "NLP Lab", timezone: "America/Los_Angeles" },
    user: { id: "u-1", full_name: "Ada Lovelace", email: "ada@example.edu" },
    gpu_indexes: [0, 1, 2, 3],
    ...overrides,
  };
}

const later = pending({
  id: "r-2",
  starts_at: "2030-01-14T01:00:00Z",
  ends_at: "2030-01-14T03:00:00Z",
  user: { id: "u-2", full_name: "Alan Turing", email: "alan@example.edu" },
  server: { id: "s-2", hostname: "h100-tokyo-01", gpu_model: "H100_80GB" },
  lab: { id: "lab-2", name: "Tokyo Lab", timezone: "Asia/Tokyo" },
  gpu_count: 1,
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

/** A queue that keeps only pending reservations, like the API's filter. */
function queueApi(initial: Reservation[], extra: Parameters<typeof fakeApi> = []) {
  const items = [...initial];
  const requests = fakeApi(
    ...signedInAs("admin"),
    ...extra,
    on("GET", "/reservations", () =>
      Response.json(page(items.filter((r) => r.status === "pending"))),
    ),
    on("POST", "/reservations/r-1/approve", () => decide("r-1", "approved")),
    on("POST", "/reservations/r-1/reject", (request) => {
      const { reason } = request.body as { reason: string };
      return decide("r-1", "rejected", reason);
    }),
  );
  function decide(id: string, status: Reservation["status"], reason: string | null = null) {
    const index = items.findIndex((r) => r.id === id);
    const current = items[index];
    if (!current) return problem(404, "reservation-not-found");
    const updated = { ...current, status, decision_reason: reason };
    items[index] = updated;
    return Response.json(updated);
  }
  return { requests, items };
}

const queueRequests = (requests: FakeRequest[]) =>
  requests.filter((r) => r.method === "GET" && r.path === "/reservations");

describe("approval queue", () => {
  it("asks for pending reservations, soonest first, and shows each in its lab's zone", async () => {
    const { requests } = queueApi([pending(), later]);
    renderRoute("/admin/approvals");

    const first = await screen.findByRole("listitem", { name: "Ada Lovelace, h100-node-01" });
    expect(within(first).getByText("ada@example.edu")).toBeInTheDocument();
    expect(within(first).getByText("h100-node-01 · NLP Lab · 4 GPUs")).toBeInTheDocument();
    expect(within(first).getByText("Sun, Jan 13, 09:00 PST – 11:00 PST")).toBeInTheDocument();
    expect(within(first).getByText("Pretraining a 1B model")).toBeInTheDocument();
    const second = screen.getByRole("listitem", { name: "Alan Turing, h100-tokyo-01" });
    expect(within(second).getByText("Mon, Jan 14, 10:00 GMT+9 – 12:00 GMT+9")).toBeInTheDocument();
    expect(within(second).getByText("h100-tokyo-01 · Tokyo Lab · 1 GPU")).toBeInTheDocument();

    const query = queueRequests(requests)[0]?.query;
    expect(query?.get("status")).toBe("pending");
    expect(query?.get("order")).toBe("starts_at");
  });

  it("marks demo requests, which are replaced every night", async () => {
    queueApi([pending({ seeded: true }), later]);
    renderRoute("/admin/approvals");

    const demo = await screen.findByRole("listitem", { name: "Ada Lovelace, h100-node-01" });
    expect(within(demo).getByText("Demo")).toBeInTheDocument();
    const real = screen.getByRole("listitem", { name: "Alan Turing, h100-tokyo-01" });
    expect(within(real).queryByText("Demo")).not.toBeInTheDocument();
  });

  it("is reached from the admin hub", async () => {
    queueApi([]);
    const { router } = renderRoute("/admin");

    fireEvent.click(await screen.findByRole("link", { name: /Approvals/ }));

    expect(await screen.findByText("Nothing to review")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/admin/approvals");
  });

  it("keeps students out, like the other admin pages", async () => {
    const requests = fakeApi(...signedInAs("student"));
    const { router } = renderRoute("/admin/approvals");

    await screen.findByRole("heading", { name: "Dashboard" });
    expect(router.state.location.pathname).toBe("/");
    expect(queueRequests(requests)).toHaveLength(0);
  });

  it("approves a request and refreshes the queue", async () => {
    const { requests } = queueApi([pending(), later]);
    renderRoute("/admin/approvals");

    const item = await screen.findByRole("listitem", { name: "Ada Lovelace, h100-node-01" });
    fireEvent.click(within(item).getByRole("button", { name: "Approve" }));

    expect(
      await screen.findByText("Approved Ada Lovelace's booking from Sun, Jan 13, 09:00 PST."),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("listitem", { name: /Ada Lovelace/ })).toBeNull();
    });
    expect(queueRequests(requests).length).toBeGreaterThan(1);
  });

  it("only offers rejection once the start has passed", async () => {
    queueApi([pending({ starts_at: "2030-01-12T17:00:00Z", ends_at: "2030-01-12T19:00:00Z" })]);
    renderRoute("/admin/approvals");

    const item = await screen.findByRole("listitem", { name: /Ada Lovelace/ });
    const approve = within(item).getByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    expect(approve).toHaveAccessibleDescription(
      "Its start time has passed, so it can no longer be approved. Reject it to release the GPUs.",
    );
    expect(within(item).getByRole("button", { name: "Reject" })).toBeEnabled();
  });

  it("rejects with a reason after checking it", async () => {
    const { requests } = queueApi([pending()]);
    renderRoute("/admin/approvals");

    const item = await screen.findByRole("listitem", { name: /Ada Lovelace/ });
    fireEvent.click(within(item).getByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog", { name: "Reject this request?" });

    fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    expect(
      await within(dialog).findByText("Give a reason; the student sees it."),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "x".repeat(501) },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    expect(await within(dialog).findByText("Use at most 500 characters.")).toBeInTheDocument();
    expect(requests.filter((r) => r.path.endsWith("/reject"))).toHaveLength(0);

    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "  Booked for a course that week  " },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));

    expect(await screen.findByText("Rejected Ada Lovelace's booking.")).toBeInTheDocument();
    expect(requests.find((r) => r.path.endsWith("/reject"))?.body).toEqual({
      reason: "Booked for a course that week",
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("puts the API's validation error under the reason", async () => {
    queueApi(
      [pending()],
      [
        on("POST", "/reservations/r-1/reject", () =>
          problem(422, "validation-error", {
            errors: [{ field: "reason", message: "String should have at most 500 characters" }],
          }),
        ),
      ],
    );
    renderRoute("/admin/approvals");

    const item = await screen.findByRole("listitem", { name: /Ada Lovelace/ });
    fireEvent.click(within(item).getByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Reason"), { target: { value: "No" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));

    expect(
      await within(dialog).findByText("String should have at most 500 characters"),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Reason")).toHaveAttribute("aria-invalid", "true");
  });

  it("shows a conflict when another admin decided first, then refreshes", async () => {
    let decidedElsewhere = false;
    const { requests } = queueApi(
      [pending(), later],
      [
        on("POST", "/reservations/r-1/approve", () => {
          decidedElsewhere = true;
          return problem(409, "invalid-reservation-state", {
            detail: "Only pending reservations can be approved; this one is rejected.",
          });
        }),
        on("GET", "/reservations", () =>
          Response.json(page(decidedElsewhere ? [later] : [pending(), later])),
        ),
      ],
    );
    renderRoute("/admin/approvals");

    const item = await screen.findByRole("listitem", { name: /Ada Lovelace/ });
    const before = queueRequests(requests).length;
    fireEvent.click(within(item).getByRole("button", { name: "Approve" }));

    // The queue is fetched again; the decided request drops out, and its error moves
    // above the list so the admin still sees why.
    await waitFor(() => {
      expect(queueRequests(requests).length).toBeGreaterThan(before);
    });
    await waitFor(() => {
      expect(screen.queryByRole("listitem", { name: /Ada Lovelace/ })).toBeNull();
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Ada Lovelace's request: Only pending reservations can be approved; this one is rejected.",
    );
    expect(screen.getByRole("listitem", { name: /Alan Turing/ })).toBeInTheDocument();
  });

  it("keeps a conflict on the row while the request is still listed", async () => {
    // Approve refused because the request started meanwhile; it stays pending and listed.
    queueApi(
      [pending()],
      [
        on("POST", "/reservations/r-1/approve", () =>
          problem(409, "invalid-reservation-state", {
            detail: "This reservation has already started.",
          }),
        ),
      ],
    );
    renderRoute("/admin/approvals");

    const item = await screen.findByRole("listitem", { name: /Ada Lovelace/ });
    fireEvent.click(within(item).getByRole("button", { name: "Approve" }));

    expect(await within(item).findByRole("alert")).toHaveTextContent(
      "This reservation has already started.",
    );
  });

  it("shows a reject conflict on the row, not in the dialog", async () => {
    queueApi(
      [pending()],
      [
        on("POST", "/reservations/r-1/reject", () =>
          problem(409, "invalid-reservation-state", {
            detail: "Only pending reservations can be rejected; this one is approved.",
          }),
        ),
      ],
    );
    renderRoute("/admin/approvals");

    const item = await screen.findByRole("listitem", { name: /Ada Lovelace/ });
    fireEvent.click(within(item).getByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Reason"), { target: { value: "Course" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject" }));

    expect(await within(item).findByRole("alert")).toHaveTextContent(
      "Only pending reservations can be rejected; this one is approved.",
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows loading, error, and empty states", async () => {
    let fail = true;
    fakeApi(
      ...signedInAs("admin"),
      on("GET", "/reservations", () =>
        fail ? problem(500, "internal-error", { detail: "Down." }) : Response.json(page([])),
      ),
    );
    renderRoute("/admin/approvals");

    expect(await screen.findByText("Could not load requests")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Nothing to review")).toBeInTheDocument();
  });
});
