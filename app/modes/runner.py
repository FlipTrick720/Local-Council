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
    options: dict | None = None  # per-job Ollama options on top of the configured ones
    think: bool | None = None


async def stream_job(client: OllamaClient, job: Job) -> AsyncIterator[dict]:
    base = {"model": job.model, **job.tag}
    parts: list[str] = []
    stats = None
    try:
        async for chunk in client.chat_stream(job.model, job.messages, job.options, job.think):
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
    async for event in merge([stream_job(client, job) for job in jobs], parallel):
        yield event


async def merge(streams: list[AsyncIterator[dict]], parallel: bool) -> AsyncIterator[dict]:
    """The events of several streams: one after another, or (parallel) interleaved as they come.
    The streams are generators that only start working when first read."""
    if not parallel:
        for s in streams:
            try:
                async for event in s:
                    yield event
            finally:
                await s.aclose()  # also when the reader stops early (Stop, skip to the final answer)
        return

    queue: asyncio.Queue = asyncio.Queue()
    finished = object()

    async def pump(s: AsyncIterator[dict]):
        try:
            async for event in s:
                await queue.put(event)
        finally:
            await queue.put(finished)

    tasks = [asyncio.create_task(pump(s)) for s in streams]
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
