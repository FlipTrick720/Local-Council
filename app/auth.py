"""Optional password for the whole app. Off as long as config.json has no auth.password_hash.

Logging in sets a signed cookie (HMAC with auth.secret) that is valid for 30 days. Setting a new password
creates a new secret, which logs out all other devices.
"""
import hashlib
import hmac
import secrets
import time

from app import config

COOKIE = "lc_session"
MAX_AGE = 30 * 24 * 3600
ITERATIONS = 200_000


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
