"""ADR 0005 public demo: explicit opt-in boundaries, separate keys, refresh, deployment files.

No container is built or started here; deployment files are checked statically.
"""

import json
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowpilot import demo
from flowpilot.api.app import DEMO_HEADER, create_app
from flowpilot.db.connection import connect
from flowpilot.db.migrate import migrate
from flowpilot.logs import LOGGER
from flowpilot.services.reviewers import authenticate
from flowpilot.settings import LLMConfig, Settings, SettingsError, load_settings
from flowpilot.ui.api import UIConfig, load_ui_config
from tests.conftest import API_KEY

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "public-demo"
CONSOLE_KEY = "console-key-0123456789abcdefghijklmnop"
N8N_KEY = "n8n-key-0123456789abcdefghijklmnopqrstu"
TOKEN = "fpr_" + "PublicDemoReviewerToken_" * 2
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - the bind these tests check is refused or allowed


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    demo_db = tmp_path / "var" / "demo" / "flowpilot-demo.db"
    normal_db = tmp_path / "var" / "flowpilot.db"
    normal_db.parent.mkdir(parents=True)
    conn = connect(normal_db)
    try:
        migrate(conn)
    finally:
        conn.close()
    monkeypatch.setattr(demo, "DEMO_DB", demo_db)
    return {"demo": demo_db, "normal": normal_db}


def base(normal: Path, **changes) -> Settings:
    return Settings(api_key=API_KEY, db_path=normal, backup_dir=normal.parent, **changes)


def count(db: Path, table: str) -> int:
    conn = connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    finally:
        conn.close()


# --- opt-in boundaries ----------------------------------------------------------------------------


def test_normal_settings_still_reject_public_binds(tmp_path: Path) -> None:
    env = {
        "FLOWPILOT_API_KEY": API_KEY,
        "FLOWPILOT_DB_PATH": str(tmp_path / "x.db"),
        "FLOWPILOT_HOST": ALL_INTERFACES,
        "FLOWPILOT_PUBLIC_DEMO": "1",  # the flag alone never unlocks the normal API
    }
    with pytest.raises(SettingsError, match="not a loopback address"):
        load_settings(env, env_file=None)
    with pytest.raises(SettingsError, match="only allowed for the isolated demo"):
        base(tmp_path / "x.db", host=ALL_INTERFACES, public_bind=True)
    with pytest.raises(SettingsError, match="not a loopback address"):
        base(tmp_path / "x.db", host="10.0.0.5", public_bind=True, demo=True)


def test_public_settings_need_the_flag_and_keep_demo_safeguards(paths: dict) -> None:
    with pytest.raises(demo.DemoError, match="FLOWPILOT_PUBLIC_DEMO=1"):
        demo.public_settings(base(paths["normal"]), environ={})
    settings = demo.public_settings(
        base(paths["normal"], llm=LLMConfig(mode="ollama")), environ={"FLOWPILOT_PUBLIC_DEMO": "1"}
    )
    assert (settings.host, settings.port) == (ALL_INTERFACES, 8000)
    assert settings.public_bind is True
    assert settings.demo is True
    assert settings.llm.mode == "off"
    assert settings.db_path == paths["demo"]
    with pytest.raises(demo.DemoError, match="configured FLOWPILOT_DB_PATH"):
        demo.public_settings(base(paths["demo"]), environ={"FLOWPILOT_PUBLIC_DEMO": "1"})


