import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  AssistantEvent,
  ChatMessage,
  PendingAction,
  Thread,
  ThreadMessages,
} from "@/assistant/assistant-api";
import { fakeApi, on, page, problem, signedInAs } from "@/test/fake-api";
import { renderRoute } from "@/test/render";

function thread(overrides: Partial<Thread> = {}): Thread {
  return {
    id: "t-1",
    title: "Booking help",
    created_at: "2030-01-12T17:00:00Z",
    updated_at: "2030-01-12T17:05:00Z",
    has_pending_action: false,
    ...overrides,
  };
}

const booking: PendingAction = {
  action: "create_reservation",
  tool_call_id: "call_1",
  summary: {
    server_id: "s-1",
    server: "a100-node-01",
    lab: "Vision Lab",
    timezone: "America/Los_Angeles",
    gpus: 2,
    gpu_model: "A100_80GB",
    start: "2030-01-12T19:00:00-08:00",
    end: "2030-01-12T21:00:00-08:00",
    status_after: "approved",
    quota_after: {
      week_start: "2030-01-07T00:00:00-08:00",
      week_end: "2030-01-14T00:00:00-08:00",
      used_gpu_hours: 16,
      quota_gpu_hours: 48,
      remaining_gpu_hours: 32,
    },
    purpose: "LoRA fine-tuning",
  },
};

function message(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role: "user",
    content: "",
    meta: {},
    created_at: "2030-01-12T17:00:00Z",
    ...overrides,
  };
}

function frames(events: AssistantEvent[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

function sse(events: AssistantEvent[]): Response {
  return new Response(frames(events), { headers: { "Content-Type": "text/event-stream" } });
}

/** A stream that sends `first`, then waits for `release()` before sending `rest`. */
function heldStream(first: AssistantEvent[], rest: AssistantEvent[]) {
  const encoder = new TextEncoder();
  let release: () => void = () => undefined;
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      controller.enqueue(encoder.encode(frames(first)));
      await released;
      controller.enqueue(encoder.encode(frames(rest)));
      controller.close();
    },
  });
  return {
    response: new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
    release: () => {
      release();
    },
  };
}

function conversation(
  messages: ChatMessage[],
  pending: PendingAction | null = null,
): ThreadMessages {
  return { thread: thread(), messages, pending_action: pending };
}

