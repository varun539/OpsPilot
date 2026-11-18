import { expect, test } from "@playwright/test";

import { API_URL } from "./support/backend";
import { createIncidentViaApi, uniqueTitle } from "./support/helpers";

test.describe("tickets", () => {
  test("creates a ticket, opens it, and closes it", async ({ page, request }) => {
    const incident = await createIncidentViaApi(request, {
      title: uniqueTitle("Ticket incident"),
      severity: "high",
    });
    const ticketTitle = uniqueTitle("Ticket UI");

    await page.goto("/tickets");
    await page.getByRole("button", { name: "New ticket" }).click();

    const dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("Incident")
      .selectOption({ label: `${incident.incident_number} — ${incident.title}` });
    await dialog.getByLabel("Title").fill(ticketTitle);
    await dialog.getByLabel("Priority").selectOption("high");
    await dialog.getByRole("button", { name: "Create ticket" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const row = page.locator("tr").filter({ hasText: ticketTitle }).first();
    await expect(row).toBeVisible();
    await expect(row).toContainText(incident.incident_number);

    // The linked incident is navigable from the ticket row.
    await row.getByRole("link", { name: incident.incident_number }).click();
    await expect(page).toHaveURL(new RegExp(`/incidents/${incident.id}$`));

    await page.goto("/tickets");
    await page.getByRole("button", { name: ticketTitle }).click();
    const detail = page.getByRole("dialog");
    await expect(detail.getByText(incident.incident_number, { exact: false })).toBeVisible();

    await detail.getByRole("button", { name: "Close ticket" }).click();
    await detail.getByRole("button", { name: "Confirm close ticket" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const closedRow = page.locator("tr").filter({ hasText: ticketTitle }).first();
    await expect(closedRow.getByTestId("ticket-status-badge")).toContainText(
      "closed",
    );
  });

  test("filters tickets by status and shows an empty state", async ({
    page,
    request,
  }) => {
    const incident = await createIncidentViaApi(request, {
      title: uniqueTitle("Ticket filter incident"),
    });
    const ticketTitle = uniqueTitle("Ticket filter");
    const created = await request.post(
      `${API_URL}/api/v1/incidents/${incident.id}/tickets`,
      { data: { title: ticketTitle, priority: "low" } },
    );
    expect(created.status()).toBe(201);

    await page.goto("/tickets");
    await page.getByLabel("Status").selectOption("closed");
    await expect(page.getByText(ticketTitle)).toHaveCount(0);

    await page.getByLabel("Status").selectOption("open");
    await expect(page.getByText(ticketTitle)).toBeVisible();
  });
});
