import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ServerUtilization, Utilization } from "@/lib/api-types";
import { fakeApi, makeLab, on, page, problem, signedInAs, type FakeRequest } from "@/test/fake-api";
import { renderRoute } from "@/test/render";

/** The item at `index` (negative counts from the end); fails the test if it is missing. */
function at<T>(items: readonly T[], index: number): T {
  const item = items.at(index);
  if (item === undefined) throw new Error(`No item at ${String(index)}`);
  return item;
}

// Monday 7 January 2030, 10:20 in Los Angeles.
const NOW = new Date("2030-01-07T18:20:00Z");

function row(overrides: Partial<ServerUtilization> = {}): ServerUtilization {
  return {
    server_id: "s-1",
    hostname: "a100-node-01",
    gpu_model: "A100_80GB",
    gpu_count: 4,
    status: "available",
    lab: { id: "lab-1", name: "Vision Lab", timezone: "America/Los_Angeles" },
    available_gpu_hours: 672,
    booked_gpu_hours: 168,
    pending_gpu_hours: 12.5,
    utilization: 0.25,
    ...overrides,
  };
}

function report(servers: ServerUtilization[]): Utilization {
  const available = servers.reduce((sum, r) => sum + r.available_gpu_hours, 0);
  const booked = servers.reduce((sum, r) => sum + r.booked_gpu_hours, 0);
  return {
    starts_at: "2029-12-31T18:00:00Z",
    ends_at: "2030-01-07T18:00:00Z",
    servers,
    totals: {
      available_gpu_hours: available,
      booked_gpu_hours: booked,
      pending_gpu_hours: servers.reduce((sum, r) => sum + r.pending_gpu_hours, 0),
      utilization: available ? booked / available : 0,
    },
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

function api(respond: (request: FakeRequest) => Response) {
  return fakeApi(
    ...signedInAs("admin"),
    on("GET", "/labs", () =>
      Response.json(page([makeLab(), makeLab({ id: "lab-2", name: "NLP Lab" })])),
    ),
    on("GET", "/stats/utilization", respond),
  );
}

const calls = (requests: FakeRequest[]) =>
  requests.filter((r) => r.path === "/stats/utilization").map((r) => r.query);

describe("admin utilization", () => {
  it("shows each server and the totals", async () => {
    api(() =>
      Response.json(
        report([
          row(),
          row({
            server_id: "s-2",
            hostname: "l40s-node-01",
            gpu_model: "L40S",
            status: "maintenance",
            available_gpu_hours: 336,
            booked_gpu_hours: 0,
            pending_gpu_hours: 0,
            utilization: 0,
          }),
        ]),
      ),
    );
    renderRoute("/admin/utilization");

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    // Header, two servers, totals.
    expect(rows).toHaveLength(4);
    const cells = (index: number) =>
      within(at(rows, index))
        .getAllByRole("cell")
        .map((cell) => cell.textContent);
    expect(cells(1)).toEqual([
      "a100-node-01Vision Lab · 4 × A100 80GB",
      "",
      "25.0%",
      "168",
      "12.5",
      "672",
    ]);
    expect(cells(2)[0]).toBe("l40s-node-01Vision Lab · 4 × L40S · Maintenance");
    expect(cells(3)).toEqual(["All servers", "", "16.7%", "168", "12.5", "1,008"]);
  });

  it("asks for the chosen period and lab", async () => {
    const requests = api(() => Response.json(report([row()])));
    renderRoute("/admin/utilization");
    await screen.findByRole("table");

    // The last seven days, to the start of the current hour.
    expect(Object.fromEntries(at(calls(requests), 0))).toEqual({
      from: "2029-12-31T18:00:00.000Z",
      to: "2030-01-07T18:00:00.000Z",
    });

    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "next-14" } });
    await waitFor(() => {
      expect(Object.fromEntries(at(calls(requests), -1))).toEqual({
        from: "2030-01-07T18:00:00.000Z",
        to: "2030-01-21T18:00:00.000Z",
      });
    });

    await waitFor(() => {
      expect(screen.getByLabelText("Lab")).toBeEnabled();
    });
    fireEvent.change(screen.getByLabelText("Lab"), { target: { value: "lab-2" } });
    await waitFor(() => {
      expect(calls(requests).at(-1)?.get("lab_id")).toBe("lab-2");
    });
  });

  it("shows empty and error states", async () => {
    let fail = true;
    api(() => (fail ? problem(500, "internal-error") : Response.json(report([]))));
    renderRoute("/admin/utilization");

    expect(await screen.findByText("Could not load utilization")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No servers")).toBeInTheDocument();
  });

  it("is listed on the admin page", async () => {
    api(() => Response.json(report([])));
    renderRoute("/admin");

    expect(await screen.findByRole("link", { name: /Utilization/ })).toHaveAttribute(
      "href",
      "/admin/utilization",
    );
  });
});
