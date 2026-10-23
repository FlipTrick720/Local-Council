"""Chat export as Markdown (readable) or JSON (complete), single chats or all as a ZIP file."""
import io
import json
import re
import zipfile

from app import chats
from app.judge import CRITERIA


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
        if msg.get("council"):
            lines += _council_details(msg["council"])
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
        if msg.get("judge"):
            lines += _judge_table(msg["judge"])
    return "\n".join(lines).rstrip() + "\n"


def _council_details(council: dict) -> list[str]:
    """The rounds of a Council answer, folded away like the thinking."""
    roles = {m["label"]: m for m in council.get("members", [])}
    lines = ["<details><summary>Beratung des Councils</summary>", ""]
    for stage in council.get("stages", []):
        title = "Erste Antworten" if stage["stage"] == "initial" else f"Diskussionsrunde {stage['round']}"
        lines += [f"#### {title}", ""]
        for label, answer in stage.get("answers", {}).items():
            m = roles.get(label, {})
            who = " · ".join(x for x in (m.get("role"), answer.get("model") or m.get("model")) if x)
            lines += [f"**Mitglied {label}** ({who})", "", (answer.get("content") or answer.get("error") or "–").strip(), ""]
    return lines + ["</details>", ""]


def _judge_table(j: dict) -> list[str]:
    lines = [f"**Bewertung durch {j.get('model')}**", "",
             "| Antwort | " + " | ".join(CRITERIA.values()) + " | Ø |", "|---" * (len(CRITERIA) + 2) + "|"]
    for label, s in j.get("scores", {}).items():
        name = "Finale Antwort" if label == "final" else f"Mitglied {label} allein ({j.get('members', {}).get(label, '?')})"
        lines.append(f"| {name}{' ★' if label == j.get('best') else ''} | "
                     + " | ".join(str(s.get(c, '–')) for c in CRITERIA) + f" | {s.get('total', '–')} |")
    return lines + [""]


def all_as_zip(user: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for summary in chats.list_all(user):
            chat = chats.get(user, summary["id"])
            base = filename(chat, "")[:-1] + f"-{chat['id'][-6:]}"
            z.writestr(f"markdown/{base}.md", to_markdown(chat))
            z.writestr(f"json/{base}.json", to_json(chat))
    return buffer.getvalue()
