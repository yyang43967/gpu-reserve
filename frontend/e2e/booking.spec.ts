import { randomBytes } from "node:crypto";

import { expect, test } from "@playwright/test";

import { OPEN_SERVER, RESTRICTED_SERVER } from "./env";
import { register, signIn, state, tomorrowInLab, uniqueEmail, waitForEmail } from "./helpers";

test("a new student finds a slot, books it, and sees it in My reservations", async ({ page }) => {
  const password = randomBytes(12).toString("base64url");
  await register(page, "Edsger Dijkstra", uniqueEmail("finder"), password);

  await page.getByRole("link", { name: "Find a slot" }).first().click();
  const search = page.getByRole("form", { name: "Search for a slot" });
  const day = tomorrowInLab();
  await search.getByLabel("GPUs").fill("2");
  await search.getByLabel("Hours").fill("1");
  await search.getByLabel("From date").fill(day);
  await search.getByLabel("From time").selectOption("19:00");
  await search.getByLabel("To date").fill(day);
  await search.getByLabel("To time").selectOption("22:00");
  // The browser runs in Tokyo; the window is typed in the lab's zone.
  await expect(search.getByLabel("Time zone")).toHaveValue("America/Los_Angeles");
  await search.getByRole("button", { name: "Search" }).click();

  const results = page.getByRole("list", { name: "Matching slots" });
  const first = results.getByRole("listitem").first();
  // Open servers rank before ones that need approval at the same start.
  await expect(first).toContainText(OPEN_SERVER);
  await expect(first).toContainText(/19:00 P[SD]T – 20:00 P[SD]T/);
  await first.getByRole("button", { name: "Book" }).click();

  const dialog = page.getByRole("dialog", { name: `Book ${OPEN_SERVER}` });
  await expect(dialog.getByLabel("Start time")).toHaveValue("19:00");
  await expect(dialog.getByLabel("End time")).toHaveValue("20:00");
  await expect(dialog.getByLabel("GPUs")).toHaveValue("2");
  await dialog.getByLabel("Purpose").fill("End-to-end test: fine-tuning run");
  await dialog.getByRole("button", { name: "Book" }).click();
  await expect(page.getByText(`Booked ${OPEN_SERVER} from`)).toBeVisible();

  await page.getByRole("link", { name: "See my reservations" }).click();
  await expect(page.getByRole("heading", { name: "My reservations" })).toBeVisible();
  const item = page.getByRole("listitem", { name: new RegExp(OPEN_SERVER) });
  await expect(item).toContainText("Approved");
  await expect(item).toContainText("End-to-end test: fine-tuning run");
  await expect(item).toContainText(/19:00 P[SD]T – 20:00 P[SD]T/);
});

test("an admin approves a restricted booking and the student is emailed", async ({ browser }) => {
  const studentEmail = uniqueEmail("approval");
  const password = randomBytes(12).toString("base64url");
  const student = await browser.newPage();
  await register(student, "Radia Perlman", studentEmail, password);

  await student.getByRole("link", { name: "Servers" }).first().click();
  await student
    .getByRole("link", { name: new RegExp(RESTRICTED_SERVER) })
    .first()
    .click();
  await student.getByRole("button", { name: "Book GPUs" }).click();
  const dialog = student.getByRole("dialog", { name: `Book ${RESTRICTED_SERVER}` });
  await dialog.getByLabel("Start date").fill(tomorrowInLab());
  await dialog.getByLabel("Start time").selectOption("20:00");
  await dialog.getByLabel("End time").selectOption("21:00");
  await dialog.getByLabel("Purpose").fill("End-to-end test: needs approval");
  await dialog.getByRole("button", { name: "Book" }).click();
  await expect(
    student.getByText(/Requested 1 GPUs from .*An admin will review it\./),
  ).toBeVisible();

  const admin = await browser.newPage();
  const { adminEmail, adminPassword } = state();
  await signIn(admin, adminEmail, adminPassword);
  await admin.goto("/admin/approvals");
  const request = admin.getByRole("listitem", {
    name: new RegExp(`Radia Perlman, ${RESTRICTED_SERVER}`),
  });
  await request.getByRole("button", { name: "Approve" }).click();
  await expect(admin.getByText("Approved Radia Perlman's booking from")).toBeVisible();

  const email = await waitForEmail(studentEmail, `Approved: ${RESTRICTED_SERVER}`);
  expect(email.To.map((t) => t.Address)).toEqual([studentEmail]);

  await student.goto("/reservations");
  await expect(student.getByRole("heading", { name: "My reservations" })).toBeVisible();
  await expect(
    student.getByRole("listitem", { name: new RegExp(RESTRICTED_SERVER) }),
  ).toContainText("Approved");
});
