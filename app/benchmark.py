"""Council vs. single model: runs a set of questions through one model (baseline) and a Council, lets a judge model
compare both answers per question, and keeps everything for later comparison. Admins only.

Files in data/benchmarks/:
  questions.json   the question set (editable in the UI); starter questions are written on first use
  runs/<id>.json   one run: its setup, a copy of the questions, and per question both answers, the judge's verdict
                   and (optionally) the admin's own vote

A run works in the background on the server (it goes on when the browser is closed). Each question goes through the
GPU queue three times (baseline, Council, judge), so other users can work in between. Every step is saved at once:
stopping keeps what is done, and resuming continues with the first missing step. After a server restart a run that
was running counts as stopped.

Position bias: the judge sees both answers in random order as "A" and "B"; the run stores which was which.
"""
import asyncio
import json
import random
import secrets
import threading
import time
from typing import AsyncIterator

from app import gpu
from app.config import DATA
from app.modes import agent, council
from app.ollama import OllamaClient

DATA_DIR = DATA / "benchmarks"
RUNS_DIR = DATA_DIR / "runs"
CATEGORIES = ["reasoning", "factual", "math", "coding", "writing"]
BASELINE_SYSTEM = "Answer the question accurately and concisely, in the language of the question."
JUDGE_PROMPT = """You are a strict, impartial judge. Two assistants answered the same question. Decide which answer \
is better: more correct, complete, clear and useful. Judge the content, not the length, and ignore the order of \
the answers.{reference_note}

Question:
{question}
{reference}
### Answer A
{a}

### Answer B
{b}

Answer with JSON only:
{{"winner": "A" | "B" | "tie", "scores": {{"A": <1-10>, "B": <1-10>}}, {correct}"reason": "<one sentence, in the \
language of the question>"}}"""

STARTER_QUESTIONS = [
    ("reasoning", "de", "Ein Schläger und ein Ball kosten zusammen 1,10 €. Der Schläger kostet 1 € mehr als der Ball. Wie viel kostet der Ball?", "5 Cent"),
    ("reasoning", "en", "If all bloops are razzies and all razzies are lazzies, are all bloops definitely lazzies?", "Yes, by transitivity."),
    ("reasoning", "de", "Anna ist älter als Ben, Ben ist älter als Clara. Wer ist am jüngsten?", "Clara"),
    ("reasoning", "en", "A farmer has 17 sheep. All but 9 run away. How many are left?", "9"),
    ("factual", "de", "Welcher Planet ist der Sonne am nächsten?", "Merkur"),
    ("factual", "en", "What is the chemical symbol for gold?", "Au"),
    ("factual", "de", "In welchem Jahr fiel die Berliner Mauer?", "1989"),
    ("factual", "en", "Who wrote the novel 'Pride and Prejudice'?", "Jane Austen"),
    ("math", "de", "Was ist 17 × 23?", "391"),
    ("math", "en", "Solve for x: 3x + 7 = 25.", "x = 6"),
    ("math", "de", "Wie viele Primzahlen gibt es zwischen 1 und 30?", "10 (2, 3, 5, 7, 11, 13, 17, 19, 23, 29)"),
    ("math", "en", "What is the derivative of x^3 · sin(x)?", "3x² · sin(x) + x³ · cos(x)"),
    ("coding", "en", "Write a Python function that returns True if a string is a palindrome, ignoring case and spaces.",
     "e.g. def is_pal(s): t = ''.join(s.lower().split()); return t == t[::-1]"),
    ("coding", "de", "Was ist der Unterschied zwischen einer Liste und einem Tupel in Python?",
     "Listen sind veränderbar (mutable), Tupel nicht; Tupel können daher z. B. Dictionary-Schlüssel sein."),
    ("coding", "en", "What is the time complexity of binary search on a sorted array, and why?",
     "O(log n), because every step halves the remaining search range."),
    ("coding", "de", "Finde den Fehler: for i in range(len(a)):\n    if a[i] = 0:\n        print(i)",
     "In der if-Bedingung muss == statt = stehen (Vergleich statt Zuweisung)."),
    ("writing", "de", "Schreibe eine höfliche E-Mail, in der du ein Meeting auf nächste Woche verschiebst. Höchstens 80 Wörter.", ""),
    ("writing", "en", "Explain to a 10-year-old how a rainbow forms, in three sentences.", ""),
    ("writing", "de", "Fasse Vor- und Nachteile von Homeoffice in je drei Stichpunkten zusammen.", ""),
    ("writing", "en", "Write a four-line poem about a server room at night.", ""),
]

