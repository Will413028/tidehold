const minute = (iso: string) => `${iso.slice(0, 16).replace("T", " ")} UTC`;

/** Replayed evidence always says when it was recorded (D12), on every page that shows it. */
export function recordedLabel(w: { replayed: boolean; recorded_at: string }): string {
  return w.replayed
    ? `recorded at ${minute(w.recorded_at)}, replayed`
    : `fetched at ${minute(w.recorded_at)}`;
}
