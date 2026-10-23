import type { Remedy } from "./types";

export function RemedyEditor({ remedy, onChange }: { remedy: Remedy; onChange: (r: Remedy) => void }) {
  return (
    <div>
      <label>
        Remedy{" "}
        <select
          value={remedy.kind}
          onChange={(e) => {
            const kind = e.target.value as Remedy["kind"];
            onChange({ ...remedy, kind, percent: kind === "none" ? 0 : remedy.percent });
          }}
        >
          <option value="refund">refund</option>
          <option value="credit">credit</option>
          <option value="none">none</option>
        </select>
      </label>
      <label>
        Percent{" "}
        <input
          type="number"
          min={0}
          max={100}
          value={remedy.percent}
          disabled={remedy.kind === "none"}
          onChange={(e) => onChange({ ...remedy, percent: Number(e.target.value) })}
        />
      </label>
      {remedy.kind === "credit" && (
        <label>
          Credit expires after (months){" "}
          <input
            type="number"
            min={1}
            value={remedy.credit_expiry_months ?? ""}
            onChange={(e) =>
              onChange({ ...remedy, credit_expiry_months: e.target.value ? Number(e.target.value) : null })
            }
          />
        </label>
      )}
      {remedy.deductions.map((d) => (
        <p key={`${d.label}|${d.quote}`}>
          Deduction: {d.label} ({d.kind === "percent" ? `${d.value}%` : `${d.value}, fixed`}), quoted:{" "}
          <q>{d.quote}</q>
        </p>
      ))}
    </div>
  );
}
