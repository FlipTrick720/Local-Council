"""Optional judge for Council answers: a chosen model scores the members' first answers (each one a single model
on its own) and the Council's final answer on a few criteria. The scores are kept per user in
data/judge/<user>.jsonl, one JSON object per line, to compare later whether the Council beats a single model.

The answers are shown to the judge in random order under neutral numbers, so it does not favour "the final one".
"""
import json
import random
import threading
import time
from typing import AsyncIterator

from app.config import DATA
from app.ollama import OllamaClient

DATA_DIR = DATA / "judge"
CRITERIA = {
    "correctness": "Korrektheit",
    "completeness": "Vollständigkeit",
    "clarity": "Klarheit",
    "usefulness": "Nützlichkeit",
}
ANSWER_CHARS = 4000

PROMPT = """You are a strict, impartial judge. Score each answer to the question below from 1 (very poor) to 10 \
(excellent) on these criteria: {criteria}. Judge only the content, not the length. Answer with JSON only:
{{"scores": {{"1": {{"correctness": 7, ...}}, "2": {{...}}, ...}}, "best": "<number>", "comment": "<one sentence, \
in the language of the question>"}}

Question:
{question}

{answers}"""

_lock = threading.Lock()


async def score(client: OllamaClient, model: str, question: str, answers: dict[str, str]) -> AsyncIterator[dict]:
    """`answers`: label → text (e.g. "A" … and "final"). Yields token events (so the user sees progress), then
    {"type": "judge", "model", "scores": {label: {criterion: n, "total": avg}}, "best": label, "comment"}."""
    labels = list(answers)
    random.shuffle(labels)
    numbered = "\n\n".join(f"### Answer {i}\n{answers[label][:ANSWER_CHARS]}" for i, label in enumerate(labels, 1))
    prompt = PROMPT.format(criteria=", ".join(CRITERIA), question=question[:3000], answers=numbered)
    parts = []
    async for chunk in client.chat_stream(model, [{"role": "user", "content": prompt}],
                                          {"temperature": 0}, think=False, format="json"):
        if chunk["type"] == "content":
            parts.append(chunk["text"])
            yield {"type": "token", "member": "judge", "model": model, "kind": "content", "text": chunk["text"]}
    data = json.loads("".join(parts) or "{}")
    scores = {}
    for i, label in enumerate(labels, 1):
        raw = (data.get("scores") or {}).get(str(i)) or {}
        values = {c: _clamp(raw.get(c)) for c in CRITERIA}
        known = [v for v in values.values() if v is not None]
        scores[label] = {**values, "total": round(sum(known) / len(known), 2) if known else None}
    best = str(data.get("best", ""))
    best_label = labels[int(best) - 1] if best.isdigit() and 0 < int(best) <= len(labels) else None
    yield {"type": "judge", "model": model, "scores": scores, "best": best_label, "comment": str(data.get("comment", ""))[:300]}


def _clamp(value) -> int | None:
    try:
        return max(1, min(10, int(value)))
    except (TypeError, ValueError):
        return None


def record(user: str, row: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock, (DATA_DIR / f"{user}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.time(), **row}, ensure_ascii=False) + "\n")


def load(user: str) -> list[dict]:
    path = DATA_DIR / f"{user}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text("utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows
