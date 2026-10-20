import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "tidehold",
  description: "Weather-cancellation deposits for dive operators (PayPal sandbox demo)",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <p role="note">Sandbox demo: do not enter real personal data. No real money moves.</p>
        {children}
      </body>
    </html>
  );
}
