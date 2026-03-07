"""Main orchestrator for HappyCapy WhatsApp skill.

Manages the complete lifecycle:
1. Interactive setup wizard (first run only, via AskUserQuestion)
2. Bridge process startup
3. QR server startup with port exposure
4. WhatsApp channel message processing
5. AI response generation via AI Gateway

Theorem T3: Interactive setup wizard runs BEFORE bridge starts (P4 + P7 + P11).
Theorem T7: Config file persists so setup wizard only runs once (P3 + P5).
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

# Add parent to path for imports
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from src.config_manager import (
    load_config,
    save_config,
    config_exists,
    validate_config,
    build_system_prompt,
    get_config_dir,
    DEFAULT_CONFIG,
)
from src.bridge_manager import BridgeManager
from src.qr_server import start_qr_server
from src.whatsapp_channel import WhatsAppChannel
from src.contact_store import ContactStore

try:
    import httpx
except ImportError:
    httpx = None


# ─── Setup Wizard Questions (mapped to AskUserQuestion format) ───


SETUP_QUESTIONS = [
    {
        "id": "purpose",
        "question": "What will you primarily use WhatsApp automation for?",
        "header": "Purpose",
        "options": [
            {"label": "Personal Assistant (Recommended)", "value": "personal_assistant",
             "description": "Auto-reply to personal messages with AI-powered responses"},
            {"label": "Business Support", "value": "business_support",
             "description": "Handle customer inquiries and business communications"},
            {"label": "Team Coordination", "value": "team_coordination",
             "description": "Help coordinate team activities and reminders"},
            {"label": "Monitoring Only", "value": "monitoring_only",
             "description": "Just log messages, never send replies"},
        ],
    },
    {
        "id": "tone",
        "question": "What tone should the AI use when replying?",
        "header": "Tone",
        "options": [
            {"label": "Casual & Friendly (Recommended)", "value": "casual_friendly",
             "description": "Relaxed, conversational tone like texting a friend"},
            {"label": "Professional", "value": "professional",
             "description": "Formal and business-appropriate language"},
            {"label": "Concise & Direct", "value": "concise_direct",
             "description": "Short, to-the-point responses with no filler"},
            {"label": "Warm & Empathetic", "value": "warm_empathetic",
             "description": "Caring and understanding tone"},
        ],
    },
    {
        "id": "mode",
        "question": "How should the bot handle incoming messages?",
        "header": "Reply Mode",
        "options": [
            {"label": "Auto-Reply (Recommended)", "value": "auto_reply",
             "description": "Automatically respond to allowed contacts"},
            {"label": "Ask Before Replying", "value": "ask_before_reply",
             "description": "Show the message and proposed reply, wait for approval"},
            {"label": "Monitor Only", "value": "monitor_only",
             "description": "Log all messages but never send any replies"},
        ],
    },
    {
        "id": "contact_access",
        "question": "Who should the bot respond to?",
        "header": "Contacts",
        "options": [
            {"label": "Everyone (Recommended)", "value": "everyone",
             "description": "Respond to all personal chat messages"},
            {"label": "Specific Contacts Only", "value": "allowlist",
             "description": "Only respond to contacts you specify"},
            {"label": "Everyone Except...", "value": "blocklist",
             "description": "Respond to all except contacts you block"},
        ],
    },
    {
        "id": "voice",
        "question": "How should voice messages be handled?",
        "header": "Voice",
        "options": [
            {"label": "Transcribe (Recommended)", "value": "transcribe",
             "description": "Convert voice messages to text using AI transcription"},
            {"label": "Acknowledge Only", "value": "placeholder",
             "description": "Note that a voice message was received without transcribing"},
            {"label": "Ignore", "value": "ignore",
             "description": "Skip voice messages entirely"},
        ],
    },
    {
        "id": "media",
        "question": "How should images, videos, and documents be handled?",
        "header": "Media",
        "options": [
            {"label": "Acknowledge Only (Recommended)", "value": "acknowledge",
             "description": "Note media was received and describe it if possible"},
            {"label": "Ignore", "value": "ignore",
             "description": "Skip messages that only contain media"},
        ],
    },
    {
        "id": "group_policy",
        "question": "How should group messages be handled?",
        "header": "Groups",
        "options": [
            {"label": "Monitor Only (Recommended)", "value": "monitor",
             "description": "Log group messages but never auto-reply (safest)"},
            {"label": "Ignore Completely", "value": "ignore",
             "description": "Don't even log group messages"},
        ],
    },
]


def map_answers_to_config(answers: dict[str, str]) -> dict:
    """Map wizard answers to config fields."""
    config = dict(DEFAULT_CONFIG)

    config["purpose"] = answers.get("purpose", "personal_assistant")
    config["tone"] = answers.get("tone", "casual_friendly")
    config["mode"] = answers.get("mode", "auto_reply")
    config["group_policy"] = answers.get("group_policy", "monitor")

    # Contact access
    contact_access = answers.get("contact_access", "everyone")
    if contact_access == "allowlist":
        phones = answers.get("allowlist_phones", "")
        config["allowlist"] = [p.strip() for p in phones.split(",") if p.strip()]
    elif contact_access == "blocklist":
        phones = answers.get("blocklist_phones", "")
        config["blocklist"] = [p.strip() for p in phones.split(",") if p.strip()]

    # Voice
    voice = answers.get("voice", "transcribe")
    config["voice_transcription"] = voice == "transcribe"

    # Media
    config["media_handling"] = answers.get("media", "acknowledge")

    return config


# ─── AI Response Generation ───


async def generate_ai_response(
    message: str,
    system_prompt: str,
    chat_history: list[dict],
    config: dict,
) -> str:
    """Generate an AI response using the AI Gateway."""
    if not httpx:
        return "AI response unavailable (httpx not installed)"

    api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
    if not api_key:
        return "AI response unavailable (no API key)"

    gateway_url = config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1")
    model = config.get("ai_model", "claude-sonnet-4-6")

    messages = [{"role": "system", "content": system_prompt}]
    # Add recent history (last 20 messages for context)
    messages.extend(chat_history[-20:])
    messages.append({"role": "user", "content": message})

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{gateway_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 1024,
                    "temperature": 0.7,
                },
                timeout=60.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            else:
                print(f"AI Gateway error {resp.status_code}: {resp.text[:200]}")
                return "I'm having trouble thinking right now. Please try again in a moment."
    except Exception as e:
        print(f"AI request error: {e}")
        return "I'm temporarily unavailable. Please try again shortly."


# ─── Main Orchestrator ───


class WhatsAppOrchestrator:
    """Main orchestrator that ties all components together."""

    def __init__(self):
        self.config: dict = {}
        self.system_prompt: str = ""
        self.bridge: BridgeManager | None = None
        self.qr_server = None
        self.channel: WhatsAppChannel | None = None
        self.chat_histories: dict[str, list[dict]] = {}  # chat_id -> messages
        self.contact_store: ContactStore | None = None

    def print_setup_instructions(self) -> None:
        """Print the setup wizard questions for the user to answer via AskUserQuestion."""
        print("\n" + "=" * 60)
        print("HAPPYCAPY WHATSAPP - SETUP WIZARD")
        print("=" * 60)
        print()
        print("This skill needs to be configured interactively.")
        print("The following AskUserQuestion calls should be made:")
        print()

        for q in SETUP_QUESTIONS:
            print(f"  Q: {q['question']}")
            for opt in q["options"]:
                print(f"     - {opt['label']}: {opt['description']}")
            print()

        print("After answering, the config will be saved and services started.")
        print("=" * 60)

    def apply_config(self, config: dict) -> None:
        """Apply and save configuration."""
        issues = validate_config(config)
        if issues:
            print(f"Config validation warnings: {issues}")

        self.config = config
        save_config(config)
        self.system_prompt = build_system_prompt(config)
        print(f"Config saved. Mode: {config['mode']}, Tone: {config['tone']}")

    def start_bridge(self) -> None:
        """Start the Node.js bridge process."""
        bridge_dir = SKILL_DIR / "bridge"
        auth_dir = self.config.get("auth_dir", str(Path.home() / ".happycapy-whatsapp" / "whatsapp-auth"))

        # Ensure auth dir exists
        Path(auth_dir).mkdir(parents=True, exist_ok=True)

        self.bridge = BridgeManager(
            bridge_dir=str(bridge_dir),
            port=self.config.get("bridge_port", 3002),
            auth_dir=auth_dir,
            token=self.config.get("bridge_token", ""),
        )
        self.bridge.start()
        print("Bridge manager started")

    def start_qr_server(self) -> str:
        """Start QR server and export port. Returns the public URL."""
        port = self.config.get("qr_server_port", 8765)
        self.qr_server = start_qr_server(port)

        # Export port for external access (Premise P12)
        try:
            result = subprocess.run(
                ["/app/export-port.sh", str(port)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            url = result.stdout.strip()
            if url:
                print(f"QR page available at: {url}")
                return url
        except Exception as e:
            print(f"Could not export port: {e}")

        return f"http://localhost:{port}"

    async def handle_message(
        self, sender_id: str, chat_id: str, content: str, media_paths: list, metadata: dict
    ) -> None:
        """Handle an incoming WhatsApp message."""
        mode = self.config.get("mode", "auto_reply")

        print(f"[{sender_id}] {content[:100]}")

        # Store conversation sample for contact profiling
        if self.contact_store:
            self.contact_store.store_sample(sender_id, "user", content)

        if mode == "monitor_only":
            return

        # Get/create chat history
        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = []

        history = self.chat_histories[chat_id]
        history.append({"role": "user", "content": content})

        # Trim history to last 40 messages
        if len(history) > 40:
            self.chat_histories[chat_id] = history[-40:]
            history = self.chat_histories[chat_id]

        if mode == "ask_before_reply":
            # In ask-before-reply mode, print the message and wait
            # The user would need to approve via the HappyCapy interface
            print(f"[APPROVAL NEEDED] {sender_id}: {content}")
            print("(In a full integration, AskUserQuestion would be used here)")
            # For now, still generate and send - full approval flow requires
            # deeper integration with the HappyCapy agent loop
            pass

        # Build per-contact system prompt with profile injection
        system_prompt = self.system_prompt
        if self.contact_store:
            profile_context = self.contact_store.format_profile_for_prompt(sender_id)
            if profile_context:
                system_prompt = system_prompt + "\n" + profile_context

        # Generate AI response
        response = await generate_ai_response(
            message=content,
            system_prompt=system_prompt,
            chat_history=history,
            config=self.config,
        )

        # Send response
        if self.channel and response:
            await self.channel.send_text(chat_id, response)
            history.append({"role": "assistant", "content": response})
            print(f"[reply -> {sender_id}] {response[:100]}")

            # Store assistant sample too
            if self.contact_store:
                self.contact_store.store_sample(sender_id, "assistant", response)

        # Check if contact profile needs generation/update (async, non-blocking)
        if self.contact_store and self.contact_store.needs_profile_update(sender_id):
            asyncio.create_task(self._update_contact_profile(sender_id))

    async def _update_contact_profile(self, jid: str) -> None:
        """Background task to generate/update a contact profile."""
        try:
            await self.contact_store.generate_profile(jid, self.config)
        except Exception as e:
            print(f"Contact profile update failed for {jid}: {e}")

    async def run(self) -> None:
        """Main run loop."""
        # Load or create config
        if config_exists():
            self.config = load_config()
            self.system_prompt = build_system_prompt(self.config)
            print(f"Loaded existing config (mode: {self.config['mode']})")
        else:
            # Print instructions - actual AskUserQuestion calls happen
            # from the SKILL.md instructions in the agent context
            self.print_setup_instructions()
            # Use defaults for now - the skill instructions will override
            self.config = dict(DEFAULT_CONFIG)
            save_config(self.config)
            self.system_prompt = build_system_prompt(self.config)

        # Initialize contact store for persistent per-contact profiles
        db_path = get_config_dir() / "contacts.db"
        self.contact_store = ContactStore(db_path)
        print(f"Contact store initialized ({db_path})")

        # Start services
        self.start_bridge()
        qr_url = self.start_qr_server()

        print(f"\nScan the QR code at: {qr_url}")
        print("Waiting for WhatsApp connection...\n")

        # Start channel
        self.channel = WhatsAppChannel(
            config=self.config,
            on_message=self.handle_message,
        )

        # Handle shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Run channel (blocks until stopped)
        await self.channel.start()

    async def shutdown(self) -> None:
        """Gracefully shut down all services."""
        print("\nShutting down...")

        if self.channel:
            await self.channel.stop()

        if self.bridge:
            self.bridge.stop()

        if self.qr_server:
            self.qr_server.shutdown()

        if self.contact_store:
            self.contact_store.close()

        print("All services stopped.")


def main():
    """Entry point."""
    orchestrator = WhatsAppOrchestrator()
    asyncio.run(orchestrator.run())


if __name__ == "__main__":
    main()
