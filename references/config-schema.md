# Configuration Schema

All configuration is stored at `~/.happycapy-whatsapp/config.json`.

## Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `purpose` | string | `"personal_assistant"` | Bot purpose: `personal_assistant`, `business_support`, `team_coordination`, `monitoring_only` |
| `tone` | string | `"casual_friendly"` | Response tone: `casual_friendly`, `professional`, `concise_direct`, `warm_empathetic`, `custom` |
| `tone_custom_instructions` | string | `""` | Custom tone instructions (when tone = "custom") |
| `admin_number` | string | `""` | Admin phone number (digits only). Bypasses filters, can send `/` commands (Theorem T_ADMCMD) |
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
| `profile_model` | string | `"claude-haiku-4-5-20251001"` | AI model for contact profile generation (Haiku for speed, Theorem T_PMODEL) |

## Environment Variable Overrides

| Environment Variable | Config Field | Type |
|---------------------|-------------|------|
| `WHATSAPP_BRIDGE_PORT` | `bridge_port` | int |
| `WHATSAPP_QR_PORT` | `qr_server_port` | int |
| `WHATSAPP_AUTH_DIR` | `auth_dir` | string |
| `WHATSAPP_BRIDGE_TOKEN` | `bridge_token` | string |
| `WHATSAPP_MODE` | `mode` | string |
| `WHATSAPP_ADMIN_NUMBER` | `admin_number` | string |
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

## Admin Commands (Theorem T_ADMCMD)

When `admin_number` is set, that phone number can send slash commands via WhatsApp to control the bot at runtime. Admin always bypasses allowlist/blocklist filters. Non-slash messages from admin go through normal AI flow.

| Command | Description |
|---------|-------------|
| `/help` | List all available commands |
| `/status` | Show current mode, tone, allowlist/blocklist counts, profiles count, model |
| `/mode <value>` | Change mode: `auto_reply`, `monitor_only`, `ask_before_reply` |
| `/tone <value>` | Change tone: `casual_friendly`, `professional`, `concise_direct`, `warm_empathetic` |
| `/allow <number>` | Add phone number to allowlist |
| `/unallow <number>` | Remove phone number from allowlist |
| `/block <number>` | Add phone number to blocklist |
| `/unblock <number>` | Remove phone number from blocklist |
| `/pause` | Shortcut for `/mode monitor_only` |
| `/resume` | Shortcut for `/mode auto_reply` |
| `/contacts` | List up to 20 known contact profiles with details |

All modifying commands persist changes to `config.json` immediately via `save_config()`.

## Safety Rules

1. **Groups are NEVER auto-replied to** - only `monitor` or `ignore` options
2. **Bridge binds to 127.0.0.1** - never externally accessible
3. **Port 3001 is reserved** - cannot be used for bridge or QR server
4. **AI reasoning is stripped (T_REASONSTRIP)** - 4-layer defense: system prompt suppression, XML tag stripping, tail-anchor pattern matching, natural language line removal, plus post-filter log-based leak detection
5. **Rate limiting** - configurable via `rate_limit_per_minute` (default 30), enforced in Node.js bridge
6. **Media cleanup** - automatic removal of files older than `media_max_age_hours` on startup
7. **No CORS on QR endpoint** - QR code is a session credential; wildcard CORS would allow cross-origin theft
8. **Restricted file permissions** - config dir 0o700, config file 0o600, SQLite DB 0o600 (owner-only)
9. **Timing-safe token comparison** - bridge uses `crypto.timingSafeEqual()` to prevent timing side-channels
10. **Path traversal prevention** - media filenames sanitized to alphanumeric; send_media validates paths within media dir
11. **Log PII redaction** - message content never logged; only length indicators (e.g., "42 chars")
12. **TOCTOU-safe temp files** - `tempfile.mkstemp()` for atomic creation (not `mktemp()`)
13. **Input length capping** - incoming content capped at 10,000 chars to prevent memory DoS
14. **Prompt injection bounding** - contact profile context hard-capped at 500 chars in system prompt
15. **Error redaction** - raw API errors and stack traces never logged or sent to clients

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
| P_ADMIN | A single trusted phone number must have privileged access to modify bot behavior at runtime without UI |
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
| T_ADMCMD | Admin `/` messages handled as commands, not forwarded to AI. Admin bypasses all filters. | P_ADMIN + T6 | `_handle_admin_command()` in main.py |

### Latency Premises

| ID | Premise |
|----|---------|
| P_POOL | TCP+TLS handshake costs ~100-300ms per new HTTPS connection |
| P_PARA | Independent I/O operations can run concurrently via asyncio.gather |
| P_CACHE | In-memory dict lookup is O(1) vs SQLite SELECT + JSON parse |
| P_ODEDUP | OrderedDict.popitem(last=False) is O(1) vs sorted() O(n log n) |
| P_LAZY | Removing non-critical operations from the hot path reduces latency |
| P_HAIKU | Haiku is fastest Claude model; suitable for non-user-facing tasks |
| P_SONNET | Sonnet 4.6 balances speed and quality for user-facing responses |
| P_FIRE | Non-critical writes can be fire-and-forget to unblock the response path |

### Latency Theorems

