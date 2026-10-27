# ADR 0003: M3 n8n runtime with network-enforced egress

Status: accepted for the runtime (P1), 2026-10-21. P2 (first workflow) proposed, 2026-10-22:
built, statically validated and run once against a scratch database; see
[P2](#p2-first-workflow-proposed).

## Context

M3 adds local n8n as a thin orchestrator over the existing FastAPI endpoints. It must not
duplicate business logic, send external communication, or bypass human approval. The project is
zero-cost and local-only. FastAPI and Ollama stay bound to `127.0.0.1`.

The P0 spike found that n8n 2.44.4 contacts `api.n8n.io` and `telemetry.n8n.io` on its own. With
every telemetry flag off, its `mcp-registry` module still fetched a catalog from `api.n8n.io`.
Application settings therefore cannot be the egress control.

## Decisions

1. **Image pinned by digest:** `n8nio/n8n@sha256:c72fa9f1bd9b59626deeb284824c0495625c3f6d894d395d27e241bf192d9900`
   (n8n 2.44.4, arm64). It runs in Docker, because Node is not installed on the host.
2. **The egress boundary is the network.** `n8n` is attached only to `n8n_internal`
   (`internal: true`, IPv6 off). It has no default route and no external DNS.
3. **Two fixed-destination gateways** (`n8n/gateway/tcp-gateway.js`, about 40 lines, run from the
   same pinned image, so no extra download):
   - `api-gateway` is on `n8n_internal` with the alias `host.docker.internal`, and on
     `gateway_egress`. It relays to the **IP literal `192.168.65.254:8000`**, Docker Desktop's host
     address. An IP is used instead of a name because Compose attaches both networks at start,
     and resolving `host.docker.internal` there could return the gateway's own alias (a loop).
   - `ui-gateway` is on `n8n_internal` and `ui_edge`. It relays to `n8n:5678` and is the only
     published port: `127.0.0.1:5678`.
   - Each gateway's destination is set once from compose. It parses nothing from client traffic:
     no CONNECT handling, no absolute-URI or Host routing.
4. **No proxy variables.** `HTTP(S)_PROXY` / `NO_PROXY` are not used, and a static test forbids them.
5. **Hardening (defence in depth, not the boundary):**
   - telemetry, version notifications, templates, personalization and the hiring banner off;
   - public API and Swagger UI off; `N8N_DISABLED_MODULES=mcp-registry`; community packages and
     AI features off;
   - a `NODES_INCLUDE` allowlist of 9 nodes: webhook, httpRequest, if, respondToWebhook,
     scheduleTrigger, errorTrigger, noOp, set, manualTrigger;
   - executions pruned after 168 h; production concurrency 1 (the LLM handles one call at a
     time); secure cookies on;
   - every container: `cap_drop: ALL` and `no-new-privileges`, memory and PID limits. Gateways
     also run as `node` with a read-only root filesystem and the script mounted read-only.
6. **Secrets and state:** Compose refuses to start without `N8N_ENCRYPTION_KEY`. n8n state lives
   in `var/n8n`, which is gitignored. Losing the key makes stored credentials unrecoverable.
7. **`compose.yaml` is written in JSON syntax.** JSON is valid YAML, so Compose reads it natively,
   and the static tests parse it with stdlib `json`. This avoids adding a YAML dependency.

## Evidence

**P0 (feasibility):**
- Digest and version verified from the image label, the `n8n --version` CLI, and `package.json`.
- License is `LicenseRef-n8n-sustainable-use`, read from `LICENSE.md` at tag `n8n@2.44.4`.
- Every hardening variable verified through n8n's own configuration loader.
- Idle memory about 380–445 MiB.

**P0-B (isolated topology test):**
- 21 probe checks, identical with and without proxy variables.
- A real n8n workflow run through its own HTTP Request node.
- Factory-default n8n made 6 outbound attempts in 2 minutes; all failed at the network.

**P1 (this compose file, live):**
- 23/23 egress checks passed from inside the real n8n container: no default route; FastAPI
  reachable only through api-gateway (health 200, no key 401, with key 200); external HTTP/HTTPS,
  IPv4 and IPv6 literals, direct resolvers, the host IP directly, Ollama `:11434`, the Docker API
  and the metadata address all blocked; neither gateway can be used as a forward proxy
  (absolute-form and CONNECT requests).
- UI: reachable on `localhost` / `127.0.0.1` only; refused on the LAN address and `[::1]`. Owner
  setup and login work, and the cookie is `HttpOnly; Secure; SameSite=Lax`. Checked with curl
  8.7.1, not a browser.
- Public API effectively off: frontend settings report it disabled and `/rest/api-keys` returns
  404. Two `/api/v1/*` paths still return 401, but no key can be issued.
- Hardened n8n: 0 outbound attempts in about 2.5 minutes. Gateways connected only to their fixed
  destinations.
- Node allowlist enforced: a Code-node workflow failed with `Unrecognized node type`; an allowed
  workflow ran.
- Static tests (`tests/test_compose_config.py`) lock in: the digest; loopback-only ports;
  network membership; fixed destinations; the gateway script having no proxy logic; the
  hardening values; the node allowlist; no proxy variables; the required key; no hard-coded
  secrets; container hardening; gitignore coverage.

## Consequences and limitations

- Verified only on **Docker Desktop 29.8.2 for macOS**. Linux Docker or a different Desktop
  version (including a change to `192.168.65.254`) needs re-testing.
- **The gateways have outbound access.** Their safety rests on fixed-destination code and the
  pinned image. They are the single egress points.
- **Anything that reaches FastAPI can use every endpoint the API key allows.** FastAPI's own
  controls (API key, human-only approval, simulated-only outbox) remain the protection behind the
  gateway.
- n8n sees all UI clients as the ui-gateway's address, so any IP-based rate limiting or logging
  in n8n cannot distinguish clients.
- Gateway images are large (they reuse the 1.8 GB n8n image) in exchange for no extra download.
- Browser cookie behaviour on `http://localhost` is assumed from browser specifications. It was
  not tested in a real browser.
- Hardening flags cover the modules observed so far. Longer-running or post-login behaviour was
  not exhaustively observed, which is acceptable because the network blocks egress either way.

## P2: first workflow (proposed)

**Decisions**

1. **One manual-trigger workflow, `flowpilot-prepare-next-lead`**, exported to
   `n8n/workflows/` and kept inactive. It takes the lowest-id `scored` lead (`GET
   /leads?status=scored&limit=1`; the API orders by id), requests an advisory qualification and a
   reply draft, and stops before approval. Nodes used: manualTrigger, httpRequest, if, set, noOp.
2. **n8n holds no business rules.** Its only decision is whether the queue is empty
   (`total == 0`). Qualification, drafting, status transitions and audit stay in FastAPI.
3. **Per-lead idempotency keys** `n8n:qualify:lead:<id>:v1` and `n8n:draft:lead:<id>:v1`, so a node
   retry, a rerun or an overlapping execution replays instead of duplicating. The POSTs carry no
   body; the routes read only the path and headers.
4. **Audit attribution:** `X-Actor: n8n:prepare-next-lead` and `X-Request-ID: n8n-<execution id>`.
5. **Credential by name.** All HTTP nodes use the Header Auth credential `FlowPilot API`
   (`X-API-Key`). On save, n8n 2.44.4 (`replaceInvalidCredentials`) binds a missing or unknown id
   to the single credential with that name and type in the project (read from n8n's source).

**Evidence (static only)**

- Node versions (HTTP Request 4.5, If 2.2, Set 3.4) and the HTTP Request field names checked
  against the pinned image's `n8n-nodes-base` code and a real export.
- `tests/test_workflow_contract.py` replays the call sequence against FastAPI: lowest-id
  selection, retries and overlapping runs without duplicates, resume after interruption, 409 on a
  changed status, and no approval or outbox rows.
- `tests/test_n8n_workflows.py` validates the committed export: allowlisted nodes, fixed api-gateway
  URL, approved method/path pairs, idempotency keys on POSTs, a named `httpHeaderAuth` credential
  reference, no literal key, inactive, no `pinData`, and every node reachable from a trigger.
- The saved workflow was read back with `n8n export:workflow` (stdout only): all three HTTP nodes
  carry the same real credential id.

**Runtime test (2026-10-22, one manual execution, scratch database, `FLOWPILOT_LLM_MODE=off`):**
an isolated API on a disposable SQLite file seeded with one synthetic `scored` lead (`seed --count
1 --seed 3202`); the real database was not used. Execute step on `Next scored lead` returned that
single lead and changed nothing. The full run then reached `Awaiting human approval`: the lead
became `drafted`; 1 assessment (`rules`) and 1 draft (version 1, `template`); keys
`n8n:qualify:lead:1:v1` and `n8n:draft:lead:1:v1`; `lead.qualified` and `lead.drafted` audited as
`n8n:prepare-next-lead` with request id `n8n-3`; 2 `llm_runs` rows (`off`, fallback `disabled`);
approvals and outbox empty. The workflow stayed inactive with no pinned data.

**Not yet verified:** the LLM path (`ollama` mode) through n8n; a rerun and the empty-queue
branch; runtime error handling (e.g. a 409 failing the run) and the 200 s timeouts. Importing
the committed export on a fresh instance (credential bound by name on save) was checked against
n8n's source code only, not by an actual fresh import.

**Lessons:** importing into an existing workflow merges canvases; n8n kept the old manual trigger
and dropped the imported one, leaving the chain detached. The reachability check was added to
catch exactly that. A CLI export also includes ownership metadata (`shared`, with the personal
project), which is stripped to the UI-download fields before committing.

## Addendum (2026-10-22): webhook lead intake (portfolio demo, Step C)

**Decision.** A second, separate workflow, `flowpilot-lead-intake`, accepts synthetic lead
submissions and sends each to `POST /leads`. `flowpilot-prepare-next-lead` is unchanged and still
manual-only.

- Trigger: exactly one webhook, `POST /webhook/flowpilot-lead-intake` (loopback only, through
  `ui-gateway` on 127.0.0.1:5678), with header authentication from an n8n `httpHeaderAuth`
  credential named `FlowPilot webhook`. It answers through Respond to Webhook nodes.
- Calls: only `POST /leads` through `api-gateway` with the existing `FlowPilot API` credential.
  The caller must send an `Idempotency-Key`; n8n forwards it as `n8n:intake:<key>`, so a retried
  call replays the original result instead of creating a lead. No qualification, drafting,
  approval or outbox access; `X-Actor: n8n:lead-intake`.
- Responses mirror the API: `201` created, `200` duplicate, `422` invalid (the API's own field
  errors), `400` without a key (nothing called), `502` for any other API status, `503` when the
  API is unreachable. API errors never become a success response, and HTTP nodes may not retry.
- Validation (`tests/test_n8n_workflows.py`): each committed export now has its own policy.
  The prepare workflow keeps its three calls and the manual-trigger rule. The intake workflow may
  only `POST /leads` from one authenticated POST webhook. An export without a policy fails, and
  `retryOnFail` is rejected for every workflow. Approval and outbox paths stay forbidden.
- Execution only against the isolated demo database (`flowpilot-demo serve`, after checking
  `X-FlowPilot-Environment: demo`); the export is committed inactive.

**Not yet verified at runtime:** import into n8n 2.44.4 and a live run (node versions:
webhook 2, Respond to Webhook 1.1, IF 2.2, HTTP Request 4.5).

## Addendum (2026-10-27): live n8n in the public demo (M7)

**Decision.** For the public portfolio demo, n8n runs on the server (`deploy/public-demo/
compose.n8n.yaml`) with the same pinned image, node allow-list and hardening as the local runtime.
Its editor is served by the shared Caddy at its own HTTPS name behind n8n's owner login and 2FA;
every webhook and form path returns 404 publicly, and n8n's networks have no internet egress.

- **CSV import:** the console's Import CSV page sends each row to `flowpilot-lead-intake` inside
  Docker (one execution per row, idempotency key per file and row).
- **Scheduled preparation:** `flowpilot-prepare-next-lead-scheduled` is a copy of the manual
  workflow that differs only in its trigger: one schedule, at most every 5 minutes, exported
  inactive. `flowpilot-prepare-next-lead` itself stays manual-only. Drafts still wait for a human
  reviewer; nothing is approved or sent.
- **Why a copy, not a change:** the manual workflow's rule (a person starts every run) still holds
  for local use and its tests; the demo's automation is a separate, separately reviewed policy.
