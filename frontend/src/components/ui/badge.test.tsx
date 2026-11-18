import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  IncidentStatusBadge,
  SeverityBadge,
  WorkflowStatusBadge,
} from "@/components/ui/badge";

describe("badges", () => {
  it("renders severity labels", () => {
    render(<SeverityBadge severity="critical" />);
    expect(screen.getByTestId("severity-badge")).toHaveTextContent("critical");
  });

  it("renders workflow statuses", () => {
    render(<WorkflowStatusBadge status="awaiting_approval" />);
    expect(screen.getByTestId("workflow-status-badge")).toHaveTextContent("awaiting approval");
  });

  it("renders incident statuses", () => {
    render(<IncidentStatusBadge status="investigating" />);
    expect(screen.getByTestId("incident-status-badge")).toHaveTextContent("investigating");
  });
});
