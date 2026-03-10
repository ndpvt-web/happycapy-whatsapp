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

The wizard uses AskUserQuestion to gather user intent dynamically. The goal: **minimum questions, maximum configuration**. Most users should be fully set up in 1-2 AskUserQuestion calls.

#### Phase 1: Open-ended intent gathering

Use AskUserQuestion with a SINGLE open-ended question:
- header: "WhatsApp Setup"
- question: "What would you like to do with WhatsApp? Describe your use case and I'll configure everything automatically. You can also mention your phone number if you'd like admin access."
- options:
  - "Personal assistant -- replies as me" (Recommended) - Bot impersonates you, replies to chats as if you typed it
  - "Business automation -- orders, invoices, tracking" - Log orders to spreadsheets, send invoices via email, handle customer messages
  - "AI assistant bot -- transparent AI helper" - Bot identifies as an AI assistant helping on your behalf
  - "Monitor messages without replying" - Log and observe WhatsApp activity silently
- multiSelect: false

The user may select an option OR type a custom free-text description via "Other".

#### Phase 2: Intent analysis and config inference

Analyze the user's response to extract as many config values as possible. Use ALL these inference rules:

**Core fields:**

| Signal in user's response | Config inference |
|---|---|
| "monitor", "watch", "log", "observe", "alert", "silent" | purpose: "monitoring_only", mode: "monitor_only" |
| "personal", "my messages", "as me", "impersonate" | purpose: "personal_assistant", mode: "auto_reply", personality_mode: "impersonate" |
| "business", "customer", "support", "client", "orders" | purpose: "business_support", mode: "auto_reply", tone: "professional" |
| "team", "coordinate", "group project", "standup" | purpose: "team_coordination", mode: "auto_reply" |
| "assistant", "AI assistant", "bot", "helper" | personality_mode: "assistant" |
| "as me", "pretend to be me", "impersonate", "act as me" | personality_mode: "impersonate" |
| Phone number mentioned (e.g. +852 92893658) | admin_number: extracted digits |
| "everyone", "all contacts", "anybody" | allowlist: [] (empty = everyone) |
| "only [name/number]", "specific people" | Follow up for allowlist numbers |
| "casual", "friendly", "chill", "relaxed" | tone: "casual_friendly" |
| "professional", "formal", "corporate" | tone: "professional" |
| "short", "brief", "concise", "direct" | tone: "concise_direct" |
| "warm", "caring", "empathetic", "kind" | tone: "warm_empathetic" |
| "never reply", "don't respond", "silent mode" | mode: "monitor_only" |
| "ask me first", "approve", "confirm before" | mode: "ask_before_reply" |
| "auto reply", "automatic", "just reply" | mode: "auto_reply" |

**Business type signals (infer business_template -- only when purpose is business_support):**

| Signal in user's response | Config inference |
|---|---|
| "restaurant", "cafe", "bakery", "food", "kitchen", "catering", "delivery" | business_template: "food_restaurant" |
| "salon", "spa", "barber", "beauty", "nail", "hair", "massage" | business_template: "beauty_wellness" |
| "shop", "store", "retail", "clothing", "electronics", "grocery", "pharmacy" | business_template: "retail_shop" |
| "lawyer", "accountant", "consultant", "agency", "freelance", "architect" | business_template: "professional_services" |
| "doctor", "clinic", "dentist", "hospital", "medical", "vet", "physio" | business_template: "healthcare" |
| "real estate", "property", "rental", "broker", "housing" | business_template: "real_estate" |
| "hotel", "travel", "tour", "airbnb", "hostel", "guesthouse" | business_template: "travel_hospitality" |
| "school", "tutor", "coaching", "training", "course", "academy" | business_template: "education" |
| "plumber", "electrician", "cleaning", "repair", "handyman", "maintenance" | business_template: "home_services" |

When a business type is inferred from free text, skip the business type question. Apply the template using `resolve_business_type()` and `apply_template()` from `src.business_templates`.

**Integration signals (infer enabled_integrations):**

| Signal in user's response | Config inference |
|---|---|
| "spreadsheet", "excel", "track orders", "log data", "inventory" | enabled_integrations includes "spreadsheet" |
| "email", "invoice", "send bills", "receipt", "notification" | enabled_integrations includes "email" |
| "business", "orders", "B2B", "small business" | enabled_integrations: ["core", "spreadsheet", "email"] (both) |

**Personality & safety signals (infer from context):**

