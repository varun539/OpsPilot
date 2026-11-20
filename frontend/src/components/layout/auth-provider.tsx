"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { setAccessTokenGetter, setUnauthorizedHandler } from "@/lib/api/client";
import type { Role } from "@/lib/api/types";
import { authConfig, devAuthEnabled, oidcConfigured } from "@/lib/auth/config";
import { getUserManager } from "@/lib/auth/oidc";
import { extractRoles, highestRole } from "@/lib/auth/roles";

export type AuthStatus =
  | "loading"
  | "authenticated"
  | "unauthenticated"
  | "config-required"
  | "error";

export interface AuthUser {
  subject: string;
  name: string;
  email?: string;
  roles: string[];
  role: Role;
}

interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  role: Role;
  error: string | null;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function userFromClaims(subject: string, name: string, roles: string[], email?: string): AuthUser {
  return { subject, name, email, roles, role: highestRole(roles) };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSession = useCallback(async () => {
    if (devAuthEnabled) {
      setUser(
        userFromClaims(
          "dev-user",
          "Development operator",
          ["admin"],
          "dev-user@localhost",
        ),
      );
      setStatus("authenticated");
      return;
    }
    if (!oidcConfigured) {
      setStatus("config-required");
      return;
    }
    try {
      const oidcUser = await getUserManager().getUser();
      if (!oidcUser || oidcUser.expired) {
        setUser(null);
        setStatus("unauthenticated");
        return;
      }
      const roles = extractRoles(oidcUser.access_token, authConfig.rolesClaim);
      const profile = oidcUser.profile as Record<string, unknown>;
      const name =
        (profile.name as string) ||
        (profile.preferred_username as string) ||
        (profile.email as string) ||
        "Authenticated user";
      setUser(
        userFromClaims(
          (profile.sub as string) || "unknown",
          name,
          roles,
          profile.email as string | undefined,
        ),
      );
      setStatus("authenticated");
    } catch {
      setError("Unable to restore your session. Please sign in again.");
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    if (devAuthEnabled) {
      setAccessTokenGetter(() => null);
    } else if (oidcConfigured) {
      setAccessTokenGetter(async () => {
        const oidcUser = await getUserManager().getUser();
        return oidcUser && !oidcUser.expired ? oidcUser.access_token : null;
      });
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadSession();
  }, [loadSession]);

  useEffect(() => {
    if (devAuthEnabled || !oidcConfigured) {
      setUnauthorizedHandler(null);
      return;
    }
    setUnauthorizedHandler(() => {
      setUser(null);
      setStatus("unauthenticated");
      setError("Your session has expired. Please sign in again.");
      void getUserManager()
        .removeUser()
        .catch(() => undefined);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    if (!oidcConfigured || devAuthEnabled) return;
    const manager = getUserManager();
    const onLoaded = () => void loadSession();
    const onUnloaded = () => {
      setUser(null);
      setStatus("unauthenticated");
    };
    const onExpired = () => {
      setUser(null);
      setStatus("unauthenticated");
    };
    manager.events.addUserLoaded(onLoaded);
    manager.events.addUserUnloaded(onUnloaded);
    manager.events.addAccessTokenExpired(onExpired);
    return () => {
      manager.events.removeUserLoaded(onLoaded);
      manager.events.removeUserUnloaded(onUnloaded);
      manager.events.removeAccessTokenExpired(onExpired);
    };
  }, [loadSession]);

  const login = useCallback(async () => {
    if (devAuthEnabled) {
      await loadSession();
      return;
    }
    if (!oidcConfigured) return;
    await getUserManager().signinRedirect();
  }, [loadSession]);

  const logout = useCallback(async () => {
    setUser(null);
    if (devAuthEnabled) {
      setStatus("unauthenticated");
      return;
    }
    if (!oidcConfigured) return;
    try {
      await getUserManager().signoutRedirect();
    } catch {
      await getUserManager().removeUser();
      setStatus("unauthenticated");
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      role: user?.role ?? "viewer",
      error,
      login,
      logout,
      refreshSession: loadSession,
    }),
    [status, user, error, login, logout, loadSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}

export async function completeOidcLogin(): Promise<void> {
  await getUserManager().signinRedirectCallback();
}
