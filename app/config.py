from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent

# Las variables reales del proceso siempre tienen prioridad sobre backend/.env.
load_dotenv(BACKEND_DIR / ".env", override=False)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "si", "sí", "on"}


def _env_list(name: str, default: str) -> tuple[str, ...]:
    raw = _env(name, default)
    return tuple(part.strip().rstrip("/") for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    secret_key: str = "npicado-desarrollo-cambia-esta-clave-000000000000"
    access_token_expire_minutes: int = 720

    mongodb_uri: str = "mongodb://127.0.0.1:27017"
    mongodb_db: str = "npicado_ia"

    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    )

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: int = 60
    groq_max_tokens: int = 1200
    groq_temperature: float = 0.2

    first_admin_username: str = "Ismael"
    first_admin_name: str = "Ismael"
    first_admin_password: str = ""

    max_users: int = 5
    max_admins: int = 2
    daily_message_limit: int = 50
    daily_document_limit: int = 8

    max_upload_mb: int = 15
    max_document_chars: int = 1_500_000
    document_retention_days: int = 7
    app_timezone: str = "America/Managua"

    serve_frontend: bool = False

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def max_standard_users(self) -> int:
        return max(0, self.max_users - self.max_admins)

    @property
    def frontend_dist(self) -> Path:
        return PROJECT_DIR / "frontend" / "dist"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            environment=_env("ENVIRONMENT", "development"),
            secret_key=_env("SECRET_KEY", cls.secret_key),
            access_token_expire_minutes=_env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 720),
            mongodb_uri=_env("MONGODB_URI", cls.mongodb_uri),
            mongodb_db=_env("MONGODB_DB", cls.mongodb_db),
            cors_origins=_env_list(
                "CORS_ORIGINS",
                ",".join(cls.cors_origins),
            ),
            groq_api_key=_env("GROQ_API_KEY"),
            groq_model=_env("GROQ_MODEL", cls.groq_model),
            groq_base_url=_env("GROQ_BASE_URL", cls.groq_base_url).rstrip("/"),
            groq_timeout_seconds=_env_int("GROQ_TIMEOUT_SECONDS", 60),
            groq_max_tokens=_env_int("GROQ_MAX_TOKENS", 1200),
            groq_temperature=float(_env("GROQ_TEMPERATURE", "0.2") or "0.2"),
            first_admin_username=_env("FIRST_ADMIN_USERNAME", "Ismael"),
            first_admin_name=_env("FIRST_ADMIN_NAME", "Ismael"),
            first_admin_password=_env("FIRST_ADMIN_PASSWORD"),
            max_users=_env_int("MAX_USERS", 5),
            max_admins=_env_int("MAX_ADMINS", 2),
            daily_message_limit=_env_int("DAILY_MESSAGE_LIMIT", 50),
            daily_document_limit=_env_int("DAILY_DOCUMENT_LIMIT", 8),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", 15),
            max_document_chars=_env_int("MAX_DOCUMENT_CHARS", 1_500_000),
            document_retention_days=_env_int("DOCUMENT_RETENTION_DAYS", 7),
            app_timezone=_env("APP_TIMEZONE", "America/Managua"),
            serve_frontend=_env_bool("SERVE_FRONTEND", False),
        )

    def validate_runtime(self) -> None:
        problems: list[str] = []
        if self.is_production:
            if (
                not self.secret_key
                or "cambia-esta-clave" in self.secret_key
                or len(self.secret_key) < 32
            ):
                problems.append("SECRET_KEY debe ser una clave aleatoria de 32+ caracteres en producción.")
            if not self.mongodb_uri.startswith(("mongodb://", "mongodb+srv://")):
                problems.append("MONGODB_URI no es una URI válida de MongoDB.")
        if self.max_users < 1:
            problems.append("MAX_USERS debe ser mayor que cero.")
        if self.max_admins < 1 or self.max_admins > self.max_users:
            problems.append("MAX_ADMINS debe estar entre 1 y MAX_USERS.")
        if self.daily_message_limit < 0 or self.daily_document_limit < 0:
            problems.append("Los límites diarios no pueden ser negativos.")
        try:
            ZoneInfo(self.app_timezone)
        except ZoneInfoNotFoundError:
            problems.append(
                f"No se encontró la zona horaria {self.app_timezone}. "
                "Instala las dependencias con `pip install -r requirements.txt` "
                "(el paquete tzdata es obligatorio en Windows)."
            )
        if problems:
            raise RuntimeError("Configuración inválida: " + " ".join(problems))
