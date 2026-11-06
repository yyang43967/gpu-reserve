import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { setAccessToken } from "@/lib/auth-token";

// jsdom has no layout, so it leaves out ResizeObserver. Radix measures some controls
// (the switch thumb) with it; a no-op is enough because nothing here depends on sizes.
if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {
      /* no layout in jsdom */
    }
    unobserve() {
      /* no layout in jsdom */
    }
    disconnect() {
      /* no layout in jsdom */
    }
  };
}

// jsdom has no object URLs. Stand-ins let image previews render; tests spy on them.
if (!("createObjectURL" in URL)) {
  let next = 0;
  Object.assign(URL, {
    createObjectURL: () => `blob:test/${String(++next)}`,
    revokeObjectURL: () => undefined,
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  // The token lives in module state; one test's session must not leak into the next.
  setAccessToken(null);
});
