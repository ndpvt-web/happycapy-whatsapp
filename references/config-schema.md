# Configuration Schema

All configuration is stored at `~/.happycapy-whatsapp/config.json`.

## Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `purpose` | string | `"personal_assistant"` | Bot purpose: `personal_assistant`, `business_support`, `team_coordination`, `monitoring_only` |
| `tone` | string | `"casual_friendly"` | Response tone: `casual_friendly`, `professional`, `concise_direct`, `warm_empathetic`, `custom` |
| `tone_custom_instructions` | string | `""` | Custom tone instructions (when tone = "custom") |
| `mode` | string | `"auto_reply"` | Reply mode: `auto_reply`, `ask_before_reply`, `monitor_only` |
| `allowlist` | string[] | `[]` | Phone numbers that can receive replies (E.164 format, empty = everyone) |
| `blocklist` | string[] | `[]` | Phone numbers blocked from receiving replies |
| `voice_transcription` | boolean | `false` | Enable voice message transcription via Groq Whisper |
| `voice_transcription_provider` | string | `"groq"` | Transcription provider |
| `media_handling` | string | `"acknowledge"` | Media handling: `acknowledge` or `ignore` |
| `group_policy` | string | `"monitor"` | Group messages: `monitor` (log only) or `ignore` |
| `bridge_port` | integer | `3002` | Internal WebSocket bridge port (must not be 3001) |
| `qr_server_port` | integer | `8765` | HTTP QR server port (exposed externally) |
| `auth_dir` | string | `"~/.happycapy-whatsapp/whatsapp-auth"` | Baileys session storage directory |
| `log_level` | string | `"INFO"` | Log level: DEBUG, INFO, WARNING, ERROR |
| `system_prompt_override` | string | `""` | Override the auto-generated system prompt entirely |
| `ai_gateway_url` | string | `"https://ai-gateway.happycapy.ai/api/v1"` | AI Gateway base URL |
| `ai_model` | string | `"claude-sonnet-4-6"` | AI model for generating responses |
| `max_message_length` | integer | `4000` | Max chars per WhatsApp message chunk |
| `rate_limit_per_minute` | integer | `30` | Max outbound messages per minute (flows to bridge via env) |
| `media_max_age_hours` | integer | `24` | Hours to keep downloaded media before cleanup (0 = forever) |
| `bridge_token` | string | `""` | Optional token for WebSocket auth between Python and bridge |
| `whisper_api_url` | string | `"https://api.groq.com/openai/v1/audio/transcriptions"` | Voice transcription API endpoint |
| `profile_model` | string | (uses `ai_model`) | AI model for contact profile generation (defaults to ai_model) |

## Environment Variable Overrides

| Environment Variable | Config Field | Type |
|---------------------|-------------|------|
| `WHATSAPP_BRIDGE_PORT` | `bridge_port` | int |
| `WHATSAPP_QR_PORT` | `qr_server_port` | int |
| `WHATSAPP_AUTH_DIR` | `auth_dir` | string |
| `WHATSAPP_BRIDGE_TOKEN` | `bridge_token` | string |
| `WHATSAPP_MODE` | `mode` | string |
| `WHATSAPP_LOG_LEVEL` | `log_level` | string |
| `AI_GATEWAY_URL` | `ai_gateway_url` | string |
| `AI_MODEL` | `ai_model` | string |
| `WHISPER_API_URL` | `whisper_api_url` | string |

Environment variables always take precedence over config file values.

## Media Processing Constants

| Constant | Value | Location | Proof |
|----------|-------|----------|-------|
| `_PDF_MAX_PAGES` | 50 | media_processor.py | 50 pages x ~500 words = ~25K words = ~33K tokens. Fits context window. |
| `_PDF_MAX_CHARS` | 30000 | media_processor.py | ~10K tokens. Leaves room for system prompt + history + response. |
| `_VIDEO_KEYFRAME_COUNT` | 1 | media_processor.py | Single frame sufficient for scene understanding; more bloats payload. |
| `_MAX_MEDIA_SIZE_BYTES` | 20MB | media_processor.py | WhatsApp limit is 16MB; 20MB provides headroom for documents. |

## Data Files

| File | Location | Description |
|------|----------|-------------|
| `config.json` | `~/.happycapy-whatsapp/config.json` | User configuration |
| `contacts.db` | `~/.happycapy-whatsapp/contacts.db` | SQLite contact profiles and conversation samples |
| `daemon.pid` | `~/.happycapy-whatsapp/daemon.pid` | Daemon process ID file |
| `daemon.log` | `~/.happycapy-whatsapp/logs/daemon.log` | Daemon and orchestrator logs (rotated at 10MB) |
| `whatsapp-auth/` | `~/.happycapy-whatsapp/whatsapp-auth/` | Baileys session files |

## Daemon Configuration

The daemon (`src/daemon.py`) uses these hardcoded settings:

| Setting | Value | Description |
|---------|-------|-------------|
| `MAX_RESTARTS` | 50 | Max restart attempts before giving up |
| `INITIAL_BACKOFF` | 3s | Initial wait before restart |
| `MAX_BACKOFF` | 120s | Maximum wait between restarts |
| `STABILITY_THRESHOLD` | 300s | Process must run this long to reset restart counter |
| `LOG_ROTATION_SIZE` | 10MB | Rotate log when it exceeds this size |

