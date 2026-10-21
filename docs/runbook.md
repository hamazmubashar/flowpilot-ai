# Operations & recovery runbook

All commands run from the repo root. `flowpilot-admin` reads `FLOWPILOT_DB_PATH` and
`FLOWPILOT_BACKUP_DIR` from the environment / `.env` (defaults `var/flowpilot.db`, `var/backups`);
override with `--db` / `--backup-dir`.

## Routine backup

```bash
uv run flowpilot-admin backup                 # var/backups/flowpilot-<UTC timestamp>.db
```

Uses the SQLite online backup API, so it is consistent even while the API is serving, then runs
`PRAGMA integrity_check` on the copy. Never copy the `.db` file with `cp` while the API is
running: in WAL mode recent writes live in `*.db-wal` and would be lost.

## Health check

```bash
uv run flowpilot-admin verify   # integrity_check, foreign_key_check, migration checksums
curl -s http://127.0.0.1:8000/health
```

`/health` returns `503` with `database: migrations_pending | unavailable` when degraded.

## Scenario: restore from backup (corruption, bad data, accidental deletion)

1. Stop the API (`Ctrl+C` on `flowpilot-api`). Later milestones: pause n8n workflows too.
2. Pick a backup: `ls -lt var/backups/`.
3. Restore:
   ```bash
   uv run flowpilot-admin restore var/backups/flowpilot-<ts>.db --yes
   ```
   The backup is verified (integrity, foreign keys, migration checksums) **before** anything is
   touched, and the current database is saved to `var/backups/pre-restore-<ts>.db` first.
4. `uv run flowpilot-admin verify`, then start the API.
5. Lost writes: anything after the backup timestamp. Clients that retry with the same
   `Idempotency-Key` can safely resubmit.

## Before applying migrations (e.g. upgrading to M2's `0002_llm`)

Migrations are forward-only; the rollback path is restoring a backup. Always back up first:

```bash
uv run flowpilot-admin backup      # note the printed path
uv run flowpilot-admin migrate
uv run flowpilot-admin verify
```

If the upgraded version must be abandoned, stop the API and
`uv run flowpilot-admin restore <backup path> --yes` with the previous code checked out.

## Scenario: failed migration

Each migration runs in its own transaction; a failure rolls back that file completely and the
command exits non-zero. The database stays at the previous version, and the API refuses to start
(`pending migrations`) until it is fixed. Fix the SQL, then re-run `flowpilot-admin migrate`.

## Scenario: "migration … was modified after being applied"

An applied migration file was edited. Applied migrations are immutable: revert the edit
(`git diff src/flowpilot/db/migrations/`) and put the change in a new numbered migration.

## Scenario: API won't start

| Message | Fix |
|---|---|
| `FLOWPILOT_API_KEY is not set` / `at least 32 characters` | `flowpilot-admin gen-key` → `.env` |
| `is not a loopback address` | Set `FLOWPILOT_HOST=127.0.0.1` (by design, non-loopback is refused) |
| `database … does not exist` | `flowpilot-admin migrate` (and `seed` for demo data) |
| `pending migrations` | `flowpilot-admin migrate` |

## Scenario: API key leaked or rotated

1. `uv run flowpilot-admin gen-key`, update `FLOWPILOT_API_KEY` in `.env`.
2. Restart the API; the old key stops working immediately.
3. Update clients (n8n credential from M3 onward).
4. Review `audit_log` for unexpected actors since the suspected leak.

## Scenario: add, rotate, or revoke a reviewer (M4.1)

Each human reviewer has their own token; only its SHA-256 is stored.

```bash
uv run flowpilot-admin reviewer add avery     # prints the token ONCE; hand it to avery only
uv run flowpilot-admin reviewer list          # names and status, never tokens
uv run flowpilot-admin reviewer revoke avery  # their token stops working on the next request
```

A lost token cannot be recovered; revoke it and add the reviewer under a new name (names are
permanent and revocation cannot be undone, so history stays attributable). Never put a
reviewer token in n8n, `.env`, logs or tickets. After a suspected leak, revoke, then check
`approvals` and `lead.approved`/`lead.rejected` audit rows by that reviewer since the leak;
decisions are final, so a wrongly approved draft's `pending` simulated outbox entry must be
handled by hand.

## Scenario: rebuild a clean demo database

```bash
uv run flowpilot-admin backup                 # optional: keep the old one
mv var/flowpilot.db var/flowpilot.db.old      # stop the API first; also move -wal/-shm if present
uv run flowpilot-admin migrate
uv run flowpilot-admin seed                   # same seed => identical data
```

## Scenario: LLM drafts/qualifications are all fallbacks (M2)

Check `fallback_reason` on the response, or the latest runs:

```bash
sqlite3 var/flowpilot.db \
  "SELECT id, task, model, outcome, fallback_reason, latency_ms, error FROM llm_runs ORDER BY id DESC LIMIT 10"
```

| `fallback_reason` | Meaning | Fix |
|---|---|---|
| `disabled` | `FLOWPILOT_LLM_MODE=off` (the default) | Set `FLOWPILOT_LLM_MODE=ollama` in `.env`, restart the API |
| `unavailable` | Ollama not reachable / HTTP error | Start Ollama (`ollama serve` or the app); `flowpilot-admin llm-check` |
| `model_missing` | Model not installed | `ollama list`; choose an installed model in `FLOWPILOT_LLM_MODEL`. FlowPilot never downloads models |
| `timeout` | Generation exceeded `FLOWPILOT_LLM_TIMEOUT_S` | First call includes model load; raise the timeout (max 180) or close memory-heavy apps |
| `busy` | Another generation held the single slot > 5 s | Expected under concurrent load; retry later |
| `invalid_output` | Reply was not valid JSON for the schema | Inspect `llm_runs.raw_output`; consider another model or a prompt revision (bump `PROMPT_VERSION`) |
| `guard_rejected` (M2 p3+: after whitespace-only layout repair; see audit `layout_normalized`) | Draft/summary contained links, contacts, prices, placeholders, injection text, hidden characters, or budget references; the subject had a line break (`invalid_subject`); or the draft broke the required layout (`missing_paragraph_breaks`, `missing_greeting_line`, `missing_closing`, `paragraph_too_long`, `missing_signoff`) | `llm_runs.error` lists the violations; the template draft was used instead |
| `injection_suspected` | Lead text matched the pre-screen; the model was not called | Expected for hostile input; review the lead manually |

