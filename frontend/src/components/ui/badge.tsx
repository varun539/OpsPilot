import type { ReactNode } from "react";

const BASE =
  "inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide";

const SEVERITY: Record<string, string> = {
  critical: "border-[#5f2229] bg-[#2a1114] text-[#fca5a5]",
  high: "border-[#5a3a1c] bg-[#2a1d10] text-[#fdba74]",
  medium: "border-[#54411b] bg-[#251f10] text-[#fcd34d]",
  low: "border-[#1e3a5f] bg-[#101f30] text-[#93c5fd]",
  info: "border-border-strong bg-surface-raised text-muted",
};

const INCIDENT_STATUS: Record<string, string> = {
  open: "border-[#1e3a5f] bg-[#101f30] text-[#93c5fd]",
  investigating: "border-[#2e2f61] bg-[#171733] text-[#c4b5fd]",
  awaiting_approval: "border-[#54411b] bg-[#251f10] text-[#fcd34d]",
  resolved: "border-[#1c4633] bg-[#0e2419] text-[#86efac]",
  closed: "border-border-strong bg-surface-raised text-muted",
  reopened: "border-[#5a3a1c] bg-[#2a1d10] text-[#fdba74]",
};

const WORKFLOW: Record<string, string> = {
  created: "border-border-strong bg-surface-raised text-muted",
  running: "border-[#1e3a5f] bg-[#101f30] text-[#93c5fd]",
  awaiting_approval: "border-[#54411b] bg-[#251f10] text-[#fcd34d]",
  resuming: "border-[#2e2f61] bg-[#171733] text-[#c4b5fd]",
  completed: "border-[#1c4633] bg-[#0e2419] text-[#86efac]",
  failed: "border-[#5f2229] bg-[#2a1114] text-[#fca5a5]",
  resume_failed: "border-[#5f2229] bg-[#2a1114] text-[#fca5a5]",
  rejected: "border-[#5a3a1c] bg-[#2a1d10] text-[#fdba74]",
  started: "border-[#1e3a5f] bg-[#101f30] text-[#93c5fd]",
  cancelled: "border-border-strong bg-surface-raised text-muted",
};

const APPROVAL: Record<string, string> = {
  pending: "border-[#54411b] bg-[#251f10] text-[#fcd34d]",
  approved: "border-[#1c4633] bg-[#0e2419] text-[#86efac]",
  rejected: "border-[#5f2229] bg-[#2a1114] text-[#fca5a5]",
};

const TICKET: Record<string, string> = {
  open: "border-[#1e3a5f] bg-[#101f30] text-[#93c5fd]",
  in_progress: "border-[#2e2f61] bg-[#171733] text-[#c4b5fd]",
  blocked: "border-[#5f2229] bg-[#2a1114] text-[#fca5a5]",
  resolved: "border-[#1c4633] bg-[#0e2419] text-[#86efac]",
  closed: "border-border-strong bg-surface-raised text-muted",
};

const PRIORITY: Record<string, string> = {
  critical: "border-[#5f2229] bg-[#2a1114] text-[#fca5a5]",
  urgent: "border-[#5f2229] bg-[#2a1114] text-[#fca5a5]",
  p1: "border-[#5f2229] bg-[#2a1114] text-[#fca5a5]",
  high: "border-[#5a3a1c] bg-[#2a1d10] text-[#fdba74]",
  p2: "border-[#5a3a1c] bg-[#2a1d10] text-[#fdba74]",
  medium: "border-[#54411b] bg-[#251f10] text-[#fcd34d]",
  p3: "border-[#54411b] bg-[#251f10] text-[#fcd34d]",
  low: "border-[#1e3a5f] bg-[#101f30] text-[#93c5fd]",
  p4: "border-[#1e3a5f] bg-[#101f30] text-[#93c5fd]",
};

function labelize(value: string): string {
  return value.replace(/_/g, " ");
}

function Colored({ value, map, testId }: { value: string; map: Record<string, string>; testId?: string }) {
  return (
    <span className={`${BASE} ${map[value] ?? "border-border-strong bg-surface-raised text-muted"}`} data-testid={testId}>
      {labelize(value)}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <Colored value={severity} map={SEVERITY} testId="severity-badge" />;
}

export function IncidentStatusBadge({ status }: { status: string }) {
  return <Colored value={status} map={INCIDENT_STATUS} testId="incident-status-badge" />;
}

export function WorkflowStatusBadge({ status }: { status: string | null }) {
  return (
    <Colored
      value={status ?? "unknown"}
      map={WORKFLOW}
      testId="workflow-status-badge"
    />
  );
}

export function ApprovalStatusBadge({ status }: { status: string }) {
  return <Colored value={status} map={APPROVAL} testId="approval-status-badge" />;
}

export function TicketStatusBadge({ status }: { status: string }) {
  return <Colored value={status} map={TICKET} testId="ticket-status-badge" />;
}

export function PriorityBadge({ priority }: { priority: string }) {
  return <Colored value={priority} map={PRIORITY} testId="priority-badge" />;
}

export function Badge({ children }: { children: ReactNode }) {
  return (
    <span className={`${BASE} border-border-strong bg-surface-raised text-muted`}>
      {children}
    </span>
  );
}
