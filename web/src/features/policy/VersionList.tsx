import type { PolicyVersion } from "@/lib/api-types";

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
