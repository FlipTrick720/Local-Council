"""Chat conversations, stored as one JSON file per chat in data/chats/<user>/<id>.json.

A chat looks like {"id", "title", "model", "created", "updated", "messages": [...]}. Each message has at least
"role" and "content"; user messages may have "attachments" (upload metadata, see app.uploads), assistant
messages also carry "model", "thinking", "stats", "error" and "stopped".
Unknown fields are kept as they are, so later features can add their own.
"""
import json
import re
import threading
from datetime import datetime

from app import rag, uploads
from app.config import DATA

DATA_DIR = DATA / "chats"
ID_RE = re.compile(r"^[a-z0-9-]{6,40}$")

_lock = threading.RLock()


class ChatNotFound(KeyError):
    pass


def valid_id(chat_id: str) -> bool:
    return bool(ID_RE.match(chat_id))


def _dir(user: str):
    return DATA_DIR / user


def _path(user: str, chat_id: str):
    if not valid_id(chat_id):
        raise ChatNotFound(chat_id)
    return _dir(user) / f"{chat_id}.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def summary(chat: dict) -> dict:
    return {
        "id": chat["id"],
        "title": chat.get("title") or "Neuer Chat",
        "model": chat.get("model", ""),
        "created": chat.get("created", ""),
        "updated": chat.get("updated", ""),
        "count": len(chat.get("messages", [])),
        "kind": chat.get("kind", "chat"),
    }


def list_all(user: str) -> list[dict]:
    """Summaries of the user's chats, most recently updated first. Broken files are skipped."""
    out = []
    for path in _dir(user).glob("*.json"):
        try:
            out.append(summary(json.loads(path.read_text("utf-8"))))
        except (ValueError, KeyError, OSError):
            continue
    return sorted(out, key=lambda c: c["updated"], reverse=True)


def get(user: str, chat_id: str) -> dict:
    path = _path(user, chat_id)
    if not path.exists():
        raise ChatNotFound(chat_id)
    return json.loads(path.read_text("utf-8"))


def save(user: str, chat_id: str, chat: dict) -> dict:
    path = _path(user, chat_id)
    with _lock:
        created = chat.get("created")
        if not created and path.exists():
            created = json.loads(path.read_text("utf-8")).get("created")
        chat = {**chat, "id": chat_id, "created": created or _now(), "updated": _now()}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(chat, indent=1, ensure_ascii=False) + "\n", "utf-8")
        tmp.replace(path)
    return chat


def patch(user: str, chat_id: str, fields: dict) -> dict:
    """Changes single fields (e.g. the title) without sending the whole chat."""
    with _lock:
        return save(user, chat_id, {**get(user, chat_id), **fields})


def _snippet(text: str, pos: int, length: int, width: int = 40) -> str:
    start, end = max(0, pos - width), min(len(text), pos + length + width)
    snippet = " ".join(text[start:end].split())
    return ("…" if start else "") + snippet + ("…" if end < len(text) else "")


def search(user: str, query: str, limit: int = 50) -> list[dict]:
    """Chats whose title, messages or attachment names contain `query` (case-insensitive), newest first.
    Each result carries up to three hits with the message index and a text snippet around the match."""
    q = query.lower().strip()
    if not q:
        return []
    results = []
    for path in _dir(user).glob("*.json"):
        try:
            chat = json.loads(path.read_text("utf-8"))
        except (ValueError, OSError):
            continue
        hits = []
        for i, msg in enumerate(chat.get("messages", [])):
            text = msg.get("content") or ""
            pos = text.lower().find(q)
            if pos >= 0:
                hits.append({"index": i, "role": msg["role"], "snippet": _snippet(text, pos, len(q))})
                continue
            names = " ".join(a.get("name", "") for a in msg.get("attachments") or [])
            if q in names.lower():
                hits.append({"index": i, "role": msg["role"], "snippet": f"📎 {names}"})
        if hits or q in (chat.get("title") or "").lower():
            results.append({**summary(chat), "hits": hits[:3], "hit_count": len(hits)})
    return sorted(results, key=lambda c: c["updated"], reverse=True)[:limit]


def delete(user: str, chat_id: str) -> None:
    path = _path(user, chat_id)
    with _lock:
        if not path.exists():
            raise ChatNotFound(chat_id)
        path.unlink()
        uploads.collect_garbage(referenced_uploads() | rag.referenced_uploads())


def referenced_uploads(user: str | None = None) -> set[str]:
    """Ids of all files attached to messages in the user's chats (without a user: in everyone's)."""
    ids = set()
    for path in DATA_DIR.glob(f"{user}/*.json" if user else "*/*.json"):
        try:
            for msg in json.loads(path.read_text("utf-8")).get("messages", []):
                ids.update(a["id"] for a in msg.get("attachments") or [] if isinstance(a, dict) and "id" in a)
        except (ValueError, OSError):
            continue
    return ids
