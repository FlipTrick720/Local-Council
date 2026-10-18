"""Chat with a single model. The client keeps the history and sends it each turn.

Before the model answers, the chat can add context to the last user message:
- knowledge base (RAG): the best-matching excerpts from app.rag,
- web search without tool support: SearXNG results for the question.
With tools switched on (and a model that supports them) the model may call app.tools in a loop instead.

Extra events besides token/done/error: {"type": "status", "text"}, {"type": "sources", "sources": [...]},
{"type": "tool_call", "name", "arguments"} and {"type": "tool_result", "name", "result"}.
"""
from typing import AsyncIterator

from app import rag, tools, websearch
from app.ollama import OllamaClient, OllamaError

MAX_TOOL_ROUNDS = 6


async def run(
    client: OllamaClient, model: str, messages: list[dict], system: str = "",
    options: dict | None = None, think: bool | None = None, *,
    knowledge: str | None = None, use_tools: bool = False, web: bool = False, cfg: dict | None = None,
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
            collection = rag.get(knowledge)
            yield {"type": "status", **base, "text": f"Durchsuche Wissensbasis „{collection['name']}“ …"}
            hits = await rag.search(client, knowledge, question, cfg.get("rag", {}).get("top_k", 5))
            if hits:
                context.append(rag.context_block(collection["name"], hits))
                sources += [{"kind": "doc", "n": n, "name": h["name"], "page": h.get("page"), "score": h["score"],
                             "text": h["text"]} for n, h in enumerate(hits, 1)]

        # Without tool calling the web search runs once, with the question as query.
        if web and question and not tool_defs:
            yield {"type": "status", **base, "text": "Suche im Web …"}
            w = cfg.get("web", {})
            results = await websearch.search(w.get("searxng_url", ""), question, w.get("max_results", 5))
            if results:
                offset = len(sources)
                context.append("Web search results for the question below. Cite them as [n] with the number shown.\n\n"
                               + "\n\n".join(f"[{offset + n}] {r['title']} ({r['url']})\n{r['snippet']}"
                                             for n, r in enumerate(results, 1)))
                sources += [{"kind": "web", "n": offset + n, "name": r["title"], "url": r["url"], "text": r["snippet"]}
                            for n, r in enumerate(results, 1)]
    except (rag.RagError, websearch.WebError, OllamaError) as e:
        yield {"type": "error", **base, "error": str(e)}
        return

    if sources:
        yield {"type": "sources", **base, "sources": sources}
    if context:
        messages[-1]["content"] = "\n\n".join([*context, f"Question: {question}"])
    if system.strip():
        messages = [{"role": "system", "content": system}, *messages]

    async for event in _answer(client, model, messages, options, think, tool_defs, cfg):
        yield event


async def _answer(client, model, messages, options, think, tool_defs, cfg) -> AsyncIterator[dict]:
    """Streams the reply. When the model calls tools, runs them and lets it continue (at most MAX_TOOL_ROUNDS)."""
    base = {"model": model}
    parts: list[str] = []
    try:
        for _ in range(MAX_TOOL_ROUNDS + 1):
            calls, stats, round_text = [], None, []
            async for chunk in client.chat_stream(model, messages, options, think, tool_defs):
                if chunk["type"] == "stats":
                    stats = {k: v for k, v in chunk.items() if k != "type"}
                elif chunk["type"] == "tool_calls":
                    calls += chunk["calls"]
                else:
                    if chunk["type"] == "content":
                        parts.append(chunk["text"])
                        round_text.append(chunk["text"])
                    yield {"type": "token", **base, "kind": chunk["type"], "text": chunk["text"]}
            if not calls:
                yield {"type": "done", **base, "content": "".join(parts), "stats": stats}
                return
            messages.append({"role": "assistant", "content": "".join(round_text), "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                name, args = fn.get("name", ""), fn.get("arguments") or {}
                yield {"type": "tool_call", **base, "name": name, "arguments": args}
                result = await tools.run(name, args, cfg)
                yield {"type": "tool_result", **base, "name": name, "result": result[:2000]}
                messages.append({"role": "tool", "content": result, "tool_name": name})
        yield {"type": "error", **base, "error": f"Abgebrochen: mehr als {MAX_TOOL_ROUNDS} Werkzeug-Runden."}
    except Exception as e:
        yield {"type": "error", **base, "error": str(e)}
