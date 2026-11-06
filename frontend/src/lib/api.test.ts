import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TokenResponse } from "@/lib/api-types";
import { ApiError, apiFetch, refreshSession, setSessionExpiredHandler } from "@/lib/api";
import { getAccessToken, setAccessToken } from "@/lib/auth-token";

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;

interface Call {
  path: string;
  method: string;
  authorization: string | null;
  credentials: RequestCredentials | undefined;
}

function mockFetch(handler: Handler) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    calls.push({
      path: url.replace("/api/v1", ""),
      method: init.method ?? "GET",
      authorization: new Headers(init.headers).get("Authorization"),
      credentials: init.credentials,
    });
    return handler(url, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function problem(status: number, slug: string, extra: Record<string, unknown> = {}) {
  return new Response(
    JSON.stringify({
      type: `https://gpureserve.dev/errors/${slug}`,
      title: `Title for ${slug}`,
      status,
      detail: `Detail for ${slug}`,
      ...extra,
    }),
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

function session(token: string): TokenResponse {
  return {
    access_token: token,
    token_type: "bearer",
    expires_in: 900,
    user: {
      id: "u1",
      email: "ada@example.edu",
      full_name: "Ada",
      role: "student",
      weekly_gpu_hour_quota: 48,
      is_active: true,
      avatar_key: null,
      created_at: "2026-11-05T00:00:00+00:00",
    },
  };
}

/** A /me endpoint that accepts only `validToken`, and a refresh that issues `newToken`. */
function apiWithExpiringToken(validToken: string, refresh: () => Response | Promise<Response>) {
  return mockFetch((url, init) => {
    if (url.endsWith("/auth/refresh")) {
      return refresh();
    }
    const authorization = new Headers(init.headers).get("Authorization");
    return authorization === `Bearer ${validToken}`
      ? Response.json({ ok: true })
      : problem(401, "invalid-token");
  });
}

const sessionExpired = vi.fn();

beforeEach(() => {
  setAccessToken(null);
  sessionExpired.mockReset();
  setSessionExpiredHandler(sessionExpired);
});

describe("apiFetch basics", () => {
  it("sends the access token and includes credentials", async () => {
    setAccessToken("token-1");
    const calls = mockFetch(() => Response.json({ ok: true }));

    await apiFetch("/me");

    expect(calls).toEqual([
      { path: "/me", method: "GET", authorization: "Bearer token-1", credentials: "include" },
    ]);
  });

  it("sends JSON bodies", async () => {
    let sent: RequestInit = {};
    mockFetch((_url, init) => {
      sent = init;
      return Response.json({});
    });

    await apiFetch("/me", { method: "PATCH", body: { full_name: "Ada" } });

    expect(sent.body).toBe('{"full_name":"Ada"}');
    expect(new Headers(sent.headers).get("Content-Type")).toBe("application/json");
  });

  it("returns undefined for 204", async () => {
    mockFetch(() => new Response(null, { status: 204 }));

    await expect(apiFetch("/users/1", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("turns problem details into an ApiError", async () => {
    mockFetch(() =>
      problem(422, "validation-failed", {
        errors: [{ field: "email", message: "value is not a valid email address" }],
      }),
    );

    const error = await apiFetch("/auth/register", { method: "POST" }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 422,
      code: "validation-failed",
      title: "Title for validation-failed",
      message: "Detail for validation-failed",
      fieldErrors: [{ field: "email", message: "value is not a valid email address" }],
    });
  });

  it("reads Retry-After from rate limit responses", async () => {
    mockFetch(() => {
      const response = problem(429, "too-many-login-attempts");
      response.headers.set("Retry-After", "120");
      return response;
    });

    const error = await apiFetch("/auth/login", { method: "POST" }).catch((e: unknown) => e);

    expect(error).toMatchObject({ status: 429, retryAfterSeconds: 120 });
  });

  it("falls back to a generic message for non-problem errors", async () => {
    mockFetch(() => new Response("oops", { status: 502 }));

    await expect(apiFetch("/me")).rejects.toThrow("Request failed with status 502");
  });
});

describe("refresh on 401", () => {
  it("refreshes once and retries with the new token", async () => {
    setAccessToken("expired");
    const calls = apiWithExpiringToken("fresh", () => Response.json(session("fresh")));

    await expect(apiFetch("/me")).resolves.toEqual({ ok: true });

    expect(calls.map((c) => [c.method, c.path, c.authorization])).toEqual([
      ["GET", "/me", "Bearer expired"],
      ["POST", "/auth/refresh", null],
      ["GET", "/me", "Bearer fresh"],
    ]);
    expect(getAccessToken()).toBe("fresh");
    expect(sessionExpired).not.toHaveBeenCalled();
  });

  it("shares one refresh between concurrent 401s", async () => {
    setAccessToken("expired");
    const calls = apiWithExpiringToken("fresh", () => Response.json(session("fresh")));

    await Promise.all([apiFetch("/me"), apiFetch("/me"), apiFetch("/me")]);

    expect(calls.filter((c) => c.path === "/auth/refresh")).toHaveLength(1);
  });

  it("ends the session when the refresh token is rejected", async () => {
    setAccessToken("expired");
    apiWithExpiringToken("never", () => problem(401, "refresh-token-reused"));

    const error = await apiFetch("/me").catch((e: unknown) => e);

    expect(error).toMatchObject({ status: 401, code: "invalid-token" });
    expect(sessionExpired).toHaveBeenCalledOnce();
    expect(getAccessToken()).toBeNull();
  });

  it("keeps the session when refresh fails for another reason", async () => {
    setAccessToken("expired");
    apiWithExpiringToken("never", () => new Response(null, { status: 503 }));

    await expect(apiFetch("/me")).rejects.toMatchObject({ status: 503 });

    expect(sessionExpired).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe("expired");
  });

  it.each(["/auth/login", "/auth/register", "/auth/refresh"])(
    "does not refresh when %s answers 401",
    async (path) => {
      const calls = mockFetch(() => problem(401, "invalid-credentials"));

      await expect(apiFetch(path, { method: "POST" })).rejects.toMatchObject({ status: 401 });

      expect(calls).toHaveLength(1);
      expect(sessionExpired).not.toHaveBeenCalled();
    },
  );

  it("retries only once", async () => {
    setAccessToken("expired");
    // The refresh succeeds, but the API still rejects the new token.
    const calls = apiWithExpiringToken("never", () => Response.json(session("fresh")));

    await expect(apiFetch("/me")).rejects.toMatchObject({ status: 401 });

    expect(calls.map((c) => c.path)).toEqual(["/me", "/auth/refresh", "/me"]);
  });
});

describe("refresh across tabs", () => {
  let held = false;
  let lockNames: string[] = [];

  beforeEach(() => {
    held = false;
    lockNames = [];
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: async <T>(name: string, task: () => Promise<T>): Promise<T> => {
          lockNames.push(name);
          held = true;
          try {
            return await task();
          } finally {
            held = false;
          }
        },
      },
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, "locks");
  });

  it("sends the refresh request only while holding the lock", async () => {
    let heldDuringRefresh: boolean | null = null;
    mockFetch(() => {
      heldDuringRefresh = held;
      return Response.json(session("fresh"));
    });

    await refreshSession();

    expect(lockNames).toEqual(["gpu-reserve:refresh"]);
    expect(heldDuringRefresh).toBe(true);
    expect(held).toBe(false);
  });

  it("works without the Web Locks API", async () => {
    Reflect.deleteProperty(navigator, "locks");
    mockFetch(() => Response.json(session("fresh")));

    await expect(refreshSession()).resolves.toMatchObject({ access_token: "fresh" });
    expect(lockNames).toEqual([]);
  });
});
