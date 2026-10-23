"""User accounts: each person has their own chats, knowledge bases and settings; admins also manage the server.

Stored in data/users.json as {"users": {name: {"password_hash", "admin", "created"}}}. Per user:
  data/chats/<name>/       chats (app.chats)
  data/rag/<name>/         knowledge bases (app.rag)
  data/settings/<name>.json  the user's own config sections (USER_SECTIONS), on top of config.json
Uploads (data/uploads/) are shared: their id is a hash of the content, so only who has the file knows it.

Before accounts existed there was one optional password in config.json (auth.password_hash). On first use that
setup becomes the account "admin" with the same password, and the existing chats and knowledge bases are moved
to it. As long as the only account has no password, the app is open and everyone is that account.
"""
import json
import re
import secrets
import shutil
import threading
import time

from app import auth, config
from app.config import ROOT

DATA = ROOT / "data"
USERS_PATH = DATA / "users.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
FIRST_ADMIN = "admin"

# Config sections every user keeps for themselves; the rest of config.json is the server's (admins only).
USER_SECTIONS = ("chat", "council", "rag")

_lock = threading.RLock()


class UserError(ValueError):
    pass


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", "utf-8")
    tmp.replace(path)


def _migrate() -> dict:
    """Turns the single-password setup into the account "admin" that owns all existing data."""
    cfg = config.load()
    users = {FIRST_ADMIN: {"password_hash": cfg["auth"].get("password_hash", ""), "admin": True, "created": time.time()}}
    for kind, pattern in (("chats", "*.json"), ("rag", "kb-*")):
        folder = DATA / kind
        target = folder / FIRST_ADMIN
        for path in list(folder.glob(pattern)):
            target.mkdir(parents=True, exist_ok=True)
            path.replace(target / path.name)
    _write_json(USERS_PATH, {"users": users})
    config.update({"auth": {"password_hash": "", "secret": cfg["auth"].get("secret") or secrets.token_hex(32)}})
    return users


def _all() -> dict:
    with _lock:
        if not USERS_PATH.exists():
            return _migrate()
        return json.loads(USERS_PATH.read_text("utf-8"))["users"]


def _save(users: dict) -> None:
    _write_json(USERS_PATH, {"users": users})


def public(name: str, user: dict) -> dict:
    return {"name": name, "admin": bool(user.get("admin")), "has_password": bool(user.get("password_hash")),
            "created": user.get("created")}


def list_all() -> list[dict]:
    return sorted((public(n, u) for n, u in _all().items()), key=lambda u: u["name"])


def get(name: str) -> dict | None:
    user = _all().get(name)
    return public(name, user) if user else None


def open_user() -> str | None:
    """The account everyone is while the app is open: the only one, as long as it has no password."""
    users = _all()
    if len(users) == 1:
        name, user = next(iter(users.items()))
        if not user.get("password_hash"):
            return name
    return None


def check_password(name: str, password: str) -> bool:
    user = _all().get(name)
    return bool(user and user.get("password_hash")) and auth.check_hash(user["password_hash"], password)


def create(name: str, password: str, admin: bool = False) -> dict:
    name = (name or "").strip().lower()
    if not valid_name(name):
        raise UserError("Name: Kleinbuchstaben, Ziffern, - und _, höchstens 32 Zeichen.")
    if not password:
        raise UserError("Bitte ein Passwort angeben.")
    with _lock:
        users = _all()
        if name in users:
            raise UserError(f"„{name}“ gibt es schon.")
        users[name] = {"password_hash": auth.make_hash(password), "admin": admin, "created": time.time()}
        _save(users)
        return public(name, users[name])


def update(name: str, password: str | None = None, admin: bool | None = None) -> dict:
    """A new password logs the user out on all devices (their session tokens are bound to the hash)."""
    with _lock:
        users = _all()
        if name not in users:
            raise UserError("Benutzer nicht gefunden.")
        if admin is False and users[name].get("admin") and _admin_count(users) == 1:
            raise UserError("Mindestens ein Admin muss bleiben.")
        if password is not None:
            users[name]["password_hash"] = auth.make_hash(password) if password else ""
        if admin is not None:
            users[name]["admin"] = admin
        _save(users)
        return public(name, users[name])


def delete(name: str) -> None:
    """Removes the account with its chats, knowledge bases and settings."""
    with _lock:
        users = _all()
        if name not in users:
            raise UserError("Benutzer nicht gefunden.")
        if users[name].get("admin") and _admin_count(users) == 1:
            raise UserError("Der letzte Admin kann nicht gelöscht werden.")
        del users[name]
        _save(users)
        for folder in (DATA / "chats" / name, DATA / "rag" / name):
            shutil.rmtree(folder, ignore_errors=True)
        (DATA / "settings" / f"{name}.json").unlink(missing_ok=True)


def _admin_count(users: dict) -> int:
    return sum(1 for u in users.values() if u.get("admin"))


# ---------- Sessions ----------

def make_token(name: str) -> str:
    return auth.make_token(config.load(), name, _all()[name].get("password_hash", ""))


def from_token(token: str | None) -> str | None:
    """The user name of a valid session cookie, or None."""
    name = auth.token_user(token)
    user = _all().get(name) if name else None
    if user and auth.valid_token(config.load(), token, user.get("password_hash", "")):
        return name
    return None


# ---------- Per-user settings ----------

def _settings_path(name: str):
    if not valid_name(name):
        raise UserError("Ungültiger Benutzer.")
    return DATA / "settings" / f"{name}.json"


def config_for(name: str) -> dict:
    """config.json with the user's own sections laid over it. Unset keys come from config.json, which thereby
    holds the defaults for new users (e.g. the system prompt presets)."""
    cfg = config.load()
    path = _settings_path(name)
    own = json.loads(path.read_text("utf-8")) if path.exists() else {}
    for section in USER_SECTIONS:
        cfg[section] = {**cfg[section], **own.get(section, {})}
    return cfg


def update_settings(name: str, partial: dict) -> None:
    """Like config.update, but only for USER_SECTIONS and stored per user."""
    with _lock:
        path = _settings_path(name)
        own = json.loads(path.read_text("utf-8")) if path.exists() else {}
        for section in USER_SECTIONS:
            if isinstance(partial.get(section), dict):
                own[section] = {**own.get(section, {}), **partial[section]}
        _write_json(path, own)
