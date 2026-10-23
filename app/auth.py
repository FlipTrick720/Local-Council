"""Passwords, signed session cookies, login throttling and the Host check. The accounts are in app.users.

Logging in sets a signed cookie "<user>.<expires>.<signature>" (HMAC with auth.secret from config.json) that is
valid for 30 days. The signature also covers the user's password hash, so a new password logs that user out on
all other devices.
"""
import hashlib
import hmac
import ipaddress
import secrets
import socket
import time

COOKIE = "lc_session"
MAX_AGE = 30 * 24 * 3600
ITERATIONS = 200_000
MAX_FAILURES = 10        # wrong passwords per client address …
FAILURE_WINDOW = 15 * 60  # … within this many seconds, then logins from it are refused until the window passes
LOCAL_SUFFIXES = ("", ".local", ".lan", ".home", ".home.arpa", ".internal", ".fritz.box")

_failures: dict[str, list[float]] = {}


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()


def make_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    return f"pbkdf2${salt}${_hash(password, salt)}"


def check_hash(stored: str, password: str) -> bool:
    try:
        _, salt, expected = stored.split("$")
    except ValueError:
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


def _sign(cfg: dict, user: str, expires: str, password_hash: str) -> str:
    message = f"{user}.{expires}.{password_hash}".encode()
    return hmac.new(cfg["auth"]["secret"].encode(), message, hashlib.sha256).hexdigest()


def make_token(cfg: dict, user: str, password_hash: str) -> str:
    expires = str(int(time.time()) + MAX_AGE)
    return f"{user}.{expires}.{_sign(cfg, user, expires, password_hash)}"


def token_user(token: str | None) -> str | None:
    """The user name in a token, not yet checked."""
    return token.split(".", 1)[0] if token and token.count(".") == 2 else None


def valid_token(cfg: dict, token: str | None, password_hash: str) -> bool:
    if not token or token.count(".") != 2 or not cfg["auth"].get("secret"):
        return False
    user, expires, signature = token.split(".")
    return (expires.isdigit() and int(expires) > time.time()
            and hmac.compare_digest(signature, _sign(cfg, user, expires, password_hash)))