| ID | Theorem | Derived From | Savings |
|----|---------|--------------|---------|
| T_POOL | Shared httpx.AsyncClient across orchestrator lifetime | P_POOL | ~100-300ms/msg |
| T_VPARA | Parallel video keyframe + audio extraction via asyncio.gather | P_PARA | ~200-500ms/video |
| T_ODEDUP | OrderedDict dedup with O(1) eviction | P_ODEDUP | ~1-5ms/eviction |
| T_PCACHE | In-memory contact profile cache with write-through invalidation | P_CACHE | ~2-10ms/msg |
| T_LAZY | Removed ffprobe duration call; adaptive timeout (90s vision, 60s text) | P_LAZY | ~50-200ms/video |
| T_FIRE | Fire-and-forget asyncio.create_task for contact sample storage | P_FIRE | ~5-15ms/msg |
| T_PMODEL | Haiku for profile generation (non-user-facing) | P_HAIKU | ~500-1000ms/profile |
| T_SONNET | Sonnet 4.6 default for user-facing responses | P_SONNET | Quality + speed |

### Security & Privacy Premises

| ID | Premise |
|----|---------|
| P_QRAUTH | QR code IS the WhatsApp session credential; stealing it = account hijack |
| P_FPERMS | Default umask (0644/0755) allows other users to read sensitive files |
| P_TIMING | JavaScript `===` short-circuits on first mismatch, leaking info via response timing |
| P_PATHTR | Untrusted message IDs can contain `../` or shell metacharacters |
| P_LOGPII | Log files may be world-readable or shipped to log aggregators; content = PII |
| P_TMPRACE | `tempfile.mktemp()` has TOCTOU race: file can be created by attacker between name generation and use |
| P_INPUTLEN | Unbounded input can exhaust memory or inflate LLM token costs |
| P_CORS | Wildcard CORS allows any web page to programmatically read cross-origin responses |
| P_PROMPTINJ | Contact-controlled data injected into system prompt can manipulate LLM behavior |
| P_MEDIASAN | Path traversal in `send_media()` could exfiltrate arbitrary files from the filesystem |
| P_REASONLEAK | LLMs may emit internal reasoning, thinking tags, or meta-commentary despite system prompt instructions |
| P_ALLOWLIST | In security, allowlist extraction (only permit known-good) is stronger than blocklist stripping (remove known-bad) |
| P_DYNSETUP | Users describe intent in natural language; fixed questionnaires waste time asking what's already stated |

### UX Theorems

| ID | Theorem | Derived From | Location |
|----|---------|--------------|----------|
| T_DYNSETUP | Dynamic setup wizard: open question first, parse intent, only ask follow-ups for ambiguous fields | P_DYNSETUP | SKILL.md, config_manager.py |

### Security & Privacy Theorems

| ID | Severity | Theorem | Derived From | Location |
|----|----------|---------|--------------|----------|
| T_QRPIN | CRITICAL | No CORS headers on /qr endpoint | P_QRAUTH + P_CORS | qr_server.py |
| T_FPERM | CRITICAL | Config (0o600), config dir (0o700), DB (0o600) permissions | P_FPERMS | config_manager.py, contact_store.py |
| T_TSAFE | HIGH | Timing-safe token comparison via `crypto.timingSafeEqual()` | P_TIMING | bridge/src/server.ts |
| T_PATHSAN | HIGH | Sanitize msg_id to `[a-zA-Z0-9_-]` for media filenames | P_PATHTR | whatsapp_channel.py |
| T_LOGREDACT | HIGH | Never log message content; use length indicators only | P_LOGPII | main.py, whatsapp_channel.py |
| T_TMPFILE | MEDIUM | Replace `mktemp()` with `mkstemp()` for atomic temp file creation | P_TMPRACE | media_processor.py |
| T_INPUTCAP | MEDIUM | Cap incoming content at 10,000 chars | P_INPUTLEN | whatsapp_channel.py |
| T_SENDSAN | MEDIUM | Validate send_media paths resolve within media directory | P_MEDIASAN | whatsapp_channel.py |
| T_PROFSAN | MEDIUM | Bound profile context to 500 chars; truncate all contact-controlled fields | P_PROMPTINJ | contact_store.py |
| T_ALLOWFIRST | HIGH | Allowlist-first `<reply>` tag extraction with blocklist fallback; both layers always active | P_ALLOWLIST + P_REASONLEAK | whatsapp_channel.py, config_manager.py |
| T_REASONSTRIP | HIGH | Blocklist safety net: XML strip + tail-anchor + natural language + post-filter detector | P_REASONLEAK | config_manager.py, whatsapp_channel.py, send_file.py |
| T_ERRREDACT | LOW | Strip raw API errors and stack traces from logs and client messages | P_LOGPII | main.py, media_processor.py, server.ts |

### Security Constants with Proofs

| Constant | Value | Proof |
|----------|-------|-------|
| `_MAX_CONTENT_CHARS` | 10000 | P_INPUTLEN: 10K chars ~ 3K tokens. WhatsApp rarely exceeds 4096; 10K = headroom without DoS risk. |
| `_MAX_PROFILE_CONTEXT_CHARS` | 500 | P_PROMPTINJ: 500 chars ~ 150 tokens. Enough for useful context, small enough to limit injection surface. |
| `_SAFE_FILENAME_RE` | `[^a-zA-Z0-9_-]` | P_PATHTR: Strips everything except safe filename characters from message IDs. |
| Config dir permissions | 0o700 | P_FPERMS: Owner rwx only. Contains bridge_token, allowlist, API URLs. |
| Config/DB file permissions | 0o600 | P_FPERMS: Owner rw only. DB contains full conversation history (highly sensitive PII). |

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
