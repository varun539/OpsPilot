"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { errorMessage } from "@/lib/api/client";
import { startInvestigation } from "@/lib/api/investigations";
import type { Incident, InvestigationStartRequest } from "@/lib/api/types";

const TEXT_LIMIT = 200_000;
const FIELD_LIMIT = 200_000;

interface FormState {
  question: string;
  incident_context: string;
  logs: string;
  stack_trace: string;
  http_status: string;
  response_text: string;
  database_error: string;
  configuration: string;
  configuration_rules: string;
  response_headers: string;
  events: string;
  retrieval_filters: string;
  requested_top_k: string;
}

function initialForm(incident: Incident): FormState {
  return {
    question: "",
    incident_context: [
      `Incident: ${incident.incident_number}`,
      `Title: ${incident.title}`,
      `Severity: ${incident.severity}`,
      `Environment: ${incident.environment ?? "unknown"}`,
      `Service: ${incident.service_name ?? "unknown"}`,
      incident.description ? `Description: ${incident.description}` : "",
    ]
      .filter(Boolean)
      .join("\n"),
    logs: "",
    stack_trace: "",
    http_status: "",
    response_text: "",
    database_error: "",
    configuration: "",
    configuration_rules: "",
    response_headers: "",
    events: "",
    retrieval_filters: "",
    requested_top_k: "",
  };
}

