"use client";

import { useState } from "react";
import Link from "next/link";

import { PriorityBadge, TicketStatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { errorMessage } from "@/lib/api/client";
import { closeTicket } from "@/lib/api/tickets";
import type { Incident, Ticket } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";

export function TicketDetailDialog({
  ticket,
  incident,
  canClose,
  onClose,
  onChanged,
}: {
  ticket: Ticket;
  incident: Incident | undefined;
  canClose: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { notify } = useToast();
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onCloseTicket() {
    setSubmitting(true);
    setError(null);
    try {
      const updated = await closeTicket(ticket.id);
      notify(`Ticket ${updated.ticket_number} closed.`);
      onChanged();
      onClose();
    } catch (closeError) {
      setError(errorMessage(closeError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={ticket.title}
      description={`${ticket.ticket_number} · created ${formatDateTime(ticket.created_at)}`}
      footer={
        <>
          <Button onClick={onClose} disabled={submitting}>Close dialog</Button>
          {canClose && ticket.status !== "closed" ? (
            confirming ? (
              <Button variant="danger" onClick={onCloseTicket} disabled={submitting}>
                {submitting ? "Closing…" : "Confirm close ticket"}
              </Button>
            ) : (
              <Button variant="danger" onClick={() => setConfirming(true)}>Close ticket</Button>
            )
          ) : null}
        </>
      }
    >
      <div className="space-y-4 text-xs">
        <FormError message={error} />
        <div className="flex flex-wrap items-center gap-2">
          <PriorityBadge priority={ticket.priority} />
          <TicketStatusBadge status={ticket.status} />
        </div>
        <dl className="grid grid-cols-2 gap-3">
          <div>
            <dt className="text-[11px] uppercase tracking-wide text-muted">Incident</dt>
            <dd className="mt-0.5">
              {incident ? (
                <Link href={`/incidents/${incident.id}`} className="text-accent hover:underline">
                  {incident.incident_number} · {incident.title}
                </Link>
              ) : (
                <span className="text-muted">{ticket.incident_id}</span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-[11px] uppercase tracking-wide text-muted">Assignee</dt>
            <dd className="mt-0.5 text-foreground">{ticket.assignee ?? "Unassigned"}</dd>
          </div>
          <div>
            <dt className="text-[11px] uppercase tracking-wide text-muted">Updated</dt>
            <dd className="mt-0.5 text-foreground">{formatDateTime(ticket.updated_at)}</dd>
          </div>
          <div>
            <dt className="text-[11px] uppercase tracking-wide text-muted">Closed</dt>
            <dd className="mt-0.5 text-foreground">{formatDateTime(ticket.closed_at)}</dd>
          </div>
        </dl>
        <div>
          <p className="text-[11px] uppercase tracking-wide text-muted">Description</p>
          <p className="mt-1 whitespace-pre-wrap leading-relaxed text-[#c3d0e3]">
            {ticket.description || "No description recorded."}
          </p>
        </div>
        {confirming ? (
          <p className="rounded border border-[#5f2229] bg-[#2a1114] px-3 py-2 text-[#fca5a5]">
            Closing is a state transition enforced by the backend. Confirm that this ticket no longer needs work.
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
