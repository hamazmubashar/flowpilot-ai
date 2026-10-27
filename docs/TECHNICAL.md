# FlowPilot AI — Technical Guide

> Portfolio overview: see the [main README](../README.md). The public demo is now live at <https://flowpilot-hamaz.duckdns.org>; milestone status notes below are kept as the engineering record.

A production-style AI business workflow automation platform for **Northstar Digital Solutions**, a
fictional B2B consultancy. Portfolio project: synthetic data only, zero paid APIs, everything runs
locally, and every outbound message requires human approval.

> **Status: M1–M4 committed; M5 (reviewer console) implemented, awaiting its milestone commit.**
> M1 deterministic intake, scoring, audit, and recovery. M2 advisory LLM qualification and reply
> drafting via a local Ollama model with deterministic fallback (LLM calls are **off by default**).
> M3 a hardened local n8n runtime (Docker) with one workflow (`flowpilot-prepare-next-lead`),
> statically validated and run **once against a scratch database**. M4 human approval/rejection of
> an exact draft version by individually authenticated reviewers, with a simulated-only outbox.
> M5 an optional Streamlit console over the API: sign-in, Dashboard, Leads, Draft Responses,
> Outbox (Simulated) and Settings. CSV import is deferred.

## What M1 does

`POST /leads` accepts an inbound enquiry and, in a single database transaction:

1. validates it (unknown fields rejected; **only reserved synthetic email domains accepted**),
2. honours an optional `Idempotency-Key` (safe retries),
3. de-duplicates on normalized email + company (`ACME, Inc.` == `acme`),
4. stores the lead, scores it with versioned deterministic rules, and routes it
   (`hot → partner_review`, `warm → sales_queue`, `cold → nurture`),
5. appends every state change to an append-only audit log.

Guardrails for later milestones are already enforced in the schema: approvals must come from a
`human` actor, outbox rows require an approved draft (trigger), and the only send channel is
`simulated`.

## Architecture

```
n8n (M3, orchestration only) ──HTTP + X-API-Key──► FastAPI (127.0.0.1:8000)
                                                     ├─ domain/   pure rules: validation, scoring
                                                     ├─ services/ transactions: intake flow
                                                     ├─ db/       migrations + repository (only SQL)
                                                     └─► SQLite (WAL) — FastAPI is the sole writer
Streamlit (M5) ──HTTP──► FastAPI          Ollama (M2) ◄── FastAPI llm/ adapter
```

See [docs/adr/0001-m1-foundation.md](adr/0001-m1-foundation.md) for the decisions behind this.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned via `.python-version`.

```bash
uv sync                                   # create .venv from uv.lock
cp .env.example .env
uv run flowpilot-admin gen-key            # paste the output into FLOWPILOT_API_KEY in .env
uv run flowpilot-admin migrate            # create var/flowpilot.db
uv run flowpilot-admin seed               # 60 deterministic synthetic leads (idempotent)
uv run flowpilot-api                      # serves http://127.0.0.1:8000
```

## API

All endpoints except `/health` require `X-API-Key`. Optional headers: `Idempotency-Key`,
`X-Actor` (audit label), `X-Request-ID` (echoed back; generated if absent).

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness + DB/migration state (no auth; 503 if degraded) |
| POST | `/leads` | Submit a lead → `201` created, `200` duplicate, `422` invalid / key reuse |
| GET | `/leads?status=&tier=&limit=&offset=` | Paginated list with score summary |
| GET | `/leads/{id}` | Lead with full score breakdown |
| GET | `/leads/{id}/audit` | Audit trail for a lead |
| POST | `/leads/{id}/qualification` | Advisory assessment (LLM or rules fallback) → `201` |
| GET | `/leads/{id}/qualification` | Latest assessment (`404 assessment_not_found` if none) |
| POST | `/leads/{id}/drafts` | New reply draft version (LLM or template) → `201`; `409` unless `scored`/`drafted` |
| GET | `/leads/{id}/drafts` | All draft versions, oldest first |
| POST | `/leads/{id}/drafts/{version}/approval` | Human review (M4): body `{"decision": "approved"\|"rejected", "reason"?}` → `201` |
| GET | `/outbox?status=&limit=&offset=` | Simulated outbox entries (newest first) with lead, draft version and approving reviewer; read-only, nothing is sent (M5) |
| GET | `/reviewers/me` | Reviewer holding `X-Reviewer-Token` → `{id, name}`; read-only sign-in check (M5) |

