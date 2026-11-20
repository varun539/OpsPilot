import Link from "next/link";

import { SeverityBadge, WorkflowStatusBadge } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState, Skeleton } from "@/components/ui/feedback";
import { formatDateTime } from "@/lib/format";
import type { Incident, Investigation } from "@/lib/api/types";

export function RecentIncidents({
  incidents,
  loading,
}: {
  incidents: Incident[];
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader
        title="Recent incidents"
        description="Newest incidents, ordered by creation time"
        action={
          <Link href="/incidents" className="text-xs text-accent hover:underline">
            View all
          </Link>
        }
      />
      {loading ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : incidents.length === 0 ? (
        <EmptyState
          title="No incidents recorded"
          description="Incidents created in OpsPilot will appear here."
        />
      ) : (
        <ul className="divide-y divide-border">
          {incidents.map((incident) => (
            <li key={incident.id}>
              <Link
                href={`/incidents/${incident.id}`}
                className="flex items-start gap-3 px-4 py-2.5 hover:bg-surface-raised/60"
              >
                <span className="mt-0.5 shrink-0">
                  <SeverityBadge severity={incident.severity} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-foreground">
                    {incident.incident_number} · {incident.title}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-muted">
                    {incident.service_name ?? "No service"} · {formatDateTime(incident.created_at)}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function RecentInvestigations({
  investigations,
  loading,
}: {
  investigations: Investigation[];
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader
        title="Recent investigations"
        description="Latest AI workflow runs"
        action={
          <Link href="/investigations" className="text-xs text-accent hover:underline">
            View all
          </Link>
        }
      />
      {loading ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : investigations.length === 0 ? (
        <EmptyState
          title="No investigations yet"
          description="Start an investigation from an incident to see workflow activity."
        />
      ) : (
        <ul className="divide-y divide-border">
          {investigations.map((investigation) => (
            <li key={investigation.id}>
              <Link
                href={`/investigations/${investigation.id}`}
                className="flex items-start gap-3 px-4 py-2.5 hover:bg-surface-raised/60"
              >
                <span className="mt-0.5 shrink-0">
                  <WorkflowStatusBadge status={investigation.workflow_status ?? investigation.status} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-foreground">
                    {investigation.investigation_number}
                  </span>
                  <span className="mt-0.5 block truncate text-[11px] text-muted">
                    {investigation.summary ?? "No summary yet"} · {formatDateTime(investigation.started_at)}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