Fallbacks are always safe to keep: they are deterministic, pass the same guards, and still require
human approval (M4) before anything is sent.

## Scenario: turn the LLM off immediately

Set `FLOWPILOT_LLM_MODE=off` in `.env` and restart the API. All qualification and drafting
requests then use deterministic fallbacks; nothing else changes.

## n8n runtime (M3)

`compose.yaml` runs n8n plus two fixed-destination gateways. Design: ADR 0003. Workflows are
version-controlled in `n8n/workflows/`; see "Import / export a workflow" below.

### First start

1. In `.env` set `N8N_ENCRYPTION_KEY` (`openssl rand -hex 32`). **Store a copy outside the repo.**
   Compose refuses to start without it.
2. Start FastAPI on the host: `uv run flowpilot-api` (stays bound to `127.0.0.1:8000`).
3. `docker compose up -d`, then `docker compose ps`: all three services should be `healthy`.
4. Open **`http://localhost:5678`** (not `127.0.0.1`; the session cookie is scoped to the hostname)
   and create the local owner account with a strong password.

### Stop, start, inspect

```bash
docker compose down              # stops containers and removes the compose networks; data kept
docker compose up -d             # start again
docker compose logs -f n8n       # n8n logs
docker compose logs api-gateway  # every connection is logged with its (fixed) destination
```

`docker compose down -v` is not needed (no named volumes). Never delete `var/n8n` unless you
intend to lose all n8n state.

### Backup

Stop n8n first (`docker compose stop n8n`) so its SQLite database is consistent, then copy
`var/n8n/` **together with** `N8N_ENCRYPTION_KEY`. A backup without the key cannot decrypt the
stored credentials.

### Scenario: `N8N_ENCRYPTION_KEY` lost or changed

There is no recovery: every credential stored in n8n (e.g. the FlowPilot API key credential) is
unreadable. Workflows themselves survive. Fix: restore the original key from your backup. If it
is truly gone, stop n8n, move `var/n8n` aside, set a new key, start fresh, and re-create the
credentials, then re-import the workflows from `n8n/workflows/`.

### Scenario: rotate the FlowPilot API key

Follow "API key leaked or rotated" above, then update the n8n credential that holds `X-API-Key`.

### Import / export a workflow

Import (`flowpilot-prepare-next-lead`):

1. Create a **Header Auth** credential named exactly **`FlowPilot API`**: header name
   `X-API-Key`, value = `FLOWPILOT_API_KEY`. It must be the only credential with that name and
   type; n8n binds the workflow's HTTP nodes to it by name on save.
2. Import `n8n/workflows/flowpilot-prepare-next-lead.json` into a **new, empty** workflow.
   Importing into an existing canvas merges the nodes: n8n keeps the old manual trigger, drops
   the imported one and leaves the chain detached.
3. Save. Do not activate it. Check that no node shows a missing credential.

Export after a change (keep it inactive, unpin any test data first): use **⋯ → Download**
(documented by n8n, not verified in this project yet), or read the saved workflow back without
writing inside the container:

```bash
docker compose exec -T -e NODE_OPTIONS=--max-old-space-size=256 n8n \
  n8n export:workflow --all --pretty > /tmp/n8n-export.json   # outside the repo
```

The CLI output is an array and includes ownership metadata (`shared`, with the personal
project). Commit only the UI-download fields (`name`, `nodes`, `pinData`, `connections`,
`active`, `settings`, `versionId`, `meta`, `id`, `tags`), then run
`uv run pytest tests/test_n8n_workflows.py`. The export holds credential ids and names, never the
key. The heap cap matters: n8n was observed using up to about 515 MiB of its 1 GiB limit.

### Verifying the egress boundary

The boundary is the Docker network: n8n is attached only to the `internal` network and can reach
FastAPI solely through `api-gateway`. Re-verify after any Docker Desktop upgrade or compose
change:

```bash
docker compose exec n8n node -e "console.log(require('fs').readFileSync('/proc/net/route','utf8'))"
#   no 00000000 destination line => no default route
docker compose exec n8n node -e "fetch('http://host.docker.internal:8000/health').then(r=>console.log(r.status))"
#   200 (FastAPI must be running)
docker compose exec n8n node -e "fetch('https://example.com').then(r=>console.log('REACHED',r.status),e=>console.log('blocked',e.cause?.code||e.name))"
#   blocked
lsof -nP -iTCP:5678 -sTCP:LISTEN      # only 127.0.0.1:5678
```

If `host.docker.internal:8000/health` fails while FastAPI is up, Docker Desktop's host address may
have changed from `192.168.65.254`. Do **not** widen the network; re-verify the host address and
update `GATEWAY_UPSTREAM_HOST` deliberately (and its static test).

### Scenario: n8n UI login loops or the session is lost

Use `http://localhost:5678`. The cookie is `Secure; HttpOnly; SameSite=Lax` and bound to
`localhost`; using `127.0.0.1` gives a separate, unauthenticated cookie scope.
