"""Plan consistency: every requirement maps to existing steps; every decision has status and blocks."""

import re
import sys
from pathlib import Path

here = Path(__file__).resolve().parent.parent
plan = (here.parent / f"{here.name}.md").read_text()
reqs = (here / "requirements.md").read_text()

steps = {m.group(1) for m in re.finditer(r"^- \[[ x]\] \*\*(\d+)\.", plan, re.M)}
errors = []
for m in re.finditer(r"^\| (R\d+) \|.*\| ([\d, ]+) \|$", reqs, re.M):
    for step in [s.strip() for s in m.group(2).split(",")]:
        if step not in steps:
            errors.append(f"{m.group(1)} maps to missing step {step}")
mapped = {m.group(1) for m in re.finditer(r"^\| (R\d+) \|.*\| [\d, ]+ \|$", reqs, re.M)}
listed = set(re.findall(r"^\| (R\d+) \|", reqs, re.M))
errors += [f"{r} has no step" for r in sorted(listed - mapped)]
for m in re.finditer(r"^- \*\*(D[A-Z\d]+) [^*]+\*\* \(([^)]*)\)", plan, re.M):
    if "status:" not in m.group(2) or "blocks:" not in m.group(2):
        errors.append(f"{m.group(1)} lacks status or blocks")
for d in set(re.findall(r"\bD\d+\b", plan)) - set(re.findall(r"^- \*\*(D[A-Z\d]+) ", plan, re.M)):
    errors.append(f"{d} referenced but not defined")
if "--final" in sys.argv:
    errors += [f"step {n} not ticked" for n in sorted(set(re.findall(r"^- \[ \] \*\*(\d+)\.", plan, re.M)), key=int)]
    for row in re.findall(r"^\| (\d+) [^|]*\|[^|]*\|[^|]*\|[^|]*\|([^|]*)\|$", plan, re.M):
        if not row[1].strip():
            errors.append(f"progress row {row[0]} has no commit")
print("\n".join(errors) or f"ok: {len(listed)} requirements, {len(steps)} steps")
sys.exit(1 if errors else 0)
