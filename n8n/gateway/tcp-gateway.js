// Fixed-destination TCP gateway for the FlowPilot n8n runtime (M3).
// See docs/adr/0003-n8n-orchestration.md.
//
// Each gateway instance relays bytes to exactly one upstream, configured once at startup by the
// compose file. It parses nothing from client traffic, so a client cannot choose a destination:
// there is no CONNECT handling, no absolute-URI handling, and no Host-header routing. It is not a
// proxy, and the security boundary is the Docker network topology, not this script.
"use strict";

const net = require("node:net");

const LISTEN_PORT = Number(process.env.GATEWAY_LISTEN_PORT);
const UPSTREAM_HOST = process.env.GATEWAY_UPSTREAM_HOST;
const UPSTREAM_PORT = Number(process.env.GATEWAY_UPSTREAM_PORT);
const MAX_CONNECTIONS = 64;

if (!Number.isInteger(LISTEN_PORT) || !UPSTREAM_HOST || !Number.isInteger(UPSTREAM_PORT)) {
  console.error("gateway: GATEWAY_LISTEN_PORT, GATEWAY_UPSTREAM_HOST, GATEWAY_UPSTREAM_PORT required");
  process.exit(2);
}

const server = net.createServer((client) => {
  const upstream = net.connect({ host: UPSTREAM_HOST, port: UPSTREAM_PORT });
  console.log(`conn ${client.remoteAddress}:${client.remotePort} -> ${UPSTREAM_HOST}:${UPSTREAM_PORT}`);
  client.pipe(upstream).pipe(client);
  const close = () => {
    client.destroy();
    upstream.destroy();
  };
  client.on("error", close);
  client.on("close", close);
  upstream.on("error", (err) => {
    console.log(`upstream error ${err.code || err.message}`);
    close();
  });
  upstream.on("close", close);
});

server.maxConnections = MAX_CONNECTIONS;
server.listen(LISTEN_PORT, "0.0.0.0", () => {
  console.log(`gateway listening :${LISTEN_PORT} -> ${UPSTREAM_HOST}:${UPSTREAM_PORT} (fixed)`);
});
