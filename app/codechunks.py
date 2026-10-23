"""Source code for knowledge bases: reads a codebase from a ZIP file or (admins only) a folder on the server, and
splits it into chunks along functions and classes, so a question finds the function it is about.

Each chunk starts with "File: <path>, lines a–b" (the embedding then also knows where it is) and is named
"<path>:a–b" in the sources. Python is split with its parser, other languages at lines that look like the start
of a function, class or similar at the left margin.
"""
import ast
import io
import os
import re
import zipfile
from pathlib import Path

CHUNK_CHARS = 1500
MAX_FILE_BYTES = 300_000
MAX_FILES = 3000
MAX_TOTAL_BYTES = 30 * 1024 * 1024  # of text read from a codebase (also guards against ZIP bombs)
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", "target",
             ".idea", ".vscode", ".mypy_cache", ".pytest_cache", ".ruff_cache", "vendor", ".next", ".cache", "coverage"}
EXTENSIONS = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".java", ".kt", ".kts", ".scala",
    ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".m", ".rb", ".php", ".pl", ".lua", ".r",
    ".jl", ".dart", ".ex", ".exs", ".erl", ".hs", ".clj", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".sql",
    ".html", ".css", ".scss", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".xml",
    ".gradle", ".cmake", ".proto", ".graphql", ".tf", ".nix",
}
NAMES = {"Dockerfile", "Makefile", "CMakeLists.txt", "Justfile", "Procfile", "Gemfile"}
START = re.compile(
    r"^(export\s+|pub(\(\w+\))?\s+|public\s+|private\s+|protected\s+|internal\s+|static\s+|async\s+|default\s+)*"
    r"(def|class|function|fn|func|impl|struct|enum|trait|interface|type|module|namespace|object|const\s+\w+\s*=\s*"
    r"(async\s*)?\(|let\s+\w+\s*=\s*(async\s*)?\(|#\s|##\s|[\w<>\[\],\s]+\s+\w+\s*\([^;]*$)")


class CodeError(ValueError):
    pass


def wanted(path: str) -> bool:
    p = Path(path)
    return (p.suffix.lower() in EXTENSIONS or p.name in NAMES) and not any(
        part in SKIP_DIRS or (part.startswith(".") and part not in (".", "..")) for part in p.parts[:-1])


def _text(data: bytes) -> str | None:
    if b"\x00" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def from_zip(data: bytes) -> list[tuple[str, str]]:
    """(path, text) for the source files in a ZIP file."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise CodeError("Die ZIP-Datei ist beschädigt.") from e
    files, total = [], 0
    for info in archive.infolist():
        if info.is_dir() or not wanted(info.filename) or info.file_size > MAX_FILE_BYTES:
            continue
        total += info.file_size
        if total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
            raise CodeError(f"Zu groß: höchstens {MAX_FILES} Dateien und {MAX_TOTAL_BYTES // 2**20} MB Quelltext.")
        with archive.open(info) as f:
            text = _text(f.read(MAX_FILE_BYTES + 1)[:MAX_FILE_BYTES])
        if text and text.strip():
            files.append((info.filename, text))
    if not files:
        raise CodeError("In der ZIP-Datei ist kein Quelltext.")
    return files


def from_folder(folder: str, roots: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """The resolved folder and (relative path, text) of its source files. The folder must be inside one of `roots`
    (config: code.roots); symbolic links are not followed, so nothing outside can be read."""
    path = Path(folder).expanduser().resolve()
    allowed = [Path(r).expanduser().resolve() for r in roots if r.strip()]
    if not any(path == r or r in path.parents for r in allowed):
        raise CodeError("Dieser Ordner ist nicht freigegeben (Einstellungen → Code: erlaubte Ordner).")
    if not path.is_dir():
        raise CodeError("Ordner nicht gefunden.")
    files, total = [], 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = str(full.relative_to(path))
            if full.is_symlink() or not wanted(rel):
                continue
            size = full.stat().st_size
            if size > MAX_FILE_BYTES:
                continue
            total += size
            if total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                raise CodeError(f"Zu groß: höchstens {MAX_FILES} Dateien und {MAX_TOTAL_BYTES // 2**20} MB Quelltext.")
            text = _text(full.read_bytes())
            if text and text.strip():
                files.append((rel, text))
    if not files:
        raise CodeError("In diesem Ordner ist kein Quelltext.")
    return str(path), files


def _python_spans(text: str) -> list[tuple[int, int]] | None:
    """Line ranges (1-based, inclusive) of the top-level statements; classes that are too long by method."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    lines = text.splitlines()
    spans = []
    for node in tree.body:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        end = node.end_lineno or node.lineno
        body = "\n".join(lines[start - 1:end])
        if isinstance(node, ast.ClassDef) and len(body) > CHUNK_CHARS and node.body:
            first = min([node.body[0].lineno] + [d.lineno for d in getattr(node.body[0], "decorator_list", [])])
            spans.append((start, first - 1))
            for child in node.body:
                c_start = min([child.lineno] + [d.lineno for d in getattr(child, "decorator_list", [])])
                spans.append((c_start, child.end_lineno or child.lineno))
        else:
            spans.append((start, end))
    return spans


def _generic_spans(text: str) -> list[tuple[int, int]]:
    """Breaks before lines at the left margin that look like a definition (or a Markdown heading)."""
    lines = text.splitlines()
    starts = [1] + [i + 1 for i, line in enumerate(lines) if i and line[:1].strip() and START.match(line)]
    return [(a, b - 1) for a, b in zip(starts, starts[1:] + [len(lines) + 1]) if b > a]


def chunks(files: list[tuple[str, str]]) -> list[dict]:
    """{"text", "name": "<path>:a–b", "page": None} per piece; small neighbouring pieces are joined up to
    CHUNK_CHARS, longer ones cut."""
    out = []
    for path, text in files:
        lines = text.splitlines()
        spans = (_python_spans(text) if path.endswith((".py", ".pyi")) else None) or _generic_spans(text)
        # Cover gaps (comments, imports between definitions) by extending each span to the next one.
        spans = sorted(spans)
        joined: list[list[int]] = []
        for i, (a, _) in enumerate(spans):
            b = (spans[i + 1][0] - 1) if i + 1 < len(spans) else len(lines)
            if i == 0:
                a = 1
            size = len("\n".join(lines[a - 1:b]))
            if joined and len("\n".join(lines[joined[-1][0] - 1:joined[-1][1]])) + size <= CHUNK_CHARS:
                joined[-1][1] = b
            else:
                joined.append([a, b])
        for a, b in joined:
            piece_lines = lines[a - 1:b]
            # Very long definitions are cut into windows of about CHUNK_CHARS.
            start = a
            while piece_lines:
                take, size = 0, 0
                while take < len(piece_lines) and (size + len(piece_lines[take]) < CHUNK_CHARS or take == 0):
                    size += len(piece_lines[take]) + 1
                    take += 1
                body = "\n".join(piece_lines[:take]).strip("\n")
                end = start + take - 1
                if body.strip():
                    out.append({"text": f"File: {path}, lines {start}–{end}\n{body}", "name": f"{path}:{start}–{end}",
                                "page": None})
                piece_lines = piece_lines[take:]
                start = end + 1
    return out
