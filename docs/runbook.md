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

## Logs

`flowpilot-api` (and `flowpilot-demo serve`) write one JSON object per line to stderr. Level:
`FLOWPILOT_LOG_LEVEL` (`DEBUG`, `INFO` default, `WARNING`, `ERROR`).

- `"event": "startup"`: version, host, port, `llm_mode`, `demo`.
- `"event": "request"`: `request_id` (same as the `X-Request-ID` response header), method, the
  matched route template (e.g. `/leads/{lead_id}`, or `unmatched`), status, `duration_ms`.
- `"event": "unhandled_error"` (level `ERROR`): the same request fields plus `error_type` and
  the code locations (`file:line:function`) of the failure. The client gets a generic 500.

Never logged: query strings, headers, request or response bodies, exception messages, the API
key, reviewer tokens, lead data. uvicorn's own access log is off (it would print raw paths and
query strings); its startup/shutdown lines remain plain text. To trace a client report, ask for
the `X-Request-ID` it received and search the logs for it, then use `GET /leads/{id}/audit` for
what happened to the lead.

## Public demo deployment (ADR 0005)

Host-independent; nothing here creates cloud resources. Only the proxy is reachable from the
internet; the API, its demo database and the console stay on internal networks; n8n is not part
of this deployment.

1. Prepare `deploy/public-demo/.env` (gitignored) from `deploy/public-demo/env.example`: the
   public host name and HTTP-redirect origin (`FLOWPILOT_PUBLIC_ORIGIN`, needed only when the
   external HTTPS port is not 443), a TLS directory with `fullchain.pem` and `privkey.pem`, three **different**
   secrets (`FLOWPILOT_API_KEY`, `FLOWPILOT_CONSOLE_API_KEY`, `FLOWPILOT_DEMO_REVIEWER_TOKEN`),
   and the refresh interval. For a private trial, a self-signed certificate works:

   ```bash
   mkdir -p deploy/public-demo/certs && openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
     -subj "/CN=localhost" -keyout deploy/public-demo/certs/privkey.pem \
     -out deploy/public-demo/certs/fullchain.pem
   ```

2. Validate, build and start (only when you decide to deploy):

   ```bash
   docker compose -f deploy/public-demo/compose.yaml --env-file deploy/public-demo/.env config --quiet
   docker compose -f deploy/public-demo/compose.yaml --env-file deploy/public-demo/.env up -d --build
   ```

3. Smoke test: `https://<host>/` shows the sign-in page with an **Explore the demo** button
   (ADR 0006); click it, or sign in with the demo reviewer token;
   Leads shows three leads and Draft Responses three drafts; an approval appears in Outbox
   (Simulated) as "not sent". `curl -sI http://<host>/` redirects to HTTPS; repeated fast
   requests get `429`. The API port is not published (`docker compose ... ps` shows ports only
   on `proxy`).
4. Operate: logs are JSON lines (`docker compose ... logs api`); the demo data is rebuilt every
   `FLOWPILOT_DEMO_REFRESH_SECONDS`, so visitor changes disappear (`docker compose ... logs
   demo-refresh` shows `demo_refresh` / `demo_refresh_failed` events). Rotate the console key or
   the reviewer token by editing `.env` and recreating the containers.
   Troubleshooting: a blank page with `429` on `/static/...` in the browser means the proxy's
   asset budget is too small for the Streamlit version (see the asset-burst test); an HTTP
   redirect to the wrong port means `FLOWPILOT_PUBLIC_ORIGIN` is missing or wrong.
5. Stop: `docker compose -f deploy/public-demo/compose.yaml down` (add `-v` to drop the demo
   data volume).

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

This rebuilds the **normal** database. For client demos, use the isolated environment below
instead: it never touches `var/flowpilot.db`.

## Scenario: portfolio demo environment (isolated)

