import type { FieldError, Problem, TokenResponse } from "@/lib/api-types";
import { getAccessToken, setAccessToken } from "@/lib/auth-token";

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api/v1";

const PROBLEM_CONTENT_TYPE = "application/problem+json";
const REFRESH_PATH = "/auth/refresh";
// Endpoints whose 401 means "wrong credentials", not "access token expired".
const NO_REFRESH_PATHS = new Set(["/auth/login", "/auth/register", REFRESH_PATH]);
const REFRESH_LOCK_NAME = "gpu-reserve:refresh";

export class ApiError extends Error {
  readonly status: number;
  /** The last segment of the problem `type` URI, such as "email-taken". */
  readonly code: string | null;
  readonly title: string | null;
  readonly detail: string | null;
  readonly fieldErrors: FieldError[];
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    problem: Partial<Problem> | null = null,
    retryAfterSeconds: number | null = null,
  ) {
    super(problem?.detail ?? problem?.title ?? `Request failed with status ${String(status)}`);
    this.name = "ApiError";
    this.status = status;
    this.code = problem?.type?.split("/").pop() ?? null;
    this.title = problem?.title ?? null;
    this.detail = problem?.detail ?? null;
    this.fieldErrors = problem?.errors ?? [];
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  const retryAfter = Number.parseInt(response.headers.get("Retry-After") ?? "", 10);
  let problem: Partial<Problem> | null = null;
  if (response.headers.get("Content-Type")?.startsWith(PROBLEM_CONTENT_TYPE)) {
    try {
      problem = (await response.json()) as Partial<Problem>;
    } catch {
      problem = null;
    }
  }
  return new ApiError(response.status, problem, Number.isNaN(retryAfter) ? null : retryAfter);
}

// --- session expiry ---

let onSessionExpired: () => void = () => undefined;

/** Called when the refresh cookie is no longer valid and the user must sign in again. */
export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler;
}

// --- refresh ---

function withRefreshLock<T>(task: () => Promise<T>): Promise<T> {
  // The lock serializes refreshes across tabs. The browser attaches the cookie when the
  // request is sent, which happens only once the lock is held, so every tab presents the
  // newest refresh token and never one another tab has already rotated. Presenting a
  // rotated token would count as reuse and end the session everywhere.
  if (!("locks" in navigator)) {
    return task();
  }
  return navigator.locks.request(REFRESH_LOCK_NAME, task);
}

let refreshInFlight: Promise<TokenResponse | null> | null = null;

async function requestRefresh(): Promise<TokenResponse | null> {
  const response = await fetch(`${API_BASE_URL}${REFRESH_PATH}`, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (response.status === 401) {
    setAccessToken(null);
    return null;
  }
  if (!response.ok) {
    // A server or network problem says nothing about the session; keep it.
    throw await toApiError(response);
  }
  const session = (await response.json()) as TokenResponse;
  setAccessToken(session.access_token);
  return session;
}

/**
 * Exchange the refresh cookie for a new access token.
 *
 * Resolves to the new session, or null when the cookie is missing, expired, or revoked.
 * Concurrent callers in the same tab share one request.
 */
export function refreshSession(): Promise<TokenResponse | null> {
  refreshInFlight ??= withRefreshLock(requestRefresh).finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

// --- requests ---

export interface ApiRequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

async function send(path: string, options: ApiRequestOptions): Promise<Response> {
  const headers = new Headers({ Accept: "application/json" });
  const token = getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  let body: string | undefined;
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }
  return fetch(`${API_BASE_URL}${path}`, {
    method: options.method ?? "GET",
    headers,
    body,
    signal: options.signal,
    credentials: "include",
  });
}

/**
 * Call the API and return the parsed JSON body (undefined for 204).
 *
 * On a 401 it refreshes the access token once and retries. If the session cannot be
 * refreshed, the session-expired handler runs and the original error is thrown.
 * Every error is thrown as an ApiError built from the problem details response.
 */
export async function apiFetch<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  let response = await send(path, options);

  if (response.status === 401 && !NO_REFRESH_PATHS.has(path)) {
    const session = await refreshSession();
    if (session === null) {
      const error = await toApiError(response);
      onSessionExpired();
      throw error;
    }
    response = await send(path, options);
  }

  if (!response.ok) {
    throw await toApiError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return apiFetch<T>(path, { signal });
}

// --- health ---

export interface HealthResponse {
  status: "ok";
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiGet<HealthResponse>("/health", signal);
}
