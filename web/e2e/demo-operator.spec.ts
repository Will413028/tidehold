import { expect, test } from "@playwright/test";

// backend/src/tidehold/bootstrap/demo.py: Pure Dive Resort's clause, quoted with its source
const CLAUSE =
  "We do not cancel our dive trips trips because of the weather (rain, clouds etc.) unless there has been an official storm warning issued by the local authorities. In this case your trip will be either rescheduled or refunded.";
const ATTRIBUTION =
  "Quoted from Pure Dive Resort's published policy (https://purediveresort.com/deep-diving/, accessed 2026-10-19).";

type Recorded = { id: string; event: string };

test("the judge path: load the demo operator, replay the recorded typhoon, cancel, refund, the guest page", async ({
  page,
}) => {
  test.setTimeout(120_000);

  // a fresh browser: the demo operator loads into its empty workspace, guests paid from the page
  await page.goto("/");
  await page.getByRole("button", { name: "Load the demo operator" }).click();
  await page.waitForURL(/\/trips\//);
  const tripUrl = page.url();
  const guests = page.getByRole("region", { name: "Guests" });
  await expect(guests).toContainText("Guest A");
  await expect(guests).toContainText("USD 350.00");
  const links = await guests.getByRole("link").all();
  const hrefs = await Promise.all(links.map((l) => l.getAttribute("href")));

  // its published policy, attributed; the wording box starts with it, ready to compile live
  await page.goto("/policies");
  await expect(page.getByRole("list", { name: "Published policies" })).toContainText(ATTRIBUTION);
  await expect(page.getByLabel("Your cancellation wording")).toHaveValue(/We do not cancel our dive trips/);

  // the recorded Super Typhoon Rosal warning for Batanes, replayed: the operator is asked
  const recorded: Recorded[] = await (await page.request.get("/api/recorded-warnings?source=pagasa")).json();
  const typhoon = recorded.find((w) => w.event === "Tropical Cyclone Warning");
  if (!typhoon) throw new Error("the committed PAGASA sample has no Tropical Cyclone Warning");
  await page.goto(tripUrl);
  await page.getByLabel("Recorded warning").selectOption(typhoon.id);
  await page.getByRole("button", { name: "Replay it as if fetched now" }).click();
  const waiting = page.getByRole("list", { name: "Waiting for your decision" });
  // day 1 is the Manila date the seed ran on; a run that crosses Manila midnight finds it on day 2
  await expect(waiting).toContainText(/Tropical Cyclone Warning in force on day [12]/);

  // the operator cancels the rest of the trip and accepts the refund (the first option)
  await page.getByRole("combobox", { name: "What" }).selectOption("rest");
  await page.getByRole("combobox", { name: "Trip day" }).selectOption("1");
  await page.getByRole("button", { name: "Record the cancellation" }).click();
  await waiting
    .getByRole("listitem")
    .filter({ hasText: "Cancelled the whole trip (weather)" })
    .getByRole("button")
    .click();
  const decision = page.getByRole("region", { name: "Decision" });
  await expect(decision.getByRole("group", { name: "Balance" })).toContainText("refund 100%");
  await decision.getByRole("button", { name: "Accept these options" }).click();
  await expect(waiting).not.toContainText("Cancelled the whole trip");

  // the guest sees the clause, where it is quoted from, and both payments refunded
  await page.goto(hrefs[0] ?? "");
  const cancellations = page.getByRole("region", { name: "Cancellations" });
  await expect(async () => {
    await page.reload();
    await expect(cancellations.getByRole("list", { name: "Your outcome" }).getByRole("listitem")).toHaveText(
      ["Your deposit: USD 50.00 refunded", "Your balance: USD 300.00 refunded"],
      { timeout: 1000 },
    );
  }).toPass({ timeout: 30000 });
  const deposit = cancellations.getByRole("figure").filter({ hasText: "your deposit" });
  await expect(deposit.locator("blockquote")).toHaveText(CLAUSE);
  await expect(deposit).toContainText(ATTRIBUTION);
  await expect(cancellations).toContainText(/recorded at .* UTC, replayed/);
});