| Signal / context | Config inference |
|---|---|
| Selected "Personal assistant -- replies as me" | personality_mode: "impersonate", privacy_level: "strict", fabrication_policy: "strict" |
| Selected "Business automation" | personality_mode: "assistant", privacy_level: "strict", fabrication_policy: "deflect", enabled_integrations: ["core", "spreadsheet", "email"] |
| Selected "AI assistant bot" | personality_mode: "assistant", privacy_level: "moderate", fabrication_policy: "deflect" |
| Selected "Monitor messages" | personality_mode: "assistant", privacy_level: "strict", fabrication_policy: "strict" |
| "private", "strict", "don't share" | privacy_level: "strict" |
| Name mentioned ("I'm John", "my name is Sarah") | owner_name: extracted name |

#### Phase 3: Verification gate -- ask ONLY what's missing

After inference, check what you have vs what's missing. Fields are split into tiers:

**REQUIRED (must ask if missing -- bot cannot work well without these):**

| Field | Why required |
|---|---|
| `purpose` | Determines the entire system prompt personality |
| `mode` | Controls whether bot replies at all -- wrong default = spam or silence |
| `admin_number` | Needed for admin commands, security alerts, error notifications |

**IMPORTANT (should ask if missing -- affects quality and safety):**

| Field | Why important |
|---|---|
| `personality_mode` | "impersonate" vs "assistant" fundamentally changes bot behavior |
| `tone` | Directly affects how the bot sounds to contacts |
| `allowlist` / contact scope | Wrong default could mean replying to strangers |
| `enabled_integrations` | User may want spreadsheet/email features but not know to ask |

**OPTIONAL (safe to use smart defaults -- never ask about these):**

| Field | Default | Rationale |
|---|---|---|
| privacy_level | "strict" | Safest default, never leaks info |
| fabrication_policy | "strict" | Safest default, asks owner when unsure |
| voice_transcription | true | Users generally want this |
| media_handling | "acknowledge" | Safe, non-committal |
| group_policy | "monitor" | Never auto-reply in groups (safe) |
| owner_name | "" | Bot works fine without it |
| quiet_hours_enabled | false | Can enable later via /quiet command |
| All technical fields | (see Phase 5) | Infrastructure, never user-facing |

**Verification procedure:**

1. After Phase 2 inference, make a checklist of what you have vs what's missing.
2. Collect ALL missing REQUIRED + IMPORTANT fields into a SINGLE AskUserQuestion call (up to 4 questions max).
3. If the user's Phase 1 answer was a preset option (not free text), you likely have most fields from the preset inference rules above -- only ask for what's truly missing.
4. NEVER silently default a REQUIRED field. Always ask.
5. **Skip irrelevant questions**: If mode is "monitor_only", don't ask about tone, personality_mode, or integrations. If personality_mode is already clear, don't ask again.

**Question templates for missing fields:**

```
Admin number (header: "Admin"):
  "What is your phone number? This will be your admin number for controlling the bot via WhatsApp."
  Options: (let user type via "Other")
  - "+1 555 123 4567" - US format example
  - "+44 7911 123456" - UK format example
  - "+852 9289 3658" - HK format example
  multiSelect: false

Tone (header: "Tone"):
  "What tone should the bot use when replying?"
  Options:
  - "Casual & Friendly (Recommended)" - Relaxed, conversational
  - "Professional" - Formal and business-appropriate
  - "Concise & Direct" - Short, no fluff
  - "Warm & Empathetic" - Caring and understanding
  multiSelect: false

Contacts (header: "Contacts"):
  "Who should the bot respond to?"
  Options:
  - "Everyone (Recommended)" - Reply to all personal messages
  - "Only my number" - Only reply to admin
  - "Specific contacts" - I'll provide phone numbers
  multiSelect: false

Personality (header: "Personality"):
  "How should the bot behave when replying?"
  Options:
  - "Act as me (Recommended)" - Impersonate you, never reveal it's AI
  - "AI Assistant" - Transparent AI helper, can say it's a bot
  multiSelect: false

Integrations (header: "Features"):
  "Which extra features would you like?"
  Options:
  - "Just AI chat (Recommended)" - Core features only
  - "Spreadsheet tracking" - Log orders, expenses, data to Excel
  - "Email sending" - Send emails via the bot
  - "Both spreadsheet + email" - Full business suite
  multiSelect: false
```

**Example flow: User selected "Personal assistant -- replies as me"**

Inferred from preset: purpose=personal_assistant, mode=auto_reply, personality_mode=impersonate, privacy_level=strict, fabrication_policy=strict.
Missing REQUIRED: admin_number. Missing IMPORTANT: tone, contacts.
Ask ONE AskUserQuestion with 3 questions: Admin, Tone, Contacts.

