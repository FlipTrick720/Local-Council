"""Council: several members (each a model with an optional role) answer, discuss each other's answers for N rounds,
then one model writes the final answer. A Council session is a conversation: follow-up questions get the earlier
questions, the members' final positions and the final answers as context.

Members are shown to each other anonymously ("Member A", "Member B", ...) to reduce model-name bias; their role
(e.g. "critic") is shown, since it explains how they argue.

Members can use the chat's tools (app.modes.agent); web searches and pages are shared through tools.Research, and
what was found is listed in the next round's prompt. Knowledge base excerpts, attached files and the memory are
the same for all members. Each member's prompt is checked against its model's context length.

Events: start, phase, warning, status, sources, token, tool_call, tool_result, done, error, skipped, judge, end.
Member events carry {"member": "A", "model", "stage": "initial"|"discussion"|"synthesis", "round"}.
"""
import math
import secrets
from dataclasses import dataclass
from typing import AsyncIterator

from app import judge, rag, tools, websearch
from app.modes import agent, chat
from app.modes.runner import merge
from app.ollama import OllamaClient, OllamaError

SYSTEM_PROMPT = (
    "You are a member of a council of AI models that answers questions together. "
    "Be accurate and concise, and be honest about uncertainty. "
    "Always answer in the same language as the user's question."
)
ROLE_PROMPT = "Your role in the council: {role}.\n{system}"

DISCUSSION_PROMPT = """Question:
{question}

Your previous answer (you are {label}):
{own}

Answers of the other council members:
{others}

{research}This is discussion round {round} of {rounds}. Critically review the other answers: point out errors, gaps or \
disagreements, adopt good points, and defend your position where you are confident. \
Then give your revised, complete answer to the question. Write in the language of the question."""

SYNTHESIS_PROMPT = """Question:
{question}

{note}The council members gave these final answers:
{answers}

{research}Write the council's final answer: combine the strongest points, resolve contradictions (state the majority \
view and mention relevant dissent), and correct errors. Answer the question directly and do not describe the \
discussion process. Answer in the same language as the question."""

CHARS_PER_TOKEN = 3.5   # same estimate as the context meter in the browser
IMAGE_TOKENS = 512
DEFAULT_CTX = 4096      # Ollama's default num_ctx
REPLY_ROOM = 1024       # tokens kept free for the answer
HISTORY_CHARS = 1500    # per earlier member position / final answer in follow-up questions

_runs: dict[str, dict] = {}  # run id → {"user", "skip"}: lets the user jump to the final answer


@dataclass
class Member:
    label: str
    model: str
    role: str = ""
    system: str = ""
    tools: bool = False
    vision: bool = False
    max_ctx: int | None = None

    @property
    def name(self) -> str:
        return f"Member {self.label}" + (f" ({self.role})" if self.role else "")


def skip(run_id: str, user: str) -> bool:
    run = _runs.get(run_id)
    if not run or run["user"] != user:
        return False
    run["skip"] = True
    return True


def _format(answers: dict[str, str], members: dict[str, Member], exclude: str | None = None) -> str:
    return "\n\n".join(f"### {members[label].name}\n{text}" for label, text in answers.items() if label != exclude)


def history_block(history: list[dict]) -> str:
    """Earlier turns of the session as one context text: each question, the members' final positions and the
    final answer. (As separate chat turns, small models start to imitate the format in their own answers.)"""
    parts = []
    for turn in history:
        if turn["role"] == "user":
            parts.append(f"[Earlier question]\n{_cut(turn['content'], 2 * HISTORY_CHARS)}")
            continue
        for m in turn.get("members") or []:
            role = f", {m['role']}" if m.get("role") else ""
            parts.append(f"[Final position of member {m.get('label', '?')}{role}]\n{_cut(m.get('content', ''), HISTORY_CHARS)}")
        parts.append(f"[The council's final answer]\n{_cut(turn['content'], 2 * HISTORY_CHARS)}")
    if not parts:
        return ""
    return ("Earlier in this council session (context for the follow-up question at the end):\n\n"
            + "\n\n".join(parts) + "\n\n---")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " […]"


def _tokens(messages: list[dict]) -> int:
    chars = sum(len(m.get("content") or "") for m in messages)
    return math.ceil(chars / CHARS_PER_TOKEN) + IMAGE_TOKENS * sum(len(m.get("images") or []) for m in messages)


