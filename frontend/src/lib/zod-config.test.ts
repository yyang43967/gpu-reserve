import { afterEach, describe, expect, it, vi } from "vitest";

describe("zod configuration", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it("parses objects without constructing a Function", async () => {
    vi.resetModules();
    const functionSpy = vi.spyOn(globalThis, "Function");
    await import("./zod-config");
    const { z } = await import("zod");

    const schema = z.object({ email: z.email(), hours: z.number().positive() });

    expect(z.config().jitless).toBe(true);
    expect(schema.parse({ email: "ada@example.edu", hours: 2 })).toEqual({
      email: "ada@example.edu",
      hours: 2,
    });
    expect(schema.safeParse({ email: "nope", hours: -1 }).success).toBe(false);
    expect(functionSpy).not.toHaveBeenCalled();
  });
});
