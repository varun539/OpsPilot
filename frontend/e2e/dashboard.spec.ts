import { expect, test } from "@playwright/test";

import { API_URL } from "./support/backend";
import { createIncidentViaApi, uniqueTitle } from "./support/helpers";

test.describe("dashboard", () => {
  test("loads API-derived metrics and recent incidents", async ({ page, request }) => {
    const incident = await createIncidentViaApi(request, {
      title: uniqueTitle("Dashboard seed"),
      severity: "critical",
    });

    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", { name: "Operations overview" }),
    ).toBeVisible();
    const metrics = page.getByRole("region", { name: "Key metrics" });
    await expect(metrics.getByText("Total incidents")).toBeVisible();
    await expect(metrics.getByText("Investigating")).toBeVisible();
    await expect(metrics.getByText("Awaiting approval")).toBeVisible();
    await expect(page.getByText(incident.title, { exact: false }).first()).toBeVisible();

    const statsResponse = await request.get(`${API_URL}/api/v1/dashboard/stats`);
    expect(statsResponse.status()).toBe(200);
    const stats = (await statsResponse.json()) as { total_incidents: number };

    const totalCard = metrics
      .getByText("Total incidents", { exact: true })
      .locator("..")
      .locator("..");
    await expect(totalCard.locator("p").last()).toHaveText(
      String(stats.total_incidents),
    );

    // Opening a recent incident navigates to its real detail page.
    await page.getByText(incident.title, { exact: false }).first().click();
    await expect(page).toHaveURL(new RegExp(`/incidents/${incident.id}$`));
    await expect(
      page.getByRole("heading", { name: new RegExp(incident.title) }),
    ).toBeVisible();
  });

  test("shows a retryable error state when the stats request fails", async ({
    page,
  }) => {
    await page.route("**/api/v1/dashboard/stats", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "internal", message: "Dashboard statistics unavailable" },
        }),
      }),
    );

    await page.goto("/dashboard");

    const errorState = page
      .getByRole("alert")
      .filter({ hasText: "Dashboard statistics unavailable" });
    await expect(errorState).toBeVisible();
    await expect(errorState.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  test("sidebar navigation works", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("link", { name: "Incidents" }).click();
    await expect(page).toHaveURL(/\/incidents$/);
    await expect(
      page.getByRole("heading", { name: "Incidents", exact: true }),
    ).toBeVisible();

    await page.getByRole("link", { name: "Approvals" }).click();
    await expect(page).toHaveURL(/\/approvals$/);
    await expect(
      page.getByRole("heading", { name: "Approvals", exact: true }),
    ).toBeVisible();
  });
});
