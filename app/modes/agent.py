"""One model answering, with tool calls in a loop: used by the chat and by every Council member.

Events (each with the fields of `base`, e.g. {"model"} or {"model", "member", "stage", "round"}):
token, tool_call, tool_result, sources, status, done, error.
"""
from datetime import datetime
from typing import AsyncIterator

from app import tools
from app.ollama import OllamaClient

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
fetch_url only accepts URLs that appeared in your search results (or that the user wrote): to read a page you know, \
search for it first. Distinguish stable releases from pre-releases."""
LANGUAGE_PROMPT = ("Always answer in the language of the user's last message (a German question gets a German "
                   "answer), even when the sources are in English.")


def guidance(web: bool, use_tools: bool, steps: int) -> str:
    now = datetime.now().astimezone()
    parts = [DATE_PROMPT.format(today=f"{tools.WEEKDAYS[now.weekday()]}, {now:%Y-%m-%d}")]
    if web:
        parts.append(WEB_PROMPT)
    if use_tools and web:
        parts.append(TOOL_PROMPT.format(steps=steps))
    parts.append(LANGUAGE_PROMPT)
    return "\n\n".join(parts)


def web_sources(results: list[dict], first: int) -> list[dict]:
    return [{"kind": "web", "n": first + i, "name": r["title"] or r["url"], "url": r["url"], "date": r.get("date"),
             "text": r["snippet"]} for i, r in enumerate(results)]


async def answer(
    client: OllamaClient, model: str, messages: list[dict], *, base: dict, cfg: dict, max_steps: int,
    options: dict | None = None, think: bool | None = None, tool_defs: list[dict] | None = None,
    sources: list[dict] | None = None, allowed: set[str] | None = None, research: tools.Research | None = None,
) -> AsyncIterator[dict]:
    """Streams the reply. When the model calls tools, runs them and lets it continue. After `max_steps` calls
    the tools are taken away, so the last round is an answer from what was found so far.
    `sources` and `allowed` are updated in place, so several answers (Council members) can share them."""
    sources = sources if sources is not None else []
    allowed = allowed if allowed is not None else set()
    messages = list(messages)
    parts: list[str] = []
    steps = 0
    try:
        while True:
            calls, stats, round_text = [], None, []
            offer = tool_defs if steps < max_steps else None
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
                if steps >= max_steps:
                    messages.append({"role": "tool", "tool_name": name, "content":
                                     f"Not run: the limit of {max_steps} tool calls is reached. Answer now."})
                    continue
                steps += 1
                yield {"type": "tool_call", **base, "name": name, "arguments": args, "step": steps}
                result, found = await tools.run(name, args, cfg, allowed, research, base.get("member", ""))
                yield {"type": "tool_result", **base, "name": name, "result": result[:2000]}
                messages.append({"role": "tool", "content": result, "tool_name": name})
                known = {s.get("url") for s in sources}
                new = [r for r in found if r["url"] not in known]
                if new:
                    sources += web_sources(new, len(sources) + 1)
                    yield {"type": "sources", **base, "sources": list(sources)}
            if steps >= max_steps:
                yield {"type": "status", **base, "text": f"Werkzeug-Limit ({max_steps}) erreicht, Antwort folgt …"}
    except Exception as e:
        yield {"type": "error", **base, "error": str(e)}
