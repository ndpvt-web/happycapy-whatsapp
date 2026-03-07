"""WhatsApp message processing channel.

Adapted from nanobot WhatsAppChannel for standalone operation.
Connects to the Node.js bridge via WebSocket, processes messages,
applies config-driven filtering, and sends AI responses.
"""

import asyncio
import base64
import json
import re
import time
from pathlib import Path
from typing import Any

try:
    import websockets
except ImportError:
    websockets = None

class WhatsAppChannel:
    """Standalone WhatsApp channel with config-driven behavior."""

    # AI reasoning patterns to strip from outbound messages
    _REASONING_PATTERNS = re.compile(
        r"(?:"
        r"\n+\(Note:\s"
        r"|\n+Note:\s+I[''']m\s"
        r"|\n+\*\*Note:\*\*"
        r"|\n+\[Internal:"
        r"|\n+\(Internal:"
        r"|\n+\(Thinking:"
        r"|\n+\[Reasoning:"
        r")"
        r"[\s\S]*$",
    )

    # ── Constants with Aristotelian proofs ──
    # P_DEDUP: WhatsApp delivers retries on reconnect; dedup prevents double-processing.
    # 1000 IDs * ~50 bytes = ~50KB. At 30 msg/min max rate, covers ~33 min of history.
    _DEDUP_MAX = 1000
    _DEDUP_EVICT_BATCH = 100  # Evict oldest 10% when full (amortized O(1) per insert)
    # P_SENT: Track outbound message keys for delete/status correlation.
    # 500 = ~16 min at max send rate; outbound needs less history than inbound.
    _SENT_KEYS_MAX = 500

    def __init__(self, config: dict[str, Any], on_message=None):
        self.config = config
        self.on_message = on_message  # async callback(sender_id, chat_id, content, media_paths, metadata)
        self._ws = None
        self._connected = False
        self._running = False
        self._seen_ids: dict[str, float] = {}
        self._sent_keys: dict[str, dict] = {}
        self._reconnect_attempts = 0

    @property
    def bridge_url(self) -> str:
        port = self.config.get("bridge_port", 3002)
        return f"ws://127.0.0.1:{port}"

    @property
    def bridge_token(self) -> str:
        return self.config.get("bridge_token", "")

    async def start(self) -> None:
        """Connect to bridge and start processing messages."""
        if not websockets:
            raise ImportError("websockets package required: pip install websockets")

        self._running = True

        while self._running:
            try:
                async with websockets.connect(self.bridge_url) as ws:
                    self._ws = ws

                    if self.bridge_token:
                        await ws.send(json.dumps({"type": "auth", "token": self.bridge_token}))

                    self._reconnect_attempts = 0
                    self._connected = True
                    print("Connected to WhatsApp bridge")

                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as e:
                            print(f"Error handling bridge message: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                self._ws = None
                self._reconnect_attempts += 1
                backoff = min(5 * (2 ** (self._reconnect_attempts - 1)), 60)
                print(f"Bridge connection error (attempt {self._reconnect_attempts}): {e}")

                if self._running:
                    print(f"Reconnecting in {backoff}s...")
                    await asyncio.sleep(backoff)

    async def stop(self) -> None:
        """Disconnect from bridge."""
        self._running = False
        self._connected = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send_text(self, chat_id: str, text: str) -> None:
        """Send a text message, splitting if necessary."""
        if not self._ws or not self._connected:
            print("Bridge not connected, cannot send")
            return

        text = self._strip_reasoning(text)
        max_len = self.config.get("max_message_length", 4000)
        chunks = self._split_message(text, max_len) if len(text) > max_len else [text]

        for chunk in chunks:
            payload = {"type": "send", "to": chat_id, "text": chunk}
            await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def send_media(self, chat_id: str, file_path: str) -> None:
        """Send a media file."""
        if not self._ws or not self._connected:
            return

        p = Path(file_path)
        if not p.is_file():
            print(f"Media file not found: {file_path}")
            return

        import mimetypes
        mime, _ = mimetypes.guess_type(file_path)
        if not mime:
            mime = "application/octet-stream"

        b64_data = base64.b64encode(p.read_bytes()).decode("ascii")
        payload = {
            "type": "send",
            "to": chat_id,
            "text": "",
            "media": {"data": b64_data, "mimetype": mime, "filename": p.name},
        }
        await self._ws.send(json.dumps(payload, ensure_ascii=False))

    def _should_process(self, sender_id: str, is_group: bool) -> bool:
        """Check if message should be processed based on config filters."""
        # Groups: never auto-reply (Theorem T6)
        if is_group:
            group_policy = self.config.get("group_policy", "monitor")
            if group_policy == "ignore":
                return False
            # "monitor" = log but don't process for reply
            return False

        # Mode check
        mode = self.config.get("mode", "auto_reply")
        if mode == "monitor_only":
            return False

        # Allowlist
        allowlist = self.config.get("allowlist", [])
        if allowlist and sender_id not in allowlist:
            return False

        # Blocklist
        blocklist = self.config.get("blocklist", [])
        if sender_id in blocklist:
            return False

        return True

    async def _handle_bridge_message(self, raw: str) -> None:
        """Handle a message from the bridge."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")

        if msg_type == "message":
            if not self._connected:
                self._connected = True

            pn = data.get("pn", "")
            sender = data.get("sender", "")
            content = data.get("content", "")
            is_group = data.get("isGroup", False)

            user_id = pn if pn else sender
            sender_id = user_id.split("@")[0] if "@" in user_id else user_id

            # Deduplication
            msg_id = data.get("id", "")
            if msg_id and msg_id in self._seen_ids:
                return
            if msg_id:
                self._seen_ids[msg_id] = time.time()
                if len(self._seen_ids) > self._DEDUP_MAX:
                    oldest = sorted(self._seen_ids, key=self._seen_ids.get)[:self._DEDUP_EVICT_BATCH]
                    for k in oldest:
                        del self._seen_ids[k]

            # Config-driven filtering
            if not self._should_process(sender_id, is_group):
                if is_group:
                    print(f"[group message from {sender_id}] {content[:80]}")
                else:
                    print(f"[filtered] {sender_id}: {content[:80]}")
                return

            # Media handling - save to disk and pass to orchestrator for understanding.
            # The orchestrator's media_processor handles vision, PDF extraction,
            # audio transcription, and video analysis (Theorems T_IMG, T_PDF, T_VID).
            media_base64 = data.get("media_base64", "")
            media_type = data.get("media_type", "")
            media_mimetype = data.get("media_mimetype", "")
            media_filename = data.get("media_filename", "")
            media_paths = []

            has_media = bool(media_base64)

            if has_media:
                # Determine file extension from mime type
                if media_type == "document" and media_filename:
                    ext = Path(media_filename).suffix or self._ext_from_mime(media_mimetype, ".bin")
                elif media_type == "sticker":
                    ext = ".webp"
                else:
                    fallback = {
                        "image": ".jpg", "audio": ".ogg",
                        "video": ".mp4", "document": ".bin",
                    }.get(media_type, ".bin")
                    ext = self._ext_from_mime(media_mimetype, fallback)

                saved_path = self._save_media(media_base64, msg_id, ext)
                if saved_path:
                    media_paths.append(saved_path)

                    # Clean up content tags (e.g. "[Image] caption" -> just caption)
                    tag_prefixes = {
                        "image": "[Image]", "video": "[Video]",
                        "document": "[Document]", "sticker": "[Sticker]",
                        "audio": "[Voice Message]",
                    }
                    prefix = tag_prefixes.get(media_type, "")
                    if prefix and content.startswith(prefix):
                        content = content[len(prefix):].strip()
                    if not content:
                        content = f"User sent a {media_type}"

            # Media handling mode: ignore media-only messages if configured
            if has_media and not media_paths and self.config.get("media_handling") == "ignore":
                return

            metadata = {
                "message_id": msg_id,
                "timestamp": data.get("timestamp"),
                "is_group": is_group,
                "media_type": media_type,
                "media_mimetype": media_mimetype,
                "media_filename": media_filename,
            }

            if self.on_message:
                # Dispatch as task so different contacts are processed concurrently.
                # The orchestrator uses per-contact locks to keep same-contact
                # messages sequential while allowing cross-contact parallelism.
                asyncio.create_task(self.on_message(sender_id, sender, content, media_paths, metadata))

        elif msg_type == "status":
            status = data.get("status")
            if status == "connected":
                self._connected = True
            elif status == "disconnected":
                self._connected = False

        elif msg_type == "sent":
            msg_id = data.get("messageId")
            if msg_id:
                self._sent_keys[msg_id] = {
                    "remoteJid": data.get("remoteJid", data.get("to", "")),
                    "fromMe": data.get("fromMe", True),
                    "id": msg_id,
                    "to": data.get("to", ""),
                }
                if len(self._sent_keys) > self._SENT_KEYS_MAX:
                    keys_to_remove = list(self._sent_keys.keys())[:len(self._sent_keys) - self._SENT_KEYS_MAX]
                    for k in keys_to_remove:
                        del self._sent_keys[k]

        elif msg_type == "error":
            print(f"Bridge error: {data.get('error')}")

    def _save_media(self, b64_data: str, msg_id: str, ext: str) -> str | None:
        """Decode base64 media and save to disk."""
        media_dir = Path.home() / ".happycapy-whatsapp" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        file_path = media_dir / f"wa_{msg_id[:16]}{ext}"
        try:
            file_path.write_bytes(base64.b64decode(b64_data))
            return str(file_path)
        except Exception as e:
            print(f"Failed to save media: {e}")
            return None

    @classmethod
    def _strip_reasoning(cls, text: str) -> str:
        """Strip AI reasoning/meta-commentary from outbound text."""
        text = cls._REASONING_PATTERNS.sub("", text)
        return text.strip()

    @staticmethod
    def _split_message(text: str, max_len: int) -> list[str]:
        """Split a long message into chunks, preferring line breaks."""
        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            split_at = text.rfind("\n", 0, max_len)
            if split_at <= 0:
                split_at = text.rfind(" ", 0, max_len)
            if split_at <= 0:
                split_at = max_len
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
        return chunks

    @staticmethod
    def _ext_from_mime(mime: str, fallback: str) -> str:
        ext_map = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/gif": ".gif", "audio/ogg; codecs=opus": ".ogg", "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "video/mp4": ".mp4",
            "application/pdf": ".pdf",
        }
        return ext_map.get(mime, fallback)

    def cleanup_media(self, max_age_hours: int = 0) -> int:
        """Remove media files older than max_age_hours.

        Proof: Media files accumulate at rate proportional to incoming messages.
        Without cleanup, disk usage grows unbounded. Default max_age from config
        (media_max_age_hours). 0 = no cleanup (keep forever).

        Returns number of files removed.
        """
        if max_age_hours <= 0:
            max_age_hours = self.config.get("media_max_age_hours", 24)
        if max_age_hours <= 0:
            return 0

        media_dir = Path.home() / ".happycapy-whatsapp" / "media"
        if not media_dir.exists():
            return 0

        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0
        for f in media_dir.iterdir():
            if f.is_file() and f.name.startswith("wa_"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except OSError:
                    pass
        if removed:
            print(f"Media cleanup: removed {removed} files older than {max_age_hours}h")
        return removed
