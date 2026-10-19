import { expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { describe as explain } from "./PayPalCheckout";

it("tells a missing PayPal setup apart from an unconfirmed payment", () => {
  expect(explain(new ApiError(503, "PAYPAL_NOT_CONFIGURED"))).toContain("not set up");
  expect(explain(new ApiError(503, "PAYPAL_OUTCOME_UNKNOWN"))).toContain("did not confirm");
  expect(explain(new ApiError(409, "BALANCE_NOT_OPEN_YET"))).toContain("BALANCE_NOT_OPEN_YET");
});
