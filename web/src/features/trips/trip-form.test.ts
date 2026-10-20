import { expect, it } from "vitest";
import { tripBody } from "./trip-form";

it("sends amounts and mandate caps as cents, area codes as a list, and the bound policy", () => {
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
    policy: "v-1\u0000Day trips",
    timezone: "Asia/Manila",
    mandate_guest_usd: "100",
    mandate_trip_usd: "0",
  }))
    form.set(k, v);

  expect(tripBody(form)).toEqual({
    name: "Anilao weekend",
    starts_on: "2026-12-05",
    days: 3,
    site_lat: 13.7565,
    site_lon: 120.9137,
    warning_source: "pagasa",
    warning_areas: ["041000000", "045600000"],
    deposit_minor: 5000,
    balance_minor: 30005,
    policy_version_id: "v-1",
    policy_product: "Day trips",
    timezone: "Asia/Manila",
    mandate_guest_minor: 10000,
    mandate_trip_minor: 0,
  });
});

it("binds a version without a product heading as null", () => {
  const form = new FormData();
  form.set("policy", "v-2\u0000");
  expect(tripBody(form)).toMatchObject({ policy_version_id: "v-2", policy_product: null });
});
