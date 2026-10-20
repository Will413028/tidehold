import { expect, test } from "@playwright/test";

// The e2e backend's fake model compiles this wording (backend/tests/integration/payloads.py).
const CLAUSE =
  "if we cancel a dive day because of the weather or sea conditions, you get a full refund for that day.";
const WORDING = `Cancellations by us: ${CLAUSE}`;

test("an operator compiles its wording, checks the rule against its clause and publishes", async ({
  page,
}) => {
  await page.goto("/policies");
  await page.getByLabel("Your cancellation wording").fill(WORDING);
  await page.getByRole("button", { name: "Compile into rules" }).click();

  const rule = page.getByRole("article", { name: "Rule" });
  await expect(rule.locator("blockquote")).toHaveText(CLAUSE);
  await expect(page.getByText("Compiled by model fake-model")).toBeVisible();
  await page.getByRole("button", { name: "Publish this policy" }).click();

  await expect(page.getByRole("status")).toHaveText(/^Published/);
  await expect(page.getByRole("list", { name: "Published policies" })).toContainText(CLAUSE);
});

test("an operator writes a warning rule by hand and confirms its event set", async ({ page }) => {
  const clause =
    "If a Gale Warning is in force and we cancel a dive day, you get a full refund for that day.";
  await page.goto("/policies");
  await page.getByLabel("Your cancellation wording").fill(`Weather. ${clause}`);
  await page.getByRole("button", { name: "Add a rule by hand" }).click();

  await page.getByLabel("Quote (verbatim from the wording)").fill(clause);
  await page.getByLabel("Requires an official warning").check();
  await page.getByLabel("Named in the wording as").fill("Gale Warning");
  await page.getByLabel("Source").selectOption("nws");
  await page.getByLabel("Warning quote (verbatim)").fill("a Gale Warning is in force");
  await page.getByRole("button", { name: "Propose the event set" }).click();
  // the model proposes Gale Warning; the operator adds Storm Warning before publishing
  await expect(page.getByLabel("Gale Warning", { exact: true })).toBeChecked();
  await page.getByLabel("Storm Warning", { exact: true }).check();
  await expect(page.getByText("Event set: 2 values confirmed")).toBeVisible();
  await page.getByRole("button", { name: "Publish this policy" }).click();

  await expect(page.getByRole("status")).toHaveText(/^Published/);
  await expect(page.getByRole("list", { name: "Published policies" })).toContainText("warnings from nws");
});

test("a quote that is not in the wording is refused", async ({ page }) => {
  await page.goto("/policies");
  await page.getByLabel("Your cancellation wording").fill("We refund a cancelled day in full.");
  await page.getByRole("button", { name: "Add a rule by hand" }).click();
  await page.getByLabel("Quote (verbatim from the wording)").fill("We always refund everything.");
  await page.getByRole("button", { name: "Publish this policy" }).click();

  await expect(page.getByRole("status")).toHaveText("SPAN_NOT_FOUND");
});
