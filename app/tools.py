"""Tools that chat models with tool support may call: calculator, date/time and (with SearXNG) web search and
reading web pages. Definitions are in Ollama's (OpenAI-style) function format.

fetch_url only reads pages whose URL came up in a web_search of the same request or that the user typed in a
message ("allowed" URLs). A page or document with hidden instructions could otherwise make the model fetch
an attacker's URL with chat content in it.
"""
import ast
import asyncio
import json
import math
import operator
import re
from datetime import datetime
from urllib.parse import urlparse

from app import websearch

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


CALCULATOR = _function(
    "calculator", "Evaluates a math expression exactly, e.g. '17*23' or 'sqrt(2)*pi'. Use it for any arithmetic.",
    {"expression": {"type": "string", "description": "Python-style math expression"}}, ["expression"])
DATETIME = _function("current_datetime", "Returns the current local date, weekday and time.", {}, [])
WEB_SEARCH = _function(
    "web_search",
    "Searches the web and returns up to 10 results with title, URL, date (if known) and snippet. Use it for current "
    "events, versions, prices, or anything that may have changed since your training. Snippets can be outdated: "
    "check the dates, and read the best (preferably official) page with fetch_url before answering.",
    {"query": {"type": "string", "description": "search query, e.g. 'Ollama releases' or 'ollama latest version github'"},
     "time_range": {"type": "string", "enum": list(websearch.TIME_RANGES),
                    "description": "optional: only results from the last day/week/month/year"}},
    ["query"])
FETCH_URL = _function(
    "fetch_url",
    "Reads the text of a web page, e.g. the official site, the GitHub releases page or another result found with "
    "web_search.",
    {"url": {"type": "string", "description": "http(s) URL"}}, ["url"])


def definitions(web: bool) -> list[dict]:
    return [CALCULATOR, DATETIME, *([WEB_SEARCH, FETCH_URL] if web else [])]


# ---------- URLs fetch_url may read ----------

URL_RE = re.compile(r"https?://[^\s<>\"'`]+")


def normalize_url(url: str) -> str:
    """Scheme, fragment, case of the host and a trailing slash do not matter; path and query do."""
    p = urlparse(url.strip().rstrip(".,;:!?)]}>\"'"))
    port = f":{p.port}" if p.port else ""
    query = f"?{p.query}" if p.query else ""
    return f"{(p.hostname or '').lower()}{port}{p.path.rstrip('/')}{query}"


def urls_in(text: str) -> set[str]:
    return {normalize_url(u) for u in URL_RE.findall(text or "")}


# ---------- Calculator: a safe subset of Python expressions ----------

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_NAMES = {k: getattr(math, k) for k in (
    "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "log10", "log2", "exp", "floor", "ceil",
    "factorial", "radians", "degrees", "pi", "e")}
_NAMES.update(abs=abs, round=round, min=min, max=max)


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Exponent zu groß")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _NAMES and not node.keywords:
        args = [_eval(a) for a in node.args]
        if node.func.id == "factorial" and args and args[0] > 1000:
            raise ValueError("Zahl zu groß")
        return _NAMES[node.func.id](*args)
    raise ValueError(f"nicht erlaubt: {ast.dump(node)[:60]}")


def calculate(expression: str) -> str:
    try:
        result = _eval(ast.parse(expression.replace("^", "**"), mode="eval"))
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        return f"Fehler: {e}"
    return str(result)


class Research:
    """Web searches and read pages shared by the members of one Council run: the same search or page is done once,
    later (or simultaneous) calls get the stored result, and summary() lists everything for the next round."""

    SUMMARY_CHARS = 4000

    def __init__(self):
        self._done: dict[tuple, asyncio.Future] = {}
        self.log: list[dict] = []  # {"by", "name", "key", "result"} in the order they were done

    @staticmethod
    def key(name: str, args: dict) -> tuple | None:
        if name == "web_search":
            return name, " ".join(str(args.get("query", "")).lower().split()), args.get("time_range")
        if name == "fetch_url":
            return name, normalize_url(str(args.get("url", "")))
        return None

    async def once(self, name: str, args: dict, by: str, run) -> tuple[str, list[dict]]:
        key = self.key(name, args)
        if key in self._done:
            result, found = await self._done[key]
            return f"(Already looked up by another council member.)\n{result}", found
        future = self._done[key] = asyncio.get_running_loop().create_future()
        try:
            result, found = await run()
        except BaseException as e:
            del self._done[key]
            future.cancel()
            raise e
        future.set_result((result, found))
        if not result.startswith(("Fehler", "Error")):
            self.log.append({"by": by, "name": name, "key": key[1], "result": result})
        return result, found

    def summary(self) -> str:
        """What the council has looked up so far, shortened, for the members' next prompt."""
        if not self.log:
            return ""
        lines, room = [], self.SUMMARY_CHARS
        for item in self.log:
            what = f"search \"{item['key']}\"" if item["name"] == "web_search" else f"page {item['key']}"
            text = f"- {item['by']}: {what}\n{item['result'][:900]}"
            if len(text) > room:
                break
            lines.append(text)
            room -= len(text)
        return ("Research the council already did (reuse it instead of repeating the same search):\n"
                + "\n\n".join(lines))


async def run(name: str, args: dict, cfg: dict, allowed: set[str], research: Research | None = None,
              by: str = "") -> tuple[str, list[dict]]:
    """Executes one tool call. Returns the result as text for the model and the web pages it is based on
    ({"title", "url", "date", "snippet"}, empty for non-web tools) for the source list under the answer.
    `allowed` holds the normalized URLs fetch_url may read; web_search adds its results to it.
    With `research` (Council), web searches and pages are shared between the members."""
    if research and Research.key(name, args) and (name != "fetch_url" or normalize_url(str(args.get("url", ""))) in allowed):
        return await research.once(name, args, by, lambda: _run(name, args, cfg, allowed))
    return await _run(name, args, cfg, allowed)


async def _run(name: str, args: dict, cfg: dict, allowed: set[str]) -> tuple[str, list[dict]]:
    web = cfg.get("web", {})
    try:
        if name == "calculator":
            return calculate(str(args.get("expression", ""))), []
        if name == "current_datetime":
            now = datetime.now().astimezone()
            return f"{WEEKDAYS[now.weekday()]}, {now:%d.%m.%Y %H:%M} ({now.tzname()})", []
        if name == "web_search":
            results = await websearch.search(web.get("searxng_url", ""), str(args.get("query", "")),
                                             web.get("max_results", 10), args.get("time_range"))
            # The reminder sits right next to the results; small models follow it more reliably there.
            hint = ("\n\nNext step: snippets are short and may be outdated. Read the most relevant official page "
                    "(e.g. GitHub releases, official website) with fetch_url before answering.") if results else ""
            allowed.update(normalize_url(r["url"]) for r in results)
            return websearch.format_results(results) + hint, results
        if name == "fetch_url":
            url = str(args.get("url", ""))
            if normalize_url(url) not in allowed:
                return ("Error: fetch_url only reads URLs from web_search results or URLs the user wrote. "
                        "Search for the page first (e.g. 'ollama releases github'), then fetch one of the result "
                        "URLs exactly as listed."), []
            text = await websearch.fetch_text(url)
            return text, [{"title": url, "url": url, "date": None, "snippet": text[:300]}]
    except websearch.WebError as e:
        return f"Fehler: {e}", []
    return f"Fehler: unbekanntes Werkzeug {name} ({json.dumps(args)[:100]})", []
