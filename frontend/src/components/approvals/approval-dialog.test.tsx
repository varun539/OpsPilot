import { render, screen, waitFor } from "@testing-library/react";
import { ToastProvider } from "@/components/ui/toast";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApprovalReviewDialog } from "@/components/approvals/approval-review-dialog";
import { getInvestigation } from "@/lib/api/investigations";
import type { ApprovalQueueItem, Investigation } from "@/lib/api/types";

vi.mock("@/lib/api/investigations", () => ({
  getInvestigation: vi.fn().mockResolvedValue({
    id: "inv-1",
    investigation_number: "INV-001",
    summary: "Investigation summary",
    report: null,
    workflow_status: "awaiting_approval",
    status: "running",
    suspected_root_cause: null,
    started_at: "2026-11-16T10:00:00Z",
  }),
  getInvestigationEvidence: vi.fn().mockResolvedValue([]),
  decideInvestigation: vi.fn().mockResolvedValue({
    investigation_id: "inv-1",
    incident_id: "inc-1",
    workflow_status: "completed",
    approval_status: "approved",
    message: "investigation resumed",
    result: { execution_status: "completed" },
  }),
}));

const item: ApprovalQueueItem = {
  approval: {
    id: "ap-1",
    incident_id: "inc-1",
    investigation_id: "inv-1",
    approval_type: "human_review",
    status: "pending",
    requested_at: "2026-11-16T10:00:00Z",
    decided_at: null,
    decided_by: null,
    decision_reason: null,
    created_at: "2026-11-16T10:00:00Z",
  },
  incident: {
    id: "inc-1",
    incident_number: "INC-001",
    title: "Database latency",
    description: null,
    severity: "high",
    priority: "medium",
    status: "awaiting_approval",
    source: null,
    environment: "production",
    service_name: "checkout-api",
    created_at: "2026-11-16T10:00:00Z",
    updated_at: "2026-11-16T10:00:00Z",
    resolved_at: null,
  },
  investigation: {
    id: "inv-1",
    incident_id: "inc-1",
    investigation_number: "INV-001",
    status: "running",
    summary: "Investigation summary",
    suspected_root_cause: null,
    report: null,
    thread_id: "thread-1",
    workflow_status: "awaiting_approval",
    last_error: null,
    started_at: "2026-11-16T10:00:00Z",
    completed_at: null,
    created_at: "2026-11-16T10:00:00Z",
    updated_at: "2026-11-16T10:00:00Z",
  },
};

describe("ApprovalReviewDialog", () => {
  it("submits an approval and shows the workflow result", async () => {
    const user = userEvent.setup();
    const onDecided = vi.fn();
    render(
      <ToastProvider>
        <ApprovalReviewDialog item={item} onClose={() => undefined} onDecided={onDecided} />
      </ToastProvider>,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: /Approve…/i })).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /Approve…/i }));
    await user.click(screen.getByRole("button", { name: /Confirm approval/i }));

    await waitFor(() => expect(screen.getByText("Decision submitted")).toBeInTheDocument());
    expect(screen.getByText(/Workflow status: completed/)).toBeInTheDocument();
    expect(onDecided).toHaveBeenCalled();
  });

  it("blocks approval and shows the failure when report generation failed", async () => {
    vi.mocked(getInvestigation).mockResolvedValueOnce({
      ...(item.investigation ?? {}),
      report: {
        question: "q",
        report: {
          summary: "Gemini API request failed (status 503)",
          incident_category: "insufficient_evidence",
          evidence_status: "insufficient",
        },
        generation_status: "provider_error",
        valid: false,
        citation_valid: false,
        errors: ["LLMResponseError: Gemini API request failed (status 503)"],
      },
    } as unknown as Investigation);

    render(
      <ToastProvider>
        <ApprovalReviewDialog item={item} onClose={() => undefined} onDecided={() => undefined} />
      </ToastProvider>,
    );

    await waitFor(() =>
      expect(screen.getByText(/approval is unavailable/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /Approve…/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Reject…/i })).toBeEnabled();
    expect(screen.getByText("Report generation failed")).toBeInTheDocument();
  });
});
