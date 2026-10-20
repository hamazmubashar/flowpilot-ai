# FlowPilot AI

A production-style AI business workflow automation platform for **Northstar Digital Solutions**, a
fictional B2B consultancy. Portfolio project: synthetic data only, zero paid APIs, everything runs
locally, and every outbound message requires human approval.

> **Status: Milestone 1 (Foundation & Core API).** Deterministic lead intake, scoring, audit, and
> recovery tooling. No LLM, no n8n, no Docker in this milestone.

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

See [docs/adr/0001-m1-foundation.md](docs/adr/0001-m1-foundation.md) for the decisions behind this.

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

```bash
source .env
curl -s -X POST http://127.0.0.1:8000/leads \
  -H "X-API-Key: $FLOWPILOT_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-001" \
  --data @data/synthetic/sample_lead.json
```

Swagger UI is disabled by default because it loads assets from a public CDN; set
`FLOWPILOT_ENABLE_DOCS=true` to enable it. `/openapi.json` is always served locally.

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

Recovery procedures: [docs/runbook.md](docs/runbook.md). Scoring rules:
[docs/scoring-rules.md](docs/scoring-rules.md).

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

## Known limitations (M1)

- **One shared API key.** All clients use the same `FLOWPILOT_API_KEY`; there are no per-client
  keys or scopes.
- **`X-Actor` is self-declared.** The audit actor label is supplied by the caller and trusted only
  because the caller holds the API key. Per-user identity arrives with the M4 approval flow.
- **SQLite supports only one writer process.** FastAPI is the sole writer; running multiple API
  worker processes is not supported.
- **No rate limiting.** Requests are not throttled.
- **Plain HTTP.** No TLS, because the service is restricted to loopback addresses.
- **Draft, approval, and outbox tables exist, but their API endpoints are deferred until M4.**
  Their schema guardrails are in place and tested; nothing writes to them yet.
- **LLM integration is deferred until M2.** Scoring and routing are rules-only.

## License

Code: MIT, see [LICENSE](LICENSE). n8n (introduced in M3) is distributed under the Sustainable Use License and is not
bundled in this repository.
