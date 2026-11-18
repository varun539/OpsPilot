import { expect, test } from "@playwright/test";

test.describe("authentication", () => {
  test("explicit development authentication gates the dashboard without leaking tokens", async ({
    page,
  }) => {
    const consoleMessages: string[] = [];
    const requestUrls: string[] = [];
    page.on("console", (message) => consoleMessages.push(message.text()));
    page.on("request", (request) => requestUrls.push(request.url()));

    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", { name: "Operations overview" }),
    ).toBeVisible();
    await expect(page.getByText("Development auth")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await expect(page.getByText("admin", { exact: true })).toBeVisible();

    // Let the dashboard finish its API requests before inspecting traffic.
    await expect(page.getByText("Total incidents")).toBeVisible();
    await page.waitForTimeout(500);

    const tokenInUrl = /(access_token|id_token|refresh_token)=/i;
    expect(requestUrls.filter((url) => tokenInUrl.test(url))).toEqual([]);
    expect(page.url()).not.toMatch(tokenInUrl);
    expect(
      consoleMessages.filter((message) =>
        /(bearer\s+[a-z0-9._-]+|access_token|id_token)/i.test(message),
      ),
    ).toEqual([]);
  });

  test("logout clears the local session and protected data", async ({ page }) => {
    await page.goto("/incidents");
    await expect(
      page.getByRole("heading", { name: "Incidents", exact: true }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Sign out" }).click();

    await expect(
      page.getByText(/Sign in with your organization/i),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Incidents", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "OpsPilot", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Tickets", exact: true })).toHaveCount(0);
  });
});
