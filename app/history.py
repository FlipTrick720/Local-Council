"""Speed-test history: one CSV per model in data/speedtest/.

The `version` column holds the start of the model's Ollama digest, so runs before and after
an `ollama pull` update of the same model tag stay distinguishable.
"""
import csv
import re

from app.config import DATA

DATA_DIR = DATA / "speedtest"
FIELDS = ["timestamp", "run_id", "mode", "concurrency", "model", "version",
          "tps", "ttft", "wall_time", "tokens", "prompt_tokens", "prompt_tps", "load_time"]
TEXT_FIELDS = {"timestamp", "run_id", "mode", "model", "version"}


def _path(model: str):
    return DATA_DIR / (re.sub(r"[^A-Za-z0-9._-]+", "_", model) + ".csv")


def append(row: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(row["model"])
    if path.exists():
        # Keep writing in the file's own column order, in case FIELDS changes later.
        with path.open(newline="", encoding="utf-8") as f:
            fields = next(csv.reader(f), FIELDS)
    else:
        fields = FIELDS
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if f.tell() == 0:
            writer.writeheader()
        writer.writerow(row)


def prune(keep: int) -> int:
    """Keeps only the newest `keep` rows per mode in every CSV. Returns the number of removed rows."""
    removed = 0
    for path in DATA_DIR.glob("*.csv"):
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames or FIELDS, list(reader)
        kept = []
        for mode in dict.fromkeys(r.get("mode") for r in rows):
            kept += [r for r in rows if r.get("mode") == mode][-keep:] if keep else []
        kept.sort(key=lambda r: r.get("timestamp") or "")
        if len(kept) == len(rows):
            continue
        removed += len(rows) - len(kept)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(kept)
        tmp.replace(path)
    return removed


def _parse(value: str):
    if value in ("", None):
        return None
    try:
        return float(value)
    except ValueError:
        return value


def load_all() -> list[dict]:
    rows = []
    for path in sorted(DATA_DIR.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows.append({k: v if k in TEXT_FIELDS else _parse(v) for k, v in row.items()})
    return sorted(rows, key=lambda r: r.get("timestamp") or "")
