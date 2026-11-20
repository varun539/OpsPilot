"use client";

import { useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";

import { useAuth } from "./auth-provider";
import { AuthLoadingScreen, ConfigRequiredScreen, SignInScreen } from "./auth-screens";
import { Sidebar, NAV_ITEMS } from "./sidebar";
import { devAuthEnabled } from "@/lib/auth/config";

export function AppShell({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();

  if (status === "loading") return <AuthLoadingScreen />;
  if (status === "config-required") return <ConfigRequiredScreen />;
  if (status === "unauthenticated" || status === "error") return <SignInScreen />;

  const section =
    NAV_ITEMS.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`))
      ?.label ?? "Overview";

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar open={mobileOpen} onClose={() => setMobileOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b border-border bg-background/95 px-4 backdrop-blur">
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="rounded p-1.5 text-muted hover:bg-surface-raised hover:text-foreground lg:hidden"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation"
            >
              <Menu className="h-4 w-4" aria-hidden="true" />
            </button>
            <span className="text-sm font-medium text-foreground">{section}</span>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-muted">
            {devAuthEnabled ? (
              <span className="rounded border border-[#54411b] bg-[#251f10] px-1.5 py-0.5 text-[#fcd34d]">
                Development auth
              </span>
            ) : null}
            <a
              href={`${process.env.NEXT_PUBLIC_OPSPILOT_API_URL ?? "http://127.0.0.1:8000"}/docs`}
              target="_blank"
              rel="noreferrer"
              className="hidden hover:text-foreground sm:inline"
            >
              API docs
            </a>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">{children}</main>
      </div>
    </div>
  );
}
