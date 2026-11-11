import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PresignResponse } from "@/lib/api-types";
import { getAccessToken } from "@/lib/auth-token";
import {
  demoLogin,
  fakeApi,
  type FakeRequest,
  makeUser,
  on,
  emptyDashboard,
  problem,
  signedInAs,
  tokenResponse,
} from "@/test/fake-api";
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
    fakeApi(...signedInAs("student"), emptyDashboard);
    renderRoute("/");

    expect(await screen.findByRole("link", { name: "Account settings" })).toHaveAttribute(
      "href",
      "/account",
    );
  });
});

describe("demo account", () => {
  it("explains that its profile cannot be changed and offers no forms", async () => {
    fakeApi(demoLogin("ada@example.edu"), ...signedInAs("student"));
    renderRoute("/account");

    expect(await screen.findByRole("heading", { name: "Demo account" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Full name")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /password/i })).not.toBeInTheDocument();
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

const STORAGE = "http://storage.test";
const MIB = 1024 * 1024;
const OLD_AVATAR = `${STORAGE}/uploads/avatars/user-1/old.png?sig=1`;

const ticket: PresignResponse = {
  key: "avatars/user-1/new.png",
  url: `${STORAGE}/uploads`,
  fields: { key: "avatars/user-1/new.png", "Content-Type": "image/png", policy: "p" },
  max_bytes: 2 * MIB,
  expires_at: "2026-11-06T10:15:00+00:00",
};

const presign = on("POST", "/uploads/presign", () => Response.json(ticket));
const storageOk = on("POST", "/uploads", () => new Response(null, { status: 204 }));

/** PATCH /me that applies the avatar key the way the API does. */
const saveAvatar = on("PATCH", "/me", (request) => {
  const { avatar_key } = request.body as { avatar_key: string | null };
  return Response.json(
    makeUser({
      avatar_key,
      avatar_url: avatar_key === null ? null : `${STORAGE}/uploads/${avatar_key}?sig=1`,
    }),
  );
});

function imageFile(size = 1000, type = "image/png") {
  const file = new File(["x"], "me.png", { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

function chooseImage(file: File) {
  fireEvent.change(screen.getByLabelText("Profile photo"), { target: { files: [file] } });
}

function requestsTo(requests: FakeRequest[], method: string, path: string) {
  return requests.filter((r) => r.method === method && r.path === path);
}

function headerAvatar() {
  return within(screen.getByRole("link", { name: "Account settings" })).getByTestId("user-avatar");
}

async function openAccount() {
  await screen.findByRole("heading", { name: "Account" });
  return screen.getByRole("button", { name: "Save photo" });
}

describe("profile photo", () => {
  it("uploads, saves, and shows the photo in the header", async () => {
    const requests = fakeApi(...signedInAs("student"), presign, storageOk, saveAvatar);
    renderRoute("/account");
    const save = await openAccount();
    expect(save).toBeDisabled();
    expect(headerAvatar()).toHaveTextContent("AL");

    chooseImage(imageFile());
    await screen.findByText("PNG, JPEG, or WebP.");
    await waitFor(() => {
      expect(save).toBeEnabled();
    });
    fireEvent.click(save);

    expect(await screen.findByText("Your photo has been saved.")).toBeInTheDocument();
    expect(requestsTo(requests, "POST", "/uploads/presign")[0]?.body).toEqual({
      purpose: "avatar",
      content_type: "image/png",
      size_bytes: 1000,
    });
    expect(requestsTo(requests, "PATCH", "/me")[0]?.body).toEqual({
      avatar_key: "avatars/user-1/new.png",
    });
    const newUrl = `${STORAGE}/uploads/avatars/user-1/new.png?sig=1`;
    expect(screen.getByRole("img", { name: "Your profile photo" })).toHaveAttribute("src", newUrl);
    expect(headerAvatar().querySelector("img")).toHaveAttribute("src", newUrl);
    expect(save).toBeDisabled();
  });

  it("removes the photo", async () => {
    const requests = fakeApi(
      ...signedInAs("student", { avatar_key: "avatars/user-1/old.png", avatar_url: OLD_AVATAR }),
      saveAvatar,
    );
    renderRoute("/account");
    const save = await openAccount();
    expect(screen.getByRole("img", { name: "Your profile photo" })).toHaveAttribute(
      "src",
      OLD_AVATAR,
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove image" }));
    expect(screen.queryByRole("img", { name: "Your profile photo" })).not.toBeInTheDocument();
    fireEvent.click(save);

    expect(await screen.findByText("Your photo has been removed.")).toBeInTheDocument();
    expect(requestsTo(requests, "PATCH", "/me")[0]?.body).toEqual({ avatar_key: null });
    expect(headerAvatar()).toHaveTextContent("AL");
    expect(headerAvatar().querySelector("img")).toBeNull();
  });

  it("keeps saving disabled when the file is refused", async () => {
    fakeApi(...signedInAs("student"));
    renderRoute("/account");
    const save = await openAccount();

    chooseImage(imageFile(1000, "image/gif"));
    await screen.findByText("Choose a PNG, JPEG, or WebP image.");
    expect(save).toBeDisabled();
  });

  it("checks the file against the avatar limit before asking to upload it", async () => {
    const requests = fakeApi(...signedInAs("student"), presign);
    renderRoute("/account");
    await openAccount();

    chooseImage(imageFile(2 * MIB + 1));

    expect(await screen.findByText("The image must be 2 MiB or smaller.")).toBeInTheDocument();
    expect(requests.filter((r) => r.path.startsWith("/uploads"))).toHaveLength(0);
  });

  it("disables saving while the upload is in progress", async () => {
    let finish: (response: Response) => void = () => undefined;
    fakeApi(
      ...signedInAs("student"),
      presign,
      on("POST", "/uploads", () => new Promise<Response>((resolve) => (finish = resolve))),
    );
    renderRoute("/account");
    const save = await openAccount();

    chooseImage(imageFile());
    expect(await screen.findByText("Uploading…")).toBeInTheDocument();
    expect(save).toBeDisabled();

    finish(new Response(null, { status: 204 }));
    await waitFor(() => {
      expect(save).toBeEnabled();
    });
  });

  it("shows a storage failure as a readable message", async () => {
    fakeApi(
      ...signedInAs("student"),
      presign,
      on(
        "POST",
        "/uploads",
        () =>
          new Response("<?xml version='1.0'?><Error><Code>AccessDenied</Code></Error>", {
            status: 403,
            headers: { "Content-Type": "application/xml" },
          }),
      ),
    );
    renderRoute("/account");
    const save = await openAccount();

    chooseImage(imageFile());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The upload failed (status 403). Try again.");
    expect(alert.textContent).not.toMatch(/<|AccessDenied/);
    expect(save).toBeDisabled();
  });

  it("shows the API's refusal to save the upload", async () => {
    fakeApi(
      ...signedInAs("student"),
      presign,
      storageOk,
      on("PATCH", "/me", () =>
        problem(422, "upload-not-found", { detail: "The uploaded file was not found." }),
      ),
    );
    renderRoute("/account");
    const save = await openAccount();

    chooseImage(imageFile());
    await waitFor(() => {
      expect(save).toBeEnabled();
    });
    fireEvent.click(save);

    expect(await screen.findByRole("alert")).toHaveTextContent("The uploaded file was not found.");
    expect(headerAvatar()).toHaveTextContent("AL");
  });

  it("frees the local preview after saving", async () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:avatar/0");
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    fakeApi(...signedInAs("student"), presign, storageOk, saveAvatar);
    renderRoute("/account");
    const save = await openAccount();

    chooseImage(imageFile());
    await waitFor(() => {
      expect(save).toBeEnabled();
    });
    expect(screen.getByRole("img", { name: "Your profile photo" })).toHaveAttribute(
      "src",
      "blob:avatar/0",
    );
    fireEvent.click(save);

    await screen.findByText("Your photo has been saved.");
    expect(revoke).toHaveBeenCalledWith("blob:avatar/0");
  });
});

describe("header avatar", () => {
  it("falls back to initials when the photo fails to load", async () => {
    fakeApi(
      ...signedInAs("student", { avatar_key: "avatars/user-1/old.png", avatar_url: OLD_AVATAR }),
    );
    renderRoute("/account");
    await openAccount();

    const image = headerAvatar().querySelector("img");
    expect(image).toHaveAttribute("src", OLD_AVATAR);
    if (image) fireEvent.error(image);

    expect(headerAvatar()).toHaveTextContent("AL");
  });
});
