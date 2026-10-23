# Compile benchmark (plan steps 2 and 5)

Newest run first; earlier runs follow under "Earlier run" headings. `backend/tests/unit/test_model_choice.py` fails when the product's prompt hash or a model it may run is not the newest run's.

## Run at prompt 00bdb5c3b65fa352 (steps 5b and 5c, 2026-10-23)

Run 2026-10-23 16:14 to 16:55 UTC on oci-a1 in a one-off container (`ghcr.io/astral-sh/uv:0.12.23-python3.13-trixie-slim`), `bench/run.py --runs 2 --split all --models groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b,groq:qwen/qwen3.8-27b,anthropic:claude-haiku-5-5,anthropic:claude-sonnet-5-5`, run by the developer's agent at the developer's request. Prompt hash `00bdb5c3b65fa352`, labels version 3. Code: `git archive 53b9af7 backend bench`, shipped as `~/tidehold-bench/src/53b9af7`; the commit that adds this section changes no file the run reads but `providers.py` (`BENCHMARKED`) and `settings.py` (the default model). Keys: Groq from the D13 file, Claude from the developer's key in a separate env file on the VM (D22), both through `--env-file`. Raw results stay on the VM: `~/tidehold-bench/results/bench-53b9af7.json`, sha256 `ae62ece346a84d9255b9f732c23e0ddde73385d7509bf7df19e6e412f2a01991`. Table from `python bench/report.py <results>`. Gemini was not run: D6 forbids it on the shared key.

| Model | Split | Structured | Spans located | Rule P / R | Ruling P / R | Event sets | p50 / p95 s | 429 / 503 |
|---|---|---|---|---|---|---|---|---|
| `groq:openai/gpt-oss-120b` | dev | 10/10 | 90% | 100% / 80% | 83% / 56% | 8/8 | 3.4 / 8.3 | 9 / 0 |
| `groq:openai/gpt-oss-120b` | held_out | 8/8 | 90% | 48% / 33% | 67% / 35% | 8/8 | 3.4 / 8.3 | 9 / 0 |
| `groq:openai/gpt-oss-20b` | dev | 10/10 | 100% | 88% / 70% | 93% / 44% | 8/8 | 2.0 / 4.9 | 8 / 0 |
| `groq:openai/gpt-oss-20b` | held_out | 6/8 | 100% | 0% / 0% | 75% / 15% | 8/8 | 2.0 / 4.9 | 8 / 0 |
| `groq:qwen/qwen3.8-27b` | dev | 9/10 | 100% | 100% / 60% | 93% / 67% | 7/8 | 0.8 / 0.9 | 8 / 0 |
| `groq:qwen/qwen3.8-27b` | held_out | 6/8 | n/a | n/a / 0% | 50% / 20% | 7/8 | 0.8 / 0.9 | 8 / 0 |
| `anthropic:claude-haiku-5-5` | dev | 10/10 | 100% | 65% / 60% | 71% / 94% | 6/8 | 10.8 / 28.1 | 0 / 0 |
| `anthropic:claude-haiku-5-5` | held_out | 7/8 | 94% | 86% / 50% | 72% / 50% | 6/8 | 10.8 / 28.1 | 0 / 0 |
| `anthropic:claude-sonnet-5-5` | dev | 10/10 | 100% | 73% / 80% | 86% / 100% | 8/8 | 4.5 / 14.7 | 0 / 0 |
| `anthropic:claude-sonnet-5-5` | held_out | 7/8 | 100% | 65% / 42% | 62% / 65% | 8/8 | 4.5 / 14.7 | 0 / 0 |

Failures (both runs): gpt-oss-20b HTTP 400 `json_validate_failed` ×2 (Aquatic Apes); qwen3.8-27b HTTP 429 ×1 after the one wait (Mike's Dauin), quote over 600 characters ×2 (Aquatic Apes), one event set 429; claude-haiku-5-5 and claude-sonnet-5-5 a quote over 600 characters ×1 each (Aquatic Apes, refused by the schema's pydantic bound, which the Claude schema carries only as a description). No daily quota or spend cap was spent. The cost per call was not recorded.

### Reading

- Two runs of 5 dev and 4 held-out fixtures; one model varies a lot between its runs (`gpt-oss-120b` held-out rule recall 50% in run 1, 17% in run 2), so differences of one fixture are noise.
- On held-out wording, `claude-sonnet-5-5` finds the most rulings (recall 65% against 35% for `gpt-oss-120b`) and more rules (42% against 33%), with every event set exact. Rulings are where the operator is asked before money moves (D3, D16), so a missed ruling is a missed question.
- `claude-haiku-5-5` has the highest held-out rule precision (86%) but maps 6 of 8 event sets and is the slowest (p95 28.1 s).
- Claude hit no rate limit; the Groq models were waited out 25 times, on the key the fallback demo shares.