**Example flow: User selected "Business automation -- orders, invoices, tracking"**

Inferred from preset: purpose=business_support, mode=auto_reply, personality_mode=assistant, tone=professional, privacy_level=strict, fabrication_policy=deflect, enabled_integrations=["core","spreadsheet","email"].

**IMPORTANT: Business Template Selection** -- When purpose is "business_support" (user selected "Business automation"), ask ONE additional question to select a business type template:

```
Business Type (header: "Business Type"):
  "What type of business do you run? This auto-configures tone, vocabulary, workflows, and response patterns."
  Options:
  - "Food & Restaurant" -- food_restaurant (Menu sharing, order-taking, delivery time estimates)
  - "Beauty & Wellness" -- beauty_wellness (Appointment booking, service menu, availability)
  - "Retail & Shop" -- retail_shop (Product catalog, stock checks, order processing)
  - "Professional Services" -- professional_services (Client intake, scheduling, quotes)
  multiSelect: false
```

User can also type via "Other" for: healthcare, real_estate, travel_hospitality, education, home_services, custom_other.

The selected template auto-configures: tone, SOUL.md personality, spreadsheet column presets, integrations, and industry-specific workflows. Templates are just starting points -- everything remains editable via /commands and SOUL.md.

After template selection: Missing REQUIRED: admin_number. Missing IMPORTANT: contacts.
Ask ONE AskUserQuestion with 2 questions: Admin, Contacts.

**Example flow: User typed "monitor my business WhatsApp +852 92893658"**

