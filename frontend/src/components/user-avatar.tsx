import { useState } from "react";

import type { User } from "@/lib/api-types";
import { cn } from "@/lib/utils";

/** Up to two initials from the full name, for when there is no photo. */
function initials(fullName: string): string {
  const letters = fullName
    .trim()
    .split(/\s+/)
    .filter((word) => word.length > 0)
    .slice(0, 2)
    .map((word) => word.charAt(0).toUpperCase());
  return letters.join("");
}

/**
 * The user's photo, or their initials. Decorative: whatever wraps it (a link to the
 * account page, for example) carries the accessible name.
 */
export function UserAvatar({
  user,
  className,
}: {
  user: Pick<User, "full_name" | "avatar_url">;
  className?: string;
}) {
  // A URL that fails to load (expired or deleted) falls back to the initials.
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const url = user.avatar_url !== null && user.avatar_url !== failedUrl ? user.avatar_url : null;

  return (
    <span
      aria-hidden="true"
      data-testid="user-avatar"
      className={cn(
        "inline-flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted text-xs font-medium text-muted-foreground",
        className,
      )}
    >
      {url ? (
        <img
          src={url}
          alt=""
          className="size-full object-cover"
          onError={() => {
            setFailedUrl(url);
          }}
        />
      ) : (
        initials(user.full_name)
      )}
    </span>
  );
}
