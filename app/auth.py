"""Optional password for the whole app. Off as long as config.json has no auth.password_hash.

Logging in sets a signed cookie (HMAC with auth.secret) that is valid for 30 days. Setting a new password
creates a new secret, which logs out all other devices.
"""
import hashlib
import hmac
import ipaddress
import secrets
import socket
import time

from app import config

COOKIE = "lc_session"
MAX_AGE = 30 * 24 * 3600
ITERATIONS = 200_000
MAX_FAILURES = 10        # wrong passwords per client address …
FAILURE_WINDOW = 15 * 60  # … within this many seconds, then logins from it are refused until the window passes
LOCAL_SUFFIXES = ("", ".local", ".lan", ".home", ".home.arpa", ".internal", ".fritz.box")

_failures: dict[str, list[float]] = {}


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("auth", {}).get("password_hash"))


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()


def set_password(password: str) -> None:
    """An empty password switches the protection off."""
    salt = secrets.token_hex(16)
    stored = f"pbkdf2${salt}${_hash(password, salt)}" if password else ""
    config.update({"auth": {"password_hash": stored, "secret": secrets.token_hex(32)}})


def check_password(cfg: dict, password: str) -> bool:
    try:
        _, salt, expected = cfg["auth"]["password_hash"].split("$")
    except (KeyError, ValueError):
        return False
    return hmac.compare_digest(_hash(password, salt), expected)


def login_blocked(client: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(client, []) if now - t < FAILURE_WINDOW]
    _failures[client] = recent
    return len(recent) >= MAX_FAILURES


def record_failure(client: str) -> None:
    _failures.setdefault(client, []).append(time.time())


def reset_failures(client: str) -> None:
    _failures.pop(client, None)


def host_allowed(host_header: str, cfg: dict) -> bool:
    """Protection against DNS rebinding: a website could point its own domain at this machine and then read
    the API from the visitor's browser. Requests are therefore only accepted for IP addresses, localhost, this
    machine's own name (also with .local, .lan, .fritz.box …) and the names in server.allowed_hosts
    ("*" allows everything, ".example.org" a whole domain)."""
    host = (host_header or "").strip().lower()
    host = host[1:host.find("]")] if host.startswith("[") else host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host == "localhost" or host.endswith(".localhost"):
        return True
    machine = socket.gethostname().lower().split(".")[0]
    if any(host == machine + suffix for suffix in LOCAL_SUFFIXES):
        return True
    for allowed in cfg.get("server", {}).get("allowed_hosts", []):
        allowed = allowed.lower()
        if allowed == "*" or host == allowed or (allowed.startswith(".") and host.endswith(allowed)):
            return True
    return False


def _sign(cfg: dict, expires: str) -> str:
    return hmac.new(cfg["auth"]["secret"].encode(), expires.encode(), hashlib.sha256).hexdigest()


def make_token(cfg: dict) -> str:
    expires = str(int(time.time()) + MAX_AGE)
    return f"{expires}.{_sign(cfg, expires)}"


def valid_token(cfg: dict, token: str | None) -> bool:
    if not token or "." not in token or not cfg["auth"].get("secret"):
        return False
    expires, signature = token.split(".", 1)
    return expires.isdigit() and int(expires) > time.time() and hmac.compare_digest(signature, _sign(cfg, expires))
