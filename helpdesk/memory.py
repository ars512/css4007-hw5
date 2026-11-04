"""User memory: key/value preferences per user in SQLite, separate from the LangGraph checkpointer.

Conversation memory (messages, steps tried) lives in the checkpointer, keyed by thread_id.
User memory lives here, keyed by user_id, and is shared by all threads of that user.
"""
import re
import sqlite3
from pathlib import Path

LANG_RE = re.compile(
    r"\bprefer(?:s)?\s+(?:the\s+)?(?:replies|answers|responses|reply|answer)?\s*(?:to be\s+)?in\s+([A-Za-z]+)"
    r"|\b(?:reply|answer|respond|write)\s+(?:to me\s+)?in\s+([A-Za-z]+)", re.I)


def extract_language(text):
    """Return a language named in a preference statement, or None."""
    m = LANG_RE.search(text or "")
    if not m:
        return None
    word = (m.group(1) or m.group(2)).capitalize()
    return None if word.lower() in ("the", "a", "an", "my") else word


class UserMemory:
    def __init__(self, path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS user_prefs ("
                          "user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
                          "PRIMARY KEY (user_id, key))")
        self.conn.commit()

    def set(self, user_id, key, value):
        self.conn.execute("INSERT INTO user_prefs VALUES (?,?,?) ON CONFLICT(user_id, key) "
                          "DO UPDATE SET value=excluded.value", (user_id, key, value))
        self.conn.commit()

    def get(self, user_id, key):
        row = self.conn.execute("SELECT value FROM user_prefs WHERE user_id=? AND key=?",
                                (user_id, key)).fetchone()
        return row[0] if row else None

    def close(self):
        self.conn.close()