def fit_context(member: Member, messages: list[dict], options: dict | None, client: OllamaClient,
                max_auto: int) -> tuple[dict | None, str | None]:
    """Raises num_ctx for this member when the prompt needs more (up to the model's trained length and
    council.max_ctx); returns the options and a warning when it was raised or still does not fit."""
    need = _tokens(messages) + REPLY_ROOM
    ctx = (options or {}).get("num_ctx") or client.options.get("num_ctx") or DEFAULT_CTX
    if need <= ctx:
        return options, None
    ceiling = min(max_auto, member.max_ctx or max_auto)
    target = min(ceiling, math.ceil(need / 2048) * 2048)
    if target > ctx:
        options, ctx = {**(options or {}), "num_ctx": target}, target
    if need <= ctx:
        return options, f"{member.name} · {member.model}: Kontextlänge auf {ctx} Tokens erhöht (braucht ~{need})."
    return options, (f"⚠ {member.name} · {member.model}: Die Anfrage braucht ~{need} Tokens, das Modell bekommt "
                     f"höchstens {ctx}. Der Anfang (frühere Fragen, Dateien) wird abgeschnitten.")


async def _capabilities(client: OllamaClient, model: str) -> tuple[list[str], int | None]:
    try:
        info = await client.show(model)
        return info["capabilities"], info.get("context_length")
    except OllamaError:
        return [], None


async def run(
    client: OllamaClient, *, user: str, members: list[dict], synthesizer: str, question: str, images: list[str],
    history: list[dict], rounds: int, parallel: bool, cfg: dict, options: dict | None = None,
    think: bool | None = None, knowledge: str | None = None, use_tools: bool = False, web: bool = False,
    tool_steps: int = 3, judge_model: str | None = None, extra_system: str = "", final_extra: str = "",
    allowed_urls: set[str] | None = None, chat_id: str | None = None,
) -> AsyncIterator[dict]:
    """`extra_system` goes into every member's and the final system prompt (the memory's facts), `final_extra` only
    into the final one (how to propose new facts: only the final answer may do that)."""
    run_id = secrets.token_hex(8)
    _runs[run_id] = {"user": user, "skip": False}
    try:
        async for event in _run(client, run_id, user, members, synthesizer, question, images, history, rounds,
                                parallel, cfg, options, think, knowledge, use_tools, web, tool_steps, judge_model,
                                extra_system, final_extra, set(allowed_urls or ()), chat_id):
            yield event
    finally:
        _runs.pop(run_id, None)


