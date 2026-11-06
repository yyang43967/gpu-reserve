import type { Page } from "@/lib/api-types";

/** Number of pages in a listing; an empty listing still has one. */
export function pageCount(data: Page<unknown>): number {
  return Math.max(1, Math.ceil(data.total / data.page_size));
}
