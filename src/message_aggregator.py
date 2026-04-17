"""Message aggregation / debounce layer for multi-message batching.

Theorem T_AGGREGATE: A user sending 3-4 messages about one topic in quick
succession intends ONE conversation turn, not N independent queries.

Design axioms:
  A1: The aggregation window must be configurable -- different owners have
      different typing speeds and different user bases.
  A2: URGENT-classified messages bypass the window immediately -- delay causes
      real harm in emergencies.
  A3: Admin messages bypass aggregation -- /commands must execute instantly.
  A4: Media-only messages (no text) are configurable: bypass (default) or extend.
  A5: When the window fires, the combined message preserves context so the AI
      sees all parts with correct framing.
  A6: Aggregation is per-contact -- different contacts must never interfere.

Placement in pipeline:
    WhatsAppChannel.on_message()
        |
        v
    MessageAggregator.enqueue()   <-- NEW
        |  (window timer fires)
        v
    WhatsAppOrchestrator.handle_message()   <-- unchanged

The aggregator is a pure asyncio layer -- no database, no I/O.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class PendingMessage:
    """A single message waiting in the aggregation window."""
    sender_id: str
    chat_id: str
    content: str
    media_paths: list
    metadata: dict
    arrived_at: float = field(default_factory=time.time)


def _combine_messages(messages: list[PendingMessage]) -> tuple[str, list, dict]:
    """Combine multiple pending messages into a single logical message.

    The combined content is formatted so the AI sees all parts naturally:
    - Single message: returned as-is (no wrapper)
    - Multiple messages: joined with newlines, annotated for AI context

    Returns (combined_content, combined_media_paths, merged_metadata).
    """
    if len(messages) == 1:
        m = messages[0]
        return m.content, m.media_paths, m.metadata

    # Multiple messages: combine with context annotation
    parts = []
    for i, m in enumerate(messages, 1):
        if m.content and m.content.strip():
            parts.append(m.content.strip())

    if len(parts) == 1:
        # Only one message had text content; still combine media
        combined_text = parts[0]
    elif parts:
        # Format as a natural multi-part message
        combined_text = "\n".join(parts)
    else:
        combined_text = ""

    # Combine all media paths (order preserved)
    all_media: list = []
    for m in messages:
        all_media.extend(m.media_paths)

    # Use the LAST message's metadata as base (most up-to-date push name, etc.)
    # but preserve the earliest message_id for audit/dedup purposes
    merged_meta = dict(messages[-1].metadata)
    if messages[0].metadata.get("id"):
        merged_meta["first_id"] = messages[0].metadata["id"]
    merged_meta["aggregated_count"] = len(messages)

    return combined_text, all_media, merged_meta


class MessageAggregator:
    """Debounce/aggregation layer between channel and orchestrator.

    Usage:
        aggregator = MessageAggregator(config, handler_fn)
        # Set as the channel's on_message callback
        channel.on_message = aggregator.enqueue
    """

    def __init__(
        self,
        config: dict,
        handler: Callable[..., Awaitable[None]],
        intent_classifier=None,
    ):
        """
        Args:
            config: Main orchestrator config dict.
            handler: The orchestrator's handle_message coroutine.
            intent_classifier: Optional IntentClassifier for URGENT bypass.
        """
        self._config = config
        self._handler = handler
        self._classifier = intent_classifier

        # Per-contact state: sender_id -> list[PendingMessage]
        self._pending: dict[str, list[PendingMessage]] = {}
        # Per-contact timer handles
        self._timers: dict[str, asyncio.TimerHandle] = {}

    def _window_seconds(self) -> float:
        """Debounce window from config (default 3s)."""
        return float(self._config.get("aggregation_window_seconds", 3.0))

    def _media_bypass(self) -> bool:
        """Whether media-only messages bypass the window (default True)."""
        return bool(self._config.get("aggregation_bypass_media", True))

    def _is_admin(self, sender_id: str) -> bool:
        """Check if sender is admin (admin messages bypass aggregation)."""
        admin_number = self._config.get("admin_number", "")
        if not admin_number:
            return False
        return sender_id == admin_number or sender_id.startswith(admin_number + "@")

    def _cancel_timer(self, contact_key: str) -> None:
        """Cancel any pending timer for this contact."""
        handle = self._timers.pop(contact_key, None)
        if handle:
            handle.cancel()

    def _schedule_flush(self, contact_key: str) -> None:
        """Schedule a flush for this contact after the debounce window."""
        self._cancel_timer(contact_key)
        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            self._window_seconds(),
            lambda: asyncio.create_task(self._flush(contact_key)),
        )
        self._timers[contact_key] = handle

    async def _flush(self, contact_key: str) -> None:
        """Fire accumulated messages for a contact as a single batch."""
        messages = self._pending.pop(contact_key, [])
        self._timers.pop(contact_key, None)

        if not messages:
            return

        combined_content, combined_media, merged_meta = _combine_messages(messages)

        # Use first message's routing info (sender_id, chat_id are the same for all)
        first = messages[0]
        try:
            await self._handler(
                first.sender_id,
                first.chat_id,
                combined_content,
                combined_media,
                merged_meta,
            )
        except Exception as e:
            print(
                f"[aggregator] handler error for {first.sender_id}: "
                f"{type(e).__name__}: {e}"
            )

    async def enqueue(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media_paths: list,
        metadata: dict,
    ) -> None:
        """Enqueue a message, starting or extending the debounce window.

        This replaces the direct call to orchestrator.handle_message().

        Bypass conditions (message dispatched immediately):
          - Admin sender
          - URGENT classification from intent_classifier
          - Media-only (no text) when aggregation_bypass_media=True
          - aggregation_window_seconds <= 0 (disabled)
        """
        window = self._window_seconds()

        # Bypass: window disabled
        if window <= 0:
            await self._handler(sender_id, chat_id, content, media_paths, metadata)
            return

        # Bypass: admin messages skip aggregation
        if self._is_admin(sender_id):
            await self._handler(sender_id, chat_id, content, media_paths, metadata)
            return

        # Bypass: media-only messages (no text content)
        if not content.strip() and media_paths and self._media_bypass():
            # Flush any existing pending text first, then deliver media immediately
            contact_key = chat_id
            if contact_key in self._pending:
                self._cancel_timer(contact_key)
                await self._flush(contact_key)
            await self._handler(sender_id, chat_id, content, media_paths, metadata)
            return

        # Bypass: URGENT classification -- dispatch immediately
        if self._classifier is not None:
            try:
                result = await self._classifier.classify(
                    content, sender_id, contact_context=""
                )
                from src.intent_classifier import EscalationLevel
                if result.level == EscalationLevel.URGENT:
                    print(
                        f"[aggregator] URGENT bypass for {sender_id}: {result.reason}"
                    )
                    # Flush any pending messages first, then this one
                    contact_key = chat_id
                    if contact_key in self._pending:
                        self._cancel_timer(contact_key)
                        await self._flush(contact_key)
                    await self._handler(sender_id, chat_id, content, media_paths, metadata)
                    return
            except Exception as e:
                print(f"[aggregator] classifier error (proceeding normally): {e}")

        # Normal path: accumulate and (re)schedule window
        contact_key = chat_id
        if contact_key not in self._pending:
            self._pending[contact_key] = []

        self._pending[contact_key].append(
            PendingMessage(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                media_paths=media_paths,
                metadata=metadata,
            )
        )

        # Extend (reset) the debounce window on each new message
        self._schedule_flush(contact_key)
        print(
            f"[aggregator] buffered msg #{len(self._pending[contact_key])} "
            f"from {sender_id} (window={window}s)"
        )

    async def flush_all(self) -> None:
        """Flush all pending batches immediately (used on shutdown)."""
        keys = list(self._pending.keys())
        for key in keys:
            self._cancel_timer(key)
            await self._flush(key)