### Default model (D22)

The developer chose `anthropic:claude-sonnet-5-5` (2026-10-23). Both Claude models join `BENCHMARKED`; the Groq models stay until step 11 keeps or removes them.

Fallback during the run: `https://dive-deposits.tail357fef.ts.net/deposits` answered 200 at 17:00 UTC.

## Earlier run (step 5, prompt 9f7c6e9404b56080)

Run 2026-10-20 00:21 to 00:51 UTC on oci-a1 in a one-off container (`ghcr.io/astral-sh/uv:0.12.23-python3.13-trixie-slim`), `bench/run.py --runs 2 --split all --models groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b,groq:qwen/qwen3.8-27b`. Prompt hash `9f7c6e9404b56080`, labels version 1, keys from the D13 file. Code: the working tree shipped as `~/tidehold-bench/src/tune-9f7c6e94`; its 67 `.py`, `.json` and `.sql` files under `backend/src` and `bench` have the same sha256 as the commit that adds this section. Raw results stay on the VM: `~/tidehold-bench/results/bench-9f7c6e94.json`, sha256 `56e270fec62c968537a7aded047c9863e2ec18743820a7907442b00740ff5ea7`. Table from `python bench/report.py <results>`. Gemini was not run: D6 forbids it on the shared key.

| Model | Split | Structured | Spans located | Rule P / R | Ruling P / R | Event sets | p50 / p95 s | 429 / 503 |
|---|---|---|---|---|---|---|---|---|
| `groq:openai/gpt-oss-120b` | dev | 9/10 | 100% | 88% / 60% | 79% / 69% | 8/8 | 3.0 / 6.7 | 6 / 0 |
| `groq:openai/gpt-oss-120b` | held_out | 8/8 | 100% | 42% / 17% | 71% / 50% | 8/8 | 3.0 / 6.7 | 6 / 0 |
| `groq:openai/gpt-oss-20b` | dev | 10/10 | 100% | 88% / 70% | 88% / 38% | 8/8 | 2.1 / 5.3 | 9 / 0 |
| `groq:openai/gpt-oss-20b` | held_out | 6/8 | 100% | 0% / 0% | 100% / 30% | 8/8 | 2.1 / 5.3 | 9 / 0 |
| `groq:qwen/qwen3.8-27b` | dev | 10/10 | 100% | 83% / 50% | 87% / 81% | 7/8 | 0.7 / 1.1 | 8 / 0 |
| `groq:qwen/qwen3.8-27b` | held_out | 4/8 | n/a | n/a / 0% | 50% / 15% | 7/8 | 0.7 / 1.1 | 8 / 0 |

Failures (both runs): gpt-oss-120b HTTP 400 `json_validate_failed` ×1 (Scuba Tribe); gpt-oss-20b HTTP 400 `json_validate_failed` ×2 (Aquatic Apes); qwen3.8-27b HTTP 429 ×2 after the one wait, quote over 600 characters ×2 (Aquatic Apes), one event set 429. No daily quota was spent.

### Tuning (dev split only)

The prompt changed from `9bc8c8ca3e1e281d` after reading only the dev results of the step 2 run (`gpt-oss-120b`): `any` was listed next to `weather`; a schedule change and a disclaimer of responsibility became rules; a course's dives were scoped per trip; a force-majeure list with no outcome was not asked about. Each was stated in the prompt (and, where the guideline was silent, in `bench/GUIDELINE.md`, labels unchanged).

| Round | Prompt hash | `gpt-oss-120b` dev, 2 runs: rule P / R | Kept |
|---|---|---|---|
| 1 | `9f7c6e9404b56080` | 100% / 100%, 100% / 60% (one HTTP 400) | yes |
| 2 | `a5c5b8d2e2a948a4` (`no_weather_clause` only for the whole wording) | 80% / 80% both runs; Blue Ribbon's "any other safety concern" read as a catch-all, Wetzone still not `conflicting_clauses` | no, reverted |

Held-out results were not read until this run had finished.

### Reading

