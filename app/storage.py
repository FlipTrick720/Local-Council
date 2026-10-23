"""Who may read an uploaded file, and how much disk space each user and the whole app may use.

Uploads are stored once per content (app.uploads), but each user who uploaded a file is listed as its owner, and
only owners may read it, attach it to a chat or put it into a knowledge base. Files from before owners were
recorded are open to the users whose own chats or knowledge bases use them.

Limits (config.json, admins only): limits.user_mb per user (their uploads, knowledge bases and chats) and
limits.total_mb for everything together; 0 switches a limit off.
"""
from pathlib import Path

from app import chats, rag, uploads
from app.config import DATA

MB = 2**20
VECTOR_BYTES_PER_CHUNK = 4096  # 1024 float32 dimensions (bge-m3); a rough upper estimate for other models


class StorageError(ValueError):
    pass


def can_read(user: str, upload_id: str) -> bool:
    try:
        m = uploads.meta(upload_id)
    except uploads.UploadError:
        return False
    if "owners" in m:
        return user in m["owners"]
    return upload_id in chats.referenced_uploads(user) | rag.user_documents(user)


def _size(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.exists() else 0


def usage(user: str) -> int:
    """Bytes the user takes: own uploads (each in full, even if others have the same file), knowledge bases, chats."""
    files = sum(m.get("size", 0) for m in uploads.all_meta() if user in m.get("owners", []))
    return files + _size(rag.DATA_DIR / user) + _size(chats.DATA_DIR / user)


def total_usage() -> int:
    return _size(uploads.DATA_DIR) + _size(rag.DATA_DIR) + _size(chats.DATA_DIR)


def check(cfg: dict, user: str, for_user: int, for_total: int) -> None:
    """Raises StorageError if adding that many bytes would exceed a limit."""
    limits = cfg.get("limits", {})
    user_limit, total_limit = limits.get("user_mb", 0) * MB, limits.get("total_mb", 0) * MB
    if user_limit and for_user and usage(user) + for_user > user_limit:
        raise StorageError(f"Dein Speicherplatz ist voll ({limits['user_mb']} MB). Lösche alte Chats, Anhänge "
                           "oder Wissensbasen, oder frag einen Admin nach mehr Platz.")
    if total_limit and for_total and total_usage() + for_total > total_limit:
        raise StorageError(f"Der Speicher der App ist voll ({limits['total_mb']} MB). Bitte einen Admin fragen.")


def check_upload(cfg: dict, user: str, data: bytes) -> None:
    upload_id = uploads.content_id(data)
    try:
        owners = uploads.meta(upload_id).get("owners", [])
        exists = True
    except uploads.UploadError:
        owners, exists = [], False
    check(cfg, user, 0 if user in owners else len(data), 0 if exists else 2 * len(data))  # 2×: file + extracted text


def index_estimate(chars: int) -> int:
    """Disk space a document of that many characters takes in a knowledge base (chunk texts + vectors)."""
    return 2 * chars + (chars // rag.CHUNK_CHARS + 1) * VECTOR_BYTES_PER_CHUNK


def summary(cfg: dict, user: str) -> dict:
    limits = cfg.get("limits", {})
    return {"used": usage(user), "limit": limits.get("user_mb", 0) * MB,
            "total_used": total_usage(), "total_limit": limits.get("total_mb", 0) * MB}
