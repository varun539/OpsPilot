import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  PlannerStatusNotice,
  describePlannerStatus,
  isPlannerDegraded,
} from "@/components/investigations/planner-status";

describe("PlannerStatusNotice", () => {
  it("treats the deterministic no-provider path as a degradation warning", () => {
    render(<PlannerStatusNotice status="fallback" error={null} />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "Planner degraded");
    expect(screen.getByText(/no LLM planner is configured/)).toBeInTheDocument();
  });

  it("distinguishes an invalid tool selection from a timeout", () => {
    const { unmount } = render(
      <PlannerStatusNotice status="fallback_invalid_selection" error="ValueError: unknown tool" />,
    );
    expect(screen.getByText(/outside the allow-list/)).toBeInTheDocument();
    expect(screen.getByText(/unknown tool/)).toBeInTheDocument();
    unmount();

    render(<PlannerStatusNotice status="fallback_timeout" error="LLMTimeoutError: timed out" />);
    expect(screen.getByText(/planner request timed out/)).toBeInTheDocument();
    expect(screen.getByText(/LLMTimeoutError/)).toBeInTheDocument();
  });

  it("does not present a degraded planner as success", () => {
    render(<PlannerStatusNotice status="fallback_timeout" error={null} />);
    expect(screen.queryByText(/ready for human review/i)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "Planner degraded");
  });

  it("reports a successful planner selection without warning", () => {
    render(<PlannerStatusNotice status="used" error={null} />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "Planner used");
    expect(screen.getByText(/LLM planner selected/)).toBeInTheDocument();
  });

  it("keeps historical records without a planner status readable", () => {
    render(<PlannerStatusNotice status={null} error={null} />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "Planner status unknown");
    expect(screen.getByText(/not recorded/)).toBeInTheDocument();
  });

  it("classifies status strings", () => {
    expect(isPlannerDegraded("fallback")).toBe(true);
    expect(isPlannerDegraded("fallback_provider_error")).toBe(true);
    expect(isPlannerDegraded("used")).toBe(false);
    expect(isPlannerDegraded(null)).toBe(false);
    expect(describePlannerStatus("fallback_refusal")).toMatch(/refused/);
    expect(describePlannerStatus("something-new")).toMatch(/not classified/);
  });
});
