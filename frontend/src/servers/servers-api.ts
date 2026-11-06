import { apiFetch } from "@/lib/api";
import type { GpuModel, Page, Server, ServerStatus } from "@/lib/api-types";

export const SERVERS_PAGE_SIZE = 24;
export const MAX_GPUS_PER_SERVER = 16;
export const DEFAULT_MAX_BOOKING_HOURS = 12;
export const MAX_BOOKING_HOURS_LIMIT = 14 * 24;
export const MAX_CPU_CORES = 1024;
export const MAX_RAM_GB = 65_536;
export const HOSTNAME_MAX_LENGTH = 63;
export const INTERCONNECT_MAX_LENGTH = 50;
export const NOTES_MAX_LENGTH = 2000;

/**
 * Display name and memory per GPU, in the order GPU pickers show them. The memory is only
 * for filter options and form previews; show a server's own `gpu_memory_gb`.
 */
// Keep memory in sync with GPU_MEMORY_GB in backend/app/models/server.py.
export const GPU_MODELS: Record<GpuModel, { label: string; memoryGb: number }> = {
  RTX_4090: { label: "RTX 4090", memoryGb: 24 },
  RTX_6000_ADA: { label: "RTX 6000 Ada", memoryGb: 48 },
  L40S: { label: "L40S", memoryGb: 48 },
  A100_40GB: { label: "A100 40GB", memoryGb: 40 },
  A100_80GB: { label: "A100 80GB", memoryGb: 80 },
  H100_80GB: { label: "H100 80GB", memoryGb: 80 },
};

export const SERVER_STATUS_LABELS: Record<ServerStatus, string> = {
  available: "Available",
  maintenance: "Maintenance",
  retired: "Retired",
};

/** Every filter is optional; null means "any". */
export interface ServerFilters {
  labId: string | null;
  gpuModel: GpuModel | null;
  /** Memory per GPU, in GB. */
  minGpuMemoryGb: number | null;
  /** Compared with the server's GPU count. */
  minGpus: number | null;
  status: ServerStatus | null;
}

export interface ServerListParams extends ServerFilters {
  page: number;
}

export interface CreateServerInput {
  lab_id: string;
  hostname: string;
  gpu_model: GpuModel;
  gpu_count: number;
  cpu_cores: number;
  ram_gb: number;
  interconnect: string | null;
  status: ServerStatus;
  requires_approval: boolean;
  max_booking_hours: number;
  notes: string | null;
  /** A key from an upload with purpose `server_image`. */
  image_key: string | null;
}

/**
 * Omitted fields are left unchanged; `interconnect`, `notes`, and `image_key` set to null
 * clear them. Lowering `gpu_count` removes the highest-numbered GPUs.
 */
export type UpdateServerInput = Partial<CreateServerInput>;

/** Every server query starts with this key, so one invalidation refreshes them all. */
export const serversKey = ["servers"] as const;

export function serversListKey(params: ServerListParams) {
  return [...serversKey, "list", params] as const;
}

export function serverKey(id: string) {
  return [...serversKey, "detail", id] as const;
}

export function serverListQuery(params: ServerListParams): URLSearchParams {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(SERVERS_PAGE_SIZE),
  });
  if (params.labId) query.set("lab_id", params.labId);
  if (params.gpuModel) query.set("gpu_model", params.gpuModel);
  if (params.minGpuMemoryGb !== null) query.set("min_gpu_memory_gb", String(params.minGpuMemoryGb));
  if (params.minGpus !== null) query.set("min_gpus", String(params.minGpus));
  if (params.status) query.set("status", params.status);
  return query;
}

export function listServers(params: ServerListParams, signal?: AbortSignal): Promise<Page<Server>> {
  return apiFetch<Page<Server>>(`/servers?${serverListQuery(params).toString()}`, { signal });
}

export function getServer(id: string, signal?: AbortSignal): Promise<Server> {
  return apiFetch<Server>(`/servers/${encodeURIComponent(id)}`, { signal });
}

export function createServer(input: CreateServerInput): Promise<Server> {
  return apiFetch<Server>("/servers", { method: "POST", body: input });
}

export function updateServer(id: string, input: UpdateServerInput): Promise<Server> {
  return apiFetch<Server>(`/servers/${encodeURIComponent(id)}`, { method: "PATCH", body: input });
}

export function deleteServer(id: string): Promise<void> {
  return apiFetch<undefined>(`/servers/${encodeURIComponent(id)}`, { method: "DELETE" });
}
