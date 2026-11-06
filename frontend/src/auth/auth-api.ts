import { apiFetch } from "@/lib/api";
import type { TokenResponse, User } from "@/lib/api-types";
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
