import { apiFetch } from "@/lib/api";
import type { Overview, Utilization } from "@/lib/api-types";
import { reservationsKey } from "@/reservations/reservations-api";

/** Under the reservations key, so booking or cancelling refreshes the dashboard. */
export const overviewKey = [...reservationsKey, "overview"] as const;

export function utilizationKey(params: UtilizationParams) {
  return [...reservationsKey, "utilization", params] as const;
}

export interface UtilizationParams {
  labId: string | null;
  from: string;
  to: string;
}

export function getOverview(signal?: AbortSignal): Promise<Overview> {
  return apiFetch<Overview>("/stats/overview", { signal });
}

export function getUtilization(
  params: UtilizationParams,
  signal?: AbortSignal,
): Promise<Utilization> {
  const query = new URLSearchParams({ from: params.from, to: params.to });
  if (params.labId) query.set("lab_id", params.labId);
  return apiFetch<Utilization>(`/stats/utilization?${query.toString()}`, { signal });
}
