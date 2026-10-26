"""Application settings loaded from the environment and an optional `.env` file.

Settings fail closed: a missing/weak API key or a non-loopback bind address is a startup error,
not a warning.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

ENV_PREFIX = "FLOWPILOT_"
MIN_API_KEY_LENGTH = 32
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

LLM_MODES = ("off", "ollama")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LLM_TIMEOUT_RANGE_S = (5.0, 180.0)
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")


class SettingsError(ValueError):
    """Raised when configuration is missing or unsafe."""


@dataclass(frozen=True)
class LLMConfig:
    """LLM calls are off unless explicitly enabled; Ollama must be on a loopback address."""

    mode: str = "off"
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:3b"
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        if self.mode not in LLM_MODES:
            raise SettingsError(
                f"FLOWPILOT_LLM_MODE={self.mode!r} is invalid; allowed: {', '.join(LLM_MODES)}."
            )
        parts = urlsplit(self.ollama_url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname not in LOOPBACK_HOSTS
            or parts.username
            or parts.password
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise SettingsError(
                f"FLOWPILOT_OLLAMA_URL={self.ollama_url!r} must be a bare http(s) URL on a "
                "loopback address, e.g. http://127.0.0.1:11434."
            )
        if not _MODEL_NAME.match(self.model):
            raise SettingsError(f"FLOWPILOT_LLM_MODEL={self.model!r} is not a valid model name.")
        low, high = LLM_TIMEOUT_RANGE_S
        if not low <= self.timeout_s <= high:
            raise SettingsError(
                f"FLOWPILOT_LLM_TIMEOUT_S={self.timeout_s} must be between {low:g} and {high:g}."
            )

    @property
    def enabled(self) -> bool:
        return self.mode == "ollama"


@dataclass(frozen=True)
class Settings:
    api_key: str
    db_path: Path
    backup_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    enable_docs: bool = False
    llm: LLMConfig = field(default_factory=LLMConfig)
    # Set only by `flowpilot-demo serve` (never from the environment): marks every response so
    # demo tooling can prove it is talking to the isolated demo database.
    demo: bool = False
    log_level: str = "INFO"
    # Optional separate credentials (ADR 0005): the console and n8n can each get their own key,
    # so either can be rotated alone; n8n's key is limited to its workflow calls.
    console_api_key: str | None = None
    n8n_api_key: str | None = None
    # Set only by `flowpilot-demo serve --public` (never from the environment): allows binding
    # 0.0.0.0 inside a private container network. Requires demo mode; see ADR 0005.
    public_bind: bool = False

    def __post_init__(self) -> None:
        if len(self.api_key) < MIN_API_KEY_LENGTH:
            raise SettingsError(
                f"FLOWPILOT_API_KEY must be at least {MIN_API_KEY_LENGTH} characters; "
                "generate one with `flowpilot-admin gen-key`."
            )
        if self.public_bind and not self.demo:
            raise SettingsError("public binding is only allowed for the isolated demo (ADR 0005).")
        if self.host not in LOOPBACK_HOSTS and not (self.public_bind and self.host == "0.0.0.0"):  # noqa: S104
            raise SettingsError(
                f"FLOWPILOT_HOST={self.host!r} is not a loopback address; "
                f"allowed values: {', '.join(sorted(LOOPBACK_HOSTS))}."
            )
        if not 1 <= self.port <= 65535:
            raise SettingsError(f"FLOWPILOT_PORT={self.port} is out of range.")
        keys = {"FLOWPILOT_API_KEY": self.api_key}
        for name, key in (
            ("FLOWPILOT_CONSOLE_API_KEY", self.console_api_key),
            ("FLOWPILOT_N8N_API_KEY", self.n8n_api_key),
        ):
            if key is None:
                continue
            if len(key) < MIN_API_KEY_LENGTH:
                raise SettingsError(f"{name} must be at least {MIN_API_KEY_LENGTH} characters.")
            if key in keys.values():
                raise SettingsError(f"{name} must differ from every other API key.")
            keys[name] = key
        if self.log_level not in LOG_LEVELS:
            raise SettingsError(
                f"FLOWPILOT_LOG_LEVEL={self.log_level!r} is invalid; "
                f"allowed: {', '.join(LOG_LEVELS)}."
            )

    def __repr__(self) -> str:  # never leak the key into logs or tracebacks
        return (
            f"Settings(api_key='***', db_path={str(self.db_path)!r}, "
            f"backup_dir={str(self.backup_dir)!r}, host={self.host!r}, port={self.port}, "
            f"enable_docs={self.enable_docs}, llm={self.llm!r}, demo={self.demo}, "
            f"log_level={self.log_level!r}, "
            f"console_api_key={'***' if self.console_api_key else None}, "
            f"n8n_api_key={'***' if self.n8n_api_key else None}, public_bind={self.public_bind})"
        )


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal `.env` file: KEY=VALUE lines, `#` comments, optional quotes."""
    values: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise SettingsError(f"{path}:{lineno}: expected KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _parse_bool(name: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off", ""}:
        return False
    raise SettingsError(f"{name}={value!r} is not a boolean.")


