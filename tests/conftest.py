from __future__ import annotations

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.main import create_app


class FakeGroq:
    configured = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, *, question, context, history, user_name):
        self.calls.append(
            {
                "question": question,
                "context": context,
                "history": history,
                "user_name": user_name,
            }
        )
        return {
            "content": "Según la biblioteca, la bienvenida incluye completar el formulario [1].",
            "model": "fake-groq",
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        }


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        environment="test",
        secret_key="test-secret-key-for-npicado-ia-0123456789abcdef",
        mongodb_uri="mongodb://localhost:27017",
        mongodb_db="npicado_test",
        groq_api_key="gsk_test",
        first_admin_username="Ismael",
        first_admin_name="Ismael",
        first_admin_password="TestAdmin123!",
        max_users=5,
        max_admins=2,
        daily_message_limit=2,
        daily_document_limit=1,
        app_timezone="America/Managua",
    )


@pytest.fixture
def database() -> Database:
    client = mongomock.MongoClient()
    return Database(client["npicado_test"], client=client)


@pytest.fixture
def fake_groq() -> FakeGroq:
    return FakeGroq()


@pytest.fixture
def client(test_settings, database, fake_groq):
    app = create_app(test_settings, database=database, groq_service=fake_groq)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_headers(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "Ismael", "password": "TestAdmin123!"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
