from pathlib import Path

import pytest

from flowpilot.settings import (
    Settings,
    SettingsError,
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
