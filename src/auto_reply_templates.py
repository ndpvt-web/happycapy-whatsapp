"""Status-aware auto-reply templates.

Built-in templates for common statuses (busy, dnd, away) plus
user-created custom templates. Managed via admin commands.

Security:
- Templates are stored locally in SQLite, no external data.
- Template content is user-controlled, not injected into AI prompts.
"""

import sqlite3
from pathlib import Path


# Built-in templates (always available, cannot be deleted)
BUILTIN_TEMPLATES: dict[str, str] = {
    "busy": "Hey! I'm currently busy. I'll get back to you as soon as I can.",
    "acknowledge": "Got it, thanks! I'll look into this.",
    "away": "I'm away right now. Will respond when I'm back.",
    "urgent_only": "I'm only handling urgent matters right now. If this is urgent, please let me know.",
    "weekend": "Taking some time off. I'll respond on Monday.",
}

# Status -> template name mapping
_STATUS_TEMPLATE_MAP: dict[str, str] = {
    "busy": "busy",
    "dnd": "urgent_only",
    "away": "away",
}


class AutoReplyTemplates:
    """Manages auto-reply templates with status-aware selection."""

    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS reply_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self._conn.commit()

    def get_status_reply(self, status_override: str) -> str | None:
        """Get the appropriate auto-reply for a given status.

        Checks custom templates first (by status name), falls back to built-in.
        Returns None if status doesn't map to a template.
        """
        template_name = _STATUS_TEMPLATE_MAP.get(status_override)
        if not template_name:
            return None

        # Check custom template first
        custom = self.get_template(template_name)
        if custom:
            return custom

        # Fall back to built-in
        return BUILTIN_TEMPLATES.get(template_name)

    def list_templates(self) -> list[dict]:
        """List all templates (built-in + custom)."""
        templates = []

        # Built-in templates
        for name, text in BUILTIN_TEMPLATES.items():
            templates.append({"name": name, "text": text, "builtin": True})

        # Custom templates (may override built-in names)
        try:
            cursor = self._conn.execute("SELECT name, text FROM reply_templates ORDER BY name")
            for row in cursor.fetchall():
                templates.append({"name": row["name"], "text": row["text"], "builtin": False})
        except Exception:
            pass

        return templates

    def add_template(self, name: str, text: str) -> bool:
        """Add or update a custom template. Returns True on success."""
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO reply_templates (name, text) VALUES (?, ?)",
                (name, text),
            )
            self._conn.commit()
            return True
        except Exception:
            return False

    def get_template(self, name: str) -> str | None:
        """Get a custom template by name. Returns None if not found."""
        try:
            cursor = self._conn.execute(
                "SELECT text FROM reply_templates WHERE name = ?", (name,)
            )
            row = cursor.fetchone()
            return row["text"] if row else None
        except Exception:
            return None

    def delete_template(self, name: str) -> bool:
        """Delete a custom template. Returns True if deleted."""
        try:
            cursor = self._conn.execute(
                "DELETE FROM reply_templates WHERE name = ?", (name,)
            )
            self._conn.commit()
            return cursor.rowcount > 0
        except Exception:
            return False

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
