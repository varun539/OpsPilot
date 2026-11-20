"use client";

import { useState } from "react";
import { KeyRound, Loader2, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { authConfig, oidcConfigured } from "@/lib/auth/config";
import { useAuth } from "./auth-provider";

const REQUIRED_ENV = [
  "NEXT_PUBLIC_OPSPILOT_API_URL",
  "NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY",
  "NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID",
  "NEXT_PUBLIC_OPSPILOT_OIDC_REDIRECT_URI",
  "NEXT_PUBLIC_OPSPILOT_OIDC_SCOPE",
];

export function SignInScreen() {
  const { login, error, status } = useAuth();
  const [submitting, setSubmitting] = useState(false);

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-md px-6 py-8">
        <div className="mb-6 flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded bg-accent-strong text-xs font-bold text-white">
            OP
          </span>
          <div>
            <h1 className="text-base font-semibold">OpsPilot</h1>
            <p className="text-xs text-muted">Incident operations and AI investigation</p>
          </div>
        </div>
        <p className="mb-4 text-sm text-muted">
          Sign in with your organization&apos;s identity provider to access the
          incident dashboard.
        </p>
        {error ? (
          <div role="alert" className="mb-4 rounded border border-[#5f2229] bg-[#2a1114] px-3 py-2 text-xs text-[#fca5a5]">
            {error}
          </div>
        ) : null}
        <Button
          variant="primary"
          className="w-full"
          disabled={submitting || status === "loading" || !oidcConfigured}
          onClick={async () => {
            setSubmitting(true);
            try {
              await login();
            } finally {
              setSubmitting(false);
            }
          }}
        >
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <KeyRound className="h-4 w-4" aria-hidden="true" />
          )}
          Sign in with OIDC
        </Button>
        <p className="mt-4 text-[11px] leading-relaxed text-muted">
          Access is verified by the OpsPilot API using the configured issuer,
          audience, and JWKS. Roles are read from the{" "}
          <code className="rounded bg-surface-raised px-1">{authConfig.rolesClaim}</code>{" "}
          claim.
        </p>
      </Card>
    </main>
  );
}

export function ConfigRequiredScreen() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-xl px-6 py-8">
        <div className="mb-4 flex items-center gap-2 text-[#fcd34d]">
          <ShieldAlert className="h-5 w-5" aria-hidden="true" />
          <h1 className="text-base font-semibold text-foreground">
            Authentication configuration required
          </h1>
        </div>
        <p className="text-sm text-muted">
          No OIDC provider is configured, so OpsPilot cannot authenticate API
          requests. Set the following public environment variables and restart
          the frontend.
        </p>
        <ul className="mt-4 space-y-1.5 rounded-md border border-border bg-background px-3 py-3 font-mono text-[11px] text-[#c3d0e3]">
          {REQUIRED_ENV.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
        <p className="mt-4 text-xs leading-relaxed text-muted">
          For loopback-only local development, the backend can instead be run with
          development authentication enabled. The frontend must opt in explicitly
          with <code className="rounded bg-surface-raised px-1">NEXT_PUBLIC_OPSPILOT_DEV_AUTH=true</code>.
          This flag is never enabled by default and should not be used in
          production.
        </p>
      </Card>
    </main>
  );
}

export function AuthLoadingScreen() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background">
      <span className="inline-flex items-center gap-2 text-sm text-muted" role="status">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Restoring session…
      </span>
    </main>
  );
}
