import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fakeApi, healthy, makeServer, on, page, problem, signedInAs } from "@/test/fake-api";
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
      healthy,
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
      healthy,
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
      healthy,
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
      healthy,
      on("GET", `/servers/${SERVER_ID}`, () =>
        Response.json({ ...server, lab: { ...server.lab, is_active: false } }),
      ),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    expect(await screen.findByText("Lab inactive")).toBeInTheDocument();
  });

  it("labels the availability timeline as not ready yet", async () => {
    fakeApi(
      ...signedInAs("student"),
      healthy,
      on("GET", `/servers/${SERVER_ID}`, () => Response.json(server)),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    const heading = await screen.findByRole("heading", { name: "Availability" });
    const card = heading.closest("[data-slot=card]");
    if (!(card instanceof HTMLElement)) throw new Error("no availability card");
    expect(within(card).getByText("Availability timeline coming soon")).toBeInTheDocument();
  });

  it("shows a loading state first", async () => {
    fakeApi(
      ...signedInAs("student"),
      healthy,
      on("GET", `/servers/${SERVER_ID}`, () => Response.json(server)),
    );
    renderRoute(`/servers/${SERVER_ID}`);

    expect(await screen.findByText("Loading server")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "vision-a100-01" })).toBeInTheDocument();
  });

  it("says the server was not found on a 404", async () => {
    fakeApi(
      ...signedInAs("student"),
      healthy,
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
    const requests = fakeApi(...signedInAs("student"), healthy);
    renderRoute("/servers/not-a-uuid");

    expect(await screen.findByText("Server not found")).toBeInTheDocument();
    expect(requests.filter((request) => request.path.startsWith("/servers"))).toHaveLength(0);
  });

  it("shows other errors with a retry", async () => {
    let fail = true;
    fakeApi(
      ...signedInAs("student"),
      healthy,
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
      healthy,
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
