"""Knowledge bases for RAG: named collections of documents, split into chunks and embedded with a local Ollama
embedding model. A chat that uses a knowledge base gets the best-matching chunks added to the question.

Every user has their own knowledge bases. Stored in data/rag/<user>/<collection id>/:
  collection.json     {"id", "name", "embed_model", "created", "docs": [{"id", "name", "chunks", "pages"}]}
  <upload id>.json    the chunk texts with their page numbers
  <upload id>.npy     the normalized embedding vectors, one row per chunk
Documents come from app.uploads (text files, PDFs, and ZIP files with source code) or, for code, from a folder on
the server (admins, see app.codechunks); code is split along functions and classes.
"""
import hashlib
import json
import math
import re
import secrets
import shutil
import threading
import time
from typing import AsyncIterator

import numpy as np

from app import codechunks, uploads
from app.config import DATA
from app.ollama import OllamaClient

DATA_DIR = DATA / "rag"
ID_RE = re.compile(r"^kb-[0-9a-f]{8}$")
CHUNK_CHARS = 1200
OVERLAP = 200  # characters repeated when a long paragraph has to be cut
BATCH = 16  # chunks per embedding request
KEYWORD_WEIGHT = 0.3  # how much exact word matches add to the embedding similarity

_lock = threading.RLock()
_cache: dict[tuple, tuple] = {}  # (user, collection id) → (file signature, vectors, chunks)


class RagError(ValueError):
    pass


def _dir(user: str, cid: str):
    if not ID_RE.match(cid):
        raise RagError("Ungültige Wissensbasis.")
    return DATA_DIR / user / cid


