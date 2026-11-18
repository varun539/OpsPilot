"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Plus, RefreshCw } from "lucide-react";

import { useAuth } from "@/components/layout/auth-provider";
import { CreateTicketDialog } from "@/components/tickets/create-ticket-dialog";
import { TicketDetailDialog } from "@/components/tickets/ticket-detail-dialog";
import { PriorityBadge, TicketStatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Select } from "@/components/ui/field";
import { listIncidents } from "@/lib/api/incidents";
import { listTickets } from "@/lib/api/tickets";
import type { Ticket } from "@/lib/api/types";
import { canOperate } from "@/lib/auth/roles";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const PAGE_SIZE = 20;

export default function TicketsPage() {
  const { role } = useAuth();
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [offset, setOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<Ticket | null>(null);

  const query = useMemo(
    () => ({ status: status || undefined, priority: priority || undefined, limit: PAGE_SIZE, offset }),
    [status, priority, offset],
  );

  const tickets = useApiResource((signal) => listTickets(query, signal), JSON.stringify(query));
  const incidents = useApiResource(
    (signal) => listIncidents({ limit: 100 }, signal),
    "ticket-incident-map",
  );

  const incidentMap = useMemo(
    () => new Map((incidents.data ?? []).map((incident) => [incident.id, incident])),
    [incidents.data],
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="Tickets"
        description="Support tickets linked to incidents. Ticket transitions remain enforced by the backend."
        action={
          canOperate(role) ? (
            <Button variant="primary" onClick={() => setCreateOpen(true)} disabled={incidents.loading || (incidents.data?.length ?? 0) === 0}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              New ticket
            </Button>
          ) : null
        }
      />

      <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-border px-4 py-3">
          <div>
            <label htmlFor="ticket-status-filter" className="mb-1 block text-[11px] text-muted">Status</label>
            <Select
              id="ticket-status-filter"
              value={status}
              className="w-40"
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
            >
              {["", "open", "in_progress", "blocked", "resolved", "closed"].map((option) => (
                <option key={option || "all"} value={option}>
                  {option ? option.replace(/_/g, " ") : "All"}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <label htmlFor="ticket-priority-filter" className="mb-1 block text-[11px] text-muted">Priority</label>
            <Select
              id="ticket-priority-filter"
              value={priority}
              className="w-36"
              onChange={(event) => {
                setPriority(event.target.value);
                setOffset(0);
              }}
            >
              {["", "critical", "urgent", "p1", "high", "p2", "medium", "p3", "low", "p4"].map((option) => (
                <option key={option || "all"} value={option}>{option || "All"}</option>
              ))}
            </Select>
          </div>
          <Button onClick={tickets.refresh} disabled={tickets.loading}>
            <RefreshCw className={`h-3.5 w-3.5 ${tickets.loading ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </Button>
        </div>

        <CardHeader title="Ticket queue" description={`Page ${Math.floor(offset / PAGE_SIZE) + 1}`} />

        {tickets.error ? (
          <ErrorState message={tickets.error} onRetry={tickets.refresh} />
        ) : tickets.loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, index) => <Skeleton key={index} className="h-10 w-full" />)}
          </div>
        ) : (tickets.data ?? []).length === 0 ? (
          <EmptyState
            title={status || priority ? "No tickets match these filters" : "No tickets yet"}
            description={
              status || priority
                ? "Adjust the status or priority filter to see more tickets."
                : "Create a ticket from this page and link it to an incident."
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-border text-[11px] uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-medium">Ticket</th>
                  <th scope="col" className="px-4 py-2 font-medium">Incident</th>
                  <th scope="col" className="px-4 py-2 font-medium">Priority</th>
                  <th scope="col" className="px-4 py-2 font-medium">Status</th>
                  <th scope="col" className="px-4 py-2 font-medium">Assignee</th>
                  <th scope="col" className="px-4 py-2 font-medium">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {(tickets.data ?? []).map((ticket) => {
                  const incident = incidentMap.get(ticket.incident_id);
                  return (
                    <tr key={ticket.id} className="cursor-pointer hover:bg-surface-raised/50" onClick={() => setSelected(ticket)}>
                      <td className="px-4 py-2.5">
                        <button
                          type="button"
                          className="text-left font-medium text-foreground hover:text-accent"
                          onClick={() => setSelected(ticket)}
                        >
                          {ticket.title}
                        </button>
                        <span className="block text-[11px] text-muted">{ticket.ticket_number}</span>
                      </td>
                      <td className="px-4 py-2.5">
                        {incident ? (
                          <Link
                            href={`/incidents/${incident.id}`}
                            onClick={(event) => event.stopPropagation()}
                            className="text-accent hover:underline"
                          >
                            {incident.incident_number}
                          </Link>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5"><PriorityBadge priority={ticket.priority} /></td>
                      <td className="px-4 py-2.5"><TicketStatusBadge status={ticket.status} /></td>
                      <td className="px-4 py-2.5 text-muted">{ticket.assignee ?? "Unassigned"}</td>
                      <td className="whitespace-nowrap px-4 py-2.5 text-muted">{formatDateTime(ticket.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <CardBody className="flex items-center justify-between border-t border-border py-2.5">
          <span className="text-[11px] text-muted">
            {tickets.loading ? "Loading…" : `${tickets.data?.length ?? 0} shown`}
          </span>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => setOffset((current) => Math.max(current - PAGE_SIZE, 0))} disabled={offset === 0 || tickets.loading}>
              Previous
            </Button>
            <Button
              size="sm"
              onClick={() => setOffset((current) => current + PAGE_SIZE)}
              disabled={tickets.loading || (tickets.data?.length ?? 0) < PAGE_SIZE}
            >
              Next
            </Button>
          </div>
        </CardBody>
      </Card>

      <CreateTicketDialog
        incidents={incidents.data ?? []}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={tickets.refresh}
      />

      {selected ? (
        <TicketDetailDialog
          ticket={selected}
          incident={incidentMap.get(selected.incident_id)}
          canClose={canOperate(role)}
          onClose={() => setSelected(null)}
          onChanged={tickets.refresh}
        />
      ) : null}
    </div>
  );
}
