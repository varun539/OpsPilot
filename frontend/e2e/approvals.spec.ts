import { expect, test } from "@playwright/test";

import {
  approveInDialog,
  getApprovalViaApi,
  getInvestigationViaApi,
  openApprovalReview,
  seedPendingInvestigation,
} from "./support/helpers";

test.describe("approvals", () => {
  // Runs first so the queue is guaranteed to be empty on a fresh E2E database.
  test("approval queue empty state is shown when nothing is pending", async ({
    page,
  }) => {
    await page.goto("/approvals");
    await expect(
      page.getByRole("heading", { name: "Approvals", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/No pending approvals|No approval records/),
    ).toBeVisible();
  });

  test("operator inspects the report and approves with a recorded reason", async ({
    page,
    request,
  }) => {
    const { incident, investigation } = await seedPendingInvestigation(
      request,
      "Approval approve",
    );

    await openApprovalReview(page, incident.title);

    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("heading", { name: "Executive summary" }),
    ).toBeVisible();
    await expect(
      dialog
        .getByText(/E2E investigation identified elevated checkout latency/i)
        .first(),
    ).toBeVisible();
    await expect(
      dialog.getByText("Database connection pool exhaustion"),
    ).toBeVisible();

    await approveInDialog(
      page,
      "Verified the evidence and approved the remediation plan.",
    );

    const approval = await getApprovalViaApi(request, investigation.id);
    expect(approval.status).toBe("approved");
    expect(approval.decided_by).toBe("dev-user");
    expect(approval.decision_reason).toBe(
      "Verified the evidence and approved the remediation plan.",
    );

    const updated = await getInvestigationViaApi(request, investigation.id);
    expect(updated.workflow_status).toBe("completed");
    // Approval does not resolve the underlying incident.
    expect(updated.incident_id).toBe(incident.id);
  });

  test("operator rejects with a reason and the queue shows the decision", async ({
    page,
    request,
  }) => {
    const { incident, investigation } = await seedPendingInvestigation(
      request,
      "Approval reject",
    );

    await openApprovalReview(page, incident.title);
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: /Reject/ }).click();
    await dialog
      .getByLabel("Decision reason")
      .fill("Evidence is insufficient for production changes.");
    await dialog.getByRole("button", { name: "Confirm rejection" }).click();

    await expect(dialog.getByText("Decision submitted")).toBeVisible();
    await expect(dialog.getByText(/Workflow status:.*completed/i)).toBeVisible();
    await dialog.getByRole("button", { name: "Close", exact: true }).click();

    const approval = await getApprovalViaApi(request, investigation.id);
    expect(approval.status).toBe("rejected");
    expect(approval.decision_reason).toBe(
      "Evidence is insufficient for production changes.",
    );

    await page.goto("/approvals");
    await page.getByLabel("Status").selectOption("rejected");
    const card = page.locator("li").filter({ hasText: incident.title }).first();
    await expect(card).toContainText("rejected");
    await expect(card).toContainText(
      "Evidence is insufficient for production changes.",
    );
  });
});
