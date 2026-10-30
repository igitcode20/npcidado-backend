from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "full_name": user.get("full_name") or user["username"],
        "role": user["role"],
        "active": bool(user.get("active", True)),
        "created_at": iso_utc(user.get("created_at")),
        "last_login_at": iso_utc(user.get("last_login_at")),
    }


def public_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(document["_id"]),
        "title": document.get("title", ""),
        "filename": document.get("filename", ""),
        "extension": document.get("extension", ""),
        "mime_type": document.get("mime_type", "application/octet-stream"),
        "size_bytes": int(document.get("size_bytes", 0)),
        "char_count": int(document.get("char_count", 0)),
        "chunk_count": int(document.get("chunk_count", 0)),
        "page_count": document.get("page_count"),
        "tags": document.get("tags", []),
        "preview": document.get("preview", ""),
        "uploaded_by": document.get("uploaded_by_name", ""),
        "uploaded_at": iso_utc(document.get("uploaded_at")),
    }


def public_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": str(source.get("document_id", "")),
        "title": source.get("title", "Documento"),
        "filename": source.get("filename", ""),
        "chunk_index": source.get("chunk_index", 0),
        "score": round(float(source.get("score", 0.0)), 4),
        "excerpt": source.get("excerpt", ""),
    }
