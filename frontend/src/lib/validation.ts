// Mirrors the API's limits so forms can reject bad input before a round trip.
export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** True for a UUID in its usual hyphenated form, as the API uses for ids. */
export function isUuid(value: string): boolean {
  return UUID_RE.test(value);
}
