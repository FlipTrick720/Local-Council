"""Chat with a single model. The client keeps the history and sends it each turn.

Before the model answers, the chat can add context to the last user message:
- knowledge base (RAG): the best-matching excerpts from app.rag,
- web search without tool support: SearXNG results for the question.
With tools switched on (and a model that supports them) the model may call app.tools in a loop instead.

Extra events besides token/done/error: {"type": "status", "text"}, {"type": "sources", "sources": [...]},
{"type": "tool_call", "name", "arguments"} and {"type": "tool_result", "name", "result"}.
"""
from datetime import datetime
from typing import AsyncIterator

from app import rag, tools, websearch
from app.ollama import OllamaClient, OllamaError

MAX_TOOL_STEPS = 5  # tool calls per answer; then the model has to answer with what it has

# Put in front of the system prompt when tools or web search are on. Small models otherwise answer
# "latest version" questions from their training data or from the first outdated snippet.
DATE_PROMPT = "Today is {today}. Your training data is older than that; do not rely on it for current facts."
WEB_PROMPT = """When the user asks about the newest, latest or current version, release, price, event or similar:
- Do not answer from memory. Look at the dates of the sources; results without a date may be outdated.
- Prefer primary sources: the official website, the official documentation, the GitHub releases page.
- If sources contradict each other, use the newest reliable source and say which date it has.
- End your answer with a line "Quellen:" followed by the URLs you actually used, one per line."""
TOOL_PROMPT = """You may call tools in several steps (at most {steps} calls): first web_search, then fetch_url on \
the most relevant official result to verify it, and search again with a better query if the results are not good \
enough. Search snippets are short and often outdated: for versions, releases and other current facts, always read \
the primary source (e.g. the GitHub releases page or the official website) with fetch_url before you answer. \
Distinguish stable releases from pre-releases."""
LANGUAGE_PROMPT = ("Always answer in the language of the user's last message (a German question gets a German "
                   "answer), even when the sources are in English.")


def guidance(web: bool, use_tools: bool) -> str:
    now = datetime.now().astimezone()
    parts = [DATE_PROMPT.format(today=f"{tools.WEEKDAYS[now.weekday()]}, {now:%Y-%m-%d}")]
    if web:
        parts.append(WEB_PROMPT)
    if use_tools and web:
        parts.append(TOOL_PROMPT.format(steps=MAX_TOOL_STEPS))
    parts.append(LANGUAGE_PROMPT)
    return "\n\n".join(parts)


def web_sources(results: list[dict], first: int) -> list[dict]:
    return [{"kind": "web", "n": first + i, "name": r["title"] or r["url"], "url": r["url"], "date": r.get("date"),
             "text": r["snippet"]} for i, r in enumerate(results)]


