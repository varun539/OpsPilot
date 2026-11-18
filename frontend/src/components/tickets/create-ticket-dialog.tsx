"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { errorMessage } from "@/lib/api/client";
import { createIncidentTicket } from "@/lib/api/incidents";
import type { Incident } from "@/lib/api/types";

const PRIORITIES = ["critical", "high", "medium", "low", "urgent", "p1", "p2", "p3", "p4"];

export function CreateTicketDialog({
  incidents,
  open,
  onClose,
  onCreated,
}: {
  incidents: Incident[];
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { notify } = useToast();
  const [incidentId, setIncidentId] = useState(incidents[0]?.id ?? "");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("medium");
  const [assignee, setAssignee] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [titleError, setTitleError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const effectiveIncidentId = incidentId || (incidents[0]?.id ?? "");

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setTitleError(null);
    if (!effectiveIncidentId) {
      setError("Select the incident this ticket belongs to.");
      return;
    }
    if (!title.trim()) {
      setTitleError("Title is required.");
      return;
    }
    if (title.trim().length > 500) {
      setTitleError("Title must be 500 characters or fewer.");
      return;
    }

    setSubmitting(true);
    try {
      const ticket = await createIncidentTicket(effectiveIncidentId, {
        title: title.trim(),
        description: description.trim() || undefined,
        priority,
        assignee: assignee.trim() || undefined,
        status: "open",
      });
      notify(`Ticket ${ticket.ticket_number} created.`);
      setTitle("");
      setDescription("");
      setAssignee("");
      setPriority("medium");
      onCreated();
      onClose();
    } catch (submitError) {
      setError(errorMessage(submitError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Create ticket"
      description="Tickets are linked to an existing incident through the incident ticket endpoint."
      footer={
        <>
          <Button onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button
            variant="primary"
            type="submit"
            form="create-ticket-form"
            disabled={submitting || incidents.length === 0}
          >
            {submitting ? "Creating…" : "Create ticket"}
          </Button>
        </>
      }
    >
      <form id="create-ticket-form" onSubmit={onSubmit} className="space-y-4" noValidate>
        <FormError message={error} />
        {incidents.length === 0 ? (
          <p className="text-xs text-[#fcd34d]">
            No incidents are available in the loaded set. Create an incident before adding a ticket.
          </p>
        ) : null}
        <Field label="Incident" htmlFor="ticket-incident" required>
          <Select
            id="ticket-incident"
            value={effectiveIncidentId}
            onChange={(event) => setIncidentId(event.target.value)}
          >
            {incidents.map((incident) => (
              <option key={incident.id} value={incident.id}>
                {incident.incident_number} — {incident.title}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Title" htmlFor="ticket-title" required error={titleError}>
          <Input
            id="ticket-title"
            value={title}
            maxLength={500}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Rotate expiring database credentials"
          />
        </Field>
        <Field label="Description" htmlFor="ticket-description">
          <Textarea
            id="ticket-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Priority" htmlFor="ticket-priority">
            <Select id="ticket-priority" value={priority} onChange={(event) => setPriority(event.target.value)}>
              {PRIORITIES.map((option) => <option key={option} value={option}>{option}</option>)}
            </Select>
          </Field>
          <Field label="Assignee" htmlFor="ticket-assignee" hint="Optional">
            <Input
              id="ticket-assignee"
              value={assignee}
              onChange={(event) => setAssignee(event.target.value)}
              placeholder="on-call-engineer"
            />
          </Field>
        </div>
      </form>
    </Dialog>
  );
}
