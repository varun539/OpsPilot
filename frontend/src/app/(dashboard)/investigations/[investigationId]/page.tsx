"use client";

import { use, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, RefreshCw, ShieldCheck } from "lucide-react";

import { useAuth } from "@/components/layout/auth-provider";
import { EvidenceViewer } from "@/components/investigations/evidence-viewer";
import { InvestigationReportView } from "@/components/investigations/investigation-report";
import { ApprovalStatusBadge, IncidentStatusBadge, WorkflowStatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { isApiError } from "@/lib/api/client";
import { getInvestigation, getInvestigationApproval, getInvestigationEvidence } from "@/lib/api/investigations";
import { getIncident } from "@/lib/api/incidents";
import type { Approval, Evidence, Incident, Investigation } from "@/lib/api/types";
import { canOperate } from "@/lib/auth/roles";
import { formatDateTime, titleCase } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

interface InvestigationBundle {
  investigation: Investigation;
  evidence: Evidence[];
  incident: Incident;
  approval: Approval | null;
}

export default function InvestigationDetailPage({
  params,
}: {
  params: Promise<{ investigationId: string }>;
}) {
  const { investigationId } = use(params);
  const { role } = useAuth();
  const [tab, setTab] = useState<"report" | "evidence">("report");

  const bundle = useApiResource<InvestigationBundle>(async (signal) => {
    const investigation = await getInvestigation(investigationId, signal);
    const [evidence, incident, approval] = await Promise.all([
      getInvestigationEvidence(investigationId, signal),
      getIncident(investigation.incident_id, signal),
      getInvestigationApproval(investigationId, signal).catch((error: unknown) => {
        if (isApiError(error) && error.status === 404) return null;
        throw error;
      }),
    ]);
    return { investigation, evidence, incident, approval };
  }, `investigation-${investigationId}`);

  if (bundle.loading && !bundle.data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (bundle.error || !bundle.data) {
    return (
      <div className="space-y-4">
        <Link href="/investigations" className="inline-flex items-center gap-1 text-xs text-muted hover:text-foreground">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to investigations
        </Link>
        <Card>
          <ErrorState message={bundle.error ?? "Investigation not found."} onRetry={bundle.refresh} />
        </Card>
      </div>
    );
  }

  const { investigation, evidence, incident, approval } = bundle.data;
  const workflowStatus = investigation.workflow_status ?? investigation.status;
  const awaitingApproval = workflowStatus === "awaiting_approval" || workflowStatus === "resume_failed";

  return (
    <div className="space-y-4">
      <Link href="/investigations" className="inline-flex items-center gap-1 text-xs text-muted hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to investigations
      </Link>

      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-base text-muted">{investigation.investigation_number}</span>
            <span>{incident.title}</span>
          </span>
        }
        description={
          <span>
            Incident{" "}
            <Link href={`/incidents/${incident.id}`} className="text-accent hover:underline">
              {incident.incident_number}
            </Link>
            {" · "}
            Workflow and incident status are tracked separately.
          </span>
        }
        action={
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={bundle.refresh} disabled={bundle.loading}>
              <RefreshCw className={`h-3.5 w-3.5 ${bundle.loading ? "animate-spin" : ""}`} aria-hidden="true" />
              Refresh
            </Button>
            {awaitingApproval && canOperate(role) ? (
              <Link href="/approvals">
                <Button variant="primary">
                  <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                  Review approval
                </Button>
              </Link>
            ) : null}
          </div>
        }
      />

      <Card>
        <CardBody className="grid gap-4 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">Workflow status</p>
            <p className="mt-1"><WorkflowStatusBadge status={workflowStatus} /></p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">Investigation status</p>
            <p className="mt-1 text-foreground">{titleCase(investigation.status)}</p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">Incident status</p>
            <p className="mt-1"><IncidentStatusBadge status={incident.status} /></p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">Approval</p>
            <p className="mt-1">
              {approval ? <ApprovalStatusBadge status={approval.status} /> : <span className="text-muted">Not requested</span>}
            </p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">Started</p>
            <p className="mt-1 text-foreground">{formatDateTime(investigation.started_at)}</p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">Completed</p>
            <p className="mt-1 text-foreground">{formatDateTime(investigation.completed_at)}</p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">Model provider</p>
            <p className="mt-1 text-foreground">
              {String((investigation.report?.provider as string | undefined) ?? "—")}
            </p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted">Investigation ID</p>
            <p className="mt-1 truncate font-mono text-[11px] text-muted">{investigation.id}</p>
          </div>
        </CardBody>
      </Card>

      {investigation.last_error ? (
        <div role="alert" className="flex items-start gap-2 rounded-md border border-[#5f2229] bg-[#2a1114] px-3 py-2 text-xs text-[#fca5a5]">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span><strong className="font-medium">Last error:</strong> {investigation.last_error}</span>
        </div>
      ) : null}

      {awaitingApproval ? (
        <div className="rounded-md border border-[#54411b] bg-[#251f10] px-3 py-2 text-xs text-[#fcd34d]">
          This workflow is paused for human approval. Approving an investigation does not resolve the
          underlying incident or confirm the suspected root cause.
        </div>
      ) : null}

      <div className="flex gap-1 border-b border-border">
        <button
          type="button"
          onClick={() => setTab("report")}
          aria-pressed={tab === "report"}
          className={`border-b-2 px-3 py-2 text-xs font-medium ${
            tab === "report" ? "border-accent text-foreground" : "border-transparent text-muted hover:text-foreground"
          }`}
        >
          Report
        </button>
        <button
          type="button"
          onClick={() => setTab("evidence")}
          aria-pressed={tab === "evidence"}
          className={`border-b-2 px-3 py-2 text-xs font-medium ${
            tab === "evidence" ? "border-accent text-foreground" : "border-transparent text-muted hover:text-foreground"
          }`}
        >
          Evidence ({evidence.length})
        </button>
      </div>

      {tab === "report" ? (
        <InvestigationReportView
          report={investigation.report ?? null}
          summary={investigation.summary ?? null}
          suspectedRootCause={investigation.suspected_root_cause ?? null}
          severity={incident.severity}
        />
      ) : (
        <div className="space-y-4">
          <EvidenceViewer evidence={evidence} />
          {approval ? (
            <Card>
              <CardHeader title="Approval record" description="Decision metadata is recorded by the backend principal" />
              <CardBody className="grid gap-3 text-xs sm:grid-cols-2">
                <p><span className="text-muted">Status: </span><ApprovalStatusBadge status={approval.status} /></p>
                <p><span className="text-muted">Requested: </span>{formatDateTime(approval.requested_at)}</p>
                <p><span className="text-muted">Decided: </span>{formatDateTime(approval.decided_at)}</p>
                <p><span className="text-muted">Decided by: </span>{approval.decided_by ?? "—"}</p>
                <p className="sm:col-span-2"><span className="text-muted">Reason: </span>{approval.decision_reason ?? "—"}</p>
              </CardBody>
            </Card>
          ) : null}
        </div>
      )}

      <p className="text-[11px] text-muted">
        Workflow state is refreshed on demand. Phase 11 execution is synchronous, so this page does
        not poll aggressively in the background.
      </p>
    </div>
  );
}