export function StartInvestigationDialog({
  incident,
  open,
  onClose,
}: {
  incident: Incident;
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const { notify } = useToast();
  const [form, setForm] = useState<FormState>(() => initialForm(incident));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function update(key: keyof FormState, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function parseJsonField(
    key: keyof FormState,
    expected: "object" | "array",
    nextErrors: Record<string, string>,
  ): unknown {
    const raw = form[key].trim();
    if (!raw) return undefined;
    try {
      const parsed = JSON.parse(raw) as unknown;
      const valid =
        expected === "array"
          ? Array.isArray(parsed)
          : typeof parsed === "object" && parsed !== null && !Array.isArray(parsed);
      if (!valid) {
        nextErrors[key] = `Must be a JSON ${expected}.`;
        return undefined;
      }
      return parsed;
    } catch {
      nextErrors[key] = "Invalid JSON.";
      return undefined;
    }
  }

  function buildPayload(): InvestigationStartRequest | null {
    const nextErrors: Record<string, string> = {};
    const textFields: (keyof FormState)[] = [
      "question",
      "incident_context",
      "logs",
      "stack_trace",
      "response_text",
      "database_error",
    ];

    const textTotal = textFields.reduce(
      (total, key) => total + form[key].length,
      0,
    );
    if (textTotal > TEXT_LIMIT) {
      nextErrors._total = `Combined text input is ${textTotal.toLocaleString()} characters; the API limit is ${TEXT_LIMIT.toLocaleString()}.`;
    }
    if (form.question.trim() && form.question.length > 10_000) {
      nextErrors.question = "Question must be 10,000 characters or fewer.";
    }
    if (form.response_text.length > 100_000) {
      nextErrors.response_text = "Response text must be 100,000 characters or fewer.";
    }
    for (const key of textFields) {
      if (form[key].length > FIELD_LIMIT) {
        nextErrors[key] = `This field must be ${FIELD_LIMIT.toLocaleString()} characters or fewer.`;
      }
    }

    let httpStatus: number | undefined;
    if (form.http_status.trim()) {
      const parsed = Number(form.http_status);
      if (!Number.isInteger(parsed) || parsed < 100 || parsed > 599) {
        nextErrors.http_status = "HTTP status must be an integer between 100 and 599.";
      } else {
        httpStatus = parsed;
      }
    }

    let topK: number | undefined;
    if (form.requested_top_k.trim()) {
      const parsed = Number(form.requested_top_k);
      if (!Number.isInteger(parsed) || parsed < 1 || parsed > 50) {
        nextErrors.requested_top_k = "Top K must be between 1 and 50.";
      } else {
        topK = parsed;
      }
    }

    const configuration = parseJsonField("configuration", "object", nextErrors);
    const configurationRules = parseJsonField("configuration_rules", "object", nextErrors);
    const responseHeaders = parseJsonField("response_headers", "object", nextErrors) as
      | Record<string, unknown>
      | undefined;
    const events = parseJsonField("events", "array", nextErrors) as
      | Record<string, unknown>[]
      | undefined;
    const retrievalFilters = parseJsonField("retrieval_filters", "object", nextErrors);

    if (responseHeaders) {
      const invalid = Object.values(responseHeaders).some((value) => typeof value !== "string");
      if (invalid) nextErrors.response_headers = "Header values must all be strings.";
    }

    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return null;

    const payload: InvestigationStartRequest = {};
    if (form.question.trim()) payload.question = form.question.trim();
    if (form.incident_context.trim()) payload.incident_context = form.incident_context.trim();
    if (form.logs.trim()) payload.logs = form.logs;
    if (form.stack_trace.trim()) payload.stack_trace = form.stack_trace;
    if (httpStatus !== undefined) payload.http_status = httpStatus;
    if (form.response_text.trim()) payload.response_text = form.response_text;
    if (form.database_error.trim()) payload.database_error = form.database_error;
    if (configuration !== undefined) payload.configuration = configuration as Record<string, unknown>;
    if (configurationRules !== undefined) {
      payload.configuration_rules = configurationRules as Record<string, unknown>;
    }
    if (responseHeaders !== undefined) {
      payload.response_headers = responseHeaders as Record<string, string>;
    }
    if (events !== undefined) payload.events = events;
    if (retrievalFilters !== undefined) {
      payload.retrieval_filters = retrievalFilters as Record<string, unknown>;
    }
    if (topK !== undefined) payload.requested_top_k = topK;
    return payload;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitError(null);
    const payload = buildPayload();
    if (!payload) return;

    setSubmitting(true);
    try {
      const result = await startInvestigation(incident.id, payload);
      notify(
        result.workflow_status === "awaiting_approval"
          ? "Investigation paused for human approval."
          : `Investigation ${result.workflow_status}.`,
      );
      onClose();
      router.push(`/investigations/${result.investigation_id}`);
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
      wide
      title={`Start AI investigation — ${incident.incident_number}`}
      description="Submits stored incident context plus any technical evidence you provide to the Phase 11 orchestration API. The server creates the workflow thread."
      footer={
        <>
          <Button onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button variant="primary" type="submit" form="start-investigation-form" disabled={submitting}>
            {submitting ? "Starting investigation…" : "Start investigation"}
          </Button>
        </>
      }
    >
      <form id="start-investigation-form" onSubmit={onSubmit} className="space-y-4" noValidate>
        <FormError message={submitError ?? errors._total ?? null} />

        <Field
          label="Investigation question"
          htmlFor="investigation-question"
          hint="Optional · blank uses the incident title"
        >
          <Input
            id="investigation-question"
            value={form.question}
            maxLength={10_000}
            onChange={(event) => update("question", event.target.value)}
            placeholder="Why is checkout latency elevated?"
          />
        </Field>

        <Field
          label="Incident context"
          htmlFor="investigation-context"
          hint={`${form.incident_context.length.toLocaleString()} / ${TEXT_LIMIT.toLocaleString()}`}
          error={errors.incident_context}
        >
          <Textarea
            id="investigation-context"
            value={form.incident_context}
            rows={5}
            onChange={(event) => update("incident_context", event.target.value)}
          />
        </Field>

        <details className="rounded-md border border-border">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted hover:text-foreground">
            Technical evidence (optional)
          </summary>
          <div className="space-y-4 border-t border-border px-3 py-3">
            <Field label="Logs" htmlFor="investigation-logs" hint={`${form.logs.length.toLocaleString()} chars`} error={errors.logs}>
              <Textarea
                id="investigation-logs"
                value={form.logs}
                rows={5}
                onChange={(event) => update("logs", event.target.value)}
                placeholder="Paste relevant application or platform logs"
              />
            </Field>
            <Field label="Stack trace" htmlFor="investigation-stack" error={errors.stack_trace}>
              <Textarea
                id="investigation-stack"
                value={form.stack_trace}
                rows={5}
                onChange={(event) => update("stack_trace", event.target.value)}
                placeholder="Paste the exception stack trace"
              />
            </Field>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="HTTP status" htmlFor="investigation-http-status" error={errors.http_status}>
                <Input
                  id="investigation-http-status"
                  inputMode="numeric"
                  value={form.http_status}
                  onChange={(event) => update("http_status", event.target.value)}
                  placeholder="502"
                />
              </Field>
              <Field label="Requested top K" htmlFor="investigation-top-k" hint="1–50" error={errors.requested_top_k}>
                <Input
                  id="investigation-top-k"
                  inputMode="numeric"
                  value={form.requested_top_k}
                  onChange={(event) => update("requested_top_k", event.target.value)}
                  placeholder="8"
                />
              </Field>
            </div>
            <Field label="HTTP response body" htmlFor="investigation-response-text" error={errors.response_text}>
              <Textarea
                id="investigation-response-text"
                value={form.response_text}
                rows={4}
                onChange={(event) => update("response_text", event.target.value)}
              />
            </Field>
            <Field label="Database error" htmlFor="investigation-db-error" error={errors.database_error}>
              <Textarea
                id="investigation-db-error"
                value={form.database_error}
                rows={3}
                onChange={(event) => update("database_error", event.target.value)}
                placeholder="deadlock detected, connection pool exhausted…"
              />
            </Field>
            <div className="grid gap-4 lg:grid-cols-2">
              <Field label="Response headers (JSON object)" htmlFor="investigation-headers" error={errors.response_headers}>
                <Textarea
                  id="investigation-headers"
                  value={form.response_headers}
                  rows={4}
                  onChange={(event) => update("response_headers", event.target.value)}
                  placeholder={'{"content-type": "application/json"}'}
                />
              </Field>
              <Field label="Configuration (JSON object)" htmlFor="investigation-config" error={errors.configuration}>
                <Textarea
                  id="investigation-config"
                  value={form.configuration}
                  rows={4}
                  onChange={(event) => update("configuration", event.target.value)}
                  placeholder={'{"timeout_ms": 300}'}
                />
              </Field>
              <Field label="Configuration rules (JSON object)" htmlFor="investigation-rules" error={errors.configuration_rules}>
                <Textarea
                  id="investigation-rules"
                  value={form.configuration_rules}
                  rows={4}
                  onChange={(event) => update("configuration_rules", event.target.value)}
                />
              </Field>
              <Field label="Retrieval filters (JSON object)" htmlFor="investigation-filters" error={errors.retrieval_filters}>
                <Textarea
                  id="investigation-filters"
                  value={form.retrieval_filters}
                  rows={4}
                  onChange={(event) => update("retrieval_filters", event.target.value)}
                />
              </Field>
            </div>
            <Field label="Events (JSON array)" htmlFor="investigation-events" error={errors.events}>
              <Textarea
                id="investigation-events"
                value={form.events}
                rows={4}
                onChange={(event) => update("events", event.target.value)}
                placeholder={'[{"timestamp": "2026-11-16T09:00:00Z", "message": "…"}]'}
              />
            </Field>
          </div>
        </details>

        <p className="text-[11px] leading-relaxed text-muted">
          The API enforces a combined 200,000-character text limit. Approval is
          required before workflow completion when the agent pauses for human review.
        </p>
      </form>
    </Dialog>
  );
}