def test_cli_serve_public_requires_the_flag(
    paths: dict, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    demo.reset(yes=True, configured_db=paths["normal"])
    monkeypatch.setattr(demo, "load_settings", lambda: base(paths["normal"]))
    monkeypatch.delenv("FLOWPILOT_PUBLIC_DEMO", raising=False)
    assert demo.main(["serve", "--public"]) == 2
    assert "FLOWPILOT_PUBLIC_DEMO=1" in capsys.readouterr().err

    served: list[Settings] = []
    import flowpilot.api.__main__ as entry

    monkeypatch.setattr(entry, "serve", lambda s, prog: served.append(s) or 0)
    monkeypatch.setenv("FLOWPILOT_PUBLIC_DEMO", "1")
    assert demo.main(["serve", "--public"]) == 0
    assert served[0].host == ALL_INTERFACES
    assert served[0].demo is True
    assert demo.main(["serve"]) == 0  # without --public: loopback, even with the flag set
    assert served[1].host == "127.0.0.1"


# --- separate API keys ----------------------------------------------------------------------------


def test_extra_keys_are_validated_and_hidden(tmp_path: Path) -> None:
    env = {"FLOWPILOT_API_KEY": API_KEY, "FLOWPILOT_DB_PATH": str(tmp_path / "x.db")}
    settings = load_settings(
        {**env, "FLOWPILOT_CONSOLE_API_KEY": CONSOLE_KEY, "FLOWPILOT_N8N_API_KEY": N8N_KEY},
        env_file=None,
    )
    assert (settings.console_api_key, settings.n8n_api_key) == (CONSOLE_KEY, N8N_KEY)
    assert CONSOLE_KEY not in repr(settings)
    assert N8N_KEY not in repr(settings)
    with pytest.raises(SettingsError, match="FLOWPILOT_CONSOLE_API_KEY must be at least"):
        load_settings({**env, "FLOWPILOT_CONSOLE_API_KEY": "short"}, env_file=None)
    with pytest.raises(SettingsError, match="must differ"):
        load_settings({**env, "FLOWPILOT_N8N_API_KEY": API_KEY}, env_file=None)
    assert load_settings(env, env_file=None).console_api_key is None


@pytest.fixture
def keyed(settings: Settings):
    keyed = Settings(
        api_key=settings.api_key,
        db_path=settings.db_path,
        backup_dir=settings.backup_dir,
        console_api_key=CONSOLE_KEY,
        n8n_api_key=N8N_KEY,
    )
    with TestClient(create_app(keyed)) as client:
        yield client


def test_console_and_default_keys_reach_everything(keyed: TestClient) -> None:
    for key in (API_KEY, CONSOLE_KEY):
        assert keyed.get("/outbox", headers={"X-API-Key": key}).status_code == 200
    assert keyed.get("/outbox", headers={"X-API-Key": "x" * 40}).status_code == 401


def test_n8n_key_is_limited_to_its_workflow_calls(keyed: TestClient, lead_payload: dict) -> None:
    n8n = {"X-API-Key": N8N_KEY}
    created = keyed.post("/leads", json=lead_payload, headers={**n8n, "Idempotency-Key": "k1"})
    assert created.status_code == 201
    lead_id = created.json()["lead"]["id"]
    assert keyed.get("/leads?status=scored&limit=1", headers=n8n).status_code == 200
    assert keyed.post(f"/leads/{lead_id}/qualification", headers=n8n).status_code == 201
    assert keyed.post(f"/leads/{lead_id}/drafts", headers=n8n).status_code == 201
    for method, path in (
        ("GET", "/outbox"),
        ("GET", f"/leads/{lead_id}"),
        ("GET", f"/leads/{lead_id}/audit"),
        ("GET", f"/leads/{lead_id}/drafts"),
        ("GET", "/reviewers/me"),
        ("POST", f"/leads/{lead_id}/drafts/1/approval"),
    ):
        response = keyed.request(method, path, headers=n8n, json={"decision": "approved"})
        assert response.status_code == 403, (method, path)
        assert response.json()["detail"]["code"] == "forbidden"


def test_request_log_names_the_client_not_the_key(keyed: TestClient) -> None:
    records: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(LOGGER)
    handler, level = Keep(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        keyed.get("/outbox", headers={"X-API-Key": CONSOLE_KEY})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    (record,) = [r for r in records if getattr(r, "event", "") == "request"]
    assert record.client == "console"
    assert CONSOLE_KEY not in str(record.__dict__)


# --- console public mode --------------------------------------------------------------------------


def test_console_api_host_needs_the_public_flag(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="FLOWPILOT_API_URL"):
        UIConfig(api_url="http://api:8000", api_key=API_KEY)
    assert UIConfig(api_url="http://api:8000", api_key=API_KEY, public_demo=True)
    with pytest.raises(SettingsError, match="FLOWPILOT_API_URL"):
        UIConfig(api_url="http://evil.example:8000", api_key=API_KEY, public_demo=True)
    env = {"FLOWPILOT_API_KEY": API_KEY, "FLOWPILOT_API_URL": "http://api:8000"}
    with pytest.raises(SettingsError):
        load_ui_config(env, env_file=None)
    config = load_ui_config({**env, "FLOWPILOT_PUBLIC_DEMO": "1"}, env_file=None)
    assert config.public_demo is True


@pytest.mark.parametrize(("flag", "renders"), [("1", True), (None, False)])
def test_console_container_bind_needs_the_public_flag(
    monkeypatch: pytest.MonkeyPatch, flag: str | None, renders: bool
) -> None:
    app_test = pytest.importorskip("streamlit.testing.v1").AppTest
    import streamlit

    from flowpilot.ui import api

    real = streamlit.get_option
    monkeypatch.setattr(
        streamlit,
        "get_option",
        lambda name: ALL_INTERFACES if name == "server.address" else real(name),
    )
    monkeypatch.setattr(api, "load_ui_config", lambda: UIConfig("http://127.0.0.1:8000", API_KEY))
    if flag is None:
        monkeypatch.delenv("FLOWPILOT_PUBLIC_DEMO", raising=False)
    else:
        monkeypatch.setenv("FLOWPILOT_PUBLIC_DEMO", flag)
    at = app_test.from_file(str(ROOT / "src" / "flowpilot" / "ui" / "app.py")).run()
    refused = any("Refusing to run" in e.value for e in at.error)
    assert refused is not renders
    assert (len(at.text_input) == 1) is renders


# --- scheduled refresh ----------------------------------------------------------------------------


def test_refresh_builds_reviewable_demo_data(paths: dict) -> None:
    before = paths["normal"].read_bytes()
    leads = demo.refresh(yes=True, reviewer_token=TOKEN, settings=base(paths["normal"]))
    assert leads == 3
    assert count(paths["demo"], "leads") == 3
    assert count(paths["demo"], "drafts") == 3
    assert count(paths["demo"], "approvals") == count(paths["demo"], "outbox") == 0
    conn = connect(paths["demo"])
    try:
        assert authenticate(conn, TOKEN).name == demo.DEMO_REVIEWER
    finally:
        conn.close()
    assert not paths["demo"].with_name(demo.SNAPSHOT_NAME).exists()
    assert paths["normal"].read_bytes() == before


def test_refresh_is_safe_while_the_api_serves(paths: dict, lead_payload: dict) -> None:
    demo.refresh(yes=True, reviewer_token=TOKEN, settings=base(paths["normal"]))
    served = demo.demo_settings(base(paths["normal"]))
    auth = {"X-API-Key": API_KEY}
    with TestClient(create_app(served)) as api:
        extra = {**lead_payload, "contact_email": "visitor@visitor.example"}
        assert api.post("/leads", json=extra, headers=auth).status_code == 201
        assert api.get("/leads", headers=auth).json()["total"] == 4
        demo.refresh(yes=True, reviewer_token=TOKEN, settings=base(paths["normal"]))
        after = api.get("/leads", headers=auth)  # the running app sees the refreshed data
        assert after.json()["total"] == 3
        assert after.headers[DEMO_HEADER] == "demo"


@pytest.mark.parametrize(
    "token", [None, "", "fpr_short", "not-a-token-" + "x" * 40, "fpr_" + "x" * 129]
)
def test_refresh_refuses_bad_tokens_before_touching_data(paths: dict, token) -> None:
    with pytest.raises(demo.DemoError, match="FLOWPILOT_DEMO_REVIEWER_TOKEN"):
        demo.refresh(yes=True, reviewer_token=token, settings=base(paths["normal"]))
    assert not paths["demo"].exists()


def test_refresh_refusals(paths: dict) -> None:
    with pytest.raises(demo.DemoError, match="--yes"):
        demo.refresh(yes=False, reviewer_token=TOKEN, settings=base(paths["normal"]))
    with pytest.raises(demo.DemoError, match="configured FLOWPILOT_DB_PATH"):
        demo.refresh(yes=True, reviewer_token=TOKEN, settings=base(paths["demo"]))


def test_refresh_forever_logs_failures_safely_and_keeps_running(
    paths: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    records: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    outcomes = iter([RuntimeError("lead Rowan Hollis: secret detail"), 3])

    def flaky(**kw: object) -> int:
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(demo, "refresh", flaky)
    logger = logging.getLogger(LOGGER)
    handler, level = Keep(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        failures = demo.refresh_forever(300, TOKEN, sleep=lambda s: None, rounds=2)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    assert failures == 1
    assert [getattr(r, "event", None) for r in records] == ["demo_refresh_failed", "demo_refresh"]
    from flowpilot.logs import JsonFormatter

    failed = json.loads(JsonFormatter().format(records[0]))
    assert failed["error_type"] == "RuntimeError"
    assert "secret detail" not in json.dumps(failed)
    assert "Rowan" not in json.dumps(failed)
    assert json.loads(JsonFormatter().format(records[1]))["leads"] == 3


def test_refresh_forever_rejects_a_bad_token_before_waiting() -> None:
    waited: list[int] = []
    with pytest.raises(demo.DemoError, match="FLOWPILOT_DEMO_REVIEWER_TOKEN"):
        demo.refresh_forever(600, "fpr_short", sleep=waited.append, rounds=1)
    assert waited == []


def test_refresh_forever_waits_then_refreshes(paths: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(demo, "refresh", lambda **kw: calls.append("refresh") or 3)
    demo.refresh_forever(600, TOKEN, sleep=lambda s: calls.append(f"sleep {s}"), rounds=2)
    assert calls == ["sleep 600", "refresh", "sleep 600", "refresh"]
    with pytest.raises(demo.DemoError, match="at least 300"):
        demo.refresh_forever(60, TOKEN, sleep=lambda s: None, rounds=1)


# --- deployment files (static) --------------------------------------------------------------------


def compose() -> dict:
    return json.loads((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))


def test_only_the_proxy_is_published_and_backends_are_internal() -> None:
    services = compose()["services"]
    assert set(services) == {"api", "demo-refresh", "console", "proxy"}  # no n8n
    assert [name for name, svc in services.items() if "ports" in svc] == ["proxy"]
    networks = compose()["networks"]
    assert networks["backend"] == {"internal": True}
    assert networks["frontend"] == {"internal": True}
    assert services["api"]["networks"] == ["backend"]
    assert services["console"]["networks"] == ["backend", "frontend"]
    assert services["proxy"]["networks"] == ["frontend", "edge"]
    assert services["demo-refresh"]["network_mode"] == "none"


def test_services_are_hardened_and_public_mode_is_explicit() -> None:
    services = compose()["services"]
    for name, svc in services.items():
        assert svc["read_only"] is True, name
        assert svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
    api = services["api"]
    assert api["environment"]["FLOWPILOT_PUBLIC_DEMO"] == "1"
    assert api["environment"]["FLOWPILOT_LLM_MODE"] == "off"
    assert api["command"][-1].endswith("flowpilot-demo serve --public")
    console = services["console"]["environment"]
    assert console["FLOWPILOT_API_URL"] == "http://api:8000"
    assert console["FLOWPILOT_API_KEY"].startswith("${FLOWPILOT_CONSOLE_API_KEY:?")
    assert "FLOWPILOT_DEMO_REVIEWER_TOKEN" not in console
    secrets = ("FLOWPILOT_API_KEY", "FLOWPILOT_CONSOLE_API_KEY", "FLOWPILOT_DEMO_REVIEWER_TOKEN")
    for secret in secrets:
        assert api["environment"][secret].startswith(f"${{{secret}:?")  # required, never defaulted


def test_proxy_can_render_its_config_as_the_unprivileged_user() -> None:
    """Regression (local trial): a root-owned tmpfs at conf.d left nginx with no server
    blocks, so it ran but listened on nothing."""
    proxy = compose()["services"]["proxy"]
    assert "/etc/nginx/conf.d:uid=101,gid=101,mode=0755" in proxy["tmpfs"]


def test_proxy_routes_only_to_the_console_with_tls_and_limits() -> None:
    conf = (DEPLOY / "nginx" / "default.conf.template").read_text(encoding="utf-8")
    assert set(re.findall(r"proxy_pass\s+([^;]+);", conf)) == {"http://console:8501"}
    assert "api:8000" not in conf
    for needed in ("listen 8443 ssl", "limit_req zone=", "limit_conn ", "Strict-Transport-Security",
                   "server_tokens off"):  # fmt: skip
        assert needed in conf, needed


def render_template(**env: str) -> str:
    """What the nginx image's envsubst produces (only FLOWPILOT_* variables are substituted)."""
    conf = (DEPLOY / "nginx" / "default.conf.template").read_text(encoding="utf-8")
    return re.sub(r"\$\{(FLOWPILOT_\w+)\}", lambda m: env[m.group(1)], conf)


@pytest.mark.parametrize(
    "origin",
    ["https://demo.example.com", "https://localhost:18443"],
    ids=["port-443", "port-18443"],
)
def test_http_redirects_to_the_configured_origin(origin: str) -> None:
    """Regression (local trial): `https://$host$request_uri` dropped a non-standard port, and
    `$host` is client-controlled. The redirect now uses the operator's configured origin."""
    rendered = render_template(FLOWPILOT_PUBLIC_HOST="demo.example.com",
                               FLOWPILOT_PUBLIC_ORIGIN=origin)  # fmt: skip
    plain = re.search(r"server \{[^{}]*listen 8080[^{}]*\}", rendered).group(0)
    assert "listen 8080 default_server;" in plain
    assert f"return 301 {origin}$request_uri;" in plain
    assert "$host" not in plain
    assert "${" not in rendered  # every placeholder was substituted


def test_redirect_origin_defaults_to_the_public_host() -> None:
    proxy = compose()["services"]["proxy"]["environment"]
    assert proxy["FLOWPILOT_PUBLIC_ORIGIN"] == (
        "${FLOWPILOT_PUBLIC_ORIGIN:-https://${FLOWPILOT_PUBLIC_HOST}}"
    )
    assert proxy["NGINX_ENVSUBST_FILTER"] == "^FLOWPILOT_"


def nginx_locations() -> dict[str, str]:
    conf = (DEPLOY / "nginx" / "default.conf.template").read_text(encoding="utf-8")
    return dict(re.findall(r"location\s+([^\s{]+)\s*\{([^}]*)\}", conf))


def nginx_zones() -> dict[str, str]:
    conf = (DEPLOY / "nginx" / "default.conf.template").read_text(encoding="utf-8")
    return dict(re.findall(r"limit_req_zone\s+\S+\s+zone=(\w+):\S+\s+rate=(\S+);", conf))


def _limit(block: str, directive: str) -> tuple[str, int]:
    if directive == "limit_req":
        zone, burst = re.search(r"limit_req zone=(\w+) burst=(\d+) nodelay;", block).groups()
    else:
        zone, burst = re.search(r"limit_conn (\w+) (\d+);", block).groups()
    return zone, int(burst)


def test_every_proxy_location_is_rate_limited() -> None:
    locations = nginx_locations()
    assert set(locations) == {"/static/", "/_stcore/stream", "/"}
    for path, block in locations.items():
        assert "limit_req zone=" in block, path
        assert "limit_conn " in block, path
    conn_zones = {_limit(block, "limit_conn")[0] for block in locations.values()}
    assert len(conn_zones) == 3  # an asset burst cannot use up the page or websocket budget


def test_pages_and_websocket_stay_strict() -> None:
    """Abuse protection is kept where requests are expensive: pages, Streamlit endpoints and
    websocket session opens."""
    zones, locations = nginx_zones(), nginx_locations()
    page_zone, page_burst = _limit(locations["/"], "limit_req")
    ws_zone, ws_burst = _limit(locations["/_stcore/stream"], "limit_req")
    assert (zones[page_zone], page_burst) == ("10r/s", 40)
    assert (zones[ws_zone], ws_burst) == ("2r/s", 10)
    assert _limit(locations["/"], "limit_conn")[1] == 20
    assert _limit(locations["/_stcore/stream"], "limit_conn")[1] == 20


def test_static_assets_have_room_for_a_full_page_load() -> None:
    """Regression (browser trial): 10 r/s with burst 40 and 20 concurrent HTTP/2 streams
    returned 429 for 41 of the 61 assets Streamlit's index.html loads, leaving a blank page."""
    zones, block = nginx_zones(), nginx_locations()["/static/"]
    zone, burst = _limit(block, "limit_req")
    assert zone != _limit(nginx_locations()["/"], "limit_req")[0]
    assert zones[zone] == "100r/s"
    assert burst >= 400
    assert _limit(block, "limit_conn")[1] >= 128  # nginx's default http2_max_concurrent_streams
    assert "proxy_pass http://console:8501;" in block


def test_asset_burst_covers_every_asset_streamlit_ships() -> None:
    streamlit = pytest.importorskip("streamlit")  # needs the `ui` group
    static = Path(streamlit.__file__).parent / "static" / "static"
    shipped = sum(1 for f in static.rglob("*") if f.is_file())
    _, burst = _limit(nginx_locations()["/static/"], "limit_req")
    assert burst >= shipped, f"burst {burst} < {shipped} shipped Streamlit assets"


def test_images_run_unprivileged_without_secrets() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.count("USER 10001:10001") == 2
    assert ".env" not in dockerfile
    assert dockerfile.count("uv sync --frozen --no-dev") == 2
    stages = dict(re.findall(r"^FROM (\S+) AS (\S+)$", dockerfile, re.M)[i][::-1]
                  for i in range(dockerfile.count("\nFROM ")))  # fmt: skip
    assert stages["api"] == stages["console"] == "runtime"
    assert stages["runtime"] == "${PYTHON_IMAGE}"  # fresh base, not the builder
    for target in ("api", "console"):
        body = dockerfile.split(f" AS {target}\n", 1)[1].split("\nFROM ", 1)[0]
        assert "COPY --from=build-" in body  # only the built virtual environment is copied
        assert "uv" not in body.replace("/opt/venv", "")  # no uv binary or uv commands at run time
    assert "--mount=type=cache,target=/root/.cache/uv" in dockerfile  # cache never in a layer
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for entry in (".env", ".env.*", "var/", ".git/", ".claude/"):
        assert entry in ignored, entry


def test_local_secrets_and_tls_material_are_gitignored() -> None:
    import subprocess

    for path in ("deploy/public-demo/.env", "deploy/public-demo/certs/privkey.pem",
                 "deploy/public-demo/certs/fullchain.pem", "server.key"):  # fmt: skip
        result = subprocess.run(  # noqa: S603
            ["git", "check-ignore", "-q", path],  # noqa: S607 - git from PATH, test only
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, f"{path} is not gitignored"


def test_env_example_has_only_placeholders() -> None:
    lines = [
        line for line in (DEPLOY / "env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]  # fmt: skip
    values = dict(line.split("=", 1) for line in lines)
    for key in ("FLOWPILOT_API_KEY", "FLOWPILOT_CONSOLE_API_KEY", "FLOWPILOT_DEMO_REVIEWER_TOKEN"):
        assert "placeholder" in values[key], key
    assert len({values[k] for k in values if "KEY" in k or "TOKEN" in k}) == 3
