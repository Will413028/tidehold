/** Turns the create-trip form into the API body; amounts are typed in USD and sent as cents. */
export function tripBody(form: FormData) {
  const usdToMinor = (name: string) => Math.round(Number(form.get(name)) * 100);
  // one select picks the version and its product heading: "<version id>\0<product or empty>"
  const [versionId, product] = String(form.get("policy") ?? "").split("\u0000");
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
    policy_version_id: versionId ?? "",
    policy_product: product ? product : null,
    timezone: String(form.get("timezone") ?? ""),
    mandate_guest_minor: usdToMinor("mandate_guest_usd"),
    mandate_trip_minor: usdToMinor("mandate_trip_usd"),
    balance_due_days: Number(form.get("balance_due_days")),
  };
}

/** The select's value for a version and one of its products (or none). */
export function policyChoice(versionId: string, product: string | null): string {
  return `${versionId}\u0000${product ?? ""}`;
}
