from __future__ import annotations

import logging
import re
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from starlette.concurrency import run_in_threadpool

from ..config import Settings
from ..database import Database
from ..dependencies import get_current_user, get_database, get_settings, require_admin
from ..serializers import iso_utc, public_document
from ..services.ingestion import (
    SUPPORTED_EXTENSIONS,
    IngestionError,
    delete_document,
    ingest_document,
)
from ..services.text import excerpt

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/documents",
    tags=["Biblioteca"],
    dependencies=[Depends(get_current_user)],
)


def _object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Documento no encontrado.") from None


@router.get("")
def list_documents(
    q: str = Query(default="", max_length=120),
    tipo: str = Query(default="", max_length=10),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if q.strip():
        pattern = re.escape(q.strip())
        filters["$or"] = [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"filename": {"$regex": pattern, "$options": "i"}},
            {"tags": {"$regex": pattern, "$options": "i"}},
        ]
    extension = tipo.strip().lower().lstrip(".")
    if extension:
        filters["extension"] = extension

    total = database.documents.count_documents(filters)
    documents = list(
        database.documents.find(filters).sort("uploaded_at", -1).skip(offset).limit(limit)
    )
    return {
        "items": [public_document(document) for document in documents],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/stats")
def library_stats(database: Database = Depends(get_database)) -> dict[str, Any]:
    total_documents = database.documents.count_documents({})
    total_chunks = database.chunks.count_documents({})
    total_characters = 0
    by_type: dict[str, int] = {}
    for document in database.documents.find({}, {"char_count": 1, "extension": 1}):
        total_characters += int(document.get("char_count", 0))
        extension = document.get("extension", "otro")
        by_type[extension] = by_type.get(extension, 0) + 1
    return {
        "documents": total_documents,
        "chunks": total_chunks,
        "characters": total_characters,
        "by_type": by_type,
    }


@router.get("/{document_id}")
def get_document(
    document_id: str,
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    oid = _object_id(document_id)
    document = database.documents.find_one({"_id": oid})
    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    fragments = [
        {
            "chunk_index": int(chunk.get("chunk_index", 0)),
            "index": int(chunk.get("chunk_index", 0)) + 1,
            "content": excerpt(chunk.get("content", ""), 1600),
        }
        for chunk in database.chunks.find({"document_id": oid})
        .sort("chunk_index", 1)
        .limit(3)
    ]
    result = public_document(document)
    result["fragments"] = fragments
    result["created_at"] = iso_utc(document.get("uploaded_at"))
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    tags: str = Form(default=""),
    admin: dict[str, Any] = Depends(require_admin),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    filename = file.filename or "documento"
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Formato no soportado. Puedes subir PDF, DOCX, XLSX, TXT, MD o CSV.",
        )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    await file.close()
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"El archivo supera el máximo permitido de {settings.max_upload_mb} MB.",
        )
    if not data:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")

    try:
        document = await run_in_threadpool(
            ingest_document,
            database,
            settings,
            filename=filename,
            data=data,
            title=title,
            tags=tags,
            uploaded_by=admin,
        )
    except IngestionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        logger.exception("Error inesperado ingestando '%s'", filename)
        raise HTTPException(
            status_code=500,
            detail="No se pudo procesar el documento. Inténtalo de nuevo.",
        ) from None

    return {"document": document, "message": "Documento añadido a la biblioteca."}


@router.delete("/{document_id}")
def remove_document(
    document_id: str,
    _admin: dict[str, Any] = Depends(require_admin),
    database: Database = Depends(get_database),
) -> dict[str, str]:
    oid = _object_id(document_id)
    document = database.documents.find_one({"_id": oid}, {"title": 1})
    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    delete_document(database, oid)
    logger.info("Documento '%s' eliminado por %s", document.get("title"), _admin.get("username"))
    return {"message": "Documento eliminado de la biblioteca."}
