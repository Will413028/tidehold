import type { FailedAction, TimelineEvent } from "./console-types";

export function ActivitySection({ failed, timeline }: { failed: FailedAction[]; timeline: TimelineEvent[] }) {
  return (
    <section>
      {failed.length > 0 && (
        <>
          <h2>Work that failed after its retries</h2>
          <ul aria-label="Failed work">
            {failed.map((a) => (
              <li key={`${a.kind}:${a.subject}`}>
                {a.kind}: {a.last_error ?? "failed"} · {a.failures}× · last {a.finished_at.slice(0, 16)} UTC
              </li>
            ))}
          </ul>
        </>
      )}
      <h2>Timeline</h2>
      <ol aria-label="Timeline">
        {timeline.map((e) => (
          <li key={`${e.at}:${e.guest}:${e.purpose}:${e.kind}`}>
            {e.at.slice(0, 19).replace("T", " ")} UTC · {e.guest} · {e.purpose} · {e.kind}
          </li>
        ))}
      </ol>
    </section>
  );
}