## Contact Profile Fields

Profiles stored in `contacts.db` include:

| Field | Type | Description |
|-------|------|-------------|
| `display_name` | string | Contact's name (if detected) |
| `tone` | string | casual, formal, mixed, neutral |
| `formality` | float | 0.0 (very casual) to 1.0 (very formal) |
| `emoji_usage` | string | none, rare, moderate, frequent |
| `avg_message_length` | string | short, medium, long |
| `language` | string | Primary language code |
| `languages_used` | string[] | All language codes used |
| `relationship` | string | friend, family, colleague, acquaintance, unknown |
| `topics` | string[] | Common discussion topics (max 5) |
| `response_style` | string | How to match their communication style |
| `sample_phrases` | string[] | Characteristic phrases (max 5) |
| `summary` | string | LLM-generated summary of this contact |

## Safety Rules

1. **Groups are NEVER auto-replied to** - only `monitor` or `ignore` options
2. **Bridge binds to 127.0.0.1** - never externally accessible
3. **Port 3001 is reserved** - cannot be used for bridge or QR server
4. **AI reasoning is stripped** - internal notes never reach WhatsApp contacts
5. **Rate limiting** - configurable via `rate_limit_per_minute` (default 30), enforced in Node.js bridge
6. **Media cleanup** - automatic removal of files older than `media_max_age_hours` on startup

## Aristotelian Proofs for Constants

Every hardcoded constant derives from first-principle premises. No arbitrary "magic numbers".

### Atomic Premises

| ID | Premise |
|----|---------|
| P1 | WhatsApp Web requires QR code scanning for initial pairing |
| P2 | Baileys emits QR as string convertible to PNG |
| P3 | Session state persists in multi-file auth directory |
| P4 | Users cannot know config needs without being asked |
| P5 | Every config value must have a sensible default |
| P6 | Terminal QR is unusable in sandboxed environments |
| P7 | Setup must happen before runtime operation |
| P8 | Auto-replies to groups are dangerous without consent |
| P9 | Port 3001 is reserved in HappyCapy |
| P10 | Skills live at `~/.claude/skills/<name>/` with SKILL.md |
| P11 | `AskUserQuestion` captures user input interactively |
| P12 | `/app/export-port.sh` exposes ports externally |
| P_DEDUP | WhatsApp delivers retries on reconnect; dedup prevents double-processing |
| P_SENT | Track outbound message keys for delete/status correlation |
| P_BAN | WhatsApp bans accounts at ~200 messages/minute |
| P_DISK | Media files accumulate at rate proportional to incoming messages |

### Theorems (Design Decisions)

| ID | Theorem | Derived From | Constant |
|----|---------|--------------|----------|
| T1 | QR must be served as web page with base64 PNG | P1 + P6 + P12 | `qr_server_port: 8765` |
| T2 | QR server must auto-refresh every 2s via JavaScript | P2 + P6 | Polling interval in qr_server.py |
| T3 | Interactive setup wizard runs BEFORE bridge starts | P4 + P7 + P11 | Setup flow in main.py |
| T4 | All config stored in JSON file + env overrides, no hardcoding | P5 + P10 | config_manager.py ENV_OVERRIDES |
| T5 | Bridge on internal port, QR server on exposed port | P9 + P12 | `bridge_port: 3002` |
| T6 | Groups NEVER auto-replied to | P8 | `group_policy: monitor` |
| T7 | Config file persists so setup wizard only runs once | P3 + P5 | `config_exists()` check |

### Constants with Proofs

| Constant | Value | Proof |
|----------|-------|-------|
| `_DEDUP_MAX` | 1000 | P_DEDUP: 1000 IDs x ~50B = ~50KB. At 30 msg/min, covers ~33 min of history. |
| `_DEDUP_EVICT_BATCH` | 100 | Evict oldest 10% when full = amortized O(1) per insert. |
| `_SENT_KEYS_MAX` | 500 | P_SENT: ~16 min at max send rate; outbound needs less history than inbound. |
| `rate_limit_per_minute` | 30 | P_BAN: 30 = 15% of WhatsApp ban threshold (200/min). Safe margin. |
| `media_max_age_hours` | 24 | P_DISK: 24h keeps recent context while bounding disk to ~1 day of media. |
| `max_message_length` | 4000 | WhatsApp renders poorly above ~4000 chars. Split at line/word boundaries. |
| `MAX_RESTARTS` (daemon) | 50 | Exponential backoff: 50 restarts x avg 60s = ~50min before giving up. Covers transient network outages. |
| `STABILITY_THRESHOLD` (daemon) | 300s | 5 min of stable run = process is healthy, reset restart counter. |
| `MIN_SAMPLES_FOR_PROFILE` | 5 | Minimum messages for statistically meaningful style analysis. |
| `PROFILE_UPDATE_INTERVAL` | 20 | 20 new messages = enough new data to justify re-analysis cost. |
