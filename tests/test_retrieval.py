from __future__ import annotations

from bson import ObjectId

from app.bootstrap import utcnow
from app.services.retrieval import retrieve_sources


def _seed_document(database, title: str, content: str):
    document_id = ObjectId()
    database.documents.insert_one(
        {
            "_id": document_id,
            "title": title,
            "filename": f"{title.lower().replace(' ', '_')}.txt",
            "uploaded_at": utcnow(),
        }
    )
    database.chunks.insert_one(
        {
            "document_id": document_id,
            "chunk_index": 0,
            "content": content,
            "title": title,
            "filename": f"{title.lower().replace(' ', '_')}.txt",
            "terms": ["manual" if "Manual" in title else "politica", "prueba"],
            "created_at": utcnow(),
        }
    )
    return document_id


def test_overview_question_returns_library_samples(database) -> None:
    _seed_document(database, "Manual de bienvenida", "Contenido del manual de bienvenida.")
    _seed_document(database, "Políticas internas", "Contenido de las políticas internas.")

    sources = retrieve_sources(database, "¿Qué documentos tengo y de qué tratan?", limit=6)
    assert len(sources) == 2
    assert {source["title"] for source in sources} == {
        "Manual de bienvenida",
        "Políticas internas",
    }


def test_specific_question_without_matches_returns_nothing(database) -> None:
    _seed_document(database, "Manual de bienvenida", "Contenido del manual de bienvenida.")
    sources = retrieve_sources(database, "¿Cuál es la política de criptomonedas?", limit=6)
    assert sources == []


def test_specific_question_finds_document_by_content(database) -> None:
    document_id = _seed_document(
        database,
        "Manual de bienvenida",
        "Las vacaciones se solicitan con quince días de anticipación.",
    )
    database.chunks.update_one(
        {"document_id": document_id},
        {"$set": {"terms": ["vacacione", "vacaciones", "solicitan", "quince", "dia"]}},
    )
    sources = retrieve_sources(database, "¿Cómo solicito vacaciones?", limit=6)
    assert len(sources) == 1
    assert sources[0]["document_id"] == document_id
    assert sources[0]["score"] > 0
