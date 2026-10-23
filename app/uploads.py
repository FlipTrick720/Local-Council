"""Files attached to chat messages, stored once in data/uploads/ and referenced from chats by id.

Per upload: <id>.json (metadata), <id><ext> (the original file) and, for text files and PDFs, <id>.txt with the
extracted text. Images are sent to vision models as they are; text is put into the message for the model.
The id is a hash of the content, so the same file uploaded twice is stored once. The metadata lists the users
who uploaded it ("owners"); only they may read it (see app.storage).
"""
import base64
import hashlib
import io
import json
import re
import time
from pathlib import Path

from app.config import DATA

DATA_DIR = DATA / "uploads"
MAX_BYTES = 20 * 1024 * 1024
MAX_CHARS = 500_000  # longer texts are cut; no local model has a context anywhere near this
ID_RE = re.compile(r"^[0-9a-f]{24}$")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


class UploadError(ValueError):
    pass


def _path(upload_id: str, suffix: str) -> Path:
    if not ID_RE.match(upload_id):
        raise UploadError("Ungültige Datei-ID.")
    return DATA_DIR / f"{upload_id}{suffix}"


def _decode(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise UploadError("Dateityp wird nicht unterstützt. Möglich sind Textdateien, PDFs und Bilder.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _pdf_text(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise UploadError("Das PDF ist passwortgeschützt.")
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as e:
        raise UploadError(f"PDF konnte nicht gelesen werden: {e}") from e
    text = "\n\n".join(f"[Seite {i}]\n{p.strip()}" for i, p in enumerate(pages, 1) if p.strip())
    if not text:
        raise UploadError("Das PDF enthält keinen auslesbaren Text (vermutlich eingescannt).")
    return text, len(pages)


def content_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:24]


def store(name: str, data: bytes, content_type: str = "", owner: str = "") -> dict:
    if not data:
        raise UploadError("Die Datei ist leer.")
    if len(data) > MAX_BYTES:
        raise UploadError(f"Die Datei ist größer als {MAX_BYTES // 2**20} MB.")
    name = Path(name or "datei").name[:200]
    ext = Path(name).suffix.lower()
    upload_id = content_id(data)
    owners = set(_owners(upload_id)) | ({owner} if owner else set())
    meta = {"id": upload_id, "name": name, "size": len(data), "created": time.time(), "owners": sorted(owners)}

    text = None
    if ext in IMAGE_TYPES or content_type.startswith("image/"):
        if ext not in IMAGE_TYPES:
            raise UploadError("Bildformat wird nicht unterstützt (PNG, JPEG, WebP oder GIF).")
        meta.update(kind="image", type=IMAGE_TYPES[ext])
    elif ext == ".zip":  # (not by content: .docx/.xlsx are ZIP files too)
        meta.update(kind="archive", type="application/zip")  # source code for knowledge bases (app.codechunks)
    elif ext == ".pdf" or data[:5] == b"%PDF-":
        text, pages = _pdf_text(data)
        meta.update(kind="text", type="application/pdf", pages=pages)
    else:
        text = _decode(data)
        meta.update(kind="text", type="text/plain")

    if text is not None:
        meta["truncated"] = len(text) > MAX_CHARS
        text = text[:MAX_CHARS]
        meta["chars"] = len(text)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path(upload_id, ext or ".bin").write_bytes(data)
    if text is not None:
        _path(upload_id, ".txt").write_text(text, "utf-8")
    meta["file"] = upload_id + (ext or ".bin")
    _write_meta(meta)
    return public(meta)


def public(meta: dict) -> dict:
    """The metadata without the owners: one user must not learn who else has the same file."""
    return {k: v for k, v in meta.items() if k != "owners"}


def _write_meta(meta: dict) -> None:
    tmp = _path(meta["id"], ".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    tmp.replace(_path(meta["id"], ".json"))


def _owners(upload_id: str) -> list[str]:
    try:
        return meta(upload_id).get("owners", [])
    except (UploadError, ValueError, OSError):
        return []


def all_meta() -> list[dict]:
    out = []
    for path in DATA_DIR.glob("*.json"):
        try:
            out.append(json.loads(path.read_text("utf-8")))
        except (ValueError, OSError):
            continue
    return out


def forget_owner(user: str) -> None:
    """Called when an account is deleted; files nobody owns any more are then removed by collect_garbage."""
    for m in all_meta():
        if user in m.get("owners", []):
            m["owners"].remove(user)
            _write_meta(m)


def meta(upload_id: str) -> dict:
    path = _path(upload_id, ".json")
    if not path.exists():
        raise UploadError("Datei nicht gefunden.")
    return json.loads(path.read_text("utf-8"))


def text(upload_id: str) -> str:
    """The extracted text of a text file or PDF."""
    meta(upload_id)
    return _path(upload_id, ".txt").read_text("utf-8")


def file_path(upload_id: str) -> Path:
    return DATA_DIR / meta(upload_id)["file"]


def expand(message: dict) -> dict:
    """Turns {"role", "content", "attachments": [ids]} into an Ollama message: text files go before the
    user's text, images into "images" (base64)."""
    blocks, images = [], []
    for upload_id in message.get("attachments") or []:
        try:
            m = meta(upload_id)
            if m["kind"] == "image":
                images.append(base64.b64encode(file_path(upload_id).read_bytes()).decode())
            elif m["kind"] == "archive":
                blocks.append(f"[{m['name']}: ZIP-Datei; nur für Wissensbasen, das Modell sieht den Inhalt nicht.]")
            else:
                content = text(upload_id)
                note = " (gekürzt)" if m.get("truncated") else ""
                blocks.append(f"--- Datei: {m['name']}{note} ---\n{content}\n--- Ende von {m['name']} ---")
        except (UploadError, OSError):
            blocks.append(f"[Die angehängte Datei {upload_id} ist nicht mehr vorhanden.]")
    out = {"role": message["role"], "content": "\n\n".join([*blocks, message["content"]]) if blocks else message["content"]}
    if images:
        out["images"] = images
    return out


def collect_garbage(referenced: set[str], min_age: float = 86400) -> int:
    """Deletes uploads no chat refers to any more. Recent ones are kept: they may still sit in the input box,
    unless their owners' accounts are gone."""
    removed = 0
    for path in DATA_DIR.glob("*.json"):
        upload_id = path.stem
        if upload_id in referenced:
            continue
        try:
            m = json.loads(path.read_text("utf-8"))
            if m.get("owners", True) and time.time() - m.get("created", 0) < min_age:
                continue
        except (ValueError, OSError):
            pass
        for f in DATA_DIR.glob(f"{upload_id}.*"):
            f.unlink(missing_ok=True)
        removed += 1
    return removed
