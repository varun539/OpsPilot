import { expect, test } from "@playwright/test";

/**
 * The `oidc-unconfigured` Playwright project serves this app without an OIDC
 * provider and without the development-auth flag. Protected screens must fail
 * closed instead of rendering data or implying that a bypass is available.
 */
test("unconfigured OIDC shows the configuration-required screen", async ({
  page,
}) => {
  await page.goto("/dashboard");

  await expect(
    page.getByRole("heading", { name: "Authentication configuration required" }),
  ).toBeVisible();
  await expect(
    page.getByText("NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY"),
  ).toBeVisible();
  await expect(
    page.getByText("NEXT_PUBLIC_OPSPILOT_DEV_AUTH=true"),
  ).toBeVisible();

  // No protected dashboard content or session controls are rendered.
  await expect(
    page.getByRole("heading", { name: "Operations overview" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Sign out" })).toHaveCount(0);
  expect(page.url()).not.toMatch(/(access_token|id_token|refresh_token)=/i);
});
