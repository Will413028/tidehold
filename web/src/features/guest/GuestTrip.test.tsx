import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { GuestTrip } from "./GuestTrip";

it("shows the trip name and dates", () => {
  const html = renderToStaticMarkup(
    <GuestTrip trip={{ name: "Anilao weekend", starts_on: "2026-12-05", ends_on: "2026-12-07", days: 3 }} />,
  );

  expect(html).toContain("Anilao weekend");
  expect(html).toContain("Dec 5, 2026 – Dec 7, 2026 · 3 days");
});
