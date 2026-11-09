import { apiFetch, apiStream } from "@/lib/api";
import type { Page } from "@/lib/api-types";

import { readEvents } from "./sse";

export const MESSAGE_MAX_LENGTH = 2000;

export interface Thread {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  has_pending_action: boolean;
}

export interface ToolStep {
  name: string;
  label: string;
  ok: boolean;
}

export interface Citation {
  ref: number;
  document: string;
  title: string;
  heading: string;
}

export interface QuotaAfter {
  week_start: string;
  week_end: string;
  used_gpu_hours: number;
  quota_gpu_hours: number;
  remaining_gpu_hours: number;
}

export interface BookingSummary {
  server_id: string;
  server: string;
  lab: string;
  timezone: string;
  gpus: number;
  gpu_model: string;
  start: string;
  end: string;
  status_after: "approved" | "pending";
  quota_after: QuotaAfter;
  purpose: string;
}

export interface CancellationSummary {
  reservation_id: string;
  server: string;
  lab: string;
  timezone: string;
  gpus: number;
  start: string;
  end: string;
  effect: "cancel" | "end_early";
  new_end: string | null;
  quota_after: QuotaAfter;
}

export type PendingAction =
  | { action: "create_reservation"; tool_call_id: string; summary: BookingSummary }
  | { action: "cancel_reservation"; tool_call_id: string; summary: CancellationSummary };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  meta: {
    tools?: ToolStep[];
    citations?: Citation[];
    kind?: "decision";
    decision?: "confirm" | "cancel";
  };
  created_at: string;
}

export interface ThreadMessages {
  thread: Thread;
  messages: ChatMessage[];
  pending_action: PendingAction | null;
}

export type AssistantEvent =
  | { type: "status"; state: string }
  | { type: "tool_start"; name: string; label: string }
  | { type: "tool_end"; name: string; label: string; ok: boolean }
  | { type: "token"; text: string }
  | ({ type: "citation" } & Citation)
  | { type: "confirmation_required"; payload: PendingAction }
  | { type: "done"; content: string }
  | { type: "error"; message: string };

export const assistantKey = ["assistant"] as const;
export const threadsKey = [...assistantKey, "threads"] as const;
export const threadMessagesKey = (threadId: string) =>
  [...assistantKey, "messages", threadId] as const;

export function listThreads(signal?: AbortSignal): Promise<Page<Thread>> {
  return apiFetch<Page<Thread>>("/assistant/threads?page_size=50", { signal });
}

export function createThread(): Promise<Thread> {
  return apiFetch<Thread>("/assistant/threads", { method: "POST", body: {} });
}

export function getThreadMessages(threadId: string, signal?: AbortSignal): Promise<ThreadMessages> {
  return apiFetch<ThreadMessages>(`/assistant/threads/${threadId}/messages`, { signal });
}

async function* stream(path: string, body: unknown, signal?: AbortSignal) {
  const response = await apiStream(path, body, signal);
  if (!response.body) return;
  yield* readEvents<AssistantEvent>(response.body);
}

export function sendMessage(threadId: string, content: string, signal?: AbortSignal) {
  return stream(`/assistant/threads/${threadId}/messages`, { content }, signal);
}

export function resumeThread(
  threadId: string,
  decision: "confirm" | "cancel",
  signal?: AbortSignal,
) {
  return stream(`/assistant/threads/${threadId}/resume`, { decision }, signal);
}
