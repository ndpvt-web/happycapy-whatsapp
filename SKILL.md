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

Check if `~/.happycapy-whatsapp/config.json` exists. If NOT, run the **dynamic setup wizard**.

#### Phase 1: Open-ended intent gathering

Use AskUserQuestion with a SINGLE open-ended question:
- header: "WhatsApp Setup"
- question: "What would you like to do with WhatsApp? Describe your use case and I'll configure everything automatically. You can also mention your phone number if you'd like admin access."
- options:
  - "Personal AI assistant that replies to my messages" - Auto-reply to personal chats with AI
  - "Monitor messages without replying" - Log and observe WhatsApp activity silently
  - "Business customer support bot" - Handle customer inquiries automatically
- multiSelect: false

The user may select an option OR type a custom free-text description via "Other".

#### Phase 2: Intent analysis and config inference

Analyze the user's response to extract as many config values as possible. Use these inference rules:

| Signal in user's response | Config inference |
|---|---|
| "monitor", "watch", "log", "observe", "alert" | purpose: "monitoring_only", mode: "monitor_only" |
| "personal", "my messages", "assistant" | purpose: "personal_assistant", mode: "auto_reply" |
| "business", "customer", "support", "client" | purpose: "business_support", mode: "auto_reply", tone: "professional" |
| "team", "coordinate", "group", "project" | purpose: "team_coordination", mode: "auto_reply" |
| Phone number mentioned (e.g. +852 92893658) | admin_number: extracted digits |
| "everyone", "all contacts" | allowlist: [] (empty = everyone) |
| "only [name/number]", "specific people" | Follow up for allowlist numbers |
| "casual", "friendly", "chill" | tone: "casual_friendly" |
| "professional", "formal", "business" | tone: "professional" |
| "short", "brief", "concise" | tone: "concise_direct" |
| "never reply", "don't respond", "silent" | mode: "monitor_only" |
| "ask me first", "approve", "confirm" | mode: "ask_before_reply" |

#### Phase 3: Verification gate -- check you have everything

After inference, verify you have ALL the information needed. The fields are split into three tiers:

**REQUIRED (must ask if missing -- the bot cannot work well without these):**

| Field | Why required |
|---|---|
| `purpose` | Determines the entire system prompt personality |
| `mode` | Controls whether bot replies at all -- wrong default = spam or silence |
| `admin_number` | Needed for admin slash commands, security alerts, and error notifications |

**IMPORTANT (should ask if missing -- affects quality and safety):**

| Field | Why important |
|---|---|
| `tone` | Directly affects how the bot sounds to contacts |
| `allowlist` / contact scope | Wrong default could mean replying to strangers |

**OPTIONAL (safe to use smart defaults):**

| Field | Default | Rationale |
|---|---|---|
| voice_transcription | true | Users generally want this |
| media_handling | "acknowledge" | Safe, non-committal |
| group_policy | "monitor" | Never auto-reply in groups (safe) |

**Verification procedure:**

1. After Phase 2 inference, make a checklist of what you have vs what's missing.
2. Collect ALL missing REQUIRED + IMPORTANT fields into a SINGLE AskUserQuestion call (up to 4 questions max). Use one question per missing field group.
3. If the user's Phase 1 answer was a preset option (not free text), you will likely be missing most fields -- ask for them all in one go.
4. NEVER silently default a REQUIRED field. Always ask.

**Example: User selected "Personal AI assistant that replies to my messages"**

You inferred: purpose=personal_assistant, mode=auto_reply.
Missing REQUIRED: admin_number. Missing IMPORTANT: tone, contact scope.
Ask ONE AskUserQuestion with up to 3 questions:

```
Question 1 (header: "Admin"):
  "What is your phone number? This will be your admin number for controlling the bot via WhatsApp."
  Options: (let user type via "Other" -- provide example formats as options)
  - "+1 555 123 4567" - US format example
  - "+44 7911 123456" - UK format example
  - "+852 9289 3658" - HK format example
  multiSelect: false

Question 2 (header: "Tone"):
  "What tone should the bot use when replying?"
  Options:
  - "Casual & Friendly (Recommended)" - Relaxed, conversational
  - "Professional" - Formal and business-appropriate
  - "Concise & Direct" - Short, no fluff
  - "Warm & Empathetic" - Caring and understanding
  multiSelect: false

Question 3 (header: "Contacts"):
  "Who should the bot respond to?"
  Options:
  - "Everyone (Recommended)" - Reply to all personal messages
  - "Only my number" - Only reply to admin
  - "Specific contacts" - I'll provide phone numbers
  multiSelect: false
```

**Example: User typed "monitor my business WhatsApp and alert me on +852 92893658"**

You inferred: purpose=monitoring_only, mode=monitor_only, admin_number=85292893658.
Missing REQUIRED: nothing. Missing IMPORTANT: nothing (tone irrelevant for monitor-only, contacts irrelevant since not replying).
--> No follow-up needed. Proceed to Phase 4.

**Example: User typed "I want a WhatsApp bot"**

You inferred: mode=auto_reply (from "bot").
Missing REQUIRED: purpose, admin_number. Missing IMPORTANT: tone, contact scope.
Ask ONE AskUserQuestion with all 4:

```
Question 1 (header: "Purpose"):
  "What should the bot do?"
  Options:
  - "Personal Assistant (Recommended)" - Help with personal messages
  - "Business Support" - Handle customer inquiries
  - "Team Coordination" - Coordinate team activities

Question 2 (header: "Admin"):
  "What is your phone number for admin access?"
  (same format as above)

Question 3 (header: "Tone"):
  (same as above)

Question 4 (header: "Contacts"):
  (same as above)
```

If "Specific contacts" is selected for contacts, do ONE more follow-up asking for comma-separated phone numbers.

#### Phase 4: Apply defaults for OPTIONAL fields only

After all REQUIRED and IMPORTANT fields are resolved (from inference + follow-up questions), apply smart defaults ONLY for optional fields that were not explicitly set:

| Field | Default | Rationale |
|---|---|---|
| voice_transcription | true | Users generally want this |
| media_handling | "acknowledge" | Safe, non-committal |
| group_policy | "monitor" | Never auto-reply in groups |
| bridge_port | 3002 | Standard port |
| qr_server_port | 8765 | Standard port |

**Tone special case:** If mode is "monitor_only", tone defaults to "casual_friendly" silently (it won't be used anyway since the bot doesn't reply). Do NOT ask the user about tone for monitor-only mode.

#### Phase 5: Save config

After resolving all fields, save using Python:
```python
import json, os
from pathlib import Path

config = {
    "purpose": "<inferred or asked>",
    "tone": "<inferred or asked>",
    "mode": "<inferred or asked>",
    "admin_number": "<inferred or asked>",
    "allowlist": [],  # or specific numbers from follow-up
    "blocklist": [],
    "voice_transcription": True,  # default
    "media_handling": "acknowledge",  # default
    "group_policy": "monitor",
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

#### Phase 6: Confirm back to user

**Always tell the user what was configured** with a summary showing every resolved field, so they can correct anything wrong. Format as a clear list:

Example:
```
Here's your WhatsApp configuration:
- Purpose: Personal Assistant
- Mode: Auto-reply
- Tone: Casual & friendly
- Admin: +852 9289 3658
- Contacts: Everyone
- Voice: Transcription enabled
- Media: Acknowledge
- Groups: Monitor only

Starting services now...
```

This lets the user spot any misunderstanding before the bot goes live.

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
