# ADR 0001 — M1 foundation decisions

Status: accepted (2026-10-20)

## Context

M1 must be deterministic and fully local: no LLM, no n8n, no Docker, no external network
requests. Requirements: localhost-only, authenticated API, business rules and DB access only in
FastAPI, idempotency, audit logging, synthetic-only data, migrations, tests, documented recovery,
and mandatory human approval with simulated sending only.

## Decisions

1. **Python 3.12, managed by uv**, pinned in `.python-version` and `requires-python`.
2. **SQLite (stdlib `sqlite3`) in WAL mode; FastAPI is the only writer.** Writers use
   `BEGIN IMMEDIATE` so concurrent requests serialize instead of failing mid-transaction.
3. **Custom migration runner (~130 lines, stdlib)** rather than Alembic: plain SQL files,
   per-file atomicity, SHA-256 checksums to detect edits of applied migrations. Avoids an ORM and
   two extra dependencies for a single-database project.
4. **Synthetic-only is enforced, not just intended**: the API rejects any email outside
   RFC 2606/6761 reserved names. The generator uses stdlib `random` with a fixed seed instead of
   Faker (one less dependency, fully reproducible).
5. **Idempotency**: optional `Idempotency-Key` header, stored per scope with a request hash.
   Same key + same body replays the original result (`Idempotent-Replayed: true`); same key +
   different body returns `422 idempotency_key_reused` (per the IETF httpapi idempotency draft).
   Natural dedupe on normalized email + company covers clients that send no key.
6. **Audit log is append-only**, enforced with `BEFORE UPDATE/DELETE` triggers.
7. **Human approval and simulated sending are schema-level guarantees**, created now so later
   milestones cannot bypass them: `approvals.actor_type = 'human'`, `outbox.channel =
   'simulated'`, and a trigger requiring an approved approval for the exact draft version.
8. **Auth**: single static API key (≥ 32 chars, constant-time compare). `X-Actor` is a
   caller-declared audit label; real per-user identity is deferred to the M4 approval UI.
9. **Swagger UI disabled by default** because it fetches assets from a public CDN.
10. **Admin tasks live in a package CLI (`flowpilot-admin`)** instead of loose `scripts/`, so
    they are typed, tested, and installed as entry points.

## Dependencies

| Package | Group | Why |
|---|---|---|
| fastapi | runtime | Approved API framework (brings pydantic + starlette) |
| uvicorn | runtime | ASGI server to run FastAPI |
| pytest | dev | Test runner |
| httpx | dev | Required by `fastapi.testclient.TestClient` |
| coverage | dev | Coverage gate (≥ 80%) |
| ruff | dev | Lint + format |

`uv_build` is the build backend (build-time only, from the uv project).

## Consequences

- Single-writer SQLite limits horizontal scaling; acceptable for a local demo. Revisit if multiple
  API workers are needed.
- Changing scoring requires a `RULES_VERSION` bump; old scores stay attributable.
