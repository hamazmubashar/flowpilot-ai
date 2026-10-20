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
   fields, matched after NFKC normalization and invisible-character removal; bidi controls and
   Unicode tag characters are treated as a match. On a match the model is never called, the
   assessment withholds the message text, and the template draft uses a generic salutation.
   Invisible characters are stripped from all text sent to the model.
5. **Structured output, validated twice.** The Pydantic JSON schema is sent as Ollama `format`, and
   the reply is re-validated strictly (`extra="forbid"`, enums, lengths) because server-side schema
   enforcement is not trusted. Model-settable flags exclude `possible_prompt_injection`.
6. **Output guards** reject links, emails, phone numbers, currency amounts, placeholders, role
   markers, and echoed injection text in drafts (and links/contacts/markers in summaries). Drafts
   must also end with the exact sign-off line `Northstar Digital Solutions` (added in prompt
   `2026.10-p2` after the first live run returned a draft without it). A model `requests_pricing`
   flag is kept only when the lead's message explicitly asks about price, fees, or a quote.
   Drafts are also rejected for hidden characters, budget references (direct or common
   paraphrases), line breaks in the subject (header-injection risk once sending exists), or a
   layout other than greeting line / blank-line-separated paragraphs (max 600 characters each) /
   `Best regards,` / sign-off.
   Because `qwen2.5:3b` returned single-line drafts on prompts p1 and p2, prompt `2026.10-p3` adds
   a worked JSON example, and `llm/layout.py` applies a whitespace-only repair before the guards:
   CRLF/trailing-space cleanup, a leading `Hi|Hello|Dear <Name>,` moved to its own line, and an
   exact trailing `Best regards, Northstar Digital Solutions` split onto its own lines. An
   invariant rejects any change to the visible words. Non-layout guards run on both the original
   and the repaired text; the repaired body is re-validated for length; the audit entry records
   `layout_normalized`; `llm_runs.raw_output` keeps the original.
7. **Lead text is data, not instructions.** Sent as delimited JSON with control characters removed
   and the message capped at 2,000 characters. The contact email and budget band are never sent to
   the model (budget is used only by deterministic scoring). The model's built-in system prompt is
   always replaced.
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
- Three opt-in live runs (one lead each, `qwen2.5:3b`, M1 MacBook Air) measured roughly
  2.5-5.8 s per call and returned schema-valid JSON each time. p1/p2 drafts were single-line and
  failed the layout guards; the p3 draft passed every guard unrepaired. These are spot checks, not
  a pass rate.
- Known gaps: unexpected (non-`LLMError`) exceptions return a generic 500 rather than falling back;
  M1 intake stores invisible characters unchanged (M2 strips them only from model input), so any
  UI must escape them; `llm_runs.raw_output` has no retention limit; the prompt's worked example
  ("Alex") can be copied into a draft and no guard checks the greeting name.
- The injection pre-screen originally used `.` to bridge trigger words, so a line break evaded it;
  fixed with `[\s\S]` bridges. Coverage: deterministic newline-split regression tests for input
  screening and draft injection-echo (`tests/test_llm_guards.py`), plus seeded whitespace-scramble
  property tests (`tests/test_guard_properties.py`) for (a) drafts carrying "ignore"/"disregard"
  injection text and (b) input payloads that trigger only `ignore_instructions` or
  `override_instructions`, so no other pattern can mask a bridge regression. The generic injection
  samples trip several patterns at once and do not by themselves exercise the bridges.
- Synchronous generation blocks the request; acceptable for a single-user local demo, revisit with
  n8n orchestration in M3.
- The regex pre-screen has false negatives and positives; safety does not depend on it alone.
