---
name: happycapy-whatsapp
description: "Complete WhatsApp automation with interactive setup wizard, visual QR code authentication (auto-refreshing web page), configurable auto-reply, voice transcription, contact filtering, and monitoring. Zero hardcoding - all behavior configured via AskUserQuestion. Use when the user wants to set up WhatsApp automation, connect WhatsApp, create a WhatsApp bot, automate WhatsApp messaging, or says /whatsapp."
---

# HappyCapy WhatsApp

Standalone WhatsApp automation with interactive setup and visual QR authentication.

## Launch Instructions

When this skill is triggered, follow these steps IN ORDER:

### Step 1: Install Dependencies (first time only)

Check if the bridge is compiled. If `~/.claude/skills/happycapy-whatsapp/bridge/dist/index.js` does not exist, run:

```bash
bash ~/.claude/skills/happycapy-whatsapp/scripts/setup.sh
```

### Step 2: Interactive Setup (first time only)

Check if `~/.happycapy-whatsapp/config.json` exists. If NOT, run the setup wizard using AskUserQuestion with these 7 questions:

**Question 1 - Purpose:**
Use AskUserQuestion with header "Purpose", question "What will you primarily use WhatsApp automation for?", options:
- "Personal Assistant (Recommended)" - Auto-reply to personal messages with AI-powered responses
- "Business Support" - Handle customer inquiries and business communications
- "Team Coordination" - Help coordinate team activities and reminders
- "Monitoring Only" - Just log messages, never send replies

Map: Personal Assistant -> purpose: "personal_assistant", Business Support -> "business_support", Team Coordination -> "team_coordination", Monitoring Only -> "monitoring_only"

**Question 2 - Tone:**
Use AskUserQuestion with header "Tone", question "What tone should the AI use when replying?", options:
- "Casual & Friendly (Recommended)" - Relaxed, conversational tone
- "Professional" - Formal and business-appropriate
- "Concise & Direct" - Short, to-the-point, no filler
- "Warm & Empathetic" - Caring and understanding

Map: Casual -> tone: "casual_friendly", Professional -> "professional", Concise -> "concise_direct", Warm -> "warm_empathetic"

**Question 3 - Reply Mode:**
Use AskUserQuestion with header "Reply Mode", question "How should the bot handle incoming messages?", options:
- "Auto-Reply (Recommended)" - Automatically respond to allowed contacts
- "Ask Before Replying" - Show message and proposed reply, wait for approval
- "Monitor Only" - Log all messages but never reply

Map: Auto-Reply -> mode: "auto_reply", Ask Before -> "ask_before_reply", Monitor -> "monitor_only"

**Question 4 - Contacts:**
Use AskUserQuestion with header "Contacts", question "Who should the bot respond to?", options:
- "Everyone (Recommended)" - Respond to all personal chat messages
- "Specific Contacts Only" - Only respond to contacts you specify (follow up for phone numbers)
- "Everyone Except..." - Block specific contacts (follow up for phone numbers)

If "Specific Contacts Only": follow up asking for comma-separated phone numbers -> allowlist
If "Everyone Except...": follow up asking for numbers to block -> blocklist

**Question 5 - Voice Messages:**
Use AskUserQuestion with header "Voice", question "How should voice messages be handled?", options:
- "Transcribe (Recommended)" - Convert voice to text using AI
- "Acknowledge Only" - Note that a voice was received
- "Ignore" - Skip voice messages

Map: Transcribe -> voice_transcription: true, others -> false

**Question 6 - Media:**
Use AskUserQuestion with header "Media", question "How should images, videos, and documents be handled?", options:
- "Acknowledge Only (Recommended)" - Note media was received
- "Ignore" - Skip media-only messages

Map: Acknowledge -> media_handling: "acknowledge", Ignore -> "ignore"

**Question 7 - Groups:**
Use AskUserQuestion with header "Groups", question "How should group messages be handled?", options:
- "Monitor Only (Recommended)" - Log group messages but NEVER auto-reply
- "Ignore Completely" - Don't even log group messages

Map: Monitor -> group_policy: "monitor", Ignore -> "ignore"

After all questions, save config using Python:
```python
import json
from pathlib import Path

config = {
    "purpose": "<from Q1>",
    "tone": "<from Q2>",
    "mode": "<from Q3>",
    "allowlist": [],  # or phone numbers from Q4
    "blocklist": [],  # or phone numbers from Q4
    "voice_transcription": True/False,  # from Q5
    "media_handling": "<from Q6>",
    "group_policy": "<from Q7>",
    "bridge_port": 3002,
    "qr_server_port": 8765,
    "auth_dir": str(Path.home() / ".happycapy-whatsapp" / "whatsapp-auth"),
    "ai_gateway_url": "https://ai-gateway.happycapy.ai/api/v1",
    "ai_model": "claude-sonnet-4-6",
    "max_message_length": 4000,
    "rate_limit_per_minute": 30
}
Path.home().joinpath(".happycapy-whatsapp").mkdir(parents=True, exist_ok=True)
Path.home().joinpath(".happycapy-whatsapp", "config.json").write_text(json.dumps(config, indent=2))
```

