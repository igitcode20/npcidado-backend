from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from .config import Settings

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_JWT_ISSUER = "npicado-ia"


class InvalidTokenError(ValueError):
    pass


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    clean = username.strip()
    if not USERNAME_RE.fullmatch(clean):
        raise ValueError("El usuario debe tener entre 3 y 32 caracteres: letras, números, punto, guion o guion bajo.")
    return clean


def validate_password(password: str) -> str:
    if not isinstance(password, str):
        raise ValueError("La contraseña no es válida.")
    if len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")
    if len(password) > 128:
        raise ValueError("La contraseña no puede superar los 128 caracteres.")
    return password


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n_raw, r_raw, p_raw, salt_b64, digest_b64 = stored.split("$", 5)
        if scheme != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        if n > 2**16 or r > 32 or p > 8:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(candidate, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(
    user_id: str,
    role: str,
    settings: Settings,
    token_version: int = 0,
) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "ver": int(token_version),
        "type": "access",
        "iss": _JWT_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return token, settings.access_token_expire_minutes * 60


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            issuer=_JWT_ISSUER,
            options={"require": ["exp", "sub", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("Token inválido o expirado.") from exc
    if payload.get("type") != "access" or not payload.get("sub"):
        raise InvalidTokenError("Token inválido.")
    return payload
