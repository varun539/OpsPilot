import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

import { IncidentStatusBadge, PriorityBadge, SeverityBadge } from "@/components/ui/badge";
import { EmptyState, Skeleton } from "@/components/ui/feedback";
import { formatDateTime } from "@/lib/format";
import type { Incident } from "@/lib/api/types";

export function IncidentTable({
  incidents,
  loading,
  emptyAction,
  filtered = false,
}: {
  incidents: Incident[];
  loading: boolean;
  emptyAction?: React.ReactNode;
  filtered?: boolean;
}) {
  if (loading) {
    return (
      <div className="space-y-2 p-4" role="status" aria-label="Loading incidents">
        {Array.from({ length: 5 }).map((_, index) => (
          <Skeleton key={index} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (incidents.length === 0) {
    return (
      <EmptyState
        title={filtered ? "No incidents match these filters" : "No incidents yet"}
        description={
          filtered
            ? "Adjust the search, severity, or status filters to see more incidents."
            : "Create the first incident to begin an investigation workflow."
        }
        action={emptyAction}
      />
    );
  }

  return (
    <>
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-border text-[11px] uppercase tracking-wide text-muted">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">Incident</th>
              <th scope="col" className="px-4 py-2 font-medium">Severity</th>
              <th scope="col" className="px-4 py-2 font-medium">Priority</th>
              <th scope="col" className="px-4 py-2 font-medium">Status</th>
              <th scope="col" className="px-4 py-2 font-medium">Service</th>
              <th scope="col" className="px-4 py-2 font-medium">Environment</th>
              <th scope="col" className="px-4 py-2 font-medium">Created</th>
              <th scope="col" className="px-4 py-2"><span className="sr-only">Open</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {incidents.map((incident) => (
              <tr key={incident.id} className="hover:bg-surface-raised/50">
                <td className="max-w-[22rem] px-4 py-2.5">
                  <Link
                    href={`/incidents/${incident.id}`}
                    className="block truncate font-medium text-foreground hover:text-accent"
                  >
                    {incident.title}
                  </Link>
                  <span className="text-[11px] text-muted">{incident.incident_number}</span>
                </td>
                <td className="px-4 py-2.5"><SeverityBadge severity={incident.severity} /></td>
                <td className="px-4 py-2.5"><PriorityBadge priority={incident.priority} /></td>
                <td className="px-4 py-2.5"><IncidentStatusBadge status={incident.status} /></td>
                <td className="max-w-[10rem] truncate px-4 py-2.5 text-muted">
                  {incident.service_name ?? "—"}
                </td>
                <td className="px-4 py-2.5 text-muted">{incident.environment ?? "—"}</td>
                <td className="whitespace-nowrap px-4 py-2.5 text-muted">
                  {formatDateTime(incident.created_at)}
                </td>
                <td className="px-4 py-2.5">
                  <Link
                    href={`/incidents/${incident.id}`}
                    aria-label={`Open ${incident.incident_number}`}
                    className="text-muted hover:text-accent"
                  >
                    <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul className="divide-y divide-border md:hidden">
        {incidents.map((incident) => (
          <li key={incident.id}>
            <Link href={`/incidents/${incident.id}`} className="block px-4 py-3 hover:bg-surface-raised/50">
              <div className="flex items-start justify-between gap-2">
                <span className="text-xs font-medium text-foreground">{incident.title}</span>
                <SeverityBadge severity={incident.severity} />
              </div>
              <p className="mt-1 text-[11px] text-muted">
                {incident.incident_number} · {incident.service_name ?? "No service"} ·{" "}
                {formatDateTime(incident.created_at)}
              </p>
              <div className="mt-1.5 flex items-center gap-1.5">
                <IncidentStatusBadge status={incident.status} />
                <PriorityBadge priority={incident.priority} />
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
