# ADR 0005: Public portfolio demo deployment (host-independent)

Status: accepted, 2026-10-26. Implementation is host-independent; no host, cloud account or paid
service is chosen or required.

## Context

Everything ran on loopback (ADR 0001 decision 8, ADR 0003). A client-facing portfolio
demo must be reachable from the internet, without weakening the controls that keep real data and
real messages out: synthetic data only, human approval, a simulated-only outbox, n8n isolated.

## Decisions

1. **Only the console is public, behind TLS.** `deploy/public-demo/compose.yaml` runs four
   containers. Only `proxy` (nginx, unprivileged image) publishes ports (80 redirects to 443). The
   console and the API sit on internal-only networks; the API is reachable only from the console
   and is never routed by the proxy. n8n is not part of the public deployment and stays local.
2. **Public binding is an explicit, double opt-in that only exists for the demo.** Normal
   settings still reject any non-loopback host. `flowpilot-demo serve --public` binds `0.0.0.0`
   only when `FLOWPILOT_PUBLIC_DEMO=1` is also set, and only with the fixed demo database, LLM
   mode forced off and the `X-FlowPilot-Environment: demo` marker (`Settings.public_bind` is set
   by code, never read from the environment, and is refused unless `demo` is true). The console
   accepts a `0.0.0.0` bind and the private `api` host only under the same flag.
3. **Separate credentials.** Optional `FLOWPILOT_CONSOLE_API_KEY` and `FLOWPILOT_N8N_API_KEY`
   sit beside `FLOWPILOT_API_KEY`; each must be at least 32 characters and differ from the
   others, so one client's key can be rotated alone. The n8n key may only make the calls its
   workflows are validated for (`GET/POST /leads`, `POST /leads/{id}/qualification`,
   `POST /leads/{id}/drafts`); anything else is `403 forbidden`. Request logs name the client
   (`default`, `console`, `n8n`), never the key. In the public deployment the console holds only
   the console key.
4. **Rate limits at the proxy, by traffic class.** nginx answers `429` above: pages and
   Streamlit endpoints 10 requests/s per client address (burst 40, 20 concurrent); fingerprinted
   `/static/` assets 100/s (burst 400, 128 concurrent, because one page load fetches dozens of
   chunks over HTTP/2 and nginx counts every HTTP/2 stream as a connection); websocket opens
   2/s (burst 10, 20 concurrent). Each class has its own connection zone. It also adds HSTS,
   `nosniff`, `DENY` framing and `no-referrer`. Plain HTTP redirects to the operator-configured
   `FLOWPILOT_PUBLIC_ORIGIN` (default `https://FLOWPILOT_PUBLIC_HOST`), never to the client's
   `Host` header, so the redirect keeps a non-standard external port and cannot be redirected
   elsewhere.
5. **Scheduled demo refresh.** The API container runs `flowpilot-demo refresh` before serving,
   and `demo-refresh` repeats it every `FLOWPILOT_DEMO_REFRESH_SECONDS` (default 3600, minimum
   300). A refresh builds a complete synthetic dataset in a separate file (five scenarios, a
   template draft for each scored lead) and copies it into the live demo database with SQLite's
   online backup API, which is safe while the API serves. Visitor changes (added leads,
   approvals) disappear at the next refresh. Each scheduled round logs a JSON `demo_refresh`
   event, or `demo_refresh_failed` with the error type only, and the loop keeps running; a
   failure while building the snapshot leaves the previous data in place.
6. **A fixed demo reviewer token.** Visitors need a token to sign in, and a refresh must not
   invalidate it, so the operator supplies `FLOWPILOT_DEMO_REVIEWER_TOKEN` (`fpr_` + 40-128
   URL-safe characters) and shares it with prospects. It can approve or reject demo drafts only;
   approval still writes a simulated outbox entry and nothing is sent.
7. **Images.** One multi-stage `Dockerfile`: locked dependencies (`uv sync --frozen`, uv 0.13.0)
   are installed in a builder stage with uv's cache on a BuildKit cache mount, and only the
   virtual environment is copied into the `api` and `console` runtime stages (Python 3.12 slim,
   non-root user 10001, no uv, no cache, no source tree, no secrets). Local build: console
   818 MB (Streamlit's pandas/pyarrow/numpy dependencies are most of it), API 238 MB. Containers
   run read-only with all capabilities dropped and `no-new-privileges`.

## Consequences and limitations

- TLS certificates are the operator's: any CA (or a self-signed one for a trial) mounted from
  `FLOWPILOT_TLS_DIR`. Certificate issuance and renewal are not automated here.
- Base images are pinned by tag, not digest; pin digests when choosing a host.
- The shared demo token is public by design; anyone with it can act as the demo reviewer
  between refreshes. Never reuse it outside the demo.
- Verified locally (2026-10-26) on loopback ports 18080/18443 with a self-signed certificate:
  build, TLS, redirect, headers, rate limits, private API isolation, sign-in and review through
  the API, refresh while serving, and a scheduled refresh tick. Not deployed to any host.
- Reviewer tokens do not expire (ADR 0004). The demo token is rotated by changing
  `FLOWPILOT_DEMO_REVIEWER_TOKEN` and recreating the containers; expiry needs a schema change.
- `llm_runs.raw_output` has no retention limit; the public demo never writes it (model off).
- The model stays off in public; live-model results come from `flowpilot-demo eval` locally.
