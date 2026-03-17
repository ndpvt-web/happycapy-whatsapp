"""WhatsApp message processing channel.

Adapted from nanobot WhatsAppChannel for standalone operation.
Connects to the Node.js bridge via WebSocket, processes messages,
applies config-driven filtering, and sends AI responses.

Security:
- Theorem T_PATHSAN: Sanitize msg_id to alphanumeric for media filenames (P_PATHTR).
- Theorem T_LOGREDACT: Never log message content; use length indicators (P_LOGPII).
- Theorem T_INPUTCAP: Cap incoming content at _MAX_CONTENT_CHARS (P_INPUTLEN).
- Theorem T_SENDSAN: Validate send_media paths within media directory (P_MEDIASAN).
"""

import asyncio
import base64
import json
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

try:
    import websockets
except ImportError:
    websockets = None

class WhatsAppChannel:
    """Standalone WhatsApp channel with config-driven behavior."""

    # ── Theorem T_REASONSTRIP: Multi-layer reasoning filter (P_REASONLEAK) ──
    # P_REASONLEAK: LLMs may emit internal reasoning, thinking tags, or
    # meta-commentary despite system prompt instructions. Outbound messages
    # must be scrubbed at multiple levels to prevent leakage to contacts.
    #
    # Architecture: Allowlist-first with blocklist fallback (Theorem T_ALLOWFIRST).
    # P_ALLOWLIST: Allowlist extraction > blocklist stripping (security axiom).
    # Unknown reasoning formats can't leak through allowlist; they can through blocklist.
    #
    # Primary: Extract content from <reply>...</reply> tags (allowlist).
    # Fallback: If no tags found, apply blocklist regex layers (graceful degradation).
    # Safety net: Blocklist regex runs on extracted content too (defense-in-depth).
    #
    # Blocklist layers (fallback + safety net):
    # Layer 1: XML/tag-based reasoning blocks (stripped entirely via _XML_REASONING_RE).
    # Layer 2: Tail-anchored reasoning patterns (everything from marker to end).
    # Layer 3: Natural language reasoning prefixes (stripped line-by-line).

    # Primary: Allowlist extraction (Theorem T_ALLOWFIRST).
    # Extract content from <reply>...</reply> tags. Supports multiple <reply> blocks
    # (concatenated with newlines). If present, ONLY this content is used as the response.
    # Tolerant matching: allows whitespace/attributes in opening tag.
    _REPLY_EXTRACT_RE = re.compile(
        r"<reply(?:\s[^>]*)?>(?P<content>[\s\S]*?)</reply>",
        re.IGNORECASE,
    )

    # Layer 1: XML-style reasoning blocks that must be removed entirely.
    # Matches <thinking>...</thinking>, <antThinking>...</antThinking>,
    # <reasoning>...</reasoning>, <reflection>...</reflection>, <inner_monologue>...</inner_monologue>.
    # Uses re.DOTALL so . matches newlines inside the block.
    _XML_REASONING_RE = re.compile(
        r"<(?:thinking|antThinking|reasoning|reflection|inner_monologue)"
        r"(?:\s[^>]*)?>[\s\S]*?</(?:thinking|antThinking|reasoning|reflection|inner_monologue)>",
    )

    # Layer 2: Tail-anchored patterns - everything from marker to end of string.
    # These catch structured reasoning markers that appear mid/end of response.
    _TAIL_REASONING_RE = re.compile(
        r"(?:"
        r"\n+\(Note:\s"
        r"|\n+Note:\s+I[''']m\s"
        r"|\n+\*\*Note:\*\*"
        r"|\n+\[Internal:"
        r"|\n+\(Internal:"
        r"|\n+\(Thinking:"
        r"|\n+\[Reasoning:"
        r"|\n+---\s*\n+(?:Note|Thinking|Internal|Reasoning):"
        r"|\n+>\s*(?:Internal|Note|Thinking|Reasoning):"
        r")"
        r"[\s\S]*$",
    )

    # Layer 3: Natural language reasoning lines to strip individually.
    # These catch common LLM self-talk patterns that bypass structured markers.
    _NATURAL_REASONING_RE = re.compile(
        r"^(?:"
        r"Let me (?:think|reason|analyze|consider|break down|work through)"
        r"|I (?:should|need to|will) (?:consider|think|analyze|reason|note)"
        r"|(?:My|Here'?s my) (?:reasoning|thought process|approach|analysis)"
        r"|To clarify my (?:reasoning|thinking|thought process)"
        r"|(?:Thinking|Reasoning) (?:about|through) this"
        r"|As an AI(?:,| )(?:I |assistant)"
        r"|I'?m (?:an AI|a language model|not (?:a |)human)"
        r").*$",
        re.MULTILINE | re.IGNORECASE,
    )

    # Layer 4 (Theorem T_REASONSTRIP): Post-filter leak detector.
    # Weaker signals that individually are too aggressive to strip (false positives),
    # but together indicate the regex layers missed something. Logs a warning
    # for monitoring; does NOT block the message (avoiding false-positive suppression).
    _LEAK_DETECTOR_RE = re.compile(
        r"<(?:think|reflect|internal|meta|note)[^>]*>"
        r"|(?:^|\n)\s*\[(?:thinking|internal|note|reasoning)\b"
        r"|(?:^|\n)\s*\*\*(?:Internal|Thinking|Note|Reasoning):"
        r"|</?(?:thought|monologue|scratchpad)[^>]*>",
        re.IGNORECASE,
    )

    # ── Constants with Aristotelian proofs ──
    # P_DEDUP: WhatsApp delivers retries on reconnect; dedup prevents double-processing.
    # 1000 IDs * ~50 bytes = ~50KB. At 30 msg/min max rate, covers ~33 min of history.
    _DEDUP_MAX = 1000
    _DEDUP_EVICT_BATCH = 100  # Evict oldest 10% when full (amortized O(1) per insert)
    # P_SENT: Track outbound message keys for delete/status correlation.
    # 500 = ~16 min at max send rate; outbound needs less history than inbound.
    _SENT_KEYS_MAX = 500
    # Theorem T_INPUTCAP: Max chars for incoming message content (P_INPUTLEN).
    # 10000 chars ≈ 3K tokens. WhatsApp messages rarely exceed 4096 chars;
    # 10K provides headroom for media-enriched content without DoS risk.
    _MAX_CONTENT_CHARS = 10000
    # Theorem T_PATHSAN: Regex for safe filename characters (P_PATHTR).
    _SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9_-]")

    def __init__(self, config: dict[str, Any], on_message=None, on_group_message=None, on_history_sync=None, on_contacts_sync=None):
        self.config = config
        self.on_message = on_message  # async callback(sender_id, chat_id, content, media_paths, metadata)
        self.on_group_message = on_group_message  # async callback(sender_id, group_jid, content, metadata)
        self.on_history_sync = on_history_sync  # async callback(messages, sync_type, progress, is_latest)
        self.on_contacts_sync = on_contacts_sync  # async callback(contacts: list[dict])
        self._ws = None
        self._connected = False
        self._running = False
        # OrderedDict for O(1) dedup eviction (Theorem T_ODEDUP).
        # Insertion order = temporal order, so popitem(last=False) evicts oldest.
        self._seen_ids: OrderedDict[str, float] = OrderedDict()
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
                    # Note: _connected stays False until we receive a
                    # status:connected event from the bridge, meaning WhatsApp
                    # itself is authenticated. Python-to-Bridge != Bridge-to-WhatsApp.
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
        # Layer 4 (Theorem T_REASONSTRIP): Post-filter leak detection.
        # Logs warning if weak signals remain after 3 regex layers.
        if self._LEAK_DETECTOR_RE.search(text):
            print("[warning] Possible reasoning leak detected in outbound message (post-filter)")
        # Convert Markdown to WhatsApp formatting (Issue 4)
        text = self._convert_markdown_to_whatsapp(text)
        max_len = self.config.get("max_message_length", 4000)
        chunks = self._split_message(text, max_len) if len(text) > max_len else [text]

        for chunk in chunks:
            payload = {"type": "send", "to": chat_id, "text": chunk}
            await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def send_text_owner_approved(self, chat_id: str, text: str) -> None:
        """Send a text message with ownerApproved flag (for group sends).

        This is the ONLY way to send to groups. The bridge blocks all group sends
        without the ownerApproved flag (Premise P_GRPGATE).
        """
        if not self._ws or not self._connected:
            print("Bridge not connected, cannot send")
            return

        text = self._strip_reasoning(text)
        # Convert Markdown to WhatsApp formatting (Issue 4)
        text = self._convert_markdown_to_whatsapp(text)
        max_len = self.config.get("max_message_length", 4000)
        chunks = self._split_message(text, max_len) if len(text) > max_len else [text]

        for chunk in chunks:
            payload = {"type": "send", "to": chat_id, "text": chunk, "ownerApproved": True}
            await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def send_typing(self, chat_id: str, composing: bool = True) -> None:
        """Send typing indicator ('composing' or 'paused') to a chat."""
        if not self._ws or not self._connected:
            return
        try:
            payload = {
                "type": "presence",
                "jid": chat_id,
                "presenceType": "composing" if composing else "paused",
            }
            await self._ws.send(json.dumps(payload))
        except Exception:
            pass  # Non-critical: typing indicator failure shouldn't break message flow

    async def send_media(self, chat_id: str, file_path: str) -> None:
        """Send a media file.

        Theorem T_SENDSAN: Validate file path resolves within media directory (P_MEDIASAN).
        Prevents path traversal attacks from sending arbitrary files.
        """
        if not self._ws or not self._connected:
            return

        # Theorem T_SENDSAN: Resolve symlinks and verify path is within allowed directories.
        p = Path(file_path).resolve()
        media_dir = (Path.home() / ".happycapy-whatsapp" / "media").resolve()
        if not str(p).startswith(str(media_dir)):
            print(f"[security] send_media blocked: path outside media dir")
            return

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

    async def fetch_history(self, chat_jid: str, count: int = 50) -> bool:
        """Request on-demand history fetch for a specific chat.

        Results arrive asynchronously via the history_sync callback.
        """
        if not self._ws or not self._connected:
            print("Bridge not connected, cannot fetch history")
            return False

        payload = {
            "type": "fetch_history",
            "chatJid": chat_jid,
            "count": min(count, 50),  # Baileys caps at 50
        }
        await self._ws.send(json.dumps(payload, ensure_ascii=False))
        print(f"[history] Requested {count} messages for {chat_jid[:20]}..")
        return True

    async def delete_message(self, remote_jid: str, msg_id: str, from_me: bool = True, participant: str = "") -> bool:
        """Delete a message (delete-for-everyone).

        Args:
            remote_jid: Chat JID where the message is.
            msg_id: The message ID to delete.
            from_me: Whether the message was sent by us.
            participant: For group messages, the sender's JID.

        Returns:
            True if delete command was sent.
        """
        if not self._ws or not self._connected:
            print("Bridge not connected, cannot delete")
            return False

        payload: dict = {
            "type": "delete",
            "remoteJid": remote_jid,
            "msgId": msg_id,
            "fromMe": from_me,
        }
        if participant:
            payload["participant"] = participant

        await self._ws.send(json.dumps(payload, ensure_ascii=False))
        print(f"[delete] Sent delete for msg {msg_id} in {remote_jid[:15]}.. (fromMe={from_me})")
        return True

    def _should_process(self, sender_id: str, is_group: bool) -> bool:
        """Check if message should be processed based on config filters."""
        # Theorem T_ADMCMD: Admin always passes through (P_ADMIN).
        admin_number = self.config.get("admin_number", "")
        if admin_number and sender_id == admin_number and not is_group:
            return True

        # Groups: check group_policy for auto_reply support
        if is_group:
            group_policy = self.config.get("group_policy", "monitor")
            if group_policy == "ignore":
                return False
            if group_policy == "auto_reply":
                return True  # Process group messages for AI reply
            # "monitor" = collect samples but don't process for AI reply
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
        # Debug: log all bridge events (remove after debugging)
        if msg_type not in ("qr",):
            print(f"[bridge-event] type={msg_type}")

        if msg_type == "message":
            if not self._connected:
                self._connected = True

            pn = data.get("pn", "")
            sender = data.get("sender", "")
            content = data.get("content", "")
            push_name = data.get("pushName", "")
            is_group = data.get("isGroup", False)
            from_me = data.get("fromMe", False)

            # Theorem T_INPUTCAP: Truncate oversized content to prevent memory DoS (P_INPUTLEN).
            if len(content) > self._MAX_CONTENT_CHARS:
                content = content[:self._MAX_CONTENT_CHARS]

            user_id = pn if pn else sender
            sender_id = user_id.split("@")[0] if "@" in user_id else user_id

            # fromMe handling: skip bot's own outbound messages, allow admin self-messages.
            # Without this, admin can't control the bot from their own WhatsApp because
            # the bot IS their phone, so all admin messages are fromMe.
            if from_me:
                admin_number = self.config.get("admin_number", "")
                if not admin_number or sender_id != admin_number:
                    # Not admin -> skip all fromMe (other people's echoed messages)
                    return
                # Admin fromMe: check if this is the bot's own reply echoed back.
                # _sent_keys tracks outbound message IDs to prevent infinite loops.
                msg_id_check = data.get("id", "")
                if msg_id_check and msg_id_check in self._sent_keys:
                    return

            # Deduplication
            msg_id = data.get("id", "")
            if msg_id and msg_id in self._seen_ids:
                return
            if msg_id:
                self._seen_ids[msg_id] = time.time()
                if len(self._seen_ids) > self._DEDUP_MAX:
                    # O(1) eviction via OrderedDict (Theorem T_ODEDUP).
                    # popitem(last=False) removes oldest-inserted entry.
                    for _ in range(self._DEDUP_EVICT_BATCH):
                        self._seen_ids.popitem(last=False)

            # Config-driven filtering
            if not self._should_process(sender_id, is_group):
                if is_group:
                    # Group collection: fire-and-forget to collector (no reply, no blocking).
                    # Extract actual sender from participant field (bridge provides it).
                    group_policy = self.config.get("group_policy", "monitor")
                    if group_policy == "monitor" and self.on_group_message and content:
                        participant = data.get("participant", "")
                        participant_id = participant.split("@")[0] if "@" in participant else sender_id
                        group_metadata = {
                            "message_id": msg_id,
                            "participant": participant,
                            "participant_id": participant_id,
                            "participant_name": data.get("participantPushName", "") or push_name,
                            "mentioned_jids": data.get("mentionedJids", []),
                            "group_subject": data.get("groupSubject", ""),
                            "timestamp": data.get("timestamp"),
                            # Quoted/reply message tracking
                            "quoted_message_id": data.get("quotedMessageId", ""),
                            "quoted_participant": data.get("quotedParticipant", ""),
                            "quoted_content": data.get("quotedContent", ""),
                        }
                        asyncio.create_task(
                            self.on_group_message(participant_id, sender, content, group_metadata)
                        )
                    print(f"[group] {sender_id} ({len(content)} chars)")
                else:
                    print(f"[filtered] {sender_id} ({len(content)} chars)")
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
                "sender_name": push_name,  # WhatsApp pushName (sender's display name)
                "media_type": media_type,
                "media_mimetype": media_mimetype,
                "media_filename": media_filename,
                # Quoted/reply message tracking
                "quoted_message_id": data.get("quotedMessageId", ""),
                "quoted_participant": data.get("quotedParticipant", ""),
                "quoted_content": data.get("quotedContent", ""),
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
                print("WhatsApp connected!")
                # Update QR server to show connected state
                from src.qr_server import qr_state
                qr_state.set_connected()
            elif status == "disconnected":
                self._connected = False
                from src.qr_server import qr_state
                qr_state.set_disconnected()
                print("WhatsApp disconnected")

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

        elif msg_type == "qr":
            # Update QR server with new QR code for web display
            qr_data = data.get("qr", "")
            if qr_data:
                from src.qr_server import qr_state
                qr_state.update_qr(qr_data)
                print("QR code received (visible on QR server page)")

        elif msg_type == "history_sync":
            messages = data.get("messages", [])
            sync_type = data.get("syncType", 0)
            progress = data.get("progress")
            is_latest = data.get("isLatest", False)
            print(f"[history-sync] {len(messages)} msgs, type={sync_type}, progress={progress}, latest={is_latest}")
            if self.on_history_sync and messages:
                asyncio.create_task(self.on_history_sync(messages, sync_type, progress, is_latest))

        elif msg_type == "contacts_sync":
            contacts = data.get("contacts", [])
            if contacts and self.on_contacts_sync:
                asyncio.create_task(self.on_contacts_sync(contacts))
                print(f"[contacts-sync] {len(contacts)} contacts received")

        elif msg_type == "error":
            print(f"Bridge error: {data.get('error')}")

    def _save_media(self, b64_data: str, msg_id: str, ext: str) -> str | None:
        """Decode base64 media and save to disk."""
        media_dir = Path.home() / ".happycapy-whatsapp" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        # Theorem T_PATHSAN: Sanitize msg_id to alphanumeric for safe filenames (P_PATHTR).
        # Untrusted msg_id could contain "../" or shell metacharacters.
        safe_id = self._SAFE_FILENAME_RE.sub("", msg_id[:16]) or "unknown"
        file_path = media_dir / f"wa_{safe_id}{ext}"
        try:
            file_path.write_bytes(base64.b64decode(b64_data))
            return str(file_path)
        except Exception as e:
            print(f"Failed to save media: {e}")
            return None

    @staticmethod
    def _convert_markdown_to_whatsapp(text: str) -> str:
        """Convert Markdown formatting to WhatsApp formatting.

        Markdown -> WhatsApp conversions:
        - **bold** or __bold__ -> *bold* (WhatsApp uses single asterisk)
        - ~~strike~~ -> ~strike~ (single tilde)
        - ### Header -> *Header* (headers to bold)
        - Keep _italic_ as-is (same in both)
        - Remove language tags from code fences (```python -> ```)
        """
        # Headers -> bold (must come before bold conversion to avoid double processing)
        text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
        # **bold** or __bold__ -> *bold*
        text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
        text = re.sub(r'__(.+?)__', r'*\1*', text)
        # ~~strike~~ -> ~strike~
        text = re.sub(r'~~(.+?)~~', r'~\1~', text)
        # Remove language tags from code fences: ```python -> ```
        text = re.sub(r'```\w+', '```', text)
        return text

    @classmethod
    def _strip_reasoning(cls, text: str) -> str:
        """Strip AI reasoning/meta-commentary from outbound text (Theorem T_REASONSTRIP).

        Allowlist-first architecture (Theorem T_ALLOWFIRST):
        1. Try to extract content from <reply>...</reply> tags (allowlist).
           If found, ONLY the extracted content is used — everything outside is discarded.
        2. If no <reply> tags found, fall back to blocklist stripping (graceful degradation).
        3. Blocklist regex layers ALWAYS run on final text (defense-in-depth safety net).

        Proof: Allowlist extraction makes unknown reasoning formats unable to leak
        (they're outside the tags). Blocklist fallback ensures no silent message drops
        when the LLM forgets to use tags. Running blocklist on extracted content catches
        reasoning accidentally placed inside <reply> tags.
        """
        # Primary: Allowlist extraction (Theorem T_ALLOWFIRST)
        reply_matches = cls._REPLY_EXTRACT_RE.findall(text)
        if reply_matches:
            # Concatenate all <reply> blocks (LLM may split across multiple tags)
            text = "\n\n".join(m.strip() for m in reply_matches if m.strip())

        # Safety net: Blocklist regex layers always run, even after allowlist extraction.
        # Catches reasoning accidentally placed inside <reply> tags, or handles the
        # fallback case where no <reply> tags were found.
        # Layer 1: Strip XML reasoning blocks (preserves content outside blocks)
        text = cls._XML_REASONING_RE.sub("", text)
        # Layer 2: Truncate from structured markers to end
        text = cls._TAIL_REASONING_RE.sub("", text)
        # Layer 3: Remove natural language reasoning lines
        text = cls._NATURAL_REASONING_RE.sub("", text)
        # Clean up multiple blank lines left by removals
        text = re.sub(r"\n{3,}", "\n\n", text)
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

    async def check_on_whatsapp(self, phone_numbers: list[str]) -> list[dict] | None:
        """Check if phone numbers are registered on WhatsApp."""
        if not self._ws or not self._connected:
            return None
        payload = {"type": "check_whatsapp", "phoneNumbers": phone_numbers}
        await self._ws.send(json.dumps(payload))
        return None  # Results come async via bridge response

    async def add_contact(self, jid: str, full_name: str, first_name: str = "") -> None:
        """Add or edit a contact in WhatsApp's synced contact list."""
        if not self._ws or not self._connected:
            print("Bridge not connected, cannot add contact")
            return
        payload: dict = {"type": "add_contact", "jid": jid, "fullName": full_name}
        if first_name:
            payload["firstName"] = first_name
        await self._ws.send(json.dumps(payload, ensure_ascii=False))
        print(f"[contacts] Add/edit contact: {jid} -> {full_name}")

    async def remove_contact(self, jid: str) -> None:
        """Remove a contact from WhatsApp's synced contact list."""
        if not self._ws or not self._connected:
            print("Bridge not connected, cannot remove contact")
            return
        payload = {"type": "remove_contact", "jid": jid}
        await self._ws.send(json.dumps(payload))
        print(f"[contacts] Remove contact: {jid}")

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
