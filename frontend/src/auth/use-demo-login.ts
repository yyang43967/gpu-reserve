import { useQuery } from "@tanstack/react-query";

import { getDemoLogin } from "@/auth/auth-api";

/** The public demo account, if this deployment has one. It never changes while the
 * app runs, so it is fetched once. */
export function useDemoLogin() {
  return useQuery({
    queryKey: ["demo-login"],
    queryFn: ({ signal }) => getDemoLogin(signal),
    staleTime: Infinity,
    retry: false,
  });
}
