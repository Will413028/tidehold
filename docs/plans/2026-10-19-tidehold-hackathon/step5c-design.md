# Step 5c design: the Claude provider

Plan step 5c and D22 (`../2026-10-19-tidehold-hackathon.md`). Written 2026-10-23 on `feat/step5b-deduction-quotes` after step 5b's fix round, before building; reviewed at start and before commit (findings at the end). Facts about the API below were read on 2026-10-23 from the installed SDK (`anthropic` 1.15.0) and Anthropic's documentation pages *Rate limits* (`platform.claude.com/docs/en/api/rate-limits`) and *Structured outputs* (`platform.claude.com/docs/en/build-with-claude/structured-outputs`).

## Dependency

- `anthropic>=1.15,<2` in `[project] dependencies`, resolved under `exclude-newer = "1 week"`: 1.15.0 (uploaded 2026-10-15; the package has released since 2023-02, 233 releases). New in `uv.lock`: `anthropic`, `docstring-parser` 0.18.0, `jiter` 0.17.0, `sniffio` 1.3.1. The SDK runs on `httpx2`, the HTTP library the backend already uses, so no conflict. `uv.lock` is already scanned by `make security` and `security.yml`.

## Provider (`policy/providers.py`)

- `Claude(client, key, model, workspace=None)` (named `Claude`, not `Anthropic`, so it does not shadow the SDK's class; start review finding 13): the same constructor shape as `Groq` and `Gemini` (an `httpx2.AsyncClient`, the key, the exact model ID), so `VENDORS` and the token-cap test treat it alike; `key` and `workspace` are `repr=False`. Each call builds an `AsyncAnthropic(api_key=key, base_url="https://api.anthropic.com", http_client=client, max_retries=0, timeout=TIMEOUT_S, default_headers={"anthropic-workspace-id": workspace} when set, open_telemetry=False)` and calls `messages.create`. The explicit key and base URL keep the SDK from reading `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` or a profile; `ANTHROPIC_CUSTOM_HEADERS` and `ANTHROPIC_LOG` are still read by the SDK and are named in the threat model as variables the VM must leave unset. Retries and fallback stay with the caller (bench `Pacer`, product no-AI path), as with the other providers.
- Request: `model` exact; `system`; one user message; `max_tokens=CLAUDE_MAX_TOKENS` (16000: adaptive thinking spends output tokens). Passing `timeout` skips the SDK's own check of non-streaming length; the call keeps `TIMEOUT_S` (90 s) like the others, and the benchmark shows whether Claude finishes inside it (developer, 2026-10-23, start review finding 5); `output_config={"format": {"type": "json_schema", "schema": claude_schema(schema)}, "effort": "medium"}`. No `temperature`, `top_p`, `top_k` (a 400 since 2026-10-21) and no `thinking` field (adaptive by default; `disabled` is a 400 on these models). No `fallbacks` (D15: only measured models run).
- `claude_schema` is the SDK's `anthropic.transform_schema`: it keeps only the keywords Claude's structured outputs accept (an allowlist) and moves bounds such as `minimum` and `maximum` into the field's description, which a denylist would not do for a later keyword (start review finding 6). The prompt hash covers the unconverted schemas, so it does not change. Validation does not loosen: `compile._parse` and `drafts` validate the answer with the pydantic models, which keep every bound (percent 0–100, quote 1–600 characters, expiry ≥ 1).
- Answer: `stop_reason == "end_turn"` with the text blocks joined (thinking blocks skipped) is a `Completion`; `max_tokens` raises `ProviderError("stop_reason max_tokens")`; `refusal` raises `ProviderError("refusal <category>")` (the category from `stop_details`, `none` when absent); any other stop reason raises `ProviderError("stop_reason <x>")`. Latency is measured around the call, as for the others.
- Errors, from the SDK's typed exceptions (never their message text):
  - `RateLimitError` (429): when `error.details.error_code` in the body is `enforced_spend_limit_reached` (the monthly spend cap: no `retry-after`, nothing succeeds before the month ends) it is `daily=True`, reason `HTTP 429 rate_limit_error enforced_spend_limit_reached`, so the benchmark's `Pacer` stops spending and the product falls back. Otherwise the limits whose `anthropic-ratelimit-{requests,input-tokens,output-tokens}-remaining` header is `0` are named (`HTTP 429 rate_limit_error requests`), `daily=False`, transient. Anthropic has no daily rate limit (per minute only), so "daily" here means "spent until the provider's reset", which is what `ProviderError.daily` already means.
  - `APIStatusError`: `HTTP <status> <error type>` (`invalid_request_error`, `overloaded_error`, ...). A user-set spend limit is a 400 `invalid_request_error`; not retried, like any 400.
  - `APITimeoutError`, `APIConnectionError`, any other SDK error (`AnthropicError`: response validation, credentials) and a body that is not JSON (the decoder's `ValueError`): the class name, no status, so nothing escapes the no-AI path (start review finding 1).
  - A 402 `billing_error` (credit or payment trouble) counts as spent, `daily=True` (developer, 2026-10-23, start review finding 10).
  - `ProviderError.transient` also counts 529 (`overloaded_error`, Anthropic's overloaded status) beside 503.
- `VENDORS["anthropic"]` is `Claude`; `build` reads `keys.anthropic` and `keys.anthropic_workspace`; `BENCHMARKED` unchanged, so `build("anthropic:claude-haiku-5-5", ...)` raises before any key is read.

## Settings (`platform/settings.py`)

`ModelKeys` gains `anthropic` (`ANTHROPIC_API_KEY`) and `anthropic_workspace` (`ANTHROPIC_WORKSPACE_ID`), both optional and `repr=False`. The SDK would also read `ANTHROPIC_API_KEY` itself; the provider always passes the key explicitly, so only `settings.py` reads the environment (ruff `TID251`).

## Bench (`bench/run.py`)

`_provider` builds `Claude` for an `anthropic:` spec with the key and workspace, without `build` (the benchmark measures models the product does not run yet). `SPACING["anthropic"] = 1.0` (the Start tier allows 1,000 requests a minute per model; the developer's key is not shared with the fallback demo). A 429 is waited out once as for the others; the spend cap stops the model's remaining calls.

## Drafts

`settlement/drafts.py` and `compile.propose_event_set` call `Provider.complete` with their own schemas; both go through `claude_schema` unchanged.

## Docs

`docs/threat-model.md`: the provider credentials row and the alias row name the Claude key and workspace; the unbounded-consumption row adds the spend cap as a spent quota. `AGENTS.md` does not name a provider or key (checked); no change. There is no `.env.example` in the repository; nothing to add.

## Tests (no real API call)

`tests/unit/test_model_choice.py` and a new `tests/unit/test_anthropic.py` with `httpx2.MockTransport` under the SDK's client:

- the request names the exact model, carries `output_config.format` (the converted schema, no `minimum`) and `effort: medium`, no `temperature`/`top_p`/`top_k`/`thinking`, `max_tokens` 16000, and `anthropic-workspace-id` only when a workspace is set;
- a JSON answer is returned as text and goes through `compile_policy`'s schema validation (a percent of 150 is still `CompileFailed`);
- `refusal` (with category), `max_tokens`, a per-minute 429 (names the limit, transient, not daily), the spend-cap 429 (daily, not transient), a 500, a 529 (transient) and a timeout each become `ProviderError` with that classification; one request is sent per call (`max_retries=0`);
- `build("anthropic:claude-haiku-5-5", ModelKeys(anthropic="k"))` raises; bench `_provider` returns a `Claude`;
- `test_every_provider_call_caps_its_output_tokens` covers the new vendor with its own cap.
- the request goes to `api.anthropic.com` with the given key while `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` are set; neither the key nor the workspace is in the provider's `repr`; every schema sent (`WIRE_SCHEMA`, `EVENT_SET_SCHEMA`, drafts' `SCHEMA`) keeps only accepted keywords; an event set is proposed through Claude; a 402 `billing_error` is spent and a non-JSON body is a `ProviderError`.
- Mutations: drop `max_retries=0`; treat a refusal as success; the spend cap as a per-minute limit; send the schema unconverted; drop the base URL pin; a billing error not spent; let a non-JSON answer escape; Claude's cap set to the others'. Each fails a test (`docs/evidence.md`).

## Human step

The Claude benchmark, at the prompt of 5b's fix round, with the developer's key: `bench/run.py --runs 2 --split all --models anthropic:claude-haiku-5-5,anthropic:claude-sonnet-5-5 --out <file>`, with `ANTHROPIC_API_KEY` and `ANTHROPIC_WORKSPACE_ID` in the environment (an `--env-file`). `CANDIDATES` (the default `--models`) is unchanged; the Claude specs are named. The run also confirms that the workspace header is accepted with the key, and records timeouts. Then the developer picks the default; `BENCHMARKED` and `DEFAULT_COMPILE_MODEL` change in the commit that appends the run.

## Review at start

An independent reviewer read this design against the plan, the code and the installed SDK's source on 2026-10-23. 13 findings:

| # | Finding | Severity | Outcome |
|---|---|---|---|
| 1 | SDK errors other than the four mapped (response validation, credentials, base `AnthropicError`) escape the no-AI path | HIGH | Fixed: every `AnthropicError` and a non-JSON body (`ValueError`) become `ProviderError`; tested |
| 2 | The SDK reads `ANTHROPIC_BASE_URL` when `base_url` is not passed | HIGH | Fixed: pinned; test with the variable set; mutation drops the pin |
| 3 | `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_LOG`, OpenTelemetry variables | MED | OpenTelemetry off; the other two named in the threat model for the deploy (step 10) |
| 4 | The key would appear in the dataclass `repr` | MED | Fixed: `repr=False` on key and workspace; tested |
| 5 | The `max_tokens` rationale is wrong; 90 s may be short for Claude | MED | Developer chose: keep `TIMEOUT_S`, watch timeouts in the benchmark; sentence corrected |
| 6 | The schema conversion is a denylist | MED | Fixed: the SDK's `transform_schema`; all three schemas tested |
| 7 | `propose_event_set` is a consumer too | MED | Named; tested through Claude |
| 8 | The token-cap test needs a per-vendor cap and a valid message body | MED | Done; mutation: Claude's cap set to the others' |
| 9 | The human step's command lacks `--out`; `CANDIDATES` | LOW | Fixed; `CANDIDATES` unchanged, the specs are named |
| 10 | 529 constant, `billing_error`, the spend-cap body shape | LOW | 529 is a named constant (`OVERLOADED`); developer chose `billing_error` as spent; the body shape is cited from the rate-limits page, and without `error_code` a 429 is per-minute (transient) |
| 11 | Secrets in errors via `__cause__` | LOW | Reasons carry no message text; nothing logs tracebacks of provider errors (checked: `service.run` stores only `_error_code`) |
| 12 | Workspace header with a plain key and the 1,000 RPM figure are unverified offline | LOW | The benchmark run confirms the header; spacing stays 1 s (ITPM may bind first; a 429 is waited out once) |
| 13 | `Anthropic` shadows the SDK's class | LOW | Renamed `Claude` |

## Review before commit

An independent reviewer read the implementation on 2026-10-23. 0 HIGH, 1 MED, 4 LOW:

| # | Finding | Severity | Outcome |
|---|---|---|---|
| 1 | `quality-lock.json` counts not raised | MED | Already raised before the commit (unit 121, bench 6; the reviewer read an earlier tree) |
| 2 | The schema conversion runs inside the `try`, so a fault in our schema would read as a provider error | LOW | Fixed: converted before the call |
| 3 | A 200 without `content` escapes `ProviderError` | LOW | Fixed: an answer without text is `ProviderError("no text in the answer")`; tested |
| 4 | The design names `Anthropic` and calls 529 a literal | LOW | Fixed |
| 5 | The validation test has no positive control | LOW | Fixed: the error names `percent`, and the same answer at 100 compiles |

