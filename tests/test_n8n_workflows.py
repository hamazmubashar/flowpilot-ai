"""Static checks of exported n8n workflows (M3-P2). Standard-library JSON only.

Every `n8n/workflows/*.json` export must use only the Compose node allowlist, call FastAPI only
through the api-gateway on approved lead endpoints, send an `Idempotency-Key` on every POST,
authenticate through a stored credential, and never touch approvals or the outbox. Every node must
be reachable from a trigger. Exports must be committed inactive and without pinned execution data.

The rules are proven against inline fixtures below and applied to every committed export.

Verified from this repository: node type IDs (compose `NODES_INCLUDE`) and the api-gateway base
URL (compose alias, ADR 0003 P1 evidence). Verified against the real n8n 2.44.4 export of
`flowpilot-prepare-next-lead`: the HTTP Request field names (`method` omitted when GET,
`authentication`, `genericAuthType`, `sendHeaders`, `headerParameters`, `queryParameters`,
`credentials`; `specifyHeaders` is omitted at its `keypair` default) and the workflow-level
`active` and `pinData` keys, whose presence the export test also asserts. URL `{{ }}` expressions
are checked only as literal templates (each must stay within one path segment); their runtime
values are not evaluated.

A credential reference must name an `httpHeaderAuth` credential. Its `id` may be null in an
unbound import template: on save, n8n 2.44.4 (`replaceInvalidCredentials` in
`dist/workflow-helpers.js`) binds a missing or unknown id to the single credential of that name and
type in the project. A committed export must carry the bound id; the export test asserts it.
"""

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = json.loads((ROOT / "compose.yaml").read_text(encoding="utf-8"))
ALLOWED_NODES = frozenset(json.loads(COMPOSE["services"]["n8n"]["environment"]["NODES_INCLUDE"]))
WORKFLOW_FILES = sorted((ROOT / "n8n" / "workflows").glob("*.json"))

HTTP_NODE = "n8n-nodes-base.httpRequest"
TRIGGER_NODES = frozenset(
    f"n8n-nodes-base.{t}" for t in ("manualTrigger", "scheduleTrigger", "errorTrigger", "webhook")
)
API_BASE = "http://host.docker.internal:8000/"
EXPRESSION = re.compile(r"\{\{.*?\}\}")
ALLOWED_CALLS = {
    ("GET", "leads"),
    ("POST", "leads/{expr}/qualification"),
    ("POST", "leads/{expr}/drafts"),
}
FORBIDDEN_TERMS = ("approv", "outbox")
SECRET_PARAMETER_NAMES = {"x-api-key", "authorization"}


def _pairs(parameters: dict, field: str) -> list[dict]:
    return (parameters.get(field) or {}).get("parameters") or []


def _http_violations(node: dict) -> list[str]:
    name = node.get("name", "?")
    params = node.get("parameters") or {}
    found = []

    method = str(params.get("method", "GET")).upper()
    raw_url = str(params.get("url", ""))
    url = raw_url.removeprefix("=")  # a leading '=' marks an n8n expression
    if not url.startswith(API_BASE):
        found.append(f"{name}: url must start with {API_BASE}")
    else:
        path = url.removeprefix(API_BASE).split("?", 1)[0].split("#", 1)[0]
        if any(c in m for m in EXPRESSION.findall(path) for c in "/?#"):
            found.append(f"{name}: path expressions may not contain '/', '?' or '#'")
        template = EXPRESSION.sub("{expr}", path)
        if (method, template) not in ALLOWED_CALLS:
            found.append(f"{name}: {method} /{template} is not an approved call")
        # Only the path selects the action; query values such as `status=awaiting_approval`,
        # header values, node names and notes are harmless wording.
        for term in FORBIDDEN_TERMS:
            if term in path.lower():
                found.append(f"{name}: path references {term!r} (approval/outbox actions)")

    if params.get("specifyHeaders", "keypair") != "keypair":
        found.append(f"{name}: headers must be name/value pairs")
    headers = {
        str(h.get("name", "")).lower(): h.get("value") for h in _pairs(params, "headerParameters")
    }
    if method == "POST" and not (params.get("sendHeaders") and headers.get("idempotency-key")):
        found.append(f"{name}: POST must send an Idempotency-Key header")

    queries = {str(q.get("name", "")).lower() for q in _pairs(params, "queryParameters")}
    if (set(headers) | queries) & SECRET_PARAMETER_NAMES:
        found.append(f"{name}: literal credential in headers or query; use a stored credential")
    credential = (node.get("credentials") or {}).get("httpHeaderAuth") or {}
    credential_id = credential.get("id")
    if (
        params.get("authentication") != "genericCredentialType"
        or params.get("genericAuthType") != "httpHeaderAuth"
        or not credential.get("name")
        or not (credential_id is None or (isinstance(credential_id, str) and credential_id))
    ):
        found.append(f"{name}: must authenticate with an httpHeaderAuth credential reference")
    return found


