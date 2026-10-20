import { expect, it } from "vitest";
import { blankRule, publishBody, toggled, withWarning } from "./rules";

it("publishes the wording, the compilation, the rules and one answer per asked item", () => {
  const rule = blankRule("a full refund");
  expect(publishBody("Wording", "c-1", [rule], { 1: " no fee ", 0: "yes" })).toEqual({
    wording: "Wording",
    compilation_id: "c-1",
    rules: [rule],
    rulings: [
      { index: 0, answer: "yes" },
      { index: 1, answer: "no fee" },
    ],
  });
});

it("a hand-written policy has no compilation", () => {
  expect(publishBody("W", null, [], {})).toMatchObject({ compilation_id: null, rulings: [] });
});

it("toggles a value in a list without duplicates", () => {
  expect(toggled(["weather"], "safety")).toEqual(["weather", "safety"]);
  expect(toggled(["weather", "safety"], "weather")).toEqual(["safety"]);
});

it("adds or removes a warning condition, keeping the rest of the rule", () => {
  const rule = { ...blankRule("q"), scope: "per_trip" as const };
  const warned = withWarning(rule, true, "nws");
  expect(warned.warning).toEqual({ named_as: "", source: "nws", event_values: [], quote: "" });
  expect(warned.scope).toBe("per_trip");
  expect(withWarning(warned, false, "nws").warning).toBeNull();
});
