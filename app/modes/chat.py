"""Plain chat with a single model. The client keeps the history and sends it each turn."""
from typing import AsyncIterator

from app.modes.runner import Job, stream_job
from app.ollama import OllamaClient


async def run(client: OllamaClient, model: str, messages: list[dict], system: str = "") -> AsyncIterator[dict]:
    if system.strip():
        messages = [{"role": "system", "content": system}, *messages]
    async for event in stream_job(client, Job(model, messages)):
        yield event
