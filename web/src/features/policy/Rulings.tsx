import type { Asked } from "./types";

/** What the compilation could not decide: the operator answers each one in its own words. */
export function Rulings({
  asked,
  answers,
  onAnswer,
}: {
  asked: Asked[];
  answers: Record<number, string>;
  onAnswer: (index: number, answer: string) => void;
}) {
  if (asked.length === 0) return null;
  return (
    <section aria-label="Needs your ruling">
      <h2>Needs your ruling</h2>
      {asked.map((item) => (
        <div key={item.index}>
          {item.clause && <blockquote>{item.clause}</blockquote>}
          <label>
            {item.question}{" "}
            <textarea
              required
              value={answers[item.index] ?? ""}
              onChange={(e) => onAnswer(item.index, e.target.value)}
            />
          </label>
        </div>
      ))}
    </section>
  );
}
