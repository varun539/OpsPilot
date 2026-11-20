import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InvestigationReportView } from "@/components/investigations/investigation-report";

describe("InvestigationReportView", () => {
  it("renders the structured report", () => {
    render(
      <InvestigationReportView
        report={{
          summary: "Checkout latency is caused by connection pool exhaustion.",
          incident_category: "database",
          evidence_status: "sufficient",
          root_cause_hypotheses: [
            {
              title: "Pool exhaustion",
              explanation: "Connections are not released promptly.",
              supporting_chunk_ids: ["chunk-1"],
              evidence_strength: "high",
            },
          ],
          recommended_actions: [
            {
              description: "Increase pool size",
              rationale: "Reduces queueing.",
              risk_level: "medium",
              requires_human_review: true,
            },
          ],
          missing_information: ["Exact pool settings"],
          limitations: ["No production metrics"],
        }}
        summary={null}
        suspectedRootCause={null}
      />,
    );

    expect(screen.getByText("Checkout latency is caused by connection pool exhaustion.")).toBeInTheDocument();
    expect(screen.getByText("Pool exhaustion")).toBeInTheDocument();
    expect(screen.getByText(/Increase pool size/)).toBeInTheDocument();
    expect(screen.getByText("Exact pool settings")).toBeInTheDocument();
  });

  it("shows an empty state without a report", () => {
    render(<InvestigationReportView report={null} summary={null} suspectedRootCause={null} />);
    expect(screen.getByText("No report available yet")).toBeInTheDocument();
  });

  it("shows an insufficient-evidence notice without calling it a failure", () => {
    render(
      <InvestigationReportView
        report={{
          question: "Why is checkout slow?",
          report: {
            summary: "No usable evidence was available for investigation.",
            incident_category: "insufficient_evidence",
            evidence_status: "insufficient",
            root_cause_hypotheses: [],
            recommended_actions: [],
          },
          generation_status: "insufficient_evidence",
          valid: true,
          citation_valid: true,
        }}
        summary={null}
        suspectedRootCause={null}
      />,
    );

    expect(screen.getByText(/Insufficient evidence:/)).toBeInTheDocument();
    expect(screen.getByText("Executive summary")).toBeInTheDocument();
    expect(screen.queryByText("Report generation failed")).not.toBeInTheDocument();
  });

  it("shows a failure state instead of a placeholder report", () => {
    render(
      <InvestigationReportView
        report={{
          question: "Why is checkout slow?",
          report: {
            summary: "Placeholder summary text",
            incident_category: "insufficient_evidence",
            evidence_status: "insufficient",
            root_cause_hypotheses: [],
            recommended_actions: [],
          },
          generation_status: "provider_error",
          valid: false,
          citation_valid: false,
          errors: ["LLMResponseError: Gemini API request failed (status 503)"],
        }}
        summary="Placeholder summary text"
        suspectedRootCause={null}
      />,
    );

    expect(screen.getByText("Report generation failed")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/no assessment to review/i);
    expect(screen.getByText(/LLMResponseError/)).toBeInTheDocument();
    expect(screen.getByText("provider_error")).toBeInTheDocument();
    expect(screen.queryByText("Executive summary")).not.toBeInTheDocument();
    expect(screen.queryByText("Placeholder summary text")).not.toBeInTheDocument();
  });

  it("treats citation validation failures as generation failures", () => {
    render(
      <InvestigationReportView
        report={{
          report: {
            summary: "Connection pool exhaustion.",
            evidence_status: "sufficient",
          },
          generation_status: "citation_validation_failed",
          valid: false,
          citation_valid: false,
          validation_errors: ["unknown chunk id: KB-999::invented"],
        }}
        summary={null}
        suspectedRootCause={null}
      />,
    );

    expect(screen.getByText("Report generation failed")).toBeInTheDocument();
    expect(screen.getByText(/unknown chunk id/)).toBeInTheDocument();
    expect(screen.queryByText("Connection pool exhaustion.")).not.toBeInTheDocument();
  });

  it("keeps citations and provenance from the persisted envelope", () => {
    render(
      <InvestigationReportView
        report={{
          question: "Why is checkout slow?",
          report: {
            summary: "Pool exhaustion is the leading hypothesis.",
            incident_category: "database",
            evidence_status: "sufficient",
            root_cause_hypotheses: [],
            recommended_actions: [],
          },
          source_references: [
            {
              chunk_id: "kb-e2e-1",
              doc_id: "doc-e2e-1",
              title: "Checkout latency runbook",
              source_path: "runbooks/checkout-latency.md",
            },
          ],
          provider: "fake-deterministic",
          generation_status: "success",
          citation_valid: true,
          retrieved_evidence: [],
          retrieval_metadata: { rewritten: false },
          valid: true,
        }}
        summary={null}
        suspectedRootCause={null}
      />,
    );

    expect(screen.getByText("Checkout latency runbook")).toBeInTheDocument();
    expect(screen.getByText(/kb-e2e-1/)).toBeInTheDocument();
  });
});
