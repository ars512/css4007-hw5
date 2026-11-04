"""Loading of the supplied fixtures in data/."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def load_json(name):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def helpdesk():
    return load_json("helpdesk.json")


def service_status():
    return load_json("service_status.json")


def ticket_template():
    return load_json("ticket_template.json")["ticket"]


def storage_dir(override=None):
    """Where live memory lives (git-ignored). Override > env var > local-storage."""
    path = override or os.environ.get("HELPDESK_STORAGE_DIR") or "local-storage"
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def detect_topics(text):
    """Topics (wifi/password) whose aliases occur in text, case-insensitive."""
    low = text.lower()
    aliases = helpdesk()["topic_aliases"]
    return [t for t, words in aliases.items() if any(w in low for w in words)]
