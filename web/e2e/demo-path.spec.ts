import { expect, type Page, test } from "@playwright/test";

// What backend/tests/e2e/server.py's fake model compiles into a Gale Warning rule.
const CLAUSE = "If a Gale Warning is in force and we cancel a dive day, you get a full refund for that day.";
const WORDING = `Weather cancellations. ${CLAUSE}`;

type Recorded = {
  id: string;
  event: string;
  area_codes: string[];
  onset: string;
  expires: string;
  recorded_at: string;
};

/** A recorded NWS Gale Warning already in force when recorded, so its replay covers today. */
async function recordedGale(page: Page): Promise<Recorded> {
  const recorded: Recorded[] = await (await page.request.get("/api/recorded-warnings?source=nws")).json();
  const day = 24 * 3600 * 1000;
  const gale = recorded.find(
    (w) =>
      w.event === "Gale Warning" &&
      Date.parse(w.onset) <= Date.parse(w.recorded_at) &&
      Date.parse(w.expires) - Date.parse(w.recorded_at) > day,
  );
  if (!gale) throw new Error("the committed NWS sample has no Gale Warning in force when recorded");
  return gale;
}

/** Nothing on the page is wider than the screen: no sideways scroll on a phone. */
async function fitsTheScreen(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, `${page.url()} is wider than the screen`).toBeLessThanOrEqual(0);
}

/** A guest pays the deposit, then the balance, with the sandbox test card. */
async function guestPays(page: Page, link: string) {
  await page.goto(link);
  await page.getByRole("button", { name: "Use the sandbox test card" }).click();
  await expect(page.getByText("Deposit: USD 50.00: paid")).toBeVisible();
  // payable from the invitation: no worker tick to wait for
  await expect(page.getByRole("heading", { name: "Pay the balance" })).toBeVisible();
  await page.getByRole("button", { name: "Use the sandbox test card" }).click();
  await expect(page.getByText("Balance: USD 300.00: paid")).toBeVisible();
}

test("the demo path: compile, trip, guests pay, a recorded gale, the operator's cancellation, the guest page", async ({
  page,
}) => {
  test.setTimeout(120_000);

  // the operator compiles its wording; the model proposes the event set it confirms
  await page.goto("/policies");
  await page.getByLabel("Your cancellation wording").fill(WORDING);
  await page.getByRole("button", { name: "Compile into rules" }).click();
  await expect(page.getByRole("article", { name: "Rule" }).locator("blockquote")).toHaveText(CLAUSE);
  await page.getByRole("button", { name: "Propose the event set" }).click();
  await expect(page.getByLabel("Gale Warning", { exact: true })).toBeChecked();
  await page.getByRole("button", { name: "Publish this policy" }).click();
  await expect(page.getByRole("status")).toHaveText(/^Published/);
  await fitsTheScreen(page);

  // a two-day trip starting today in the recorded gale's area, every settlement asked
  const gale = await recordedGale(page);
  await page.goto("/");
  await page.getByLabel("Name").fill("Gale weekend");
  await page.getByLabel("Starts on").fill(new Date().toISOString().slice(0, 10));
  await page.getByLabel("Days").fill("2");
  await page.getByLabel("Time zone").fill("UTC");
  await page.getByLabel("Site latitude").fill("58.3");
  await page.getByLabel("Site longitude").fill("-135.5");
  await page.getByLabel("Warning source").selectOption("nws");
  await page.getByLabel("Warning area codes").fill(gale.area_codes[0]);
  await page.getByLabel("Deposit (USD)").fill("50");
  await page.getByLabel("Balance (USD)").fill("300");
  await page.getByRole("button", { name: "Create trip" }).click();
  await fitsTheScreen(page);
  await page.getByRole("region", { name: "Trips" }).getByRole("link", { name: "Gale weekend" }).click();
  const tripUrl = page.url();

  // two guests, each paying on its own page
  for (const name of ["Ana", "Ben"]) {
    await page.getByLabel("Invite guest").fill(name);
    await page.getByRole("button", { name: "Invite" }).click();
    await expect(page.getByRole("region", { name: "Guests" })).toContainText(name);
  }
  const links = await page.getByRole("region", { name: "Guests" }).getByRole("link").all();
  const hrefs = await Promise.all(links.map((l) => l.getAttribute("href")));
  for (const href of hrefs) await guestPays(page, href ?? "");

  // a recorded gale, replayed: the operator is asked, then cancels day 1
  await page.goto(tripUrl);
  await page.getByLabel("Recorded warning").selectOption(gale.id);
  await page.getByRole("button", { name: "Replay it as if fetched now" }).click();
  const waiting = page.getByRole("list", { name: "Waiting for your decision" });
  await expect(waiting).toContainText("Gale Warning in force on day 1");
  await page.getByRole("combobox", { name: "Trip day" }).selectOption("1");
  await page.getByRole("button", { name: "Record the cancellation" }).click();
  await waiting
    .getByRole("listitem")
    .filter({ hasText: "Cancelled day 1 (weather)" })
    .getByRole("button")
    .click();
  const decision = page.getByRole("region", { name: "Decision" });
  // until step 8b, step 8a settles only a held balance, so a paid one gets nothing yet
  await expect(decision.getByRole("group", { name: "Balance" })).toContainText("Ana: nothing");
  await decision.getByRole("button", { name: "Accept these options" }).click();
  await expect(waiting).not.toContainText("Cancelled day 1");
  await fitsTheScreen(page);

  // the guest sees the operator's cancellation, the recorded warning, the clause and its outcome
  await page.goto(hrefs[0] ?? "");
  const cancellations = page.getByRole("region", { name: "Cancellations" });
  await expect(cancellations).toContainText(
    "The operator cancelled day 1 for weather. Gale Warning (NWS) was in force.",
  );
  await expect(cancellations.getByRole("list", { name: "Your outcome" }).getByRole("listitem")).toHaveText([
    "Your deposit: USD 25.00 refunded",
    "Your balance: no change",
  ]);
  await expect(cancellations).toContainText(/recorded at .* UTC, replayed/);
  await expect(cancellations).not.toContainText("Ben");
  const deposit = cancellations.getByRole("figure").filter({ hasText: "your deposit" });
  await expect(deposit.locator("blockquote")).toHaveText(CLAUSE);
  await fitsTheScreen(page);
});
