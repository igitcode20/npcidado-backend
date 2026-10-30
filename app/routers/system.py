from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import Settings
from ..database import Database
from ..dependencies import get_current_user, get_database, get_settings
from ..services.quotas import usage_for_user

router = APIRouter(tags=["Sistema"])


@router.get("/health")
def health(request: Request) -> Any:
    database: Database = request.app.state.database
    settings: Settings = request.app.state.settings
    database_ok = database.ping() if database is not None else False
    payload = {
        "status": "ok" if database_ok else "degraded",
        "app": "NPICADO IA",
        "version": __version__,
        "database": "connected" if database_ok else "unavailable",
        "groq_configured": bool(settings.groq_api_key),
        "groq_model": settings.groq_model if settings.groq_api_key else None,
    }
    return JSONResponse(status_code=200 if database_ok else 503, content=payload)


@router.get("/meta")
def meta(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "app": "NPICADO IA",
        "version": __version__,
        "max_users": settings.max_users,
        "max_admins": settings.max_admins,
        "daily_message_limit": settings.daily_message_limit,
        "daily_document_limit": settings.daily_document_limit,
        "groq_configured": bool(settings.groq_api_key),
        "timezone": settings.app_timezone,
    }


@router.get("/usage")
def usage(
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return usage_for_user(database, user, settings)
