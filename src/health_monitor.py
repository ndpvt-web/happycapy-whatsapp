"""Health monitor: Track uptime, memory usage, and connection stats.

Provides /health admin command with system health at a glance.
Ported from nanobot health monitoring patterns.
"""

import os
import time
from dataclasses import dataclass, field


@dataclass
class HealthStats:
    """Snapshot of system health metrics."""
    uptime_seconds: float = 0.0
    memory_mb: float = 0.0
    messages_processed: int = 0
    messages_per_minute: float = 0.0
    active_chats: int = 0
    whatsapp_connected: bool = False
    bridge_running: bool = False
    errors_last_hour: int = 0


class HealthMonitor:
    """Track bot health metrics for admin monitoring.

    Lightweight: no DB, just in-memory counters.
    Reset on restart (by design - uptime tracking).
    """

    def __init__(self):
        self._start_time = time.time()
        self._messages_processed = 0
        self._errors: list[float] = []  # timestamps of recent errors
        self._message_timestamps: list[float] = []  # for rate calc
        self._active_chats: set[str] = set()
        self._whatsapp_connected = False
        self._bridge_running = False

    def record_message(self, chat_id: str) -> None:
        """Record an incoming message."""
        now = time.time()
        self._messages_processed += 1
        self._message_timestamps.append(now)
        self._active_chats.add(chat_id)
        # Trim old timestamps (keep last hour)
        cutoff = now - 3600
        self._message_timestamps = [t for t in self._message_timestamps if t > cutoff]

    def record_error(self) -> None:
        """Record an error event."""
        now = time.time()
        self._errors.append(now)
        # Trim old errors (keep last hour)
        cutoff = now - 3600
        self._errors = [t for t in self._errors if t > cutoff]

    def set_whatsapp_connected(self, connected: bool) -> None:
        self._whatsapp_connected = connected

    def set_bridge_running(self, running: bool) -> None:
        self._bridge_running = running

    def _get_memory_mb(self) -> float:
        """Get current process memory usage in MB."""
        try:
            # Read from /proc/self/status (Linux)
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # VmRSS is in kB
                        kb = int(line.split()[1])
                        return kb / 1024.0
        except Exception:
            pass
        return 0.0

    def _messages_per_minute(self) -> float:
        """Calculate messages per minute over the last 5 minutes."""
        now = time.time()
        cutoff = now - 300  # last 5 minutes
        recent = sum(1 for t in self._message_timestamps if t > cutoff)
        return recent / 5.0

    def get_stats(self) -> HealthStats:
        """Get current health statistics."""
        now = time.time()
        return HealthStats(
            uptime_seconds=now - self._start_time,
            memory_mb=self._get_memory_mb(),
            messages_processed=self._messages_processed,
            messages_per_minute=self._messages_per_minute(),
            active_chats=len(self._active_chats),
            whatsapp_connected=self._whatsapp_connected,
            bridge_running=self._bridge_running,
            errors_last_hour=len([t for t in self._errors if t > now - 3600]),
        )

    def format_status(self) -> str:
        """Format health stats as a WhatsApp-friendly string."""
        stats = self.get_stats()

        # Format uptime
        uptime = stats.uptime_seconds
        if uptime < 3600:
            uptime_str = f"{uptime / 60:.0f}m"
        elif uptime < 86400:
            hours = int(uptime // 3600)
            mins = int((uptime % 3600) // 60)
            uptime_str = f"{hours}h {mins}m"
        else:
            days = int(uptime // 86400)
            hours = int((uptime % 86400) // 3600)
            uptime_str = f"{days}d {hours}h"

        wa_status = "CONNECTED" if stats.whatsapp_connected else "DISCONNECTED"
        bridge_status = "running" if stats.bridge_running else "stopped"

        return (
            f"*Health Monitor*\n\n"
            f"Uptime: {uptime_str}\n"
            f"Memory: {stats.memory_mb:.1f} MB\n"
            f"WhatsApp: {wa_status}\n"
            f"Bridge: {bridge_status}\n"
            f"Messages processed: {stats.messages_processed}\n"
            f"Rate: {stats.messages_per_minute:.1f} msg/min (5m avg)\n"
            f"Active chats: {stats.active_chats}\n"
            f"Errors (1h): {stats.errors_last_hour}"
        )
