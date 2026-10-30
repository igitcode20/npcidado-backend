from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from bson import Binary, ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..bootstrap import utcnow
from ..config import Settings
from ..database import Database
from ..dependencies import get_current_user, get_database, get_settings
from ..schemas import GenerateRequest
from ..serializers import iso_utc, public_source
from ..services.document_builder import (
    EmptyKnowledgeError,
    build_document,
)
from ..services.quotas import (
    QuotaExceeded,
    consume_quota,
    refund_quota,
    usage_for_user,
)
from ..services.retrieval import retrieve_sources

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Generación de documentos"])

_FORMAT_LABELS = {"docx": "Word", "pdf": "PDF", "xlsx": "Excel", "txt": "Texto"}


def _object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Documento generado no encontrado.") from None


def _generated_public(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(record["_id"]),
        "tema": record.get("topic", ""),
        "title": record.get("title", ""),
        "formato": record.get("format", ""),
        "formato_label": _FORMAT_LABELS.get(record.get("format", ""), ""),
        "filename": record.get("filename", ""),
        "size_bytes": int(record.get("size_bytes", 0)),
        "ai_tokens_used": 0,
        "sources_count": len(record.get("sources", [])),
        "sources": record.get("sources", []),
        "created_at": iso_utc(record.get("created_at")),
        "expires_at": iso_utc(record.get("expires_at")),
        "download_url": f"/api/generated/{record['_id']}/download",
    }


@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_document(
    payload: GenerateRequest,
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Genera el archivo con Python. Este endpoint NO llama a Groq ni consume tokens de IA."""
    query = f"{payload.tema} {payload.instrucciones}".strip()
    sources = retrieve_sources(
        database,
        query,
        limit=payload.fuentes,
        max_per_document=3,
    )
    if not sources:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "no_content",
                "message": "No encontré información relacionada en la biblioteca. Sube documentos sobre el tema o usa otras palabras.",
            },
        )

    try:
        consume_quota(database, user, "document", settings)
    except QuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.as_detail()) from exc

    try:
        built = build_document(
            topic=payload.tema,
            format=payload.formato,
            instructions=payload.instrucciones,
            user_name=user.get("full_name") or user["username"],
            sources=sources,
            timezone=settings.app_timezone,
        )
        now = utcnow()
        sources_public = [public_source(source) for source in sources]
        record = {
            "user_id": user["_id"],
            "topic": payload.tema,
            "title": built.filename,
            "format": built.format,
            "filename": built.filename,
            "mime_type": built.mime_type,
            "size_bytes": len(built.data),
            "data": Binary(built.data),
            "sources": sources_public,
            "ai_tokens_used": 0,
            "created_at": now,
            "expires_at": now + timedelta(days=settings.document_retention_days),
        }
        result = database.generated_documents.insert_one(record)
        record["_id"] = result.inserted_id
        usage = usage_for_user(database, user, settings)
        logger.info(
            "Documento %s generado por %s (%s KB, 0 tokens)",
            built.filename,
            user.get("username"),
            max(1, len(built.data) // 1024),
        )
        return {
            "document": _generated_public(record),
            "usage": usage,
            "message": "Documento generado con Python sin consumir tokens de IA.",
        }
    except EmptyKnowledgeError as exc:
        refund_quota(database, user["_id"], "document", settings)
        raise HTTPException(status_code=422, detail={"code": "no_content", "message": str(exc)}) from exc
    except HTTPException:
        refund_quota(database, user["_id"], "document", settings)
        raise
    except Exception:
        logger.exception("Error generando documento para %s", user.get("username"))
        refund_quota(database, user["_id"], "document", settings)
        raise HTTPException(
            status_code=500,
            detail="No se pudo generar el documento. Inténtalo de nuevo.",
        ) from None


@router.get("/generated")
def list_generated(
    limit: int = Query(default=12, ge=1, le=50),
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    records = list(
        database.generated_documents.find(
            {"user_id": user["_id"]},
            {"data": 0},
        )
        .sort("created_at", -1)
        .limit(limit)
    )
    return {"items": [_generated_public(record) for record in records]}


@router.get("/generated/{generated_id}/download")
def download_generated(
    generated_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
) -> Response:
    oid = _object_id(generated_id)
    record = database.generated_documents.find_one({"_id": oid})
    if not record:
        raise HTTPException(status_code=404, detail="Documento generado no encontrado.")
    if str(record.get("user_id")) != str(user["_id"]) and user.get("role") != "admin":
        raise HTTPException(status_code=404, detail="Documento generado no encontrado.")
    expires_at = record.get("expires_at")
    if expires_at and expires_at < utcnow():
        raise HTTPException(status_code=410, detail="Este documento expiró. Vuelve a generarlo.")
    data = bytes(record.get("data", b""))
    if not data:
        raise HTTPException(status_code=404, detail="El archivo ya no está disponible.")
    filename = record.get("filename", "documento")
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "documento"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=data,
        media_type=record.get("mime_type", "application/octet-stream"),
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(data)),
            "Cache-Control": "private, no-store, max-age=0",
        },
    )


@router.delete("/generated/{generated_id}")
def delete_generated(
    generated_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
) -> dict[str, str]:
    oid = _object_id(generated_id)
    result = database.generated_documents.delete_one({"_id": oid, "user_id": user["_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Documento generado no encontrado.")
    return {"message": "Documento eliminado."}
