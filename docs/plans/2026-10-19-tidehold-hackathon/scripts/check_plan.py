"""Plan consistency: every requirement maps to existing steps; every decision has status and
blocks, and an open one lists its options; every step not ticked names what blocks it, its
scope, consumers, what must not change and an acceptance that names a mutation (or says why
there is none)."""

import re
import sys
from pathlib import Path

here = Path(__file__).resolve().parent.parent
plan = (here.parent / f"{here.name}.md").read_text()
reqs = (here / "requirements.md").read_text()

STEP = re.compile(r"^( *)- \[([ x])\] \*\*(\d+[a-z]?)\.", re.M)
STEP_IDS = r"[\da-z, ]+"

headers = list(STEP.finditer(plan))
steps = {m.group(3) for m in headers}
errors = []
for m in re.finditer(rf"^\| (R\d+) \|.*\| ({STEP_IDS}) \|$", reqs, re.M):
    for step in [s.strip() for s in m.group(2).split(",")]:
        if step not in steps:
            errors.append(f"{m.group(1)} maps to missing step {step}")
mapped = {m.group(1) for m in re.finditer(rf"^\| (R\d+) \|.*\| {STEP_IDS} \|$", reqs, re.M)}
listed = set(re.findall(r"^\| (R\d+) \|", reqs, re.M))
errors += [f"{r} has no step" for r in sorted(listed - mapped)]

decisions = list(re.finditer(r"^- \*\*(D[A-Z\d]+) [^*]+\*\* \(([^)]*)\)", plan, re.M))
for i, m in enumerate(decisions):
    if "status:" not in m.group(2) or "blocks:" not in m.group(2):
        errors.append(f"{m.group(1)} lacks status or blocks")
    if "status: open" in m.group(2):
        end = decisions[i + 1].start() if i + 1 < len(decisions) else len(plan)
        if len(re.findall(r"^  - [A-Z]: ", plan[m.end() : end], re.M)) < 2:
            errors.append(f"{m.group(1)} is open but lists fewer than two options")
for d in set(re.findall(r"\bD\d+\b", plan)) - {m.group(1) for m in decisions}:
    errors.append(f"{d} referenced but not defined")

steps_end = plan.index("\n## Decisions")
for i, m in enumerate(headers):
    if m.group(2) == "x":
        continue
    end = min(headers[i + 1].start() if i + 1 < len(headers) else steps_end, steps_end)
    block, step = plan[m.start() : end], m.group(3)
    parent = any(h.group(3) != step and re.fullmatch(rf"{step}[a-z]", h.group(3)) for h in headers)
    if "blocked by:" not in block.split("\n")[0]:
        errors.append(f"step {step} does not say what blocks it")
    for field in ("Scope", "Consumers:", "Must not change:", "Acceptance"):
        if f"- {field}" not in block:
            errors.append(f"step {step} has no {field.rstrip(':')}")
    acceptance = " ".join(re.findall(r"- Acceptance[^:]*:(.*)", block))
    if not parent and "mutation" not in acceptance.lower():
        errors.append(f"step {step} has no mutation in its acceptance")

if "--final" in sys.argv:
    errors += [f"step {m.group(3)} not ticked" for m in headers if m.group(2) == " "]
    for row in re.findall(r"^\| (\d+[a-z]?) [^|]*\|[^|]*\|[^|]*\|[^|]*\|([^|]*)\|$", plan, re.M):
        if not row[1].strip():
            errors.append(f"progress row {row[0]} has no commit")
print("\n".join(errors) or f"ok: {len(listed)} requirements, {len(steps)} steps")
sys.exit(1 if errors else 0)
