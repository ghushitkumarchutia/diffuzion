import { expect, test } from "@playwright/test";

test("foundation page identifies the product and available scope", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle("Diffuzion");
  await expect(page.getByRole("heading", { name: "Diffuzion", exact: true })).toBeVisible();
  await expect(page.getByText("Analysis is not available yet.", { exact: false })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
});

test("missing page provides a safe local return path", async ({ page }) => {
  await page.goto("/missing-page");
  await expect(page.getByRole("heading", { name: "Page unavailable" })).toBeVisible();
  await page.getByRole("link", { name: "Return to Diffuzion" }).click();
  await expect(page).toHaveURL("/");
});
