import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fakeApi, makeServer, on, page, problem, signedInAs } from "@/test/fake-api";
import { renderRoute } from "@/test/render";

const SERVER_ID = "6f1c2a7e-3b9d-4c55-9a0e-2d8f1b7c4e10";
const MISSING_ID = "0b8e9f4a-1c2d-4e3f-8a5b-6c7d8e9f0a1b";

const server = makeServer({
  id: SERVER_ID,
  hostname: "vision-a100-01",
  lab: { id: "lab-1", name: "Vision Lab", timezone: "America/Los_Angeles", is_active: true },
  gpu_model: "A100_80GB",
  gpu_memory_gb: 80,
  gpu_count: 8,
  cpu_cores: 128,
  ram_gb: 1024,
  interconnect: "NVLink",
  max_booking_hours: 48,
  notes: "Scratch disk is wiped weekly.\nAsk before installing drivers.",
});

function spec(label: string) {
  const term = screen.getByText(label, { selector: "dt" });
  return term.nextElementSibling?.textContent;
}

describe("server detail page", () => {
  it("shows the server's specs, lab, and booking rules", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", `/servers/${SERVER_ID}`, () => Response.json(server)),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    expect(await screen.findByRole("heading", { name: "vision-a100-01" })).toBeInTheDocument();
    expect(screen.getByText("Vision Lab · America/Los_Angeles")).toBeInTheDocument();
    expect(screen.getByText("Available")).toBeInTheDocument();

    expect(spec("GPUs")).toBe("8 × A100 80GB");
    expect(spec("Memory per GPU")).toBe("80 GB");
    expect(spec("Total GPU memory")).toBe("640 GB");
    expect(spec("CPU cores")).toBe("128");
    expect(spec("RAM")).toBe("1024 GB");
    expect(spec("Interconnect")).toBe("NVLink");
    expect(spec("Longest booking")).toBe("48 hours");
    expect(spec("Approval")).toBe("Not required");
    expect(spec("Lab time zone")).toBe("America/Los_Angeles");

    expect(screen.getByText(/Scratch disk is wiped weekly/)).toBeInTheDocument();
    expect(screen.queryByText("Requires approval")).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("uses the memory per GPU the API reports", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", `/servers/${SERVER_ID}`, () =>
        Response.json({ ...server, gpu_memory_gb: 81, gpu_count: 2 }),
      ),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    await screen.findByRole("heading", { name: "vision-a100-01" });
    expect(spec("Memory per GPU")).toBe("81 GB");
    expect(spec("Total GPU memory")).toBe("162 GB");
  });

  it("marks servers that need approval and shows the image", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", `/servers/${SERVER_ID}`, () =>
        Response.json({
          ...server,
          requires_approval: true,
          status: "maintenance",
          interconnect: null,
          notes: null,
          max_booking_hours: 1,
          image_url: "http://storage.test/uploads/servers/abc.png?sig=1",
        }),
      ),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    await screen.findByRole("heading", { name: "vision-a100-01" });
    expect(screen.getByText("Requires approval")).toBeInTheDocument();
    expect(screen.getByText("Maintenance")).toBeInTheDocument();
    expect(spec("Approval")).toBe("An admin approves each booking");
    expect(spec("Interconnect")).toBe("Not specified");
    expect(spec("Longest booking")).toBe("1 hour");
    expect(screen.queryByRole("heading", { name: "Notes" })).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "The vision-a100-01 server" })).toHaveAttribute(
      "src",
      "http://storage.test/uploads/servers/abc.png?sig=1",
    );
  });

  it("shows an inactive lab to admins", async () => {
    fakeApi(
      ...signedInAs("admin"),
      on("GET", `/servers/${SERVER_ID}`, () =>
        Response.json({ ...server, lab: { ...server.lab, is_active: false } }),
      ),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    expect(await screen.findByText("Lab inactive")).toBeInTheDocument();
  });

  it("shows availability, one row per lab day with the slots returned", async () => {
    const requests = fakeApi(
      ...signedInAs("student"),
      on("GET", `/servers/${SERVER_ID}`, () => Response.json(server)),
      on("GET", `/servers/${SERVER_ID}/availability`, () =>
        Response.json({
          server_id: SERVER_ID,
          timezone: "America/Los_Angeles",
          gpu_count: 8,
          slot_minutes: 30,
          starts_at: "2030-11-03T07:00:00Z",
          ends_at: "2030-11-04T08:00:00Z",
          // The fall-back day in Los Angeles: 25 hours, so 50 half-hour slots.
          runs: [
            { starts_at: "2030-11-03T07:00:00Z", ends_at: "2030-11-03T16:00:00Z", free: 8 },
            { starts_at: "2030-11-03T16:00:00Z", ends_at: "2030-11-03T18:00:00Z", free: 0 },
            { starts_at: "2030-11-03T18:00:00Z", ends_at: "2030-11-04T08:00:00Z", free: 8 },
          ],
        }),
      ),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    const days = await screen.findAllByTestId("timeline-day");
    expect(days).toHaveLength(1);
    const [day] = days;
    if (!day) throw new Error("no day row");
    expect(within(day).getByText("Sun, Nov 3")).toBeInTheDocument();
    expect(within(day).getAllByTestId("timeline-slot")).toHaveLength(50);
    // The repeated hour is marked where the clock goes back, after 01:30 PDT.
    const divider = within(day).getByTestId("clock-change");
    expect(divider.previousElementSibling).toHaveAttribute(
      "title",
      "Sun, Nov 3, 01:30 PDT: 8 of 8 GPUs free",
    );
    expect(divider.nextElementSibling).toHaveAttribute(
      "title",
      "Sun, Nov 3, 01:00 PST: 8 of 8 GPUs free",
    );
    expect(
      within(day).getByText(/Clocks go back: 01:00–02:00 happens twice \(PDT, then PST\)\./, {
        selector: "p",
      }),
    ).toBeInTheDocument();
    expect(
      within(day).getByText(
        "08:00–10:00: 0 of 8 free; all free otherwise. Clocks go back: 01:00–02:00 happens twice (PDT, then PST).",
      ),
    ).toHaveClass("sr-only");
    const window = requests.find((r) => r.path.endsWith("/availability"))?.query;
    const from = new Date(window?.get("from") ?? "");
    const to = new Date(window?.get("to") ?? "");
    expect(to.getTime() - from.getTime()).toBe(7 * 24 * 60 * 60_000);
  });

  it("draws the spring-forward day with 46 slots and marks the skipped hour", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", `/servers/${SERVER_ID}`, () => Response.json(server)),
      on("GET", `/servers/${SERVER_ID}/availability`, () =>
        Response.json({
          server_id: SERVER_ID,
          timezone: "America/Los_Angeles",
          gpu_count: 8,
          slot_minutes: 30,
          starts_at: "2030-03-10T08:00:00Z",
          ends_at: "2030-03-11T07:00:00Z",
          runs: [{ starts_at: "2030-03-10T08:00:00Z", ends_at: "2030-03-11T07:00:00Z", free: 8 }],
        }),
      ),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    const [day] = await screen.findAllByTestId("timeline-day");
    if (!day) throw new Error("no day row");
    expect(within(day).getAllByTestId("timeline-slot")).toHaveLength(46);
    const divider = within(day).getByTestId("clock-change");
    expect(divider.previousElementSibling).toHaveAttribute(
      "title",
      "Sun, Mar 10, 01:30 PST: 8 of 8 GPUs free",
    );
    expect(divider.nextElementSibling).toHaveAttribute(
      "title",
      "Sun, Mar 10, 03:00 PDT: 8 of 8 GPUs free",
    );
    expect(
      within(day).getByText(/Clocks go forward: 02:00–03:00 is skipped \(PST to PDT\)\./, {
        selector: "p",
      }),
    ).toBeInTheDocument();
  });

  it("shows an availability error with a retry", async () => {
    let fail = true;
    fakeApi(
      ...signedInAs("student"),
      on("GET", `/servers/${SERVER_ID}`, () => Response.json(server)),
      on("GET", `/servers/${SERVER_ID}/availability`, () =>
        fail
          ? problem(500, "internal-error", { detail: "Database is down." })
          : Response.json({
              server_id: SERVER_ID,
              timezone: "America/Los_Angeles",
              gpu_count: 8,
              slot_minutes: 30,
              starts_at: "2030-01-12T08:00:00Z",
              ends_at: "2030-01-13T08:00:00Z",
              runs: [
                { starts_at: "2030-01-12T08:00:00Z", ends_at: "2030-01-13T08:00:00Z", free: 8 },
              ],
            }),
      ),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    expect(await screen.findByText("Could not load availability")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findAllByTestId("timeline-slot")).toHaveLength(48);
  });

  it("shows a loading state first", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", `/servers/${SERVER_ID}`, () => Response.json(server)),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    expect(await screen.findByText("Loading server")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "vision-a100-01" })).toBeInTheDocument();
  });

  it("says the server was not found on a 404", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", `/servers/${MISSING_ID}`, () => problem(404, "server-not-found")),
    );
    renderRoute(`/servers/${MISSING_ID}`);

    expect(await screen.findByText("Server not found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse servers" })).toHaveAttribute(
      "href",
      "/servers",
    );
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("goes straight to not found for a malformed id, without a request", async () => {
    const requests = fakeApi(...signedInAs("student"));
    renderRoute("/servers/not-a-uuid");

    expect(await screen.findByText("Server not found")).toBeInTheDocument();
    expect(requests.filter((request) => request.path.startsWith("/servers"))).toHaveLength(0);
  });

  it("shows other errors with a retry", async () => {
    let fail = true;
    fakeApi(
      ...signedInAs("student"),
      on("GET", `/servers/${SERVER_ID}`, () =>
        fail
          ? problem(500, "internal-error", { detail: "The server failed." })
          : Response.json(server),
      ),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    expect(await screen.findByText("Could not load this server")).toBeInTheDocument();
    expect(screen.getByText("The server failed.")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "vision-a100-01" })).toBeInTheDocument();
  });

  it("is reached from a card on the servers page", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/labs", () => Response.json(page([]))),
      on("GET", "/servers", () => Response.json(page([server]))),
      on("GET", `/servers/${SERVER_ID}`, () => Response.json(server)),
    );
    const { router } = renderRoute("/servers");

    fireEvent.click(await screen.findByRole("link", { name: /vision-a100-01/ }));

    expect(await screen.findByText("Total GPU memory")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/servers/${SERVER_ID}`);
  });
});
