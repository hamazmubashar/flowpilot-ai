# ADR 0004: M4 human approval with individually authenticated reviewers

Status: proposed, 2026-10-22. M4 (`0fcb2c6`) first used one shared reviewer key; M4.1 replaces it
with per-reviewer tokens (decision 1).

## Context

M1 created the guardrails in the schema: `approvals.actor_type = 'human'`, one decision per draft
(`draft_id UNIQUE`), immutable versioned drafts, and an outbox that accepts only `simulated` rows
for an approved approval of the exact draft. M4 adds the first code that writes them.

The API has one shared key, and n8n holds it (ADR 0003). An approval endpoint behind that key
alone would let n8n, or any key holder, approve by declaring a name in `X-Actor`. M4's first
version added a second shared key (`FLOWPILOT_REVIEWER_KEY`), which kept n8n out but still let
any key holder decide under any name.

## Decisions

1. **One token per reviewer (M4.1).** `flowpilot-admin reviewer add <name>` creates a reviewer
   and prints a random token (`fpr_` + 256 bits) once; only its SHA-256 is stored (a fast hash
   suffices for random tokens; no new dependency). `reviewer list` never shows tokens; `reviewer
   revoke` takes effect on the next request. Add and revoke are audited as `admin-cli`, without
   tokens or hashes. Reviewer management is an admin CLI task, like `migrate` and `restore`
   (ADR 0001 decision 10); there is no admin endpoint. `FLOWPILOT_REVIEWER_KEY` is removed.
2. **Identity from the database.** `POST /leads/{id}/drafts/{version}/approval` requires the API
   key and `X-Reviewer-Token`. The reviewer's name and id come from the matching active reviewer;
   `X-Actor` is ignored. A missing, unknown or revoked token returns `403 reviewer_required`.
3. **Enforced in the schema (migration 0003).** New approvals must carry `reviewer_id` of an
   active reviewer whose name equals `decided_by` (trigger). Reviewers cannot be deleted, renamed
   or re-keyed, and revocation is permanent. Existing approvals are not rewritten; they keep
   `reviewer_id` NULL.
4. **Exact version, once, atomically.** Only the latest version of a `drafted` or
   `awaiting_approval` lead can be decided, and only once. The reviewer's active status, the
   checks and all writes happen in one `BEGIN IMMEDIATE` transaction, so a revocation that lands
   after authentication still refuses the decision. `Idempotency-Key` replays a retried request;
   the reviewer is part of the request hash, so another reviewer reusing the key gets `422`.
5. **Simulated outbox on approval.** Approval moves the lead to `approved` and inserts one
   `pending` outbox row with the draft's exact subject, body and the lead's stored email. The
   schema forces `channel = 'simulated'`; nothing sends it. Rejection writes no outbox row.
6. **n8n never approves.** It holds no reviewer token, tokens are only printed to the admin's
   terminal, it cannot run the CLI, and the M3 workflow validator rejects any `approv…` path.

## Consequences and limitations

- Reviewer tokens are bearer tokens: no password, MFA or expiry. Whoever holds one acts as that
  reviewer until it is revoked. They travel over loopback HTTP only.
- A lost token cannot be recovered; the reviewer is revoked and re-added under a new name.
- Decisions are final; a rejected lead cannot be redrafted (drafting requires `scored`/`drafted`).
- The outbox is enqueue-only: no listing, no simulated "send", no retry.
- Nothing moves a lead to `awaiting_approval` yet; decisions are accepted from `drafted` too.
- A future Streamlit UI can send the reviewer's token from its server side; password login, if
  added, would issue tokens and not replace this check.
