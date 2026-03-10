"""Configuration manager for HappyCapy WhatsApp skill.

Handles loading, saving, validation, and defaults for all configuration.
Zero hardcoded values - everything configurable via JSON + env overrides.

Security:
- Theorem T_FPERM: Config dir (0o700) and config file (0o600) have restricted permissions.
  Config contains bridge_token, allowlist/blocklist, API URLs - sensitive data (P_FPERMS).
"""

import json
import os
from pathlib import Path
from typing import Any

# Default configuration - every field has a sensible default (Premise P5)
DEFAULT_CONFIG: dict[str, Any] = {
    "purpose": "personal_assistant",
    "tone": "casual_friendly",
    "tone_custom_instructions": "",
    "mode": "auto_reply",
    "admin_number": "",
    "allowlist": [],
    "blocklist": [],
    "voice_transcription": False,
    "voice_transcription_provider": "groq",
    "media_handling": "acknowledge",
    "group_policy": "monitor",
    "bridge_port": 3002,
    "qr_server_port": 8765,
    "auth_dir": str(Path.home() / ".happycapy-whatsapp" / "whatsapp-auth"),
    "log_level": "INFO",
    "system_prompt_override": "",
    "bridge_token": "",
    "ai_gateway_url": "https://ai-gateway.happycapy.ai/api/v1/openai/v1",
    "ai_model": "gpt-4.1-mini",
    "max_message_length": 4000,
    "rate_limit_per_minute": 30,
    "media_max_age_hours": 24,
    "whisper_api_url": "https://api.groq.com/openai/v1/audio/transcriptions",
    "profile_model": "gpt-4.1-mini",
    # Intelligence layer fields (nanobot-inspired)
    "status_override": "",           # available/busy/dnd/away (empty = auto)
    "auto_reply_when_busy": True,    # Use templates when busy/dnd/away
    "escalation_enabled": True,      # Enable escalation engine for high-importance messages
    "importance_threshold": 7,       # Score >= this triggers admin notification
    "group_keywords": [],            # Keywords that boost group message importance
    # Quiet hours settings (nanobot-inspired)
    "quiet_hours_enabled": False,          # Enable/disable quiet hours
    "quiet_hours_start": "23:00",          # Start time (HH:MM)
    "quiet_hours_end": "07:00",            # End time (HH:MM)
    "quiet_hours_timezone": "UTC",         # Timezone (e.g. Asia/Hong_Kong)
    "quiet_hours_override_threshold": 9,   # Score >= this bypasses quiet hours
    # Tool calling (LLM function calling for image gen, video gen, PDF creation)
    "tool_calling_enabled": True,
    # Personality mode: "impersonate" (act as owner) or "assistant" (act as AI assistant)
    "personality_mode": "impersonate",
    # Owner's name (used in impersonation mode for natural responses)
    "owner_name": "",
    # Alert on every message in auto_reply mode? (False in impersonate mode - bot handles it)
    "alert_on_auto_reply": False,
    # Privacy level: "strict" (never share cross-contact), "moderate", "open"
    "privacy_level": "strict",
    # Fabrication policy: "strict" (always ask owner), "deflect", "relaxed"
    "fabrication_policy": "strict",
}

# Environment variable overrides (Theorem T4)
ENV_OVERRIDES: dict[str, tuple[str, type]] = {
    "WHATSAPP_BRIDGE_PORT": ("bridge_port", int),
    "WHATSAPP_QR_PORT": ("qr_server_port", int),
    "WHATSAPP_AUTH_DIR": ("auth_dir", str),
    "WHATSAPP_BRIDGE_TOKEN": ("bridge_token", str),
    "WHATSAPP_MODE": ("mode", str),
    "WHATSAPP_ADMIN_NUMBER": ("admin_number", str),
    "WHATSAPP_LOG_LEVEL": ("log_level", str),
    "AI_GATEWAY_URL": ("ai_gateway_url", str),
    "AI_MODEL": ("ai_model", str),
    "WHISPER_API_URL": ("whisper_api_url", str),
}

import re as _re

# ── Intent parsing for dynamic setup wizard (Theorem T_DYNSETUP) ──
# P_DYNSETUP: Users describe intent in natural language; fixed questionnaires
# waste time asking what's already stated. Allowlist keyword matching extracts
# config values from free-text, reducing follow-up questions to only ambiguous fields.

# Rules are grouped by config field. Within each group, first match wins.
# Groups are independent: matching "purpose" doesn't prevent matching "tone".
_PURPOSE_RULES: list[tuple[_re.Pattern, str]] = [
    (_re.compile(r"\b(?:monitor|watch|log|observe|alert|silent|spy)\b", _re.I), "monitoring_only"),
    (_re.compile(r"\b(?:business|customer|support|client|sales|commerce)\b", _re.I), "business_support"),
    (_re.compile(r"\b(?:team|coordinate|group project|standup|reminder)\b", _re.I), "team_coordination"),
    (_re.compile(r"\b(?:personal|my messages|assistant|helper|buddy)\b", _re.I), "personal_assistant"),
]