def workflow_violations(workflow: dict) -> list[str]:
    found = []
    if workflow.get("active", False) is not False:
        found.append("workflow must be exported inactive")
    if workflow.get("pinData"):
        found.append("workflow must not contain pinned execution data (pinData)")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return [*found, "workflow has no nodes"]
    for node in nodes:
        node_type = node.get("type")
        if node_type not in ALLOWED_NODES:
            found.append(f"{node.get('name', '?')}: node type {node_type!r} is not allowlisted")
        elif node_type == HTTP_NODE:
            found.extend(_http_violations(node))
    found.extend(_reachability_violations(workflow))
    return found


def _reachability_violations(workflow: dict) -> list[str]:
    """Every node must run from a trigger; an orphaned chain (e.g. after an import that merged
    into an existing canvas) would pass the per-node checks yet never execute."""
    names = {node.get("name") for node in workflow["nodes"]}
    reached = {n.get("name") for n in workflow["nodes"] if n.get("type") in TRIGGER_NODES}
    if not reached:
        return ["workflow has no trigger node"]
    connections = workflow.get("connections") or {}
    pending = list(reached)
    while pending:
        for output in (connections.get(pending.pop()) or {}).get("main") or []:
            for link in output or []:
                target = link.get("node")
                if target in names and target not in reached:
                    reached.add(target)
                    pending.append(target)
    return [f"{name}: not connected to a trigger" for name in sorted(names - reached)]


# --- fixtures --------------------------------------------------------------------------------

CREDENTIAL = {"httpHeaderAuth": {"id": "cred-placeholder", "name": "FlowPilot API"}}
LEAD_ID = "{{ $json.lead_id }}"


def _http(name: str, url: str, method: str | None = None, key: str | None = None) -> dict:
    params: dict[str, Any] = {
        "url": url,
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "options": {"timeout": 200000},
    }
    if method:
        params["method"] = method
    if key:
        params["sendHeaders"] = True
        params["headerParameters"] = {"parameters": [{"name": "Idempotency-Key", "value": key}]}
    return {"name": name, "type": HTTP_NODE, "parameters": params, "credentials": CREDENTIAL}


def _to(*targets: str) -> dict:
    """n8n `connections` entry: one main output per target, in output order."""
    return {"main": [[{"node": t, "type": "main", "index": 0}] for t in targets]}


def valid_workflow() -> dict:
    """Shape of the planned "prepare next scored lead" workflow (illustrative, not an export)."""
    return {
        "name": "flowpilot-prepare-next-lead",
        "active": False,
        "pinData": {},
        "nodes": [
            {"name": "Start", "type": "n8n-nodes-base.manualTrigger", "parameters": {}},
            _http("Next scored lead", f"={API_BASE}leads?status=scored&limit=1"),
            {"name": "Queue empty?", "type": "n8n-nodes-base.if", "parameters": {}},
            {"name": "Pick lead", "type": "n8n-nodes-base.set", "parameters": {}},
            _http(
                "Qualify",
                f"={API_BASE}leads/{LEAD_ID}/qualification",
                "POST",
                f"=n8n:qualify:lead:{LEAD_ID}:v1",
            ),
            _http(
                "Draft",
                f"={API_BASE}leads/{LEAD_ID}/drafts",
                "POST",
                f"=n8n:draft:lead:{LEAD_ID}:v1",
            ),
            {"name": "Done", "type": "n8n-nodes-base.noOp", "parameters": {}},
        ],
        "connections": {
            "Start": _to("Next scored lead"),
            "Next scored lead": _to("Queue empty?"),
            "Queue empty?": _to("Done", "Pick lead"),
            "Pick lead": _to("Qualify"),
            "Qualify": _to("Draft"),
            "Draft": _to("Done"),
        },
    }


def _node(workflow: dict, name: str) -> dict:
    return next(n for n in workflow["nodes"] if n["name"] == name)


def _set_url(name: str, url: str):
    def mutate(wf: dict) -> None:
        _node(wf, name)["parameters"]["url"] = url

    return mutate


def _drop_key(wf: dict) -> None:
    params = _node(wf, "Draft")["parameters"]
    params.pop("sendHeaders")
    params.pop("headerParameters")


def _literal_api_key(wf: dict) -> None:
    params = _node(wf, "Qualify")["parameters"]
    params["headerParameters"]["parameters"].append({"name": "X-API-Key", "value": "not-a-key"})


def _literal_query_key(wf: dict) -> None:
    params = _node(wf, "Next scored lead")["parameters"]
    params["sendQuery"] = True
    params["queryParameters"] = {"parameters": [{"name": "x-api-key", "value": "not-a-key"}]}


