"""Admin Elevated Mode Manager.

Aristotelian Foundation:
- Material: Ephemeral in-memory flag per admin JID (not persisted -- dies with restart).
- Formal: AdminModeManager with activate/deactivate/is_elevated keyed by JID.
- Efficient: /break-chains activates, /secure-it deactivates. Checked before every LLM call.
- Final: Admin gains cross-contact memory access without permanent security exposure.

Security Design:
- Elevated mode is EPHEMERAL -- server restart clears it (fail-safe).
- Only the configured admin_number can activate.
- Auto-expires after configurable timeout (default 30 minutes).
- All elevated actions are audit-logged.
"""

import time
from typing import Any

# Default auto-expire: 30 minutes of inactivity
DEFAULT_TIMEOUT_S = 30 * 60

# Activation / deactivation keywords
ACTIVATE_KEYWORD = "/break-chains"
DEACTIVATE_KEYWORD = "/secure-it"


class AdminModeManager:
    """Manages elevated admin mode state.

    Thread-safe for asyncio (single-threaded event loop).
    Not persisted -- intentionally ephemeral for security.
    """

    def __init__(self, timeout_s: int = DEFAULT_TIMEOUT_S):
        self._timeout_s = timeout_s
        # JID -> {"activated_at": float, "last_used": float}
        self._elevated: dict[str, dict[str, float]] = {}
        # Audit log: list of (timestamp, jid, action, detail)
        self._audit_log: list[tuple[float, str, str, str]] = []

    def activate(self, jid: str) -> str:
        """Activate elevated mode for an admin JID.

        Returns a confirmation message.
        """
        now = time.time()
        self._elevated[jid] = {"activated_at": now, "last_used": now}
        self._audit_log.append((now, jid, "activate", "Elevated mode activated"))
        timeout_min = self._timeout_s // 60
        return (
            f"Elevated mode ACTIVATED. You now have cross-contact memory access.\n"
            f"Auto-expires after {timeout_min} min of inactivity.\n"
            f"Send /secure-it to deactivate."
        )

    def deactivate(self, jid: str) -> str:
        """Deactivate elevated mode for an admin JID."""
        was_active = jid in self._elevated
        self._elevated.pop(jid, None)
        self._audit_log.append((time.time(), jid, "deactivate", "Elevated mode deactivated"))
        if was_active:
            return "Elevated mode DEACTIVATED. Cross-contact access revoked."
        return "Elevated mode was not active."

    def is_elevated(self, jid: str) -> bool:
        """Check if a JID is in elevated mode (with auto-expiry)."""
        state = self._elevated.get(jid)
        if not state:
            return False

        now = time.time()
        if now - state["last_used"] > self._timeout_s:
            # Auto-expired
            self._elevated.pop(jid, None)
            self._audit_log.append((now, jid, "auto_expire", "Elevated mode auto-expired"))
            return False

        # Touch last_used on check
        state["last_used"] = now
        return True

    def get_status(self, jid: str) -> dict[str, Any]:
        """Get elevated mode status for display."""
        state = self._elevated.get(jid)
        if not state:
            return {"active": False}

        now = time.time()
        elapsed = now - state["activated_at"]
        remaining = max(0, self._timeout_s - (now - state["last_used"]))

        return {
            "active": True,
            "elapsed_minutes": int(elapsed / 60),
            "remaining_minutes": int(remaining / 60),
        }

    def get_audit_log(self, limit: int = 20) -> list[dict]:
        """Return recent audit entries."""
        entries = self._audit_log[-limit:]
        return [
            {"timestamp": ts, "jid": jid, "action": action, "detail": detail}
            for ts, jid, action, detail in entries
        ]

    def is_mode_command(self, text: str) -> str | None:
        """Check if text is a mode activation/deactivation command.

        Returns 'activate', 'deactivate', or None.
        """
        stripped = text.strip().lower()
        if stripped == ACTIVATE_KEYWORD:
            return "activate"
        if stripped == DEACTIVATE_KEYWORD:
            return "deactivate"
        return None
