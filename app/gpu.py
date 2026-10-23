"""A first-come, first-served queue for work on the GPU, so several users do not make Ollama swap models back and
forth. queue.parallel in config.json (admins, default 1) is how many jobs run at the same time.

A job is one streamed request: a chat answer (with its tool calls), a whole Council run, one Speed-Test run, or
indexing a document. While a job waits, its stream gets {"type": "queue", "position": n} (1 = next), and
{"type": "queue", "position": 0} when it starts. Closing the stream (the Stop button) leaves the queue.

Everything here runs on the server's event loop only, so the lists need no locks.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

from app import config

KEEPALIVE = 15  # seconds between repeated position events while waiting (keeps proxies from closing the stream)


@dataclass(eq=False)
class Ticket:
    user: str
    label: str
    granted: bool = False
    wake: asyncio.Event = field(default_factory=asyncio.Event)


_waiting: list[Ticket] = []
_running: list[Ticket] = []


def limit() -> int:
    return max(1, int(config.load().get("queue", {}).get("parallel", 1) or 1))


def _dispatch() -> None:
    while _waiting and len(_running) < limit():
        ticket = _waiting.pop(0)
        ticket.granted = True
        _running.append(ticket)
    for ticket in [*_waiting, *_running]:
        ticket.wake.set()


def _leave(ticket: Ticket) -> None:
    if ticket in _waiting:
        _waiting.remove(ticket)
    if ticket in _running:
        _running.remove(ticket)
    _dispatch()


async def _wait(ticket: Ticket) -> AsyncIterator[int]:
    """Yields the ticket's position whenever it changes (and every KEEPALIVE seconds) until it may run."""
    _waiting.append(ticket)
    _dispatch()
    last = None
    while not ticket.granted:
        position = _waiting.index(ticket) + 1
        if position != last:
            yield position
            last = position
        ticket.wake.clear()
        try:
            await asyncio.wait_for(ticket.wake.wait(), KEEPALIVE)
        except TimeoutError:
            last = None


async def queued(user: str, label: str, events: AsyncIterator[dict]) -> AsyncIterator[dict]:
    """Runs the event stream `events` when it is its turn; the generator is only started then."""
    ticket = Ticket(user, label)
    try:
        async for position in _wait(ticket):
            yield {"type": "queue", "position": position}
        yield {"type": "queue", "position": 0}
        async for event in events:
            yield event
    finally:
        _leave(ticket)
        await events.aclose()


@asynccontextmanager
async def slot(user: str, label: str):
    """For short jobs without a stream (chat titles): waits silently for a turn."""
    ticket = Ticket(user, label)
    try:
        async for _ in _wait(ticket):
            pass
        yield
    finally:
        _leave(ticket)


def status() -> dict:
    return {"parallel": limit(), "running": len(_running), "waiting": len(_waiting)}