async def _run(client, run_id, user, member_specs, synthesizer, question, images, history, rounds, parallel, cfg,
               options, think, knowledge, use_tools, web, tool_steps, judge_model, extra_system, final_extra, allowed,
               chat_id):
    max_auto = cfg.get("council", {}).get("max_ctx", 16384)
    members: dict[str, Member] = {}
    for i, spec in enumerate(member_specs):
        label = chr(ord("A") + i)
        caps, max_ctx = await _capabilities(client, spec["model"])
        members[label] = Member(label, spec["model"], spec.get("role", "").strip(), spec.get("system", "").strip(),
                                use_tools and "tools" in caps, "vision" in caps, max_ctx)
    yield {"type": "start", "run": run_id, "rounds": rounds, "synthesizer": synthesizer,
           "members": [{"label": m.label, "model": m.model, "role": m.role} for m in members.values()]}
    if use_tools and not any(m.tools for m in members.values()):
        yield {"type": "status", "text": "Kein Ratsmitglied unterstützt Werkzeuge, sie antworten ohne."}
    if images and not all(m.vision for m in members.values()):
        blind = ", ".join(m.name for m in members.values() if not m.vision)
        yield {"type": "warning", "text": f"{blind} kann keine Bilder sehen und bekommt nur den Text."}

    # Shared context: knowledge base and (if no member can search itself) one web search.
    sources: list[dict] = []
    context = {"blocks": []}
    try:
        searchers = any(m.tools for m in members.values())
        async for event in chat.prepare(client, user, question, knowledge=knowledge, web=web and not searchers,
                                        cfg=cfg, base={}):
            if event["type"] == "context":
                context = event
            else:
                yield event
    except (rag.RagError, websearch.WebError, OllamaError) as e:
        yield {"type": "error", "error": str(e)}
        yield {"type": "end"}
        return
    sources += context["sources"]
    if sources:
        yield {"type": "sources", "sources": list(sources)}
    earlier = history_block(history)
    shared_question = "\n\n".join(p for p in (earlier, *context["blocks"],
                                                f"Question: {question}" if earlier or context["blocks"] else question) if p)
    research = tools.Research()
    member_options = chat.web_ctx(options, client, cfg) if web else options

    def system_for(m: Member) -> str:
        parts = [SYSTEM_PROMPT]
        if m.role or m.system:
            parts.append(ROLE_PROMPT.format(role=m.role or "member", system=m.system).strip())
        if m.tools or web:
            parts.append(agent.guidance(web, m.tools, tool_steps))
        if extra_system.strip():
            parts.append(extra_system.strip())
        return "\n\n".join(parts)

    answers: dict[str, str] = {}   # label → latest complete answer
    initial: dict[str, str] = {}   # label → first answer (for the judge: each model on its own)
    skipped = False

    async def stage(name: str, round_: int, prompts: dict[str, str]) -> AsyncIterator[dict]:
        """Runs one stage for the given members; stops early when the user asks for the final answer."""
        nonlocal skipped
        yield {"type": "phase", "stage": name, "round": round_, "members": list(prompts)}
        streams = []
        for label, prompt in prompts.items():
            m = members[label]
            question_msg = {"role": "user", "content": prompt}
            if images and m.vision and name == "initial":  # later rounds discuss the answers, not the picture
                question_msg["images"] = images
            msgs = [{"role": "system", "content": system_for(m)}, question_msg]
            opts, warning = fit_context(m, msgs, member_options, client, max_auto)
            if warning:
                yield {"type": "warning", "member": label, "stage": name, "round": round_, "text": warning}
            streams.append(agent.answer(
                client, m.model, msgs, base={"member": label, "model": m.model, "stage": name, "round": round_},
                cfg=cfg, max_steps=tool_steps, options=opts, think=think,
                tool_defs=tools.definitions(web) if m.tools else None, sources=sources, allowed=allowed,
                research=research))
        partial: dict[str, list[str]] = {label: [] for label in prompts}
        finished: dict[str, str] = {}
        merged = merge(streams, parallel)
        try:
            async for event in merged:
                if event["type"] == "token" and event["kind"] == "content":
                    partial[event["member"]].append(event["text"])
                elif event["type"] == "done":
                    finished[event["member"]] = event["content"]
                yield event
                if _runs[run_id]["skip"]:
                    skipped = True
                    break
        finally:
            await merged.aclose()
        for label in prompts:
            if label in finished:
                answers[label] = finished[label]
            elif skipped and "".join(partial[label]).strip() and label not in answers:
                answers[label] = "".join(partial[label]) + " […]"  # cut off by "skip"; better than nothing

    async for event in stage("initial", 0, {label: shared_question for label in members}):
        yield event
    initial.update(answers)

    for r in range(1, rounds + 1):
        if skipped or len(answers) < 2:
            break
        previous = dict(answers)
        found = research.summary()
        prompts = {label: DISCUSSION_PROMPT.format(
            question=shared_question, label=f"Member {label}", own=own, others=_format(previous, members, exclude=label),
            research=f"{found}\n\n" if found else "", round=r, rounds=rounds) for label, own in previous.items()}
        async for event in stage("discussion", r, prompts):
            yield event

    if skipped:
        yield {"type": "skipped", "text": "Direkt zur finalen Antwort gesprungen."}
    if not answers:
        yield {"type": "error", "stage": "synthesis", "error": "Kein Ratsmitglied hat eine Antwort geliefert."}
        yield {"type": "end"}
        return

    final_positions = {label: answers[label] for label in members if label in answers}
    yield {"type": "positions", "members": [{"label": label, "model": members[label].model, "role": members[label].role,
                                             "content": text} for label, text in final_positions.items()]}
    found = research.summary()
    prompt = SYNTHESIS_PROMPT.format(
        question=shared_question, answers=_format(final_positions, members), research=f"{found}\n\n" if found else "",
        note="The discussion was cut short by the user. " if skipped else "")
    synth_system = "\n\n".join(p for p in (SYSTEM_PROMPT, extra_system.strip(), final_extra.strip()) if p)
    synth_messages = [{"role": "system", "content": synth_system}, {"role": "user", "content": prompt}]
    synth = Member("final", synthesizer, "final answer")
    _, synth.max_ctx = await _capabilities(client, synthesizer)
    synth_options, warning = fit_context(synth, synth_messages, member_options, client, max_auto)
    if warning:
        yield {"type": "warning", "member": "final", "stage": "synthesis", "text": warning}
    yield {"type": "phase", "stage": "synthesis", "round": rounds + 1, "members": ["final"]}
    final_text = None
    async for event in agent.answer(client, synthesizer, synth_messages, cfg=cfg, max_steps=0, options=synth_options,
                                    think=think, base={"member": "final", "model": synthesizer, "stage": "synthesis",
                                                       "round": rounds + 1}):
        if event["type"] == "done":
            final_text = event["content"]
        yield event

    if judge_model and final_text and initial:
        yield {"type": "phase", "stage": "judge", "round": rounds + 2, "members": ["judge"]}
        try:
            async for event in judge.score(client, judge_model, question, {**initial, "final": final_text}):
                if event["type"] == "judge":
                    event["members"] = {label: m.model for label, m in members.items() if label in initial}
                    event["synthesizer"] = synthesizer
                    judge.record(user, {"chat": chat_id, "question": question[:300], "judge": judge_model,
                                        "members": event["members"], "synthesizer": synthesizer,
                                        "rounds": rounds, "skipped": skipped, "scores": event["scores"],
                                        "best": event["best"]})
                yield event
        except (OllamaError, ValueError) as e:
            yield {"type": "error", "member": "judge", "stage": "judge", "error": f"Bewertung fehlgeschlagen: {e}"}
    yield {"type": "end"}