_lock = threading.RLock()
_tasks: dict[str, asyncio.Task] = {}


class BenchmarkError(ValueError):
    pass


def _write(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


# ---------- Questions ----------

def questions() -> list[dict]:
    path = DATA_DIR / "questions.json"
    if not path.exists():
        return save_questions([{"category": c, "lang": lang, "question": q, "reference": ref}
                               for c, lang, q, ref in STARTER_QUESTIONS])
    return json.loads(path.read_text("utf-8"))


def save_questions(items: list[dict]) -> list[dict]:
    clean = []
    for item in items:
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        clean.append({
            "id": str(item.get("id") or secrets.token_hex(3)),
            "category": item.get("category") if item.get("category") in CATEGORIES else "reasoning",
            "lang": "en" if item.get("lang") == "en" else "de",
            "question": question[:4000],
            "reference": str(item.get("reference") or "").strip()[:2000],
        })
    if not clean:
        raise BenchmarkError("Mindestens eine Frage.")
    with _lock:
        _write(DATA_DIR / "questions.json", clean)
    return clean


# ---------- Runs ----------

def _path(run_id: str):
    if not run_id.isalnum() or len(run_id) > 32:
        raise BenchmarkError("Lauf nicht gefunden.")
    return RUNS_DIR / f"{run_id}.json"


def get(run_id: str) -> dict:
    path = _path(run_id)
    if not path.exists():
        raise BenchmarkError("Lauf nicht gefunden.")
    run = json.loads(path.read_text("utf-8"))
    if run["status"] == "running" and run_id not in _tasks:
        run["status"] = "stopped"  # the server was restarted while it ran
    return run


def _save(run: dict) -> None:
    with _lock:
        _write(_path(run["id"]), run)


def list_runs() -> list[dict]:
    out = []
    for path in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            run = get(path.stem)
        except (ValueError, OSError, BenchmarkError):
            continue
        out.append({k: run[k] for k in ("id", "created", "status", "setup", "error")} | {
            "progress": progress(run), "summary": summary(run)})
    return out


def delete(run_id: str) -> None:
    stop(run_id)
    _path(run_id).unlink(missing_ok=True)


def judge_warning(setup: dict) -> str:
    models = {m["model"] for m in setup["members"]} | {setup["synthesizer"]}
    if setup["judge"] in models:
        return (f"Der Richter {setup['judge']} ist selbst im Council. Er könnte Antworten im eigenen Stil "
                "bevorzugen; besser ein Modell nehmen, das nicht mitmacht.")
    if setup["judge"] == setup["baseline"]:
        return f"Der Richter {setup['judge']} ist auch das Einzelmodell und könnte seine eigene Antwort bevorzugen."
    return ""


def create(setup: dict) -> dict:
    qs = questions()
    run = {
        "id": time.strftime("%Y%m%d%H%M%S") + secrets.token_hex(2), "created": time.time(), "status": "stopped",
        "setup": {**setup, "warning": judge_warning(setup)}, "error": "",
        "results": [{"qid": q["id"], "question": q, "baseline": None, "council": None, "judge": None, "human": None}
                    for q in qs],
    }
    _save(run)
    return run


def progress(run: dict) -> dict:
    total = len(run["results"])
    done = sum(1 for r in run["results"] if r["judge"])
    current = next((i for i, r in enumerate(run["results"]) if not r["judge"]), None)
    return {"done": done, "total": total, "current": None if current is None else current + 1,
            "step": run.get("step", "")}


def start(run_id: str, user: str, client: OllamaClient, cfg: dict) -> dict:
    """Starts or resumes a run in the background (only one benchmark at a time)."""
    check_idle()
    run = get(run_id)
    if all(r["judge"] for r in run["results"]):
        raise BenchmarkError("Dieser Lauf ist schon fertig.")
    run.update(status="running", error="")
    _save(run)
    task = asyncio.get_running_loop().create_task(_work(run_id, user, client, cfg))
    _tasks[run_id] = task
    task.add_done_callback(lambda _: _tasks.pop(run_id, None))
    return run


def check_idle() -> None:
    if any(not t.done() for t in _tasks.values()):
        raise BenchmarkError("Es läuft schon ein Benchmark. Erst stoppen oder abwarten.")


def stop(run_id: str) -> None:
    task = _tasks.get(run_id)
    if task and not task.done():
        task.cancel()


async def _work(run_id: str, user: str, client: OllamaClient, cfg: dict) -> None:
    run = get(run_id)
    setup = run["setup"]
    try:
        for i, result in enumerate(run["results"]):
            q = result["question"]
            for step, label in (("baseline", "Einzelmodell"), ("council", "Council"), ("judge", "Richter")):
                if result[step]:
                    continue
                run["step"] = f"Frage {i + 1}/{len(run['results'])}: {label}"
                _save(run)
                async with gpu.slot(user, "Benchmark"):
                    if step == "baseline":
                        result[step] = await _single(client, setup["baseline"], q["question"], cfg)
                    elif step == "council":
                        result[step] = await _council(client, user, setup, q["question"], cfg)
                    else:
                        result[step] = await _judge(client, setup["judge"], q, result)
                _save(run)
        run.update(status="done", step="")
    except asyncio.CancelledError:
        run.update(status="stopped", step="")
        _save(run)
        raise
    except Exception as e:  # keep what is done; the run can be resumed
        run.update(status="stopped", step="", error=str(e)[:500])
    _save(run)


async def _collect(events: AsyncIterator[dict]) -> tuple[str, int, int, str | None]:
    """Final text (of the member "final" for a Council), generated and prompt tokens of all answers, error."""
    content, tokens, prompt_tokens, error = "", 0, 0, None
    async for event in events:
        if event["type"] == "done":
            stats = event.get("stats") or {}
            tokens += stats.get("tokens") or 0
            prompt_tokens += stats.get("prompt_tokens") or 0
            if event.get("member") in (None, "final"):
                content = event["content"]
        elif event["type"] == "error" and event.get("member") in (None, "final"):
            error = event["error"]
    return content, tokens, prompt_tokens, error


async def _single(client: OllamaClient, model: str, question: str, cfg: dict) -> dict:
    start = time.perf_counter()
    messages = [{"role": "system", "content": BASELINE_SYSTEM}, {"role": "user", "content": question}]
    content, tokens, prompt_tokens, error = await _collect(
        agent.answer(client, model, messages, base={}, cfg=cfg, max_steps=0))
    return {"content": content, "error": error, "seconds": round(time.perf_counter() - start, 2), "tokens": tokens,
            "prompt_tokens": prompt_tokens}


async def _council(client: OllamaClient, user: str, setup: dict, question: str, cfg: dict) -> dict:
    start = time.perf_counter()
    content, tokens, prompt_tokens, error = await _collect(council.run(
        client, user=user, members=setup["members"], synthesizer=setup["synthesizer"], question=question, images=[],
        history=[], rounds=setup["rounds"], parallel=setup.get("parallel", False), cfg=cfg))
    return {"content": content, "error": error, "seconds": round(time.perf_counter() - start, 2), "tokens": tokens,
            "prompt_tokens": prompt_tokens}


async def _judge(client: OllamaClient, model: str, q: dict, result: dict) -> dict:
    order = ["baseline", "council"]
    random.shuffle(order)
    labels = {"A": order[0], "B": order[1]}
    ref = q.get("reference", "")
    prompt = JUDGE_PROMPT.format(
        question=q["question"], a=(result[labels["A"]]["content"] or "(keine Antwort)")[:6000],
        b=(result[labels["B"]]["content"] or "(keine Antwort)")[:6000],
        reference=f"\nReference answer (correct): {ref}\n" if ref else "",
        reference_note=" A reference answer is given: check each answer for correctness against it." if ref else "",
        correct='"correct": {"A": true | false, "B": true | false}, ' if ref else "")
    parts = []
    async for chunk in client.chat_stream(model, [{"role": "user", "content": prompt}], {"temperature": 0},
                                          think=False, format="json"):
        if chunk["type"] == "content":
            parts.append(chunk["text"])
    try:
        data = json.loads("".join(parts) or "{}")
    except ValueError:
        data = {}
    winner_label = str(data.get("winner", "")).strip().upper()
    winner_label = winner_label if winner_label in ("A", "B") else "tie"
    scores = data.get("scores") or {}
    correct = data.get("correct") or {}
    return {
        "labels": labels, "winner_label": winner_label,
        "winner": labels.get(winner_label, "tie"),
        "scores": {labels[x]: _score(scores.get(x)) for x in "AB"},
        "correct": {labels[x]: correct.get(x) if isinstance(correct.get(x), bool) else None for x in "AB"} if ref else {},
        "reason": str(data.get("reason", ""))[:300],
    }


def _score(value) -> int | None:
    try:
        return max(1, min(10, int(value)))
    except (TypeError, ValueError):
        return None


# ---------- The admin's own votes and the summary ----------

def vote(run_id: str, qid: str, label: str) -> dict:
    """`label`: "A", "B" or "tie", in the judge's anonymous order (shown to the admin the same way)."""
    with _lock:
        run = get(run_id)
        result = next((r for r in run["results"] if r["qid"] == qid), None)
        if not result or not result["judge"] or label not in ("A", "B", "tie"):
            raise BenchmarkError("Diese Frage kann noch nicht bewertet werden.")
        result["human"] = {"label": label, "winner": result["judge"]["labels"].get(label, "tie"), "time": time.time()}
        _save(run)
        return summary(run)


def _mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def summary(run: dict) -> dict:
    judged = [r for r in run["results"] if r["judge"]]
    wins = {"baseline": 0, "council": 0, "tie": 0}
    for r in judged:
        wins[r["judge"]["winner"]] += 1
    categories = {}
    for cat in CATEGORIES:
        rows = [r for r in judged if r["question"]["category"] == cat]
        if rows:
            categories[cat] = {side: _mean([r["judge"]["scores"].get(side) for r in rows]) for side in ("baseline", "council")}
            categories[cat]["n"] = len(rows)
    sides = {}
    for side in ("baseline", "council"):
        answered = [r[side] for r in run["results"] if r[side]]
        sides[side] = {
            "seconds": _mean([a["seconds"] for a in answered]), "tokens": _mean([a["tokens"] for a in answered]),
            "score": _mean([r["judge"]["scores"].get(side) for r in judged]),
            "correct": _mean([1 if r["judge"]["correct"].get(side) else 0 for r in judged
                              if r["judge"]["correct"].get(side) is not None]),
        }
    voted = [r for r in judged if r["human"]]
    agree = sum(1 for r in voted if r["human"]["winner"] == r["judge"]["winner"])
    factor = (round(sides["council"]["seconds"] / sides["baseline"]["seconds"], 1)
              if sides["council"]["seconds"] and sides["baseline"]["seconds"] else None)
    return {"judged": len(judged), "wins": wins, "categories": categories, "sides": sides, "time_factor": factor,
            "votes": len(voted), "agreement": round(agree / len(voted), 2) if voted else None,
            "human_wins": {k: sum(1 for r in voted if r["human"]["winner"] == k) for k in wins}}
