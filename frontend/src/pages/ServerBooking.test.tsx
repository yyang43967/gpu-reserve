import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Reservation } from "@/lib/api-types";
import { fakeApi, makeServer, on, problem, signedInAs, type FakeRequest } from "@/test/fake-api";
import { renderRoute } from "@/test/render";

const SERVER_ID = "6f1c2a7e-3b9d-4c55-9a0e-2d8f1b7c4e10";
const server = makeServer({ id: SERVER_ID, hostname: "vision-a100-01", gpu_count: 8 });

const availability = {
  server_id: SERVER_ID,
  timezone: "America/Los_Angeles",
  gpu_count: 8,
  slot_minutes: 30,
  starts_at: "2030-11-03T07:00:00Z",
  ends_at: "2030-11-03T11:00:00Z",
  runs: [
    // Sunday 3 November 2030 in Los Angeles, 00:00 PDT to 02:00 PST: 01:00 and 01:30
    // happen twice.
    { starts_at: "2030-11-03T07:00:00Z", ends_at: "2030-11-03T11:00:00Z", free: 8 },
  ],
};

/** The quota week of `at`, faked for the week of Monday 7 January 2030 and later weeks. */
function quotaFor(request: FakeRequest) {
  const nextWeek = (request.query.get("at") ?? "") >= "2030-01-14T08:00:00Z";
  return {
    week_start: nextWeek ? "2030-01-14T08:00:00Z" : "2030-01-07T08:00:00Z",
    week_end: nextWeek ? "2030-01-21T08:00:00Z" : "2030-01-14T08:00:00Z",
    timezone: "America/Los_Angeles",
    used_hours: nextWeek ? 40 : 6.5,
    quota_hours: 48,
    remaining_hours: nextWeek ? 8 : 41.5,
  };
}

function created(body: Record<string, unknown>, status = "approved"): Reservation {
  return {
    id: "r-1",
    status: status as Reservation["status"],
    gpu_count: body.gpu_count as number,
    starts_at: body.starts_at as string,
    ends_at: body.ends_at as string,
    purpose: body.purpose as string,
    created_via: "web",
    decided_at: null,
    decision_reason: null,
    created_at: "2030-01-07T18:00:00Z",
    server: { id: SERVER_ID, hostname: "vision-a100-01", gpu_model: "A100_80GB" },
    lab: { id: "lab-1", name: "Vision Lab", timezone: "America/Los_Angeles" },
    user: { id: "user-1", full_name: "Ada Lovelace", email: "ada@example.edu" },
    gpu_indexes: [0],
  };
}

function bookingApi(respond?: (request: FakeRequest) => Response) {
  return fakeApi(
    ...signedInAs("student"),
    on("GET", `/servers/${SERVER_ID}`, () => Response.json(server)),
    on("GET", `/servers/${SERVER_ID}/availability`, () => Response.json(availability)),
    on("GET", "/me/quota", (request) => Response.json(quotaFor(request))),
    on("POST", "/reservations", (request) =>
      respond
        ? respond(request)
        : Response.json(created(request.body as Record<string, unknown>), { status: 201 }),
    ),
  );
}

async function openDialog() {
  fireEvent.click(await screen.findByRole("button", { name: "Book GPUs" }));
  return screen.findByRole("dialog", { name: "Book vision-a100-01" });
}

function set(dialog: HTMLElement, label: string, value: string) {
  fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
}

function fill(dialog: HTMLElement, values: Record<string, string>) {
  for (const [label, value] of Object.entries(values)) set(dialog, label, value);
}

function submit(dialog: HTMLElement) {
  fireEvent.click(within(dialog).getByRole("button", { name: "Book" }));
}

function posted(requests: FakeRequest[]) {
  return requests.filter((r) => r.method === "POST" && r.path === "/reservations");
}

