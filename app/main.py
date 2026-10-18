"""FastAPI app: JSON/NDJSON API under /api, static frontend under /."""
import json
from typing import AsyncIterator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config, history
from app.config import ROOT
from app.modes import chat, council, speedtest
from app.ollama import OllamaClient, OllamaError

app = FastAPI(title="Local Council")


class SpeedtestRequest(BaseModel):
    prompt: str = Field(min_length=1)
    models: list[str] | None = None
    parallel: bool | None = None


class CouncilRequest(BaseModel):
    question: str = Field(min_length=1)
    members: list[str] | None = None
    synthesizer: str | None = None
    rounds: int | None = Field(default=None, ge=0, le=5)
    parallel: bool | None = None


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    model: str | None = None
    system: str | None = None


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


@app.get("/api/config")
def get_config():
    return config.load()


@app.put("/api/config")
def put_config(cfg: dict):
    return config.save(cfg)


@app.get("/api/models")
async def get_models():
    try:
        return await OllamaClient.from_config(config.load()).list_models()
    except OllamaError as e:
        raise HTTPException(502, str(e))


@app.post("/api/speedtest")
def post_speedtest(req: SpeedtestRequest):
    cfg = config.load()
    models = _unique(req.models if req.models is not None else cfg["speedtest"]["models"])
    if not models:
        raise HTTPException(400, "Keine Modelle für den Speed-Test ausgewählt.")
    parallel = req.parallel if req.parallel is not None else cfg["speedtest"]["parallel"]
    return ndjson(speedtest.run(OllamaClient.from_config(cfg), models, req.prompt, parallel))


@app.get("/api/speedtest/history")
def get_speedtest_history():
    return history.load_all()


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
    messages = [m.model_dump() for m in req.messages]
    return ndjson(chat.run(OllamaClient.from_config(cfg), model, messages, system))


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
