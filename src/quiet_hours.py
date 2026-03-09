"""Quiet hours system for HappyCapy WhatsApp skill.

Suppresses non-urgent admin notifications during configured quiet hours.
Queues alerts and flushes them as a digest when quiet hours end.

Ported from nanobot GroupAlerter quiet hours logic.
"""

import time
from datetime import datetime, time as dt_time
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo


class QuietHours:
    """Timezone-aware quiet hours with alert queuing."""

    def __init__(
        self,
        enabled: bool = False,
        start: str = "23:00",
        end: str = "07:00",
        timezone: str = "UTC",
        override_threshold: int = 9,
    ):
        self.enabled = enabled
        self.start = start
        self.end = end
        self.timezone = timezone
        self.override_threshold = override_threshold  # Score >= this bypasses quiet hours
        self._queue: list[dict] = []
        self._last_flush: float = time.time()

    def update_config(self, config: dict[str, Any]) -> None:
        """Update quiet hours settings from config."""
        self.enabled = config.get("quiet_hours_enabled", False)
        self.start = config.get("quiet_hours_start", "23:00")
        self.end = config.get("quiet_hours_end", "07:00")
        self.timezone = config.get("quiet_hours_timezone", "UTC")
        self.override_threshold = config.get("quiet_hours_override_threshold", 9)

    def is_active(self) -> bool:
        """Check if currently in quiet hours."""
        if not self.enabled:
            return False
        try:
            tz = ZoneInfo(self.timezone)
            now = datetime.now(tz)
            current = now.time()

            start_h, start_m = map(int, self.start.split(":"))
            end_h, end_m = map(int, self.end.split(":"))
            start_time = dt_time(start_h, start_m)
            end_time = dt_time(end_h, end_m)

            # Handle spanning midnight (e.g., 23:00 -> 07:00)
            if start_time <= end_time:
                return start_time <= current <= end_time
            else:
                return current >= start_time or current <= end_time
        except Exception:
            return False

    def should_suppress(self, score: int) -> bool:
        """Check if an alert should be suppressed.

        Returns True if in quiet hours AND score is below override threshold.
        """
        if not self.is_active():
            return False
        return score < self.override_threshold

    def queue_alert(self, alert: dict) -> None:
        """Queue an alert for later delivery."""
        alert["queued_at"] = time.time()
        self._queue.append(alert)
        # Cap queue at 50 to prevent unbounded growth during long quiet periods
        if len(self._queue) > 50:
            self._queue = self._queue[-50:]

    def check_and_flush(self) -> list[dict] | None:
        """Check if quiet hours ended and return queued alerts as digest.

        Returns list of queued alerts if it's time to flush, None otherwise.
        Call this periodically (e.g., on each incoming message).
        """
        if not self.enabled or not self._queue:
            return None

        # Don't flush while still in quiet hours
        if self.is_active():
            return None

        # Rate limit flushing (5 min between flushes)
        now = time.time()
        if now - self._last_flush < 300:
            return None

        self._last_flush = now
        alerts = list(self._queue)
        self._queue.clear()
        return alerts

    def format_digest(self, alerts: list[dict]) -> str:
        """Format queued alerts as a digest message."""
        if not alerts:
            return ""
        lines = [f"*Quiet Hours Digest ({len(alerts)} alerts)*\n"]
        for a in alerts:
            sender = a.get("sender_name", a.get("sender_id", "?"))
            preview = a.get("content_preview", "")[:100]
            score = a.get("score", 0)
            group = a.get("group_name", "")
            if group:
                lines.append(f"[{score}/10] {group} - {sender}: {preview}")
            else:
                lines.append(f"[{score}/10] {sender}: {preview}")
        return "\n".join(lines)

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    def status(self) -> str:
        """Get quiet hours status string."""
        if not self.enabled:
            return "Quiet hours: disabled"
        active = "ACTIVE" if self.is_active() else "inactive"
        return (
            f"Quiet hours: {active} ({self.start}-{self.end} {self.timezone})\n"
            f"Override threshold: {self.override_threshold}/10\n"
            f"Queued alerts: {self.queue_size}"
        )
