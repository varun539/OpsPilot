import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IncidentTable } from "@/components/incidents/incident-table";
import type { Incident } from "@/lib/api/types";

const incident: Incident = {
  id: "1",
  incident_number: "INC-001",
  title: "Database latency",
  description: null,
  severity: "high",
  priority: "medium",
  status: "open",
  source: null,
  environment: "production",
  service_name: "checkout-api",
  created_at: "2026-11-16T10:00:00Z",
  updated_at: "2026-11-16T10:00:00Z",
  resolved_at: null,
};

describe("IncidentTable", () => {
  it("renders incident rows", () => {
    render(<IncidentTable incidents={[incident]} loading={false} />);
    expect(screen.getAllByText("Database latency").length).toBeGreaterThan(0);
    expect(screen.getAllByText("INC-001").length).toBeGreaterThan(0);
  });

  it("shows an empty state", () => {
    render(<IncidentTable incidents={[]} loading={false} filtered />);
    expect(screen.getByText("No incidents match these filters")).toBeInTheDocument();
  });

  it("shows skeletons while loading", () => {
    const { container } = render(<IncidentTable incidents={[]} loading />);
    expect(container.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
  });
});
