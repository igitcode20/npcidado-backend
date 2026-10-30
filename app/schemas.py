from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _clean_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Este campo es obligatorio.")
    return value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str:
        return _clean_required(value)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        return _clean_required(value)


class ConversationRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=90)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return _clean_required(value)


class GenerateRequest(BaseModel):
    tema: str = Field(min_length=3, max_length=180)
    formato: Literal["docx", "pdf", "xlsx", "txt"]
    instrucciones: str = Field(default="", max_length=2000)
    fuentes: int = Field(default=8, ge=3, le=12)

    @field_validator("tema")
    @classmethod
    def clean_topic(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("instrucciones")
    @classmethod
    def clean_instructions(cls, value: str) -> str:
        return value.strip()


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    full_name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "user"] = "user"


class AdminUserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=80)
    role: Literal["admin", "user"] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
