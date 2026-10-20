"""Application settings loaded from the environment and an optional `.env` file.

Settings fail closed: a missing/weak API key or a non-loopback bind address is a startup error,
not a warning.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_PREFIX = "FLOWPILOT_"
MIN_API_KEY_LENGTH = 32
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class SettingsError(ValueError):
    """Raised when configuration is missing or unsafe."""


@dataclass(frozen=True)
class Settings:
    api_key: str
    db_path: Path
    backup_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    enable_docs: bool = False

    def __post_init__(self) -> None:
        if len(self.api_key) < MIN_API_KEY_LENGTH:
            raise SettingsError(
                f"FLOWPILOT_API_KEY must be at least {MIN_API_KEY_LENGTH} characters; "
                "generate one with `flowpilot-admin gen-key`."
            )
        if self.host not in LOOPBACK_HOSTS:
            raise SettingsError(
                f"FLOWPILOT_HOST={self.host!r} is not a loopback address; "
                f"allowed values: {', '.join(sorted(LOOPBACK_HOSTS))}."
            )
        if not 1 <= self.port <= 65535:
            raise SettingsError(f"FLOWPILOT_PORT={self.port} is out of range.")

    def __repr__(self) -> str:  # never leak the key into logs or tracebacks
        return (
            f"Settings(api_key='***', db_path={str(self.db_path)!r}, "
            f"backup_dir={str(self.backup_dir)!r}, host={self.host!r}, port={self.port}, "
            f"enable_docs={self.enable_docs})"
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
    )
