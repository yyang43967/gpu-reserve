import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { getAccessToken } from "@/lib/auth-token";
import { fakeApi, makeUser, on, problem, signedInAs, tokenResponse } from "@/test/fake-api";
import { renderRoute } from "@/test/render";

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("account page", () => {
  it("shows the profile", async () => {
    fakeApi(...signedInAs("student"));
    renderRoute("/account");

    expect(await screen.findByRole("heading", { name: "Account" })).toBeInTheDocument();
    expect(screen.getByText("ada@example.edu")).toBeInTheDocument();
    expect(screen.getByText("48 GPU-hours")).toBeInTheDocument();
    expect(screen.getByLabelText("Full name")).toHaveValue("Ada Lovelace");
  });

  it("is linked from the header", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("GET", "/health", () => Response.json({ status: "ok" })),
    );
    renderRoute("/");

    expect(await screen.findByRole("link", { name: "Account settings" })).toHaveAttribute(
      "href",
      "/account",
    );
  });
});

describe("profile form", () => {
  it("saves the name and updates the header", async () => {
    const requests = fakeApi(
      ...signedInAs("student"),
      on("PATCH", "/me", () => Response.json(makeUser({ full_name: "Ada King" }))),
    );
    renderRoute("/account");
    await screen.findByRole("heading", { name: "Account" });

    type("Full name", "Ada King");
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));

    expect(await screen.findByText("Your name has been saved.")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PATCH")?.body).toEqual({ full_name: "Ada King" });
    expect(screen.getByRole("link", { name: "Account settings" })).toHaveTextContent("Ada King");
  });

  it("keeps save disabled until the name changes", async () => {
    fakeApi(...signedInAs("student"));
    renderRoute("/account");

    expect(await screen.findByRole("button", { name: "Save name" })).toBeDisabled();
  });

  it("requires a name", async () => {
    fakeApi(...signedInAs("student"));
    renderRoute("/account");
    await screen.findByRole("heading", { name: "Account" });

    type("Full name", "   ");
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));

    expect(await screen.findByText("Enter your name.")).toBeInTheDocument();
  });
});

describe("password form", () => {
  async function submitPasswords(current: string, next: string, confirm = next) {
    await screen.findByRole("heading", { name: "Account" });
    type("Current password", current);
    type("New password", next);
    type("Confirm new password", confirm);
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
  }

  it("changes the password and keeps the new session", async () => {
    const requests = fakeApi(
      ...signedInAs("student"),
      on("PUT", "/me/password", () => Response.json(tokenResponse(makeUser(), "after-change"))),
    );
    renderRoute("/account");

    await submitPasswords("old-password-1", "new-password-1");

    expect(
      await screen.findByText("Password changed. You have been signed out on other devices."),
    ).toBeInTheDocument();
    expect(getAccessToken()).toBe("after-change");
    expect(requests.find((r) => r.path === "/me/password")?.body).toEqual({
      current_password: "old-password-1",
      new_password: "new-password-1",
    });
    expect(screen.getByLabelText("Current password")).toHaveValue("");
  });

  it("checks the confirmation locally", async () => {
    const requests = fakeApi(...signedInAs("student"));
    renderRoute("/account");

    await submitPasswords("old-password-1", "new-password-1", "new-password-2");

    expect(await screen.findByText("The passwords do not match.")).toBeInTheDocument();
    expect(requests.some((r) => r.path === "/me/password")).toBe(false);
  });

  it("rejects reusing the current password locally", async () => {
    fakeApi(...signedInAs("student"));
    renderRoute("/account");

    await submitPasswords("same-password-1", "same-password-1");

    expect(
      await screen.findByText("Choose a password different from the current one."),
    ).toBeInTheDocument();
  });

  it("shows a wrong current password under that field", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("PUT", "/me/password", () =>
        problem(422, "incorrect-password", { detail: "The current password is incorrect." }),
      ),
    );
    renderRoute("/account");

    await submitPasswords("wrong-password", "new-password-1");

    const field = screen.getByLabelText("Current password");
    await waitFor(() => {
      expect(field).toHaveAccessibleDescription("The current password is incorrect.");
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("explains when too many attempts were made", async () => {
    fakeApi(
      ...signedInAs("student"),
      on("PUT", "/me/password", () =>
        problem(429, "too-many-password-attempts", { headers: { "Retry-After": "600" } }),
      ),
    );
    renderRoute("/account");

    await submitPasswords("wrong-password", "new-password-1");

    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 10 minutes.");
  });
});
