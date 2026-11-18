import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { AuthProvider } from "@/components/layout/auth-provider";
import { ToastProvider } from "@/components/ui/toast";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <ToastProvider>
        <AppShell>{children}</AppShell>
      </ToastProvider>
    </AuthProvider>
  );
}