describe("booking dialog", () => {
  it("shows GPU-hours for the week the chosen start falls in", async () => {
    const requests = bookingApi();
    renderRoute(`/servers/${SERVER_ID}`);
    const dialog = await openDialog();

    fill(dialog, { "Start date": "2030-01-11", "Start time": "22:00" });
    expect(
      await within(dialog).findByText(
        /Week starting Mon, Jan 7, 00:00 PST: 6\.5 of 48 GPU-hours used, 41\.5 left\./,
      ),
    ).toBeInTheDocument();

    fill(dialog, { "Start date": "2030-01-15", "Start time": "20:00" });
    expect(
      await within(dialog).findByText(
        /Week starting Mon, Jan 14, 00:00 PST: 40 of 48 GPU-hours used, 8 left\./,
      ),
    ).toBeInTheDocument();
    const asked = requests.filter((r) => r.path === "/me/quota").map((r) => r.query.get("at"));
    expect(asked).toContain("2030-01-16T04:00:00.000Z");
  });

  it("books an overnight reservation, moving the end date to the next day", async () => {
    const requests = bookingApi();
    renderRoute(`/servers/${SERVER_ID}`);
    const dialog = await openDialog();

    fill(dialog, { "Start date": "2030-01-11", "Start time": "22:00", "End time": "02:00" });
    expect(within(dialog).getByLabelText("End date")).toHaveValue("2030-01-12");
    fill(dialog, { GPUs: "2", Purpose: "Overnight training run" });
    submit(dialog);

    expect(
      await screen.findByText(/Booked 2 GPUs from Fri, Jan 11, 22:00 PST/),
    ).toBeInTheDocument();
    expect(posted(requests)[0]?.body).toEqual({
      server_id: SERVER_ID,
      gpu_count: 2,
      starts_at: "2030-01-12T06:00:00.000Z",
      ends_at: "2030-01-12T10:00:00.000Z",
      purpose: "Overnight training run",
    });
  });

  it("books a multi-day reservation with a picked end date", async () => {
    const requests = bookingApi();
    renderRoute(`/servers/${SERVER_ID}`);
    const dialog = await openDialog();

    fill(dialog, {
      "Start date": "2030-01-11",
      "Start time": "22:00",
      "End date": "2030-01-13",
      "End time": "06:00",
      Purpose: "Long evaluation",
    });
    // Changing the end time keeps the picked end date.
    set(dialog, "End time", "07:00");
    expect(within(dialog).getByLabelText("End date")).toHaveValue("2030-01-13");
    submit(dialog);

    await waitFor(() => {
      expect(posted(requests)).toHaveLength(1);
    });
    expect(posted(requests)[0]?.body).toMatchObject({
      starts_at: "2030-01-12T06:00:00.000Z",
      ends_at: "2030-01-13T15:00:00.000Z",
    });
  });

  it("treats an end at 00:00 as midnight after the start day", async () => {
    const requests = bookingApi();
    renderRoute(`/servers/${SERVER_ID}`);
    const dialog = await openDialog();

    fill(dialog, {
      "Start date": "2030-01-11",
      "Start time": "22:00",
      "End time": "00:00",
      Purpose: "Evening run",
    });
    expect(within(dialog).getByLabelText("End date")).toHaveValue("2030-01-12");
    submit(dialog);

    await waitFor(() => {
      expect(posted(requests)[0]?.body).toMatchObject({ ends_at: "2030-01-12T08:00:00.000Z" });
    });
  });

  it("refuses a start in the spring-forward gap without sending anything", async () => {
    const requests = bookingApi();
    renderRoute(`/servers/${SERVER_ID}`);
    const dialog = await openDialog();

    fill(dialog, {
      "Start date": "2030-03-10",
      "Start time": "02:30",
      "End time": "04:00",
      Purpose: "Run",
    });
    submit(dialog);

    expect(
      await within(dialog).findByText("Clocks skip this time on that day. Pick another time."),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Start time")).toHaveAttribute("aria-invalid", "true");
    expect(posted(requests)).toHaveLength(0);
  });

  it("names the occurrence of an ambiguous fall-back time", async () => {
    const requests = bookingApi();
    renderRoute(`/servers/${SERVER_ID}`);
    const dialog = await openDialog();

    fill(dialog, {
      "Start date": "2030-11-03",
      "Start time": "01:30",
      "End time": "03:00",
      Purpose: "Run",
    });

    expect(
      within(dialog).getByText("01:30 happens twice that day; this is the first one (PDT)."),
    ).toBeInTheDocument();
    submit(dialog);
    await waitFor(() => {
      expect(posted(requests)[0]?.body).toMatchObject({
        starts_at: "2030-11-03T08:30:00.000Z",
        ends_at: "2030-11-03T11:00:00.000Z",
      });
    });
  });

  it("puts a known conflict under its field, using the API's detail", async () => {
    bookingApi(() =>
      problem(409, "gpu-unavailable", {
        detail: "Only 1 of 2 requested GPUs is free on vision-a100-01 for that time.",
      }),
    );
    renderRoute(`/servers/${SERVER_ID}`);
    const dialog = await openDialog();

    fill(dialog, { "Start date": "2030-01-12", "Start time": "08:00", "End time": "10:00" });
    fill(dialog, { GPUs: "2", Purpose: "Run" });
    submit(dialog);

    expect(
      await within(dialog).findByText(
        "Only 1 of 2 requested GPUs is free on vision-a100-01 for that time.",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("GPUs")).toHaveAttribute("aria-invalid", "true");
  });

  it("shows the quota and unknown problem types above the form", async () => {
    let slug = "quota-exceeded";
    bookingApi(() => problem(409, slug, { detail: `Refused: ${slug}.` }));
    renderRoute(`/servers/${SERVER_ID}`);
    const dialog = await openDialog();

    fill(dialog, { "Start date": "2030-01-12", "Start time": "08:00", "End time": "10:00" });
    fill(dialog, { Purpose: "Run" });
    submit(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Refused: quota-exceeded.");

    slug = "brand-new-rule";
    submit(dialog);
    expect(await within(dialog).findByText("Refused: brand-new-rule.")).toBeInTheDocument();
  });

  it("refreshes availability after booking", async () => {
    const requests = bookingApi();
    renderRoute(`/servers/${SERVER_ID}`);
    const dialog = await openDialog();
    const availabilityCalls = () => requests.filter((r) => r.path.endsWith("/availability")).length;
    await waitFor(() => {
      expect(availabilityCalls()).toBe(1);
    });

    fill(dialog, { "Start date": "2030-01-12", "Start time": "08:00", "End time": "10:00" });
    fill(dialog, { Purpose: "Run" });
    submit(dialog);

    await waitFor(() => {
      expect(availabilityCalls()).toBe(2);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says a booking on a restricted server awaits approval", async () => {
    bookingApi((request) =>
      Response.json(created(request.body as Record<string, unknown>, "pending"), { status: 201 }),
    );
    renderRoute(`/servers/${SERVER_ID}`);
    const dialog = await openDialog();

    fill(dialog, { "Start date": "2030-01-12", "Start time": "08:00", "End time": "10:00" });
    fill(dialog, { Purpose: "Run" });
    submit(dialog);

    expect(await screen.findByText(/An admin will review it\./)).toBeInTheDocument();
  });

  it("prefills the start from a free slot in the timeline", async () => {
    bookingApi();
    renderRoute(`/servers/${SERVER_ID}`);

    // The second slot of the day: 00:30 PDT.
    const slots = await screen.findAllByRole("button", { name: /^Book from / });
    const slot = slots[1];
    if (!slot) throw new Error("no slot");
    fireEvent.click(slot);
    const dialog = await screen.findByRole("dialog", { name: "Book vision-a100-01" });

    expect(within(dialog).getByLabelText("Start date")).toHaveValue("2030-11-03");
    expect(within(dialog).getByLabelText("Start time")).toHaveValue("00:30");
    expect(within(dialog).getByLabelText("End time")).toHaveValue("01:30");
  });

  it("does not offer booking on a server in maintenance", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", `/servers/${SERVER_ID}`, () => Response.json({ ...server, status: "maintenance" })),
      on("GET", `/servers/${SERVER_ID}/availability`, () => Response.json(availability)),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    expect(await screen.findByRole("button", { name: "Book GPUs" })).toBeDisabled();
    expect(
      screen.getByText("This server is in maintenance and cannot be booked."),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: /^Book from / })).toHaveLength(0);
  });
});
