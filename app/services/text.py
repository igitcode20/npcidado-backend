from __future__ import annotations

import re
import unicodedata
from collections import Counter

SPANISH_STOPWORDS = {
    "a", "al", "algo", "algunas", "alguno", "algunos", "ante", "antes", "aquel",
    "aquella", "aquellas", "aquello", "aquellos", "aqui", "asi", "aun", "bajo",
    "bien", "cada", "casi", "como", "con", "contra", "cual", "cuales", "cualquier",
    "cuando", "cuanto", "de", "del", "demas", "desde", "donde", "dos", "el", "ella",
    "ellas", "ello", "ellos", "en", "entre", "era", "eran", "es", "esa", "esas",
    "ese", "eso", "esos", "esta", "estaba", "estado", "estamos", "estan", "estar",
    "estas", "este", "esto", "estos", "fue", "fueron", "ha", "haber", "habia",
    "han", "hasta", "hay", "la", "las", "le", "les", "lo", "los", "mas", "me",
    "mi", "mis", "mucho", "muy", "no", "nos", "o", "otra", "otras", "otro",
    "otros", "para", "pero", "poco", "por", "porque", "que", "quien", "quienes",
    "se", "sea", "segun", "ser", "si", "sido", "sin", "sobre", "son", "su",
    "sus", "tal", "tambien", "tanto", "te", "tiene", "tienen", "todo", "todos",
    "tu", "tus", "un", "una", "unas", "uno", "unos", "y", "ya", "yo",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def normalize_search_text(value: str) -> str:
    return strip_accents(value.lower()).replace("ñ", "n")


def basic_tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_search_text(value))


def _stem(token: str) -> str:
    """Plural español sencillo; no pretende ser un stemmer lingüístico completo."""
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def content_tokens(value: str) -> list[str]:
    return [
        token
        for token in basic_tokens(value)
        if len(token) >= 2 and token not in SPANISH_STOPWORDS and not token.isdigit()
    ]


def index_terms(value: str) -> list[str]:
    terms: set[str] = set()
    for token in content_tokens(value):
        terms.add(token)
        stemmed = _stem(token)
        if stemmed != token:
            terms.add(stemmed)
    return sorted(terms)


def query_terms(value: str) -> list[str]:
    terms: list[str] = []
    for token in content_tokens(value):
        for variant in (token, _stem(token)):
            if variant and variant not in terms:
                terms.append(variant)
    return terms


def term_frequencies(value: str) -> Counter[str]:
    return Counter(content_tokens(value))


def excerpt(value: str, length: int = 280) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return f"{cut}…"


def clean_display_text(value: str) -> str:
    """Quita controles que rompen XML/PDF, conserva saltos de línea."""
    value = value.replace("\x00", " ")
    value = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def markdown_preview(value: str, length: int = 220) -> str:
    """Texto plano breve para títulos/previews; no intenta renderizar Markdown."""
    text = re.sub(r"```.*?```", " ", value, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    text = re.sub(r"[*_~]{1,3}", "", text)
    return excerpt(text, length)


def chunk_text(text: str, size: int = 1400, overlap: int = 180) -> list[str]:
    text = clean_display_text(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(start + size, text_length)
        if end < text_length:
            search_from = max(start + int(size * 0.55), end - 300)
            window = text[search_from:end]
            candidates = [window.rfind("\n\n"), window.rfind(". "), window.rfind("; ")]
            split_at = max(candidates)
            if split_at > 0:
                end = search_from + split_at + (2 if window[split_at:split_at + 2] in {". ", "; "} else 0)
        if end <= start:
            end = min(start + size, text_length)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break
        next_start = max(0, end - overlap)
        # No empezar en mitad de una palabra.
        while next_start < text_length and text[next_start - 1].isalnum() and text[next_start].isalnum():
            next_start += 1
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


def merge_overlapping_fragments(fragments: list[str]) -> str:
    if not fragments:
        return ""
    merged = fragments[0].strip()
    for fragment in fragments[1:]:
        fragment = fragment.strip()
        if not fragment:
            continue
        max_overlap = min(360, len(merged), len(fragment))
        for overlap_size in range(max_overlap, 24, -1):
            if merged[-overlap_size:].strip() == fragment[:overlap_size].strip():
                merged += fragment[overlap_size:]
                break
        else:
            merged += "\n\n" + fragment
    return merged


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÜÑ¿¡0-9])", text)
    return [part.strip() for part in parts if len(part.strip()) >= 30]


def slugify(value: str, max_length: int = 70) -> str:
    value = strip_accents(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    if not value:
        value = "documento"
    return value[:max_length].rstrip("-")


def safe_filename(value: str, fallback: str = "documento") -> str:
    name = value.replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name)
    name = name.strip(". ")
    return name[:180] or fallback
