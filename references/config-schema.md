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
| `rate_limit_per_minute` | integer | `30` | Max outbound messages per minute |
| `profile_model` | string | (uses `ai_model`) | AI model for contact profile generation (defaults to ai_model) |

## Environment Variable Overrides

| Environment Variable | Config Field | Type |
|---------------------|-------------|------|
| `WHATSAPP_BRIDGE_PORT` | `bridge_port` | int |
| `WHATSAPP_QR_PORT` | `qr_server_port` | int |
| `WHATSAPP_AUTH_DIR` | `auth_dir` | string |
| `WHATSAPP_MODE` | `mode` | string |
| `WHATSAPP_LOG_LEVEL` | `log_level` | string |
| `AI_GATEWAY_URL` | `ai_gateway_url` | string |
| `AI_MODEL` | `ai_model` | string |

Environment variables always take precedence over config file values.

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
5. **Rate limiting** - max 30 messages per minute to prevent spam
