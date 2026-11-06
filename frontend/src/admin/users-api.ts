import { apiFetch } from "@/lib/api";
import type { Page, User, UserRole } from "@/lib/api-types";

export const USERS_PAGE_SIZE = 20;
export const MAX_WEEKLY_GPU_HOUR_QUOTA = 10_000;
export const DEFAULT_WEEKLY_GPU_HOUR_QUOTA = 48;

export type StatusFilter = "active" | "inactive";

export interface UserListParams {
  page: number;
  q: string;
  role: UserRole | null;
  status: StatusFilter | null;
}

export interface CreateUserInput {
  email: string;
  full_name: string;
  password: string;
  role: UserRole;
  weekly_gpu_hour_quota: number;
}

export interface UpdateUserInput {
  role?: UserRole;
  is_active?: boolean;
  weekly_gpu_hour_quota?: number;
}

/** Every admin user query starts with this key, so one invalidation refreshes them all. */
export const adminUsersKey = ["admin", "users"] as const;

export function adminUsersListKey(params: UserListParams) {
  return [...adminUsersKey, "list", params] as const;
}

export function listUsers(params: UserListParams, signal?: AbortSignal): Promise<Page<User>> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(USERS_PAGE_SIZE),
  });
  if (params.q) query.set("q", params.q);
  if (params.role) query.set("role", params.role);
  if (params.status) query.set("is_active", String(params.status === "active"));
  return apiFetch<Page<User>>(`/users?${query.toString()}`, { signal });
}

export function createUser(input: CreateUserInput): Promise<User> {
  return apiFetch<User>("/users", { method: "POST", body: input });
}

export function updateUser(id: string, input: UpdateUserInput): Promise<User> {
  return apiFetch<User>(`/users/${encodeURIComponent(id)}`, { method: "PATCH", body: input });
}

export function deleteUser(id: string): Promise<void> {
  return apiFetch<undefined>(`/users/${encodeURIComponent(id)}`, { method: "DELETE" });
}
