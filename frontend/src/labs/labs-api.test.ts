import { describe, expect, it } from "vitest";

import { createLab, deleteLab, listLabs, updateLab } from "@/labs/labs-api";
import { fakeApi, makeLab, on, page } from "@/test/fake-api";

describe("labs api", () => {
  it("asks for one large page and leaves out the status filter when unset", async () => {
    const requests = fakeApi(on("GET", "/labs", () => Response.json(page([makeLab()]))));

    await listLabs({ page: 1, isActive: null });

    expect(Object.fromEntries(requests[0]?.query ?? [])).toEqual({
      page: "1",
      page_size: "100",
    });
  });

  it("filters by status", async () => {
    const requests = fakeApi(on("GET", "/labs", () => Response.json(page([]))));

    await listLabs({ page: 1, isActive: false });

    expect(requests[0]?.query.get("is_active")).toBe("false");
  });

  it("creates, updates, and deletes labs", async () => {
    const requests = fakeApi(
      on("POST", "/labs", () => Response.json(makeLab(), { status: 201 })),
      on("PATCH", "/labs/lab-1", () => Response.json(makeLab())),
      on("DELETE", "/labs/lab-1", () => new Response(null, { status: 204 })),
    );

    await createLab({
      name: "Vision Lab",
      location: "Gates 104",
      timezone: "America/Los_Angeles",
      description: null,
      is_active: true,
    });
    await updateLab("lab-1", { description: null });
    await deleteLab("lab-1");

    expect(requests.map((request) => `${request.method} ${request.path}`)).toEqual([
      "POST /labs",
      "PATCH /labs/lab-1",
      "DELETE /labs/lab-1",
    ]);
    expect(requests[1]?.body).toEqual({ description: null });
  });
});
