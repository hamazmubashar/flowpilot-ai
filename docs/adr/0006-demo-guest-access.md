# ADR 0006: One-click guest access to the public demo

Status: accepted, 2026-10-27. Amends ADR 0005 decision 6 (fixed demo reviewer token) and the rule
that the console holds only the console key.

## Context

ADR 0005 made the demo reviewer token public by design: the operator shares it with prospects,
and it can only approve or reject demo drafts, which only ever write a simulated outbox entry.
In practice, sharing it meant a visitor had to ask for the token before seeing anything. Most
portfolio visitors (recruiters, prospective clients) will not do that, so the deployed demo was
effectively closed to the people it was built for.

## Decisions

1. **The console offers "Explore the demo" in the public demo only.** When
   `FLOWPILOT_PUBLIC_DEMO=1` and `FLOWPILOT_DEMO_REVIEWER_TOKEN` is set, the sign-in page shows a
   primary button that signs the session in as the demo reviewer. Token sign-in stays available
   below it. Outside the public demo the button never appears, even if the variable is present.
2. **The token stays server-side.** The console reads it from its own environment, checks it with
   the same read-only `GET /reviewers/me` call as a typed sign-in, and keeps it only in the
   session's server-side state. It is never rendered, logged or sent to the browser; tests assert
   that it does not appear in the page.
3. **The console container receives the demo token, and nothing more.** `compose.yaml` passes
   `FLOWPILOT_DEMO_REVIEWER_TOKEN` to the console (required, never defaulted). The console still
   does not hold the admin API key or an n8n key.
4. **A failed guest check explains itself without detail.** If the API rejects the token (for
   example mid-refresh, or after the operator rotated it), the visitor is told the demo is
   refreshing and to try again; nothing about the token is shown.

## Consequences

- Anyone can now act as the demo reviewer without asking. This was already true for anyone who
  had the token; the blast radius is unchanged: demo database only, synthetic data, LLM off,
  simulated outbox, data rebuilt every `FLOWPILOT_DEMO_REFRESH_SECONDS`.
- Visitors share one reviewer identity, so they can see and undo each other's decisions until the
  next refresh. Acceptable for a demo; not a pattern for real reviewers.
- Rotating the demo token now means recreating the console as well as the API containers
  (`docker compose ... up -d` after editing `.env` does both).
- Rate limits at the proxy (ADR 0005 decision 4) remain the control against abuse.
