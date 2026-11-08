import { readFileSync } from "node:fs";

import { expect, type Page } from "@playwright/test";

import { LAB_TIMEZONE, MAILPIT_URL, STATE_FILE, type E2eState } from "./env";

export function state(): E2eState {
  return JSON.parse(readFileSync(STATE_FILE, "utf8")) as E2eState;
}

/**
 * Tomorrow's date in the lab, as the date inputs want it. Bookings in the specs start
 * tomorrow at 19:00 or later lab time: always in the future, inside the 14-day horizon,
 * outside peak hours, and a few GPU-hours, well within any quota.
 */
export function tomorrowInLab(now: Date = new Date()): string {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: LAB_TIMEZONE }).format(now);
  const [year, month, day] = today.split("-").map(Number) as [number, number, number];
  return new Date(Date.UTC(year, month - 1, day + 1)).toISOString().slice(0, 10);
}

export function uniqueEmail(label: string): string {
  return `${label}-${state().runId}-${String(Date.now())}@example.edu`;
}

export async function register(page: Page, fullName: string, email: string, password: string) {
  await page.goto("/register");
  await page.getByLabel("Full name").fill(fullName);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

export async function signIn(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

interface MailpitMessage {
  Subject: string;
  To: { Address: string }[];
}

/** Wait for a message to `to` whose subject starts with `prefix`; the worker sends
 * notifications on its next minute tick. */
export async function waitForEmail(to: string, prefix: string): Promise<MailpitMessage> {
  let found: MailpitMessage | undefined;
  await expect
    .poll(
      async () => {
        const response = await fetch(
          `${MAILPIT_URL}/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`,
        );
        const body = (await response.json()) as { messages: MailpitMessage[] };
        found = body.messages.find((m) => m.Subject.startsWith(prefix));
        return found !== undefined;
      },
      { timeout: 130_000, intervals: [2_000] },
    )
    .toBe(true);
  if (!found) throw new Error("unreachable");
  return found;
}
