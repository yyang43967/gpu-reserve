import { apiFetch } from "@/lib/api";
import type { PresignResponse, UploadPurpose } from "@/lib/api-types";

const MIB = 1024 * 1024;

/** Largest file per purpose. The API enforces the same limits. */
export const MAX_UPLOAD_BYTES: Record<UploadPurpose, number> = {
  avatar: 2 * MIB,
  server_image: 5 * MIB,
};

export const ALLOWED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;

/** Value for a file input's `accept` attribute. */
export const IMAGE_ACCEPT = ALLOWED_IMAGE_TYPES.join(",");

/** The storage service rejected the upload, or could not be reached. */
export class UploadError extends Error {
  readonly status: number | null;

  constructor(status: number | null) {
    super(
      status === null
        ? "The upload could not reach storage. Check your connection and try again."
        : `The upload failed (status ${String(status)}). Try again.`,
    );
    this.name = "UploadError";
    this.status = status;
  }
}

/** A message for a file the API would reject, or null if it looks acceptable. */
export function checkImageFile(file: File, purpose: UploadPurpose): string | null {
  if (!(ALLOWED_IMAGE_TYPES as readonly string[]).includes(file.type)) {
    return "Choose a PNG, JPEG, or WebP image.";
  }
  if (file.size === 0) {
    return "The file is empty.";
  }
  const limit = MAX_UPLOAD_BYTES[purpose];
  if (file.size > limit) {
    return `The image must be ${String(limit / MIB)} MiB or smaller.`;
  }
  return null;
}

/**
 * Upload an image straight to storage and return its key, to be saved on the profile or
 * server afterwards.
 *
 * The API signs a short-lived form; the file then goes to storage with a plain POST. No
 * bearer token or cookie is sent to storage: the signed form is the only credential.
 */
export async function uploadImage(
  file: File,
  purpose: UploadPurpose,
  signal?: AbortSignal,
): Promise<string> {
  const ticket = await apiFetch<PresignResponse>("/uploads/presign", {
    method: "POST",
    body: { purpose, content_type: file.type, size_bytes: file.size },
    signal,
  });

  const form = new FormData();
  for (const [name, value] of Object.entries(ticket.fields)) {
    form.append(name, value);
  }
  // Storage reads the policy fields first; the file must come last.
  form.append("file", file);

  let response: Response;
  try {
    response = await fetch(ticket.url, {
      method: "POST",
      body: form,
      signal,
      credentials: "omit",
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new UploadError(null);
  }
  if (!response.ok) {
    throw new UploadError(response.status);
  }
  return ticket.key;
}
