import type { PolicyVersion } from "@/lib/api-types";
import { quotedFromText } from "@/lib/quoted-from";

export function VersionList({ versions }: { versions: PolicyVersion[] }) {
  if (versions.length === 0) return <p>No published policy yet.</p>;
  return (
    <ul aria-label="Published policies">
      {versions.map((v) => (
        <li key={v.id}>
          Published {v.created_at.slice(0, 16).replace("T", " ")} UTC · {v.rules.length}{" "}
          {v.rules.length === 1 ? "rule" : "rules"}
          {v.products.length > 0 && ` · ${v.products.join(", ")}`}
          {v.warning_source && ` · warnings from ${v.warning_source}`}
          {v.quoted_from && (
            <p>
              <a href={v.quoted_from.url}>{quotedFromText(v.quoted_from)}</a>
            </p>
          )}
          <ul>
            {v.rules.map((r) => (
              <li key={r.clause}>
                <q>{r.clause}</q>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}