Inferred: purpose=monitoring_only, mode=monitor_only, admin_number=85292893658.
Missing: nothing relevant (monitor mode doesn't need tone/personality/integrations).
--> No follow-up needed. Proceed to Phase 4.

**Example flow: User typed "I want a WhatsApp bot for my bakery, take orders and email receipts"**

Inferred: purpose=business_support, mode=auto_reply, tone=professional, personality_mode=assistant, enabled_integrations=["core","spreadsheet","email"].
Also inferred from "bakery": business_template=food_restaurant (auto-configures bakery-appropriate SOUL.md with order-taking workflows, warm tone, food-specific language).
Missing REQUIRED: admin_number. Missing IMPORTANT: contacts.
Ask ONE AskUserQuestion with 2 questions: Admin, Contacts. (Skip business type question since it was inferred from "bakery".)

If "Specific contacts" is selected for contacts, do ONE more follow-up asking for comma-separated phone numbers.

#### Phase 4: Show defaults and offer customization

After resolving REQUIRED and IMPORTANT fields, apply smart defaults for everything else. Then **show the user what they're getting** and let them choose to continue or customize.

**Smart defaults (applied automatically):**

| Field | Default | Rationale |
|---|---|---|
| personality_mode | "impersonate" (personal), "assistant" (business/team) | Most natural per use case |
| tone | "casual_friendly" (personal/team), "professional" (business) | Matches the purpose |
| mode | "auto_reply" (personal/business/team), "monitor_only" (monitor) | Expected behavior |
| privacy_level | "strict" | Safest -- never shares cross-contact info |
| fabrication_policy | "strict" if impersonate, "deflect" if assistant | Impersonate must never make things up |
| voice_transcription | true | Users generally want this |
| media_handling | "acknowledge" | Safe, non-committal |
| group_policy | "monitor" | Never auto-reply in groups |
| owner_name | "" | Bot works without it |

**Show the user their settings summary and ask:**

Use AskUserQuestion with:
- header: "Review Settings"
- question: "Here are your settings based on what you told me:\n\n[SHOW FULL SETTINGS SUMMARY - purpose, personality, tone, mode, privacy, voice, groups, integrations]\n\nWould you like to continue with these settings or customize them?"
- options:
  - "Continue -- looks good" (Recommended) - Start the bot with these settings
  - "Customize advanced settings" - Change personality, tone, mode, or privacy level
- multiSelect: false

**If user selects "Continue":** Proceed to Phase 5 (save config).

**If user selects "Customize":** Use ONE more AskUserQuestion call with up to 4 questions:

```
Personality (header: "Personality"):
  "How should the bot behave?"
  - "Act as me (Recommended)" -- impersonate
  - "AI Assistant" -- assistant

Tone (header: "Tone"):
  "What tone should the bot use?"
  - "Casual & Friendly" -- casual_friendly
  - "Professional" -- professional
  - "Concise & Direct" -- concise_direct
  - "Warm & Empathetic" -- warm_empathetic

Reply Mode (header: "Reply Mode"):
  "How should incoming messages be handled?"
  - "Auto-Reply" -- auto_reply
  - "Ask Before Replying" -- ask_before_reply
  - "Monitor Only" -- monitor_only

Privacy (header: "Privacy"):
  "How should private info between contacts be handled?"
  - "Strict (Recommended)" -- strict
  - "Moderate" -- moderate
  - "Open" -- open
```

Apply their choices as overrides on top of the smart defaults.

#### Phase 5: Save config

After resolving all fields, save the COMPLETE config using Python:

```python
import json
from pathlib import Path

config = {
    # Core (from inference + questions)
    "purpose": "<inferred or asked>",
    "tone": "<inferred or asked or defaulted>",
    "mode": "<inferred or asked>",
    "admin_number": "<inferred or asked>",
    "personality_mode": "<inferred or defaulted>",
    "owner_name": "<inferred or empty>",
    "business_template": "",  # or template ID like "food_restaurant", "beauty_wellness", etc.
    # Contact filtering
    "allowlist": [],  # or specific numbers
    "blocklist": [],
    # Privacy & safety
    "privacy_level": "<inferred or defaulted>",
    "fabrication_policy": "<inferred or defaulted>",
    # Integrations
    "enabled_integrations": ["core"],  # or ["core", "spreadsheet", "email"]
    # Media & voice
    "voice_transcription": True,
    "voice_transcription_provider": "groq",
    "media_handling": "acknowledge",
    "group_policy": "monitor",
    # Alerts & intelligence
    "alert_on_auto_reply": False,  # True for assistant mode
    "escalation_enabled": True,
    "importance_threshold": 7,
    "status_override": "",
    "auto_reply_when_busy": True,
    "group_keywords": [],
    # Quiet hours (off by default)
    "quiet_hours_enabled": False,
    "quiet_hours_start": "23:00",
    "quiet_hours_end": "07:00",
    "quiet_hours_timezone": "UTC",
    "quiet_hours_override_threshold": 9,
    # Tool calling
    "tool_calling_enabled": True,
    # Technical (never ask user)
    "tone_custom_instructions": "",
    "system_prompt_override": "",
    "bridge_port": 3002,
    "qr_server_port": 8765,
    "auth_dir": str(Path.home() / ".happycapy-whatsapp" / "whatsapp-auth"),
    "log_level": "INFO",
    "bridge_token": "",
    "ai_gateway_url": "https://ai-gateway.happycapy.ai/api/v1/openai/v1",
    "ai_model": "gpt-4.1-mini",
    "profile_model": "gpt-4.1-mini",
    "max_message_length": 4000,
    "rate_limit_per_minute": 30,
    "media_max_age_hours": 24,
    "whisper_api_url": "https://api.groq.com/openai/v1/audio/transcriptions",
}
Path.home().joinpath(".happycapy-whatsapp").mkdir(parents=True, exist_ok=True)
Path.home().joinpath(".happycapy-whatsapp", "config.json").write_text(json.dumps(config, indent=2))
```

#### Phase 6: Confirm back to user

**Always tell the user what was configured** with a summary. Only show user-facing fields, not technical internals:

Example for personal use:
```
Here's your WhatsApp configuration:
- Purpose: Personal Assistant
- Personality: Impersonate (replies as you, never reveals AI)
- Mode: Auto-reply
- Tone: Casual & friendly
- Admin: +852 9289 3658
- Contacts: Everyone
- Privacy: Strict (never shares info between contacts)
- Voice: Transcription enabled
- Groups: Monitor only

Starting services now...
```

Example for business use:
```
Here's your WhatsApp configuration:
- Purpose: Business Support
- Personality: AI Assistant (transparent)
- Mode: Auto-reply
- Tone: Professional
- Admin: +1 555 123 4567
- Contacts: Everyone
- Privacy: Strict
- Integrations: Spreadsheet tracking + Email sending
- Voice: Transcription enabled
- Groups: Monitor only

Your bot can now:
- Log orders/data to Excel spreadsheets
- Send emails (invoices, confirmations) via the bot
- Generate images, videos, and PDFs
- Reply to customer messages automatically

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
- **latex-document skill** (for professional PDF generation via LaTeX compilation)
  - If not already installed, clone from: `git clone https://github.com/ndpvt-web/latex-document-skill ~/.claude/skills/latex-document`
  - The `create_pdf` tool uses the latex-document skill's `compile_latex.sh` script for multi-pass LaTeX compilation with automatic engine detection (pdflatex/xelatex/lualatex)
  - Falls back to reportlab for plain text content when LaTeX is not used
