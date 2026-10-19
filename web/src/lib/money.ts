/** Amounts arrive as USD minor units (cents); code, never the model, computes them. */
export function formatUsd(minor: number): string {
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(minor);
  return `${sign}USD ${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}
