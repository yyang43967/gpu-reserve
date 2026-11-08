import { fileURLToPath } from "node:url";

/** The API of the end-to-end stack (docker-compose.e2e.yml). */
export const API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:18000";
/** Mailpit's web API in that stack. */
export const MAILPIT_URL = process.env.E2E_MAILPIT_URL ?? "http://127.0.0.1:18025";
/** Where `vite preview` serves the built frontend. */
export const APP_URL = "http://localhost:4173";

export const REPO_ROOT = fileURLToPath(new URL("../..", import.meta.url));
export const COMPOSE_FILE = "docker-compose.e2e.yml";
/** Written by global setup, read by the specs. Ignored by Git. */
export const STATE_FILE = fileURLToPath(new URL("./.state.json", import.meta.url));

export const LAB_TIMEZONE = "America/Los_Angeles";
export const OPEN_SERVER = "e2e-a100-01";
export const RESTRICTED_SERVER = "e2e-h100-01";

export interface E2eState {
  adminEmail: string;
  adminPassword: string;
  /** Unique per run, so emails and accounts never collide with an earlier run's. */
  runId: string;
}
