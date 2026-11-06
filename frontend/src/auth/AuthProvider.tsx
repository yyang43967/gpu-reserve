import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, type ReactNode } from "react";

import * as authApi from "@/auth/auth-api";
import {
  AuthContext,
  meQueryKey,
  type AuthContextValue,
  type AuthState,
} from "@/auth/auth-context";
import { refreshSession, setSessionExpiredHandler } from "@/lib/api";
import type { User } from "@/lib/api-types";
import { getAccessToken } from "@/lib/auth-token";

/**
 * Resolve the current user. With no access token in memory (a fresh page load), the
 * refresh cookie is exchanged for one first; no cookie means signed out.
 */
async function loadCurrentUser(signal: AbortSignal): Promise<User | null> {
  if (getAccessToken() === null) {
    const session = await refreshSession();
    return session?.user ?? null;
  }
  return authApi.getMe(signal);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const me = useQuery({
    queryKey: meQueryKey,
    queryFn: ({ signal }) => loadCurrentUser(signal),
    staleTime: Infinity,
    retry: false,
  });

  const setUser = useCallback(
    (user: User | null) => {
      queryClient.setQueryData(meQueryKey, user);
    },
    [queryClient],
  );

  const endSession = useCallback(() => {
    // Nothing cached for this user may leak into the next session. The "me" query is
    // set rather than removed: removing it while observed would refetch it, which would
    // try the refresh cookie again.
    queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== meQueryKey[0] });
    setUser(null);
  }, [queryClient, setUser]);

  useEffect(() => {
    // The refresh cookie was rejected: drop to signed out and let the guards redirect.
    setSessionExpiredHandler(() => {
      endSession();
    });
  }, [endSession]);

  const login = useCallback(
    async (input: authApi.LoginInput) => {
      const user = await authApi.login(input);
      setUser(user);
      return user;
    },
    [setUser],
  );

  const register = useCallback(
    async (input: authApi.RegisterInput) => {
      const user = await authApi.register(input);
      setUser(user);
      return user;
    },
    [setUser],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      endSession();
    }
  }, [endSession]);

  const refetch = me.refetch;
  const state: AuthState = useMemo(() => {
    if (me.isPending) return { status: "loading", user: null };
    if (me.isError) {
      return {
        status: "error",
        user: null,
        error: me.error,
        retry: () => {
          void refetch();
        },
      };
    }
    return me.data
      ? { status: "authenticated", user: me.data }
      : { status: "anonymous", user: null };
  }, [me.isPending, me.isError, me.error, me.data, refetch]);

  const value: AuthContextValue = useMemo(
    () => ({ ...state, login, register, logout }),
    [state, login, register, logout],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
