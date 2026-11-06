/** What to do with the saved image: leave it, remove it, or use a freshly uploaded key. */
export type ImageChoice = { kind: "keep" } | { kind: "remove" } | { kind: "set"; key: string };

export const KEEP_IMAGE: ImageChoice = { kind: "keep" };