def _write_json(path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


def get(user: str, cid: str) -> dict:
    path = _dir(user, cid) / "collection.json"
    if not path.exists():
        raise RagError("Wissensbasis nicht gefunden.")
    return json.loads(path.read_text("utf-8"))


def list_all(user: str) -> list[dict]:
    return _collections(f"{user}/kb-*/collection.json")


def _collections(pattern: str) -> list[dict]:
    out = []
    for path in DATA_DIR.glob(pattern):
        try:
            out.append(json.loads(path.read_text("utf-8")))
        except (ValueError, OSError):
            continue
    return sorted(out, key=lambda c: c["name"].lower())


def create(user: str, name: str, embed_model: str) -> dict:
    cid = f"kb-{secrets.token_hex(4)}"
    collection = {"id": cid, "name": name.strip()[:80], "embed_model": embed_model, "created": time.time(), "docs": []}
    _dir(user, cid).mkdir(parents=True)
    _write_json(_dir(user, cid) / "collection.json", collection)
    return collection


def delete(user: str, cid: str) -> None:
    with _lock:
        get(user, cid)
        shutil.rmtree(_dir(user, cid), ignore_errors=True)
        _cache.pop((user, cid), None)


def remove_document(user: str, cid: str, upload_id: str) -> dict:
    if not uploads.ID_RE.match(upload_id):
        raise RagError("Dokument nicht gefunden.")
    with _lock:
        collection = get(user, cid)
        collection["docs"] = [d for d in collection["docs"] if d["id"] != upload_id]
        for suffix in (".json", ".npy"):
            (_dir(user, cid) / f"{upload_id}{suffix}").unlink(missing_ok=True)
        _write_json(_dir(user, cid) / "collection.json", collection)
        return collection


def user_documents(user: str) -> set[str]:
    """Ids of the uploads in the user's knowledge bases."""
    return {d["id"] for c in list_all(user) for d in c["docs"]}


def referenced_uploads() -> set[str]:
    """Ids of all documents in any user's knowledge bases."""
    return {d["id"] for c in _collections("*/kb-*/collection.json") for d in c["docs"]}


# ---------- Chunking ----------

def chunk(text: str) -> list[dict]:
    """Splits text into pieces of about CHUNK_CHARS characters at paragraph ends; short paragraphs are joined,
    long ones are cut at sentence ends with some overlap. PDF text carries "[Seite n]" markers (see
    app.uploads), which become page numbers."""
    pieces: list[tuple[str, int | None]] = []  # (paragraph, page)
    page = None
    for part in re.split(r"(\[Seite \d+\])", text):
        marker = re.fullmatch(r"\[Seite (\d+)\]", part)
        if marker:
            page = int(marker.group(1))
            continue
        for para in re.split(r"\n\s*\n", part):
            para = " ".join(para.split())
            while len(para) > CHUNK_CHARS:  # very long paragraph: cut at a sentence end if possible
                cut = para.rfind(". ", 0, CHUNK_CHARS)
                cut = cut + 1 if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
                pieces.append((para[:cut], page))
                back = para.find(" ", cut - OVERLAP)  # the next piece repeats the end of this one
                para = para[back if 0 <= back < cut else cut:].strip()
            if para:
                pieces.append((para, page))

    chunks, current, current_page = [], "", None
    for para, para_page in pieces:
        if current and len(current) + len(para) + 1 > CHUNK_CHARS:
            chunks.append({"text": current, "page": current_page})
            current = ""
        if not current:
            current_page = para_page
        current = f"{current} {para}".strip()
    if current:
        chunks.append({"text": current, "page": current_page})
    return chunks


def _normalize(vectors) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


# ---------- Indexing and search ----------

async def add_document(client: OllamaClient, user: str, cid: str, upload_id: str) -> AsyncIterator[dict]:
    """Chunks and embeds an uploaded document (or the source code in a ZIP file). Yields progress events, then the
    updated collection."""
    meta = uploads.meta(upload_id)
    if meta["kind"] == "archive":
        try:
            files = codechunks.from_zip(uploads.file_path(upload_id).read_bytes())
        except codechunks.CodeError as e:
            raise RagError(str(e)) from e
        chunks, pages = codechunks.chunks(files), None
    elif meta["kind"] == "text":
        chunks, pages = chunk(uploads.text(upload_id)), meta.get("pages")
    else:
        raise RagError("Nur Textdateien, PDFs und ZIP-Dateien mit Quelltext können in eine Wissensbasis.")
    async for event in _index(client, user, cid, upload_id, meta["name"], chunks, pages):
        yield event


async def add_folder(client: OllamaClient, user: str, cid: str, folder: str, roots: list[str]) -> AsyncIterator[dict]:
    """Indexes the source code in a folder on the server (inside code.roots). Indexing it again replaces it."""
    try:
        path, files = codechunks.from_folder(folder, roots)
    except codechunks.CodeError as e:
        raise RagError(str(e)) from e
    doc_id = hashlib.sha256(path.encode()).hexdigest()[:24]  # stable, so re-indexing replaces the old version
    async for event in _index(client, user, cid, doc_id, f"📁 {path}", codechunks.chunks(files), None):
        yield event


async def _index(client: OllamaClient, user: str, cid: str, doc_id: str, name: str, chunks: list[dict],
                 pages: int | None) -> AsyncIterator[dict]:
    collection = get(user, cid)
    if not chunks:
        raise RagError("Die Datei enthält keinen Text.")
    vectors = []
    for start in range(0, len(chunks), BATCH):
        batch = chunks[start:start + BATCH]
        vectors += await client.embed(collection["embed_model"], [c["text"] for c in batch])
        yield {"type": "progress", "done": start + len(batch), "total": len(chunks)}

    with _lock:
        collection = get(user, cid)
        folder = _dir(user, cid)
        np.save(folder / f"{doc_id}.npy", _normalize(vectors))
        _write_json(folder / f"{doc_id}.json", chunks)
        doc = {"id": doc_id, "name": name, "chunks": len(chunks), "pages": pages}
        collection["docs"] = [d for d in collection["docs"] if d["id"] != doc_id] + [doc]
        _write_json(folder / "collection.json", collection)
    yield {"type": "done", "collection": collection}


def _load(user: str, cid: str) -> tuple[np.ndarray, list[dict]]:
    """All vectors and chunks of a collection, cached until a file changes."""
    collection = get(user, cid)
    folder = _dir(user, cid)
    files = [folder / f"{d['id']}.npy" for d in collection["docs"]]
    signature = tuple((f.name, f.stat().st_mtime) for f in files if f.exists())
    cached = _cache.get((user, cid))
    if cached and cached[0] == signature:
        return cached[1], cached[2]
    matrices, chunks = [], []
    for doc in collection["docs"]:
        try:
            matrix = np.load(folder / f"{doc['id']}.npy")
            texts = json.loads((folder / f"{doc['id']}.json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        matrices.append(matrix)
        chunks += [{"name": doc["name"], **c} for c in texts]  # code chunks bring their own "path:lines" name
    vectors = np.vstack(matrices) if matrices else np.zeros((0, 1), dtype=np.float32)
    _cache[(user, cid)] = (signature, vectors, chunks)
    return vectors, chunks


async def search(client: OllamaClient, user: str, cid: str, query: str, top_k: int = 5) -> list[dict]:
    collection = get(user, cid)
    vectors, chunks = _load(user, cid)
    if not chunks:
        return []
    query_vector = _normalize(await client.embed(collection["embed_model"], [query]))[0]
    scores = vectors @ query_vector + KEYWORD_WEIGHT * _keyword_scores(query, chunks)
    best = np.argsort(-scores)[:top_k]
    return [{**chunks[i], "score": round(float(scores[i]), 3)} for i in best]


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w{3,}", text.lower()))


def _keyword_scores(query: str, chunks: list[dict]) -> np.ndarray:
    """Share of the query's words (weighted by rarity, IDF) that occur in each chunk. Embeddings miss exact
    matches like error codes, part numbers or names; this brings them up."""
    terms = _words(query)
    if not terms:
        return np.zeros(len(chunks), dtype=np.float32)
    chunk_words = [_words(c["text"]) for c in chunks]
    n = len(chunks)
    idf = {t: math.log((n + 1) / (1 + sum(t in w for w in chunk_words))) for t in terms}
    total = sum(idf.values()) or 1
    return np.array([sum(idf[t] for t in terms if t in w) / total for w in chunk_words], dtype=np.float32)


def context_block(collection_name: str, hits: list[dict]) -> str:
    """The excerpts as they are put in front of the user's question."""
    parts = []
    for n, hit in enumerate(hits, 1):
        where = f"{hit['name']}, S. {hit['page']}" if hit.get("page") else hit["name"]
        parts.append(f"[{n}] ({where})\n{hit['text']}")
    return (
        f"Excerpts from the knowledge base \"{collection_name}\" that may help to answer the question below. "
        "Use them if relevant and cite them as [1], [2], … If they do not contain the answer, say so and answer "
        "from your own knowledge only if you are sure.\n\n" + "\n\n".join(parts)
    )
