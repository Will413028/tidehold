const format = new Intl.DateTimeFormat("en", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/** Trip dates arrive as ISO calendar dates (YYYY-MM-DD) and are shown without a time zone shift. */
export function formatDateRange(startsOn: string, endsOn: string): string {
  const start = format.format(new Date(`${startsOn}T00:00:00Z`));
  if (startsOn === endsOn) return start;
  return `${start} – ${format.format(new Date(`${endsOn}T00:00:00Z`))}`;
}
