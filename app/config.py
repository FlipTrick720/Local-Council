"""Loads and saves config.json. Missing keys are filled in from config.example.json."""
import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = ROOT / "config.example.json"
CONFIG_PATH = Path(os.environ.get("COUNCIL_CONFIG", ROOT / "config.json"))


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
    if not CONFIG_PATH.exists():
        return save(defaults())
    return _merge(defaults(), json.loads(CONFIG_PATH.read_text("utf-8")))


def update(partial: dict) -> dict:
    """Merges a partial config (e.g. {"chat": {"system": "..."}}) into the stored one."""
    return save(_merge(load(), partial))


def save(cfg: dict) -> dict:
    merged = _merge(defaults(), cfg)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", "utf-8")
    tmp.replace(CONFIG_PATH)
    return merged
