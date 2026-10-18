"""Council: members answer, then discuss each other's answers for N rounds, then one model synthesizes.

Members are shown to each other anonymously ("Member A", "Member B", ...) to reduce model-name bias.
"""
from typing import AsyncIterator

from app.modes.runner import Job, run_jobs
from app.ollama import OllamaClient

SYSTEM_PROMPT = (
    "You are a member of a council of AI models that answers questions together. "
    "Be accurate and concise, and be honest about uncertainty. "
    "Always answer in the same language as the user's question."
)

DISCUSSION_PROMPT = """Question:
{question}

Your previous answer (you are {label}):
{own}

Answers of the other council members:
{others}

This is discussion round {round} of {rounds}. Critically review the other answers: point out errors, gaps or \
disagreements, adopt good points, and defend your position where you are confident. \
Then give your revised, complete answer to the question."""

SYNTHESIS_PROMPT = """Question:
{question}

After {rounds} round(s) of discussion, the council members gave these final answers:
{answers}

Write the council's final answer: combine the strongest points, resolve contradictions (state the majority view \
and mention relevant dissent), and correct errors. Answer the question directly and do not describe the \
discussion process. Answer in the same language as the question."""


def _label(i: int) -> str:
    return f"Member {chr(ord('A') + i)}"


def _format(answers: dict[str, str], labels: dict[str, str], exclude: str | None = None) -> str:
    return "\n\n".join(f"### {labels[m]}\n{a}" for m, a in answers.items() if m != exclude)


async def run(
    client: OllamaClient,
    members: list[str],
    synthesizer: str,
    question: str,
    rounds: int,
    parallel: bool,
) -> AsyncIterator[dict]:
    labels = {m: _label(i) for i, m in enumerate(members)}
    answers: dict[str, str] = {}

    async def stage(jobs: list[Job]) -> AsyncIterator[dict]:
        async for event in run_jobs(client, jobs, parallel):
            if event["type"] == "done":
                answers[event["model"]] = event["content"]
            yield event

    yield {"type": "start", "members": members, "synthesizer": synthesizer, "rounds": rounds, "labels": labels}

    tag = {"stage": "initial", "round": 0}
    yield {"type": "phase", **tag, "models": members}
    jobs = [Job(m, [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}], tag)
            for m in members]
    async for event in stage(jobs):
        yield event

    for r in range(1, rounds + 1):
        if len(answers) < 2:
            break
        previous = {m: answers[m] for m in members if m in answers}
        tag = {"stage": "discussion", "round": r}
        yield {"type": "phase", **tag, "models": list(previous)}
        jobs = []
        for m, own in previous.items():
            prompt = DISCUSSION_PROMPT.format(
                question=question, label=labels[m], own=own,
                others=_format(previous, labels, exclude=m), round=r, rounds=rounds,
            )
            jobs.append(Job(m, [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}], tag))
        answers.clear()
        async for event in stage(jobs):
            yield event

    if not answers:
        yield {"type": "error", "stage": "synthesis", "error": "Kein Ratsmitglied hat eine Antwort geliefert."}
        yield {"type": "end"}
        return

    final_answers = {m: answers[m] for m in members if m in answers}
    tag = {"stage": "synthesis", "round": rounds + 1}
    yield {"type": "phase", **tag, "models": [synthesizer]}
    prompt = SYNTHESIS_PROMPT.format(question=question, rounds=rounds, answers=_format(final_answers, labels))
    job = Job(synthesizer, [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}], tag)
    async for event in run_jobs(client, [job], parallel=False):
        yield event
    yield {"type": "end"}