### Step 3: Start Services

Launch the orchestrator. Use **daemon mode** for 24/7 operation (auto-restarts on crash):
```bash
cd ~/.claude/skills/happycapy-whatsapp && bash scripts/start.sh daemon
```

Or for foreground mode (for debugging):
```bash
cd ~/.claude/skills/happycapy-whatsapp && python3 -m src.main &
```

Daemon management:
```bash
bash ~/.claude/skills/happycapy-whatsapp/scripts/start.sh status   # Check status
bash ~/.claude/skills/happycapy-whatsapp/scripts/start.sh stop     # Stop daemon
bash ~/.claude/skills/happycapy-whatsapp/scripts/start.sh restart  # Restart daemon
```

### Step 4: Share QR URL

After services start, run:
```bash
/app/export-port.sh 8765
```

Share the returned URL with the user so they can scan the QR code.

Tell the user: "Open this URL and scan the QR code with your WhatsApp mobile app (Settings > Linked Devices > Link a Device)."

### Step 5: Confirm Connection

Monitor the orchestrator output. When you see "WhatsApp connected!", inform the user that their WhatsApp is now linked and the bot is active.

## 24/7 Daemon Mode

The daemon provides continuous operation with process supervision:
- **Auto-restart** on crash with exponential backoff (3s to 120s)
- **PID file** tracking at `~/.happycapy-whatsapp/daemon.pid`
- **Log rotation** at 10MB with one backup file
- **Graceful shutdown** via SIGTERM
- Restarts reset if the process was stable for >5 minutes
- Maximum 50 restart attempts before giving up

Logs: `~/.happycapy-whatsapp/logs/daemon.log`

## Contact Cards (Persistent Profiles)

The bot automatically builds per-contact profiles over time:
- After **5 messages** from a contact, an LLM analyzes the conversation to generate a profile
- Profiles are **re-analyzed every 20 new messages** to stay current
- Profile data: tone, formality, emoji usage, language, relationship, topics, sample phrases
- Stored in SQLite at `~/.happycapy-whatsapp/contacts.db`
- Profiles are injected into the AI system prompt for **personalized, context-aware replies**

This means the bot adapts to each contact's communication style automatically.

## Architecture

```
Phone <-> WhatsApp Server <-> Baileys Bridge (Node.js :3002 internal)
                                    | WebSocket
                              Channel (Python)
                                    |
                              AI Agent (LLM via AI Gateway)

Browser <-> QR Server (Python HTTP :8765 exposed)
```

## Configuration

Stored at `~/.happycapy-whatsapp/config.json`. See `references/config-schema.md` for all fields.

## Media Intelligence

The bot understands all incoming media types and can send files outbound.

### Inbound Understanding (automatic)
- **Images**: Sent to the AI via multimodal vision API - the AI can see and describe images
- **PDFs**: Text extracted automatically via pdfplumber and included in AI context
- **Voice messages**: Transcribed to text via Whisper API (when voice_transcription enabled)
- **Videos**: Keyframe extracted for vision + audio extracted for transcription
- **Stickers**: Analyzed via vision API like images
- **Documents**: PDF text extraction; other formats acknowledged with metadata

### Outbound Sending

To send a file (image, PDF, video, audio, document) to a WhatsApp contact:

```bash
# Send a file
cd ~/.claude/skills/happycapy-whatsapp
python -m src.send_file --to 1234567890 --file /path/to/file.pdf

# Send with caption
python -m src.send_file --to 1234567890 --file photo.jpg --caption "Here you go"

# Send text only
python -m src.send_file --to 1234567890 --text "Hello from the agent!"
```

The `--to` parameter accepts phone numbers (digits only) or full JIDs (number@s.whatsapp.net).
The script auto-connects to the running bridge, sends, and disconnects.

## Security

- Bridge binds to 127.0.0.1 only (not externally accessible)
- Token authentication on WebSocket
- Groups are NEVER auto-replied to (Theorem T6)
- AI reasoning stripped from outbound messages
- Contact filtering via allowlist/blocklist
- Rate limiting: configurable messages per minute
- Media files cleaned up automatically on startup

## Requirements

- Node.js 20+ (available in HappyCapy)
- Python 3.11+ (available in HappyCapy)
- `AI_GATEWAY_API_KEY` environment variable (auto-configured)
- ffmpeg (for video processing - available in HappyCapy)
- pdfplumber (for PDF text extraction - installed by setup.sh)