`flowpilot-demo` runs the whole demo against its own database, `var/demo/flowpilot-demo.db`.
That path is fixed in code (no variable or `.env` value can redirect it), and every command
refuses to run if it is the configured `FLOWPILOT_DB_PATH`. The demo API forces LLM mode off,
so results are deterministic, and marks every response `X-FlowPilot-Environment: demo`.

1. Stop the normal API (the demo API needs port 8000, which n8n's `api-gateway` targets).
2. Reset and serve:

   ```bash
   uv run flowpilot-demo reset --yes   # recreates only the demo DB; prints the demo:reviewer token once
   uv run flowpilot-demo serve         # API on 127.0.0.1:8000 against the demo DB, LLM off
   ```

3. Prove the API on port 8000 is the demo server (expect `x-flowpilot-environment: demo`):

   ```bash
   curl -sI http://127.0.0.1:8000/health | grep -i x-flowpilot-environment
   ```

4. Submit the five scenarios (another terminal). `run` checks the marker first and writes
   nothing without it; it exits 1 if any outcome differs from the expected one:

   ```bash
   uv run flowpilot-demo run
   ```

   | Scenario | Expected |
   |---|---|
   | High-priority lead | `201`, lead #1, hot 98, `partner_review` |
   | Mid-priority lead | `201`, lead #2, warm 60, `sales_queue` |
   | Low-priority lead | `201`, lead #3, cold 22, `nurture` |
   | Same lead again (other casing/spacing) | `200`, duplicate of lead #1, nothing new stored |
   | Invalid input (non-synthetic email) | `422` on `contact_email`, nothing stored |

   Running it again without a reset replays the same requests and creates nothing new.

5. Console: `uv run --group ui streamlit run src/flowpilot/ui/app.py`, sign in with the demo
   reviewer token. Dashboard and Leads show the three leads.
6. Drafts through n8n (optional): check the marker (step 3) first, then `docker compose up -d`,
   open http://localhost:5678 and run `flowpilot-prepare-next-lead` once per lead; it drafts the
   oldest `scored` lead each time. Review the drafts on Draft Responses; an approval appears in
   Outbox (Simulated). Nothing is ever sent.
7. When done: stop the demo API (Ctrl+C) and start the normal one (`uv run flowpilot-api`).

### Synthetic LLM evaluation (M6, demo database only)

Measures the local Ollama model on synthetic leads: model success rate, fallback reasons,
latency and guard failures, for qualification and drafting. It is the only command that turns
the model on; `serve` and `run` stay deterministic with the model off.

```bash
uv run flowpilot-admin llm-check                       # no inference: Ollama reachable, model installed
uv run flowpilot-demo eval --yes                       # 3 scenarios + 10 generated leads
uv run flowpilot-demo eval --yes --count 20 --json /tmp/flowpilot-eval.json   # report outside the repo
```

- Order: path checks, then the no-inference model check, then a **reset of the demo database**.
  If Ollama or the model is missing it stops before the reset, so existing demo data is kept.
- It runs the API in-process against the demo database (no network server, so the normal API
  on port 8000 can keep running and never receives these requests) and checks the demo marker.
- Leads are synthetic (`.example`); nothing is approved and nothing reaches the outbox, which is
  verified at the end, also after an error.
- The report holds counts, guard names and timings only: no emails, lead messages, raw model
  output or secrets. Raw output stays in the demo database's `llm_runs`, as for any draft.
- Keep Ollama otherwise idle while it runs (one generation at a time; other users show up as
  `busy` fallbacks). The first call loads the model and is reported separately as warm-up.
- Results describe this machine, model and Ollama version; record `ollama list` alongside them.
- Write `--json` reports outside the repository (e.g. `/tmp/...`); it refuses to overwrite a file.
- The reset gives `demo:reviewer` a new token that `eval` does not print, so the console cannot
  sign in with an older one. To get a sign-in token afterwards, either keep the evaluation data
  and add another demo reviewer (names are unique and cannot be reused; the token prints once):

  ```bash
  uv run flowpilot-admin --db var/demo/flowpilot-demo.db reviewer add demo:reviewer-2
  ```

  or start a fresh demo, which deletes the evaluation leads: `uv run flowpilot-demo reset --yes`.

**Recorded result (reported 2026-10-25):** `qwen2.5:3b`, prompt version `2026.10-p3`,
13 synthetic leads (3 standard scenarios + 10 generated, seed 5150, the defaults).
Qualification 13/13 and drafts 13/13 came from the model (no fallbacks, no guard failures),
no layout repairs; approvals and outbox stayed empty. Latency p50 (successful model calls): qualification 2,676 ms; draft generation 3,247 ms.
The Ollama version was not recorded. One run on one machine with 13 leads is a small sample:
it shows the path works, not a measured success rate for other leads, machines or models.

### n8n webhook intake (`flowpilot-lead-intake`, demo database only)

Intake only: n8n forwards one JSON lead to `POST /leads`; the API validates, de-duplicates,
scores and audits it. Nothing is qualified, drafted, approved or sent.

1. Demo API running (`uv run flowpilot-demo serve`) and the marker check passes. **If
   `x-flowpilot-environment: demo` is missing, stop: do not send anything.**

   ```bash
   curl -sI http://127.0.0.1:8000/health | grep -i x-flowpilot-environment
   ```

2. `docker compose up -d`, open http://localhost:5678.
3. Once: create a **Header Auth** credential named exactly `FlowPilot webhook`, header name
   `X-Webhook-Token`, value from `openssl rand -hex 24` (keep it outside the repository). The
   existing `FlowPilot API` credential is reused.
4. Import `n8n/workflows/flowpilot-lead-intake.json` into a **new, empty** workflow, confirm both
   credentials are selected, save. Leave it inactive.
5. Send a lead. For one call, click **Execute workflow** and use the test URL
   (`/webhook-test/…`); to demo several calls, toggle the workflow **Active** and use `/webhook/…`:

   ```bash
   export FP_WEBHOOK_TOKEN='<the credential value>'   # in your shell only
   curl -s -X POST http://localhost:5678/webhook/flowpilot-lead-intake \
     -H "X-Webhook-Token: $FP_WEBHOOK_TOKEN" -H "Idempotency-Key: demo-hot-1" \
     -H "Content-Type: application/json" -d '{
       "company_name": "Harbor Crest Logistics", "contact_name": "Morgan Reyes",
       "contact_email": "morgan.reyes@harborcrest.demo.example", "company_size": "201-1000",
       "industry": "logistics", "service_interest": "process_automation",
       "budget_band": "over_100k", "timeline": "immediate", "source": "referral",
       "message": "We re-key orders from email into three systems and want to automate it."}'
   ```

   The body is exactly the `POST /leads` body. Use a new `Idempotency-Key` per submission and
   reuse it only when retrying the same one.

   | Situation | Status | Body |
   |---|---|---|
   | New lead | `201` | `{"result": "created", "lead_id": 1, "tier": "hot", "route": "partner_review", "score": 98, "replayed": false}` |
   | Same request retried (same key) | `201` | same, `"replayed": true`; nothing new stored |
   | Same lead, new key (other casing/spacing) | `200` | `{"result": "duplicate", "lead_id": 1, ...}` |
   | Non-synthetic email (e.g. `@gmail.com`) or bad field | `422` | `{"result": "invalid", "detail": [...]}` from the API |
   | No or malformed `Idempotency-Key` | `400` | `{"result": "rejected", ...}`; the API is not called |
   | Wrong or missing `X-Webhook-Token` | `403` | n8n's own response; nothing runs |
   | API down | `503` | `{"result": "error", ...}`; nothing confirmed |

6. Stop: toggle the workflow **Inactive** (the production URL then returns 404), then stop the
   demo API. Never point this workflow at the normal API.

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
