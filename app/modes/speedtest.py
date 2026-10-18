"""Speed test: same prompt to several models, compare latency and tokens/s."""
from typing import AsyncIterator

from app.modes.runner import Job, run_jobs
from app.ollama import OllamaClient


async def run(client: OllamaClient, models: list[str], prompt: str, parallel: bool) -> AsyncIterator[dict]:
    yield {"type": "start", "models": models, "parallel": parallel}
    jobs = [Job(m, [{"role": "user", "content": prompt}]) for m in models]
    async for event in run_jobs(client, jobs, parallel):
        yield event
    yield {"type": "end"}