def _no_credential(wf: dict) -> None:
    del _node(wf, "Draft")["credentials"]


def _no_auth(wf: dict) -> None:
    _node(wf, "Qualify")["parameters"]["authentication"] = "none"


def _custom_auth(wf: dict) -> None:
    node = _node(wf, "Next scored lead")
    node["parameters"]["genericAuthType"] = "httpCustomAuth"
    node["credentials"] = {"httpCustomAuth": {"id": "cred-placeholder", "name": "Custom Auth"}}


def _unnamed_credential(wf: dict) -> None:
    _node(wf, "Draft")["credentials"] = {"httpHeaderAuth": {"id": None, "name": ""}}


def _json_headers(wf: dict) -> None:
    _node(wf, "Draft")["parameters"]["specifyHeaders"] = "json"


def _code_node(wf: dict) -> None:
    wf["nodes"].append({"name": "Script", "type": "n8n-nodes-base.code", "parameters": {}})


def _delete_method(wf: dict) -> None:
    _node(wf, "Draft")["parameters"]["method"] = "DELETE"


def _get_on_post_path(wf: dict) -> None:
    del _node(wf, "Draft")["parameters"]["method"]


def _active(wf: dict) -> None:
    wf["active"] = True


def _pinned(wf: dict) -> None:
    wf["pinData"] = {"Next scored lead": [{"json": {"total": 0}}]}


def _no_nodes(wf: dict) -> None:
    wf["nodes"] = []


def _orphaned_chain(wf: dict) -> None:
    """The real failed import: a leftover trigger and GET, with the planned chain detached."""
    wf["nodes"].append(_http("HTTP Request", f"{API_BASE}leads"))
    wf["connections"]["Start"] = _to("HTTP Request")


def _no_trigger(wf: dict) -> None:
    wf["nodes"] = [n for n in wf["nodes"] if n["name"] != "Start"]


INVALID = {
    "disallowed node type": (_code_node, "is not allowlisted"),
    "external host": (_set_url("Qualify", "https://example.com/leads/1/qualification"), "url must"),
    "host via expression": (_set_url("Qualify", "={{ $json.url }}"), "url must"),
    "direct host address": (_set_url("Draft", "http://192.168.65.254:8000/leads/1/drafts"), "url"),
    "ollama port": (_set_url("Draft", "http://host.docker.internal:11434/api/chat"), "url must"),
    "unapproved path": (_set_url("Draft", f"={API_BASE}leads/{LEAD_ID}/audit"), "not an approved"),
    "approval path": (
        _set_url("Draft", f"={API_BASE}leads/{LEAD_ID}/approvals"),
        "approval/outbox",
    ),
    "outbox path": (_set_url("Draft", f"={API_BASE}outbox"), "approval/outbox"),
    "path expression with slash": (
        _set_url("Draft", f"={API_BASE}leads/{{{{ $json.id + '/x' }}}}/drafts"),
        "may not contain",
    ),
    "disallowed method": (_delete_method, "not an approved"),
    "GET on a POST-only path": (_get_on_post_path, "not an approved"),
    "POST without Idempotency-Key": (_drop_key, "Idempotency-Key"),
    "headers as raw JSON": (_json_headers, "name/value pairs"),
    "literal X-API-Key header": (_literal_api_key, "literal credential"),
    "literal key in query": (_literal_query_key, "literal credential"),
    "missing credential reference": (_no_credential, "credential reference"),
    "authentication disabled": (_no_auth, "credential reference"),
    "custom auth credential": (_custom_auth, "credential reference"),
    "unnamed credential": (_unnamed_credential, "credential reference"),
    "active workflow": (_active, "inactive"),
    "pinned execution data": (_pinned, "pinData"),
    "no nodes": (_no_nodes, "no nodes"),
    "chain detached from trigger": (_orphaned_chain, "Next scored lead: not connected"),
    "no trigger": (_no_trigger, "no trigger node"),
}


# --- tests -----------------------------------------------------------------------------------


def test_allowlist_is_read_from_compose() -> None:
    assert len(ALLOWED_NODES) == 9
    assert HTTP_NODE in ALLOWED_NODES
    assert "n8n-nodes-base.code" not in ALLOWED_NODES


def test_valid_fixture_passes() -> None:
    assert workflow_violations(valid_workflow()) == []


def test_harmless_approval_wording_is_allowed() -> None:
    workflow = valid_workflow()
    workflow["nodes"].append(
        {
            "name": "Awaiting human approval",
            "type": "n8n-nodes-base.noOp",
            "parameters": {},
            "notes": "Approval and the simulated outbox are handled by humans in M4.",
        }
    )
    workflow["connections"]["Done"] = _to("Awaiting human approval")
    _node(workflow, "Next scored lead")["parameters"]["url"] = (
        f"={API_BASE}leads?status=awaiting_approval&limit=1"
    )
    qualify = _node(workflow, "Qualify")
    qualify["notes"] = "Advisory only; never approves or writes to the outbox."
    qualify["parameters"]["headerParameters"]["parameters"].append(
        {"name": "X-Actor", "value": "n8n:pre-approval-prep"}
    )
    assert workflow_violations(workflow) == []


