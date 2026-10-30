from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..bootstrap import utcnow
from ..config import Settings
from ..database import Database
from ..dependencies import get_current_user, get_database, get_settings
from ..schemas import ChangePasswordRequest, LoginRequest
from ..security import (
    create_access_token,
    hash_password,
    normalize_username,
    validate_password,
    verify_password,
)
from ..serializers import public_user
from ..services.quotas import usage_for_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Autenticación"])

_DUMMY_HASH = hash_password("dummy-password-for-timing-only-123")


class LoginThrottle:
    def __init__(self, max_attempts: int = 8, window_seconds: int = 900) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}
        self._lock = Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        attempts = [value for value in self._attempts.get(key, []) if now - value < self.window_seconds]
        if attempts:
            self._attempts[key] = attempts
        else:
            self._attempts.pop(key, None)
        return attempts

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= self.max_attempts

    def fail(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            attempts = self._prune(key, now)
            attempts.append(now)
            self._attempts[key] = attempts

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


login_throttle = LoginThrottle()


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "desconocido"


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    client_key = _client_key(request)
    if login_throttle.blocked(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos. Espera 15 minutos antes de volver a intentar.",
        )

    username_key = normalize_username(payload.username)
    user = database.users.find_one({"username_key": username_key})
    stored_hash = user["password_hash"] if user else _DUMMY_HASH
    password_ok = verify_password(payload.password, stored_hash)

    if not user or not password_ok:
        login_throttle.fail(client_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos.",
        )

    if not user.get("active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta está desactivada. Contacta al administrador.",
        )

    now = utcnow()
    database.users.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": now}})
    user["last_login_at"] = now
    token, expires_in = create_access_token(
        str(user["_id"]),
        user["role"],
        settings,
        int(user.get("token_version", 0)),
    )
    login_throttle.reset(client_key)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": public_user(user),
    }


@router.get("/me")
def me(
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return {
        "user": public_user(user),
        "usage": usage_for_user(database, user, settings),
    }


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    user: dict[str, Any] = Depends(get_current_user),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="La contraseña actual no es correcta.")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe ser diferente.")
    try:
        validate_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    new_version = int(user.get("token_version", 0)) + 1
    database.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hash_password(payload.new_password),
                "token_version": new_version,
                "updated_at": utcnow(),
            }
        },
    )
    token, expires_in = create_access_token(
        str(user["_id"]), user["role"], settings, new_version
    )
    logger.info("El usuario %s cambió su contraseña.", user.get("username"))
    return {
        "message": "Contraseña actualizada correctamente.",
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
    }
