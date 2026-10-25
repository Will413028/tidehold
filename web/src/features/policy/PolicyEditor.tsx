"use client";

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { PolicyVersion } from "@/lib/api-types";
import { OperatorNav } from "@/lib/nav";
import { ensureWorkspace } from "@/lib/workspace";
import { RuleEditor } from "./RuleEditor";
import { Rulings } from "./Rulings";
import { blankRule, publishBody } from "./rules";
import type { Asked, Compilation, Rule } from "./types";
import { VersionList } from "./VersionList";

type Editable = { id: string; rule: Rule; clause: string | null };

const editable = (rule: Rule, clause: string | null): Editable => ({ id: crypto.randomUUID(), rule, clause });

function describeMode(c: Compilation): string {
  if (c.mode === "no_ai") return `No model answer (${c.reason}): write the rules by hand.`;
  const how = c.mode === "recorded" ? "a recorded answer" : `model ${c.model}`;
  return `Compiled by ${how}: check every rule against its clause before publishing.`;
}

export function PolicyEditor() {
  const [wording, setWording] = useState("");
  const [compilation, setCompilation] = useState<Compilation | null>(null);
  const [rules, setRules] = useState<Editable[]>([]);
  const [asked, setAsked] = useState<Asked[]>([]);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [versions, setVersions] = useState<PolicyVersion[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api<PolicyVersion[]>("/api/policy-versions")
      .then((all) => {
        setVersions(all);
        // start from the latest published wording, so it can be recompiled or edited
        setWording((typed) => typed || (all.at(-1)?.wording ?? ""));
      })
      .catch(() => setVersions([])); // no workspace yet
  }, []);

  async function run<T>(work: () => Promise<T>): Promise<T | null> {
    setMessage(null);
    try {
      await ensureWorkspace();
      return await work();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.detail : String(e));
      return null;
    }
  }

  async function compile() {
    const result = await run(() => api<Compilation>("/api/policy-compilations", { body: { wording } }));
    if (result === null) return;
    setCompilation(result);
    setRules(result.rules.map((r) => editable(r.rule, r.clause)));
    setAsked(result.needs_ruling);
    setAnswers({});
  }

  async function publish() {
    const body = publishBody(
      wording,
      compilation?.id ?? null,
      rules.map((r) => r.rule),
      answers,
    );
    const version = await run(() => api<PolicyVersion>("/api/policy-versions", { body }));
    if (version === null) return;
    setVersions((all) => [version, ...all]);
    setMessage("Published. Bind it to a trip on the Trips page.");
  }

  const update = (id: string, rule: Rule) =>
    setRules((all) => all.map((r) => (r.id === id ? { ...r, rule } : r)));

  return (
    <main>
      <OperatorNav />
      <h1>Cancellation policy</h1>
      <label>
        Your cancellation wording{" "}
        <textarea
          rows={8}
          maxLength={20000}
          value={wording}
          onChange={(e) => {
            setWording(e.target.value);
            setCompilation(null); // a compilation belongs to the exact wording it read
          }}
        />
      </label>
      <button type="button" disabled={!wording.trim()} onClick={() => void compile()}>
        Compile into rules
      </button>
      {compilation && <p>{describeMode(compilation)}</p>}
      {rules.map((r) => (
        <RuleEditor
          key={r.id}
          rule={r.rule}
          clause={r.clause}
          onChange={(rule) => update(r.id, rule)}
          onRemove={() => setRules((all) => all.filter((x) => x.id !== r.id))}
        />
      ))}
      <button
        type="button"
        disabled={!wording.trim()}
        onClick={() => setRules((all) => [...all, editable(blankRule(""), null)])}
      >
        Add a rule by hand
      </button>
      <Rulings
        asked={asked}
        answers={answers}
        onAnswer={(i, a) => setAnswers((all) => ({ ...all, [i]: a }))}
      />
      <button type="button" disabled={!wording.trim()} onClick={() => void publish()}>
        Publish this policy
      </button>
      {message && <p role="status">{message}</p>}
      <h2>Published</h2>
      <VersionList versions={versions} />
    </main>
  );
}
