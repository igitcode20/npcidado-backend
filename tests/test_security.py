from __future__ import annotations

import time

import jwt
import pytest

from app.config import Settings
from app.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    validate_password,
    validate_username,
    verify_password,
)


def test_password_hash_roundtrip_and_salt() -> None:
    first = hash_password("TestPassword123!")
    second = hash_password("TestPassword123!")
    assert first != second
    assert verify_password("TestPassword123!", first)
    assert not verify_password("incorrecta", first)
    assert not verify_password("TestPassword123!", "formato-invalido")


def test_password_and_username_policies() -> None:
    with pytest.raises(ValueError):
        validate_password("corta")
    assert validate_password("ClaveSegura123") == "ClaveSegura123"
    assert validate_username("Ismael") == "Ismael"
    with pytest.raises(ValueError):
        validate_username("usuario con espacios")
    with pytest.raises(ValueError):
        validate_username("ab")
    with pytest.raises(ValueError):
        validate_username("usuario$peligroso")


def test_jwt_roundtrip_and_expiration() -> None:
    settings = Settings(secret_key="x" * 64, access_token_expire_minutes=1)
    token, expires_in = create_access_token("507f1f77bcf86cd799439011", "admin", settings)
    assert expires_in == 60
    payload = decode_access_token(token, settings)
    assert payload["sub"] == "507f1f77bcf86cd799439011"
    assert payload["role"] == "admin"

    expired = jwt.encode(
        {
            "sub": "507f1f77bcf86cd799439011",
            "role": "user",
            "type": "access",
            "iss": "npicado-ia",
            "iat": int(time.time()) - 100,
            "exp": int(time.time()) - 50,
        },
        settings.secret_key,
        algorithm="HS256",
    )
    with pytest.raises(Exception):
        decode_access_token(expired, settings)
