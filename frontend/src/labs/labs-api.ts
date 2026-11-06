import { apiFetch } from "@/lib/api";
import type { Lab, Page } from "@/lib/api-types";

/** The API's maximum page size. Lab counts are small, so one page fills a dropdown. */
export const LABS_PAGE_SIZE = 100;
export const DEFAULT_LAB_TIMEZONE = "America/Los_Angeles";
export const LAB_NAME_MAX_LENGTH = 100;
export const LAB_LOCATION_MAX_LENGTH = 200;
export const LAB_DESCRIPTION_MAX_LENGTH = 2000;

export interface LabListParams {
  page: number;
  /** Admins only; students always get active labs. */
  isActive: boolean | null;
}

export interface CreateLabInput {
  name: string;
  location: string;
  timezone: string;
  description: string | null;
  is_active: boolean;
}

/** Omitted fields are left unchanged; `description: null` clears it. */
export type UpdateLabInput = Partial<CreateLabInput>;

/** Every lab query starts with this key, so one invalidation refreshes them all. */
export const labsKey = ["labs"] as const;

export function labsListKey(params: LabListParams) {
  return [...labsKey, "list", params] as const;
}

export function labKey(id: string) {
  return [...labsKey, "detail", id] as const;
}

export function listLabs(params: LabListParams, signal?: AbortSignal): Promise<Page<Lab>> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(LABS_PAGE_SIZE),
  });
  if (params.isActive !== null) query.set("is_active", String(params.isActive));
  return apiFetch<Page<Lab>>(`/labs?${query.toString()}`, { signal });
}

export function getLab(id: string, signal?: AbortSignal): Promise<Lab> {
  return apiFetch<Lab>(`/labs/${encodeURIComponent(id)}`, { signal });
}

export function createLab(input: CreateLabInput): Promise<Lab> {
  return apiFetch<Lab>("/labs", { method: "POST", body: input });
}

export function updateLab(id: string, input: UpdateLabInput): Promise<Lab> {
  return apiFetch<Lab>(`/labs/${encodeURIComponent(id)}`, { method: "PATCH", body: input });
}

/** Fails with 409 `lab-not-empty` while the lab has servers; deactivate it instead. */
export function deleteLab(id: string): Promise<void> {
  return apiFetch<undefined>(`/labs/${encodeURIComponent(id)}`, { method: "DELETE" });
}
