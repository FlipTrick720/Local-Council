"""Chat export as Markdown (readable) or JSON (complete), single chats or all as a ZIP file."""
import io
import json
import re
import zipfile

from app import chats


def filename(chat: dict, ext: str) -> str:
    slug = re.sub(r"[^\w-]+", "-", chat.get("title") or "chat", flags=re.U).strip("-")[:50] or "chat"
    return f"{chat.get('created', '')[:10]}-{slug}.{ext}".lstrip("-")


def to_json(chat: dict) -> str:
    return json.dumps(chat, indent=2, ensure_ascii=False) + "\n"


def to_markdown(chat: dict) -> str:
    lines = [f"# {chat.get('title') or 'Chat'}", ""]
    meta = [f"Modell: {chat.get('model', '')}", f"erstellt {chat.get('created', '')}", f"zuletzt {chat.get('updated', '')}"]
    lines += [" · ".join(meta), ""]
    if (chat.get("system") or "").strip():
        lines += ["**System-Prompt:**", "", *[f"> {line}" for line in chat["system"].splitlines()], ""]
    for msg in chat.get("messages", []):
        lines += ["---", ""]
        if msg["role"] == "user":
            lines += ["### Du", ""]
        else:
            lines += [f"### {msg.get('model') or 'Assistent'}", ""]
        for a in msg.get("attachments") or []:
            lines.append(f"📎 *{a.get('name', 'Datei')}*  ")
        if msg.get("attachments"):
            lines.append("")
        if msg.get("thinking"):
            lines += ["<details><summary>Gedankengang</summary>", "", msg["thinking"].strip(), "", "</details>", ""]
        if msg.get("content"):
            lines += [msg["content"].strip(), ""]
        if msg.get("error"):
            lines += [f"*Fehler: {msg['error']}*", ""]
        elif msg.get("stopped"):
            lines += ["*(abgebrochen)*", ""]
    return "\n".join(lines).rstrip() + "\n"


def all_as_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for summary in chats.list_all():
            chat = chats.get(summary["id"])
            base = filename(chat, "")[:-1] + f"-{chat['id'][-6:]}"
            z.writestr(f"markdown/{base}.md", to_markdown(chat))
            z.writestr(f"json/{base}.json", to_json(chat))
    return buffer.getvalue()
