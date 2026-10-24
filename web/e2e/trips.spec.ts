import { expect, test } from "@playwright/test";

// the hand-written policy of backend/tests/integration/payloads.py (POLICY)
const CLAUSE =
  "if we cancel a dive day because of the weather or sea conditions, you get a full refund for that day.";
const POLICY = {
  wording: `Cancellations by us: ${CLAUSE}`,
  rules: [
    {
      product: null,
      causes: ["weather"],
      warning: null,
      scope: "per_day",
      applies_to: "both",
      remedies: [{ kind: "refund", percent: 100, deductions: [], credit_expiry_months: null }],
      chooser: null,
      quote: CLAUSE,
    },
  ],
};

test("an operator creates a trip bound to a published policy", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("note")).toContainText("do not enter real personal data");
  await expect(page.getByText("Publish a cancellation policy first")).toBeVisible();

  // policies.spec.ts covers the policy screen; this spec publishes through the API (same session)
  const origin = { Origin: new URL(page.url()).origin };
  expect((await page.request.post("/api/workspaces", { headers: origin, data: {} })).status()).toBe(201);
  expect((await page.request.post("/api/policy-versions", { headers: origin, data: POLICY })).status()).toBe(
    201,
  );
  await page.reload();

  await page.getByLabel("Name").fill("Anilao weekend");
  await page.getByLabel("Starts on").fill("2026-12-05");
  await page.getByLabel("Days", { exact: true }).fill("2");
  await page.getByLabel("Time zone").fill("Asia/Manila");
  await page.getByLabel("Site latitude").fill("13.7565");
  await page.getByLabel("Site longitude").fill("120.9137");
  await page.getByLabel("Warning area codes").fill("041000000");
  await page.getByLabel("Deposit (USD)").fill("50");
  await page.getByLabel("Balance (USD)").fill("300");
  await page.getByLabel("Per guest (USD)").fill("400");
  await page.getByLabel("Per trip (USD)").fill("4000");
  await page.getByRole("button", { name: "Create trip" }).click();

  const trips = page.getByRole("region", { name: "Trips" });
  await expect(trips.getByRole("link", { name: "Anilao weekend" })).toBeVisible();
  await expect(trips).toContainText("Dec 5, 2026 – Dec 6, 2026");
  // the amounts are wide-only columns: a phone shows the name, dates and guests
  if ((page.viewportSize()?.width ?? 0) > 720) await expect(trips).toContainText("USD 300.00");
});