describe("assistant page", () => {
  it("shows an empty state, then a streamed answer with steps and sources", async () => {
    let saved = conversation([]);
    const held = heldStream(
      [
        { type: "status", state: "started" },
        { type: "tool_start", name: "search_policies", label: "Searching the policies" },
        { type: "tool_end", name: "search_policies", label: "Searching the policies", ok: true },
        { type: "token", text: "You get 48 " },
      ],
      [
        { type: "token", text: "GPU-hours a week [1]." },
        {
          type: "citation",
          ref: 1,
          document: "fair-use-and-quota",
          title: "Fair use",
          heading: "Your weekly quota",
        },
        { type: "done", content: "You get 48 GPU-hours a week [1]." },
      ],
    );
    const requests = fakeApi(
      ...signedInAs("student"),
      on("GET", "/assistant/threads", () => Response.json(page([] as Thread[]))),
      on("POST", "/assistant/threads", () => Response.json(thread(), { status: 201 })),
      on("GET", "/assistant/threads/t-1/messages", () => Response.json(saved)),
      on("POST", "/assistant/threads/t-1/messages", () => {
        saved = conversation([
          message({ role: "user", content: "How many hours do I get?" }),
          message({
            role: "assistant",
            content: "You get 48 GPU-hours a week [1].",
            meta: {
              tools: [{ name: "search_policies", label: "Searching the policies", ok: true }],
              citations: [
                {
                  ref: 1,
                  document: "fair-use-and-quota",
                  title: "Fair use",
                  heading: "Your weekly quota",
                },
              ],
            },
          }),
        ]);
        return held.response;
      }),
    );
    renderRoute("/assistant");

    expect(await screen.findByText("No conversations yet.")).toBeInTheDocument();
    expect(screen.getByText("Ask about policies or book GPUs.")).toBeInTheDocument();

    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "How many hours do I get?" } });
    fireEvent.keyDown(input, { key: "Enter" });

    // The first message created the conversation; the answer keeps streaming into the
    // same view rather than being cut off when the address changes.
    expect(await screen.findByText("You get 48")).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeDisabled();
    held.release();

    await screen.findByText("You get 48 GPU-hours a week [1].");
    const answer = screen.getByRole("listitem", { name: "Assistant" });
    expect(within(answer).getByText("Searching the policies")).toBeInTheDocument();
    expect(within(answer).getByText("[1] Fair use › Your weekly quota")).toBeInTheDocument();

    const sent = requests.find((r) => r.method === "POST" && r.path.endsWith("/messages"));
    expect(sent?.body).toEqual({ content: "How many hours do I get?" });
    expect(sent?.headers.get("Accept")).toBe("text/event-stream");
    expect(sent?.headers.get("Authorization")).toBe("Bearer access-token");
  });

  it("re-renders a waiting confirmation after a reload and confirms it", async () => {
    let saved = conversation(
      [message({ role: "user", content: "Book 2 A100s tonight 19-21" })],
      booking,
    );
    const requests = fakeApi(
      ...signedInAs("student"),
      on("GET", "/assistant/threads", () =>
        Response.json(page([thread({ has_pending_action: saved.pending_action !== null })])),
      ),
      on("GET", "/assistant/threads/t-1/messages", () => Response.json(saved)),
      on("POST", "/assistant/threads/t-1/resume", () => {
        saved = conversation([
          ...saved.messages,
          message({
            role: "user",
            content: "Confirmed",
            meta: { kind: "decision", decision: "confirm" },
          }),
          message({ role: "assistant", content: "Booked a100-node-01." }),
        ]);
        return sse([
          { type: "status", state: "started" },
          { type: "token", text: "Booked a100-node-01." },
          { type: "done", content: "Booked a100-node-01." },
        ]);
      }),
    );
    renderRoute("/assistant?thread=t-1");

    const card = await screen.findByRole("region", { name: "Confirm this booking" });
    expect(within(card).getByText("a100-node-01 · Vision Lab")).toBeInTheDocument();
    expect(within(card).getByText("2 × A100 80GB")).toBeInTheDocument();
    expect(within(card).getByText("Sat, Jan 12, 19:00 PST – 21:00 PST")).toBeInTheDocument();
    expect(within(card).getByText("Approved immediately")).toBeInTheDocument();
    expect(
      within(card).getByText("16 of 48 GPU-hours used that week, 32 left"),
    ).toBeInTheDocument();
    expect(screen.getByText("Waiting")).toBeInTheDocument();

    fireEvent.click(within(card).getByRole("button", { name: "Confirm booking" }));

    expect(await screen.findByText("Booked a100-node-01.")).toBeInTheDocument();
    expect(screen.getByText("You confirmed.")).toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.queryByRole("region", { name: "Confirm this booking" }),
      ).not.toBeInTheDocument();
    });
    const resume = requests.find((r) => r.path === "/assistant/threads/t-1/resume");
    expect(resume?.body).toEqual({ decision: "confirm" });
  });

  it("shows the card when a run stops for confirmation", async () => {
    let saved = conversation([]);
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/assistant/threads", () => Response.json(page([thread()]))),
      on("GET", "/assistant/threads/t-1/messages", () => Response.json(saved)),
      on("POST", "/assistant/threads/t-1/messages", () => {
        saved = conversation([message({ role: "user", content: "Book it" })], booking);
        return sse([
          { type: "status", state: "started" },
          { type: "tool_start", name: "propose_reservation", label: "Checking the booking" },
          { type: "confirmation_required", payload: booking },
        ]);
      }),
    );
    renderRoute("/assistant?thread=t-1");

    const input = await screen.findByLabelText("Message");
    fireEvent.change(input, { target: { value: "Book it" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const card = await screen.findByRole("region", { name: "Confirm this booking" });
    expect(within(card).getByRole("button", { name: "Don't book" })).toBeEnabled();
    expect(
      screen.getByText("Sending a message cancels the waiting confirmation."),
    ).toBeInTheDocument();
  });

  it("shows a refused message and gives the text back", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/assistant/threads", () => Response.json(page([thread()]))),
      on("GET", "/assistant/threads/t-1/messages", () => Response.json(conversation([]))),
      on("POST", "/assistant/threads/t-1/messages", () =>
        problem(429, "too-many-requests", {
          detail: "Too many messages to the assistant. Try again later.",
          headers: { "Retry-After": "30" },
        }),
      ),
    );
    renderRoute("/assistant?thread=t-1");

    const input = await screen.findByLabelText("Message");
    fireEvent.change(input, { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/too many requests/i);
    expect(screen.getByLabelText("Message")).toHaveValue("hello");
  });

  it("shows an error when conversations cannot be loaded", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/assistant/threads", () => problem(500, "internal-error")),
    );
    renderRoute("/assistant");

    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
