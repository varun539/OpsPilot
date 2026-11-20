import { expect, test } from "@playwright/test";

import { API_URL } from "./support/backend";
import {
  createIncidentViaApi,
  createIncidentViaUi,
  uniqueTitle,
} from "./support/helpers";

test.describe("investigations", () => {
  test("operator starts an investigation and the persisted report renders", async ({
    page,
  }) => {
    const title = uniqueTitle("Investigation UI incident");
    await createIncidentViaUi(page, title);

    await page.getByRole("button", { name: "Start AI Investigation" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Start investigation" })
      .click();

    await expect(page).toHaveURL(/\/investigations\/[^/]+$/);
    await expect(page.getByTestId("workflow-status-badge").first()).toContainText(
      "awaiting approval",
    );
    await expect(page.getByText("Executive summary")).toBeVisible();
    await expect(
      page.getByText(/E2E investigation identified elevated checkout latency/i),
    ).toBeVisible();
    await expect(page.getByText(/Category: database/)).toBeVisible();
    await expect(
      page.getByText("Database connection pool exhaustion"),
    ).toBeVisible();
    await expect(page.getByText(/Sources: kb-e2e-1/)).toBeVisible();

    // Persisted knowledge-base evidence and source metadata are visible.
    await page.getByRole("button", { name: /Evidence \(/ }).click();
    await expect(page.getByText("Investigation evidence")).toBeVisible();
    await expect(page.getByText(/kb-e2e-1/).first()).toBeVisible();

    // Evidence content is collapsed by default; expand the stored chunk.
    const evidenceRecord = page
      .locator("details")
      .filter({ hasText: "kb-e2e-1" })
      .first();
    await evidenceRecord.locator("summary").click();
    await expect(
      evidenceRecord.getByText(/Checkout latency runbook/).first(),
    ).toBeVisible();

    // Refresh keeps the same persisted investigation.
    const investigationUrl = page.url();
    await page.reload();
    await expect(page).toHaveURL(investigationUrl);
    await expect(page.getByTestId("workflow-status-badge").first()).toContainText(
      "awaiting approval",
    );
  });

  test("investigation list filters by workflow status", async ({ page, request }) => {
    const incident = await createIncidentViaApi(request, {
      title: uniqueTitle("Investigation list probe"),
    });
    const started = await request.post(
      `${API_URL}/api/v1/incidents/${incident.id}/investigations`,
      { data: {} },
    );
    expect(started.status()).toBe(202);

    await page.goto("/investigations");
    await page.getByLabel("Workflow status").selectOption("awaiting_approval");
    await expect(page.getByText(incident.title)).toBeVisible();

    await page.getByLabel("Workflow status").selectOption("completed");
    await expect(page.getByText(incident.title)).toHaveCount(0);
  });

  test("unknown investigation renders a not-found error state", async ({ page }) => {
    await page.goto("/investigations/does-not-exist");
    await expect(
      page.getByRole("alert").filter({
        hasText: "The requested record was not found.",
      }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  });
});
