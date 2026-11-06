import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Server } from "@/lib/api-types";
import {
  fakeApi,
  healthy,
  makeLab,
  makeServer,
  on,
  page,
  problem,
  signedInAs,
  type FakeRequest,
} from "@/test/fake-api";
import { renderRoute } from "@/test/render";

const visionLab = makeLab({ id: "lab-1", name: "Vision Lab" });
const nlpLab = makeLab({ id: "lab-2", name: "NLP Lab" });
const oldLab = makeLab({ id: "lab-3", name: "Old Lab", is_active: false });

const a100 = makeServer({
  id: "server-1",
  hostname: "gpu-01",
  gpu_model: "A100_80GB",
  gpu_memory_gb: 80,
  gpu_count: 8,
});
const rtx = makeServer({
  id: "server-2",
  hostname: "gpu-02",
  lab: { id: "lab-2", name: "NLP Lab", timezone: "America/New_York", is_active: true },
  gpu_model: "RTX_4090",
  gpu_memory_gb: 24,
  gpu_count: 2,
  status: "maintenance",
  requires_approval: true,
});

/** A /servers API that filters and pages like the server does. */
function serversApi(servers: Server[], pageSize = 24) {
  return on("GET", "/servers", (request: FakeRequest) => {
    const q = request.query;
    const matching = servers.filter(
      (server) =>
        (!q.get("lab_id") || server.lab.id === q.get("lab_id")) &&
        (!q.get("gpu_model") || server.gpu_model === q.get("gpu_model")) &&
        server.gpu_memory_gb >= Number(q.get("min_gpu_memory_gb") ?? 0) &&
        server.gpu_count >= Number(q.get("min_gpus") ?? 0) &&
        (!q.get("status") || server.status === q.get("status")),
    );
    const number = Number(q.get("page") ?? "1");
    const start = (number - 1) * pageSize;
    return Response.json(
      page(matching.slice(start, start + pageSize), {
        total: matching.length,
        page: number,
        page_size: pageSize,
      }),
    );
  });
}

const labsApi = on("GET", "/labs", () => Response.json(page([visionLab, nlpLab])));

function serverRequests(requests: FakeRequest[]) {
  return requests.filter((request) => request.path === "/servers");
}

function lastServerQuery(requests: FakeRequest[]) {
  const query = new URLSearchParams(serverRequests(requests).at(-1)?.query);
  query.delete("page_size");
  return Object.fromEntries(query);
}

