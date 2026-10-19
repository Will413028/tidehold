import { expect, it } from "vitest";
import { tripBody } from "./trip-form";

it("sends amounts as cents and area codes as a list", () => {
  const form = new FormData();
  for (const [k, v] of Object.entries({
    name: "Anilao weekend",
    starts_on: "2026-12-05",
    days: "3",
    site_lat: "13.7565",
    site_lon: "120.9137",
    warning_source: "pagasa",
    warning_areas: "041000000, 045600000",
    deposit_usd: "50",
    balance_usd: "300.05",
  }))
    form.set(k, v);

  expect(tripBody(form)).toMatchObject({
    days: 3,
    warning_areas: ["041000000", "045600000"],
    deposit_minor: 5000,
    balance_minor: 30005,
  });
});