async def run(
    client: OllamaClient, model: str, messages: list[dict], system: str = "",
    options: dict | None = None, think: bool | None = None, *,
    knowledge: str | None = None, use_tools: bool = False, web: bool = False, cfg: dict | None = None,
    user: str = "",
) -> AsyncIterator[dict]:
    cfg = cfg or {}
    base = {"model": model}
    messages = [dict(m) for m in messages]
    question = messages[-1]["content"] if messages and messages[-1]["role"] == "user" else ""
    context, sources = [], []

    capabilities = []
    if use_tools:
        try:
            capabilities = (await client.show(model))["capabilities"]
        except OllamaError:
            pass
    tool_defs = None
    if use_tools and "tools" in capabilities:
        tool_defs = tools.definitions(web)
    elif use_tools:
        yield {"type": "status", **base, "text": f"{model} unterstützt keine Werkzeuge, es antwortet ohne."}

    try:
        if knowledge and question:
            collection = rag.get(user, knowledge)
            yield {"type": "status", **base, "text": f"Durchsuche Wissensbasis „{collection['name']}“ …"}
            hits = await rag.search(client, user, knowledge, question, cfg.get("rag", {}).get("top_k", 5))
            if hits:
                context.append(rag.context_block(collection["name"], hits))
                sources += [{"kind": "doc", "n": n, "name": h["name"], "page": h.get("page"), "score": h["score"],
                             "text": h["text"]} for n, h in enumerate(hits, 1)]

        # Without tool calling the web search runs once, with the question as query.
        if web and question and not tool_defs:
            yield {"type": "status", **base, "text": "Suche im Web …"}
            w = cfg.get("web", {})
            results = await websearch.search(w.get("searxng_url", ""), question, w.get("max_results", 10))
            if results:
                offset = len(sources)
                context.append("Web search results for the question below. Cite them as [n] with the number shown.\n\n"
                               + websearch.format_results(results, offset))
                sources += web_sources(results, offset + 1)
    except (rag.RagError, websearch.WebError, OllamaError) as e:
        yield {"type": "error", **base, "error": str(e)}
        return

    if sources:
        yield {"type": "sources", **base, "sources": sources}
    if context:
        messages[-1]["content"] = "\n\n".join([*context, f"Question: {question}"])
    if tool_defs or web:
        system = "\n\n".join(p for p in (guidance(web, bool(tool_defs)), system.strip()) if p)
    if web and not (options or {}).get("num_ctx") and not client.options.get("num_ctx"):
        # Search results and read pages do not fit into Ollama's default of 4096 tokens; Ollama would silently
        # cut off the beginning (including the instructions above). Set web.num_ctx to 0 to keep the default.
        options = {**(options or {}), **({"num_ctx": cfg["web"]["num_ctx"]} if cfg.get("web", {}).get("num_ctx") else {})}
    if system.strip():
        messages = [{"role": "system", "content": system}, *messages]

    async for event in _answer(client, model, messages, options, think, tool_defs, cfg, sources):
        yield event


async def _answer(client, model, messages, options, think, tool_defs, cfg, sources) -> AsyncIterator[dict]:
    """Streams the reply. When the model calls tools, runs them and lets it continue. After MAX_TOOL_STEPS
    calls the tools are taken away, so the last round is an answer from what was found so far."""
    base = {"model": model}
    parts: list[str] = []
    sources = list(sources)
    steps = 0
    try:
        while True:
            calls, stats, round_text = [], None, []
            offer = tool_defs if steps < MAX_TOOL_STEPS else None
            async for chunk in client.chat_stream(model, messages, options, think, offer):
                if chunk["type"] == "stats":
                    stats = {k: v for k, v in chunk.items() if k != "type"}
                elif chunk["type"] == "tool_calls":
                    calls += chunk["calls"]
                else:
                    if chunk["type"] == "content":
                        parts.append(chunk["text"])
                        round_text.append(chunk["text"])
                    yield {"type": "token", **base, "kind": chunk["type"], "text": chunk["text"]}
            if not calls or offer is None:  # (a model may still emit calls after the tools were taken away)
                yield {"type": "done", **base, "content": "".join(parts), "stats": stats}
                return
            messages.append({"role": "assistant", "content": "".join(round_text), "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                name, args = fn.get("name", ""), fn.get("arguments") or {}
                if steps >= MAX_TOOL_STEPS:
                    messages.append({"role": "tool", "tool_name": name, "content":
                                     f"Not run: the limit of {MAX_TOOL_STEPS} tool calls is reached. Answer now."})
                    continue
                steps += 1
                yield {"type": "tool_call", **base, "name": name, "arguments": args, "step": steps}
                result, found = await tools.run(name, args, cfg)
                yield {"type": "tool_result", **base, "name": name, "result": result[:2000]}
                messages.append({"role": "tool", "content": result, "tool_name": name})
                known = {s.get("url") for s in sources}
                new = [r for r in found if r["url"] not in known]
                if new:
                    sources += web_sources(new, len(sources) + 1)
                    yield {"type": "sources", **base, "sources": sources}
            if steps >= MAX_TOOL_STEPS:
                yield {"type": "status", **base, "text": f"Werkzeug-Limit ({MAX_TOOL_STEPS}) erreicht, Antwort folgt …"}
    except Exception as e:
        yield {"type": "error", **base, "error": str(e)}
