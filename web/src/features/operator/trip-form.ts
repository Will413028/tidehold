/** Turns the create-trip form into the API body; amounts are typed in USD and sent as cents. */
export function tripBody(form: FormData) {
  const usdToMinor = (name: string) => Math.round(Number(form.get(name)) * 100);
  return {
    name: String(form.get("name") ?? ""),
    starts_on: String(form.get("starts_on") ?? ""),
    days: Number(form.get("days")),
    site_lat: Number(form.get("site_lat")),
    site_lon: Number(form.get("site_lon")),
    warning_source: String(form.get("warning_source") ?? ""),
    warning_areas: String(form.get("warning_areas") ?? "")
      .split(",")
      .map((code) => code.trim())
      .filter(Boolean),
    deposit_minor: usdToMinor("deposit_usd"),
    balance_minor: usdToMinor("balance_usd"),
  };
}
