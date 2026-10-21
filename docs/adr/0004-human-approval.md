# ADR 0004: M4 human approval with a separate reviewer key

Status: proposed, 2026-10-22.

## Context

M1 created the guardrails in the schema: `approvals.actor_type = 'human'`, one decision per draft
(`draft_id UNIQUE`), immutable versioned drafts, and an outbox that accepts only `simulated` rows
for an approved approval of the exact draft. M4 adds the first code that writes them.

The API has one shared key, and n8n holds it (ADR 0003). An approval endpoint behind that key
alone would let n8n, or any key holder, approve by declaring a name in `X-Actor`; the schema's
`actor_type = 'human'` cannot tell a person from a script.

## Decisions

1. **Separate reviewer key.** `POST /leads/{id}/drafts/{version}/approval` requires the API key
   and `X-Reviewer-Key` matching `FLOWPILOT_REVIEWER_KEY` (≥ 32 chars, must differ from the API
   key, constant-time compare). It is optional: unset returns `503 review_disabled`, so existing
   setups keep working. It is never given to n8n; the M3 workflow validator also rejects any n8n
   call to an `approv…` path.
2. **Reviewer name required.** `X-Actor` is mandatory for decisions and stored as `decided_by`.
3. **Exact version, once.** The path names the draft version. Only the latest version of a
   `drafted` or `awaiting_approval` lead can be decided, and only once; everything is checked and
   written in one `BEGIN IMMEDIATE` transaction. `Idempotency-Key` replays a retried request.
4. **Simulated outbox on approval.** Approval moves the lead to `approved` and inserts one
   `pending` outbox row with the draft's exact subject, body and the lead's stored email. The
   schema forces `channel = 'simulated'`; nothing sends it. Rejection writes no outbox row.
5. **No migration.** The M1 schema already holds everything M4 needs.

## Consequences and limitations

- The reviewer key is shared: anyone holding it can decide under any `X-Actor` name. Per-user
  identity is not implemented.
- Decisions are final; a rejected lead cannot be redrafted (drafting requires `scored`/`drafted`).
- The outbox is enqueue-only: no listing, no simulated "send", no retry.
- Nothing moves a lead to `awaiting_approval` yet; decisions are accepted from `drafted` too.
