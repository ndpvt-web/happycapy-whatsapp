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

## Safety Rules

1. **Groups are NEVER auto-replied to** - only `monitor` or `ignore` options
2. **Bridge binds to 127.0.0.1** - never externally accessible
3. **Port 3001 is reserved** - cannot be used for bridge or QR server
4. **AI reasoning is stripped** - internal notes never reach WhatsApp contacts
5. **Rate limiting** - max 30 messages per minute to prevent spam