**Human review (M4).** The approval endpoint also requires `X-Reviewer-Token`: the reviewer's
own token from `flowpilot-admin reviewer add <name>` (shown once; only its SHA-256 is stored;
never given to n8n). The reviewer's identity comes from the database and is stored as
`decided_by` + `reviewer_id` with `actor_type = 'human'`; `X-Actor` is ignored for decisions. A
missing, unknown or revoked token returns `403 reviewer_required`; a database trigger also
refuses any approval without an active reviewer. Only the latest draft version of a
`drafted`/`awaiting_approval` lead can be decided, once (`409 draft_superseded` /
`draft_already_decided` / `lead_not_reviewable`). Approval moves the lead to `approved` and
enqueues one **simulated** outbox entry (`pending`) with that draft's exact content; rejection
moves it to `rejected`. Nothing is ever sent. `Idempotency-Key` makes retries safe and is scoped
to the reviewer. `GET /reviewers/me` runs the same token check without writing anything; the M5
console uses it to sign reviewers in.

**Reviewer console (M5).** Optional Streamlit UI (`ui` dependency group) that calls the API over
loopback and never opens the database. It binds to `127.0.0.1:8501` with usage statistics off
(`.streamlit/config.toml`; run it from the repository root):

```bash
uv run flowpilot-api                                       # in one terminal
uv run --group ui streamlit run src/flowpilot/ui/app.py    # in another; open http://localhost:8501
```

The console reads `FLOWPILOT_API_KEY` and `FLOWPILOT_API_URL` (loopback only) like the API. A
reviewer's token is held only in that browser session's server-side memory: it is never shown,
logged or saved, and a page reload signs the reviewer out. Every page load re-checks it with
`GET /reviewers/me`, so a revoked token returns to sign-in before any data is shown.

