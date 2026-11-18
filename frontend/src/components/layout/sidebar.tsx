"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AlertTriangle,
  BrainCircuit,
  LayoutDashboard,
  LogOut,
  Settings,
  ShieldCheck,
  Ticket,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useAuth } from "./auth-provider";

export const NAV_ITEMS = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: "/incidents", label: "Incidents", icon: AlertTriangle },
  { href: "/investigations", label: "Investigations", icon: BrainCircuit },
  { href: "/approvals", label: "Approvals", icon: ShieldCheck },
  { href: "/tickets", label: "Tickets", icon: Ticket },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function isActivePath(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  return (
    <>
      {open ? (
        <div
          className="fixed inset-0 z-30 bg-black/60 lg:hidden"
          aria-hidden="true"
          onClick={onClose}
        />
      ) : null}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-border bg-surface transition-transform lg:static lg:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
        aria-label="Primary navigation"
      >
        <div className="flex h-14 items-center justify-between border-b border-border px-4">
          <Link href="/dashboard" className="flex items-center gap-2" onClick={onClose}>
            <span className="flex h-6 w-6 items-center justify-center rounded bg-accent-strong text-[11px] font-bold text-white">
              OP
            </span>
            <span className="text-sm font-semibold tracking-tight">OpsPilot</span>
            <Badge>v1</Badge>
          </Link>
          <button
            type="button"
            className="rounded p-1 text-muted hover:text-foreground lg:hidden"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <nav className="flex-1 space-y-0.5 px-2 py-3">
          {NAV_ITEMS.map((item) => {
            const active = isActivePath(pathname, item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onClose}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors ${
                  active
                    ? "bg-surface-raised text-foreground"
                    : "text-muted hover:bg-surface-raised/60 hover:text-foreground"
                }`}
              >
                <Icon
                  className={`h-4 w-4 ${active ? "text-accent" : ""}`}
                  aria-hidden="true"
                />
                {item.label}
              </Link>
            );
          })}
        </nav>
        {user ? (
          <div className="border-t border-border px-3 py-3">
            <div className="flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-raised text-xs font-semibold text-foreground">
                {user.name.slice(0, 2).toUpperCase()}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-foreground">
                  {user.name}
                </p>
                <p className="text-[11px] uppercase tracking-wide text-muted">
                  {user.role}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void logout()}
                className="rounded p-1.5 text-muted hover:bg-surface-raised hover:text-foreground"
                aria-label="Sign out"
                title="Sign out"
              >
                <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          </div>
        ) : null}
        <div className="border-t border-border px-4 py-3 text-[11px] leading-relaxed text-muted">
          Incident operations
          <br />
          AI investigations require human approval.
        </div>
      </aside>
    </>
  );
}