- Tuning raised `gpt-oss-120b`'s dev rule precision from 55% to 88%. Dev recall stayed at 60%, and held-out did not move beyond run-to-run noise (rules 42% / 17% against 42% / 17%; rulings 71% / 50% against 74% / 55%). Five dev fixtures do not carry over to unseen wording.
- The same prompt at temperature 0 varies a lot between runs: tuning round 1 scored dev recall 100% in one run, and this run scored 60%. A single run of 5 fixtures cannot separate two prompts.
- What this means for the product (D16): a rule the compiler misses becomes operator work, not a wrong payout, because the operator confirms every rule and publishing re-checks each one. The held-out numbers mean the operator will write or fix most rules on unseen wording.
- Step 5 acceptance, "3 live compiles on the VM, p95 at most 60 s": `gpt-oss-120b` completed 17 live compiles through the product's compile path (`tidehold.policy.compile_policy`, not the HTTP route), with the slowest call at 6.7 s.

### D6 provisional ranking (unchanged)

1. `groq:openai/gpt-oss-120b`: the only model with held-out rules found, 8/8 structured, p95 6.7 s.
2. `groq:openai/gpt-oss-20b`: held-out rule recall 0%, and 2 of 8 held-out compiles refused by Groq (`json_validate_failed`).
3. `groq:qwen/qwen3.8-27b`: fastest, but 4 of 8 held-out compiles valid and quotes run too long.

Fallback during the run: `https://dive-deposits.tail357fef.ts.net/deposits` answered 200 at 00:53 UTC. Groq per-minute limits were hit 23 times over the three models and waited out; the fallback shares that key.

## Earlier run (step 2, prompt 9bc8c8ca3e1e281d)

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

### Failures

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

### What the Gemini rows measure

They measure the free tier's quota, not the models. One probe per model with the same key at about 18:10 UTC, after the run, returned 429 `RESOURCE_EXHAUSTED` with these quota IDs:

- `gemini-3.5-flash`, `gemini-3.8-flash`: `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, value 20. Two runs need at least 26 calls per model, 44 with every retry, so the limit is reached by the first run's middle. This also accounts for the smoke run's "503s": that model was overloaded and the day's quota was partly spent.
- `gemini-3.1-pro-preview`: per-minute and per-day request and input-token quotas, with no value; all 26 of its calls failed, each retried once, so the free tier gives this model nothing.

`gemma-4-31b-it` did not hit a quota. It failed on server errors and invalid JSON, and its p95 is 60.5 s.

Since this run, the adapter records the provider's error code and the limit it names: Groq's `code` and `(TPM)`/`(RPD)`, and Gemini's `status` and quota IDs. A spent daily quota is no longer retried, and the benchmark stops sending a model's remaining calls once its daily quota is spent.

### Fallback stack during the run

The plan measures this as "no HTTP 429 in the fallback worker log". That measure cannot fail: the worker container has logged nothing since it started (0 lines, checked after the run), and the api logged only its startup at 10:00 UTC. So the absence of 429s proves nothing.

Two direct facts take its place:

- The fallback calls Groq `openai/gpt-oss-120b` first and Gemini `gemini-3.8-flash` second (`deposits_runtime.py` at 8065eba; compose default `DEPOSITS_GEMINI_MODEL`).
- This benchmark spent `gemini-3.8-flash`'s daily free-tier quota on the shared key.

Result: the fallback was left with no working Gemini fallback until Google's daily reset. The reset happens at midnight Pacific, which is 07:00 UTC on 2026-10-20 (this time comes from Google's documentation; it was not observed). Groq, its first choice, was rate limited 8 times during the run. At 47e263a the benchmark did not record whether those were per-minute or per-day limits.

### Acceptance

- Stop condition not reached:
  - `gpt-oss-120b` and `gpt-oss-20b` produced valid structured output on 7 of 8 held-out compiles (88%), above the 80% bar.
  - All three Groq models have p95 at or under 8.3 s.
- Held-out rule recall is low: the best model, `gpt-oss-120b`, scores 17%, against 60% on dev. Step 5 tunes the prompt on dev only (GUIDELINE "Splits"), so this file records the held-out numbers but no reasons for them. Rulings carry most of the held-out value, at 74% precision and 55% recall. D16 has every rule confirmed by the operator, so a missed rule becomes operator work rather than a wrong payout.

### D6 provisional ranking

1. `groq:openai/gpt-oss-120b`: best held-out rule and ruling scores, p95 7.8 s.
2. `groq:openai/gpt-oss-20b`: same validity as 120b, but held-out rule recall is 0%.
3. `groq:qwen/qwen3.8-27b`: fastest (p95 1.6 s), but held-out rule recall is 0% and quotes run too long.

Not ranked:
- The Gemini flash models and `gemini-3.1-pro-preview`, because their free tier cannot run this benchmark. Measuring them needs a paid tier (D6 option A) or one run of 20 calls or fewer per model per day on a key the fallback does not share.
- `gemma-4-31b-it`: unreliable (8 of 18 compiles valid) and at the latency limit.
