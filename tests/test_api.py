from __future__ import annotations

from typing import Any

KNOWLEDGE_TEXT = """Bienvenida a NPICADO IA

NPICADO IA es la biblioteca privada de conocimiento de la organización. Su objetivo es
centralizar documentos y permitir consultas inteligentes con fuentes verificables.

Proceso de bienvenida:
1. Completar el formulario de ingreso durante los primeros tres días.
2. Revisar el manual de convivencia y las políticas de seguridad.
3. Solicitar la activación del correo corporativo al equipo de tecnología.

Las vacaciones se solicitan con quince días de calendario de anticipación.
"""


def _login(client, username: str, password: str) -> dict[str, Any]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _create_user(client, headers, username: str, role: str = "user", password: str = "ClaveSegura123") -> dict[str, Any]:
    response = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "username": username,
            "full_name": f"Usuario {username}",
            "password": password,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client, headers) -> dict[str, Any]:
    response = client.post(
        "/api/documents",
        headers=headers,
        files={"file": ("bienvenida.txt", KNOWLEDGE_TEXT.encode("utf-8"), "text/plain")},
        data={"title": "Manual de bienvenida", "tags": "inducción, personal"},
    )
    assert response.status_code == 201, response.text
    return response.json()["document"]


def test_seed_login_and_health(client) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200, health.text
    assert health.json()["database"] == "connected"

    login = _login(client, "Ismael", "TestAdmin123!")
    assert login["user"]["role"] == "admin"
    assert login["user"]["username"] == "Ismael"

    bad = client.post("/api/auth/login", json={"username": "Ismael", "password": "incorrecta"})
    assert bad.status_code == 401

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {login['access_token']}"})
    assert me.status_code == 200
    assert me.json()["usage"]["unlimited"] is True


def test_admin_user_management_and_limits(client, admin_headers) -> None:
    _create_user(client, admin_headers, "SegundoAdmin", role="admin")
    _create_user(client, admin_headers, "LectorUno")
    _create_user(client, admin_headers, "LectorDos")
    _create_user(client, admin_headers, "LectorTres")

    sixth = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": "Sexto",
            "full_name": "Usuario Sexto",
            "password": "ClaveSegura123",
            "role": "user",
        },
    )
    assert sixth.status_code == 409

    user_login = _login(client, "LectorUno", "ClaveSegura123")
    user_headers = {"Authorization": f"Bearer {user_login['access_token']}"}
    forbidden = client.get("/api/admin/users", headers=user_headers)
    assert forbidden.status_code == 403

    users = client.get("/api/admin/users", headers=admin_headers)
    assert users.status_code == 200
    assert users.json()["total"] == 5
    assert users.json()["slots_available"] == 0


def test_plan_requires_second_admin_before_fourth_standard_user(client, admin_headers) -> None:
    _create_user(client, admin_headers, "EstandarUno")
    _create_user(client, admin_headers, "EstandarDos")
    _create_user(client, admin_headers, "EstandarTres")

    fourth_user = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": "EstandarCuatro",
            "full_name": "Usuario Cuatro",
            "password": "ClaveSegura123",
            "role": "user",
        },
    )
    assert fourth_user.status_code == 409
    assert "usuarios estándar" in fourth_user.json()["detail"]

    second_admin = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": "SegundoAdmin",
            "full_name": "Segunda Administradora",
            "password": "ClaveSegura123",
            "role": "admin",
        },
    )
    assert second_admin.status_code == 201, second_admin.text


def test_upload_chat_sources_and_message_quota(client, admin_headers, fake_groq) -> None:
    document = _upload(client, admin_headers)
    assert document["chunk_count"] >= 1
    assert document["title"] == "Manual de bienvenida"

    # Un usuario normal
    _create_user(client, admin_headers, "Lector")
    user_login = _login(client, "Lector", "ClaveSegura123")
    headers = {"Authorization": f"Bearer {user_login['access_token']}"}

    library = client.get("/api/documents", headers=headers)
    assert library.status_code == 200
    assert library.json()["total"] == 1

    first = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "¿Qué debo hacer en mi bienvenida?"},
    )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["message"]["role"] == "assistant"
    assert payload["sources"]
    assert "formulario" in fake_groq.calls[0]["context"]
    conversation_id = payload["conversation"]["id"]

    second = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "¿Y las vacaciones?", "conversation_id": conversation_id},
    )
    assert second.status_code == 200, second.text

    usage = client.get("/api/usage", headers=headers)
    assert usage.status_code == 200
    assert usage.json()["messages"]["used"] == 2
    assert usage.json()["messages"]["remaining"] == 0

    blocked = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "Una tercera pregunta"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "quota_messages"

    conversations = client.get("/api/chat/conversations", headers=headers)
    assert conversations.status_code == 200
    assert len(conversations.json()["items"]) == 1
    history = client.get(
        f"/api/chat/conversations/{conversation_id}/messages", headers=headers
    )
    assert history.status_code == 200
    assert len(history.json()["items"]) == 4


def test_generate_download_and_document_quota(client, admin_headers) -> None:
    _upload(client, admin_headers)
    _create_user(client, admin_headers, "Creador")
    login = _login(client, "Creador", "ClaveSegura123")
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    response = client.post(
        "/api/generate",
        headers=headers,
        json={
            "tema": "Proceso de bienvenida",
            "formato": "docx",
            "instrucciones": "Incluir pasos y vacaciones",
            "fuentes": 5,
        },
    )
    assert response.status_code == 201, response.text
    generated = response.json()["document"]
    assert generated["formato"] == "docx"
    assert generated["ai_tokens_used"] == 0
    assert generated["sources_count"] >= 1

    download = client.get(f"/api/generated/{generated['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.content.startswith(b"PK")
    assert "attachment" in download.headers["content-disposition"]

    history = client.get("/api/generated", headers=headers)
    assert history.status_code == 200
    assert len(history.json()["items"]) == 1

    second = client.post(
        "/api/generate",
        headers=headers,
        json={"tema": "Proceso de bienvenida", "formato": "txt"},
    )
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "quota_documents"

    # Un admin no tiene límite de documentos.
    admin_generate = client.post(
        "/api/generate",
        headers=admin_headers,
        json={"tema": "Proceso de bienvenida", "formato": "pdf"},
    )
    assert admin_generate.status_code == 201, admin_generate.text

    stats = client.get("/api/admin/stats", headers=admin_headers)
    assert stats.status_code == 200
    assert stats.json()["knowledge"]["documents"] == 1
    assert stats.json()["today"]["documents_generated"] >= 2


def test_password_change_and_deactivation(client, admin_headers) -> None:
    created = _create_user(client, admin_headers, "Temporal")
    user_id = created["user"]["id"]
    login = _login(client, "Temporal", "ClaveSegura123")
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "ClaveSegura123", "new_password": "NuevaClave456"},
    )
    assert changed.status_code == 200, changed.text
    new_token = changed.json()["access_token"]
    session_check = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {new_token}"}
    )
    assert session_check.status_code == 200
    old_session = client.get("/api/auth/me", headers=headers)
    assert old_session.status_code == 401
    _login(client, "Temporal", "NuevaClave456")

    deactivated = client.patch(
        f"/api/admin/users/{user_id}",
        headers=admin_headers,
        json={"active": False},
    )
    assert deactivated.status_code == 200, deactivated.text
    blocked = client.post(
        "/api/auth/login",
        json={"username": "Temporal", "password": "NuevaClave456"},
    )
    assert blocked.status_code == 403
