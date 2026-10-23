"""Chat with a single model. The client keeps the history and sends it each turn.

Before the model answers, the chat can add context to the last user message (prepare(), also used by the Council):
- knowledge base (RAG): the best-matching excerpts from app.rag,
- web search without tool support: SearXNG results for the question.
With tools switched on (and a model that supports them) the model may call app.tools in a loop (app.modes.agent).

Extra events besides token/done/error: {"type": "status", "text"}, {"type": "sources", "sources": [...]},
{"type": "tool_call", "name", "arguments"} and {"type": "tool_result", "name", "result"}.
"""
from typing import AsyncIterator

from app import rag, tools, websearch
from app.modes import agent
from app.ollama import OllamaClient, OllamaError

MAX_TOOL_STEPS = 5  # tool calls per answer; then the model has to answer with what it has


async def supports_tools(client: OllamaClient, model: str) -> bool:
    try:
        return "tools" in (await client.show(model))["capabilities"]
    except OllamaError:
        return False


async def prepare(
    client: OllamaClient, user: str, question: str, *, knowledge: str | None, web: bool, cfg: dict, base: dict,
) -> AsyncIterator[dict]:
    """Knowledge base excerpts and (if `web`) one web search for the question. Yields status events, then
    {"type": "context", "blocks": [...texts for the model...], "sources": [...]}; raises on errors."""
    blocks, sources = [], []
    if knowledge and question:
        collection = rag.get(user, knowledge)
        yield {"type": "status", **base, "text": f"Durchsuche Wissensbasis „{collection['name']}“ …"}
        hits = await rag.search(client, user, knowledge, question, cfg.get("rag", {}).get("top_k", 5))
        if hits:
            blocks.append(rag.context_block(collection["name"], hits))
            sources += [{"kind": "doc", "n": n, "name": h["name"], "page": h.get("page"), "score": h["score"],
                         "text": h["text"]} for n, h in enumerate(hits, 1)]
    if web and question:
        yield {"type": "status", **base, "text": "Suche im Web …"}
        w = cfg.get("web", {})
        results = await websearch.search(w.get("searxng_url", ""), question, w.get("max_results", 10))
        if results:
            offset = len(sources)
            blocks.append("Web search results for the question below. Cite them as [n] with the number shown.\n\n"
                          + websearch.format_results(results, offset))
            sources += agent.web_sources(results, offset + 1)
    yield {"type": "context", "blocks": blocks, "sources": sources}


def web_ctx(options: dict | None, client: OllamaClient, cfg: dict) -> dict | None:
    """Search results and read pages do not fit into Ollama's default of 4096 tokens; Ollama would silently cut
    off the beginning (including the instructions). web.num_ctx is used unless a context length is set (0 = off)."""
    if (options or {}).get("num_ctx") or client.options.get("num_ctx") or not cfg.get("web", {}).get("num_ctx"):
        return options
    return {**(options or {}), "num_ctx": cfg["web"]["num_ctx"]}


async def run(
    client: OllamaClient, model: str, messages: list[dict], system: str = "",
    options: dict | None = None, think: bool | None = None, *,
    knowledge: str | None = None, use_tools: bool = False, web: bool = False, cfg: dict | None = None,
    user: str = "", allowed_urls: set[str] | None = None,
) -> AsyncIterator[dict]:
    cfg = cfg or {}
    base = {"model": model}
    messages = [dict(m) for m in messages]
    question = messages[-1]["content"] if messages and messages[-1]["role"] == "user" else ""

    tool_defs = None
    if use_tools and await supports_tools(client, model):
        tool_defs = tools.definitions(web)
    elif use_tools:
        yield {"type": "status", **base, "text": f"{model} unterstützt keine Werkzeuge, es antwortet ohne."}

    context = {"blocks": [], "sources": []}
    try:
        # With tool calling the model searches itself; without, the web search runs once with the question.
        async for event in prepare(client, user, question, knowledge=knowledge, web=web and not tool_defs,
                                   cfg=cfg, base=base):
            if event["type"] == "context":
                context = event
            else:
                yield event
    except (rag.RagError, websearch.WebError, OllamaError) as e:
        yield {"type": "error", **base, "error": str(e)}
        return

    sources = context["sources"]
    if sources:
        yield {"type": "sources", **base, "sources": sources}
    if context["blocks"]:
        messages[-1]["content"] = "\n\n".join([*context["blocks"], f"Question: {question}"])
    if tool_defs or web:
        system = "\n\n".join(p for p in (agent.guidance(web, bool(tool_defs), MAX_TOOL_STEPS), system.strip()) if p)
    if web:
        options = web_ctx(options, client, cfg)
    if system.strip():
        messages = [{"role": "system", "content": system}, *messages]

    async for event in agent.answer(client, model, messages, base=base, cfg=cfg, max_steps=MAX_TOOL_STEPS,
                                    options=options, think=think, tool_defs=tool_defs, sources=list(sources),
                                    allowed=set(allowed_urls or ())):
        yield event
