"""Runs one or more chat jobs against Ollama and merges their streams into one event stream."""
import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator

from app.ollama import OllamaClient


@dataclass
class Job:
    model: str
    messages: list[dict]
    tag: dict = field(default_factory=dict)  # copied into every event, e.g. {"stage": "initial"}


async def stream_job(client: OllamaClient, job: Job) -> AsyncIterator[dict]:
    base = {"model": job.model, **job.tag}
    parts: list[str] = []
    stats = None
    try:
        async for chunk in client.chat_stream(job.model, job.messages):
            if chunk["type"] == "stats":
                stats = chunk
                stats.pop("type")
            else:
                if chunk["type"] == "content":
                    parts.append(chunk["text"])
                yield {"type": "token", **base, "kind": chunk["type"], "text": chunk["text"]}
        yield {"type": "done", **base, "content": "".join(parts), "stats": stats}
    except Exception as e:
        yield {"type": "error", **base, "error": str(e)}


async def run_jobs(client: OllamaClient, jobs: list[Job], parallel: bool) -> AsyncIterator[dict]:
    if not parallel:
        for job in jobs:
            async for event in stream_job(client, job):
                yield event
        return

    queue: asyncio.Queue = asyncio.Queue()
    finished = object()

    async def pump(job: Job):
        try:
            async for event in stream_job(client, job):
                await queue.put(event)
        finally:
            await queue.put(finished)

    tasks = [asyncio.create_task(pump(job)) for job in jobs]
    remaining = len(tasks)
    try:
        while remaining:
            event = await queue.get()
            if event is finished:
                remaining -= 1
            else:
                yield event
    finally:
        for task in tasks:
            task.cancel()
