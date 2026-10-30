from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import Settings
from .database import Database
from .security import hash_password, normalize_username, validate_password, validate_username

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    """Guardamos fechas naive en UTC para mantener un único formato en Mongo."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def seed_first_admin(database: Database, settings: Settings) -> None:
    """Crea el primer administrador solo cuando la base no tiene ningún admin.

    Nunca cambia la contraseña de un administrador existente en cada arranque.
    """
    if database.users.count_documents({"role": "admin"}) > 0:
        return

    username = settings.first_admin_username.strip()
    password = settings.first_admin_password
    if not username or not password:
        logger.warning(
            "No hay administradores y falta FIRST_ADMIN_USERNAME/FIRST_ADMIN_PASSWORD. "
            "Configura esas variables para crear el primer acceso."
        )
        return

    try:
        validate_username(username)
        validate_password(password)
    except ValueError as exc:
        logger.error("No se pudo crear el administrador inicial: %s", exc)
        return

    username_key = normalize_username(username)
    existing = database.users.find_one({"username_key": username_key})
    now = utcnow()
    if existing:
        # Caso borde: existía un usuario con ese nombre pero ningún admin.
        database.users.update_one(
            {"_id": existing["_id"]},
            {"$set": {"role": "admin", "active": True, "updated_at": now}},
        )
        logger.warning("El usuario %s existente fue promovido a administrador inicial.", username)
        return

    database.users.insert_one(
        {
            "username": username,
            "username_key": username_key,
            "full_name": settings.first_admin_name.strip() or username,
            "password_hash": hash_password(password),
            "role": "admin",
            "active": True,
            "token_version": 0,
            "created_at": now,
            "updated_at": now,
            "created_by": None,
            "last_login_at": None,
        }
    )
    logger.info("Administrador inicial '%s' creado.", username)


def initialize_database(database: Database, settings: Settings) -> None:
    database.ensure_indexes()
    seed_first_admin(database, settings)