@dataclass(frozen=True)
class StoragePaths:
    db_path: Path
    backup_dir: Path


def _merged_env(environ: Mapping[str, str] | None, env_file: Path | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        merged.update(parse_env_file(env_file))
    merged.update(os.environ if environ is None else environ)
    return merged


def _storage(merged: Mapping[str, str]) -> StoragePaths:
    return StoragePaths(
        db_path=Path(merged.get(ENV_PREFIX + "DB_PATH") or "var/flowpilot.db"),
        backup_dir=Path(merged.get(ENV_PREFIX + "BACKUP_DIR") or "var/backups"),
    )


def load_storage_paths(
    environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")
) -> StoragePaths:
    """Paths only, for admin commands that must work before an API key exists."""
    return _storage(_merged_env(environ, env_file))


def _llm(merged: Mapping[str, str]) -> LLMConfig:
    defaults = LLMConfig()
    timeout_raw = merged.get(ENV_PREFIX + "LLM_TIMEOUT_S") or str(defaults.timeout_s)
    try:
        timeout_s = float(timeout_raw)
    except ValueError as exc:
        raise SettingsError(f"FLOWPILOT_LLM_TIMEOUT_S={timeout_raw!r} is not a number.") from exc
    return LLMConfig(
        mode=(merged.get(ENV_PREFIX + "LLM_MODE") or defaults.mode).strip().lower(),
        ollama_url=merged.get(ENV_PREFIX + "OLLAMA_URL") or defaults.ollama_url,
        model=merged.get(ENV_PREFIX + "LLM_MODEL") or defaults.model,
        timeout_s=timeout_s,
    )


def load_llm_config(
    environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")
) -> LLMConfig:
    """LLM settings only, for `flowpilot-admin llm-check` (no API key required)."""
    return _llm(_merged_env(environ, env_file))


def load_settings(
    environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")
) -> Settings:
    """Build settings from `env_file` overlaid by `environ` (real env vars win)."""
    merged = _merged_env(environ, env_file)

    def get(name: str, default: str | None = None) -> str | None:
        return merged.get(ENV_PREFIX + name, default)

    api_key = get("API_KEY")
    if not api_key:
        raise SettingsError(
            "FLOWPILOT_API_KEY is not set; copy .env.example to .env and set a key "
            "(`flowpilot-admin gen-key`)."
        )
    port_raw = get("PORT", "8000") or "8000"
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise SettingsError(f"FLOWPILOT_PORT={port_raw!r} is not an integer.") from exc

    storage = _storage(merged)
    return Settings(
        api_key=api_key,
        db_path=storage.db_path,
        backup_dir=storage.backup_dir,
        host=get("HOST", "127.0.0.1") or "127.0.0.1",
        port=port,
        enable_docs=_parse_bool("FLOWPILOT_ENABLE_DOCS", get("ENABLE_DOCS", "false") or ""),
        log_level=(get("LOG_LEVEL", "INFO") or "INFO").strip().upper(),
        console_api_key=get("CONSOLE_API_KEY") or None,
        n8n_api_key=get("N8N_API_KEY") or None,
        llm=_llm(merged),
    )
