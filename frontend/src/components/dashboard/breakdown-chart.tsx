import type { BreakdownItem } from "@/lib/api/types";
import { titleCase } from "@/lib/format";

const COLORS: Record<string, string> = {
  critical: "bg-[#ef4444]",
  high: "bg-[#f97316]",
  medium: "bg-[#f59e0b]",
  low: "bg-[#3b82f6]",
  info: "bg-[#64748b]",
  open: "bg-[#3b82f6]",
  investigating: "bg-[#8b5cf6]",
  awaiting_approval: "bg-[#f59e0b]",
  resolved: "bg-[#22c55e]",
  closed: "bg-[#64748b]",
  reopened: "bg-[#f97316]",
  running: "bg-[#3b82f6]",
  created: "bg-[#64748b]",
  resuming: "bg-[#8b5cf6]",
  completed: "bg-[#22c55e]",
  failed: "bg-[#ef4444]",
  resume_failed: "bg-[#ef4444]",
};

/**
 * Compact horizontal bar breakdown. The accessible table of counts is the
 * primary content; bars are supplementary.
 */
export function BreakdownChart({
  title,
  data,
  emptyLabel = "No records yet",
}: {
  title: string;
  data: BreakdownItem[];
  emptyLabel?: string;
}) {
  const total = data.reduce((sum, item) => sum + item.count, 0);
  const max = Math.max(...data.map((item) => item.count), 1);

  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
        <span className="text-[11px] tabular-nums text-muted">{total} total</span>
      </div>
      {total === 0 ? (
        <p className="rounded border border-dashed border-border px-3 py-6 text-center text-xs text-muted">
          {emptyLabel}
        </p>
      ) : (
        <ul className="space-y-2">
          {data.map((item) => (
            <li key={item.name} className="grid grid-cols-[8rem_1fr_2rem] items-center gap-2">
              <span className="truncate text-xs text-muted">{titleCase(item.name)}</span>
              <span className="h-1.5 overflow-hidden rounded-full bg-surface-raised">
                <span
                  className={`block h-full rounded-full ${COLORS[item.name] ?? "bg-[#475569]"}`}
                  style={{ width: `${Math.max((item.count / max) * 100, 4)}%` }}
                />
              </span>
              <span className="text-right text-xs tabular-nums text-foreground">{item.count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
