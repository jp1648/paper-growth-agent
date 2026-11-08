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
  await expect(
    page.getByText(/Cost-adjusted P.L is unavailable in this snapshot/),
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

test("accepted investigation is readable and trace filters work", async ({
  page,
}) => {
  test.skip(!password, "Requires isolated fixture dashboard");
  await page.goto("/login");
  await page.getByLabel("Workspace password").fill(password!);
  await page.getByRole("button", { name: "Enter observatory" }).click();
  await expect(page.getByRole("button", { name: /Refresh/ })).toBeVisible();
  const original = await (await page.request.get("/api/data")).json();
  original.snapshot.portfolio.economic_result = {
    broker_pnl_usd: "-2",
    recorded_operating_cost_usd_estimate: "4.25",
    pnl_after_recorded_costs_usd_estimate: "-6.25",
    unknown_cost_requests: 2,
    unresolved_research_runs: 1,
    research_run_ledger: {
      provider_reconciled_usd: "1.5",
      estimated_usd: "3.25",
      unresolved_recorded_usd: "5",
      settled_runs: 2,
      unresolved_runs: 1,
      unbounded_unknown_runs: 0,
    },
    complete: false,
    scope: "Current experiment journal only.",
    limitations: [
      "Broker fees are already in broker P/L.",
      "Infrastructure costs are not fully recorded.",
    ],
  };
  original.snapshot.x_credits = {
    status: "available",
    checked_at: "2026-11-06T04:00:00Z",
    total_balance: 44.71,
    prepaid_balance: 25,
    free_balance: 19.71,
    free_grants: [{ amount: 19.71, expires_at: "2027-02-03T04:19:12Z" }],
  };
  original.snapshot.runs = [
    {
      id: "accepted-fixture",
      status: "accepted",
      cost: "1.25",
      model_calls: 3,
      tool_calls: 2,
      decision: {
        symbol: "AAPL",
        action: "WATCH",
        why_now: "Wait for verified valuation inputs.",
        business_attraction: "Recurring revenue supports the business.",
        price_attraction: "Fair value is not established.",
        expected_outcome: "Review updated filings.",
        evidence_ids: ["source-1"],
      },
      reports: {
        valuation: { normalization_review: {} as Record<string, unknown> },
        business: { conclusion: "Complete findings preserved." },
        adversarial: {
          unresolved_material_gaps: true,
          conclusion: "Missing valuation evidence.",
        },
      },
      calculations: {},
      sources: [{ id: "source-1", title: "Company filing" }],
    },
  ];
  const events = [
    { name: "model.call", phase: "end" },
    { name: "tool.search", phase: "end" },
    { name: "research.failed", phase: "error" },
  ].map((e, i) => ({
    ...e,
    at: "2026-11-06T04:00:00Z",
    span_id: String(i),
    parent_span_id: null,
    detail: '{"ok":true}',
    truncated: false,
  }));
  await page.route("**/api/data*", (route) =>
    route.fulfill({
      json: new URL(route.request().url()).searchParams.has("run")
        ? { events }
        : original,
    }),
  );
  await page.getByRole("button", { name: /Refresh/ }).click();
  const economic = page.getByRole("region", { name: "Economic result" });
  const runCosts = page.getByRole("region", { name: "Research run ledger" });
  await expect(
    runCosts.getByText(/Provider-reconciled: \$1.5000/),
  ).toBeVisible();
  await expect(runCosts.getByText(/Estimated: \$3.2500/)).toBeVisible();
  await expect(
    runCosts.getByText(/do not add the two views together/),
  ).toBeVisible();
  await expect(economic.getByText("-$6.2500", { exact: true })).toBeVisible();
  await expect(
    economic.getByText(/2 unpriced requests · 1 unresolved research runs/),
  ).toBeVisible();
  await expect(
    economic.getByText(/not an all-in investment return/),
  ).toBeVisible();
  await expect(
    economic.getByText(/Infrastructure costs are not fully recorded/),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "X API credits" }),
  ).toBeVisible();
  await expect(
    page.locator(".x-credits").getByText("$44.71", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".x-credits").getByText(/free credits expire/),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../state/always-on/x-credits-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("button", { name: "Research", exact: true }).click();
  await page.locator(".run-choice").first().click();
  await expect(
    page.getByRole("heading", { name: "WATCH — no trade proposed" }),
  ).toBeVisible();
  await expect(page.getByText("Material evidence gaps remain")).toBeVisible();
  const accounting = page.getByRole("region", {
    name: "Accounting normalization review",
  });
  await expect(
    accounting.getByText("No accounting normalization review saved."),
  ).toBeVisible();
  original.snapshot.runs[0].reports.valuation.normalization_review = {
    cash_flow_basis:
      "Historical cash-flow proxy; discount basis requires review.",
    debt_and_cash: "Debt treatment is unresolved.",
    dilution_and_sbc: "Dilution needs filing verification.",
    nonrecurring_items: "No conclusion recorded.",
    unresolved_material_gaps: true,
    evidence_ids: ["source-1"],
  };
  await page.getByRole("button", { name: /Refresh/ }).click();
  await expect(
    accounting.getByText("Agent reports unresolved accounting gaps."),
  ).toBeVisible();
  await accounting.locator("summary").click();
  await expect(
    accounting.getByText("Debt treatment is unresolved."),
  ).toBeVisible();
  await expect(
    accounting.getByText(/not independent verification/),
  ).toBeVisible();
  await expect(accounting.getByText("source-1", { exact: true })).toBeVisible();
  original.snapshot.runs[0].reports.valuation.normalization_review.unresolved_material_gaps = false;
  await page.getByRole("button", { name: /Refresh/ }).click();
  await expect(
    accounting.getByText("Agent reports no material accounting gaps."),
  ).toBeVisible();
  await expect(
    accounting.getByText(/not independent verification/),
  ).toBeVisible();

  const report = page.locator("details").filter({
    has: page.locator("summary", {
      hasText: "Business quality & financial health",
    }),
  });
  await expect(report).not.toHaveAttribute("open", "");
  await report.locator("summary").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Complete findings preserved.")).toBeVisible();
  await report.locator("summary").click();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `../state/always-on/investigation-${width}.png`,
      fullPage: true,
    });
  }
  await page.getByRole("button", { name: "Inspect execution trace" }).click();
  await expect(page.locator(".trace-row")).toHaveCount(3);
  for (const filter of ["Failures", "Models", "Tools"]) {
    await page.getByRole("button", { name: filter, exact: true }).click();
    await expect(page.locator(".trace-row")).toHaveCount(1);
    await expect(
      page.getByRole("button", { name: filter, exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
  }
});

test("long trace loads every event and full details require authentication", async ({
  page,
  request,
}) => {
  test.skip(!password, "Requires isolated fixture dashboard");
  await page.goto("/login");
  await page.getByLabel("Workspace password").fill(password!);
  await page.getByRole("button", { name: "Enter observatory" }).click();
  await page.getByRole("button", { name: "Traces", exact: true }).click();
  await page.locator(".run-choice").filter({ hasText: "long-fixture" }).click();
  await expect(page.locator(".trace-row")).toHaveCount(100);
  await page
    .getByRole("button", { name: "Load older events", exact: true })
    .click();
  await expect(page.locator(".trace-row")).toHaveCount(200);
  await page
    .getByRole("button", { name: "Load older events", exact: true })
    .click();
  await expect(page.locator(".trace-row")).toHaveCount(205);
  await expect(
    page.getByRole("button", { name: "Load older events", exact: true }),
  ).toHaveCount(0);
  await page.locator(".trace-row").first().locator("summary").click();
  const href = await page
    .getByRole("link", { name: "Open complete event (new tab)" })
    .first()
    .getAttribute("href");
  expect(href).toBeTruthy();
  expect((await request.get(href!)).status()).toBe(401);
  const full = await page.request.get(href!);
  expect(full.headers()["cache-control"]).toContain("no-store");
  expect((await full.json()).event.payload.end_marker).toBe("FULL_EVENT_END");
  expect(
    (
      await page.request.get("/api/data?run=long-fixture&cursor=invalid")
    ).status(),
  ).toBe(400);
});
