import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { EvidenceViewer } from "@/components/investigations/evidence-viewer";
import type { Evidence } from "@/lib/api/types";

const evidence: Evidence[] = [
  {
    id: "ev-1",
    investigation_id: "inv-1",
    evidence_type: "diagnostic",
    source: "log_analyzer",
    content: { finding: "pool saturated" },
    metadata: { tool: "log_analyzer" },
    created_at: "2026-11-16T10:00:00Z",
  },
  {
    id: "ev-2",
    investigation_id: "inv-1",
    evidence_type: "knowledge_base",
    source: "chunk-1",
    content: "Retrieved documentation",
    metadata: {},
    created_at: "2026-11-16T10:00:01Z",
  },
];

describe("EvidenceViewer", () => {
  it("filters evidence by type", async () => {
    const user = userEvent.setup();
    render(<EvidenceViewer evidence={evidence} />);

    expect(screen.getByText("2 records stored by the orchestration service")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Knowledge Base \(1\)/i }));
    expect(screen.getByText("Retrieved documentation")).toBeInTheDocument();
    expect(screen.queryByText("pool saturated")).not.toBeInTheDocument();
  });

  it("shows an empty state", () => {
    render(<EvidenceViewer evidence={[]} />);
    expect(screen.getByText("No evidence recorded")).toBeInTheDocument();
  });
});
