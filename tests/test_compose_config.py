"""Static checks of the n8n runtime configuration (compose.yaml is JSON-syntax YAML)."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = json.loads((ROOT / "compose.yaml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
GATEWAY_JS = (ROOT / "n8n" / "gateway" / "tcp-gateway.js").read_text(encoding="utf-8")
DIGEST = "sha256:c72fa9f1bd9b59626deeb284824c0495625c3f6d894d395d27e241bf192d9900"
ALLOWED_NODES = {
    "n8n-nodes-base.webhook",
    "n8n-nodes-base.httpRequest",
    "n8n-nodes-base.if",
    "n8n-nodes-base.respondToWebhook",
    "n8n-nodes-base.scheduleTrigger",
    "n8n-nodes-base.errorTrigger",
    "n8n-nodes-base.noOp",
    "n8n-nodes-base.set",
    "n8n-nodes-base.manualTrigger",
}


def test_services_are_exactly_n8n_and_two_gateways() -> None:
    assert set(SERVICES) == {"n8n", "api-gateway", "ui-gateway"}


def test_all_images_pinned_to_verified_digest() -> None:
    for svc in SERVICES.values():
        assert svc["image"] == f"n8nio/n8n@{DIGEST}"


def test_only_ui_gateway_publishes_and_only_on_loopback() -> None:
    assert SERVICES["ui-gateway"]["ports"] == ["127.0.0.1:5678:5678"]
    assert "ports" not in SERVICES["n8n"]
    assert "ports" not in SERVICES["api-gateway"]


def test_network_membership() -> None:
    nets = COMPOSE["networks"]
    assert nets["n8n_internal"]["internal"] is True
    assert all(n.get("enable_ipv6") is False for n in nets.values())
    assert set(SERVICES["n8n"]["networks"]) == {"n8n_internal"}
    assert set(SERVICES["api-gateway"]["networks"]) == {"n8n_internal", "gateway_egress"}
    assert set(SERVICES["ui-gateway"]["networks"]) == {"n8n_internal", "ui_edge"}
    aliases = SERVICES["api-gateway"]["networks"]["n8n_internal"]["aliases"]
    assert aliases == ["host.docker.internal"]


def test_gateways_have_fixed_destinations() -> None:
    assert SERVICES["api-gateway"]["environment"] == {
        "GATEWAY_LISTEN_PORT": "8000",
        "GATEWAY_UPSTREAM_HOST": "192.168.65.254",
        "GATEWAY_UPSTREAM_PORT": "8000",
    }
    assert SERVICES["ui-gateway"]["environment"] == {
        "GATEWAY_LISTEN_PORT": "5678",
        "GATEWAY_UPSTREAM_HOST": "n8n",
        "GATEWAY_UPSTREAM_PORT": "5678",
    }


def test_gateway_script_is_not_a_proxy() -> None:
    assert GATEWAY_JS.count("net.connect(") == 1
    assert "net.connect({ host: UPSTREAM_HOST, port: UPSTREAM_PORT })" in GATEWAY_JS
    assert set(re.findall(r"process\.env\.(\w+)", GATEWAY_JS)) == {
        "GATEWAY_LISTEN_PORT",
        "GATEWAY_UPSTREAM_HOST",
        "GATEWAY_UPSTREAM_PORT",
    }
    for forbidden in ('on("data"', "on('data'", 'require("node:http', "require('node:http"):
        assert forbidden not in GATEWAY_JS


def test_n8n_hardening_settings() -> None:
    env = SERVICES["n8n"]["environment"]
    expected = {
        "N8N_DIAGNOSTICS_ENABLED": "false",
        "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
        "N8N_TEMPLATES_ENABLED": "false",
        "N8N_PERSONALIZATION_ENABLED": "false",
        "N8N_PUBLIC_API_DISABLED": "true",
        "N8N_PUBLIC_API_SWAGGERUI_DISABLED": "true",
        "N8N_DISABLED_MODULES": "mcp-registry",
        "EXECUTIONS_DATA_PRUNE": "true",
        "EXECUTIONS_DATA_MAX_AGE": "168",
        "N8N_CONCURRENCY_PRODUCTION_LIMIT": "1",
        "N8N_SECURE_COOKIE": "true",
    }
    assert {k: env.get(k) for k in expected} == expected
    assert set(json.loads(env["NODES_INCLUDE"])) == ALLOWED_NODES


def test_no_proxy_variables_relied_on() -> None:
    for svc in SERVICES.values():
        keys = {k.upper() for k in svc.get("environment", {})}
        assert not keys & {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY"}


def test_encryption_key_required_not_hardcoded() -> None:
    value = SERVICES["n8n"]["environment"]["N8N_ENCRYPTION_KEY"]
    assert re.fullmatch(r"\$\{N8N_ENCRYPTION_KEY:\?.+\}", value)


def test_no_hardcoded_secrets() -> None:
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8").replace(DIGEST, "") + GATEWAY_JS
    assert not re.search(r"\b[0-9a-fA-F]{32,}\b", text)
    assert not re.search(r"(?i)(api[_-]?key|password|secret|token)\"?\s*[:=]\s*\"[^$\"]{8,}", text)


def test_container_hardening() -> None:
    for name, svc in SERVICES.items():
        assert svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
        assert "mem_limit" in svc, name
        for bad in ("privileged", "network_mode", "extra_hosts", "dns"):
            assert bad not in svc, (name, bad)
        assert not any("docker.sock" in v for v in svc.get("volumes", [])), name
    for name in ("api-gateway", "ui-gateway"):
        assert SERVICES[name]["read_only"] is True
        assert SERVICES[name]["user"] == "node"
        assert all(v.endswith(":ro") for v in SERVICES[name]["volumes"])


def test_data_and_secrets_stay_out_of_git() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore
    assert "var/" in ignore
    assert SERVICES["n8n"]["volumes"] == ["${FLOWPILOT_N8N_DATA_DIR:-./var/n8n}:/home/node/.n8n"]
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nN8N_ENCRYPTION_KEY=\n" in example
    assert "\nFLOWPILOT_N8N_DATA_DIR=./var/n8n\n" in example
