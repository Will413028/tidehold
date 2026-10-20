import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import RootLayout from "./layout";

it("puts the sandbox notice on every page (D15)", () => {
  const html = renderToStaticMarkup(
    <RootLayout>
      <p>page</p>
    </RootLayout>,
  );

  expect(html).toContain("Sandbox demo: do not enter real personal data");
  expect(html).toContain("<p>page</p>");
});
