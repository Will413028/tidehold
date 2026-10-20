import { expect, it } from "vitest";
import { recordedLabel } from "./evidence";

it("labels a replayed warning with the time it was recorded (D12)", () => {
  expect(recordedLabel({ replayed: true, recorded_at: "2026-10-19T11:00:21Z" })).toBe(
    "recorded at 2026-10-19 11:00 UTC, replayed",
  );
  expect(recordedLabel({ replayed: false, recorded_at: "2026-10-19T11:00:21Z" })).toBe(
    "fetched at 2026-10-19 11:00 UTC",
  );
});
