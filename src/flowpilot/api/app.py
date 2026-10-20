"""Application factory."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from flowpilot import __version__
from flowpilot.api.routes import protected, public
from flowpilot.db.connection import connect
from flowpilot.db.migrate import pending
from flowpilot.llm.client import LLMClient, OllamaClient
from flowpilot.services.assist import LeadNotDraftable, LeadNotFound
from flowpilot.services.intake import Clock, IdempotencyKeyReused, utc_now
from flowpilot.settings import LLMConfig, Settings

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class StartupError(RuntimeError):
    """The database is missing or not fully migrated."""


def check_database(settings: Settings) -> None:
    if not settings.db_path.is_file():
        raise StartupError(
            f"database {settings.db_path} does not exist; run `flowpilot-admin migrate`"
        )
    conn = connect(settings.db_path)
    try:
        todo = pending(conn)
    finally:
        conn.close()
    if todo:
        names = ", ".join(m.label for m in todo)
        raise StartupError(f"pending migrations: {names}; run `flowpilot-admin migrate`")


class _FromSettings:
    """Sentinel: build the LLM client from settings (None when FLOWPILOT_LLM_MODE=off)."""


FROM_SETTINGS = _FromSettings()


def build_llm_client(config: LLMConfig) -> OllamaClient | None:
    if not config.enabled:
        return None
    return OllamaClient(base_url=config.ollama_url, model=config.model, timeout_s=config.timeout_s)


def create_app(
    settings: Settings,
    clock: Clock = utc_now,
    llm: LLMClient | _FromSettings | None = FROM_SETTINGS,
) -> FastAPI:
    llm_client = build_llm_client(settings.llm) if isinstance(llm, _FromSettings) else llm

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        check_database(settings)  # refuse to serve against an unmigrated schema
        try:
            yield
        finally:
            close = getattr(llm_client, "close", None)
            if callable(close):
                close()

    docs = settings.enable_docs
    app = FastAPI(
        title="FlowPilot AI API",
        version=__version__,
        description="Northstar Digital Solutions (fictional) lead pipeline. Synthetic data only.",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
    )
    app.state.settings = settings
    app.state.clock = clock
    app.state.llm = llm_client

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        rid = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response

    @app.exception_handler(IdempotencyKeyReused)
    async def idempotency_conflict(_: Request, exc: IdempotencyKeyReused) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "idempotency_key_reused",
                    "message": "Idempotency-Key was already used with a different payload",
                }
            },
        )

    @app.exception_handler(LeadNotFound)
    async def lead_not_found(_: Request, exc: LeadNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": {"code": "lead_not_found", "message": f"lead {exc} does not exist"}},
        )

    @app.exception_handler(LeadNotDraftable)
    async def lead_not_draftable(_: Request, exc: LeadNotDraftable) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "lead_not_draftable",
                    "message": f"lead status {exc.status!r} does not allow drafting",
                }
            },
        )

    app.include_router(public)
    app.include_router(protected)
    return app