| Page | What it does (all data from the API) |
|---|---|
| Sign in | Reviewer token check (`GET /reviewers/me`) |
| Dashboard | Totals, new leads per day, leads by source and status, recent leads, pending review, recent audit activity (`GET /leads`, `GET /leads/{id}/audit`) |
| Leads | Server-side status/tier filters and paging; lead details; Add Lead form (`POST /leads`, with an idempotency key so retries cannot duplicate) |
| Draft Responses | Queue of `drafted`/`awaiting_approval` leads, lead context, advisory assessment, draft versions; approve or reject the latest version after an explicit confirmation (`POST .../approval` with the reviewer's own token) |
| Outbox (Simulated) | Read-only list of approved replies (`GET /outbox`); nothing is sent |
| Settings | Read-only: confirmed reviewer, API address and `/health`, how reviewers are managed |

Not available in the console: text search and source/date filters (the API filters leads by
status and tier only), CSV import (deferred), reviewer management (CLI only), editing drafts or
outbox entries. UI tests use Streamlit's in-process `AppTest`; visual checks are manual.

```bash
source .env
curl -s -X POST http://127.0.0.1:8000/leads \
  -H "X-API-Key: $FLOWPILOT_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-001" \
  --data @data/synthetic/sample_lead.json
```

Swagger UI is disabled by default because it loads assets from a public CDN; set
`FLOWPILOT_ENABLE_DOCS=true` to enable it. `/openapi.json` is always served locally.

## Portfolio demo

`uv run flowpilot-demo reset --yes`, `uv run flowpilot-demo serve`, then `uv run flowpilot-demo run`
submits five synthetic scenarios (hot, warm and cold leads, a duplicate, an invalid email)
against an isolated demo database that cannot be the normal one, with LLM mode off. See
[docs/runbook.md](runbook.md#scenario-portfolio-demo-environment-isolated).

The n8n workflow `flowpilot-lead-intake` (inactive by default) accepts the same leads through an
authenticated webhook and submits them to `POST /leads`; see the runbook's "n8n webhook intake".
`uv run flowpilot-demo eval --yes` measures the local model (success rate, fallback reasons,
latency, guard failures) on synthetic leads in the demo database; see the runbook's
"Synthetic LLM evaluation".

## Public demo deployment (ADR 0005)

Host-independent container setup in `deploy/public-demo/`: nginx (TLS, rate limits) in front of
the Streamlit console; the API and its demo database stay on internal networks; n8n is not
deployed; synthetic data only, LLM off, scheduled refresh of the demo data. Visitors get one-click guest access as the demo reviewer
([ADR 0006](adr/0006-demo-guest-access.md)); token sign-in stays available. Public binding needs
an explicit double opt-in and works only for the demo database. Verified locally with a
self-signed certificate; not deployed to any host. See
[docs/adr/0005-public-demo.md](adr/0005-public-demo.md) and the runbook's "Public demo
deployment".

## Admin CLI

| Command | Purpose |
|---|---|
| `flowpilot-admin migrate` | Apply pending migrations (checksummed, atomic per file) |
| `flowpilot-admin status` | Show schema version and pending migrations |
| `flowpilot-admin seed [--count N] [--seed S]` | Insert synthetic leads; re-running is a no-op |
| `flowpilot-admin backup [--out FILE]` | Online-consistent backup + integrity check |
| `flowpilot-admin restore FILE --yes` | Verify backup, save current DB, restore |
| `flowpilot-admin verify` | Integrity, foreign keys, migration checksums |
| `flowpilot-admin gen-key` | Generate an API key |
| `flowpilot-admin reviewer add NAME` / `list` / `revoke NAME` | Manage human reviewers; `add` prints the token once (M4.1) |
| `flowpilot-admin llm-check` | Ollama reachable on loopback + model installed (`/api/tags` only; no inference, no download) |

Recovery procedures: [docs/runbook.md](runbook.md). Scoring rules:
[docs/scoring-rules.md](scoring-rules.md).

## Quality checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run coverage run -m pytest
uv run coverage report            # fails under 80%
```

The test suite blocks all non-loopback network connections, so an accidental external call fails
the build.

## Security & privacy posture

- Loopback-only binding is enforced in settings (non-loopback `FLOWPILOT_HOST` is a startup error).
- API key ≥ 32 chars, compared in constant time; never printed in `Settings` repr.
- Synthetic data only: emails must use `example.com/.net/.org` or `.example/.test/.invalid`.
- Audit log is append-only (UPDATE/DELETE blocked by triggers).
- No automatic approval and no real sending exist anywhere in the codebase or schema.
- Application logs are JSON lines on stderr with request id, route template, status and timing
  only: no query strings, headers, bodies, exception messages, keys, tokens or lead data
  (`FLOWPILOT_LOG_LEVEL`; see the runbook's "Logs").

## Local LLM (M2)

Qualification and drafting are **advisory**. They never change a lead's score, tier, or route, and
cannot approve or send anything: approvals must come from a `human` actor and the outbox accepts
only `simulated` messages for an approved draft (database-enforced since M1). Drafting moves a lead
from `scored` to `drafted` and nothing further.

**Enable explicitly** (default `FLOWPILOT_LLM_MODE=off` uses deterministic fallbacks only):

Add these lines to `.env` (the URL must be loopback; anything else is a startup error):

```dotenv
FLOWPILOT_LLM_MODE=ollama
FLOWPILOT_OLLAMA_URL=http://127.0.0.1:11434
FLOWPILOT_LLM_MODEL=qwen2.5:3b
FLOWPILOT_LLM_TIMEOUT_S=60
```

`.env` comments must be on their own lines; an inline `# ...` after a value becomes part of the
value. Then verify before starting the API:

```bash
uv run flowpilot-admin llm-check
```

Every qualification or draft request for an eligible lead returns a usable result (`201`); only
auth, unknown-lead, ineligible-status, and validation errors (`401`/`404`/`409`/`422`) do not. The
pipeline per request:

1. **Pre-screen** company name, contact name, and message for prompt-injection patterns, after
   Unicode normalization (NFKC) and removal of invisible characters so zero-width splitting and
   fullwidth text cannot evade it; bidi overrides and Unicode tag characters count as a match. A
   match skips the model entirely (`fallback_reason: injection_suspected`). Invisible characters
   are also stripped from everything sent to the model.
2. **Call Ollama** (`/api/chat`, `stream: false`, `temperature: 0`, fixed seed) with a versioned
   system prompt; lead text is sent as delimited JSON data, never as instructions, and the contact
   email and budget band are never sent. The JSON schema of the expected output is passed as
   `format`.
3. **Validate** the reply strictly (Pydantic, unknown fields rejected), **repair layout** with a
   whitespace-only normalizer (greeting and `Best regards,` / sign-off moved onto their own lines;
   no words are added, removed, or changed, and a wrong sign-off is never fixed), then apply
   **output guards**. Layout guards judge the repaired text; every other guard judges both the
   original and the repaired text, so repair can never clear a content or security violation.
   Output guards:
   drafts may not contain links, email addresses, phone numbers, prices, placeholders, role markers,
   echoed injection text, hidden/invisible characters, or any reference to the prospect's budget;
   subjects may not contain line breaks. Drafts must follow the required layout (greeting line,
   blank-line-separated paragraphs of readable length, `Best regards,`, then the exact sign-off line
   `Northstar Digital Solutions`). A model `requests_pricing` flag is kept only if the lead's own
   message explicitly asks about price, fees, or a quote.
4. **Fall back** deterministically on any failure: rules-based assessment or a route-specific
   template draft. `fallback_reason` is one of `disabled`, `injection_suspected`, `unavailable`,
   `timeout`, `model_missing`, `busy`, `invalid_output`, `guard_rejected`.

Each attempt is recorded in `llm_runs` (model, prompt version, outcome, latency, input hash,
truncated raw output) and in the audit log. Only one generation runs at a time.

**Model licensing (unverified).** `ollama show qwen2.5:3b` reports the *Qwen RESEARCH LICENSE
AGREEMENT*, which appears to restrict commercial use; `gemma3:1b` reports the *Gemma Terms of Use*.
This project uses them only for a non-commercial portfolio demo and makes **no claim of commercial
suitability**. Review the license text yourself before any other use; the model is configurable.

Live checks against a real model are opt-in and never part of the default suite:
`FLOWPILOT_TEST_OLLAMA=1 uv run pytest -m ollama -s`.

## n8n runtime (M3, in progress)

`compose.yaml` runs a pinned, hardened n8n (`n8nio/n8n@sha256:c72fa9f1…`, n8n 2.44.4) for
workflow orchestration. n8n only calls the FastAPI endpoints above; it never sends messages and
never approves anything.

### Workflow: `flowpilot-prepare-next-lead` (M3-P2)

[`n8n/workflows/flowpilot-prepare-next-lead.json`](../n8n/workflows/flowpilot-prepare-next-lead.json),
manual trigger, inactive:

```
Manual trigger → GET /leads?status=scored&limit=1 → queue empty? ── yes → stop
                                                        └─ no → pick lowest-id lead
  → POST /leads/{id}/qualification → POST /leads/{id}/drafts → stop: awaiting human approval
```

- Every call goes to `http://host.docker.internal:8000` (the api-gateway), authenticates with the
  n8n Header Auth credential **`FlowPilot API`** (`X-API-Key`), and sends `X-Actor:
  n8n:prepare-next-lead` and `X-Request-ID: n8n-<execution id>`.
- POSTs carry no body and per-lead idempotency keys (`n8n:qualify:lead:<id>:v1`,
  `n8n:draft:lead:<id>:v1`), so a rerun or a retried failed execution for the same lead replays
  instead of duplicating. Node-level automatic retries (Retry On Fail) are not enabled.
- It stops before approval; nothing reaches the approval or outbox tables.
- `tests/test_n8n_workflows.py` checks every export statically (node allowlist, fixed URL,
  approved method/path pairs, idempotency keys, stored credential, inactive, no pinned data, every
  node reachable from the trigger). `tests/test_workflow_contract.py` replays the same call
  sequence against FastAPI. Neither runs the workflow inside n8n; one manual run against a scratch
  database (LLM off) is recorded in ADR 0003 P2.

```
host 127.0.0.1:5678 ─► ui-gateway ─────────────┐
                                               ▼
                       [ n8n ]  ── n8n_internal network (internal: no route out) ──
                          │
                          └─► api-gateway (alias host.docker.internal) ─► 192.168.65.254:8000 ─► FastAPI 127.0.0.1:8000
```

- **Egress boundary is the network, not n8n settings.** n8n is attached only to an `internal`
  Docker network (no default route, no external DNS). Its only ways out are two gateways, each a
  ~40-line TCP relay (`n8n/gateway/tcp-gateway.js`) with one destination fixed in `compose.yaml`
  and no proxy logic. Proxy environment variables are not used.
- **UI is loopback-only** at **`http://localhost:5678`** (use `localhost`, not `127.0.0.1`: the
  session cookie is scoped to the hostname). It is not reachable from the LAN or on `[::1]`.
- **Hardening** (defence in depth): telemetry, version checks, templates, personalization, public
  API and the `mcp-registry` module are disabled; nodes are restricted to a 9-node allowlist (no
  Code, Execute Command, SSH, email or chat nodes); executions pruned after 7 days; one
  production execution at a time; containers drop all capabilities.

```bash
# once: set N8N_ENCRYPTION_KEY in .env (openssl rand -hex 32) and back it up outside the repo
uv run flowpilot-api                  # FastAPI on 127.0.0.1:8000 (separate terminal)
docker compose up -d                  # n8n + gateways; refuses to start without the key
open http://localhost:5678            # create the local owner account on first visit
docker compose down                   # stop (data stays in var/n8n)
```

Operations, key loss, and verification steps: [docs/runbook.md](runbook.md#n8n-runtime-m3).
Design and evidence: [docs/adr/0003-n8n-orchestration.md](adr/0003-n8n-orchestration.md).

## Known limitations (M1–M3)

- **One shared API key.** All clients use the same `FLOWPILOT_API_KEY`; there are no per-client
  keys or scopes. Review decisions additionally need a per-reviewer token.
- **`X-Actor` is self-declared.** The audit actor label is supplied by the caller and trusted only
  because the caller holds the API key. Review decisions do not use it: they are attributed to
  the reviewer whose token was presented.
- **Reviewer tokens are bearer tokens.** No password, MFA or expiry: whoever holds a reviewer's
  token acts as that reviewer until `flowpilot-admin reviewer revoke`. They travel over loopback
  HTTP only.
- **SQLite supports only one writer process.** FastAPI is the sole writer; running multiple API
  worker processes is not supported.
- **No rate limiting.** Requests are not throttled.
- **Plain HTTP.** No TLS, because the service is restricted to loopback addresses.
- **The outbox is enqueue-only.** Approval writes a `pending` simulated entry; nothing marks it
  sent and there is no endpoint to list it. A rejected lead cannot be redrafted.
- **Scoring and routing remain rules-only.** LLM output is advisory and is never fed back into
  them.
- **The injection pre-screen is heuristic.** It will miss novel phrasings, paraphrases, other
  languages, and homoglyph substitutions (e.g. Cyrillic look-alikes, which NFKC does not fold), and
  occasionally flag benign text (which then gets a template draft). The primary defence is that
  model output has no authority.
- **Output guards are strict and pattern-based.** Legitimate drafts can be rejected (e.g. a lead
  asking for help with *budgeting software* trips the budget guard) and fall back to the template;
  paraphrased budget references may still slip through to human review.
- **LLM calls are synchronous.** A request waits for generation (up to `FLOWPILOT_LLM_TIMEOUT_S`;
  the first call also loads the model), and only one generation runs at a time; concurrent
  requests wait up to 5 s, then fall back with `busy`.
- **Live behaviour is only spot-checked.** Three opt-in runs (one synthetic lead each,
  `qwen2.5:3b` on an M1 MacBook Air, temperature 0) measured roughly 2.5-5.8 s per call (the
  higher figures include a cold model load) and schema-valid JSON every time, so Ollama's `format`
  constraint appeared to be honoured. Prompts p1 and p2 produced single-line drafts that failed
  the layout guards; p3 produced a draft that passed every guard without repair. This is not a
  measured pass rate across leads, routes, or message styles; the default suite uses a fake client,
  and strict validation plus fallback keep the API safe either way.
- **Draft quality from a 3B model is limited.** Live drafts were generic, and assessments raised
  questionable advisory flags (e.g. `unclear_need` for a clearly stated need). Drafts are starting
  points for human review.
- **Unexpected errors return 500, not a fallback.** Model failures the client maps to typed errors
  fall back deterministically, but an unexpected exception (for example a bug, or an HTTP-client
  error outside the mapped types) returns a generic `500 Internal Server Error`. No internal
  details are exposed and nothing is written, but no draft is produced for that request.
- **M1 intake stores invisible characters.** Lead fields may contain zero-width, bidi, or other
  control/format characters. M2 strips them before anything is sent to the model and the
  injection pre-screen detects them, but the stored values are unchanged: any future UI or export
  must escape or strip them.
- **`llm_runs.raw_output` has no retention limit.** Up to 4 KB of raw model output is stored per
  attempt indefinitely (synthetic data only in this project); a retention policy is not yet
  implemented.
- **n8n egress isolation is verified only for the tested topology.** It was tested on Docker
  Desktop 29.8.2 for macOS, where `192.168.65.254` forwards to host loopback. Other Docker
  versions or Linux need re-testing. The gateways themselves have outbound network access; their
  safety rests on their fixed-destination code. Anything that reaches FastAPI through the
  api-gateway can use every endpoint the API key allows.
- **Browser login to the n8n UI was verified with curl, not a real browser.** The session cookie
  is `Secure; HttpOnly; SameSite=Lax`, which browsers accept on `http://localhost`.
- **Losing `N8N_ENCRYPTION_KEY` makes stored n8n credentials unrecoverable.**
- **The n8n workflow has been run only once.** One manual run against a scratch database with the
  LLM off succeeded. The `ollama` path, reruns, the empty-queue branch, runtime error handling and
  the 200 s HTTP timeouts are untested inside n8n.
- **Prompt examples can be copied.** The draft prompt includes a worked example (contact "Alex",
  "reporting needs"). A model may copy its name or wording into a real draft; no guard checks that
  the greeting matches the lead's contact name, so human review must catch it.

## License

Code: MIT, see [LICENSE](../LICENSE). n8n (introduced in M3) is distributed under the Sustainable Use License and is not
bundled in this repository; the image is pulled by digest at runtime. That license permits use
"for your own internal business purposes or for non-commercial or personal use", and excludes
`.ee.` (Enterprise) files. This project uses n8n only as a local, non-commercial demo.
