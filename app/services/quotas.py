from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from ..bootstrap import utcnow
from ..config import Settings
from ..database import Database

QuotaKind = Literal["message", "document"]
_FIELD: dict[QuotaKind, str] = {"message": "messages", "document": "documents"}


class QuotaExceeded(Exception):
    def __init__(self, kind: QuotaKind, limit: int, used: int, reset_at: str) -> None:
        self.kind = kind
        self.limit = limit
        self.used = used
        self.reset_at = reset_at
        if kind == "message":
            message = f"Alcanzaste el límite de {limit} mensajes por día."
        else:
            message = f"Alcanzaste el límite de {limit} documentos generados por día."
        super().__init__(message)

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": "quota_messages" if self.kind == "message" else "quota_documents",
            "message": str(self),
            "limit": self.limit,
            "used": self.used,
            "reset_at": self.reset_at,
        }


def _local_now(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.app_timezone))


def local_day(settings: Settings) -> str:
    return _local_now(settings).date().isoformat()


def reset_at_iso(settings: Settings) -> str:
    now = _local_now(settings)
    tomorrow = now.date() + timedelta(days=1)
    reset = datetime.combine(tomorrow, time.min, tzinfo=ZoneInfo(settings.app_timezone))
    return reset.isoformat()


def _ensure_usage_row(database: Database, user_id: ObjectId, day: str) -> None:
    now = utcnow()
    try:
        database.usage.update_one(
            {"user_id": user_id, "date": day},
            {
                "$setOnInsert": {
                    "user_id": user_id,
                    "date": day,
                    "messages": 0,
                    "documents": 0,
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
    except DuplicateKeyError:
        # Otra petición simultánea acaba de crear la fila del día.
        pass


def consume_quota(
    database: Database,
    user: dict[str, Any],
    kind: QuotaKind,
    settings: Settings,
) -> dict[str, Any]:
    day = local_day(settings)
    user_id = user["_id"]
    field = _FIELD[kind]
    _ensure_usage_row(database, user_id, day)
    now = utcnow()

    if user.get("role") == "admin":
        database.usage.update_one(
            {"user_id": user_id, "date": day},
            {"$inc": {field: 1}, "$set": {"updated_at": now}},
        )
        return usage_for_user(database, user, settings)

    limit = (
        settings.daily_message_limit if kind == "message" else settings.daily_document_limit
    )
    result = database.usage.update_one(
        {"user_id": user_id, "date": day, field: {"$lt": limit}},
        {"$inc": {field: 1}, "$set": {"updated_at": now}},
    )
    if result.modified_count == 0:
        current = database.usage.find_one({"user_id": user_id, "date": day}) or {}
        raise QuotaExceeded(kind, limit, int(current.get(field, limit)), reset_at_iso(settings))
    return usage_for_user(database, user, settings)


def refund_quota(
    database: Database,
    user_id: ObjectId,
    kind: QuotaKind,
    settings: Settings,
) -> None:
    field = _FIELD[kind]
    day = local_day(settings)
    database.usage.update_one(
        {"user_id": user_id, "date": day, field: {"$gt": 0}},
        {"$inc": {field: -1}, "$set": {"updated_at": utcnow()}},
    )


def usage_for_user(
    database: Database,
    user: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    day = local_day(settings)
    row = database.usage.find_one({"user_id": user["_id"], "date": day}) or {}
    unlimited = user.get("role") == "admin"
    message_limit = None if unlimited else settings.daily_message_limit
    document_limit = None if unlimited else settings.daily_document_limit

    def bucket(used_value: int, limit: int | None) -> dict[str, Any]:
        return {
            "used": int(used_value),
            "limit": limit,
            "remaining": None if limit is None else max(0, limit - int(used_value)),
            "unlimited": limit is None,
        }

    messages_used = int(row.get("messages", 0))
    documents_used = int(row.get("documents", 0))
    return {
        "date": day,
        "timezone": settings.app_timezone,
        "reset_at": reset_at_iso(settings),
        "unlimited": unlimited,
        "messages": bucket(messages_used, message_limit),
        "documents": bucket(documents_used, document_limit),
    }


def usage_summaries_for_admin(
    database: Database,
    user_ids: list[ObjectId],
    settings: Settings,
) -> dict[str, dict[str, int]]:
    day = local_day(settings)
    result: dict[str, dict[str, int]] = {}
    for row in database.usage.find({"user_id": {"$in": user_ids}, "date": day}):
        result[str(row["user_id"])] = {
            "messages": int(row.get("messages", 0)),
            "documents": int(row.get("documents", 0)),
        }
    return result
