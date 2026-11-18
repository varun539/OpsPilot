import { expect, test } from "@playwright/test";

import { API_URL } from "./support/backend";
import {
  createIncidentViaApi,
  createIncidentViaUi,
  seedPendingInvestigation,
  uniqueTitle,
} from "./support/helpers";

test.describe("incidents", () => {
  test("creates an incident through the UI and opens its detail history", async ({
    page,
  }) => {
    const title = uniqueTitle("UI created incident");
    await createIncidentViaUi(page, title);

    await expect(page.getByRole("tab", { name: /Investigations/ })).toBeVisible();
    await page.getByRole("tab", { name: /Investigations/ }).click();
    await expect(page.getByText("No investigations")).toBeVisible();

    await page.getByRole("tab", { name: /Tickets/ }).click();
    await expect(page.getByText("No tickets")).toBeVisible();

    await page.getByRole("tab", { name: "Activity" }).click();
    await expect(page.getByText("Activity timeline")).toBeVisible();
    await expect(page.getByText("Created", { exact: false }).first()).toBeVisible();
  });

  test("filters by severity and text search", async ({ page, request }) => {
    const critical = await createIncidentViaApi(request, {
      title: uniqueTitle("Critical filter probe"),
      severity: "critical",
    });
    const low = await createIncidentViaApi(request, {
      title: uniqueTitle("Low filter probe"),
      severity: "low",
    });

    await page.goto("/incidents");
    await expect(page.getByText(critical.title).first()).toBeVisible();

    await page.getByLabel("Severity").selectOption("critical");
    await expect(page.getByText(critical.title).first()).toBeVisible();
    await expect(page.getByText(low.title)).toHaveCount(0);

    await page.getByLabel("Severity").selectOption("");
    await page.getByLabel("Search loaded incidents").fill(critical.title);
    await expect(page.getByText(critical.title).first()).toBeVisible();
    await expect(page.getByText(low.title)).toHaveCount(0);
  });

  test("paginates incident results using API offsets", async ({ page, request }) => {
    for (let index = 0; index < 12; index += 1) {
      await createIncidentViaApi(request, {
        title: uniqueTitle(`Pagination probe ${index}`),
        severity: "info",
      });
    }

    await page.goto("/incidents");
    await page.getByLabel("Severity").selectOption("info");

    await expect(page.getByText("Page 1", { exact: true })).toBeVisible();
    await expect(page.locator("table tbody tr")).toHaveCount(10);

    await page.getByRole("button", { name: "Next", exact: true }).click();

    await expect(page.getByText("Page 2", { exact: true })).toBeVisible();
    await expect(page.locator("table tbody tr")).toHaveCount(2);
    await expect(
      page.getByRole("button", { name: "Next", exact: true }),
    ).toBeDisabled();
  });

  test("incident detail shows linked tickets, investigations, and activity", async ({
    page,
    request,
  }) => {
    const { incident, investigation } = await seedPendingInvestigation(
      request,
      "History probe incident",
    );
    const ticketTitle = uniqueTitle("History probe ticket");
    const ticketResponse = await request.post(
      `${API_URL}/api/v1/incidents/${incident.id}/tickets`,
      { data: { title: ticketTitle, priority: "high" } },
    );
    expect(ticketResponse.status()).toBe(201);

    await page.goto(`/incidents/${incident.id}`);
    await expect(
      page.getByRole("heading", { name: new RegExp(incident.title) }),
    ).toBeVisible();

    await page.getByRole("tab", { name: /Investigations/ }).click();
    await expect(page.getByText(investigation.investigation_number)).toBeVisible();
    await expect(page.getByTestId("workflow-status-badge").first()).toContainText(
      "awaiting approval",
    );

    await page.getByRole("tab", { name: /Tickets/ }).click();
    await expect(page.getByText(ticketTitle)).toBeVisible();

    await page.getByRole("tab", { name: "Approvals" }).click();
    await expect(
      page.getByTestId("approval-status-badge").first(),
    ).toContainText("pending");

    await page.getByRole("tab", { name: "Activity" }).click();
    await expect(page.getByText("Investigation Started")).toBeVisible();
    await expect(page.getByText("Human Review Requested")).toBeVisible();
  });
});
