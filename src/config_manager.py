"""Configuration manager for HappyCapy WhatsApp skill.

Handles loading, saving, validation, and defaults for all configuration.
Zero hardcoded values - everything configurable via JSON + env overrides.
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
    "ai_gateway_url": "https://ai-gateway.happycapy.ai/api/v1",
    "ai_model": "claude-sonnet-4-6",
    "max_message_length": 4000,
    "rate_limit_per_minute": 30,
    "media_max_age_hours": 24,
    "whisper_api_url": "https://api.groq.com/openai/v1/audio/transcriptions",
    "profile_model": "claude-haiku-4-5-20251001",
}

# Environment variable overrides (Theorem T4)
ENV_OVERRIDES: dict[str, tuple[str, type]] = {
    "WHATSAPP_BRIDGE_PORT": ("bridge_port", int),
    "WHATSAPP_QR_PORT": ("qr_server_port", int),
    "WHATSAPP_AUTH_DIR": ("auth_dir", str),
    "WHATSAPP_BRIDGE_TOKEN": ("bridge_token", str),
    "WHATSAPP_MODE": ("mode", str),
    "WHATSAPP_LOG_LEVEL": ("log_level", str),
    "AI_GATEWAY_URL": ("ai_gateway_url", str),
    "AI_MODEL": ("ai_model", str),
    "WHISPER_API_URL": ("whisper_api_url", str),
}

CONFIG_DIR = Path.home() / ".happycapy-whatsapp"
CONFIG_FILE = CONFIG_DIR / "config.json"


def get_config_dir() -> Path:
    """Return (and create) the config directory."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
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
    """Save config to disk."""
    get_config_dir()
    with open(CONFIG_FILE, "w") as f:
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

    port = config.get("bridge_port", 0)
    if not isinstance(port, int) or port < 1024 or port == 3001:
        issues.append(f"Invalid bridge_port: {port} (must be >= 1024, not 3001)")

    qr_port = config.get("qr_server_port", 0)
    if not isinstance(qr_port, int) or qr_port < 1024 or qr_port == 3001:
        issues.append(f"Invalid qr_server_port: {qr_port} (must be >= 1024, not 3001)")

    return issues


def build_system_prompt(config: dict[str, Any]) -> str:
    """Build the AI system prompt from config."""
    if config.get("system_prompt_override"):
        return config["system_prompt_override"]

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

    parts = [
        purpose_prompts.get(config["purpose"], purpose_prompts["personal_assistant"]),
        tone_prompts.get(config["tone"], tone_prompts["casual_friendly"]),
        "Keep responses appropriate for WhatsApp - concise and mobile-friendly.",
        "Never reveal that you are an AI unless directly asked.",
        "Never include internal reasoning, notes, or metadata in your responses.",
    ]

    return " ".join(parts)
