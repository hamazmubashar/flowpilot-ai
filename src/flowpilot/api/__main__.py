"""`flowpilot-api`: serve the API on a loopback address."""

from __future__ import annotations

import sys

from flowpilot.api.app import StartupError, check_database, create_app
from flowpilot.settings import Settings, SettingsError, load_settings


def main() -> int:
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"flowpilot-api: {exc}", file=sys.stderr)
        return 2
    return serve(settings)


def serve(settings: Settings, prog: str = "flowpilot-api") -> int:
    """Check the database, then serve `settings` (also used by `flowpilot-demo serve`)."""
    try:
        check_database(settings)
    except StartupError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 2

    import uvicorn  # imported lazily so config errors surface without server startup noise

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