describe("servers page", () => {
  it("shows a card per server with specs from the API", async () => {
    fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([a100, rtx]));
    renderRoute("/servers");

    const list = await screen.findByRole("list", { name: "Servers" });
    const [firstCard, secondCard, ...rest] = within(list).getAllByRole("listitem");
    expect(rest).toHaveLength(0);
    if (!firstCard || !secondCard) throw new Error("expected two cards");

    const first = within(firstCard);
    expect(first.getByRole("heading", { name: "gpu-01" })).toBeInTheDocument();
    expect(first.getByText("Vision Lab")).toBeInTheDocument();
    expect(first.getByText(/8 × A100 80GB/)).toBeInTheDocument();
    expect(first.getByText(/80 GB each/)).toBeInTheDocument();
    expect(first.getByRole("link")).toHaveAttribute("href", "/servers/server-1");

    const second = within(secondCard);
    expect(second.getByText("Maintenance")).toBeInTheDocument();
    expect(second.getByText("Needs approval")).toBeInTheDocument();
  });

  it("shows memory per GPU as the API reports it", async () => {
    // The API is the source of truth, even if it ever disagrees with the local table.
    const custom = makeServer({ gpu_model: "A100_40GB", gpu_memory_gb: 41 });
    fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([custom]));
    renderRoute("/servers");

    expect(await screen.findByText(/41 GB each/)).toBeInTheDocument();
  });

  it("reads filters from the URL and sends them to the API", async () => {
    const requests = fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([a100]));
    renderRoute("/servers?lab=lab-1&gpu=A100_80GB&memory=48&gpus=4&status=available");

    await screen.findByRole("heading", { name: "gpu-01" });
    expect(lastServerQuery(requests)).toEqual({
      page: "1",
      lab_id: "lab-1",
      gpu_model: "A100_80GB",
      min_gpu_memory_gb: "48",
      min_gpus: "4",
      status: "available",
    });
    await waitFor(() => {
      expect(screen.getByLabelText("Lab")).toHaveValue("lab-1");
    });
    expect(screen.getByLabelText("GPU model")).toHaveValue("A100_80GB");
    expect(screen.getByLabelText("Memory per GPU")).toHaveValue("48");
    expect(screen.getByLabelText("GPUs per server")).toHaveValue("4");
    expect(screen.getByLabelText("Status")).toHaveValue("available");
  });

  it("ignores filter values it does not offer", async () => {
    const requests = fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([a100]));
    // Students cannot filter by retired; 3 GPUs and 12 GB are not options.
    renderRoute("/servers?gpu=K80&memory=12&gpus=3&status=retired");

    await screen.findByRole("heading", { name: "gpu-01" });
    expect(lastServerQuery(requests)).toEqual({ page: "1" });
  });

  it("updates the URL and the query when a filter changes, back to page 1", async () => {
    const requests = fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([a100, rtx]));
    const { router } = renderRoute("/servers?page=2&gpu=RTX_4090");
    await screen.findByRole("heading", { name: "Servers" });

    fireEvent.change(screen.getByLabelText("GPUs per server"), { target: { value: "8" } });

    await waitFor(() => {
      expect(router.state.location.search).toBe("?gpu=RTX_4090&gpus=8");
    });
    await waitFor(() => {
      expect(lastServerQuery(requests)).toEqual({
        page: "1",
        gpu_model: "RTX_4090",
        min_gpus: "8",
      });
    });
  });

  it("keeps every filter when several change before the page re-renders", async () => {
    const requests = fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([a100, rtx]));
    const { router } = renderRoute("/servers?page=2");
    await screen.findByRole("heading", { name: "Servers" });

    // One act batches the events into a single render, so each change sees the
    // same render as the first.
    act(() => {
      fireEvent.change(screen.getByLabelText("Memory per GPU"), { target: { value: "48" } });
      fireEvent.change(screen.getByLabelText("GPUs per server"), { target: { value: "4" } });
      fireEvent.change(screen.getByLabelText("Status"), { target: { value: "available" } });
    });

    await waitFor(() => {
      expect(router.state.location.search).toBe("?memory=48&gpus=4&status=available");
    });
    await waitFor(() => {
      expect(lastServerQuery(requests)).toEqual({
        page: "1",
        min_gpu_memory_gb: "48",
        min_gpus: "4",
        status: "available",
      });
    });
    expect(await screen.findByRole("heading", { name: "gpu-01" })).toBeInTheDocument();
  });

  it("builds on a location set elsewhere after its own updates", async () => {
    fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([a100, rtx]));
    const { router } = renderRoute("/servers");
    await screen.findByRole("heading", { name: "gpu-01" });

    fireEvent.change(screen.getByLabelText("GPU model"), { target: { value: "RTX_4090" } });
    await waitFor(() => {
      expect(router.state.location.search).toBe("?gpu=RTX_4090");
    });
    // A link or the Back button replaces the filters.
    await act(() => router.navigate("/servers?memory=80"));

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "available" } });

    await waitFor(() => {
      expect(router.state.location.search).toBe("?memory=80&status=available");
    });
  });

  it("lists labs in the lab filter, marking inactive ones", async () => {
    fakeApi(
      ...signedInAs("admin"),
      healthy,
      on("GET", "/labs", () => Response.json(page([visionLab, oldLab]))),
      serversApi([a100]),
    );
    renderRoute("/servers");

    const select = await screen.findByLabelText("Lab");
    expect(
      await within(select).findByRole("option", { name: "Old Lab (inactive)" }),
    ).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "lab-1" } });

    await screen.findByRole("heading", { name: "gpu-01" });
  });

  it("offers the retired status to admins only", async () => {
    fakeApi(...signedInAs("admin"), healthy, labsApi, serversApi([a100]));
    renderRoute("/servers");

    const status = await screen.findByLabelText("Status");
    expect(within(status).getByRole("option", { name: "Retired" })).toBeInTheDocument();
  });

  it("hides the retired status from students", async () => {
    fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([a100]));
    renderRoute("/servers");

    const status = await screen.findByLabelText("Status");
    expect(within(status).queryByRole("option", { name: "Retired" })).not.toBeInTheDocument();
  });

  it("explains when no server matches and clears the filters", async () => {
    const requests = fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([rtx]));
    const { router } = renderRoute("/servers?gpu=H100_80GB&gpus=16");

    expect(await screen.findByText("No servers match these filters")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    await screen.findByRole("heading", { name: "gpu-02" });
    expect(router.state.location.search).toBe("");
    expect(lastServerQuery(requests)).toEqual({ page: "1" });
  });

  it("shows an empty state without servers", async () => {
    fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([]));
    renderRoute("/servers");

    expect(await screen.findByText("No servers yet")).toBeInTheDocument();
    expect(screen.getByText(/Check back later/)).toBeInTheDocument();
  });

  it("points admins to the admin area when there are no servers", async () => {
    fakeApi(...signedInAs("admin"), healthy, labsApi, serversApi([]));
    renderRoute("/servers");

    expect(await screen.findByText(/from the admin area/)).toBeInTheDocument();
  });

  it("shows an error with a retry", async () => {
    let fail = true;
    const list = serversApi([a100]);
    fakeApi(
      ...signedInAs("student"),
      healthy,
      labsApi,
      (request) =>
        request.path === "/servers" && fail
          ? problem(500, "internal-error", { detail: "The server failed." })
          : undefined,
      list,
    );
    renderRoute("/servers");

    expect(await screen.findByText("Could not load servers")).toBeInTheDocument();
    expect(screen.getByText("The server failed.")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "gpu-01" })).toBeInTheDocument();
  });

  it("keeps the server list usable when labs fail to load", async () => {
    fakeApi(
      ...signedInAs("student"),
      healthy,
      on("GET", "/labs", () => problem(500, "internal-error")),
      serversApi([a100]),
    );
    renderRoute("/servers");

    expect(await screen.findByRole("heading", { name: "gpu-01" })).toBeInTheDocument();
    expect(
      await screen.findByRole("option", { name: "All labs (list unavailable)" }),
    ).toBeInTheDocument();
  });

  it("pages through results with history entries", async () => {
    const many = Array.from({ length: 30 }, (_, i) =>
      makeServer({ id: `server-${String(i)}`, hostname: `node-${String(i).padStart(2, "0")}` }),
    );
    const requests = fakeApi(...signedInAs("student"), healthy, labsApi, serversApi(many));
    const { router } = renderRoute("/servers");

    await screen.findByRole("heading", { name: "node-00" });
    expect(screen.getByText("1–24 of 30")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByRole("heading", { name: "node-24" })).toBeInTheDocument();
    expect(router.state.location.search).toBe("?page=2");
    expect(router.state.historyAction).toBe("PUSH");
    expect(lastServerQuery(requests)).toEqual({ page: "2" });
  });

  it("steps back to the last page when the page is past the end", async () => {
    fakeApi(...signedInAs("student"), healthy, labsApi, serversApi([a100]));
    const { router } = renderRoute("/servers?page=5");

    await screen.findByRole("heading", { name: "gpu-01" });
    await waitFor(() => {
      expect(router.state.location.search).toBe("");
    });
  });
});
