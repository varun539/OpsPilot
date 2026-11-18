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