_MODE_RULES: list[tuple[_re.Pattern, str]] = [
    (_re.compile(r"\b(?:never reply|don'?t respond|no repl|silent mode)\b", _re.I), "monitor_only"),
    (_re.compile(r"\b(?:monitor|watch|log|observe|silent|spy)\b", _re.I), "monitor_only"),
    (_re.compile(r"\b(?:ask (?:me )?first|approve|confirm before|manual)\b", _re.I), "ask_before_reply"),
    (_re.compile(r"\b(?:auto.?reply|automatic|just reply|respond auto)\b", _re.I), "auto_reply"),
    (_re.compile(r"\b(?:reply|respond|answer|assistant|bot|help)\b", _re.I), "auto_reply"),
]

_TONE_RULES: list[tuple[_re.Pattern, str]] = [
    (_re.compile(r"\b(?:casual|friendly|chill|relaxed|informal)\b", _re.I), "casual_friendly"),
    (_re.compile(r"\b(?:professional|formal|corporate|polished)\b", _re.I), "professional"),
    (_re.compile(r"\b(?:short|brief|concise|direct|terse|minimal)\b", _re.I), "concise_direct"),
    (_re.compile(r"\b(?:warm|caring|empathetic|kind|gentle)\b", _re.I), "warm_empathetic"),
]

_FEATURE_RULES: list[tuple[_re.Pattern, dict[str, Any]]] = [
    (_re.compile(r"\b(?:everyone|all contacts|anybody|anyone)\b", _re.I),
     {"allowlist": [], "blocklist": []}),
    (_re.compile(r"\b(?:transcri(?:be|ption)|voice.?to.?text|speech)\b", _re.I),
     {"voice_transcription": True}),
]

# Phone number extraction: international formats like +852 92893658, 85292893658, etc.
_PHONE_RE = _re.compile(r"(?:\+?\d[\d\s\-]{7,15}\d)")


def parse_intent(user_text: str) -> dict[str, Any]:
    """Extract config values from user's natural language description.

    Theorem T_DYNSETUP: Keyword-based allowlist matching on free-text intent.
    Returns a partial config dict with only the fields that could be inferred.
    Fields NOT present in the returned dict are ambiguous and need follow-up.

    Each field group (purpose, mode, tone) uses first-match-wins within the group,
    so "monitor my business WhatsApp" correctly gives purpose=monitoring_only
    (because "monitor" matches before "business" in the purpose rules).

    Args:
        user_text: The user's free-text description of their WhatsApp use case.

    Returns:
        Partial config dict (only inferred fields).
    """
    inferred: dict[str, Any] = {}

    # First-match-wins within each independent field group
    for pattern, purpose in _PURPOSE_RULES:
        if pattern.search(user_text):
            inferred["purpose"] = purpose
            break

    for pattern, mode in _MODE_RULES:
        if pattern.search(user_text):
            inferred["mode"] = mode
            break

    for pattern, tone in _TONE_RULES:
        if pattern.search(user_text):
            inferred["tone"] = tone
            break

    # Feature rules: all matching rules apply (not first-match-wins)
    for pattern, values in _FEATURE_RULES:
        if pattern.search(user_text):
            inferred.update(values)

    # Extract phone numbers for admin_number
    phones = _PHONE_RE.findall(user_text)
    if phones:
        admin = _re.sub(r"[^\d]", "", phones[0])
        inferred["admin_number"] = admin

    return inferred


CONFIG_DIR = Path.home() / ".happycapy-whatsapp"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _secure_permissions(path: Path, is_dir: bool = False) -> None:
    """Set restrictive permissions on sensitive files/dirs (Theorem T_FPERM).

    P_FPERMS: Default umask (0644/0755) allows other users to read.
    Config contains bridge_token, allowlist, API URLs. DB contains messages.
    Dirs: 0o700 (owner rwx only). Files: 0o600 (owner rw only).
    """
    try:
        os.chmod(path, 0o700 if is_dir else 0o600)
    except OSError:
        pass  # Non-fatal: some filesystems don't support chmod


