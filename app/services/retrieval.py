from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from bson import ObjectId

from ..database import Database
from .text import (
    content_tokens,
    excerpt,
    index_terms,
    normalize_search_text,
    query_terms,
    term_frequencies,
)

_OVERVIEW_WORDS = {
    "ayuda", "ayudame", "biblioteca", "cita", "citando", "clave", "claves",
    "contenido", "cuenta", "cuentame", "dame", "di", "dime", "documento",
    "documentos", "explica", "explicame", "fuente", "fuentes", "general",
    "habla", "hablan", "hacer", "importante", "importantes", "informacion",
    "muestra", "muestrame", "puedes", "podrias", "punto", "puntos", "resume",
    "resumen", "resumir", "tema", "temas", "tengo", "todo", "todos", "trata",
    "tratan",
}


def _is_overview_query(query: str) -> bool:
    words = set(content_tokens(query))
    if not words or not (words & _OVERVIEW_WORDS):
        return False
    return not (words - _OVERVIEW_WORDS)


def _overview_sources(database: Database, limit: int) -> list[dict[str, Any]]:
    """Para preguntas tipo «¿qué documentos tengo?» usa la portada de cada documento."""
    documents = list(
        database.documents.find({}, {"title": 1, "filename": 1}).sort("uploaded_at", -1).limit(limit)
    )
    sources: list[dict[str, Any]] = []
    for document in documents:
        chunk = database.chunks.find_one(
            {"document_id": document["_id"], "chunk_index": 0},
            {"content": 1, "chunk_index": 1, "title": 1, "filename": 1},
        )
        if not chunk:
            continue
        sources.append(
            {
                "document_id": document["_id"],
                "title": document.get("title") or chunk.get("title") or "Documento",
                "filename": document.get("filename") or chunk.get("filename", ""),
                "chunk_index": 0,
                "content": chunk.get("content", ""),
                "excerpt": excerpt(chunk.get("content", ""), 300),
                "score": 0.5,
            }
        )
    return sources


def retrieve_sources(
    database: Database,
    query: str,
    *,
    limit: int = 6,
    max_per_document: int = 2,
) -> list[dict[str, Any]]:
    """Recuperación léxica local, sin embeddings ni tokens de IA.

    Indexa términos (incluyendo plurales simples) y puntúa coincidencias de
    cobertura, frecuencia y título. Para el volumen de NPICADO IA es rápido,
    determinista y no necesita servicios externos.
    """
    terms = query_terms(query)
    if not terms:
        return []

    normalized_query = normalize_search_text(query)
    candidates = list(
        database.chunks.find(
            {"terms": {"$in": terms}},
            {
                "content": 1,
                "document_id": 1,
                "chunk_index": 1,
                "title": 1,
                "filename": 1,
                "terms": 1,
                "_id": 0,
            },
        ).limit(700)
    )
    if not candidates:
        if _is_overview_query(query):
            return _overview_sources(database, limit)
        return []

    query_set = set(terms)
    scored: list[dict[str, Any]] = []
    for chunk in candidates:
        content = chunk.get("content", "")
        title = chunk.get("title", "")
        content_terms = set(chunk.get("terms") or [])
        title_terms = set(index_terms(title))
        matched = query_set & content_terms
        if not matched:
            continue
        frequencies = term_frequencies(f"{title} {content}")
        coverage = len(matched) / max(1, len(query_set))
        raw_score = coverage * 7.0
        for term in matched:
            raw_score += min(2.2, 0.7 + math.log1p(frequencies.get(term, 0)) * 0.45)
        title_matches = len(query_set & title_terms)
        raw_score += title_matches * 2.4
        if normalized_query and len(normalized_query) >= 8 and normalized_query in normalize_search_text(content):
            raw_score += 3.0
        if chunk.get("chunk_index") == 0:
            raw_score += 0.25
        scored.append(
            {
                "document_id": chunk["document_id"],
                "title": title or "Documento",
                "filename": chunk.get("filename", ""),
                "chunk_index": int(chunk.get("chunk_index", 0)),
                "content": content,
                "score_raw": raw_score,
                "excerpt": excerpt(content, 300),
            }
        )

    scored.sort(key=lambda item: (item["score_raw"], -item["chunk_index"]), reverse=True)

    per_document: dict[ObjectId, int] = defaultdict(int)
    selected: list[dict[str, Any]] = []
    for item in scored:
        doc_id = item["document_id"]
        if per_document[doc_id] >= max_per_document:
            continue
        per_document[doc_id] += 1
        selected.append(item)
        if len(selected) >= limit:
            break

    if not selected:
        return []
    maximum = max(item["score_raw"] for item in selected) or 1.0
    for item in selected:
        item["score"] = round(min(1.0, 0.35 + (item["score_raw"] / maximum) * 0.65), 4)
        item.pop("score_raw", None)
    return selected


def build_context(sources: list[dict[str, Any]], max_chars: int = 8500) -> str:
    if not sources:
        return "(La biblioteca no devolvió fragmentos relacionados con la consulta.)"
    blocks: list[str] = []
    total = 0
    for index, source in enumerate(sources, start=1):
        block = (
            f"[FUENTE {index}] {source.get('title', 'Documento')} "
            f"(archivo: {source.get('filename', '')}, fragmento {int(source.get('chunk_index', 0)) + 1})\n"
            f"{source.get('content', '').strip()}"
        )
        if total + len(block) > max_chars:
            remaining = max_chars - total
            if remaining > 400:
                blocks.append(block[:remaining])
            break
        blocks.append(block)
        total += len(block)
    return "\n\n---\n\n".join(blocks)


def public_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from ..serializers import public_source

    return [public_source(source) for source in sources]
