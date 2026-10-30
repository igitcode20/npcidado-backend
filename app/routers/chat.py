from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query

from ..bootstrap import utcnow
from ..config import Settings
from ..database import Database
from ..dependencies import get_current_user, get_database, get_groq, get_settings
from ..schemas import ChatRequest, ConversationRenameRequest
from ..serializers import iso_utc
from ..services.groq_service import GroqError, GroqNotConfiguredError
from ..services.quotas import (
    QuotaExceeded,
    consume_quota,
    refund_quota,
    usage_for_user,
)
from ..services.retrieval import build_context, public_sources as retrieval_public_sources
from ..services.retrieval import retrieve_sources
from ..services.text import markdown_preview

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


def _object_id(value: str, message: str = "Conversación no encontrada.") -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail=message) from None


def _owned_conversation(database: Database, conversation_id: str, user_id: ObjectId) -> dict[str, Any]:
    oid = _object_id(conversation_id)
    conversation = database.conversations.find_one({"_id": oid, "user_id": user_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return conversation


def _conversation_public(conversation: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(conversation["_id"]),
        "title": conversation.get("title", "Nueva conversación"),
        "last_message": conversation.get("last_message", ""),
        "message_count": int(conversation.get("message_count", 0)),
        "created_at": iso_utc(conversation.get("created_at")),
        "updated_at": iso_utc(conversation.get("updated_at")),
    }


def _message_public(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(message["_id"]),
        "role": message["role"],
        "content": message.get("content", ""),
        "sources": message.get("sources", []),
        "created_at": iso_utc(message.get("created_at")),
    }


@router.get("/conversations")
def list_conversations(
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    conversations = list(
        database.conversations.find({"user_id": user["_id"]})
        .sort("updated_at", -1)
        .limit(80)
    )
    return {"items": [_conversation_public(conversation) for conversation in conversations]}


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(
    conversation_id: str,
    limit: int = Query(default=200, ge=1, le=300),
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    conversation = _owned_conversation(database, conversation_id, user["_id"])
    messages = list(
        database.messages.find({"conversation_id": conversation["_id"]})
        .sort("created_at", 1)
        .limit(limit)
    )
    return {
        "conversation": _conversation_public(conversation),
        "items": [_message_public(message) for message in messages],
    }


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: str,
    payload: ConversationRenameRequest,
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    conversation = _owned_conversation(database, conversation_id, user["_id"])
    title = re.sub(r"\s+", " ", payload.title).strip()[:90]
    database.conversations.update_one(
        {"_id": conversation["_id"]},
        {"$set": {"title": title, "updated_at": utcnow()}},
    )
    conversation["title"] = title
    return {"conversation": _conversation_public(conversation)}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
) -> dict[str, str]:
    conversation = _owned_conversation(database, conversation_id, user["_id"])
    database.messages.delete_many({"conversation_id": conversation["_id"]})
    database.conversations.delete_one({"_id": conversation["_id"]})
    return {"message": "Conversación eliminada."}


@router.post("")
async def send_message(
    payload: ChatRequest,
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
    groq=Depends(get_groq),
) -> dict[str, Any]:
    message_text = payload.message.strip()
    if not message_text:
        raise HTTPException(status_code=422, detail="Escribe una pregunta.")

    # Validamos la conversación (si existe) antes de consumir cuota.
    conversation: dict[str, Any] | None = None
    if payload.conversation_id:
        conversation = _owned_conversation(database, payload.conversation_id, user["_id"])

    try:
        consume_quota(database, user, "message", settings)
    except QuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.as_detail()) from exc

    created_conversation = False
    quota_refunded = False
    try:
        history: list[dict[str, str]] = []
        if conversation:
            recent = list(
                database.messages.find(
                    {"conversation_id": conversation["_id"]},
                    {"role": 1, "content": 1},
                )
                .sort("created_at", -1)
                .limit(10)
            )
            history = [
                {"role": item["role"], "content": item["content"]}
                for item in reversed(recent)
                if item.get("content")
            ]

        prior_questions = [item["content"] for item in history if item["role"] == "user"][-2:]
        retrieval_query = "\n".join([*prior_questions, message_text])
        sources = retrieve_sources(database, retrieval_query, limit=6, max_per_document=2)
        context = build_context(sources)

        result = await groq.chat(
            question=message_text,
            context=context,
            history=history,
            user_name=user.get("full_name") or user["username"],
        )

        now = utcnow()
        if conversation is None:
            title = re.sub(r"\s+", " ", message_text).strip()[:72]
            if len(title) > 68:
                title = title[:68].rstrip() + "…"
            conversation = {
                "_id": ObjectId(),
                "user_id": user["_id"],
                "title": title or "Nueva conversación",
                "last_message": "",
                "message_count": 0,
                "created_at": now,
                "updated_at": now,
            }
            database.conversations.insert_one(conversation)
            created_conversation = True

        sources_public = retrieval_public_sources(sources)
        user_message = {
            "_id": ObjectId(),
            "conversation_id": conversation["_id"],
            "user_id": user["_id"],
            "role": "user",
            "content": message_text,
            "sources": [],
            "created_at": now,
        }
        assistant_message = {
            "_id": ObjectId(),
            "conversation_id": conversation["_id"],
            "user_id": user["_id"],
            "role": "assistant",
            "content": result["content"],
            "sources": sources_public,
            "model": result.get("model", settings.groq_model),
            "tokens": result.get("usage", {}),
            "created_at": now + timedelta(milliseconds=2),
        }
        database.messages.insert_many([user_message, assistant_message])
        database.conversations.update_one(
            {"_id": conversation["_id"]},
            {
                "$set": {
                    "last_message": markdown_preview(result["content"]),
                    "updated_at": utcnow(),
                },
                "$inc": {"message_count": 2},
            },
        )
        current_usage = usage_for_user(database, user, settings)
        return {
            "conversation": _conversation_public(
                {
                    **conversation,
                    "last_message": markdown_preview(result["content"]),
                    "message_count": int(conversation.get("message_count", 0)) + 2,
                }
            ),
            "user_message": _message_public(user_message),
            "message": _message_public(assistant_message),
            "sources": sources_public,
            "usage": current_usage,
        }
    except (GroqNotConfiguredError, GroqError) as exc:
        refund_quota(database, user["_id"], "message", settings)
        quota_refunded = True
        if created_conversation and conversation:
            database.conversations.delete_one({"_id": conversation["_id"]})
        status_code = 503 if isinstance(exc, GroqNotConfiguredError) else getattr(exc, "status_code", 502)
        code = "groq_not_configured" if isinstance(exc, GroqNotConfiguredError) else "groq_error"
        raise HTTPException(
            status_code=status_code,
            detail={"code": code, "message": str(exc)},
        ) from exc
    except HTTPException:
        if not quota_refunded:
            refund_quota(database, user["_id"], "message", settings)
        if created_conversation and conversation:
            database.conversations.delete_one({"_id": conversation["_id"]})
        raise
    except Exception:
        logger.exception("Error inesperado en el chat del usuario %s", user.get("username"))
        if not quota_refunded:
            refund_quota(database, user["_id"], "message", settings)
        if created_conversation and conversation:
            database.conversations.delete_one({"_id": conversation["_id"]})
        raise HTTPException(
            status_code=500,
            detail="Ocurrió un error procesando tu mensaje. Inténtalo de nuevo.",
        ) from None
