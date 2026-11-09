import { describe, expect, it } from "vitest";

import { readEvents } from "./sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(chunks: string[]): Promise<unknown[]> {
  const events: unknown[] = [];
  for await (const event of readEvents(streamOf(chunks))) events.push(event);
  return events;
}

describe("readEvents", () => {
  it("reads frames split across chunks and several in one chunk", async () => {
    const events = await collect([
      'data: {"type":"tok',
      'en","text":"Hel"}\n\ndata: {"type":"token","text":"lo"}\n',
      '\ndata: {"type":"done","content":"Hello"}\n\n',
    ]);

    expect(events).toEqual([
      { type: "token", text: "Hel" },
      { type: "token", text: "lo" },
      { type: "done", content: "Hello" },
    ]);
  });

  it("keeps multi-byte characters split between chunks", async () => {
    const bytes = new TextEncoder().encode('data: {"type":"token","text":"café"}\n\n');
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 34));
        controller.enqueue(bytes.slice(34));
        controller.close();
      },
    });
    const events: unknown[] = [];
    for await (const event of readEvents(stream)) events.push(event);

    expect(events).toEqual([{ type: "token", text: "café" }]);
  });
});
