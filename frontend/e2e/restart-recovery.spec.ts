import { expect, test } from "@playwright/test";

import { restartBackend } from "./support/backend";
import {
  approveInDialog,
  getApprovalViaApi,
  getInvestigationViaApi,
  openApprovalReview,
  seedPendingInvestigation,
} from "./support/helpers";

test("backend restart preserves the pending workflow and resumes it", async ({
  page,
  request,
}) => {
  const { incident, investigation } = await seedPendingInvestigation(
    request,
    "Restart recovery",
  );

  // Pending investigation is visible before the restart.
  await page.goto(`/investigations/${investigation.id}`);
  await expect(page.getByTestId("workflow-status-badge").first()).toContainText(
    "awaiting approval",
  );
  await expect(page.getByText("Executive summary")).toBeVisible();

  // Reconstruct the backend process against the same application and
  // checkpoint SQLite files.
  await restartBackend();

  // The browser now talks to the new process and sees the same investigation.
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`/investigations/${investigation.id}$`));
  await expect(page.getByTestId("workflow-status-badge").first()).toContainText(
    "awaiting approval",
  );
  await expect(
    page.getByText(/E2E investigation identified elevated checkout latency/i),
  ).toBeVisible();

  const recovered = await getInvestigationViaApi(request, investigation.id);
  expect(recovered.workflow_status).toBe("awaiting_approval");
  expect(recovered.thread_id).toBe(investigation.thread_id);

  // Approving now resumes the original LangGraph thread in the new process.
  await openApprovalReview(page, incident.title);
  await approveInDialog(page, "Approved after backend restart recovery.");

  const completed = await getInvestigationViaApi(request, investigation.id);
  expect(completed.workflow_status).toBe("completed");
  expect(completed.thread_id).toBe(investigation.thread_id);

  const approval = await getApprovalViaApi(request, investigation.id);
  expect(approval.status).toBe("approved");
  expect(approval.decision_reason).toBe(
    "Approved after backend restart recovery.",
  );

  // A second restart proves the final workflow result is durable.
  await restartBackend();
  await page.goto(`/investigations/${investigation.id}`);
  await expect(page.getByTestId("workflow-status-badge").first()).toContainText(
    "completed",
  );
  await page.reload();
  await expect(page.getByTestId("workflow-status-badge").first()).toContainText(
    "completed",
  );

  const afterSecondRestart = await getInvestigationViaApi(
    request,
    investigation.id,
  );
  expect(afterSecondRestart.workflow_status).toBe("completed");
});
