"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";

import { completeOidcLogin } from "@/components/layout/auth-provider";

export default function AuthCallbackPage() {
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    completeOidcLogin()
      .then(() => {
        if (active) window.location.replace("/dashboard");
      })
      .catch(() => {
        if (active) setError("Sign-in could not be completed. Please try again.");
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        {error ? (
          <>
            <p className="text-sm text-[#fca5a5]" role="alert">
              {error}
            </p>
            <Link href="/dashboard" className="mt-3 inline-block text-xs text-accent underline">
              Return to sign in
            </Link>
          </>
        ) : (
          <span className="inline-flex items-center gap-2 text-sm text-muted" role="status">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Completing sign-in…
          </span>
        )}
      </div>
    </main>
  );
}
