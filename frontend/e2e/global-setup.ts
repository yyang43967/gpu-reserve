import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { writeFileSync } from "node:fs";

import {
  API_URL,
  COMPOSE_FILE,
  LAB_TIMEZONE,
  OPEN_SERVER,
  REPO_ROOT,
  RESTRICTED_SERVER,
  STATE_FILE,
  type E2eState,
} from "./env";

function compose(args: string[], env: Record<string, string> = {}): void {
  execFileSync("docker", ["compose", "-f", COMPOSE_FILE, ...args], {
    cwd: REPO_ROOT,
    stdio: "inherit",
    env: { ...process.env, ...env },
  });
}

async function api<T>(path: string, body: unknown, token?: string): Promise<T> {
  const response = await fetch(`${API_URL}/api/v1${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`${path} failed with ${String(response.status)}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

/**
 * Bring the end-to-end database to a known state: latest migrations, no data, one
 * admin, and one lab with an open server and a server that needs approval.
 */
export default async function globalSetup(): Promise<void> {
  const state: E2eState = {
    adminEmail: "e2e-admin@example.edu",
    adminPassword: randomBytes(18).toString("base64url"),
    runId: randomBytes(4).toString("hex"),
  };

  compose(["exec", "-T", "api", "alembic", "upgrade", "head"]);
  compose(["exec", "-T", "api", "python", "-m", "app.cli", "reset-database"]);
  compose(
    [
      "exec",
      "-T",
      "-e",
      "ADMIN_PASSWORD",
      "api",
      "python",
      "-m",
      "app.cli",
      "create-admin",
      "--email",
      state.adminEmail,
      "--name",
      "E2E Admin",
    ],
    { ADMIN_PASSWORD: state.adminPassword },
  );

  const { access_token: token } = await api<{ access_token: string }>("/auth/login", {
    email: state.adminEmail,
    password: state.adminPassword,
  });
  const lab = await api<{ id: string }>(
    "/labs",
    { name: "E2E Lab", location: "Building 1", timezone: LAB_TIMEZONE },
    token,
  );
  const server = (hostname: string, gpu_model: string, requires_approval: boolean) =>
    api(
      "/servers",
      {
        lab_id: lab.id,
        hostname,
        gpu_model,
        gpu_count: 4,
        cpu_cores: 32,
        ram_gb: 256,
        status: "available",
        requires_approval,
        max_booking_hours: 12,
      },
      token,
    );
  await server(OPEN_SERVER, "A100_80GB", false);
  await server(RESTRICTED_SERVER, "H100_80GB", true);

  writeFileSync(STATE_FILE, JSON.stringify(state), { mode: 0o600 });
}
