/** OIDC and API configuration read from public environment variables. */

function value(raw: string | undefined): string {
  return (raw ?? "").trim();
}

export const authConfig = {
  apiUrl: value(process.env.NEXT_PUBLIC_OPSPILOT_API_URL) || "http://127.0.0.1:8000",
  authority: value(process.env.NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY),
  clientId: value(process.env.NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID),
  redirectUri:
    value(process.env.NEXT_PUBLIC_OPSPILOT_OIDC_REDIRECT_URI) ||
    (typeof window === "undefined" ? "" : `${window.location.origin}/auth/callback`),
  scope: value(process.env.NEXT_PUBLIC_OPSPILOT_OIDC_SCOPE) || "openid profile email",
  rolesClaim: value(process.env.NEXT_PUBLIC_OPSPILOT_ROLES_CLAIM) || "roles",
  devAuth: value(process.env.NEXT_PUBLIC_OPSPILOT_DEV_AUTH).toLowerCase() === "true",
};

export const oidcConfigured = Boolean(authConfig.authority && authConfig.clientId);

/**
 * Local development mode mirrors the backend's loopback-only bypass. It is
 * never selected automatically and requires an explicit build-time flag.
 */
export const devAuthEnabled = authConfig.devAuth && !oidcConfigured;
