"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, RefreshCw } from "lucide-react";

import { SeverityBadge, WorkflowStatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Select } from "@/components/ui/field";
import { listIncidents } from "@/lib/api/incidents";
import { listInvestigations } from "@/lib/api/investigations";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const PAGE_SIZE = 20;
const WORKFLOW_STATUSES = [
  "",
  "created",
  "running",
  "awaiting_approval",
  "resuming",
  "completed",
  "failed",
  "resume_failed",
  "rejected",
];

export default function InvestigationsPage() {
  const [workflowStatus, setWorkflowStatus] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);

  const query = useMemo(
    () => ({
      workflow_status: workflowStatus || undefined,
      status: status || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
    [workflowStatus, status, offset],
  );

  const investigations = useApiResource(
    (signal) => listInvestigations(query, signal),
    JSON.stringify(query),
  );
  const incidents = useApiResource(
    (signal) => listIncidents({ limit: 100 }, signal),
    "investigation-incident-map",
  );

  const incidentMap = useMemo(
    () => new Map((incidents.data ?? []).map((incident) => [incident.id, incident])),
    [incidents.data],
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="Investigations"
        description="AI workflow runs across all incidents. Workflow status is distinct from incident status."
      />

      <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-border px-4 py-3">
          <div>
            <label htmlFor="workflow-filter" className="mb-1 block text-[11px] text-muted">Workflow status</label>
            <Select
              id="workflow-filter"
              value={workflowStatus}
              className="w-48"
              onChange={(event) => {
                setWorkflowStatus(event.target.value);
                setOffset(0);
              }}
            >
              {WORKFLOW_STATUSES.map((option) => (
                <option key={option || "all"} value={option}>
                  {option ? option.replace(/_/g, " ") : "All"}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <label htmlFor="investigation-status-filter" className="mb-1 block text-[11px] text-muted">Investigation status</label>
            <Select
              id="investigation-status-filter"
              value={status}
              className="w-40"
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
            >
              {["", "started", "running", "completed", "failed", "cancelled"].map((option) => (
                <option key={option || "all"} value={option}>{option || "All"}</option>
              ))}
            </Select>
          </div>
          <Button onClick={investigations.refresh} disabled={investigations.loading}>
            <RefreshCw className={`h-3.5 w-3.5 ${investigations.loading ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </Button>
        </div>

        <CardHeader title="Workflow runs" description={`Page ${Math.floor(offset / PAGE_SIZE) + 1}`} />

        {investigations.error ? (
          <ErrorState message={investigations.error} onRetry={investigations.refresh} />
        ) : investigations.loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, index) => <Skeleton key={index} className="h-10 w-full" />)}
          </div>
        ) : (investigations.data ?? []).length === 0 ? (
          <EmptyState
            title="No investigations"
            description="Start an AI investigation from an incident detail page. Runs and workflow state appear here."
            action={<Link href="/incidents"><Button size="sm">Browse incidents</Button></Link>}
          />
        ) : (
          <ul className="divide-y divide-border">
            {(investigations.data ?? []).map((investigation) => {
              const incident = incidentMap.get(investigation.incident_id);
              return (
                <li key={investigation.id}>
                  <Link
                    href={`/investigations/${investigation.id}`}
                    className="flex flex-wrap items-start justify-between gap-2 px-4 py-3 hover:bg-surface-raised/50"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs text-foreground">{investigation.investigation_number}</span>
                        {incident ? <SeverityBadge severity={incident.severity} /> : null}
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-foreground">
                        {incident ? `${incident.incident_number} · ${incident.title}` : investigation.incident_id}
                      </span>
                      <span className="block truncate text-[11px] text-muted">
                        {investigation.summary ?? "No report summary yet"}
                      </span>
                    </span>
                    <span className="flex items-center gap-2 text-[11px] text-muted">
                      {formatDateTime(investigation.started_at)}
                      <WorkflowStatusBadge status={investigation.workflow_status ?? investigation.status} />
                      <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}

        <CardBody className="flex items-center justify-between border-t border-border py-2.5">
          <span className="text-[11px] text-muted">
            {investigations.loading ? "Loading…" : `${investigations.data?.length ?? 0} shown`}
          </span>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => setOffset((current) => Math.max(current - PAGE_SIZE, 0))} disabled={offset === 0 || investigations.loading}>
              Previous
            </Button>
            <Button
              size="sm"
              onClick={() => setOffset((current) => current + PAGE_SIZE)}
              disabled={investigations.loading || (investigations.data?.length ?? 0) < PAGE_SIZE}
            >
              Next
            </Button>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
