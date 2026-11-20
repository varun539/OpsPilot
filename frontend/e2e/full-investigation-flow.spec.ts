import { expect, test } from "@playwright/test";

import { API_URL } from "./support/backend";
import {
  approveInDialog,
  createIncidentViaUi,
  getApprovalViaApi,
  getInvestigationViaApi,
  openApprovalReview,
  uniqueTitle,
} from "./support/helpers";

/**
 * Full-stack journey: browser -> Next.js -> real FastAPI -> real LangGraph
 * graph -> deterministic diagnostic tools and fake external providers ->
 * real human-review interrupt -> approval resume -> persisted result.
 *
 * Nothing in the application API is mocked. Only the LLM/retrieval providers
 * are deterministic test doubles, injected by tests/e2e_backend.py.
 */
test("complete incident to investigation to approval journey", async ({
  page,
  request,
}) => {
  const title = uniqueTitle("Full flow checkout latency");

  // 1. Create an incident through the UI (real POST /api/v1/incidents).
  await createIncidentViaUi(page, title);
  const incidentUrl = page.url();
  const incidentId = incidentUrl.split("/").pop() as string;

  // 2. Start the investigation through the UI (real orchestration endpoint).
  await page.getByRole("button", { name: "Start AI Investigation" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Start investigation" })
    .click();

  // 3. The real graph reaches its human-review interrupt and persists state.
  await expect(page).toHaveURL(/\/investigations\/[^/]+$/);
  const investigationUrl = page.url();
  const investigationId = investigationUrl.split("/").pop() as string;
  await expect(page.getByTestId("workflow-status-badge").first()).toContainText(
    "awaiting approval",
  );
  await expect(
    page.getByRole("heading", { name: "Executive summary" }),
  ).toBeVisible();
  await expect(page.getByText("Database connection pool exhaustion")).toBeVisible();

  const pending = await getInvestigationViaApi(request, investigationId);
  expect(pending.workflow_status).toBe("awaiting_approval");
  expect(pending.thread_id).toBeTruthy();
  expect(pending.summary).toMatch(/E2E investigation/);
  const originalThreadId = pending.thread_id;

  const evidenceResponse = await request.get(
    `${API_URL}/api/v1/investigations/${investigationId}/evidence`,
  );
  expect(evidenceResponse.status()).toBe(200);
  const evidence = (await evidenceResponse.json()) as Array<{
    evidence_type: string;
    source: string | null;
  }>;
  expect(evidence.length).toBeGreaterThan(0);
  expect(evidence.some((item) => item.source === "kb-e2e-1")).toBe(true);

  const pendingApproval = await getApprovalViaApi(request, investigationId);
  expect(pendingApproval.status).toBe("pending");

  // 4. Review the pending approval in the browser and approve it.
  await openApprovalReview(page, title);
  await expect(
    page.getByRole("dialog").getByRole("heading", { name: "Executive summary" }),
  ).toBeVisible();
  await approveInDialog(
    page,
    "Full-flow approval: evidence verified and remediation accepted.",
  );

  // 5. The same LangGraph thread resumed and the final result persisted.
  const completed = await getInvestigationViaApi(request, investigationId);
  expect(completed.workflow_status).toBe("completed");
  expect(completed.thread_id).toBe(originalThreadId);

  const approval = await getApprovalViaApi(request, investigationId);
  expect(approval.status).toBe("approved");
  expect(approval.decided_by).toBe("dev-user");
  expect(approval.decision_reason).toBe(
    "Full-flow approval: evidence verified and remediation accepted.",
  );

  // Approval never resolves the incident automatically.
  const incidentResponse = await request.get(
    `${API_URL}/api/v1/incidents/${incidentId}`,
  );
  expect(incidentResponse.status()).toBe(200);
  const incident = (await incidentResponse.json()) as { status: string };
  expect(incident.status).not.toBe("resolved");

  // 6. A duplicate decision is rejected safely by the API.
  const duplicate = await request.post(
    `${API_URL}/api/v1/investigations/${investigationId}/approval`,
    { data: { decision: "approved", reason: "duplicate" } },
  );
  expect(duplicate.status()).toBe(409);

  // 7. A browser refresh shows the persisted final state, not stale UI.
  await page.goto(investigationUrl);
  await expect(page.getByTestId("workflow-status-badge").first()).toContainText(
    "completed",
  );
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Executive summary" }),
  ).toBeVisible();
  await expect(page.getByTestId("workflow-status-badge").first()).toContainText(
    "completed",
  );

  // 8. The approval queue now shows the recorded decision.
  await page.goto("/approvals");
  await page.getByLabel("Status").selectOption("approved");
  const card = page.locator("li").filter({ hasText: title }).first();
  await expect(card).toContainText("approved");
  await expect(card).toContainText(
    "Full-flow approval: evidence verified and remediation accepted.",
  );
});
