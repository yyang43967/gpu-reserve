import { apiFetch } from "@/lib/api";
import type { GpuModel, SlotSearchResponse } from "@/lib/api-types";
import { DEFAULT_LAB_TIMEZONE } from "@/labs/labs-api";

export const MAX_SEARCH_DAYS = 14;
export const MAX_RESULTS_PER_SERVER = 3;

export interface SlotSearchInput {
  gpu_count: number;
  /** Whole half hours. */
  duration_minutes: number;
  /** Earliest start, ISO 8601 with an offset. */
  earliest: string;
  /** Latest end: every result ends at or before this. */
  latest: string;
  gpu_model?: GpuModel | null;
  /** Memory per GPU, in GB. */
  min_gpu_memory_gb?: number | null;
}

export function searchSlots(input: SlotSearchInput): Promise<SlotSearchResponse> {
  return apiFetch<SlotSearchResponse>("/search/slots", { method: "POST", body: input });
}

/**
 * The zone the window is typed in, at first: the browser's own zone when a lab uses it
 * (people usually search in the zone they live in), otherwise the first lab's.
 */
export function initialZone(labZones: readonly string[], browserZone: string): string {
  return labZones.includes(browserZone) ? browserZone : (labZones[0] ?? DEFAULT_LAB_TIMEZONE);
}
