<p align="center">
  <img src="https://raw.githubusercontent.com/ndpvt-web/happycapy-whatsapp/main/assets/logo.png" alt="HappyCapy WhatsApp" width="120" />
</p>

<h1 align="center">HappyCapy WhatsApp</h1>

<p align="center">
  <strong>AI-powered WhatsApp automation with identity impersonation, adaptive memory, and enterprise-grade intelligence</strong>
</p>

<p align="center">
  <a href="#features">Features</a> &bull;
  <a href="#architecture">Architecture</a> &bull;
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#setup-wizard">Setup Wizard</a> &bull;
  <a href="#admin-commands">Admin Commands</a> &bull;
  <a href="#security">Security</a> &bull;
  <a href="#configuration">Configuration</a>
</p>

---

## Overview

HappyCapy WhatsApp is a full-featured WhatsApp automation skill that connects to your personal or business WhatsApp account and operates as an intelligent AI agent. It can **impersonate you** (contacts never know it's AI), act as a transparent AI assistant, or silently monitor messages -- all configurable through an interactive setup wizard or 30+ admin commands sent directly via WhatsApp.

The system is built on a modular intelligence layer with 20+ specialized modules covering everything from per-contact personality adaptation to knowledge graph-based context retrieval, fabrication detection, prompt injection defense, and self-learning reflection.

### Key Differentiators

- **Identity impersonation**: The bot acts AS you, matching your texting style per contact. It never reveals it's AI, even when directly asked.
- **Per-contact memory isolation**: Each contact has their own memory silo. Contact A's conversations are never leaked to Contact B.
- **Self-learning reflection**: The bot learns from its own mistakes and your corrections over time, building a lessons-learned database.
- **12-layer context assembly**: System prompts are assembled from security anchors, identity files, config, memory, profiles, RAG, integrations, and learned lessons.
- **Multimodal understanding**: Images, PDFs, voice messages, videos, and stickers are all understood and processed automatically.
- **Tool calling**: Generate images, videos, PDFs (via LaTeX compilation), send messages to other contacts, search the web, and escalate questions to the owner -- all from within a WhatsApp conversation.

---

## Features

### Core Messaging

| Feature | Description |
|---------|-------------|
| **Auto-reply** | AI responds to messages automatically using LLM (GPT-4.1-mini via AI Gateway) |
| **Three modes** | `auto_reply` (fully automatic), `ask_before_reply` (human approval), `monitor_only` (silent logging) |
| **Four tone presets** | Casual & Friendly, Professional, Concise & Direct, Warm & Empathetic |
| **Two personality modes** | `impersonate` (act as owner, deny being AI) or `assistant` (transparent AI helper) |
| **Reasoning suppression** | AI thinking/reasoning is stripped before sending -- contacts only see the final response |
| **Rate limiting** | Configurable messages-per-minute cap to prevent spam |
| **Message length cap** | Configurable max message length (default 4000 chars) |

### Intelligence Layer

The bot includes a nanobot-inspired intelligence layer with 15+ specialized modules:

#### Message Scoring & Routing

- **Importance Scorer** (`importance_scorer.py`): Every message gets a 1-10 importance score based on urgency keywords, contact familiarity, repetition patterns, question marks, caps lock, and exclamation marks. Group messages scored on a 0-100 scale.
- **Message Queue** (`message_queue.py`): All inbound messages are queued with their importance scores. Supports priority ordering, reply tracking, and bulk cleanup.
- **Escalation Engine** (`escalation_engine.py`): When the AI cannot answer or a message scores above the threshold, it escalates to the admin with a unique code (e.g., `ESC-001`). Admin responds via `/respond ESC-001 <answer>` and the reply is routed back to the original contact.
- **Auto-Reply Templates** (`auto_reply_templates.py`): Built-in and custom quick-reply templates for busy/DND status (e.g., "I'm busy right now, I'll get back to you").

#### Memory & Context

- **Two-Layer Memory** (`memory_store.py`): Per-contact `MEMORY.md` (long-term facts) and `HISTORY.md` (timestamped event log). LLM consolidation runs periodically to summarize conversations into structured memory.
- **Memory Isolation**: Each contact's memory lives in an isolated directory (`memory/contacts/{hash}/`). Contact A's memory is never visible to Contact B.
- **Memory Search** (`MemorySearch`): Keyword + date-range + topic search over history with fuzzy matching and recency scoring.
- **Knowledge Graph** (`knowledge_graph.py`): LightRAG-inspired graph that extracts entities (people, places, topics) and relationships from conversations via LLM. Context is retrieved via entity subgraph traversal instead of keyword search.
- **Context Builder** (`context_builder.py`): Assembles the system prompt from 12 layers:

```
Layer  1: Security Anchor (immutable identity + anti-injection)
Layer  2: SOUL.md (bot personality - editable)
Layer  3: USER.md (owner profile - editable)
Layer  4: Config instructions (purpose, tone, mode)
Layer  5: Privacy rules (strict/moderate/open)
Layer  6: Anti-fabrication rules (strict/deflect/relaxed)
Layer  7: Per-contact MEMORY.md (long-term facts)
Layer  8: Per-contact HISTORY.md (recent events)
Layer  9: Contact profile (communication style)
Layer 10: RAG context (relevant past conversations)
Layer 11: Integration instructions (spreadsheet, email)
Layer 12: Reasoning suppression (reply tag enforcement)
+ Bonus: Learned lessons from reflection engine
+ Bonus: Escalation context for admin replies
```

#### Contact Intelligence

- **Contact Profiles** (`contact_store.py`): After 5 messages, the bot auto-generates a profile for each contact via LLM analysis: tone, formality, emoji usage, language, relationship type, topics, interaction frequency, and sample phrases. Re-analyzed every 10 new messages.
- **Session Manager** (`session_manager.py`): Tracks per-contact conversation sessions with timeout-based freshness. When a contact resumes after 30+ minutes of inactivity, context signals help the AI decide whether to continue or start fresh.

#### Self-Learning & Reflection

- **Reflection Engine** (`reflection_engine.py`): Three learning signals:
  1. **Owner corrections**: When you manually type in a contact's chat (overriding the bot), it's recorded as a correction lesson.
  2. **Escalation feedback**: Answers to `/respond` commands are stored for future reuse on similar questions.
  3. **Self-reflection**: Periodic LLM-powered analysis of recent interactions identifies mistakes, tone mismatches, fabrication, and areas for improvement.
- Lessons are stored in SQLite with relevance scoring and automatic expiry (90 days).
- Active lessons are injected into the system prompt so the AI applies them.

#### Security Guards

- **Content Filter** (`content_filter.py`): Scans ALL outbound messages for API keys, bearer tokens, private keys, AWS access keys, OpenAI/Anthropic keys, GitHub tokens, Slack tokens, system prompt markers, internal file paths, and credit card numbers. Blocks before sending.
- **Fabrication Guard** (`fabrication_guard.py`): Detects when the AI fabricates personal claims (location, activity, companions, availability, emotional state). Vague deflections are allowed; specific fabrications are blocked and replaced with safe responses.
- **Semantic Guard** (`semantic_guard.py`): LLM-as-judge prompt injection defense. Classifies inbound messages for 8 attack categories: identity override, instruction override, prompt extraction, privilege escalation, delimiter framing, indirect injection, data exfiltration, and safety bypass.
- **Privacy Levels**: Three configurable levels (`strict`, `moderate`, `open`) controlling cross-contact information sharing.
- **Fabrication Policy**: Three policies (`strict`, `deflect`, `relaxed`) controlling what happens when the AI doesn't know something.

### Media Intelligence

#### Inbound Understanding (automatic)

| Media Type | Processing |
|-----------|-----------|
| **Images** | Sent to AI via multimodal vision API -- the bot can see and describe images |
| **PDFs** | Text extracted via pdfplumber and included in AI context |
| **Voice messages** | Transcribed to text via Whisper API (Groq) |
| **Videos** | Keyframe extracted for vision + audio extracted for transcription |
| **Stickers** | Analyzed via vision API like images |
| **Documents** | PDF text extraction; other formats acknowledged with metadata |

#### Outbound Generation (via tool calling)

| Tool | Description |
|------|-------------|
| `generate_image` | AI image generation from text prompts |
| `generate_video` | AI video generation (6-10 seconds, configurable aspect ratio) |
| `create_pdf` | Professional PDF documents compiled from LaTeX source via the [latex-document skill](https://github.com/ndpvt-web/latex-document-skill) with multi-pass compilation and automatic engine detection |
| `send_message` | Send text, images, PDFs, or videos to any contact by phone number |
| `ask_owner` | Escalate questions to the admin when the bot doesn't know the answer |
| `web_search` | Search the web for current information |

### Pluggable Integrations

The integration framework (`src/integrations/`) supports plug-and-play modules:

| Integration | Features |
|------------|----------|
| **Spreadsheet** (`spreadsheet.py`) | Log orders, expenses, and data to Excel spreadsheets. Tools: `log_to_spreadsheet`, `read_spreadsheet`, `search_spreadsheet` |
| **Email** (`email.py`) | Send emails via the bot. Tools: `send_email` |

Adding a new integration requires a single Python file with a class extending `BaseIntegration`.

### Scheduling & Automation

- **Cron Service** (`cron_service.py`): SQLite-backed scheduler with asyncio timer execution.
  - One-shot reminders: `/remind 30 Call back John` (fires in 30 minutes)
  - Recurring tasks: `/cron every 60 Check inventory` (every 60 minutes)
  - Job management: `/cron list`, `/cron del <id>`
- **Heartbeat Service** (`heartbeat_service.py`): Periodic maintenance tasks running every 30 minutes:
  - Message queue cleanup
  - Audit log pruning
  - Escalation expiry
  - Conversation sample pruning
  - Knowledge graph extraction

### Group Monitoring

- **Monitor mode**: Groups are never auto-replied to (safety). All group messages are logged and scored.
- **Group Search**: FTS5-powered full-text search over group message history (`/groupsearch <query>`).
- **Group scoring**: 0-100 scale with factors for @mentions, keyword matches, urgent words, and question patterns.
- **Keyword alerts**: Configure keywords that trigger admin notifications for group messages.

### Quiet Hours

- **Timezone-aware**: Configure start/end times with timezone (e.g., `Asia/Hong_Kong`).
- **Alert queuing**: Non-urgent notifications are queued during quiet hours and flushed as a digest when hours end.
- **Override threshold**: Messages scoring above the threshold (default 9/10) bypass quiet hours.
- **Admin commands**: `/quiet on|off`, `/quiet set 23:00 07:00 Asia/Hong_Kong`.

### Health Monitoring

- **System health**: `/health` shows uptime, memory usage, messages processed, messages/minute, active chats, connection status.
- **Audit log** (`audit_log.py`): Every message (in/out), admin command, security event, and error is logged with timestamps in SQLite. View via `/audit`.

---

## Architecture

```
                                 +-------------------+
                                 |   WhatsApp Cloud  |
                                 +--------+----------+
                                          |
                                          | E2E Encrypted
                                          |
+------------------+    WebSocket    +----+-----+
|  QR Auth Server  |<-- - - - - - ->| Baileys  |
|  (Python :8765)  |   QR events    | Bridge   |
|  Auto-refresh    |                | (Node.js)|
|  web page        |                | Port 3002|
+------------------+                +----+-----+
                                         |
                                    WebSocket
                                         |
                              +----------+-----------+
                              |   WhatsApp Channel   |
                              |   (whatsapp_channel)  |
                              | - Deduplication      |
                              | - fromMe filtering   |
                              | - Contact filtering  |
                              | - Rate limiting      |
                              +----------+-----------+
                                         |
                              +----------+-----------+
                              |   Main Orchestrator  |
                              |      (main.py)       |
                              +----------+-----------+
                                         |
            +----------------------------+----------------------------+
            |              |             |             |              |
    +-------+------+ +----+----+ +------+------+ +----+-----+ +-----+------+
    | Intelligence | | Memory  | | Media       | | Tool     | | Security   |
    | Layer        | | System  | | Processor   | | Executor | | Guards     |
    +-------+------+ +----+----+ +------+------+ +----+-----+ +-----+------+
    |               |            |              |              |
    |- Scorer       |- Memory    |- Vision API  |- Image gen   |- Content
    |- Queue        |  Store     |- PDF extract |- Video gen   |  filter
    |- Escalation   |- Knowledge |- Whisper     |- PDF/LaTeX   |- Fabrication
    |- Templates    |  Graph     |  transcribe  |- send_message|  guard
    |- Contact      |- Context   |- Video key-  |- ask_owner   |- Semantic
    |  profiles     |  Builder   |  frame       |- web_search  |  guard
    |- Session mgr  |- Reflection|  extraction  |- Integrations|- Privacy
    |- Cron service |  Engine    |              |              |  levels
    |- Health       |            |              |              |
    |- Quiet hours  |            |              |              |
    +---------------+            +--------------+--------------+
```

### Data Flow

```
Inbound message
    |
    v
[Deduplication] -> [Contact Filter] -> [fromMe Filter]
    |
    v
[Admin Command?] --yes--> [Command Handler] -> [Response]
    |no
    v
[Owner Correction?] --yes--> [Record in Reflection DB] -> [Stop]
    |no
    v
[Score Message] -> [Queue] -> [Audit Log]
    |
    v
[Status Check] -> [Busy/DND?] --yes--> [Auto-reply Template]
    |no
    v
[Escalation Check] -> [Score >= Threshold?] --yes--> [Alert Admin]
    |
    v
[Process Media] -> [Vision/PDF/Audio/Video]
    |
    v
[Build System Prompt] (12 layers + lessons + escalation context)
    |
    v
[Generate AI Response] -> [Tool Calls?] --yes--> [Execute Tools] -> [Final Response]
    |
    v
[Content Filter] -> [Fabrication Guard] -> [Send to Contact]
    |
    v
[Store Sample] -> [Update Session] -> [Check Profile Update]
    |
    v
[Memory Consolidation Trigger?] --yes--> [Consolidate + Self-Reflect]
```

### Technology Stack

| Component | Technology |
|-----------|-----------|
| Bridge | Node.js + Baileys (WhatsApp Web API) |
| Orchestrator | Python 3.11+ asyncio |
| LLM | GPT-4.1-mini via AI Gateway (configurable) |
| Database | SQLite (contacts, audit, escalations, sessions, cron, knowledge graph, reflection) |
| Media | ffmpeg, pdfplumber, Whisper API (Groq), vision API |
| PDF | LaTeX compilation via [latex-document skill](https://github.com/ndpvt-web/latex-document-skill) |
| QR Server | Python HTTP server with auto-refresh |
| Daemon | Custom Python process supervisor with auto-restart |

---

## Quick Start

### Prerequisites

- Node.js 20+
- Python 3.11+
- `AI_GATEWAY_API_KEY` environment variable
- ffmpeg (for video processing)

### Installation

```bash
# Clone the repository
git clone https://github.com/ndpvt-web/happycapy-whatsapp.git ~/.claude/skills/happycapy-whatsapp

# Run setup (installs Python + Node.js dependencies, compiles bridge)
bash ~/.claude/skills/happycapy-whatsapp/scripts/setup.sh

# Install the latex-document skill for PDF generation (optional but recommended)
git clone https://github.com/ndpvt-web/latex-document-skill ~/.claude/skills/latex-document
```

### Starting the Bot

```bash
# Start as a 24/7 daemon (recommended -- auto-restarts on crash)
bash ~/.claude/skills/happycapy-whatsapp/scripts/start.sh daemon

# Or start in foreground (for debugging)
bash ~/.claude/skills/happycapy-whatsapp/scripts/start.sh foreground

# Check status
bash ~/.claude/skills/happycapy-whatsapp/scripts/start.sh status

# View logs
tail -f ~/.happycapy-whatsapp/logs/daemon.log

# Restart
bash ~/.claude/skills/happycapy-whatsapp/scripts/start.sh restart

# Stop
bash ~/.claude/skills/happycapy-whatsapp/scripts/start.sh stop
```

### Connecting WhatsApp

1. Start the bot (see above)
2. Open the QR authentication page in your browser (URL shown in logs, typically `http://localhost:8765`)
3. Open WhatsApp on your phone: **Settings > Linked Devices > Link a Device**
4. Scan the QR code displayed on the web page
5. Wait for "WhatsApp connected!" in the logs

The QR page auto-refreshes every 2 seconds and displays a "Connected" status once linked.

---

## Setup Wizard

On first run, the bot launches an interactive setup wizard that configures everything through a natural conversation. The wizard uses intent inference to minimize questions:

### Phase 1: Intent Gathering

You describe your use case in natural language, or pick a preset:

- **Personal assistant** -- Bot replies as you, contacts never know it's AI
- **Business automation** -- Handle orders, invoices, customer messages
- **AI assistant bot** -- Transparent AI helper
- **Monitor messages** -- Silent observation only

### Phase 2: Smart Inference

The wizard extracts config values from your description:

| You say... | Bot infers... |
|-----------|--------------|
| "monitor my business WhatsApp" | mode: monitor_only, purpose: monitoring_only |
| "casual tone, reply to everyone" | tone: casual_friendly, allowlist: [] |
| "+852 92893658" | admin_number: 85292893658 |
| "professional support bot" | tone: professional, purpose: business_support |

### Phase 3: Verify Only What's Missing

The wizard only asks follow-up questions for fields it couldn't infer. Most users are fully set up in 1-2 questions.

### Phase 4: Smart Defaults

All unresolved optional fields get safe defaults:

| Field | Default | Rationale |
|-------|---------|-----------|
| privacy_level | strict | Never leaks info between contacts |
| fabrication_policy | strict (impersonate) / deflect (assistant) | Safety first |
| voice_transcription | true | Most users want this |
| group_policy | monitor | Never auto-reply in groups |
| tool_calling_enabled | true | Image/video/PDF generation |
| escalation_enabled | true | Smart alerts for important messages |

---

## Admin Commands

Control the bot entirely via WhatsApp by sending slash commands from the admin number:

### Bot Control

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/status` | Bot status (mode, tone, contacts, uptime) |
| `/mode <mode>` | Change mode: `auto_reply`, `monitor_only`, `ask_before_reply` |
| `/tone <tone>` | Change tone: `casual_friendly`, `professional`, `concise_direct`, `warm_empathetic` |
| `/pause` | Quick switch to monitor_only |
| `/resume` | Quick switch to auto_reply |
| `/busy` | Set status to busy (auto-reply with templates) |
| `/dnd` | Set status to Do Not Disturb |
| `/available` | Clear status override |

### Contact Management

| Command | Description |
|---------|-------------|
| `/allow <number>` | Add phone number to allowlist |
| `/unallow <number>` | Remove from allowlist |
| `/block <number>` | Add to blocklist |
| `/unblock <number>` | Remove from blocklist |
| `/contacts` | List all known contacts with profiles |
| `/profile <number>` | View detailed contact card |

### Message Routing

| Command | Description |
|---------|-------------|
| `/queue` | Show message queue stats |
| `/escalate` | Show pending escalations |
| `/respond ESC-XXX <answer>` | Reply to an escalation (routed back to contact) |
| `/delete <msg_id>` | Delete a sent message |

### Memory & Knowledge

| Command | Description |
|---------|-------------|
| `/memory` | View MEMORY.md (long-term facts) |
| `/memory history` | View recent event log |
| `/memory consolidate` | Force memory consolidation now |
| `/memorysearch <query>` | Search memory history |
| `/kg` | Knowledge graph stats |
| `/kg search <query>` | Search the knowledge graph |
| `/kg extract` | Force knowledge extraction |

### Scheduling

| Command | Description |
|---------|-------------|
| `/remind <minutes> <message>` | Set a one-shot reminder |
| `/cron list` | Show scheduled jobs |
| `/cron del <id>` | Delete a scheduled job |
| `/cron every <minutes> <message>` | Add a recurring job |

### Groups

| Command | Description |
|---------|-------------|
| `/groups` | List monitored groups |
| `/groupsearch <query>` | Search group message history |
| `/grouprecent [group]` | View recent group messages |
| `/greply <group> <msg>` | Send a message to a group |

### Quiet Hours

| Command | Description |
|---------|-------------|
| `/quiet` | Show quiet hours status |
| `/quiet on\|off` | Enable/disable quiet hours |
| `/quiet set <start> <end> <tz>` | Configure (e.g., `/quiet set 23:00 07:00 Asia/Hong_Kong`) |

### Identity & Learning

| Command | Description |
|---------|-------------|
| `/identity` | View/manage bot identity files (SOUL.md, USER.md) |
| `/reflect` | View reflection engine stats (lessons learned) |
| `/reflect run` | Force a self-reflection analysis |

### System

| Command | Description |
|---------|-------------|
| `/health` | System health (uptime, memory, message rates, connection status) |
| `/heartbeat` | Force maintenance tick now |
| `/audit` | Show recent audit events |
| `/session` | Session stats |
| `/session reset` | Reset session tracking |
| `/historysync` | History sync stats |
| `/tools` | Tool calling status |
| `/tools on\|off` | Enable/disable tool calling |
| `/template list\|add\|del` | Manage reply templates |

---

## Security

### Defense in Depth

The bot implements multiple overlapping security layers:

```
Layer 0: Security Anchor (immutable system prompt block)
Layer 1: Content Filter (outbound credential/token scanning)
Layer 2: Fabrication Guard (blocks fabricated personal claims)
Layer 3: Semantic Guard (LLM-as-judge prompt injection detection)
Layer 4: Privacy Isolation (per-contact memory silos)
Layer 5: Reasoning Suppression (strips AI thinking from responses)
Layer 6: Bridge Security (localhost-only binding, token auth)
Layer 7: File Permissions (0o600 config, 0o700 directories)
```

### Outbound Content Filter

Every message is scanned before sending for:
- API keys (OpenAI, Anthropic, AWS, GitHub, Slack)
- Bearer tokens and private keys
- System prompt markers and internal paths
- Credit card numbers (Luhn-validated)
- Unicode bypass attempts (NFKD normalization, zero-width character stripping)

### Prompt Injection Defense

- **Security Anchor**: Immutable identity block at the top of every system prompt. Cannot be overridden by user messages, tool outputs, or web content.
- **Semantic Guard**: LLM classifier that detects 8 categories of prompt injection attacks before the message reaches the main AI.
- **Profile Sanitization**: Contact profiles injected into prompts are truncated to 500 characters to limit injection surface from manipulated conversation data.

### Privacy

- Per-contact memory isolation (hashed directories)
- Three privacy levels (strict/moderate/open)
- No cross-contact information leakage in strict mode
- `ask_owner` tool for sensitive information decisions
- Config file permissions restricted to owner-only (0o600)
- Bridge binds to 127.0.0.1 only (not externally accessible)

---

## Configuration

All configuration is stored at `~/.happycapy-whatsapp/config.json`. Every field has a sensible default and can be overridden via environment variables.

### Core Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `purpose` | string | `personal_assistant` | Bot purpose: `personal_assistant`, `business_support`, `team_coordination`, `monitoring_only` |
| `tone` | string | `casual_friendly` | Response tone: `casual_friendly`, `professional`, `concise_direct`, `warm_empathetic`, `custom` |
| `mode` | string | `auto_reply` | Operating mode: `auto_reply`, `ask_before_reply`, `monitor_only` |
| `admin_number` | string | `""` | Admin phone number (digits only) for remote control |
| `personality_mode` | string | `impersonate` | `impersonate` (act as owner) or `assistant` (transparent AI) |
| `owner_name` | string | `""` | Owner's name for natural responses |

### Contact Filtering

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `allowlist` | array | `[]` | Phone numbers to respond to (empty = everyone) |
| `blocklist` | array | `[]` | Phone numbers to ignore |

### Privacy & Safety

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `privacy_level` | string | `strict` | `strict`, `moderate`, or `open` |
| `fabrication_policy` | string | `strict` | `strict` (ask owner), `deflect`, `relaxed` |

### Media & Voice

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `voice_transcription` | boolean | `false` | Enable voice-to-text via Whisper |
| `voice_transcription_provider` | string | `groq` | Transcription provider |
| `media_handling` | string | `acknowledge` | How to handle media types |

### Intelligence

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `escalation_enabled` | boolean | `true` | Enable the escalation engine |
| `importance_threshold` | integer | `7` | Score >= this triggers admin alert (1-10) |
| `alert_on_auto_reply` | boolean | `false` | Alert admin on every auto-reply |
| `tool_calling_enabled` | boolean | `true` | Enable LLM tool calling (image/video/PDF gen) |
| `group_policy` | string | `monitor` | `monitor` (log only) or `ignore` |
| `group_keywords` | array | `[]` | Keywords that trigger group alerts |

### Quiet Hours

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `quiet_hours_enabled` | boolean | `false` | Enable quiet hours |
| `quiet_hours_start` | string | `23:00` | Start time (HH:MM) |
| `quiet_hours_end` | string | `07:00` | End time (HH:MM) |
| `quiet_hours_timezone` | string | `UTC` | Timezone (e.g., `Asia/Hong_Kong`) |
| `quiet_hours_override_threshold` | integer | `9` | Score >= this bypasses quiet hours |

### Integrations

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled_integrations` | array | `["core"]` | Active integrations: `core`, `spreadsheet`, `email` |

### Technical

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ai_gateway_url` | string | AI Gateway URL | LLM API endpoint |
| `ai_model` | string | `gpt-4.1-mini` | LLM model for responses |
| `profile_model` | string | `gpt-4.1-mini` | LLM model for profile/memory tasks |
| `bridge_port` | integer | `3002` | Baileys bridge WebSocket port |
| `qr_server_port` | integer | `8765` | QR authentication server port |
| `max_message_length` | integer | `4000` | Max outbound message length |
| `rate_limit_per_minute` | integer | `30` | Rate limit cap |
| `media_max_age_hours` | integer | `24` | Auto-cleanup media files after this |

### Environment Variable Overrides

| Environment Variable | Config Field |
|---------------------|-------------|
| `WHATSAPP_BRIDGE_PORT` | `bridge_port` |
| `WHATSAPP_QR_PORT` | `qr_server_port` |
| `WHATSAPP_AUTH_DIR` | `auth_dir` |
| `WHATSAPP_BRIDGE_TOKEN` | `bridge_token` |
| `WHATSAPP_MODE` | `mode` |
| `WHATSAPP_ADMIN_NUMBER` | `admin_number` |
| `AI_GATEWAY_URL` | `ai_gateway_url` |
| `AI_MODEL` | `ai_model` |

---

## Identity Files

The bot's personality is defined by two editable Markdown files in `~/.happycapy-whatsapp/identity/`:

### SOUL.md

Defines the bot's core personality, communication rules, and behavioral constraints. Two templates are provided:

- **Impersonate mode**: "You ARE the owner of this phone. Every message you send goes directly to a real contact as if the owner typed it. NEVER reveal you are AI."
- **Assistant mode**: "I am a personal AI assistant on WhatsApp, helping manage messages for the phone owner."

### USER.md

Contains the owner's profile information: name, timezone, language, preferences, and custom instructions. Edit this to give the bot context about who you are.

Both files are loaded fresh on every prompt build, so changes take effect without restart.

---

## Daemon Mode

The daemon provides 24/7 operation with process supervision:

| Feature | Detail |
|---------|--------|
| Auto-restart | Exponential backoff: 3s to 120s between restarts |
| PID tracking | `~/.happycapy-whatsapp/daemon.pid` |
| Log rotation | At 10MB with one backup file |
| Graceful shutdown | Via SIGTERM signal |
| Stability detection | Backoff resets after 5 minutes of stable operation |
| Max restarts | 50 attempts before giving up |

Logs are written to `~/.happycapy-whatsapp/logs/daemon.log`.

---

## File Structure

```
~/.claude/skills/happycapy-whatsapp/
|-- README.md                    # This file
|-- SKILL.md                     # Claude Code skill definition
|-- bridge/                      # Node.js WhatsApp bridge (Baileys)
|   |-- src/                     # TypeScript source
|   |-- dist/                    # Compiled JavaScript
|   `-- package.json
|-- scripts/
|   |-- setup.sh                 # First-time installation
|   |-- start.sh                 # Daemon management
|   `-- compile_pdf.sh           # LaTeX compilation helper
|-- src/
|   |-- main.py                  # Main orchestrator (2,339 lines)
|   |-- whatsapp_channel.py      # Bridge communication layer
|   |-- config_manager.py        # Config loading, saving, validation
|   |-- context_builder.py       # 12-layer system prompt assembly
|   |-- contact_store.py         # Per-contact profile system
|   |-- memory_store.py          # Two-layer memory with isolation
|   |-- knowledge_graph.py       # LightRAG-inspired entity graph
|   |-- reflection_engine.py     # Self-learning from mistakes
|   |-- tool_executor.py         # LLM tool calling (image/video/PDF)
|   |-- media_processor.py       # Multimodal media understanding
|   |-- importance_scorer.py     # Message importance scoring
|   |-- escalation_engine.py     # Owner escalation with routing
|   |-- message_queue.py         # Priority message queue
|   |-- session_manager.py       # Conversation session tracking
|   |-- cron_service.py          # Scheduling and reminders
|   |-- health_monitor.py        # System health metrics
|   |-- heartbeat_service.py     # Periodic maintenance tasks
|   |-- quiet_hours.py           # Timezone-aware quiet hours
|   |-- audit_log.py             # Full audit trail
|   |-- auto_reply_templates.py  # Quick reply templates
|   |-- content_filter.py        # Outbound credential scanner
|   |-- fabrication_guard.py     # Fabricated claim detector
|   |-- semantic_guard.py        # Prompt injection classifier
|   |-- qr_server.py             # QR code authentication page
|   |-- bridge_manager.py        # Bridge process management
|   |-- daemon.py                # Process supervisor
|   |-- send_file.py             # CLI utility for sending files
|   `-- integrations/
|       |-- __init__.py           # Integration registry + loader
|       |-- base.py               # Abstract base class
|       |-- spreadsheet.py        # Excel/spreadsheet tracking
|       `-- email.py              # Email sending integration

~/.happycapy-whatsapp/           # Runtime data directory
|-- config.json                  # Bot configuration
|-- contacts.db                  # SQLite: profiles, samples, KG, audit
|-- reflection.db                # SQLite: lessons learned
|-- whatsapp-auth/               # WhatsApp session credentials
|-- identity/
|   |-- SOUL.md                  # Bot personality
|   `-- USER.md                  # Owner profile
|-- memory/
|   |-- MEMORY.md                # Global memory (legacy)
|   |-- HISTORY.md               # Global history (legacy)
|   `-- contacts/                # Per-contact isolated memory
|       |-- {hash}/MEMORY.md
|       `-- {hash}/HISTORY.md
|-- media/                       # Generated and received media files
|-- logs/
|   `-- daemon.log               # Process logs with rotation
`-- daemon.pid                   # Daemon PID file
```

---

## Sending Files Programmatically

Use the CLI utility to send files to any WhatsApp contact:

```bash
cd ~/.claude/skills/happycapy-whatsapp

# Send a file (image, PDF, video, audio, document)
python -m src.send_file --to 1234567890 --file /path/to/file.pdf

# Send with caption
python -m src.send_file --to 1234567890 --file photo.jpg --caption "Here you go"

# Send text only
python -m src.send_file --to 1234567890 --text "Hello from the agent!"
```

The `--to` parameter accepts phone numbers (digits only) or full JIDs (`number@s.whatsapp.net`).

---

## Codebase Stats

| Metric | Value |
|--------|-------|
| Python source files | 32 |
| Total Python lines | 11,072 |
| Main orchestrator | 2,339 lines |
| Intelligence modules | 15+ |
| Admin commands | 30+ |
| LLM tools | 6 |
| Security layers | 7 |
| Context prompt layers | 12+ |
| SQLite databases | 2 |

---

## Dependencies

### Python

- `httpx` -- Async HTTP client for API calls
- `pdfplumber` -- PDF text extraction
- `qrcode` -- QR code generation
- `Pillow` -- Image processing
- `reportlab` -- Fallback PDF generation

### Node.js

- `@whiskeysockets/baileys` -- WhatsApp Web API
- `ws` -- WebSocket server
- `qrcode-terminal` -- Terminal QR display (development)

### System

- `ffmpeg` -- Video/audio processing
- `pdflatex` / `xelatex` / `lualatex` -- LaTeX compilation (via latex-document skill)

---

## License

This project is proprietary software. All rights reserved.

---

<p align="center">
  Built with the <a href="https://github.com/ndpvt-web/happycapy-whatsapp">HappyCapy</a> platform
</p>
