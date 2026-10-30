from __future__ import annotations

import logging
import re
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.errors import DuplicateKeyError

from ..bootstrap import utcnow
from ..config import Settings
from ..database import Database
from ..dependencies import get_database, get_settings, require_admin
from ..schemas import AdminUserCreate, AdminUserUpdate
from ..security import hash_password, normalize_username, validate_password, validate_username
from ..serializers import public_user
from ..services.quotas import local_day, usage_summaries_for_admin

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/admin",
    tags=["Administración"],
    dependencies=[Depends(require_admin)],
)


def _object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Usuario no encontrado.") from None


def _clean_name(value: str) -> str:
    clean = re.sub(r"\s+", " ", value).strip()
    if len(clean) < 2:
        raise HTTPException(status_code=422, detail="El nombre completo es obligatorio.")
    return clean[:80]


def _user_with_usage(
    user: dict[str, Any], usage_map: dict[str, dict[str, int]]
) -> dict[str, Any]:
    result = public_user(user)
    usage = usage_map.get(str(user["_id"]), {"messages": 0, "documents": 0})
    result["usage_today"] = usage
    return result


@router.get("/users")
def list_users(
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    users = list(database.users.find({}).sort("created_at", 1))
    usage_map = usage_summaries_for_admin(database, [user["_id"] for user in users], settings)
    return {
        "items": [_user_with_usage(user, usage_map) for user in users],
        "total": len(users),
        "max_users": settings.max_users,
        "max_admins": settings.max_admins,
        "max_standard_users": settings.max_standard_users,
        "slots_available": max(0, settings.max_users - len(users)),
        "daily_message_limit": settings.daily_message_limit,
        "daily_document_limit": settings.daily_document_limit,
    }


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminUserCreate,
    admin: dict[str, Any] = Depends(require_admin),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    total_users = database.users.count_documents({})
    if total_users >= settings.max_users:
        raise HTTPException(
            status_code=409,
            detail=f"El plan está completo ({settings.max_users}/{settings.max_users}). Reutiliza una cuenta existente o aumenta MAX_USERS en el servidor.",
        )
    if payload.role == "admin":
        admins = database.users.count_documents({"role": "admin"})
        if admins >= settings.max_admins:
            raise HTTPException(
                status_code=409,
                detail=f"Ya existen {settings.max_admins} administradores, el máximo permitido.",
            )
    else:
        standard_users = database.users.count_documents({"role": "user"})
        if standard_users >= settings.max_standard_users:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"El plan incluye {settings.max_standard_users} usuarios estándar y ya están ocupados. "
                    "Crea al segundo administrador o reutiliza una cuenta existente."
                ),
            )

    try:
        username = validate_username(payload.username.strip())
        password = validate_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    username_key = normalize_username(username)
    if database.users.find_one({"username_key": username_key}):
        raise HTTPException(status_code=409, detail="Ese nombre de usuario ya existe.")

    now = utcnow()
    document = {
        "username": username,
        "username_key": username_key,
        "full_name": _clean_name(payload.full_name),
        "password_hash": hash_password(password),
        "role": payload.role,
        "active": True,
        "token_version": 0,
        "created_at": now,
        "updated_at": now,
        "created_by": admin["_id"],
        "last_login_at": None,
    }
    try:
        result = database.users.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Ese nombre de usuario ya existe.") from None
    document["_id"] = result.inserted_id
    logger.info("Admin %s creó al usuario %s (%s)", admin.get("username"), username, payload.role)
    return {
        "user": _user_with_usage(document, {}),
        "message": "Usuario creado correctamente.",
        "slots_available": max(0, settings.max_users - total_users - 1),
    }


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    admin: dict[str, Any] = Depends(require_admin),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    oid = _object_id(user_id)
    target = database.users.find_one({"_id": oid})
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    updates: dict[str, Any] = {"updated_at": utcnow()}
    if payload.full_name is not None:
        updates["full_name"] = _clean_name(payload.full_name)
    if payload.role is not None and payload.role != target["role"]:
        if oid == admin["_id"]:
            raise HTTPException(status_code=400, detail="No puedes cambiar tu propio rol de administrador.")
        if payload.role == "admin":
            admins = database.users.count_documents({"role": "admin"})
            if admins >= settings.max_admins:
                raise HTTPException(
                    status_code=409,
                    detail=f"Ya existen {settings.max_admins} administradores, el máximo permitido.",
                )
        else:
            if database.users.count_documents({"role": "user"}) >= settings.max_standard_users:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"El plan admite {settings.max_standard_users} usuarios estándar y los cupos están ocupados. "
                        "Reorganiza los roles antes de cambiar este."
                    ),
                )
            other_admins = database.users.count_documents(
                {"role": "admin", "active": True, "_id": {"$ne": oid}}
            )
            if target.get("role") == "admin" and target.get("active") and other_admins == 0:
                raise HTTPException(
                    status_code=409,
                    detail="Debe quedar al menos un administrador activo.",
                )
        updates["role"] = payload.role

    if payload.active is not None and payload.active != target.get("active", True):
        if oid == admin["_id"] and not payload.active:
            raise HTTPException(status_code=400, detail="No puedes desactivar tu propia cuenta.")
        if not payload.active:
            other_active_admins = database.users.count_documents(
                {"role": "admin", "active": True, "_id": {"$ne": oid}}
            )
            if target.get("role") == "admin" and target.get("active") and other_active_admins == 0:
                raise HTTPException(
                    status_code=409,
                    detail="Debe quedar al menos un administrador activo.",
                )
        updates["active"] = payload.active

    if payload.password is not None:
        try:
            validate_password(payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        updates["password_hash"] = hash_password(payload.password)
        updates["token_version"] = int(target.get("token_version", 0)) + 1

    if len(updates) == 1:
        raise HTTPException(status_code=400, detail="No enviaste cambios para aplicar.")

    database.users.update_one({"_id": oid}, {"$set": updates})
    updated = database.users.find_one({"_id": oid}) or target
    usage_map = usage_summaries_for_admin(database, [oid], settings)
    logger.info("Admin %s actualizó al usuario %s", admin.get("username"), target.get("username"))
    return {
        "user": _user_with_usage(updated, usage_map),
        "message": "Usuario actualizado correctamente.",
    }


@router.get("/stats")
def admin_stats(
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    today = local_day(settings)
    usage_rows = list(database.usage.find({"date": today}))
    messages_today = sum(int(row.get("messages", 0)) for row in usage_rows)
    documents_today = sum(int(row.get("documents", 0)) for row in usage_rows)
    total_users = database.users.count_documents({})
    active_users = database.users.count_documents({"active": True})
    admins = database.users.count_documents({"role": "admin"})
    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "admins": admins,
            "max": settings.max_users,
        },
        "knowledge": {
            "documents": database.documents.count_documents({}),
            "chunks": database.chunks.count_documents({}),
        },
        "today": {
            "messages": messages_today,
            "documents_generated": documents_today,
            "date": today,
        },
        "activity": {
            "conversations": database.conversations.count_documents({}),
            "messages": database.messages.count_documents({}),
        },
        "ai": {
            "groq_configured": bool(settings.groq_api_key),
            "model": settings.groq_model,
        },
        "limits": {
            "daily_message_limit": settings.daily_message_limit,
            "daily_document_limit": settings.daily_document_limit,
            "max_users": settings.max_users,
            "max_admins": settings.max_admins,
        },
    }
