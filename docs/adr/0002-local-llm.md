# ADR 0002: M2 local LLM integration

Status: accepted (2026-10-20)

## Context

M2 adds LLM-assisted lead qualification and reply drafting using a locally installed Ollama model
(`qwen2.5:3b`, `gemma3:1b`) on a 16 GB MacBook Air M1. Constraints: no paid services, cloud, or
external network calls; no model downloads; M1 scoring, routing, and guardrails must be preserved;
LLM output must never approve leads or trigger communication; independent of n8n and the dashboard.

## Decisions

1. **Advisory only, on demand.** Two explicit endpoints (`POST /leads/{id}/qualification`,
   `POST /leads/{id}/drafts`) rather than running the model inside intake. Intake stays fast and
   deterministic. Output is stored in `lead_assessments` / `drafts` and never read by scoring or
   routing. The only status change is `scored → drafted`.
2. **`FLOWPILOT_LLM_MODE=off` by default** (D3). Model calls require explicit local configuration.
   Off mode returns deterministic fallbacks through the same API.
3. **Deterministic fallback on every failure.** The API always returns `201` with a usable result
   and a typed `fallback_reason`; it never surfaces LLM failures as 5xx.
4. **Prompt-injection pre-screen skips the model** (D2). Regex patterns over all free-text lead
   fields. On a match the model is never called, the assessment withholds the message text, and the
   template draft uses a generic salutation.
5. **Structured output, validated twice.** The Pydantic JSON schema is sent as Ollama `format`, and
   the reply is re-validated strictly (`extra="forbid"`, enums, lengths) because server-side schema
   enforcement is not trusted. Model-settable flags exclude `possible_prompt_injection`.
6. **Output guards** reject links, emails, phone numbers, currency amounts, placeholders, role
   markers, and echoed injection text in drafts (and links/contacts/markers in summaries).
7. **Lead text is data, not instructions.** Sent as delimited JSON with control characters removed
   and the message capped at 2,000 characters. The contact email is never sent to the model. The
   model's built-in system prompt is always replaced.
8. **`httpx` as a runtime dependency** (D1), reusing the already-locked package: separate connect
   (2 s) and read (`FLOWPILOT_LLM_TIMEOUT_S`, default 60 s) timeouts, `trust_env=False` so proxy
   variables cannot route traffic off-host, no redirects, and `MockTransport` for socket-free tests.
9. **Loopback-only Ollama URL**, enforced in settings like the API bind address.
10. **No retries; single-flight concurrency.** Temperature 0 with a fixed seed makes immediate
    retries mostly pointless; workflow-level retries belong to n8n (M3). One generation at a time
    (a semaphore with a 5 s wait, otherwise `busy`) to bound memory use.
11. **No DB transaction during generation.** Read, call the model, then write in one short
    `BEGIN IMMEDIATE` transaction that re-checks idempotency and lead status.
12. **Schema guardrails (migration 0002):** `llm_runs` records every attempt (`outcome` must agree
    with `fallback_reason`); an `llm` draft requires a successful run for the same lead and a model
    name (trigger); drafts are immutable (trigger), so edits become new versions.
13. **Model: `qwen2.5:3b` first** (D4), configurable. Better instruction and JSON adherence than
    1B, and it fits in 16 GB. **License uncertainty:** Ollama reports the Qwen RESEARCH LICENSE
    AGREEMENT for this model, which appears to restrict commercial use. The full text was not
    reviewed. The project claims no commercial suitability.

## Consequences

- Default tests are deterministic and offline (a fake client); live inference is opt-in
  (`pytest -m ollama` with `FLOWPILOT_TEST_OLLAMA=1`).
- Real latency, output quality, and Ollama `format` enforcement on this hardware are unmeasured
  until the opt-in live run is approved.
- Synchronous generation blocks the request; acceptable for a single-user local demo, revisit with
  n8n orchestration in M3.
- The regex pre-screen has false negatives and positives; safety does not depend on it alone.
