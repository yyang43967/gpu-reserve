import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

import { ApiError } from "@/lib/api";

// Problem codes that belong to one input rather than to the whole form.
const FIELD_FOR_CODE: Record<string, string> = {
  "email-taken": "email",
  "incorrect-password": "current_password",
};

export function retryAfterMessage(seconds: number | null): string {
  if (seconds === null) return "Please wait a moment and try again.";
  if (seconds < 60) return `Try again in ${String(seconds)} seconds.`;
  const minutes = Math.ceil(seconds / 60);
  return `Try again in ${String(minutes)} minute${minutes === 1 ? "" : "s"}.`;
}

/** A sentence describing any request failure, for use outside forms too. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return `${error.title ?? "Too many attempts"}. ${retryAfterMessage(error.retryAfterSeconds)}`;
    }
    return error.message;
  }
  // fetch rejects with a TypeError when the network or server is unreachable.
  return "Could not reach the server. Check your connection and try again.";
}

/**
 * Show a failed submission on the form: errors tied to a known field go under that
 * input, everything else becomes the returned form-level message.
 */
export function applyServerErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fields: readonly Path<T>[],
): string | null {
  if (!(error instanceof ApiError)) {
    return describeError(error);
  }
  const known = new Set<string>(fields);
  const leftovers: string[] = [];

  for (const fieldError of error.fieldErrors) {
    if (known.has(fieldError.field)) {
      setError(fieldError.field as Path<T>, { type: "server", message: fieldError.message });
    } else {
      leftovers.push(fieldError.message);
    }
  }

  const codeField = error.code ? FIELD_FOR_CODE[error.code] : undefined;
  if (codeField && known.has(codeField)) {
    setError(codeField as Path<T>, { type: "server", message: error.message });
    return null;
  }
  if (error.fieldErrors.length > 0) {
    return leftovers.length > 0 ? leftovers.join(" ") : null;
  }
  return describeError(error);
}
