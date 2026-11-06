import { beforeEach, describe, expect, it } from "vitest";

import type { PresignResponse } from "@/lib/api-types";
import { setAccessToken } from "@/lib/auth-token";
import { UploadError, checkImageFile, uploadImage } from "@/lib/uploads";
import { fakeApi, on, problem } from "@/test/fake-api";

const MIB = 1024 * 1024;
const STORAGE_ORIGIN = "http://storage.test";

function file(type: string, size: number, name = "photo.png"): File {
  const image = new File(["x"], name, { type });
  // Avoids allocating megabytes just to test the size checks.
  Object.defineProperty(image, "size", { value: size });
  return image;
}

function ticket(overrides: Partial<PresignResponse> = {}): PresignResponse {
  return {
    key: "avatars/user-1/abc.png",
    url: `${STORAGE_ORIGIN}/uploads`,
    fields: { key: "avatars/user-1/abc.png", "Content-Type": "image/png", policy: "p" },
    max_bytes: 2 * MIB,
    expires_at: "2026-11-06T10:15:00+00:00",
    ...overrides,
  };
}

describe("checkImageFile", () => {
  it("accepts PNG, JPEG, and WebP within the purpose's limit", () => {
    expect(checkImageFile(file("image/png", 2 * MIB), "avatar")).toBeNull();
    expect(checkImageFile(file("image/jpeg", 1000), "avatar")).toBeNull();
    expect(checkImageFile(file("image/webp", 5 * MIB), "server_image")).toBeNull();
  });

  it("rejects other types", () => {
    expect(checkImageFile(file("image/gif", 1000), "avatar")).toBe(
      "Choose a PNG, JPEG, or WebP image.",
    );
    expect(checkImageFile(file("", 1000), "avatar")).toBe("Choose a PNG, JPEG, or WebP image.");
  });

  it("rejects empty files", () => {
    expect(checkImageFile(file("image/png", 0), "avatar")).toBe("The file is empty.");
  });

  it("applies a limit per purpose", () => {
    expect(checkImageFile(file("image/png", 2 * MIB + 1), "avatar")).toBe(
      "The image must be 2 MiB or smaller.",
    );
    expect(checkImageFile(file("image/png", 2 * MIB + 1), "server_image")).toBeNull();
    expect(checkImageFile(file("image/png", 5 * MIB + 1), "server_image")).toBe(
      "The image must be 5 MiB or smaller.",
    );
  });
});

describe("uploadImage", () => {
  beforeEach(() => {
    setAccessToken("access-token");
  });

  it("presigns, posts the form to storage, and returns the key", async () => {
    const requests = fakeApi(
      on("POST", "/uploads/presign", () => Response.json(ticket())),
      on("POST", "/uploads", () => new Response(null, { status: 204 })),
    );
    const image = file("image/png", 1234);

    await expect(uploadImage(image, "avatar")).resolves.toBe("avatars/user-1/abc.png");

    const [presign, upload] = requests;
    expect(presign?.body).toEqual({
      purpose: "avatar",
      content_type: "image/png",
      size_bytes: 1234,
    });

    expect(upload?.origin).toBe(STORAGE_ORIGIN);
    const form = upload?.body as FormData;
    expect([...form.keys()]).toEqual(["key", "Content-Type", "policy", "file"]);
    expect(form.get("Content-Type")).toBe("image/png");
    expect(form.get("file")).toBeInstanceOf(File);
  });

  it("sends no bearer token or cookie to storage", async () => {
    const requests = fakeApi(
      on("POST", "/uploads/presign", () => Response.json(ticket())),
      on("POST", "/uploads", () => new Response(null, { status: 204 })),
    );

    await uploadImage(file("image/png", 10), "avatar");

    const upload = requests[1];
    expect(requests[0]?.headers.get("Authorization")).toBe("Bearer access-token");
    expect(upload?.headers.get("Authorization")).toBeNull();
    expect(upload?.credentials).toBe("omit");
  });

  it("passes on API errors from presigning without contacting storage", async () => {
    const requests = fakeApi(
      on("POST", "/uploads/presign", () =>
        problem(403, "forbidden", { detail: "Only admins can upload server images." }),
      ),
    );

    await expect(uploadImage(file("image/png", 10), "server_image")).rejects.toMatchObject({
      name: "ApiError",
      status: 403,
    });
    expect(requests).toHaveLength(1);
  });

  it("throws an UploadError when storage rejects the form", async () => {
    fakeApi(
      on("POST", "/uploads/presign", () => Response.json(ticket())),
      on("POST", "/uploads", () => new Response("<Error/>", { status: 400 })),
    );

    const failure = uploadImage(file("image/png", 10), "avatar");

    await expect(failure).rejects.toBeInstanceOf(UploadError);
    await expect(failure).rejects.toMatchObject({ status: 400 });
  });

  it("throws an UploadError when storage cannot be reached", async () => {
    fakeApi(
      on("POST", "/uploads/presign", () => Response.json(ticket())),
      on("POST", "/uploads", () => {
        throw new TypeError("Failed to fetch");
      }),
    );

    await expect(uploadImage(file("image/png", 10), "avatar")).rejects.toMatchObject({
      name: "UploadError",
      status: null,
    });
  });
});
