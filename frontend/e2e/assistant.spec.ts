import { randomBytes } from "node:crypto";

import { expect, test } from "@playwright/test";

import { OPEN_SERVER } from "./env";
import { register, uniqueEmail } from "./helpers";

// The stack runs the assistant with a scripted model (ASSISTANT_MODEL=scripted:e2e): asked
// to book, it proposes one GPU on the first open server tomorrow 19:00-20:00 lab time.
test("the assistant books only after the student confirms on the card", async ({ page }) => {
  const password = randomBytes(12).toString("base64url");
  await register(page, "Barbara Liskov", uniqueEmail("assistant"), password);

  await page.getByRole("link", { name: "Assistant" }).first().click();
  const message = page.getByLabel("Message");
  await message.fill("Please book a GPU for me");
  await message.press("Enter");

  const card = page.getByRole("region", { name: "Confirm this booking" });
  await expect(card).toBeVisible();
  await expect(card).toContainText(`${OPEN_SERVER} · E2E Lab`);
  await expect(card).toContainText(/19:00 P[SD]T – 20:00 P[SD]T/);
  await expect(card).toContainText("Approved immediately");

  // Nothing is booked yet.
  await page.goto("/reservations");
  await expect(page.getByText("No reservations yet")).toBeVisible();

  // The waiting confirmation survives leaving the page and coming back.
  await page.goBack();
  await expect(card).toBeVisible();
  await page.reload();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Confirm booking" }).click();

  await expect(page.getByText(`Booked ${OPEN_SERVER}.`)).toBeVisible();
  await expect(card).toBeHidden();

  await page.getByRole("link", { name: "My reservations" }).first().click();
  const item = page.getByRole("listitem", { name: new RegExp(OPEN_SERVER) });
  await expect(item).toContainText("Approved");
  await expect(item).toContainText("Assistant end-to-end test booking");
  await expect(item).toContainText(/19:00 P[SD]T – 20:00 P[SD]T/);
});
