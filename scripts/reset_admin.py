"""Restablece la contraseña de una cuenta desde la terminal.

Uso (desde la carpeta backend, con .env configurado):
    python scripts/reset_admin.py
    python scripts/reset_admin.py --username Ismael

La contraseña se pide de forma oculta. Invalida las sesiones anteriores.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import utcnow  # noqa: E402
from app.config import Settings  # noqa: E402
from app.database import Database  # noqa: E402
from app.security import hash_password, normalize_username, validate_password  # noqa: E402


def main() -> int:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Restablecer contraseña de NPICADO IA")
    parser.add_argument(
        "--username",
        default=settings.first_admin_username,
        help="Usuario a actualizar (por defecto, el primer administrador)",
    )
    args = parser.parse_args()

    try:
        database = Database.connect(settings)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1

    try:
        username_key = normalize_username(args.username)
        user = database.users.find_one({"username_key": username_key})
        if not user:
            print(f"No existe el usuario «{args.username}» en la base {settings.mongodb_db}.")
            print("Arranca el backend con FIRST_ADMIN_USERNAME y FIRST_ADMIN_PASSWORD para sembrarlo.")
            return 2

        print(f"Cuenta encontrada: {user.get('full_name', '')} (@{user['username']}) · rol: {user.get('role')}")
        password = getpass.getpass("Nueva contraseña: ")
        confirmation = getpass.getpass("Repite la contraseña: ")
        if password != confirmation:
            print("ERROR: las contraseñas no coinciden.")
            return 2
        try:
            validate_password(password)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 2

        result = database.users.update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "password_hash": hash_password(password),
                    "active": True,
                    "updated_at": utcnow(),
                },
                "$inc": {"token_version": 1},
            },
        )
        if result.modified_count != 1:
            print("ERROR: no se pudo actualizar la cuenta.")
            return 1
        print("Contraseña actualizada. La cuenta está activa y sus sesiones anteriores fueron invalidadas.")
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
