from __future__ import annotations

import logging
import time
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database as MongoDatabaseDriver

from .config import Settings

logger = logging.getLogger(__name__)


class Database:
    """Contenedor pequeño para la base de datos y sus colecciones."""

    def __init__(self, db: MongoDatabaseDriver, client: MongoClient | None = None) -> None:
        self.db = db
        self.client = client

    def __getitem__(self, name: str):
        return self.db[name]

    @property
    def users(self):
        return self.db["users"]

    @property
    def documents(self):
        return self.db["documents"]

    @property
    def chunks(self):
        return self.db["chunks"]

    @property
    def conversations(self):
        return self.db["conversations"]

    @property
    def messages(self):
        return self.db["messages"]

    @property
    def usage(self):
        return self.db["usage"]

    @property
    def generated_documents(self):
        return self.db["generated_documents"]

    def ping(self) -> bool:
        try:
            self.db.command("ping")
            return True
        except Exception:
            return False

    def ensure_indexes(self) -> None:
        self.users.create_index("username_key", unique=True, name="ux_users_username")
        self.users.create_index([("role", ASCENDING), ("active", ASCENDING)], name="ix_users_role")

        self.documents.create_index("sha256", unique=True, name="ux_documents_sha256")
        self.documents.create_index([("uploaded_at", DESCENDING)], name="ix_documents_date")
        self.documents.create_index("extension", name="ix_documents_ext")

        self.chunks.create_index(
            [("document_id", ASCENDING), ("chunk_index", ASCENDING)],
            unique=True,
            name="ux_chunks_document_index",
        )
        self.chunks.create_index("terms", name="ix_chunks_terms")
        self.chunks.create_index("document_id", name="ix_chunks_document")

        self.conversations.create_index(
            [("user_id", ASCENDING), ("updated_at", DESCENDING)],
            name="ix_conversations_user_date",
        )
        self.messages.create_index(
            [("conversation_id", ASCENDING), ("created_at", ASCENDING)],
            name="ix_messages_conversation_date",
        )
        self.messages.create_index(
            [("user_id", ASCENDING), ("created_at", DESCENDING)],
            name="ix_messages_user_date",
        )

        self.usage.create_index(
            [("user_id", ASCENDING), ("date", ASCENDING)],
            unique=True,
            name="ux_usage_user_date",
        )

        self.generated_documents.create_index(
            [("user_id", ASCENDING), ("created_at", DESCENDING)],
            name="ix_generated_user_date",
        )
        try:
            self.generated_documents.create_index(
                "expires_at",
                expireAfterSeconds=0,
                name="ttl_generated_expiry",
            )
        except Exception as exc:  # mongomock y algunos entornos locales no soportan TTL
            logger.debug("No se pudo crear el índice TTL de documentos generados: %s", exc)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()

    @classmethod
    def connect(cls, settings: Settings, attempts: int = 3) -> "Database":
        client = MongoClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=30000,
            retryWrites=True,
            appname="npicado-ia",
        )
        database = cls(client[settings.mongodb_db], client=client)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                client.admin.command("ping")
                logger.info("Conectado a MongoDB (base: %s).", settings.mongodb_db)
                return database
            except Exception as exc:
                last_error = exc
                logger.warning("MongoDB no disponible (intento %s/%s): %s", attempt, attempts, exc)
                if attempt < attempts:
                    time.sleep(1.5)
        if client:
            client.close()
        raise RuntimeError("No se pudo conectar a MongoDB. Verifica MONGODB_URI y la lista de IP en Atlas.") from last_error


def database_from_client(client: Any, db_name: str = "npicado_test") -> Database:
    """Útil para pruebas con mongomock."""
    return Database(client[db_name], client=client)
