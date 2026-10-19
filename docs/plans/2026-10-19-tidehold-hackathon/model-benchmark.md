# Compile benchmark (plan step 2)

Run 2026-10-19 15:30 to 18:03 UTC on oci-a1 in a one-off container (`ghcr.io/astral-sh/uv:0.12.23-python3.13-trixie-slim`), code at 47e263a, `bench/run.py --runs 2 --split all`. Prompt hash `9bc8c8ca3e1e281d`, labels version 1 (`bench/GUIDELINE.md`, `bench/labels.json`), keys from the D13 file. Raw results stay on the VM: `~/tidehold-bench/results/bench-47e263a.json`, sha256 `e2a4ee6971f8ac992805b327087efcfbca9b2cfeb84f1b1ccd84bec6326b39e7`. Table from `python bench/report.py <results>`.

Per model, two runs: structured is successful compiles over attempts; rule and ruling precision and recall are means over the runs (n/a: nothing predicted); event sets over both runs; latency over every compile call of the model, both splits; 429 and 503 counted over both runs (waited out once each).

| Model | Split | Structured | Spans located | Rule P / R | Ruling P / R | Event sets | p50 / p95 s | 429 / 503 |
|---|---|---|---|---|---|---|---|---|
| `groq:openai/gpt-oss-120b` | dev | 9/10 | 100% | 55% / 60% | 100% / 62% | 8/8 | 3.4 / 7.8 | 8 / 0 |
| `groq:openai/gpt-oss-120b` | held_out | 7/8 | 100% | 42% / 17% | 74% / 55% | 8/8 | 3.4 / 7.8 | 8 / 0 |
| `groq:openai/gpt-oss-20b` | dev | 9/10 | 100% | 42% / 40% | 100% / 56% | 8/8 | 2.3 / 8.3 | 8 / 0 |
| `groq:openai/gpt-oss-20b` | held_out | 7/8 | 100% | 0% / 0% | 73% / 30% | 8/8 | 2.3 / 8.3 | 8 / 0 |
| `groq:qwen/qwen3.8-27b` | dev | 8/10 | 100% | 38% / 20% | 90% / 62% | 8/8 | 0.6 / 1.6 | 9 / 0 |
| `groq:qwen/qwen3.8-27b` | held_out | 6/8 | n/a | n/a / 0% | 67% / 20% | 8/8 | 0.6 / 1.6 | 9 / 0 |
| `gemini:gemini-3.8-flash` | dev | 0/10 | n/a | n/a / 0% | n/a / 0% | 0/8 | n/a | 14 / 12 |
| `gemini:gemini-3.8-flash` | held_out | 0/8 | n/a | n/a / 0% | n/a / 0% | 0/8 | n/a | 14 / 12 |
| `gemini:gemini-3.5-flash` | dev | 0/10 | n/a | n/a / 0% | n/a / 0% | 0/8 | 11.7 / 38.4 | 25 / 1 |
| `gemini:gemini-3.5-flash` | held_out | 2/8 | 100% | 50% / 8% | 100% / 5% | 0/8 | 11.7 / 38.4 | 25 / 1 |
| `gemini:gemini-3.1-pro-preview` | dev | 0/10 | n/a | n/a / 0% | n/a / 0% | 0/8 | n/a | 26 / 0 |
| `gemini:gemini-3.1-pro-preview` | held_out | 0/8 | n/a | n/a / 0% | n/a / 0% | 0/8 | n/a | 26 / 0 |
| `gemini:gemma-4-31b-it` | dev | 6/10 | 100% | 100% / 60% | 100% / 38% | 2/8 | 23.7 / 60.5 | 0 / 8 |
| `gemini:gemma-4-31b-it` | held_out | 2/8 | n/a | n/a / 0% | 50% / 10% | 2/8 | 23.7 / 60.5 | 0 / 8 |

## Failures

Counted from the results file per run; at 47e263a an HTTP error kept only its status, so the 400s have no recorded cause.

