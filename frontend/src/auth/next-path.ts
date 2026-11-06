/**
 * The page to return to after signing in, from the `next` query parameter.
 *
 * Only same-site paths are accepted, so a crafted link cannot send a user to another
 * site after they sign in.
 */
export function safeNextPath(next: string | null): string {
  if (!next?.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return "/";
  }
  return next;
}

export function loginPathFor(location: { pathname: string; search: string }): string {
  const next = `${location.pathname}${location.search}`;
  return next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`;
}
