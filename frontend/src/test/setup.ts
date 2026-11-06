import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { setAccessToken } from "@/lib/auth-token";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  // The token lives in module state; one test's session must not leak into the next.
  setAccessToken(null);
});
