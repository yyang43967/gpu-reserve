import { vi } from "vitest";

import type { Page, TokenResponse, User, UserRole } from "@/lib/api-types";

export function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: "user-1",
    email: "ada@example.edu",
    full_name: "Ada Lovelace",
    role: "student",
    weekly_gpu_hour_quota: 48,
    is_active: true,
    avatar_key: null,
    created_at: "2026-11-05T09:00:00+00:00",
    ...overrides,
  };
}

export function tokenResponse(user: User, token = "access-token"): TokenResponse {
  return { access_token: token, token_type: "bearer", expires_in: 900, user };
}

export function problem(
  status: number,
  slug: string,
  options: {
    detail?: string;
    errors?: { field: string; message: string }[];
    headers?: Record<string, string>;
  } = {},
): Response {
  return new Response(
    JSON.stringify({
      type: `https://gpureserve.dev/errors/${slug}`,
      title: slug.replaceAll("-", " "),
      status,
      detail: options.detail,
      errors: options.errors,
    }),
    {
      status,
      headers: { "Content-Type": "application/problem+json", ...options.headers },
    },
  );
}

export function page<T>(items: T[], overrides: Partial<Page<T>> = {}): Page<T> {
  return { items, total: items.length, page: 1, page_size: 20, ...overrides };
}

export interface FakeRequest {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

type Route = (request: FakeRequest) => Response | Promise<Response> | undefined;

/**
 * Stub fetch with a tiny API. `routes` are tried in order; the first to return a
 * Response wins. Unmatched requests fail the test loudly.
 */
export function fakeApi(...routes: Route[]) {
  const requests: FakeRequest[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
      "http://localhost",
    );
    const request: FakeRequest = {
      method: init.method ?? "GET",
      path: url.pathname.replace(/^\/api\/v1/, ""),
      query: url.searchParams,
      body: typeof init.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
    };
    requests.push(request);
    for (const route of routes) {
      const response = await route(request);
      if (response) return response;
    }
    throw new Error(`Unexpected request: ${request.method} ${request.path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return requests;
}

export function on(
  method: string,
  path: string,
  respond: (request: FakeRequest) => Response | Promise<Response>,
): Route {
  return (request) =>
    request.method === method && request.path === path ? respond(request) : undefined;
}

/** A refresh cookie for this user: the app boots signed in. Logging out clears it. */
export function signedInAs(role: UserRole = "student", overrides: Partial<User> = {}): Route[] {
  const user = makeUser({ role, ...overrides });
  let cookieValid = true;
  return [
    on("POST", "/auth/refresh", () =>
      cookieValid ? Response.json(tokenResponse(user)) : problem(401, "invalid-token"),
    ),
    on("GET", "/me", () => Response.json(user)),
    on("POST", "/auth/logout", () => {
      cookieValid = false;
      return new Response(null, { status: 204 });
    }),
  ];
}

/** No refresh cookie: the app boots signed out. */
export const signedOut: Route = on("POST", "/auth/refresh", () =>
  problem(401, "invalid-token", { detail: "No refresh token was sent." }),
);

export const healthy: Route = on("GET", "/health", () => Response.json({ status: "ok" }));
