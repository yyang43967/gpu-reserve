/**
 * Reads `data: {json}` frames from a server-sent event stream. Frames can arrive split
 * across chunks, or several in one chunk.
 */
export async function* readEvents<T>(body: ReadableStream<Uint8Array>): AsyncGenerator<T> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const data = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) yield JSON.parse(data) as T;
        end = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}
