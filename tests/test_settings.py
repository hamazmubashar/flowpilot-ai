from pathlib import Path

import pytest

from flowpilot.settings import (
    LLMConfig,
    Settings,
    SettingsError,
    load_llm_config,
    load_settings,
    load_storage_paths,
    parse_env_file,
)

KEY = "k" * 32


def test_defaults() -> None:
    s = load_settings({"FLOWPILOT_API_KEY": KEY}, env_file=None)
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.db_path == Path("var/flowpilot.db")
    assert s.enable_docs is False


def test_missing_api_key_fails_closed() -> None:
    with pytest.raises(SettingsError, match="not set"):
        load_settings({}, env_file=None)


def test_short_api_key_rejected() -> None:
    with pytest.raises(SettingsError, match="at least 32"):
        load_settings({"FLOWPILOT_API_KEY": "short"}, env_file=None)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com", ""])  # noqa: S104
def test_non_loopback_host_rejected(host: str) -> None:
    with pytest.raises(SettingsError, match="loopback"):
        Settings(api_key=KEY, db_path=Path("x.db"), backup_dir=Path("b"), host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_hosts_accepted(host: str) -> None:
    assert Settings(api_key=KEY, db_path=Path("x.db"), backup_dir=Path("b"), host=host).host == host


@pytest.mark.parametrize("port", ["abc", "0", "70000"])
def test_bad_port_rejected(port: str) -> None:
    with pytest.raises(SettingsError):
        load_settings({"FLOWPILOT_API_KEY": KEY, "FLOWPILOT_PORT": port}, env_file=None)


def test_bad_bool_rejected() -> None:
    with pytest.raises(SettingsError, match="boolean"):
        load_settings({"FLOWPILOT_API_KEY": KEY, "FLOWPILOT_ENABLE_DOCS": "maybe"}, env_file=None)


def test_repr_hides_api_key() -> None:
    s = Settings(api_key=KEY, db_path=Path("x.db"), backup_dir=Path("b"))
    assert KEY not in repr(s)
    assert "***" in repr(s)


def test_env_file_parsing_and_precedence(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n\n"
        f"FLOWPILOT_API_KEY='{KEY}'\n"
        "export FLOWPILOT_PORT=9000\n"
        'FLOWPILOT_DB_PATH="from-file.db"\n'
        "FLOWPILOT_ENABLE_DOCS=yes\n",
        encoding="utf-8",
    )
    s = load_settings({"FLOWPILOT_DB_PATH": "from-env.db"}, env_file=env)
    assert s.api_key == KEY
    assert s.port == 9000
    assert s.enable_docs is True
    assert s.db_path == Path("from-env.db")  # real environment wins over .env


def test_env_file_rejects_malformed_line(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("NOT A PAIR\n", encoding="utf-8")
    with pytest.raises(SettingsError, match=":1:"):
        parse_env_file(env)


def test_storage_paths_need_no_api_key() -> None:
    paths = load_storage_paths({"FLOWPILOT_BACKUP_DIR": "bk"}, env_file=None)
    assert paths.backup_dir == Path("bk")
    assert paths.db_path == Path("var/flowpilot.db")


# --- M2: LLM configuration -------------------------------------------------------------------


def test_llm_defaults_to_off() -> None:
    s = load_settings({"FLOWPILOT_API_KEY": KEY}, env_file=None)
    assert s.llm == LLMConfig()
    assert s.llm.mode == "off"
    assert s.llm.enabled is False
    assert s.llm.model == "qwen2.5:3b"
    assert s.llm.ollama_url == "http://127.0.0.1:11434"


def test_llm_enabled_from_env() -> None:
    cfg = load_llm_config(
        {
            "FLOWPILOT_LLM_MODE": " Ollama ",
            "FLOWPILOT_OLLAMA_URL": "http://[::1]:11434",
            "FLOWPILOT_LLM_MODEL": "gemma3:1b",
            "FLOWPILOT_LLM_TIMEOUT_S": "90",
        },
        env_file=None,
    )
    assert cfg.enabled is True
    assert cfg.model == "gemma3:1b"
    assert cfg.timeout_s == 90.0


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:11434",
        "http://10.0.0.5:11434",
        "http://0.0.0.0:11434",
        "ftp://127.0.0.1:11434",
        "http://user:pw@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434?x=1",
        "http://127.0.0.1.evil.example:11434",
        "127.0.0.1:11434",
    ],
)
def test_non_loopback_ollama_url_rejected(url: str) -> None:
    with pytest.raises(SettingsError, match="loopback"):
        LLMConfig(ollama_url=url)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:11434", "http://localhost:11434/", "https://[::1]:8443"]
)
def test_loopback_ollama_urls_accepted(url: str) -> None:
    assert LLMConfig(ollama_url=url).ollama_url == url


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"mode": "openai"}, "LLM_MODE"),
        ({"model": "bad model"}, "LLM_MODEL"),
        ({"model": "-rm"}, "LLM_MODEL"),
        ({"timeout_s": 1.0}, "between"),
        ({"timeout_s": 500.0}, "between"),
    ],
)
def test_invalid_llm_config_rejected(kwargs: dict, match: str) -> None:
    with pytest.raises(SettingsError, match=match):
        LLMConfig(**kwargs)


def test_non_numeric_timeout_rejected() -> None:
    with pytest.raises(SettingsError, match="not a number"):
        load_llm_config({"FLOWPILOT_LLM_TIMEOUT_S": "soon"}, env_file=None)


def test_repr_includes_llm_config_without_key() -> None:
    s = Settings(api_key=KEY, db_path=Path("x.db"), backup_dir=Path("b"))
    assert "llm=LLMConfig(mode='off'" in repr(s)
    assert KEY not in repr(s)
