from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .bootstrap import initialize_database
from .config import Settings
from .database import Database
from .routers import admin, auth, chat, generation, knowledge, system
from .services.groq_service import GroqService

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    groq_service: Any | None = None,
) -> FastAPI:
    _configure_logging()
    settings = settings or Settings.from_env()
    settings.validate_runtime()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owns_database = False
        owns_groq = False
        if database is not None:
            app.state.database = database
        else:
            app.state.database = Database.connect(settings)
            owns_database = True
        initialize_database(app.state.database, settings)

        if groq_service is not None:
            app.state.groq_service = groq_service
        else:
            app.state.groq_service = GroqService(settings)
            owns_groq = True

        logger.info(
            "NPICADO IA iniciado | entorno=%s | usuarios máx=%s | límites=%s mensajes/%s documentos",
            settings.environment,
            settings.max_users,
            settings.daily_message_limit,
            settings.daily_document_limit,
        )
        if not settings.groq_api_key:
            logger.warning("GROQ_API_KEY no está configurada: el chat responderá con error hasta configurarla.")
        try:
            yield
        finally:
            if owns_groq and hasattr(app.state.groq_service, "aclose"):
                await app.state.groq_service.aclose()
            if owns_database:
                app.state.database.close()
            logger.info("NPICADO IA detenido.")

    app = FastAPI(
        title="NPICADO IA API",
        description="Backend de la biblioteca inteligente NPICADO IA.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.database = database

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
        expose_headers=["Content-Disposition", "Content-Length"],
        max_age=3600,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(part) for part in first.get("loc", []) if part not in {"body", "query"})
        message = first.get("msg", "Datos inválidos.")
        detail = f"{field}: {message}" if field else message
        safe_errors = [
            {
                "loc": list(error.get("loc", [])),
                "msg": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"detail": detail, "errors": safe_errors},
        )

    # Routers bajo /api
    app.include_router(system.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(knowledge.router, prefix="/api")
    app.include_router(generation.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")

    @app.get("/api", include_in_schema=False)
    def api_root() -> dict[str, str]:
        return {
            "app": "NPICADO IA",
            "version": __version__,
            "status": "ok",
            "docs": "/api/docs",
        }

    frontend_dist = settings.frontend_dist
    is_frontend_available = settings.serve_frontend and (frontend_dist / "index.html").is_file()
    if settings.serve_frontend and not is_frontend_available:
        logger.warning("SERVE_FRONTEND=true pero no existe %s", frontend_dist / "index.html")

    if is_frontend_available:
        assets_dir = frontend_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        def serve_index():
            return FileResponse(frontend_dist / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "Ruta no encontrada."})
            requested = (frontend_dist / full_path).resolve()
            try:
                requested.relative_to(frontend_dist.resolve())
            except ValueError:
                return FileResponse(frontend_dist / "index.html")
            if requested.is_file():
                return FileResponse(requested)
            return FileResponse(frontend_dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def root() -> dict[str, str]:
            return {
                "app": "NPICADO IA",
                "status": "ok",
                "message": "API activa. El frontend se sirve por separado.",
                "docs": "/api/docs",
            }

    return app


app = create_app()
