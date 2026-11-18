"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, BrainCircuit, RefreshCw, ShieldCheck, Ticket as TicketIcon } from "lucide-react";

import { useAuth } from "@/components/layout/auth-provider";
import { StartInvestigationDialog } from "@/components/incidents/start-investigation-dialog";
import {
  ApprovalStatusBadge,
  IncidentStatusBadge,
  PriorityBadge,
  SeverityBadge,
  TicketStatusBadge,
  WorkflowStatusBadge,
} from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { JsonViewer } from "@/components/ui/json-viewer";
import { getIncidentHistory } from "@/lib/api/incidents";
import type { IncidentHistory } from "@/lib/api/types";
import { canOperate } from "@/lib/auth/roles";
import { formatDateTime, titleCase } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const ACTIVE_WORKFLOW_STATUSES = new Set(["created", "running", "awaiting_approval", "resuming"]);
const TABS = ["overview", "investigations", "tickets", "activity", "approvals", "resolutions"] as const;
type Tab = (typeof TABS)[number];

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`border-b-2 px-3 py-2 text-xs font-medium transition-colors ${
        active
          ? "border-accent text-foreground"
          : "border-transparent text-muted hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

function DetailItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-0.5 text-xs text-foreground">{children}</dd>
    </div>
  );
}

function Overview({ history }: { history: IncidentHistory }) {
  const { incident } = history;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader title="Description" />
        <CardBody>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-[#c3d0e3]">
            {incident.description || "No description was recorded for this incident."}
          </p>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Incident metadata" />
        <CardBody>
          <dl className="grid grid-cols-2 gap-4">
            <DetailItem label="Severity"><SeverityBadge severity={incident.severity} /></DetailItem>
            <DetailItem label="Priority"><PriorityBadge priority={incident.priority} /></DetailItem>
            <DetailItem label="Status"><IncidentStatusBadge status={incident.status} /></DetailItem>
            <DetailItem label="Service">{incident.service_name ?? "—"}</DetailItem>
            <DetailItem label="Environment">{incident.environment ?? "—"}</DetailItem>
            <DetailItem label="Source">{incident.source ?? "—"}</DetailItem>
            <DetailItem label="Created">{formatDateTime(incident.created_at)}</DetailItem>
            <DetailItem label="Updated">{formatDateTime(incident.updated_at)}</DetailItem>
            <DetailItem label="Resolved">{formatDateTime(incident.resolved_at)}</DetailItem>
          </dl>
        </CardBody>
      </Card>
    </div>
  );
}

export default function IncidentDetailPage({
  params,
}: {
  params: Promise<{ incidentId: string }>;
}) {
  const { incidentId } = use(params);
  const { role } = useAuth();
  const [tab, setTab] = useState<Tab>("overview");
  const [startOpen, setStartOpen] = useState(false);

  const history = useApiResource(
    (signal) => getIncidentHistory(incidentId, signal),
    `incident-${incidentId}`,
  );

  const activeInvestigation = useMemo(() => {
    return history.data?.investigations.find((investigation) =>
      ACTIVE_WORKFLOW_STATUSES.has(investigation.workflow_status ?? ""),
    );
  }, [history.data]);

  if (history.loading && !history.data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (history.error || !history.data) {
    return (
      <div className="space-y-4">
        <Link href="/incidents" className="inline-flex items-center gap-1 text-xs text-muted hover:text-foreground">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to incidents
        </Link>
        <Card>
          <ErrorState message={history.error ?? "Incident not found."} onRetry={history.refresh} />
        </Card>
      </div>
    );
  }

  const { incident } = history.data;

  return (
    <div className="space-y-4">
      <Link href="/incidents" className="inline-flex items-center gap-1 text-xs text-muted hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to incidents
      </Link>

      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-muted">{incident.incident_number}</span>
            <span>{incident.title}</span>
          </span>
        }
        description={`Service ${incident.service_name ?? "unspecified"} · ${incident.environment ?? "unknown environment"}`}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={history.refresh} disabled={history.loading}>
              <RefreshCw className={`h-3.5 w-3.5 ${history.loading ? "animate-spin" : ""}`} aria-hidden="true" />
              Refresh
            </Button>
            {canOperate(role) ? (
              <Button
                variant="primary"
                onClick={() => setStartOpen(true)}
                disabled={Boolean(activeInvestigation)}
                title={
                  activeInvestigation
                    ? "An investigation is already active for this incident"
                    : undefined
                }
              >
                <BrainCircuit className="h-4 w-4" aria-hidden="true" />
                Start AI Investigation
              </Button>
            ) : null}
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-1.5">
        <SeverityBadge severity={incident.severity} />
        <IncidentStatusBadge status={incident.status} />
        <PriorityBadge priority={incident.priority} />
        {activeInvestigation ? (
          <span className="inline-flex items-center gap-1 rounded border border-[#54411b] bg-[#251f10] px-1.5 py-0.5 text-[11px] text-[#fcd34d]">
            <ShieldCheck className="h-3 w-3" aria-hidden="true" />
            Active investigation
          </span>
        ) : null}
      </div>

      <div className="overflow-x-auto border-b border-border">
        <div role="tablist" aria-label="Incident sections" className="flex min-w-max">
          {TABS.map((item) => (
            <TabButton key={item} active={tab === item} onClick={() => setTab(item)}>
              {titleCase(item)}
              {item === "investigations" && (history.data?.investigations.length ?? 0) > 0
                ? ` (${history.data?.investigations.length})`
                : ""}
              {item === "tickets" && (history.data?.tickets.length ?? 0) > 0
                ? ` (${history.data?.tickets.length})`
                : ""}
            </TabButton>
          ))}
        </div>
      </div>

      {tab === "overview" ? <Overview history={history.data} /> : null}

      {tab === "investigations" ? (
        <Card>
          <CardHeader
            title="Investigation history"
            description="AI workflow runs and their current workflow state"
          />
          {history.data.investigations.length === 0 ? (
            <EmptyState
              title="No investigations"
              description="Start an AI investigation to gather diagnostic evidence and produce a structured report."
              icon={<BrainCircuit className="h-5 w-5" aria-hidden="true" />}
              action={
                canOperate(role) ? (
                  <Button size="sm" onClick={() => setStartOpen(true)} disabled={Boolean(activeInvestigation)}>
                    Start investigation
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <ul className="divide-y divide-border">
              {history.data.investigations.map((investigation) => (
                <li key={investigation.id}>
                  <Link
                    href={`/investigations/${investigation.id}`}
                    className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 hover:bg-surface-raised/50"
                  >
                    <span className="min-w-0">
                      <span className="block text-xs font-medium text-foreground">
                        {investigation.investigation_number}
                      </span>
                      <span className="block truncate text-[11px] text-muted">
                        {investigation.summary ?? investigation.suspected_root_cause ?? "No report summary yet"}
                      </span>
                    </span>
                    <span className="flex items-center gap-2 text-[11px] text-muted">
                      {formatDateTime(investigation.started_at)}
                      <WorkflowStatusBadge status={investigation.workflow_status ?? investigation.status} />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : null}

      {tab === "tickets" ? (
        <Card>
          <CardHeader title="Linked tickets" description="Tickets associated with this incident" />
          {history.data.tickets.length === 0 ? (
            <EmptyState
              title="No tickets"
              description="Tickets can be created from the Tickets page and linked to this incident."
              icon={<TicketIcon className="h-5 w-5" aria-hidden="true" />}
              action={<Link href="/tickets"><Button size="sm">Open tickets</Button></Link>}
            />
          ) : (
            <ul className="divide-y divide-border">
              {history.data.tickets.map((ticket) => (
                <li key={ticket.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
                  <span>
                    <span className="block text-xs font-medium text-foreground">{ticket.title}</span>
                    <span className="text-[11px] text-muted">
                      {ticket.ticket_number} · {ticket.assignee ?? "Unassigned"} · {formatDateTime(ticket.created_at)}
                    </span>
                  </span>
                  <span className="flex items-center gap-1.5">
                    <PriorityBadge priority={ticket.priority} />
                    <TicketStatusBadge status={ticket.status} />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : null}

      {tab === "activity" ? (
        <Card>
          <CardHeader title="Activity timeline" description="Chronological incident event history" />
          {history.data.events.length === 0 ? (
            <EmptyState title="No activity yet" description="Workflow and approval events will appear here." />
          ) : (
            <ol className="relative space-y-0 px-4 py-3">
              {[...history.data.events].reverse().map((event, index, all) => (
                <li key={event.id} className="relative flex gap-3 pb-4">
                  <span className="flex flex-col items-center">
                    <span className="mt-1 h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
                    {index < all.length - 1 ? <span className="mt-1 w-px flex-1 bg-border" aria-hidden="true" /> : null}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium text-foreground">{titleCase(event.event_type)}</p>
                    <p className="text-[11px] text-muted">
                      {formatDateTime(event.created_at)}
                      {event.actor ? ` · ${event.actor}` : ""}
                    </p>
                    {Object.keys(event.new_value ?? event.old_value ?? {}).length > 0 ? (
                      <div className="mt-1.5">
                        <JsonViewer value={event.new_value ?? event.old_value} label="Event details" />
                      </div>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Card>
      ) : null}

      {tab === "approvals" ? (
        <Card>
          <CardHeader title="Approvals" description="Human review decisions recorded for this incident" />
          {history.data.approvals.length === 0 ? (
            <EmptyState title="No approval records" description="Approvals are created when an investigation pauses for human review." />
          ) : (
            <ul className="divide-y divide-border">
              {history.data.approvals.map((approval) => (
                <li key={approval.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs text-foreground">
                      {titleCase(approval.approval_type)} · requested {formatDateTime(approval.requested_at)}
                    </span>
                    <ApprovalStatusBadge status={approval.status} />
                  </div>
                  <p className="mt-1 text-[11px] text-muted">
                    {approval.decided_by ? `Decided by ${approval.decided_by} · ${formatDateTime(approval.decided_at)}` : "Awaiting decision"}
                    {approval.decision_reason ? ` — ${approval.decision_reason}` : ""}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : null}

      {tab === "resolutions" ? (
        <Card>
          <CardHeader title="Resolutions" description="Recorded resolution history" />
          {history.data.resolutions.length === 0 ? (
            <EmptyState title="No resolutions recorded" description="Resolution records are added when the incident is resolved." />
          ) : (
            <ul className="divide-y divide-border">
              {history.data.resolutions.map((resolution) => (
                <li key={resolution.id} className="px-4 py-3">
                  <p className="text-xs font-medium text-foreground">{resolution.resolution_summary}</p>
                  <p className="mt-0.5 text-[11px] text-muted">
                    {formatDateTime(resolution.created_at)}
                    {resolution.resolved_by ? ` · ${resolution.resolved_by}` : ""}
                  </p>
                  {resolution.resolution_details ? (
                    <p className="mt-1 whitespace-pre-wrap text-[11px] text-[#c3d0e3]">{resolution.resolution_details}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : null}

      <StartInvestigationDialog
        incident={incident}
        open={startOpen}
        onClose={() => setStartOpen(false)}
      />
    </div>
  );
}
