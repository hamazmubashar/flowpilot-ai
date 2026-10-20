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

## Scenario: rebuild a clean demo database

```bash
uv run flowpilot-admin backup                 # optional: keep the old one
mv var/flowpilot.db var/flowpilot.db.old      # stop the API first; also move -wal/-shm if present
uv run flowpilot-admin migrate
uv run flowpilot-admin seed                   # same seed => identical data
```
