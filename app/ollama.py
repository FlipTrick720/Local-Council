"""Thin async client for the Ollama HTTP API (/api/tags, /api/chat)."""
import json
import time
from typing import AsyncIterator

import httpx


class OllamaError(RuntimeError):
    pass


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
                "size": m.get("size"),
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
            })
        return sorted(models, key=lambda m: m["name"])

    async def chat_stream(self, model: str, messages: list[dict], options: dict | None = None) -> AsyncIterator[dict]:
        """Yields {"type": "thinking"|"content", "text": ...} chunks, then one {"type": "stats", ...}."""
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {**self.options, **(options or {})},
        }
        if self.keep_alive:
            body["keep_alive"] = self.keep_alive

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
