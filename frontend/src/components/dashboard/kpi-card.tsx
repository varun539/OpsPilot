import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";

export function KpiCard({
  label,
  value,
  icon,
  hint,
  tone = "neutral",
  loading = false,
}: {
  label: string;
  value: number | undefined;
  icon: ReactNode;
  hint?: string;
  tone?: "neutral" | "danger" | "warning" | "accent" | "success";
  loading?: boolean;
}) {
  const toneClass = {
    neutral: "text-muted",
    danger: "text-[#fca5a5]",
    warning: "text-[#fcd34d]",
    accent: "text-accent",
    success: "text-[#86efac]",
  }[tone];

  return (
    <Card className="px-4 py-3">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</p>
        <span className={toneClass} aria-hidden="true">
          {icon}
        </span>
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-16" />
      ) : (
        <p className="mt-1 text-2xl font-semibold tabular-nums text-foreground">{value ?? 0}</p>
      )}
      {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
    </Card>
  );
}
