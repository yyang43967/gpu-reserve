import { ApiError, apiFetch } from "@/lib/api";
import type { DemoLogin, TokenResponse, User } from "@/lib/api-types";
import { setAccessToken } from "@/lib/auth-token";

export interface LoginInput {
  email: string;
  password: string;
}

export interface RegisterInput extends LoginInput {
  full_name: string;
}

export async function login(input: LoginInput): Promise<User> {
  const session = await apiFetch<TokenResponse>("/auth/login", { method: "POST", body: input });
  setAccessToken(session.access_token);
  return session.user;
}

export async function register(input: RegisterInput): Promise<User> {
  const session = await apiFetch<TokenResponse>("/auth/register", {
    method: "POST",
    body: input,
  });
  setAccessToken(session.access_token);
  return session.user;
}

export async function logout(): Promise<void> {
  try {
    await apiFetch<undefined>("/auth/logout", { method: "POST" });
  } finally {
    // Signed out locally even if the request failed; the server session then expires.
    setAccessToken(null);
  }
}

export function getMe(signal?: AbortSignal): Promise<User> {
  return apiFetch<User>("/me", { signal });
}

/** Omitted fields are left unchanged; `avatar_key: null` removes the avatar. */
export function updateProfile(input: {
  full_name?: string;
  avatar_key?: string | null;
}): Promise<User> {
  return apiFetch<User>("/me", { method: "PATCH", body: input });
}

/** Change the password. The API signs out every other session and returns a fresh one. */
export async function changePassword(input: {
  current_password: string;
  new_password: string;
}): Promise<User> {
  const session = await apiFetch<TokenResponse>("/me/password", { method: "PUT", body: input });
  setAccessToken(session.access_token);
  return session.user;
}

/** The demo account's credentials, or null when this deployment has none (404). */
export async function getDemoLogin(signal?: AbortSignal): Promise<DemoLogin | null> {
  try {
    return await apiFetch<DemoLogin>("/auth/demo", { signal });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}
