"""Loads and saves config.json. Missing keys are filled in from config.example.json."""
import copy
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = ROOT / "config.example.json"
CONFIG_PATH = Path(os.environ.get("COUNCIL_CONFIG", ROOT / "config.json"))

# Requests are handled in parallel threads; this keeps read-modify-write of config.json in one piece.
_lock = threading.RLock()


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def defaults() -> dict:
    return json.loads(EXAMPLE_PATH.read_text("utf-8"))


def load() -> dict:
    with _lock:
        if not CONFIG_PATH.exists():
            return save(defaults())
        return _merge(defaults(), json.loads(CONFIG_PATH.read_text("utf-8")))


def update(partial: dict) -> dict:
    """Applies a partial config like {"chat": {"system": "..."}} to the stored one.

    Only the section level is merged: a value inside a section (e.g. ollama.options) is replaced as a whole,
    so keys can also be removed from it.
    """
    with _lock:
        cfg = load()
        for section, values in partial.items():
            if isinstance(values, dict) and isinstance(cfg.get(section), dict):
                cfg[section].update(values)
            else:
                cfg[section] = values
        return save(cfg)


def save(cfg: dict) -> dict:
    merged = _merge(defaults(), cfg)
    with _lock:
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", "utf-8")
        tmp.replace(CONFIG_PATH)
    return merged