def test_unbound_named_credential_is_allowed() -> None:
    workflow = valid_workflow()
    for node in workflow["nodes"]:
        if node["type"] == HTTP_NODE:
            node["credentials"] = {"httpHeaderAuth": {"id": None, "name": "FlowPilot API"}}
    assert workflow_violations(workflow) == []


@pytest.mark.parametrize("case", INVALID, ids=list(INVALID))
def test_invalid_fixture_is_rejected(case: str) -> None:
    mutate, expected = INVALID[case]
    workflow = copy.deepcopy(valid_workflow())
    mutate(workflow)
    violations = workflow_violations(workflow)
    assert any(expected in v for v in violations), violations


def test_prepare_next_lead_export_is_committed() -> None:
    assert ROOT / "n8n" / "workflows" / "flowpilot-prepare-next-lead.json" in WORKFLOW_FILES


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_exported_workflow_is_valid(path: Path) -> None:
    workflow = json.loads(path.read_text(encoding="utf-8"))
    assert {"active", "pinData"} <= workflow.keys()  # a renamed key would skip those checks
    assert workflow_violations(workflow) == []
    unbound = [
        n["name"]
        for n in workflow["nodes"]
        if n.get("type") == HTTP_NODE and not n["credentials"]["httpHeaderAuth"].get("id")
    ]
    assert unbound == [], f"committed exports must carry bound credential ids: {unbound}"


# --- flowpilot-prepare-next-lead: manual trigger only ----------------------------------------

PREPARE_NEXT_LEAD = ROOT / "n8n" / "workflows" / "flowpilot-prepare-next-lead.json"
MANUAL_TRIGGER = "n8n-nodes-base.manualTrigger"


def _is_trigger(node_type: str) -> bool:
    """Allowlisted triggers plus anything named like one, so a trigger type that is not on
    today's allowlist (e.g. `formTrigger`, legacy `cron`) is still caught if it is added later."""
    short = node_type.rsplit(".", 1)[-1].lower()
    return (
        node_type in TRIGGER_NODES
        or short.endswith("trigger")
        or short in {"webhook", "cron", "interval"}
    )


def manual_trigger_violations(workflow: dict) -> list[str]:
    """This workflow may start only when a person clicks Execute in n8n: never on a schedule,
    an inbound request, or another workflow's error. Disabled triggers count too."""
    triggers = [n for n in workflow.get("nodes") or [] if _is_trigger(str(n.get("type", "")))]
    found = [
        f"{n.get('name', '?')}: trigger {n.get('type')!r} is not manualTrigger"
        for n in triggers
        if n.get("type") != MANUAL_TRIGGER
    ]
    if not any(n.get("type") == MANUAL_TRIGGER for n in triggers):
        found.append("workflow must have a manualTrigger node")
    return found


AUTOMATIC_TRIGGERS = [
    "n8n-nodes-base.scheduleTrigger",
    "n8n-nodes-base.webhook",
    "n8n-nodes-base.errorTrigger",
    "n8n-nodes-base.formTrigger",
    "n8n-nodes-base.cron",
]


def test_manual_trigger_fixture_passes() -> None:
    assert manual_trigger_violations(valid_workflow()) == []


@pytest.mark.parametrize("trigger_type", AUTOMATIC_TRIGGERS)
def test_automatic_trigger_substitution_is_rejected(trigger_type: str) -> None:
    workflow = valid_workflow()
    _node(workflow, "Start")["type"] = trigger_type
    violations = manual_trigger_violations(workflow)
    assert f"Start: trigger {trigger_type!r} is not manualTrigger" in violations
    assert "workflow must have a manualTrigger node" in violations


@pytest.mark.parametrize("trigger_type", AUTOMATIC_TRIGGERS)
def test_automatic_trigger_beside_manual_is_rejected(trigger_type: str) -> None:
    workflow = valid_workflow()
    workflow["nodes"].append(
        {"name": "Also", "type": trigger_type, "parameters": {}, "disabled": True}
    )
    workflow["connections"]["Also"] = _to("Next scored lead")
    assert manual_trigger_violations(workflow) == [
        f"Also: trigger {trigger_type!r} is not manualTrigger"
    ]


def test_prepare_next_lead_export_uses_manual_trigger_only() -> None:
    workflow = json.loads(PREPARE_NEXT_LEAD.read_text(encoding="utf-8"))
    assert manual_trigger_violations(workflow) == []
