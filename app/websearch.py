"""Optional web search through a SearXNG instance (config: web.searxng_url), and reading single web pages.

SearXNG must allow the JSON format (settings.yml: search → formats: [html, json]).
Pages are only fetched from public addresses: the model must not be able to reach devices in the home network.
"""
import asyncio
import ipaddress
import socket
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

TIMEOUT = httpx.Timeout(15, connect=5)
TIME_RANGES = ("day", "week", "month", "year")
MAX_PAGE_BYTES = 2 * 1024 * 1024
NAT64 = ipaddress.ip_network("64:ff9b::/96")
HEADERS = {"User-Agent": "Mozilla/5.0 (Local Council; +https://github.com/FlipTrick720/Local-Council)"}


class WebError(RuntimeError):
    pass


async def search(base_url: str, query: str, max_results: int = 10, time_range: str | None = None) -> list[dict]:
    """Results in SearXNG's relevance order: {"title", "url", "snippet", "date"} with date "YYYY-MM-DD" when
    SearXNG knows it. `time_range` (day/week/month/year) limits results to recent pages."""
    if not base_url:
        raise WebError("Websuche ist nicht eingerichtet (Einstellungen → Websuche: SearXNG-URL).")
    params = {"q": query, "format": "json"}
    if time_range in TIME_RANGES:
        params["time_range"] = time_range
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
            r = await client.get(f"{base_url.rstrip('/')}/search", params=params)
    except httpx.HTTPError as e:
        raise WebError(f"SearXNG nicht erreichbar unter {base_url}: {e}") from e
    if r.status_code == 403:
        raise WebError("SearXNG lehnt das JSON-Format ab: in settings.yml unter search → formats „json“ ergänzen.")
    if r.status_code != 200:
        raise WebError(f"SearXNG antwortet mit HTTP {r.status_code}.")
    results, seen = [], set()
    for x in r.json().get("results", []):
        url = x.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        results.append({"title": x.get("title", ""), "url": url, "snippet": (x.get("content") or "")[:400],
                        "date": _date(x.get("publishedDate"))})
        if len(results) >= max_results:
            break
    return results


def _date(value) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _age(day: str) -> str:
    days = (date.today() - date.fromisoformat(day)).days
    return "today" if days <= 0 else "1 day ago" if days == 1 else f"{days} days ago"


def format_results(results: list[dict], offset: int = 0) -> str:
    """The results as text for the model: relevance order, each with its date, and a line naming the newest
    dated results first, so that "latest version" questions do not stop at an old page."""
    if not results:
        return "No results."
    dated = sorted((r for r in results if r["date"]), key=lambda r: r["date"], reverse=True)
    lines = []
    if dated:
        newest = ", ".join(f"[{offset + results.index(r) + 1}] {r['date']}" for r in dated[:5])
        lines.append(f"Newest dated results: {newest}. Results without a date may be old or new.\n")
    for n, r in enumerate(results, offset + 1):
        when = f"{r['date']} ({_age(r['date'])})" if r["date"] else "unknown"
        lines.append(f"[{n}] {r['title']}\nURL: {r['url']}\nDate: {when}\n{r['snippet']}\n")
    return "\n".join(lines).rstrip()


async def _pin(url: str) -> tuple[str, dict, dict]:
    """Resolves the host once, checks that every address is public, and returns the URL with the host replaced by
    the first address, plus the Host header and TLS name to send. The connection then goes exactly to the checked
    address: a DNS server cannot answer the check with a public address and the download with a LAN one
    (DNS rebinding)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebError("Nur http- und https-Adressen.")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or None,
                                        type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise WebError(f"Adresse nicht gefunden: {parsed.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip in NAT64:  # NAT64 hides an IPv4 address that may be in the LAN
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if not ip.is_global:
            raise WebError("Adressen im lokalen Netz werden nicht abgerufen.")
    ip = ipaddress.ip_address(infos[0][4][0].split("%")[0])
    host = f"[{ip}]" if ip.version == 6 else str(ip)
    port = f":{parsed.port}" if parsed.port else ""
    pinned = parsed._replace(netloc=host + port).geturl()
    headers = {"Host": parsed.hostname + port}
    extensions = {"sni_hostname": parsed.hostname} if parsed.scheme == "https" else {}
    return pinned, headers, extensions


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


async def fetch_text(url: str, max_chars: int = 6000) -> str:
    """The readable text of a public web page. Redirects are followed by hand, each target checked and pinned
    again (see _pin); at most MAX_PAGE_BYTES are downloaded."""
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=False) as client:
        for _ in range(4):
            pinned, headers, extensions = await _pin(url)
            try:
                async with client.stream("GET", pinned, headers=headers, extensions=extensions) as r:
                    if r.is_redirect and "location" in r.headers:
                        url = urljoin(url, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        raise WebError(f"Die Seite antwortet mit HTTP {r.status_code}.")
                    body = bytearray()
                    async for part in r.aiter_bytes():
                        body += part
                        if len(body) >= MAX_PAGE_BYTES:
                            break
                    kind = r.headers.get("content-type", "")
                    raw = bytes(body).decode(r.encoding or "utf-8", errors="replace")
            except httpx.HTTPError as e:
                raise WebError(f"Seite nicht erreichbar: {e}") from e
            break
        else:
            raise WebError("Zu viele Weiterleitungen.")
    text = html_to_text(raw) if "html" in kind else raw if kind.startswith("text/") else ""
    if not text:
        raise WebError(f"Kein lesbarer Text ({kind or 'unbekannter Typ'}).")
    return text[:max_chars] + ("\n… (gekürzt)" if len(text) > max_chars else "")
