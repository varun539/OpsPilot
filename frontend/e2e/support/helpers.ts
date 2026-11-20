import { expect, type APIRequestContext, type Page } from "@playwright/test";

import { API_URL } from "./backend";

export interface ApiIncident {
  id: string;
  incident_number: string;
  title: string;
  severity: string;
  status: string;
  priority: string;
  created_at: string;
}

export interface ApiInvestigation {
  id: string;
  investigation_number: string;
  incident_id: string;
  status: string;
  workflow_status: string | null;
  summary: string | null;
  thread_id: string | null;
}

export interface ApiApproval {
  id: string;
  investigation_id: string | null;
  status: string;
  decided_by: string | null;
  decision_reason: string | null;
}

let sequence = 0;

export function uniqueTitle(prefix: string): string {
  sequence += 1;
  return `${prefix} ${Date.now()}-${sequence}`;
}

export interface IncidentOverrides {
  title?: string;
  severity?: string;
  priority?: string;
  status?: string;
  description?: string;
  service_name?: string;
  environment?: string;
}

export async function createIncidentViaApi(
  request: APIRequestContext,
  overrides: IncidentOverrides = {},
): Promise<ApiIncident> {
  const response = await request.post(`${API_URL}/api/v1/incidents`, {
    data: {
      title: overrides.title ?? uniqueTitle("E2E incident"),
      severity: overrides.severity ?? "high",
      priority: overrides.priority ?? "medium",
      ...(overrides.status ? { status: overrides.status } : {}),
      ...(overrides.description ? { description: overrides.description } : {}),
      ...(overrides.service_name ? { service_name: overrides.service_name } : {}),
      ...(overrides.environment ? { environment: overrides.environment } : {}),
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as ApiIncident;
}

export async function startInvestigationViaApi(
  request: APIRequestContext,
  incidentId: string,
): Promise<ApiInvestigation> {
  const response = await request.post(
    `${API_URL}/api/v1/incidents/${incidentId}/investigations`,
    { data: {} },
  );
  expect(response.status(), await response.text()).toBe(202);
  const started = (await response.json()) as { investigation_id: string };
  const investigation = await request.get(
    `${API_URL}/api/v1/investigations/${started.investigation_id}`,
  );
  expect(investigation.status(), await investigation.text()).toBe(200);
  return (await investigation.json()) as ApiInvestigation;
}

export async function seedPendingInvestigation(
  request: APIRequestContext,
  prefix = "E2E approval",
): Promise<{ incident: ApiIncident; investigation: ApiInvestigation }> {
  const incident = await createIncidentViaApi(request, {
    title: uniqueTitle(prefix),
    severity: "high",
  });
  const investigation = await startInvestigationViaApi(request, incident.id);
  expect(investigation.workflow_status).toBe("awaiting_approval");
  return { incident, investigation };
}

export async function getInvestigationViaApi(
  request: APIRequestContext,
  investigationId: string,
): Promise<ApiInvestigation> {
  const response = await request.get(
    `${API_URL}/api/v1/investigations/${investigationId}`,
  );
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as ApiInvestigation;
}

export async function getApprovalViaApi(
  request: APIRequestContext,
  investigationId: string,
): Promise<ApiApproval> {
  const response = await request.get(
    `${API_URL}/api/v1/investigations/${investigationId}/approval`,
  );
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as ApiApproval;
}

export async function createIncidentViaUi(page: Page, title: string): Promise<void> {
  await page.goto("/incidents");
  await page.getByRole("button", { name: "New incident" }).click();
  await page.getByLabel("Title").fill(title);
  await page.getByRole("dialog").getByRole("button", { name: "Create incident" }).click();
  await expect(page).toHaveURL(/\/incidents\/[^/]+$/);
  await expect(page.getByRole("heading", { name: new RegExp(escapeRegExp(title)) })).toBeVisible();
}

export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Open the approvals page, target one card, and open its review dialog. */
export async function openApprovalReview(page: Page, marker: string): Promise<void> {
  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: "Approvals", exact: true })).toBeVisible();
  const card = page.locator("li").filter({ hasText: marker }).first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Review decision" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
}

export async function approveInDialog(page: Page, reason: string): Promise<void> {
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("heading", { name: "Executive summary" }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: /Approve/ }).click();
  await dialog.getByLabel("Decision reason").fill(reason);
  await dialog.getByRole("button", { name: "Confirm approval" }).click();
  await expect(dialog.getByText("Decision submitted")).toBeVisible();
  await expect(dialog.getByText(/Workflow status:.*completed/i)).toBeVisible();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
}
