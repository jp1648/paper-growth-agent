import { test, expect } from "@playwright/test";
const password = process.env.TEST_DASHBOARD_PASSWORD;
test("private dashboard, research filtering, traces and responsive layout", async ({
  page,
  request,
}) => {
  test.skip(
    !password,
    "Set TEST_DASHBOARD_PASSWORD for an isolated fixture dashboard",
  );
  expect((await request.get("/api/data")).status()).toBe(401);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Workspace password").fill(password!);
  await page.getByRole("button", { name: "Enter observatory" }).click();
  await expect(
    page.getByRole("heading", { name: "Performance, with perspective." }),
  ).toBeVisible();
  await expect(
    page.getByText("Automatic trading paused", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Spend by source" }),
  ).toBeVisible();
  await expect(
    page.getByText(/Missing billing data does not mean zero cost/),
  ).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.screenshot({
    path: "../state/dashboard-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: /^Research/ }).click();
  await page.getByLabel("Filter investigations").fill("no-such-run");
  await expect(page.getByText("No matching investigations.")).toBeVisible();
  await page.getByLabel("Filter investigations").fill("");
  await page.locator(".run-choice").first().click();
  await expect(
    page.getByRole("heading", { name: "No accepted decision" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Inspect execution trace" }).click();
  await expect(
    page.getByRole("heading", { name: "Follow the work, step by step." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../state/dashboard-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  expect((await page.request.get("/api/data")).status()).toBe(401);
});
