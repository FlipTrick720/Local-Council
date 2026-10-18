"""Optional web search through a SearXNG instance (config: web.searxng_url), and reading single web pages.

SearXNG must allow the JSON format (settings.yml: search → formats: [html, json]).
Pages are only fetched from public addresses: the model must not be able to reach devices in the home network.
"""
import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

TIMEOUT = httpx.Timeout(15, connect=5)
MAX_PAGE_BYTES = 2 * 1024 * 1024
NAT64 = ipaddress.ip_network("64:ff9b::/96")
HEADERS = {"User-Agent": "Mozilla/5.0 (Local Council; +https://github.com/FlipTrick720/Local-Council)"}


class WebError(RuntimeError):
    pass


async def search(base_url: str, query: str, max_results: int = 5) -> list[dict]:
    if not base_url:
        raise WebError("Websuche ist nicht eingerichtet (Einstellungen → Websuche: SearXNG-URL).")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
            r = await client.get(f"{base_url.rstrip('/')}/search", params={"q": query, "format": "json"})
    except httpx.HTTPError as e:
        raise WebError(f"SearXNG nicht erreichbar unter {base_url}: {e}") from e
    if r.status_code == 403:
        raise WebError("SearXNG lehnt das JSON-Format ab: in settings.yml unter search → formats „json“ ergänzen.")
    if r.status_code != 200:
        raise WebError(f"SearXNG antwortet mit HTTP {r.status_code}.")
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
        for x in r.json().get("results", [])[:max_results]
    ]


def format_results(results: list[dict]) -> str:
    if not results:
        return "Keine Ergebnisse."
    return "\n\n".join(f"[{n}] {r['title']}\n{r['url']}\n{r['snippet']}" for n, r in enumerate(results, 1))


async def _check_public(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebError("Nur http- und https-Adressen.")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, None)
    except socket.gaierror as e:
        raise WebError(f"Adresse nicht gefunden: {parsed.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip in NAT64:  # NAT64 hides an IPv4 address that may be in the LAN
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if not ip.is_global:
            raise WebError("Adressen im lokalen Netz werden nicht abgerufen.")


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
    """The readable text of a public web page. Redirects are followed by hand, each target checked again;
    at most MAX_PAGE_BYTES are downloaded."""
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=False) as client:
        for _ in range(4):
            await _check_public(url)
            try:
                async with client.stream("GET", url) as r:
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
