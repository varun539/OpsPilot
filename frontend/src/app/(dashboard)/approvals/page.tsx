"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { RefreshCw, ShieldCheck } from "lucide-react";

import { useAuth } from "@/components/layout/auth-provider";
import { ApprovalReviewDialog } from "@/components/approvals/approval-review-dialog";
import {
  ApprovalStatusBadge,
  SeverityBadge,
  WorkflowStatusBadge,
} from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Select } from "@/components/ui/field";
import { listApprovals } from "@/lib/api/approvals";
import type { ApprovalQueueItem } from "@/lib/api/types";
import { canOperate } from "@/lib/auth/roles";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

export default function ApprovalsPage() {
  const { role } = useAuth();
  const [statusFilter, setStatusFilter] = useState("pending");
  const [selected, setSelected] = useState<ApprovalQueueItem | null>(null);

  const key = useMemo(() => JSON.stringify({ statusFilter }), [statusFilter]);
  const approvals = useApiResource(
    (signal) => listApprovals({ status: statusFilter || undefined, limit: 50 }, signal),
    key,
  );

  const items = approvals.data ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Approvals"
        description="Human review queue for investigations paused by the LangGraph workflow. Decisions are recorded by the authenticated backend principal."
        action={
          <div className="flex items-end gap-2">
            <div>
              <label htmlFor="approval-status-filter" className="mb-1 block text-[11px] text-muted">Status</label>
              <Select
                id="approval-status-filter"
                value={statusFilter}
                className="w-36"
                onChange={(event) => setStatusFilter(event.target.value)}
              >
                <option value="pending">Pending</option>
                <option value="">All</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
              </Select>
            </div>
            <Button onClick={approvals.refresh} disabled={approvals.loading}>
              <RefreshCw className={`h-3.5 w-3.5 ${approvals.loading ? "animate-spin" : ""}`} aria-hidden="true" />
              Refresh
            </Button>
          </div>
        }
      />

      {!canOperate(role) ? (
        <div className="rounded-md border border-border bg-surface px-3 py-2 text-xs text-muted">
          You have read access to approval history. Approve and reject actions require the operator or admin role.
        </div>
      ) : (
        <div className="rounded-md border border-[#54411b] bg-[#251f10] px-3 py-2 text-xs text-[#fcd34d]">
          Approval reviews the investigation workflow only. It does not resolve the incident or confirm that the cause has been fixed.
        </div>
      )}

      {approvals.error ? (
        <Card>
          <ErrorState message={approvals.error} onRetry={approvals.refresh} />
        </Card>
      ) : approvals.loading ? (
        <Card>
          <CardBody className="space-y-2">
            {Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-20 w-full" />)}
          </CardBody>
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={<ShieldCheck className="h-5 w-5" aria-hidden="true" />}
            title={statusFilter === "pending" ? "No pending approvals" : "No approval records"}
            description={
              statusFilter === "pending"
                ? "Investigations awaiting human review appear here after the workflow pauses."
                : "Records appear when investigations request or record human review."
            }
          />
        </Card>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => {
            const investigation = item.investigation;
            const pending = item.approval.status === "pending";
            return (
              <li key={item.approval.id}>
                <Card className="px-4 py-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <Link href={`/incidents/${item.incident.id}`} className="font-mono text-xs text-accent hover:underline">
                          {item.incident.incident_number}
                        </Link>
                        <span className="text-sm font-medium text-foreground">{item.incident.title}</span>
                        <SeverityBadge severity={item.incident.severity} />
                      </div>
                      <p className="mt-1 line-clamp-2 text-xs text-[#c3d0e3]">
                        {investigation?.summary ?? "No investigation summary recorded."}
                      </p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
                        {investigation ? (
                          <Link href={`/investigations/${investigation.id}`} className="font-mono text-accent hover:underline">
                            {investigation.investigation_number}
                          </Link>
                        ) : (
                          <span>No investigation</span>
                        )}
                        <span>Requested {formatDateTime(item.approval.requested_at)}</span>
                        {investigation ? (
                          <WorkflowStatusBadge status={investigation.workflow_status ?? investigation.status} />
                        ) : null}
                        <ApprovalStatusBadge status={item.approval.status} />
                      </div>
                      {!pending && item.approval.decided_by ? (
                        <p className="mt-1 text-[11px] text-muted">
                          Decided by {item.approval.decided_by} · {formatDateTime(item.approval.decided_at)}
                          {item.approval.decision_reason ? ` — ${item.approval.decision_reason}` : ""}
                        </p>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 gap-2">
                      {investigation ? (
                        <Link href={`/investigations/${investigation.id}`}>
                          <Button size="sm">Inspect</Button>
                        </Link>
                      ) : null}
                      {pending && canOperate(role) && investigation ? (
                        <Button size="sm" variant="primary" onClick={() => setSelected(item)}>
                          Review decision
                        </Button>
                      ) : null}
                    </div>
                  </div>
                </Card>
              </li>
            );
          })}
        </ul>
      )}

      {selected ? (
        <ApprovalReviewDialog
          item={selected}
          onClose={() => setSelected(null)}
          onDecided={approvals.refresh}
        />
      ) : null}
    </div>
  );
}
