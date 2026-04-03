"""Text-to-Speech integration for HappyCapy WhatsApp bot.

Aristotelian Foundation:
- Material: Cartesia Sonic TTS API (https://api.cartesia.ai/tts/bytes)
- Formal: BaseIntegration plugin -- auto-discovered, two tools (speak, generate_audio)
- Efficient: Single HTTP POST -> MP3 bytes -> save to MEDIA_DIR -> deliver via platform
- Final: The agent gains a voice. Platform-agnostic: WhatsApp today, email/Telegram tomorrow.

Replacement Test: No existing tool generates audio. Image/video tools are visual-only.
Parsimony: One file, one API call, one env var (CARTESIA_API_KEY). No new dependencies.
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult

# ── Constants ──

CARTESIA_API_URL = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VOICES_URL = "https://api.cartesia.ai/voices"
CARTESIA_VERSION = "2024-06-10"
MEDIA_DIR = Path.home() / ".happycapy-whatsapp" / "media"

# Default voice: warm, natural male voice good for assistant messages
DEFAULT_VOICE_ID = "d709a7e8-9495-4247-aef0-01b3207d11bf"  # Donny - Steady Presence
DEFAULT_MODEL = "sonic-2"

# Curated voice presets (name -> voice_id)
VOICE_PRESETS = {
    "donny": "d709a7e8-9495-4247-aef0-01b3207d11bf",     # Steady Presence (default)
    "haley": "cec7cae1-ac8b-4a59-9eac-ec48366f37ae",     # Engaging Friend
    "mindy": "d6905573-8e91-4e32-b103-fd4d1205cd87",     # Spirited Ally
    "damon": "dbfa416f-d5c0-4c24-b4da-c3b1b13e3b34",     # Commanding Narrator
    "diana": "ea93f57f-7c7d-4cab-9c0f-0e1e6b89dc94",     # Gentle Mom
}

MAX_TRANSCRIPT_CHARS = 5000  # ~2-3 min of speech
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB WhatsApp limit
TTS_TIMEOUT = 60  # seconds

# ── Tool Definitions ──

_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": (
                "Convert text to speech audio and send it as a voice message to the current chat. "
                "Use when the user asks you to 'say', 'speak', 'read aloud', 'voice message', "
                "'send audio', or when a voice reply would be more natural than text. "
                "The audio is generated using a high-quality AI voice and sent as an audio file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "The text to convert to speech. Write it as natural spoken language -- "
                            "no markdown, no bullet points, no special formatting. "
                            "Keep under 5000 characters (~3 minutes of audio)."
                        ),
                    },
                    "voice": {
                        "type": "string",
                        "description": (
                            "Voice preset name. Options: 'donny' (steady, default), "
                            "'haley' (friendly), 'mindy' (spirited), 'damon' (commanding), "
                            "'diana' (gentle). Or a Cartesia voice ID."
                        ),
                    },
                    "language": {
                        "type": "string",
                        "description": (
                            "Language code for the speech. Default 'en'. "
                            "Supports: en, fr, de, es, pt, zh, ja, hi, it, ko, nl, pl, ru, sv, tr, ar, etc."
                        ),
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_audio",
            "description": (
                "Generate a speech audio file from text without sending it. "
                "Returns the file path. Use when you need to create audio for "
                "later use, email attachment, or multi-step workflows. "
                "For immediate voice replies, use 'speak' instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to convert to speech. Natural spoken language, under 5000 chars.",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Voice preset or Cartesia voice ID. Default: 'donny'.",
                    },
                    "language": {
                        "type": "string",
                        "description": "Language code. Default 'en'.",
                    },
                },
                "required": ["text"],
            },
        },
    },
]


# ── Integration Class ──


class Integration(BaseIntegration):
    """Text-to-Speech integration using Cartesia Sonic API."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._client = kwargs.get("client")
        self._channel = kwargs.get("channel")
        self._sender_jid: str = ""
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    def _resolve_voice_for_recipient(self, voice_arg: str) -> str:
        """Resolve voice ID, using admin's cloned voice when sending to admin.

        If config has 'admin_voice_id' and the current sender is admin,
        use the cloned voice unless a specific voice was explicitly requested.
        """
        # Check if sender is admin and no explicit voice override
        admin_number = self.config.get("admin_number", "")
        admin_voice_id = self.config.get("admin_voice_id", "")

        if (
            admin_voice_id
            and admin_number
            and admin_number in self._sender_jid
            and voice_arg in ("donny", "")  # Only override default voice
        ):
            return admin_voice_id

        # Standard resolution
        voice_id = VOICE_PRESETS.get(voice_arg, voice_arg)
        if not voice_id or len(voice_id) < 10:
            voice_id = DEFAULT_VOICE_ID
        return voice_id

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="tts",
            display_name="Text-to-Speech (Cartesia)",
            description="AI voice generation -- speak tool for voice replies, generate_audio for files",
        )

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        return (
            "## Text-to-Speech (Voice Messages)\n\n"
            "You can send voice messages using the `speak` tool. Use it when:\n"
            "- The user asks you to speak, say something aloud, or send a voice note\n"
            "- A warm voice reply would be more personal than text\n"
            "- Reading long content aloud (articles, summaries)\n"
            "- The user explicitly requests audio\n\n"
            "Available voices: donny (steady, default), haley (friendly), "
            "mindy (spirited), damon (commanding), diana (gentle).\n"
            "Supports multiple languages: en, zh, hi, es, fr, de, ja, ko, etc.\n\n"
            "Write text as natural speech -- no markdown, no bullets, no formatting.\n"
            "Use `generate_audio` when you need the file path without sending it.\n"
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        handlers = {
            "speak": self._speak,
            "generate_audio": self._generate_audio,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(False, tool_name, f"Unknown TTS tool: {tool_name}")
        return await handler(arguments)

    async def _speak(self, args: dict[str, Any]) -> ToolResult:
        """Generate audio and send it to the current chat."""
        result = await self._synthesize(args)
        if not result.success or not result.media_path:
            return result

        # Send via WhatsApp channel if available
        if self._channel and self._sender_jid:
            try:
                await self._channel.send_media(self._sender_jid, result.media_path)
                return ToolResult(
                    success=True,
                    tool_name="speak",
                    content="Voice message sent successfully.",
                    media_path=result.media_path,
                )
            except Exception as e:
                return ToolResult(
                    success=False,
                    tool_name="speak",
                    content=f"Audio generated but failed to send: {type(e).__name__}",
                    media_path=result.media_path,
                )

        # Fallback: return file path for the caller to handle delivery
        return ToolResult(
            success=True,
            tool_name="speak",
            content=f"Audio generated at {result.media_path}. Send it as a media attachment.",
            media_path=result.media_path,
        )

    async def _generate_audio(self, args: dict[str, Any]) -> ToolResult:
        """Generate audio file without sending it."""
        return await self._synthesize(args, tool_name="generate_audio")

    async def _synthesize(
        self, args: dict[str, Any], tool_name: str = "speak",
    ) -> ToolResult:
        """Core synthesis: text -> Cartesia API -> MP3 file in MEDIA_DIR."""
        if not httpx:
            return ToolResult(False, tool_name, "TTS unavailable (httpx not installed)")

        text = args.get("text", "").strip()
        if not text:
            return ToolResult(False, tool_name, "No text provided for speech synthesis")
        if len(text) > MAX_TRANSCRIPT_CHARS:
            text = text[:MAX_TRANSCRIPT_CHARS]

        # Resolve voice (with admin voice clone support)
        voice_arg = args.get("voice", "donny").strip().lower()
        voice_id = self._resolve_voice_for_recipient(voice_arg)

        language = args.get("language", "en").strip().lower()

        api_key = os.environ.get("CARTESIA_API_KEY", "")
        if not api_key:
            return ToolResult(False, tool_name, "Cartesia API key not configured (CARTESIA_API_KEY)")

        headers = {
            "X-API-Key": api_key,
            "Cartesia-Version": CARTESIA_VERSION,
            "Content-Type": "application/json",
        }
        payload = {
            "model_id": DEFAULT_MODEL,
            "transcript": text,
            "voice": {"mode": "id", "id": voice_id},
            "output_format": {
                "container": "mp3",
                "sample_rate": 24000,
                "bit_rate": 128000,
            },
            "language": language,
        }

        try:
            if self._client:
                resp = await self._client.post(
                    CARTESIA_API_URL, headers=headers, json=payload, timeout=TTS_TIMEOUT,
                )
            else:
                async with httpx.AsyncClient() as c:
                    resp = await c.post(
                        CARTESIA_API_URL, headers=headers, json=payload, timeout=TTS_TIMEOUT,
                    )

            if resp.status_code != 200:
                error_text = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
                return ToolResult(False, tool_name, f"Cartesia API error: {error_text}")

            if len(resp.content) == 0:
                return ToolResult(False, tool_name, "Cartesia returned empty audio")

            # Save to media directory
            filename = f"tts_{int(time.time())}_{voice_arg}.mp3"
            filepath = MEDIA_DIR / filename
            filepath.write_bytes(resp.content)

            if filepath.stat().st_size > MAX_FILE_SIZE:
                filepath.unlink()
                return ToolResult(False, tool_name, "Generated audio exceeds 20MB limit")

            size_kb = filepath.stat().st_size / 1024
            return ToolResult(
                success=True,
                tool_name=tool_name,
                content=f"Audio generated ({size_kb:.0f}KB, voice={voice_arg}, lang={language})",
                media_path=str(filepath),
            )

        except httpx.TimeoutException:
            return ToolResult(False, tool_name, f"TTS generation timed out ({TTS_TIMEOUT}s)")
        except Exception as e:
            return ToolResult(False, tool_name, f"TTS error: {type(e).__name__}: {e}")
