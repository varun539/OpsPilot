"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { createIncident } from "@/lib/api/incidents";
import { errorMessage } from "@/lib/api/client";
import type { IncidentCreateRequest } from "@/lib/api/types";

const SEVERITIES = ["critical", "high", "medium", "low", "info"];
const PRIORITIES = ["critical", "high", "medium", "low", "urgent", "p1", "p2", "p3", "p4"];

const INITIAL = {
  title: "",
  description: "",
  severity: "medium",
  priority: "medium",
  source: "",
  environment: "",
  service_name: "",
};

export function CreateIncidentDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const { notify } = useToast();
  const [form, setForm] = useState(INITIAL);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function update(key: keyof typeof INITIAL, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function validate(): boolean {
    const next: Record<string, string> = {};
    const title = form.title.trim();
    if (!title) next.title = "Title is required.";
    else if (title.length > 500) next.title = "Title must be 500 characters or fewer.";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitError(null);
    if (!validate()) return;

    const payload: IncidentCreateRequest = {
      title: form.title.trim(),
      severity: form.severity,
      priority: form.priority,
      status: "open",
    };
    if (form.description.trim()) payload.description = form.description.trim();
    if (form.source.trim()) payload.source = form.source.trim();
    if (form.environment.trim()) payload.environment = form.environment.trim();
    if (form.service_name.trim()) payload.service_name = form.service_name.trim();

    setSubmitting(true);
    try {
      const incident = await createIncident(payload);
      notify(`Incident ${incident.incident_number} created.`);
      setForm(INITIAL);
      onClose();
      router.push(`/incidents/${incident.id}`);
    } catch (error) {
      setSubmitError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Create incident"
      description="Records are created directly through the OpsPilot API."
      footer={
        <>
          <Button onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button variant="primary" type="submit" form="create-incident-form" disabled={submitting}>
            {submitting ? "Creating…" : "Create incident"}
          </Button>
        </>
      }
    >
      <form id="create-incident-form" onSubmit={onSubmit} className="space-y-4" noValidate>
        <FormError message={submitError} />
        <Field label="Title" htmlFor="incident-title" required error={errors.title}>
          <Input
            id="incident-title"
            value={form.title}
            maxLength={500}
            onChange={(event) => update("title", event.target.value)}
            placeholder="Checkout API latency spike"
          />
        </Field>
        <Field label="Description" htmlFor="incident-description">
          <Textarea
            id="incident-description"
            value={form.description}
            onChange={(event) => update("description", event.target.value)}
            placeholder="Observed symptoms, scope, and relevant context"
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Severity" htmlFor="incident-severity">
            <Select
              id="incident-severity"
              value={form.severity}
              onChange={(event) => update("severity", event.target.value)}
            >
              {SEVERITIES.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </Select>
          </Field>
          <Field label="Priority" htmlFor="incident-priority">
            <Select
              id="incident-priority"
              value={form.priority}
              onChange={(event) => update("priority", event.target.value)}
            >
              {PRIORITIES.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </Select>
          </Field>
          <Field label="Service" htmlFor="incident-service">
            <Input
              id="incident-service"
              value={form.service_name}
              onChange={(event) => update("service_name", event.target.value)}
              placeholder="checkout-api"
            />
          </Field>
          <Field label="Environment" htmlFor="incident-environment">
            <Input
              id="incident-environment"
              value={form.environment}
              onChange={(event) => update("environment", event.target.value)}
              placeholder="production"
            />
          </Field>
        </div>
        <Field label="Source" htmlFor="incident-source" hint="Optional">
          <Input
            id="incident-source"
            value={form.source}
            onChange={(event) => update("source", event.target.value)}
            placeholder="pagerduty"
          />
        </Field>
      </form>
    </Dialog>
  );
}