| Model | Compile failures (both runs) | Event-set failures |
|---|---|---|
| gpt-oss-120b | HTTP 400 ×1, HTTP 429 ×1 | none |
| gpt-oss-20b | HTTP 400 ×2 | none |
| qwen3.8-27b | HTTP 429 ×2; quote over 600 characters ×2 (the wire schema has no `maxLength`, pydantic does) | none |
| gemini-3.8-flash | HTTP 503 ×11, HTTP 429 ×7 | 429 ×6, 503 ×2 |
| gemini-3.5-flash | HTTP 429 ×16 | 429 ×8 |
| gemini-3.1-pro-preview | HTTP 429 ×18 | 429 ×8 |
| gemma-4-31b-it | HTTP 500 ×5, HTTP 503 ×2, invalid JSON (trailing characters) ×3 | 500 ×5, invalid JSON ×1 |

## What the Gemini rows measure

They measure the free tier's quota, not the models. One probe per model with the same key at about 18:10 UTC, after the run, returned 429 `RESOURCE_EXHAUSTED` with these quota IDs:

- `gemini-3.5-flash`, `gemini-3.8-flash`: `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, value 20. Two runs need at least 26 calls per model, 44 with every retry, so the limit is reached by the first run's middle. This also accounts for the smoke run's "503s": that model was overloaded and the day's quota was partly spent.
- `gemini-3.1-pro-preview`: per-minute and per-day request and input-token quotas, with no value; all 26 of its calls failed, each retried once, so the free tier gives this model nothing.

`gemma-4-31b-it` did not hit a quota. It failed on server errors and invalid JSON, and its p95 is 60.5 s.

Since this run, the adapter records the provider's error code and the limit it names: Groq's `code` and `(TPM)`/`(RPD)`, and Gemini's `status` and quota IDs. A spent daily quota is no longer retried, and the benchmark stops sending a model's remaining calls once its daily quota is spent.

## Fallback stack during the run

The plan measures this as "no HTTP 429 in the fallback worker log". That measure cannot fail: the worker container has logged nothing since it started (0 lines, checked after the run), and the api logged only its startup at 10:00 UTC. So the absence of 429s proves nothing.

Two direct facts take its place:

- The fallback calls Groq `openai/gpt-oss-120b` first and Gemini `gemini-3.8-flash` second (`deposits_runtime.py` at 8065eba; compose default `DEPOSITS_GEMINI_MODEL`).
- This benchmark spent `gemini-3.8-flash`'s daily free-tier quota on the shared key.

Result: the fallback was left with no working Gemini fallback until Google's daily reset. The reset happens at midnight Pacific, which is 07:00 UTC on 2026-10-20 (this time comes from Google's documentation; it was not observed). Groq, its first choice, was rate limited 8 times during the run. At 47e263a the benchmark did not record whether those were per-minute or per-day limits.

## Acceptance

- Stop condition not reached:
  - `gpt-oss-120b` and `gpt-oss-20b` produced valid structured output on 7 of 8 held-out compiles (88%), above the 80% bar.
  - All three Groq models have p95 at or under 8.3 s.
- Held-out rule recall is low: the best model, `gpt-oss-120b`, scores 17%, against 60% on dev. Step 5 tunes the prompt on dev only (GUIDELINE "Splits"), so this file records the held-out numbers but no reasons for them. Rulings carry most of the held-out value, at 74% precision and 55% recall. D16 has every rule confirmed by the operator, so a missed rule becomes operator work rather than a wrong payout.

## D6 provisional ranking

1. `groq:openai/gpt-oss-120b`: best held-out rule and ruling scores, p95 7.8 s.
2. `groq:openai/gpt-oss-20b`: same validity as 120b, but held-out rule recall is 0%.
3. `groq:qwen/qwen3.8-27b`: fastest (p95 1.6 s), but held-out rule recall is 0% and quotes run too long.

Not ranked:
- The Gemini flash models and `gemini-3.1-pro-preview`, because their free tier cannot run this benchmark. Measuring them needs a paid tier (D6 option A) or one run of 20 calls or fewer per model per day on a key the fallback does not share.
- `gemma-4-31b-it`: unreliable (8 of 18 compiles valid) and at the latency limit.
