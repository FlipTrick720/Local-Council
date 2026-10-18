"""Tools that chat models with tool support may call: calculator, date/time and (with SearXNG) web search and
reading web pages. Definitions are in Ollama's (OpenAI-style) function format."""
import ast
import json
import math
import operator
from datetime import datetime

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


async def run(name: str, args: dict, cfg: dict) -> tuple[str, list[dict]]:
    """Executes one tool call. Returns the result as text for the model and the web pages it is based on
    ({"title", "url", "date", "snippet"}, empty for non-web tools) for the source list under the answer."""
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
            return websearch.format_results(results) + hint, results
        if name == "fetch_url":
            url = str(args.get("url", ""))
            text = await websearch.fetch_text(url)
            return text, [{"title": url, "url": url, "date": None, "snippet": text[:300]}]
    except websearch.WebError as e:
        return f"Fehler: {e}", []
    return f"Fehler: unbekanntes Werkzeug {name} ({json.dumps(args)[:100]})", []
