"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, Check, X } from "lucide-react";

import { InvestigationReportView } from "@/components/investigations/investigation-report";
import { SeverityBadge, WorkflowStatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, FormError, Textarea } from "@/components/ui/field";
import { Spinner } from "@/components/ui/feedback";
import { useToast } from "@/components/ui/toast";
import { errorMessage } from "@/lib/api/client";
import { getInvestigation, getInvestigationEvidence } from "@/lib/api/investigations";
import { decideInvestigation } from "@/lib/api/investigations";
import type { ApprovalQueueItem, InvestigationApprovalResponse } from "@/lib/api/types";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { formatDateTime } from "@/lib/format";

type Decision = "approved" | "rejected";

export function ApprovalReviewDialog({
  item,
  onClose,
  onDecided,
}: {
  item: ApprovalQueueItem;
  onClose: () => void;
  onDecided: () => void;
}) {
  const { notify } = useToast();
  const [decision, setDecision] = useState<Decision | null>(null);
  const [reason, setReason] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<InvestigationApprovalResponse | null>(null);

  const investigationId = item.investigation?.id;
  const detail = useApiResource(async (signal) => {
    if (!investigationId) return null;
    const [investigation, evidence] = await Promise.all([
      getInvestigation(investigationId, signal),
      getInvestigationEvidence(investigationId, signal),
    ]);
    return { investigation, evidence };
  }, `approval-${investigationId ?? "none"}`);

  const investigation = detail.data?.investigation ?? item.investigation;
  const evidence = detail.data?.evidence ?? [];

  async function submit() {
    if (!decision || !investigationId) return;
    if (reason.length > 4000) {
      setSubmitError("Reason must be 4,000 characters or fewer.");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const response = await decideInvestigation(investigationId, {
        decision,
        reason: reason.trim() || undefined,
      });
      setResult(response);
      notify(
        `Decision recorded. Workflow status: ${response.workflow_status}.`,
        response.workflow_status === "failed" ? "error" : "success",
      );
      onDecided();
    } catch (error) {
      setSubmitError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      wide
      title={`Review approval — ${item.incident.incident_number}`}
      description={`${item.incident.title} · requested ${formatDateTime(item.approval.requested_at)}`}
      footer={
        result ? (
          <Button variant="primary" onClick={onClose}>Close</Button>
        ) : (
          <>
            <Button onClick={onClose} disabled={submitting}>Cancel</Button>
            <Button
              variant="danger"
              onClick={() => setDecision("rejected")}
              disabled={submitting || !investigation}
              aria-pressed={decision === "rejected"}
            >
              <X className="h-4 w-4" aria-hidden="true" />
              Reject…
            </Button>
            <Button
              variant="primary"
              onClick={() => setDecision("approved")}
              disabled={submitting || !investigation}
              aria-pressed={decision === "approved"}
            >
              <Check className="h-4 w-4" aria-hidden="true" />
              Approve…
            </Button>
          </>
        )
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <SeverityBadge severity={item.incident.severity} />
          <WorkflowStatusBadge status={investigation?.workflow_status ?? investigation?.status ?? null} />
          <Link href={`/incidents/${item.incident.id}`} className="text-accent hover:underline">
            Open incident
          </Link>
          {investigation ? (
            <Link href={`/investigations/${investigation.id}`} className="text-accent hover:underline">
              Open investigation
            </Link>
          ) : null}
        </div>

        {!investigation ? (
          <p className="rounded border border-border px-3 py-2 text-xs text-muted">
            No investigation record is attached to this approval.
          </p>
        ) : detail.loading ? (
          <Spinner label="Loading investigation detail…" />
        ) : detail.error ? (
          <FormError message={detail.error} />
        ) : (
          <>
            <div className="rounded border border-border bg-background px-3 py-2 text-xs text-[#c3d0e3]">
              <p className="text-[11px] uppercase tracking-wide text-muted">Executive summary</p>
              <p className="mt-1 whitespace-pre-wrap">
                {investigation.summary ?? "No summary was recorded with this investigation."}
              </p>
            </div>
            <InvestigationReportView
              report={investigation.report ?? null}
              summary={investigation.summary ?? null}
              suspectedRootCause={investigation.suspected_root_cause ?? null}
              severity={item.incident.severity}
            />
            {evidence.length > 0 ? (
              <p className="text-[11px] text-muted">
                {evidence.length} evidence records are available on the investigation detail page.
              </p>
            ) : null}
          </>
        )}

        {result ? (
          <div role="status" className="rounded border border-[#1c4633] bg-[#0e2419] px-3 py-2.5 text-xs text-[#bbf7d0]">
            <p className="font-medium">Decision submitted</p>
            <p className="mt-1">
              Workflow status: <strong>{result.workflow_status}</strong>
              {result.approval_status ? ` · Approval: ${result.approval_status}` : ""}
            </p>
            <p className="mt-0.5">{result.message}</p>
            {result.result ? (
              <p className="mt-0.5 font-mono text-[10px]">
                execution_status: {String(result.result.execution_status ?? "unknown")}
              </p>
            ) : null}
            <p className="mt-2 text-[11px] text-[#86efac]">
              This records review of the investigation only. The incident remains open unless separately resolved.
            </p>
          </div>
        ) : decision ? (
          <div className="space-y-3 rounded border border-[#54411b] bg-[#251f10] px-3 py-2.5">
            <p className="flex items-start gap-2 text-xs text-[#fcd34d]">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              You are about to <strong className="uppercase">{decision === "approved" ? "approve" : "reject"}</strong>{" "}
              investigation {investigation?.investigation_number ?? investigationId} for incident{" "}
              {item.incident.incident_number}. The backend records the acting principal; no actor field is sent by this UI.
            </p>
            <FormError message={submitError} />
            <Field label="Decision reason" htmlFor="approval-reason" hint="Optional · max 4,000 characters">
              <Textarea
                id="approval-reason"
                value={reason}
                maxLength={4000}
                rows={3}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Why is this decision appropriate?"
              />
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button variant={decision === "approved" ? "primary" : "danger"} onClick={submit} disabled={submitting}>
                {submitting ? "Submitting decision…" : decision === "approved" ? "Confirm approval" : "Confirm rejection"}
              </Button>
              <Button onClick={() => setDecision(null)} disabled={submitting}>Change decision</Button>
            </div>
          </div>
        ) : (
          <p className="text-[11px] text-muted">
            Choose Approve or Reject to review the decision before it is submitted. Approval does not
            mean the underlying technical problem has been fixed.
          </p>
        )}
      </div>
    </Dialog>
  );
}
