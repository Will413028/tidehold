import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

const CLAUSE = "If a Gale Warning is in force and we cancel a dive day, you get a full refund for that day.";

type Recorded = {
  id: string;
  event: string;
  area_codes: string[];
  onset: string;
  expires: string;
  recorded_at: string;
};

async function post(request: APIRequestContext, page: Page, path: string, data: unknown) {
  const response = await request.post(path, { headers: { Origin: new URL(page.url()).origin }, data });
  expect(response.ok(), `${path}: ${await response.text()}`).toBe(true);
  return response.json();
}

/** A two-day trip starting today (UTC) in the area of a recorded NWS Gale Warning, bound to a
 * rule that conditions on that warning, with a mandate of 0 so every settlement asks. */
async function tripUnderAGale(page: Page): Promise<{ id: string; gale: Recorded }> {
  const { request } = page;
  await page.goto("/");
  await post(request, page, "/api/workspaces", {});
  const recorded: Recorded[] = await (await request.get("/api/recorded-warnings?source=nws")).json();
  // one already in force when recorded, so its replay (shifted to now, D12) covers today
  const day = 24 * 3600 * 1000;
  const gale = recorded.find(
    (w) =>
      w.event === "Gale Warning" &&
      Date.parse(w.onset) <= Date.parse(w.recorded_at) &&
      Date.parse(w.expires) - Date.parse(w.recorded_at) > day,
  );
  if (!gale) throw new Error("the committed NWS sample has no Gale Warning in force when recorded");
  const version = await post(request, page, "/api/policy-versions", {
    wording: `Weather. ${CLAUSE}`,
    rules: [
      {
        product: null,
        causes: ["weather"],
        warning: {
          named_as: "Gale Warning",
          source: "nws",
          event_values: ["Gale Warning"],
          quote: "a Gale Warning is in force",
        },
        scope: "per_day",
        applies_to: "both",
        remedies: [{ kind: "refund", percent: 100, deductions: [], credit_expiry_months: null }],
        chooser: null,
        quote: CLAUSE,
      },
    ],
  });
  const trip = await post(request, page, "/api/trips", {
    name: "Monterey gale",
    starts_on: new Date().toISOString().slice(0, 10),
    days: 2,
    site_lat: 36.6,
    site_lon: -121.9,
    warning_source: "nws",
    warning_areas: [gale.area_codes[0]],
    deposit_minor: 5000,
    balance_minor: 30000,
    policy_version_id: version.id,
    policy_product: null,
    timezone: "UTC",
    mandate_guest_minor: 0,
    mandate_trip_minor: 0,
  });
  return { id: trip.id, gale };
}

test("the operator replays a recorded gale, cancels the day and decides the settlement", async ({ page }) => {
  const { id, gale } = await tripUnderAGale(page);
  await page.goto(`/trips/${id}`);

  await page.getByLabel("Invite guest").fill("Ana");
  await page.getByRole("button", { name: "Invite" }).click();
  const guests = page.getByRole("region", { name: "Guests" });
  await expect(guests).toContainText("Ana");
  const link = await guests.getByRole("link", { name: "open" }).getAttribute("href");
  await post(page.request, page, `/api${link?.replace("/g/", "/guest/")}/deposit/test-card`, {});

  await page.getByLabel("Recorded warning").selectOption(gale.id);
  await page.getByRole("button", { name: "Replay it as if fetched now" }).click();
  await expect(page.getByRole("list", { name: "Warnings in force" })).toContainText(
    /recorded at .* UTC, replayed/,
  );
  const asks = page.getByRole("region", { name: "Asks and settlements" });
  await expect(asks).toContainText("Gale Warning in force on day 1");

  await page.getByRole("combobox", { name: "Trip day" }).selectOption("1");
  await page.getByRole("button", { name: "Record the cancellation" }).click();
  const waiting = page.getByRole("list", { name: "Waiting for your decision" });
  await waiting
    .getByRole("listitem")
    .filter({ hasText: "Cancelled day 1 (weather)" })
    .getByRole("button")
    .click();

  const decision = page.getByRole("region", { name: "Decision" });
  await expect(decision).toContainText("MANDATE_GUEST_CAP");
  await expect(decision.getByRole("group", { name: "Deposit" })).toContainText("Ana: refund USD 25.00");
  await decision.getByRole("button", { name: "Accept these options" }).click();

  const settlement = asks.getByRole("row").filter({ hasText: "Cancelled day 1 (weather)" });
  await expect(settlement).toContainText("accepted");
  await expect(waiting).not.toContainText("Cancelled day 1");
  await expect(asks.getByRole("row").filter({ hasText: "Gale Warning in force on day 1" })).toContainText(
    "answered",
  );

  // the guest's own page (R12): the cancellation, the warning with its recorded label, the clause
  await page.goto(link ?? "");
  const cancellations = page.getByRole("region", { name: "Cancellations" });
  await expect(cancellations).toContainText("The operator cancelled day 1 for weather.");
  await expect(cancellations.getByRole("list", { name: "Your outcome" })).toContainText(
    "Your deposit: USD 25.00 refunded",
  );
  await expect(cancellations.getByRole("list", { name: "Official warnings in force" })).toContainText(
    /Gale Warning \(NWS\).*recorded at .* UTC, replayed/,
  );
  const deposit = cancellations.getByRole("figure").filter({ hasText: "your deposit" });
  await expect(deposit.locator("blockquote")).toHaveText(CLAUSE);
});
