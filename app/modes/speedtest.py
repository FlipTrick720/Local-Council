"""Speed test: same prompt to several models, compare latency and tokens/s. Results go to app.history."""
from datetime import datetime
from typing import AsyncIterator

from app import history
from app.modes.runner import Job, run_jobs
from app.ollama import OllamaClient, OllamaError


async def run(client: OllamaClient, models: list[str], prompt: str, parallel: bool) -> AsyncIterator[dict]:
    try:
        versions = {m["name"]: m["digest"] for m in await client.list_models()}
    except OllamaError:
        versions = {}
    run_id = datetime.now().isoformat(timespec="seconds")
    # A "parallel" run with a single model is effectively sequential.
    mode = "parallel" if parallel and len(models) > 1 else "sequential"

    yield {"type": "start", "models": models, "parallel": parallel}
    jobs = [Job(m, [{"role": "user", "content": prompt}]) for m in models]
    async for event in run_jobs(client, jobs, parallel):
        if event["type"] == "done" and event["stats"]:
            history.append({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "run_id": run_id,
                "mode": mode,
                "concurrency": len(models) if mode == "parallel" else 1,
                "model": event["model"],
                "version": versions.get(event["model"], "")[:12],
                **event["stats"],
            })
        yield event
    yield {"type": "end"}
