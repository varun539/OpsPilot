"use client";

import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { authConfig } from "./config";

let manager: UserManager | null = null;

/**
 * Access tokens are kept in session-scoped storage (not localStorage) by the
 * maintained OIDC client library. No client secret is used.
 */
export function getUserManager(): UserManager {
  if (manager) return manager;
  manager = new UserManager({
    authority: authConfig.authority,
    client_id: authConfig.clientId,
    redirect_uri: authConfig.redirectUri,
    post_logout_redirect_uri:
      typeof window === "undefined" ? undefined : `${window.location.origin}/dashboard`,
    response_type: "code",
    scope: authConfig.scope,
    automaticSilentRenew: false,
    monitorSession: false,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    stateStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
  return manager;
}
