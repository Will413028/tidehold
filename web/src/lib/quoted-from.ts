import type { QuotedFrom } from "./api-types";

/** The attribution a real operator's quoted clause carries (product contract: source URL and
 * access date). */
export function quotedFromText(q: QuotedFrom): string {
  return `Quoted from ${q.name}'s published policy (${q.url}, accessed ${q.accessed}).`;
}
