# ADR 0003: M3 n8n runtime with network-enforced egress

Status: accepted for the runtime (P1), 2026-10-21. Workflows are decided in later phases.

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
