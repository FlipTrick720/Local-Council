"""Thin async client for the Ollama HTTP API (/api/tags, /api/ps, /api/show, /api/generate, /api/chat, /api/embed)."""
import asyncio
import json
import time
from typing import AsyncIterator

import httpx


class OllamaError(RuntimeError):
    pass


_capabilities: dict[str, list[str]] = {}  # digest → capabilities, so /api/show runs once per model version


class OllamaClient:
    def __init__(self, url: str, timeout: float = 600, keep_alive: str | None = None, options: dict | None = None):
        self.url = url.rstrip("/")
        self.timeout = httpx.Timeout(timeout, connect=10)
        self.keep_alive = keep_alive
        self.options = options or {}

    @classmethod
    def from_config(cls, cfg: dict) -> "OllamaClient":
        o = cfg["ollama"]
        return cls(o["url"], o.get("timeout", 600), o.get("keep_alive"), o.get("options"))

    async def list_models(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.url}/api/tags")
                r.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaError(f"Ollama nicht erreichbar unter {self.url}: {e}") from e
        models = []
        for m in r.json().get("models", []):
            details = m.get("details") or {}
            models.append({
                "name": m["name"],
                "digest": m.get("digest", ""),
                "size": m.get("size"),
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
            })
        models.sort(key=lambda m: m["name"])
        caps = await asyncio.gather(*(self._capabilities_of(m) for m in models))
        for m, c in zip(models, caps):
            m["capabilities"] = c
        return models

    async def _capabilities_of(self, m: dict) -> list[str] | None:
        """E.g. ["completion", "vision"] or ["embedding"]; None if Ollama does not say."""
        if m["digest"] not in _capabilities:
            try:
                _capabilities[m["digest"]] = (await self.show(m["name"]))["capabilities"]
            except OllamaError:
                return None
        return _capabilities[m["digest"]] or None

    async def show(self, model: str) -> dict:
        """What a model can do ("vision", "thinking", "tools", …) and the context length it was trained for."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(f"{self.url}/api/show", json={"model": model})
        except httpx.HTTPError as e:
            raise OllamaError(f"Ollama nicht erreichbar unter {self.url}: {e}") from e
        if r.status_code != 200:
            raise OllamaError(_error_text(r.content, r.status_code))
        data = r.json()
        info = data.get("model_info") or {}
        max_ctx = next((v for k, v in info.items() if k.endswith(".context_length")), None)
        return {"name": model, "capabilities": data.get("capabilities") or [], "context_length": max_ctx}

    async def loaded_models(self) -> list[dict]:
        """Models Ollama currently holds in memory (RAM/VRAM)."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.url}/api/ps")
                r.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaError(f"Ollama nicht erreichbar unter {self.url}: {e}") from e
        return [
            {"name": m["name"], "size": m.get("size"), "size_vram": m.get("size_vram"), "expires_at": m.get("expires_at"),
             "context_length": m.get("context_length")}
            for m in r.json().get("models", [])
        ]

    async def set_loaded(self, model: str, load: bool) -> None:
        """Loads a model into memory (for keep_alive) or unloads it right away.

        A /api/generate request without a prompt only loads or unloads the model.
        """
        body = {"model": model, "keep_alive": (self.keep_alive or "5m") if load else 0}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(f"{self.url}/api/generate", json=body)
        except httpx.HTTPError as e:
            raise OllamaError(f"Ollama-Anfrage fehlgeschlagen ({self.url}): {e}") from e
        if r.status_code != 200:
            raise OllamaError(_error_text(r.content, r.status_code))

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """One embedding vector per text."""
        body = {"model": model, "input": texts}
        if self.keep_alive:
            body["keep_alive"] = self.keep_alive
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(f"{self.url}/api/embed", json=body)
        except httpx.HTTPError as e:
            raise OllamaError(f"Ollama-Anfrage fehlgeschlagen ({self.url}): {e}") from e
        if r.status_code != 200:
            message = _error_text(r.content, r.status_code)
            if "not found" in message:
                message += f" – Embedding-Modell installieren mit: ollama pull {model}"
            raise OllamaError(message)
        return r.json()["embeddings"]

    async def chat_stream(
        self, model: str, messages: list[dict], options: dict | None = None, think: bool | None = None,
        tools: list[dict] | None = None, format: str | dict | None = None,
    ) -> AsyncIterator[dict]:
        """Yields {"type": "thinking"|"content", "text": ...} chunks, {"type": "tool_calls", "calls": [...]} when
        the model wants to call tools, then one {"type": "stats", ...}.

        `options` are merged over the configured ones. `think` switches reasoning on/off for thinking models;
        None leaves it to the model. `tools` are function definitions in Ollama's format. `format` ("json" or a
        JSON schema) makes the model answer with JSON only.
        """
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {**self.options, **(options or {})},
        }
        if self.keep_alive:
            body["keep_alive"] = self.keep_alive
        if think is not None:
            body["think"] = think
        if tools:
            body["tools"] = tools
        if format:
            body["format"] = format

        start = time.perf_counter()
        first = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", f"{self.url}/api/chat", json=body) as r:
                    if r.status_code != 200:
                        raise OllamaError(_error_text(await r.aread(), r.status_code))
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        if "error" in chunk:
                            raise OllamaError(chunk["error"])
                        msg = chunk.get("message") or {}
                        for kind in ("thinking", "content"):
                            if msg.get(kind):
                                if first is None:
                                    first = time.perf_counter()
                                yield {"type": kind, "text": msg[kind]}
                        if msg.get("tool_calls"):
                            yield {"type": "tool_calls", "calls": msg["tool_calls"]}
                        if chunk.get("done"):
                            yield {"type": "stats", **_stats(chunk, start, first, time.perf_counter())}
        except httpx.HTTPError as e:
            raise OllamaError(f"Ollama-Anfrage fehlgeschlagen ({self.url}): {e}") from e


def _error_text(raw: bytes, status: int) -> str:
    try:
        return json.loads(raw).get("error") or f"HTTP {status}"
    except ValueError:
        return f"HTTP {status}: {raw[:200].decode(errors='replace')}"


def _stats(chunk: dict, start: float, first: float | None, end: float) -> dict:
    ns = 1e9
    eval_count, eval_ns = chunk.get("eval_count", 0), chunk.get("eval_duration", 0)
    prompt_count, prompt_ns = chunk.get("prompt_eval_count", 0), chunk.get("prompt_eval_duration", 0)
    return {
        "ttft": round(first - start, 3) if first else None,
        "wall_time": round(end - start, 3),
        "load_time": round(chunk.get("load_duration", 0) / ns, 3),
        "prompt_tokens": prompt_count,
        "prompt_tps": round(prompt_count / (prompt_ns / ns), 1) if prompt_ns else None,
        "tokens": eval_count,
        "tps": round(eval_count / (eval_ns / ns), 1) if eval_ns else None,
    }
