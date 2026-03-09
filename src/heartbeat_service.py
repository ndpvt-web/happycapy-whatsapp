"""Heartbeat service: periodic background maintenance tasks.

Runs every N minutes (default 30) to perform:
1. Message queue cleanup (remove entries older than 72 hours)
2. Audit log pruning (remove entries older than 30 days)
3. Conversation sample pruning (keep last 5000 per contact)
4. Health monitor error list trimming
5. Dedup cache cleanup in channel

All asyncio-based, no external cron needed.
Ported from nanobot HeartbeatService pattern.
"""

import asyncio
import time
from typing import Any, Callable, Coroutine


class HeartbeatService:
    """Periodic maintenance runner using asyncio timers.

    Runs a tick every interval_s seconds. Each tick executes all
    registered maintenance tasks. Tasks that fail are logged but
    don't crash the service (availability > correctness for maintenance).
    """

    DEFAULT_INTERVAL_S = 30 * 60  # 30 minutes

    def __init__(
        self,
        interval_s: int = DEFAULT_INTERVAL_S,
        enabled: bool = True,
    ):
        self.interval_s = interval_s
        self.enabled = enabled
        self._task: asyncio.Task | None = None
        self._running = False
        self._tick_count = 0
        self._last_tick_time: float = 0
        self._tasks: list[tuple[str, Callable[[], Coroutine]]] = []

    def register_task(self, name: str, coro_fn: Callable[[], Coroutine]) -> None:
        """Register a maintenance task (async callable, no args)."""
        self._tasks.append((name, coro_fn))

    async def start(self) -> None:
        """Start the heartbeat loop."""
        if not self.enabled:
            print("[heartbeat] Disabled, skipping start")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        print(f"[heartbeat] Started (interval={self.interval_s}s, tasks={len(self._tasks)})")

    async def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        print("[heartbeat] Stopped")

    async def _run_loop(self) -> None:
        """Main loop: sleep then tick, repeat."""
        try:
            # Wait one interval before first tick (let services stabilize)
            await asyncio.sleep(self.interval_s)

            while self._running:
                await self._tick()
                await asyncio.sleep(self.interval_s)
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        """Execute all registered maintenance tasks."""
        self._tick_count += 1
        self._last_tick_time = time.time()
        results = []

        for name, coro_fn in self._tasks:
            try:
                await coro_fn()
                results.append(f"{name}:ok")
            except Exception as e:
                results.append(f"{name}:err({type(e).__name__})")

        print(f"[heartbeat] Tick #{self._tick_count} [{', '.join(results)}]")

    def status(self) -> dict[str, Any]:
        """Return heartbeat service status."""
        return {
            "enabled": self.enabled,
            "running": self._running,
            "interval_s": self.interval_s,
            "tick_count": self._tick_count,
            "last_tick_time": self._last_tick_time,
            "registered_tasks": [name for name, _ in self._tasks],
        }

    async def force_tick(self) -> None:
        """Force an immediate maintenance tick (admin command)."""
        await self._tick()


# ── Maintenance task factories ──
# Each returns an async callable suitable for register_task().


def make_queue_cleanup_task(message_queue: Any) -> Callable[[], Coroutine]:
    """Create task to clean up old queue entries (>72h)."""
    async def _task():
        if message_queue:
            message_queue.cleanup(older_than_hours=72)
    return _task


def make_audit_prune_task(audit_log: Any) -> Callable[[], Coroutine]:
    """Create task to prune old audit entries (>30 days)."""
    async def _task():
        if audit_log and hasattr(audit_log, "prune"):
            audit_log.prune(days=30)
    return _task


def make_escalation_expire_task(escalation: Any) -> Callable[[], Coroutine]:
    """Create task to expire old escalations (>24h)."""
    async def _task():
        if escalation:
            escalation.expire(hours=24)
    return _task


def make_sample_prune_task(contact_store: Any, keep_last: int = 5000) -> Callable[[], Coroutine]:
    """Create task to prune conversation samples per contact."""
    async def _task():
        if contact_store and hasattr(contact_store, "prune_old_samples"):
            contact_store.prune_old_samples(keep_last=keep_last)
    return _task
