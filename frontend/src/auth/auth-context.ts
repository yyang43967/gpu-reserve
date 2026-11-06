import { createContext, useContext } from "react";

import type { LoginInput, RegisterInput } from "@/auth/auth-api";
import type { User } from "@/lib/api-types";

export type AuthState =
  | { status: "loading"; user: null }
  | { status: "error"; user: null; error: Error; retry: () => void }
  | {
      status: "anonymous";
      user: null;
      /**
       * True when the user signed out in this tab. Guards then go to a plain login page
       * instead of remembering the page for the next sign-in.
       */
      signedOut: boolean;
    }
  | { status: "authenticated"; user: User };

export type AuthContextValue = AuthState & {
  login: (input: LoginInput) => Promise<User>;
  register: (input: RegisterInput) => Promise<User>;
  logout: () => Promise<void>;
};

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}

export const meQueryKey = ["me"] as const;
