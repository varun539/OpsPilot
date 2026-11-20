import { AlertTriangle, CheckCircle2, MinusCircle } from "lucide-react";

import type { Investigation } from "@/lib/api/types";

export type PlannerStatus = NonNullable<Investigation["planner_status"]>;

const FALLBACK_REASONS: Record<string, string> = {
  fallback: "no LLM planner is configured, so the deterministic planner was used",
  fallback_timeout: "the planner request timed out",
  fallback_invalid_selection: "the planner proposed a tool outside the allow-list",
  fallback_validation: "the planner response failed schema validation",
  fallback_refusal: "the planner response was refused by the provider",
  fallback_empty: "the planner returned no usable output",
  fallback_provider_error: "the planner provider call failed",
};

export function isPlannerDegraded(status: string | null | undefined): boolean {
  if (typeof status !== "string") return false;
  return status === "fallback" || status.startsWith("fallback_");
}

export function describePlannerStatus(status: string | null | undefined): string {
  if (!status) {
    return "Planner status was not recorded for this investigation.";
  }
  if (status === "used") {
    return "The LLM planner selected the diagnostic tools.";
  }
  const reason = FALLBACK_REASONS[status];
  if (reason) {
    return `A deterministic fallback plan was used because ${reason}.`;
  }
  return "A fallback plan was used and the reason was not classified.";
}

/**
 * Planner degradation is shown as a warning, never as success. It never
 * changes the workflow verdict: a failed investigation still shows its own
 * failure banner.
 */
export function PlannerStatusNotice({
  status,
  error,
}: {
  status: string | null | undefined;
  error: string | null | undefined;
}) {
  if (isPlannerDegraded(status)) {
    return (
      <div
        role="status"
        aria-label="Planner degraded"
        className="flex items-start gap-2 rounded-md border border-[#54411b] bg-[#251f10] px-3 py-2 text-xs text-[#fcd34d]"
      >
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>
          <strong className="font-medium">Planner degraded.</strong>{" "}
          {describePlannerStatus(status)}
          {error ? (
            <>
              {" "}
              <span className="font-mono text-[11px] text-[#fcd34d]/80">{error}</span>
            </>
          ) : null}
        </span>
      </div>
    );
  }

  if (status === "used") {
    return (
      <div
        role="status"
        aria-label="Planner used"
        className="flex items-start gap-2 rounded-md border border-border bg-surface px-3 py-2 text-xs text-muted"
      >
        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>{describePlannerStatus(status)}</span>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-label="Planner status unknown"
      className="flex items-start gap-2 rounded-md border border-border bg-surface px-3 py-2 text-xs text-muted"
    >
      <MinusCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>{describePlannerStatus(status)}</span>
    </div>
  );
}