def get_config_dir() -> Path:
    """Return (and create) the config directory with restricted permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _secure_permissions(CONFIG_DIR, is_dir=True)  # Theorem T_FPERM
    return CONFIG_DIR


def load_config() -> dict[str, Any]:
    """Load config from disk, applying defaults and env overrides."""
    config = dict(DEFAULT_CONFIG)

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            config.update(saved)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: failed to load config: {e}")

    # Apply environment variable overrides
    for env_var, (config_key, cast) in ENV_OVERRIDES.items():
        val = os.environ.get(env_var)
        if val is not None:
            try:
                config[config_key] = cast(val)
            except (ValueError, TypeError) as e:
                print(f"Warning: invalid {env_var}={val!r} (expected {cast.__name__}): {e}")

    return config


def save_config(config: dict[str, Any]) -> None:
    """Save config to disk with restricted permissions (Theorem T_FPERM)."""
    get_config_dir()
    # Write with restrictive permissions: owner read/write only (0o600).
    # Use os.open + os.fdopen to set permissions atomically at creation time,
    # avoiding the window where default umask leaves the file world-readable.
    fd = os.open(str(CONFIG_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)


def config_exists() -> bool:
    """Check if a saved config exists (Theorem T7 - skip wizard if exists)."""
    return CONFIG_FILE.exists()


def validate_config(config: dict[str, Any]) -> list[str]:
    """Validate config and return list of issues (empty = valid)."""
    issues = []

    valid_purposes = {"personal_assistant", "business_support", "team_coordination", "monitoring_only"}
    if config.get("purpose") not in valid_purposes:
        issues.append(f"Invalid purpose: {config.get('purpose')}")

    valid_tones = {"casual_friendly", "professional", "concise_direct", "warm_empathetic", "custom"}
    if config.get("tone") not in valid_tones:
        issues.append(f"Invalid tone: {config.get('tone')}")

    valid_modes = {"auto_reply", "ask_before_reply", "monitor_only"}
    if config.get("mode") not in valid_modes:
        issues.append(f"Invalid mode: {config.get('mode')}")

    valid_groups = {"monitor", "ignore"}
    if config.get("group_policy") not in valid_groups:
        issues.append(f"Invalid group_policy: {config.get('group_policy')}")

    admin = config.get("admin_number", "")
    if admin and not admin.replace("+", "").isdigit():
        issues.append(f"Invalid admin_number: must be phone digits")

    # Intelligence layer validation
    valid_statuses = {"", "available", "busy", "dnd", "away"}
    if config.get("status_override", "") not in valid_statuses:
        issues.append(f"Invalid status_override: {config.get('status_override')}")

    threshold = config.get("importance_threshold", 7)
    if not isinstance(threshold, int) or threshold < 1 or threshold > 10:
        issues.append(f"Invalid importance_threshold: {threshold} (must be 1-10)")

    port = config.get("bridge_port", 0)
    if not isinstance(port, int) or port < 1024 or port == 3001:
        issues.append(f"Invalid bridge_port: {port} (must be >= 1024, not 3001)")

    qr_port = config.get("qr_server_port", 0)
    if not isinstance(qr_port, int) or qr_port < 1024 or qr_port == 3001:
        issues.append(f"Invalid qr_server_port: {qr_port} (must be >= 1024, not 3001)")

    return issues


def build_system_prompt(config: dict[str, Any]) -> str:
    """Build the AI system prompt from config."""
    # Theorem T_REASONSTRIP: Always append reasoning suppression, even with overrides.
    # P_REASONLEAK: User-provided system_prompt_override may omit reasoning suppression,
    # leaving only the regex filter as defense. Appending it ensures Layer 0 always active.
    _MANDATORY_SUFFIX = (
        " CRITICAL: Wrap your entire response in <reply>...</reply> tags. "
        "Only the content inside <reply> tags will be sent to the WhatsApp contact. "
        "Put any thinking, reasoning, or notes OUTSIDE the <reply> tags."
    )
    if config.get("system_prompt_override"):
        return config["system_prompt_override"] + _MANDATORY_SUFFIX

    purpose_prompts = {
        "personal_assistant": "You are a helpful personal assistant on WhatsApp.",
        "business_support": "You are a professional business support assistant on WhatsApp.",
        "team_coordination": "You are a team coordination assistant on WhatsApp.",
        "monitoring_only": "You are monitoring WhatsApp messages. Do not reply.",
    }

    tone_prompts = {
        "casual_friendly": "Be casual, friendly, and conversational.",
        "professional": "Maintain a professional and formal tone.",
        "concise_direct": "Be concise and direct. No fluff.",
        "warm_empathetic": "Be warm, empathetic, and understanding.",
        "custom": config.get("tone_custom_instructions", ""),
    }

    # Theorem T_REASONSTRIP: System prompt is Layer 0 of reasoning leak prevention.
    # Explicit, redundant instructions reduce the probability of LLM non-compliance.
    # Even if the LLM ignores one instruction, the combination is harder to bypass.
    reasoning_suppression = (
        "CRITICAL RULES: "
        "1) ALWAYS wrap your entire response in <reply>...</reply> tags. Only the content inside <reply> tags will be sent. "
        "2) Put any thinking, reasoning, or notes OUTSIDE the <reply> tags - they will be discarded. "
        "3) Never use XML tags like <thinking>, <reasoning>, or <reflection> inside your <reply>. "
        "4) Never prefix responses with phrases like 'Let me think' or 'My reasoning is' inside your <reply>. "
        "5) Never reveal that you are an AI unless directly asked. "
        "6) Your <reply> content goes directly to a WhatsApp contact - only include the final response."
    )

    parts = [
        purpose_prompts.get(config["purpose"], purpose_prompts["personal_assistant"]),
        tone_prompts.get(config["tone"], tone_prompts["casual_friendly"]),
        "Keep responses appropriate for WhatsApp - concise and mobile-friendly.",
        reasoning_suppression,
    ]

    return " ".join(parts)
