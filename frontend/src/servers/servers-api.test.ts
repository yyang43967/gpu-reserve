import { describe, expect, it } from "vitest";

import {
  type ServerListParams,
  deleteServer,
  listServers,
  serverListQuery,
  updateServer,
} from "@/servers/servers-api";
import { fakeApi, makeServer, on, page } from "@/test/fake-api";

const noFilters: ServerListParams = {
  page: 1,
  labId: null,
  gpuModel: null,
  minGpuMemoryGb: null,
  minGpus: null,
  status: null,
};

describe("server list query", () => {
  it("sends only paging without filters", () => {
    expect(serverListQuery(noFilters).toString()).toBe("page=1&page_size=24");
  });

  it("maps every filter to its query parameter", () => {
    const query = serverListQuery({
      page: 2,
      labId: "lab-1",
      gpuModel: "H100_80GB",
      minGpuMemoryGb: 48,
      minGpus: 4,
      status: "available",
    });

    expect(Object.fromEntries(query)).toEqual({
      page: "2",
      page_size: "24",
      lab_id: "lab-1",
      gpu_model: "H100_80GB",
      min_gpu_memory_gb: "48",
      min_gpus: "4",
      status: "available",
    });
  });
});

describe("servers api", () => {
  it("lists servers with the filters in the query string", async () => {
    const requests = fakeApi(on("GET", "/servers", () => Response.json(page([makeServer()]))));

    const result = await listServers({ ...noFilters, minGpus: 2 });

    expect(result.items[0]?.hostname).toBe("gpu-01");
    expect(requests[0]?.query.get("min_gpus")).toBe("2");
  });

  it("sends explicit nulls in updates so fields can be cleared", async () => {
    const requests = fakeApi(on("PATCH", "/servers/server-1", () => Response.json(makeServer())));

    await updateServer("server-1", { notes: null, image_key: null, gpu_count: 4 });

    expect(requests[0]?.body).toEqual({ notes: null, image_key: null, gpu_count: 4 });
  });

  it("deletes a server", async () => {
    const requests = fakeApi(
      on("DELETE", "/servers/server-1", () => new Response(null, { status: 204 })),
    );

    await expect(deleteServer("server-1")).resolves.toBeUndefined();
    expect(requests).toHaveLength(1);
  });
});
