import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { RemedyEditor } from "./RemedyEditor";

it("shows each deduction with its own quote", () => {
  const html = renderToStaticMarkup(
    <RemedyEditor
      remedy={{
        kind: "refund",
        percent: 100,
        deductions: [
          {
            kind: "percent",
            value: 3.5,
            label: "processing fee",
            quote: "minus 3.5% processing fee for online payments",
          },
        ],
        credit_expiry_months: null,
      }}
      onChange={() => undefined}
    />,
  );
  expect(html).toContain("Deduction: processing fee (3.5%)");
  expect(html).toContain("<q>minus 3.5% processing fee for online payments</q>");
});
