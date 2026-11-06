---
description: Model benchmark runs
paths:
  - "bench/**"
  - ".github/workflows/eval.yml"
---

# Bench

- Before running `bench/run.py`, compute the total call count against the Gemini free tier (20 calls per model per day); never retry a daily-quota 429.
