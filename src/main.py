"""Main orchestrator for HappyCapy WhatsApp skill.

Manages the complete lifecycle:
1. Interactive setup wizard (first run only, via AskUserQuestion)
2. Bridge process startup
3. QR server startup with port exposure
4. WhatsApp channel message processing
5. AI response generation via AI Gateway

Theorem T3: Interactive setup wizard runs BEFORE bridge starts (P4 + P7 + P11).
Theorem T7: Config file persists so setup wizard only runs once (P3 + P5).
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

# Add parent to path for imports
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from src.config_manager import (
    load_config,
    save_config,
    config_exists,
    validate_config,
    build_system_prompt,
    get_config_dir,
    DEFAULT_CONFIG,
)
from src.bridge_manager import BridgeManager
from src.qr_server import start_qr_server
from src.whatsapp_channel import WhatsAppChannel
from src.contact_store import ContactStore
from src.media_processor import process_media, cleanup_temp_files
# Intelligence layer modules (nanobot-inspired)
from src.audit_log import AuditLog
from src.importance_scorer import ImportanceScorer
from src.message_queue import MessageQueue
from src.auto_reply_templates import AutoReplyTemplates
from src.knowledge_graph import KnowledgeGraph
from src.escalation_engine import EscalationEngine
from src.memory_store import MemoryStore, MemorySearch
from src.quiet_hours import QuietHours
from src.semantic_guard import SemanticGuard
from src.fabrication_guard import FabricationGuard
from src.content_filter import ContentFilter
from src.health_monitor import HealthMonitor
from src.heartbeat_service import (
    HeartbeatService,
    make_queue_cleanup_task,
    make_audit_prune_task,
    make_escalation_expire_task,
    make_sample_prune_task,
)
from src.context_builder import ContextBuilder
from src.reflection_engine import ReflectionEngine
from src.cron_service import CronService
from src.session_manager import SessionManager
from src.tool_executor import ToolExecutor, ToolResult, TOOL_DEFINITIONS
from src.broadcast import (
    create_broadcast_engine, BroadcastEngine, BroadcastIntegration,
    CampaignStore, AUTO_SEGMENTS,
)

try:
    import httpx
except ImportError:
    httpx = None


from dataclasses import dataclass


@dataclass
class AIResponse:
    """Enhanced response from AI that includes tool call information."""

    content: str | None  # Text response (None if only tool calls)
    tool_calls: list[dict] | None  # OpenAI-format tool calls
    finish_reason: str  # "stop" or "tool_calls"


# ─── Setup Wizard Questions (mapped to AskUserQuestion format) ───


SETUP_QUESTIONS = [
    {
        "id": "admin_number",
        "question": "What is your WhatsApp phone number? (digits only, e.g. 14155551234). This lets you control the bot via /commands in WhatsApp.",
        "header": "Admin",
        "options": [
            {"label": "Enter my number", "value": "enter",
             "description": "You'll type your phone number (digits only, e.g. 14155551234)"},
            {"label": "Skip - No admin", "value": "skip",
             "description": "No remote control via WhatsApp (can still configure via HappyCapy UI)"},
        ],
    },
    {
        "id": "owner_name",
        "question": "What's your name? (Used when the bot replies as you, e.g. 'Hey, it's John')",
        "header": "Your Name",
        "options": [
            {"label": "Enter my name", "value": "enter",
             "description": "Your first name or nickname for natural-sounding replies"},
            {"label": "Skip", "value": "skip",
             "description": "Bot will reply without using a name"},
        ],
    },
    {
        "id": "purpose",
        "question": "What will you use this for? (Smart defaults are set based on your choice - you can change everything later via /commands)",
        "header": "Purpose",
        "options": [
            {"label": "Personal Assistant (Recommended)", "value": "personal_assistant",
             "description": "Replies as you, casual tone, strict privacy. Best for personal WhatsApp."},
            {"label": "Business Support", "value": "business_support",
             "description": "Professional tone, spreadsheet+email integrations, handles customer inquiries."},
            {"label": "Team Coordination", "value": "team_coordination",
             "description": "Friendly tone, reminders, team activity coordination."},
            {"label": "Monitoring Only", "value": "monitoring_only",
             "description": "Just logs messages, never sends any replies."},
        ],
    },
    {
        "id": "integrations",
        "question": "Which extra features would you like to enable?",
        "header": "Integrations",
        "options": [
            {"label": "None (Just AI chat)", "value": "none",
             "description": "Core features only: AI replies, image/video/PDF generation"},
            {"label": "Spreadsheet Tracking", "value": "spreadsheet",
             "description": "Log orders, expenses, customer data to Excel spreadsheets"},
            {"label": "Email Sending", "value": "email",
             "description": "Send emails (invoices, confirmations) via the bot"},
            {"label": "Both (Recommended for business)", "value": "both",
             "description": "Full business suite: spreadsheet tracking + email sending"},
        ],
    },
]

# Business type selection -- shown when user picks "Business Support" as purpose
BUSINESS_TYPE_QUESTION = {
    "id": "business_type",
    "question": "What type of business do you run? This auto-configures tone, vocabulary, workflows, and response patterns. (Pick the closest match -- you can customize everything later.)",
    "header": "Business Type",
    "options": [
        {"label": "Food & Restaurant",
         "value": "food_restaurant",
         "description": "Menu sharing, order-taking, delivery time estimates. E.g. restaurant, cafe, bakery, cloud kitchen."},
        {"label": "Beauty & Wellness",
         "value": "beauty_wellness",
         "description": "Appointment booking, service menu, availability. E.g. salon, spa, barbershop, nail studio."},
        {"label": "Retail & Shop",
         "value": "retail_shop",
         "description": "Product catalog, stock checks, order processing. E.g. clothing, electronics, grocery, pharmacy."},
        {"label": "Professional Services",
         "value": "professional_services",
         "description": "Client intake, scheduling, quotes. E.g. lawyer, accountant, consultant, freelancer."},
    ],
    # Additional types available via "Other" free-text:
    # healthcare, real_estate, travel_hospitality, education, home_services, custom_other
    # User can type the name and we'll fuzzy-match to a template ID.
}

# Extended business types -- matched from free-text "Other" input
_BUSINESS_TYPE_ALIASES: dict[str, str] = {
    # Direct IDs
    "food_restaurant": "food_restaurant",
    "beauty_wellness": "beauty_wellness",
    "retail_shop": "retail_shop",
    "professional_services": "professional_services",
    "healthcare": "healthcare",
    "real_estate": "real_estate",
    "travel_hospitality": "travel_hospitality",
    "education": "education",
    "home_services": "home_services",
    "custom_other": "custom_other",
    # Keyword matches
    "food": "food_restaurant", "restaurant": "food_restaurant", "cafe": "food_restaurant",
    "bakery": "food_restaurant", "kitchen": "food_restaurant", "catering": "food_restaurant",
    "delivery": "food_restaurant",
    "beauty": "beauty_wellness", "salon": "beauty_wellness", "spa": "beauty_wellness",
    "barber": "beauty_wellness", "nail": "beauty_wellness", "hair": "beauty_wellness",
    "massage": "beauty_wellness", "skincare": "beauty_wellness",
    "retail": "retail_shop", "shop": "retail_shop", "store": "retail_shop",
    "clothing": "retail_shop", "electronics": "retail_shop", "grocery": "retail_shop",
    "pharmacy": "retail_shop",
    "professional": "professional_services", "lawyer": "professional_services",
    "accountant": "professional_services", "consultant": "professional_services",
    "agency": "professional_services", "freelance": "professional_services",
    "architect": "professional_services", "consulting": "professional_services",
    "health": "healthcare", "doctor": "healthcare", "clinic": "healthcare",
    "dentist": "healthcare", "hospital": "healthcare", "medical": "healthcare",
    "physio": "healthcare", "vet": "healthcare", "veterinary": "healthcare",
    "real estate": "real_estate", "property": "real_estate", "rental": "real_estate",
    "broker": "real_estate", "housing": "real_estate", "apartment": "real_estate",
    "travel": "travel_hospitality", "hotel": "travel_hospitality", "hostel": "travel_hospitality",
    "tour": "travel_hospitality", "airbnb": "travel_hospitality", "guesthouse": "travel_hospitality",
    "hospitality": "travel_hospitality",
    "education": "education", "school": "education", "tutor": "education",
    "coaching": "education", "training": "education", "course": "education",
    "music school": "education", "academy": "education",
    "plumber": "home_services", "electrician": "home_services", "cleaning": "home_services",
    "pest control": "home_services", "repair": "home_services", "painting": "home_services",
    "handyman": "home_services", "ac repair": "home_services", "maintenance": "home_services",
    "home service": "home_services", "home services": "home_services",
    "custom": "custom_other", "other": "custom_other", "general": "custom_other",
}


def resolve_business_type(user_input: str) -> str:
    """Resolve user input to a business template ID.

    Handles direct IDs, preset option values, and fuzzy keyword matching.
    Falls back to 'custom_other' if no match.
    """
    if not user_input:
        return "custom_other"
    cleaned = user_input.strip().lower()
    # Direct match
    if cleaned in _BUSINESS_TYPE_ALIASES:
        return _BUSINESS_TYPE_ALIASES[cleaned]
    # Keyword search -- find the first keyword that appears in the input
    for keyword, template_id in _BUSINESS_TYPE_ALIASES.items():
        if keyword in cleaned:
            return template_id
    return "custom_other"


# Advanced settings shown only if user chooses to customize after seeing defaults
ADVANCED_QUESTIONS = [
    {
        "id": "personality_mode",
        "question": "How should the bot behave when replying to your contacts?",
        "header": "Personality",
        "options": [
            {"label": "Act as me (Recommended)", "value": "impersonate",
             "description": "Reply AS you -- contacts won't know it's AI. Never reveals it's a bot."},
            {"label": "Act as my assistant", "value": "assistant",
             "description": "Reply as an AI assistant on your behalf. Contacts know it's automated."},
        ],
    },
    {
        "id": "tone",
        "question": "What tone should be used when replying?",
        "header": "Tone",
        "options": [
            {"label": "Casual & Friendly", "value": "casual_friendly",
             "description": "Relaxed, conversational tone like texting a friend"},
            {"label": "Professional", "value": "professional",
             "description": "Formal and business-appropriate language"},
            {"label": "Concise & Direct", "value": "concise_direct",
             "description": "Short, to-the-point responses with no filler"},
            {"label": "Warm & Empathetic", "value": "warm_empathetic",
             "description": "Caring and understanding tone"},
        ],
    },
    {
        "id": "mode",
        "question": "How should the bot handle incoming messages?",
        "header": "Reply Mode",
        "options": [
            {"label": "Auto-Reply", "value": "auto_reply",
             "description": "Automatically respond to allowed contacts"},
            {"label": "Ask Before Replying", "value": "ask_before_reply",
             "description": "Show the message and proposed reply, wait for your approval"},
            {"label": "Monitor Only", "value": "monitor_only",
             "description": "Log all messages but never send any replies"},
        ],
    },
    {
        "id": "privacy_level",
        "question": "How should the bot handle private information between contacts?",
        "header": "Privacy",
        "options": [
            {"label": "Strict (Recommended)", "value": "strict",
             "description": "Never share info from one contact with another"},
            {"label": "Moderate", "value": "moderate",
             "description": "Share general info but protect private details"},
            {"label": "Open", "value": "open",
             "description": "Share freely (only if all contacts are trusted)"},
        ],
    },
]


# Readable labels for config values (used in defaults summary)
_DISPLAY_LABELS = {
    "personality_mode": {"impersonate": "Act as you", "assistant": "AI Assistant"},
    "tone": {"casual_friendly": "Casual & Friendly", "professional": "Professional",
             "concise_direct": "Concise & Direct", "warm_empathetic": "Warm & Empathetic"},
    "mode": {"auto_reply": "Auto-Reply", "ask_before_reply": "Ask Before Replying",
             "monitor_only": "Monitor Only"},
    "privacy_level": {"strict": "Strict", "moderate": "Moderate", "open": "Open"},
    "purpose": {"personal_assistant": "Personal Assistant", "business_support": "Business Support",
                "team_coordination": "Team Coordination", "monitoring_only": "Monitoring Only"},
}


def get_defaults_summary(config: dict) -> str:
    """Build a human-readable summary of the smart defaults for review.

    Shown to the user after core questions so they can approve or customize.
    """
    def _label(field: str, value: str) -> str:
        return _DISPLAY_LABELS.get(field, {}).get(value, value)

    lines = [
        "*Your Settings*\n",
        f"Purpose: {_label('purpose', config.get('purpose', ''))}",
    ]
    # Show business template if active
    biz_template = config.get("business_template", "")
    if biz_template:
        from src.business_templates import get_template
        tmpl = get_template(biz_template)
        if tmpl:
            lines.append(f"Business Type: {tmpl['name']} -- {tmpl['description']}")
    lines.extend([
        f"Personality: {_label('personality_mode', config.get('personality_mode', ''))}",
        f"Tone: {_label('tone', config.get('tone', ''))}",
        f"Mode: {_label('mode', config.get('mode', ''))}",
        f"Privacy: {_label('privacy_level', config.get('privacy_level', ''))}",
        f"Voice messages: {'Transcribe' if config.get('voice_transcription') else 'Ignore'}",
        f"Groups: {'Monitor' if config.get('group_policy') == 'monitor' else 'Ignore'}",
    ])
    integrations = config.get("enabled_integrations", ["core"])
    non_core = [i for i in integrations if i != "core"]
    if non_core:
        lines.append(f"Integrations: {', '.join(i.title() for i in non_core)}")
    else:
        lines.append("Integrations: None (core AI only)")

    return "\n".join(lines)


def map_answers_to_config(answers: dict[str, str]) -> dict:
    """Map wizard answers to config fields with smart defaults based on purpose.

    Core questions (4) set defaults; advanced overrides apply on top if provided.
    All defaults can be changed later via /commands.
    """
    config = dict(DEFAULT_CONFIG)

    purpose = answers.get("purpose", "personal_assistant")
    config["purpose"] = purpose

    # Smart defaults based on purpose (Axiom A_ONBOARD: minimize questions)
    if purpose == "personal_assistant":
        config["personality_mode"] = "impersonate"
        config["tone"] = "casual_friendly"
        config["mode"] = "auto_reply"
        config["privacy_level"] = "strict"
        config["alert_on_auto_reply"] = False
    elif purpose == "business_support":
        config["personality_mode"] = "assistant"
        config["tone"] = "professional"
        config["mode"] = "auto_reply"
        config["privacy_level"] = "strict"
        config["alert_on_auto_reply"] = True
    elif purpose == "team_coordination":
        config["personality_mode"] = "assistant"
        config["tone"] = "casual_friendly"
        config["mode"] = "auto_reply"
        config["privacy_level"] = "moderate"
        config["alert_on_auto_reply"] = True
    elif purpose == "monitoring_only":
        config["personality_mode"] = "assistant"
        config["tone"] = "concise_direct"
        config["mode"] = "monitor_only"
        config["privacy_level"] = "strict"
        config["alert_on_auto_reply"] = False

    # Always-best defaults (no need to ask)
    config["voice_transcription"] = True
    config["media_handling"] = "acknowledge"
    config["group_policy"] = "monitor"

    # Business template overrides (applied AFTER purpose defaults, BEFORE advanced overrides)
    business_type_raw = answers.get("business_type", "")
    if business_type_raw and business_type_raw not in ("skip", "enter", ""):
        from src.business_templates import get_template, apply_template
        template_id = resolve_business_type(business_type_raw)
        template = get_template(template_id)
        if template:
            apply_template(template, config)

    # Owner name
    owner_name = answers.get("owner_name", "skip")
    if owner_name not in ("skip", "enter", ""):
        config["owner_name"] = owner_name.strip()

    # Integrations (only set manually if no business template set them)
    integ = answers.get("integrations", "none")
    if not config.get("business_template"):
        # No template active -- use manual integrations choice
        if integ == "spreadsheet":
            config["enabled_integrations"] = ["core", "spreadsheet"]
        elif integ == "email":
            config["enabled_integrations"] = ["core", "email"]
        elif integ == "both":
            config["enabled_integrations"] = ["core", "spreadsheet", "email"]
        else:
            config["enabled_integrations"] = ["core"]

    # Admin number (Theorem T_ADMCMD)
    admin = answers.get("admin_number", "skip")
    if admin not in ("skip", "enter", ""):
        config["admin_number"] = "".join(c for c in admin if c.isdigit())

    # Advanced overrides: if user chose to customize, these override smart defaults
    for field in ("personality_mode", "tone", "mode", "privacy_level"):
        val = answers.get(field)
        if val and val not in ("skip", "enter", ""):
            config[field] = val

    # Re-derive alert_on_auto_reply from personality_mode
    if config["personality_mode"] == "impersonate":
        config["alert_on_auto_reply"] = False

    return config


# ─── AI Response Generation ───


async def generate_ai_response(
    message: str,
    system_prompt: str,
    chat_history: list[dict],
    config: dict,
    media_content: list[dict] | None = None,
    client: "httpx.AsyncClient | None" = None,
    tools: list[dict] | None = None,
) -> AIResponse:
    """Generate an AI response using the AI Gateway.

    Theorem T_POOL: Reuses shared httpx client when provided (P_POOL).
    Saves ~100-300ms per call by avoiding TCP+TLS handshake.

    Supports multimodal input (Theorem T_IMG) and tool calling:
    - media_content: list of OpenAI-compatible content parts (image_url, etc.)
    - tools: list of OpenAI-format tool definitions (enables function calling)
    """
    _error = lambda msg: AIResponse(content=msg, tool_calls=None, finish_reason="stop")

    if not httpx:
        return _error("AI response unavailable (httpx not installed)")

    api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
    if not api_key:
        return _error("AI response unavailable (no API key)")

    gateway_url = config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1")
    model = config.get("ai_model", "claude-sonnet-4-6")

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(chat_history[-20:])

    if media_content and message:
        user_parts = [{"type": "text", "text": message}]
        user_parts.extend(media_content)
        messages.append({"role": "user", "content": user_parts})
    elif message:
        messages.append({"role": "user", "content": message})
    # If message is empty (tool result follow-up), history already has everything

    url = f"{gateway_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": 1024, "temperature": 0.7}

    # Add tool definitions if provided
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    # Vision needs more time; text-only is faster (Theorem T_LAZY applied to timeout).
    timeout = 90.0 if media_content else 60.0

    try:
        if client:
            resp = await client.post(url, headers=headers, json=payload, timeout=timeout)
        else:
            async with httpx.AsyncClient() as _c:
                resp = await _c.post(url, headers=headers, json=payload, timeout=timeout)

        if resp.status_code == 200:
            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]
            return AIResponse(
                content=msg.get("content"),
                tool_calls=msg.get("tool_calls"),
                finish_reason=choice.get("finish_reason", "stop"),
            )
        else:
            # Theorem T_ERRREDACT: Don't log raw API response body (P_LOGPII).
            print(f"AI Gateway error: HTTP {resp.status_code}")
            return _error("I'm having trouble thinking right now. Please try again in a moment.")
    except Exception as e:
        # Theorem T_ERRREDACT: Log error type only, not full message which may contain PII.
        print(f"AI request error: {type(e).__name__}")
        return _error("I'm temporarily unavailable. Please try again shortly.")


# ─── Main Orchestrator ───


class WhatsAppOrchestrator:
    """Main orchestrator that ties all components together."""

    def __init__(self):
        self.config: dict = {}
        self.system_prompt: str = ""
        self.bridge: BridgeManager | None = None
        self.qr_server = None
        self.channel: WhatsAppChannel | None = None
        self.chat_histories: dict[str, list[dict]] = {}  # chat_id -> messages
        self.contact_store: ContactStore | None = None
        self._contact_locks: dict[str, asyncio.Lock] = {}  # per-contact locks for ordered processing
        # Theorem T_POOL: Single shared httpx client for connection reuse (P_POOL).
        # Saves ~100-300ms per HTTP call by amortizing TCP+TLS handshake.
        self._http_client: httpx.AsyncClient | None = None
        # Intelligence layer (nanobot-inspired modules)
        self.audit_log: AuditLog | None = None
        self.scorer: ImportanceScorer | None = None
        self.message_queue: MessageQueue | None = None
        self.templates: AutoReplyTemplates | None = None
        self.kg: KnowledgeGraph | None = None
        self.escalation: EscalationEngine | None = None
        # Two-layer memory system (nanobot-inspired)
        self.memory: MemoryStore | None = None
        self.memory_search: MemorySearch | None = None
        self._message_count_since_consolidation = 0
        self._CONSOLIDATION_THRESHOLD = 30  # Consolidate every N messages
        self._consolidation_ran_at_startup = False
        # Per-contact conversation takeover: {jid: expiry_timestamp}
        self._takeover_contacts: dict[str, float] = {}
        # Push name cache: skip DB writes when name hasn't changed
        self._last_push_names: dict[str, str] = {}
        # Quiet hours system
        self.quiet_hours: QuietHours | None = None
        # Security guards
        self.semantic_guard: SemanticGuard | None = None
        self.fabrication_guard: FabricationGuard | None = None
        self.content_filter: ContentFilter | None = None
        # Health monitor (uptime, memory, connections)
        self.health_monitor: HealthMonitor | None = None
        # Heartbeat service (periodic maintenance)
        self.heartbeat: HeartbeatService | None = None
        # Context builder (layered system prompt assembly)
        self.context_builder: ContextBuilder | None = None
        # Cron/scheduling service (reminders, recurring tasks)
        self.cron: CronService | None = None
        # History sync stats
        self._history_sync_stats: dict = {"total_stored": 0, "total_skipped": 0, "syncs_received": 0}
        # Session manager (conversation continuity tracking)
        self.session_mgr: SessionManager | None = None
        # Tool executor (image gen, video gen, PDF creation)
        self.tool_executor: ToolExecutor | None = None
        # Reflection engine (learns from mistakes and owner corrections)
        self.reflection: ReflectionEngine | None = None
        # Escalation context tracker: maps admin_number -> list of recent alerts
        # Each alert: {"sender_id": ..., "sender_name": ..., "content_preview": ..., "timestamp": ...}
        self._recent_escalation_alerts: list[dict] = []
        self._ESCALATION_ALERT_MAX = 20  # Keep last 20 alerts for context matching
        # Track last bot response per contact for correction detection
        self._last_bot_response: dict[str, str] = {}  # sender_id -> last response
        # Broadcast campaign engine
        self.broadcast: BroadcastEngine | None = None
        self._broadcast_store: CampaignStore | None = None

    def print_setup_instructions(self) -> None:
        """Print the setup wizard questions for the user to answer via AskUserQuestion."""
        print("\n" + "=" * 60)
        print("HAPPYCAPY WHATSAPP - SETUP WIZARD")
        print("=" * 60)
        print()
        print("This skill needs to be configured interactively.")
        print("Phase 1: Ask 4 core questions:")
        print()

        for q in SETUP_QUESTIONS:
            print(f"  Q: {q['question']}")
            for opt in q["options"]:
                print(f"     - {opt['label']}: {opt['description']}")
            print()

        print("Phase 2: Show smart defaults and ask: Continue or Customize?")
        print("Phase 3 (optional): If Customize, ask advanced settings:")
        print()
        for q in ADVANCED_QUESTIONS:
            print(f"  Q: {q['question']}")
            for opt in q["options"]:
                print(f"     - {opt['label']}: {opt['description']}")
            print()

        print("After answering, the config will be saved and services started.")
        print("=" * 60)

    def apply_config(self, config: dict) -> None:
        """Apply and save configuration."""
        issues = validate_config(config)
        if issues:
            print(f"Config validation warnings: {issues}")

        self.config = config
        save_config(config)
        self.system_prompt = build_system_prompt(config)
        print(f"Config saved. Mode: {config['mode']}, Tone: {config['tone']}")

    def start_bridge(self) -> None:
        """Start the Node.js bridge process."""
        bridge_dir = SKILL_DIR / "bridge"
        auth_dir = self.config.get("auth_dir", str(Path.home() / ".happycapy-whatsapp" / "whatsapp-auth"))

        # Ensure auth dir exists
        Path(auth_dir).mkdir(parents=True, exist_ok=True)

        self.bridge = BridgeManager(
            bridge_dir=str(bridge_dir),
            port=self.config.get("bridge_port", 3002),
            auth_dir=auth_dir,
            token=self.config.get("bridge_token", ""),
            rate_limit=self.config.get("rate_limit_per_minute", 30),
        )
        self.bridge.start()
        print("Bridge manager started")

    def start_qr_server(self) -> str:
        """Start QR server and export port. Returns the public URL."""
        port = self.config.get("qr_server_port", 8765)
        self.qr_server = start_qr_server(port)

        # Export port for external access (Premise P12)
        try:
            result = subprocess.run(
                ["/app/export-port.sh", str(port)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            url = result.stdout.strip()
            if url:
                print(f"QR page available at: {url}")
                return url
        except Exception as e:
            print(f"Could not export port: {e}")

        return f"http://localhost:{port}"

    async def handle_message(
        self, sender_id: str, chat_id: str, content: str, media_paths: list, metadata: dict
    ) -> None:
        """Handle an incoming WhatsApp message.

        Uses per-contact locks so that:
        - Messages from DIFFERENT contacts are processed concurrently
        - Messages from the SAME contact are processed sequentially (preserving order)
        This prevents contact B from waiting for contact A's AI response.
        """
        # Atomic get-or-create per-contact lock (setdefault is atomic in CPython)
        lock = self._contact_locks.setdefault(chat_id, asyncio.Lock())

        async with lock:
            await self._process_message(sender_id, chat_id, content, media_paths, metadata)

    async def _process_message(
        self, sender_id: str, chat_id: str, content: str, media_paths: list, metadata: dict
    ) -> None:
        """Process a single message (called under per-contact lock).

        Media understanding flow (Theorems T_IMG, T_PDF, T_VID):
        1. If media_paths contains files, process them via media_processor
        2. Vision-capable media (images, stickers, video keyframes) become multimodal content parts
        3. Text-extractable media (PDFs, documents) have text injected into the user message
        4. Audio/video audio tracks get transcribed and injected as text
        """
        import time as _time
        mode = self.config.get("mode", "auto_reply")

        # Theorem T_LOGREDACT: Never log message content; use length indicators (P_LOGPII).
        print(f"[{sender_id}] ({len(content)} chars, {len(media_paths)} media)")

        # Health monitor: record every inbound message + mark WA connected
        if self.health_monitor:
            self.health_monitor.record_message(chat_id)
            self.health_monitor.set_whatsapp_connected(True)

        # Daily summary counters
        self._daily_msg_count += 1
        self._daily_unique_contacts.add(sender_id)

        # Update contact name from WhatsApp pushName (skip if unchanged since last message)
        push_name = metadata.get("sender_name", "")
        if push_name and self.contact_store and self._last_push_names.get(sender_id) != push_name:
            self._last_push_names[sender_id] = push_name
            self.contact_store.update_whatsapp_name(sender_id, push_name=push_name)
            # Enrich metadata with best known name for downstream use
            metadata["sender_name"] = self.contact_store.get_contact_name(sender_id)

        # Per-contact takeover: skip processing if owner is handling this contact
        if sender_id in self._takeover_contacts:
            expiry = self._takeover_contacts[sender_id]
            if _time.time() < expiry:
                # Owner has taken over this conversation; don't auto-reply
                if self.contact_store:
                    asyncio.create_task(self.contact_store.store_sample(sender_id, "user", content))
                return
            else:
                del self._takeover_contacts[sender_id]

        # Theorem T_ADMCMD: Admin slash commands are handled directly, not forwarded to AI.
        admin_number = self.config.get("admin_number", "")
        if admin_number and sender_id == admin_number and content.strip().startswith("/"):
            if self.audit_log:
                self.audit_log.log("admin_cmd", chat_id, "inbound", len(content), metadata.get("id", ""))
            await self._handle_admin_command(chat_id, content.strip(), metadata)
            return

        # Owner correction detection: when admin manually types in a contact's chat
        # (not their own), and the bot had previously replied, record as correction.
        if admin_number and sender_id == admin_number:
            contact_jid = chat_id.split("@")[0] if "@" in chat_id else chat_id
            if contact_jid != admin_number and self.reflection and contact_jid in self._last_bot_response:
                bot_said = self._last_bot_response.pop(contact_jid)
                contact_name = ""
                if self.contact_store:
                    profile = self.contact_store.get_profile(contact_jid)
                    contact_name = profile.get("name", "") if profile else ""
                self.reflection.record_correction(
                    bot_said=bot_said,
                    owner_correction=content,
                    contact_id=contact_jid,
                    contact_name=contact_name,
                )
                print(f"[reflection] Recorded owner correction for {contact_name or contact_jid}")
                # Don't process further -- admin's direct chat message goes to the contact as-is
                return

        # ── Intelligence layer: score, queue, audit (T_SCOREPLUGIN, T_QUEUEFIRST, T_AUDITALL) ──
        score, reasons = (5, [])
        queue_id = None
        if self.scorer:
            score, reasons = self.scorer.score_dm(content, sender_id)

        # Only queue messages in ask_before_reply mode (in auto_reply, queue is wasted I/O)
        if self.message_queue and mode != "auto_reply":
            queue_id = self.message_queue.add(
                sender_id, metadata.get("sender_name", ""), content, score, reasons,
            )

        if self.audit_log:
            self.audit_log.log(
                "msg_in", chat_id, "inbound", len(content), metadata.get("id", ""),
                {"score": score, "reasons": reasons},
            )

        # Status-aware auto-reply: if owner is busy and score below threshold
        status = self.config.get("status_override", "")
        if status in ("busy", "dnd", "away") and self.config.get("auto_reply_when_busy", True):
            if score < self.config.get("importance_threshold", 7):
                template_reply = self.templates.get_status_reply(status) if self.templates else None
                if template_reply:
                    await self.channel.send_text(chat_id, template_reply)
                    if self.message_queue and queue_id:
                        self.message_queue.mark_replied(queue_id)
                    if self.audit_log:
                        self.audit_log.log("msg_out", chat_id, "outbound", len(template_reply), "", {"auto_reply": True, "status": status})
                    return

        # High importance: notify admin (if enabled and not from admin themselves)
        # Always alert admin for high-importance messages regardless of personality mode.
        # In impersonate mode, the bot also uses ask_owner for questions -- alerts are
        # a separate safety net for messages the LLM might not escalate on its own.
        alert_enabled = (
            self.config.get("escalation_enabled", True)
            and score >= self.config.get("importance_threshold", 7)
            and sender_id != admin_number
            and admin_number
        )
        if alert_enabled:
            sender_name = metadata.get("sender_name", sender_id)
            alert_data = {
                "sender_id": sender_id,
                "sender_name": sender_name,
                "content_preview": content[:100],
                "score": score,
                "reasons": reasons,
            }
            # Check quiet hours suppression
            if self.quiet_hours and self.quiet_hours.should_suppress(score):
                self.quiet_hours.queue_alert(alert_data)
                print(f"[quiet-hours] Alert suppressed for {sender_name} (score {score})")
            else:
                admin_jid = f"{admin_number}@s.whatsapp.net"
                # Include actual message preview so admin knows what was said
                preview = content[:300] + ("..." if len(content) > 300 else "")
                alert = f"*[{score}/10]* {sender_name}:\n{preview}"
                try:
                    await self.channel.send_text(admin_jid, alert)
                    # Track alert for escalation context matching when admin replies
                    from datetime import datetime as _dt
                    self._recent_escalation_alerts.append({
                        "sender_id": sender_id,
                        "sender_name": sender_name,
                        "content_preview": content[:500],
                        "score": score,
                        "timestamp": _dt.now().isoformat(),
                    })
                    # Trim to max size
                    if len(self._recent_escalation_alerts) > self._ESCALATION_ALERT_MAX:
                        self._recent_escalation_alerts = self._recent_escalation_alerts[-self._ESCALATION_ALERT_MAX:]
                except Exception:
                    pass  # Don't fail main pipeline if admin alert fails

        # Quiet hours: check if digest needs flushing (runs on every message)
        if self.quiet_hours and admin_number:
            queued_alerts = self.quiet_hours.check_and_flush()
            if queued_alerts:
                digest = self.quiet_hours.format_digest(queued_alerts)
                if digest:
                    admin_jid = f"{admin_number}@s.whatsapp.net"
                    try:
                        await self.channel.send_text(admin_jid, digest)
                        print(f"[quiet-hours] Flushed {len(queued_alerts)} queued alerts as digest")
                    except Exception:
                        pass

        # SemanticGuard: check for prompt injection before processing
        # Skip for assistant/business modes -- order messages don't need injection detection
        _personality = self.config.get("personality_mode", "impersonate")
        if self.semantic_guard and content and _personality == "impersonate":
            api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
            api_url = self.config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1/openai/v1")
            guard_model = self.config.get("profile_model", "gpt-4.1-mini")  # Use fast model
            try:
                guard_result = await self.semantic_guard.classify(
                    content, api_url, api_key, guard_model, client=self._http_client,
                )
                if guard_result.is_injection:
                    print(f"[semantic-guard] INJECTION detected from {sender_id}: {guard_result.category} (conf={guard_result.confidence:.2f})")
                    if self.audit_log:
                        self.audit_log.log("security", chat_id, "inbound", len(content), metadata.get("id", ""),
                                          {"guard": "semantic", "category": guard_result.category, "confidence": guard_result.confidence})
                    # Don't respond to injection attempts - silently drop
                    return
            except Exception:
                pass  # Guard failure should not block normal processing

        # Process media for understanding (before storing sample, so we capture rich content)
        media_content_parts = []  # Multimodal parts for vision API
        media_text_parts = []     # Extracted text to append to content
        temp_files_to_cleanup = []

        for file_path in media_paths:
            try:
                media_type = metadata.get("media_type", "")
                media_mime = metadata.get("media_mimetype", "")
                media_filename = metadata.get("media_filename", "")

                # Auto-detect type from extension if not provided
                if not media_type:
                    ext = Path(file_path).suffix.lower()
                    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                        media_type = "sticker" if ext == ".webp" else "image"
                    elif ext == ".pdf":
                        media_type = "document"
                        media_mime = "application/pdf"
                    elif ext in (".mp4", ".avi", ".mov", ".mkv"):
                        media_type = "video"
                    elif ext in (".ogg", ".mp3", ".m4a", ".wav"):
                        media_type = "audio"
                    else:
                        media_type = "document"

                result = await process_media(
                    file_path, media_type, media_mime, media_filename, self.config,
                    client=self._http_client,
                )

                # Collect multimodal content parts (images, stickers, video keyframes)
                if result.get("content_parts"):
                    media_content_parts.extend(result["content_parts"])
                    print(f"  [media] {result.get('description', media_type)} -> vision")

                # Collect extracted text (PDFs, documents)
                if result.get("extracted_text"):
                    media_text_parts.append(result["extracted_text"])
                    print(f"  [media] {result.get('description', media_type)} -> text extracted")

                # Collect audio transcription (voice, video audio)
                if result.get("audio_transcription"):
                    media_text_parts.append(f"[Audio transcription: {result['audio_transcription']}]")
                    print(f"  [media] Audio transcribed")

                # Track temp files for cleanup
                if result.get("keyframe_path"):
                    temp_files_to_cleanup.append(result["keyframe_path"])

            except Exception as e:
                print(f"  [media] Processing error for {file_path}: {e}")
                if self.health_monitor:
                    self.health_monitor.record_error()

        # Enrich content with extracted text from media
        enriched_content = content
        if media_text_parts:
            enriched_content = content + "\n\n" + "\n\n".join(media_text_parts)

        # Quoted/reply message tracking: prepend reply context so AI knows what's being replied to
        quoted_content = metadata.get("quoted_content", "")
        if quoted_content:
            quoted_prefix = f"[Replying to: \"{quoted_content[:200]}\"]\n"
            enriched_content = quoted_prefix + enriched_content
            if metadata.get("quoted_participant"):
                print(f"  [reply-chain] Quote from {metadata['quoted_participant'][:12]}.. ({len(quoted_content)} chars)")

        # Theorem T_FIRE: Fire-and-forget sample storage to avoid blocking the AI call.
        # The asyncio lock inside store_sample handles concurrent writes safely.
        if self.contact_store:
            asyncio.create_task(self.contact_store.store_sample(sender_id, "user", enriched_content))

        # Broadcast reply attribution: check if this message is a reply to a campaign
        if self.broadcast and content:
            try:
                await self.broadcast.check_reply_attribution(sender_id, content)
            except Exception:
                pass  # Attribution failure should never block message processing

        if mode == "monitor_only":
            cleanup_temp_files(*temp_files_to_cleanup)
            return

        # Session tracking: detect conversation freshness (Theorem T_TIMEOUT)
        session_context = ""
        if self.session_mgr:
            sess = self.session_mgr.touch(sender_id)
            if sess["is_resumed"]:
                # Session expired -> clear in-memory history for fresh start
                self.chat_histories.pop(chat_id, None)
                session_context = self.session_mgr.build_resume_context(sess)
                print(f"  [session] Resumed after {sess['gap_seconds']}s gap")

        # Get/create chat history
        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = []

        history = self.chat_histories[chat_id]
        history.append({"role": "user", "content": enriched_content})

        # Trim history to last 40 messages
        if len(history) > 40:
            self.chat_histories[chat_id] = history[-40:]
            history = self.chat_histories[chat_id]

        if mode == "ask_before_reply":
            # Theorem T_LOGREDACT: Don't log content even for approval mode (P_LOGPII).
            print(f"[APPROVAL NEEDED] {sender_id} ({len(enriched_content)} chars)")

        # Build layered system prompt via ContextBuilder (nanobot pattern)
        # Gathers: security anchor + SOUL.md + USER.md + config + memory + profile + RAG
        memory_ctx = ""
        recent_history = ""
        profile_context = ""
        rag_context = ""

        if self.memory:
            memory_ctx = self.memory.get_memory_context(jid=sender_id) or ""
            recent_history = self.memory.get_recent_history(jid=sender_id, max_entries=5, max_chars=2000) or ""

        if self.contact_store:
            profile_context = self.contact_store.format_profile_for_prompt(sender_id) or ""

        if self.kg:
            try:
                rag_context, _kg_stats = self.kg.retrieve(sender_id, enriched_content)
            except Exception:
                pass  # KG failure should not block the response

        if self.context_builder:
            system_prompt = self.context_builder.build_system_prompt(
                self.config,
                memory_context=memory_ctx,
                recent_history=recent_history,
                contact_profile=profile_context,
                rag_context=rag_context,
            )
        else:
            # Fallback to legacy prompt
            system_prompt = self.system_prompt

        # Append session context if conversation was resumed after timeout
        if session_context:
            system_prompt = system_prompt + "\n\n" + session_context

        # Inject reflection lessons (learned from past mistakes/corrections)
        if self.reflection:
            lessons_ctx = self.reflection.get_lessons_for_prompt(contact_id=sender_id)
            if lessons_ctx:
                system_prompt = system_prompt + "\n\n---\n\n" + lessons_ctx

        # Escalation context injection for admin replies:
        # When admin sends a non-slash message, check if it's about a recent escalation.
        # If so, inject the escalation context so the AI knows what it's replying to.
        if sender_id == admin_number and self._recent_escalation_alerts:
            esc_context = self._build_escalation_context_for_admin(content)
            if esc_context:
                system_prompt = system_prompt + "\n\n---\n\n" + esc_context

        # Send typing indicator before AI processing (non-blocking, best-effort)
        if self.channel:
            asyncio.create_task(self.channel.send_typing(chat_id))

        # Generate AI response with multimodal content and optional tool calling.
        # Theorem T_POOL: Pass shared client for connection reuse.
        use_tools = self.config.get("tool_calling_enabled", True) and self.tool_executor is not None
        ai_resp = await generate_ai_response(
            message=enriched_content,
            system_prompt=system_prompt,
            chat_history=history,
            config=self.config,
            media_content=media_content_parts if media_content_parts else None,
            client=self._http_client,
            tools=self.tool_executor.get_tool_definitions() if use_tools else None,
        )

        # Cleanup temporary files
        cleanup_temp_files(*temp_files_to_cleanup)

        # ── Tool call loop (max 1 iteration) ──
        response = ai_resp.content or ""
        generated_media: list[str] = []  # Paths to files generated by tools
        tool_result_messages: list[dict] = []

        if ai_resp.finish_reason == "tool_calls" and ai_resp.tool_calls and self.tool_executor:
            tool_count = len(ai_resp.tool_calls)
            print(f"  [tools] LLM requested {tool_count} tool(s)")

            # Send "generating..." for slow tools (video)
            has_video = any(
                tc.get("function", {}).get("name") == "generate_video"
                for tc in ai_resp.tool_calls
            )
            if has_video and self.channel:
                await self.channel.send_text(chat_id, "Generating video, this may take a minute...")

            # Execute each tool call
            for tc in ai_resp.tool_calls:
                tc_id = tc.get("id", "")
                func = tc.get("function", {})
                func_name = func.get("name", "")

                try:
                    func_args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    func_args = {}

                print(f"  [tools] Executing {func_name}...")
                result = await self.tool_executor.execute(func_name, func_args)

                if result.success and result.media_path:
                    generated_media.append(result.media_path)
                    tool_content = result.content
                    print(f"  [tools] {func_name} OK -> {Path(result.media_path).name}")
                elif result.success:
                    tool_content = result.content
                    print(f"  [tools] {func_name} OK (no media)")
                else:
                    tool_content = f"Error: {result.content}"
                    print(f"  [tools] {func_name} FAILED: {result.content}")

                tool_result_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": func_name,
                    "content": tool_content,
                })

            # Add assistant tool_call message + tool results to history
            history.append({
                "role": "assistant",
                "content": ai_resp.content,
                "tool_calls": ai_resp.tool_calls,
            })
            history.extend(tool_result_messages)

            # Call LLM again with tool results to get final text response (no tools this time)
            print("  [tools] Getting final response with tool results...")
            final_resp = await generate_ai_response(
                message="",
                system_prompt=system_prompt,
                chat_history=history,
                config=self.config,
                client=self._http_client,
                tools=None,
            )
            response = final_resp.content or ""

        # ── Outbound guards: content filter + fabrication guard (before sending) ──
        if response:
            # Content filter: block credentials, tokens, internal paths
            if self.content_filter:
                filter_result = self.content_filter.check(response)
                if filter_result.is_blocked:
                    print(f"[content-filter] BLOCKED outbound: {filter_result.category} - {filter_result.description}")
                    if self.audit_log:
                        self.audit_log.log("security", chat_id, "outbound", len(response), "",
                                          {"guard": "content_filter", "category": filter_result.category})
                    response = "I'm sorry, I can't share that information."

            # Fabrication guard: block fabricated personal claims
            # Skip for tool-call responses (AI is confirming data it just logged)
            if self.fabrication_guard and not tool_result_messages:
                fab_result = self.fabrication_guard.check(response)
                if fab_result.is_fabrication:
                    print(f"[fabrication-guard] BLOCKED: {fab_result.category} (conf={fab_result.confidence:.2f})")
                    if self.audit_log:
                        self.audit_log.log("security", chat_id, "outbound", len(response), "",
                                          {"guard": "fabrication", "category": fab_result.category, "confidence": fab_result.confidence})
                    response = fab_result.replacement

        # Send generated media files first (images, videos, PDFs from tool calls)
        if self.channel and generated_media:
            for media_path in generated_media:
                try:
                    await self.channel.send_media(chat_id, media_path)
                    print(f"  [tools] Sent media: {Path(media_path).name}")
                except Exception as e:
                    print(f"  [tools] Failed to send media: {type(e).__name__}")

        # Send text response
        if self.channel and response:
            await self.channel.send_text(chat_id, response)
            history.append({"role": "assistant", "content": response})
            # Theorem T_LOGREDACT: Log reply length, not content (P_LOGPII).
            print(f"[reply -> {sender_id}] ({len(response)} chars)")

            # Mark queue as replied and audit outbound (T_QUEUEFIRST, T_AUDITALL)
            if self.message_queue and queue_id:
                self.message_queue.mark_replied(queue_id)
            if self.audit_log:
                self.audit_log.log("msg_out", chat_id, "outbound", len(response), "")

            # Track last bot response per contact for correction detection
            self._last_bot_response[sender_id] = response[:500]

            # Theorem T_FIRE: Fire-and-forget assistant sample storage.
            if self.contact_store:
                asyncio.create_task(self.contact_store.store_sample(sender_id, "assistant", response))

        # Check if contact profile needs generation/update (async, non-blocking)
        if self.contact_store and self.contact_store.needs_profile_update(sender_id):
            asyncio.create_task(self._update_contact_profile(sender_id))

        # Memory consolidation: trigger after N messages across all contacts
        self._message_count_since_consolidation += 1
        if (self.memory and
            self._message_count_since_consolidation >= self._CONSOLIDATION_THRESHOLD):
            asyncio.create_task(self._consolidate_memory())
            # Also trigger self-reflection alongside consolidation
            if self.reflection and self.contact_store:
                asyncio.create_task(self._run_self_reflection())

    def _build_escalation_context_for_admin(self, admin_message: str) -> str:
        """Build context for admin's reply by matching it to recent escalation alerts.

        When admin sends a non-slash-command message, this checks if it's likely
        a reply to a recent escalation alert. Returns context string for injection
        into the system prompt, so the AI knows what the admin is replying to.
        """
        if not self._recent_escalation_alerts:
            return ""

        # Strategy: show the most recent escalation alerts to give the AI context.
        # The AI can then determine which one the admin is replying to.
        from datetime import datetime as _dt

        # Filter alerts from last 2 hours only
        cutoff = _dt.now().timestamp() - 7200  # 2 hours
        recent = []
        for alert in reversed(self._recent_escalation_alerts):
            try:
                ts = _dt.fromisoformat(alert["timestamp"]).timestamp()
                if ts >= cutoff:
                    recent.append(alert)
            except (ValueError, KeyError):
                recent.append(alert)  # Include if timestamp unparseable

        if not recent:
            return ""

        # Also check pending escalations from the EscalationEngine (ask_owner)
        pending_esc = ""
        if self.escalation:
            pending = self.escalation.pending()
            if pending:
                esc_lines = []
                for esc in pending[:5]:
                    name = esc.get("sender_name") or esc["sender_id"]
                    esc_lines.append(f"  [{esc['code']}] From {name}: {esc.get('question_preview', '?')}")
                pending_esc = "\nPending ask_owner escalations:\n" + "\n".join(esc_lines)

        # Build context block
        lines = [
            "## Escalation Context (YOU ARE THE ADMIN / PHONE OWNER)",
            "You are receiving a message from yourself (the admin/owner).",
            "This is likely a reply to one of these recent escalation alerts you received:",
            "",
        ]
        for i, alert in enumerate(recent[:10]):
            name = alert.get("sender_name", alert.get("sender_id", "?"))
            preview = alert.get("content_preview", "")[:200]
            score = alert.get("score", "?")
            lines.append(f"{i+1}. [{score}/10] *{name}*: {preview}")

        if pending_esc:
            lines.append(pending_esc)

        lines.extend([
            "",
            "INSTRUCTIONS FOR ADMIN REPLY:",
            "- The admin is telling you what to reply to a specific contact.",
            "- Determine which contact/escalation the admin is responding to.",
            "- Use the send_message tool to forward the admin's reply to the correct contact.",
            "- If you can't determine which contact, ask the admin to clarify.",
            "- The reply should go TO the contact, not back to the admin.",
        ])

        return "\n".join(lines)

    async def _startup_consolidation_check(self) -> None:
        """Check on startup if any contacts need memory consolidation.

        The in-memory consolidation counter resets on restart, so contacts
        with samples but empty MEMORY.md would never get consolidated.
        This runs once after startup to catch up.
        """
        if not self.memory or not self.contact_store or self._consolidation_ran_at_startup:
            return
        self._consolidation_ran_at_startup = True
        try:
            active = self.contact_store.get_active_jids(min_samples=self._CONSOLIDATION_THRESHOLD)
            needs = []
            for jid, name in active:
                existing_memory = self.memory.read_contact_memory(jid)
                if not existing_memory:
                    needs.append((jid, name))
            if needs:
                print(f"[memory] Startup check: {len(needs)} contacts have samples but no memory, consolidating...")
                await self._consolidate_memory()
                # Also run reflection on startup if needed
                if self.reflection and self.contact_store:
                    await self._run_self_reflection()
        except Exception as e:
            print(f"[memory] Startup consolidation check error: {e}")

    async def _consolidate_memory(self) -> None:
        """Background task: consolidate per-contact memory (isolated).

        Memory isolation: each contact's conversation samples are consolidated
        into their own MEMORY.md and HISTORY.md files. Contact A's memory
        is NEVER visible to Contact B.
        """
        if not self.memory or not self.contact_store:
            return
        try:
            self._message_count_since_consolidation = 0
            api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
            api_url = self.config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1/openai/v1")
            model = self.config.get("profile_model", "gpt-4.1-mini")

            # Get contacts with enough samples for consolidation
            active_contacts = self.contact_store.get_active_jids(min_samples=3)
            total = 0
            for jid, name in active_contacts:
                samples = self.contact_store.get_recent_samples(jid, limit=self._CONSOLIDATION_THRESHOLD)
                if not samples:
                    continue
                result = await self.memory.consolidate_contact(
                    jid, name, samples, api_url, api_key, model
                )
                if result["success"]:
                    total += result["messages_consolidated"]
                else:
                    print(f"[memory] Consolidation failed for {name}: {result.get('error', '?')}")
            if total:
                print(f"[memory] Consolidated {total} messages across {len(active_contacts)} contacts")
        except Exception as e:
            print(f"[memory] Consolidation error: {e}")

    async def _run_self_reflection(self) -> None:
        """Background task: LLM-powered self-reflection on recent interactions.

        Analyzes recent bot responses for mistakes, tone issues, and areas
        for improvement. Stores lessons in the reflection database.
        """
        if not self.reflection or not self.contact_store:
            return
        try:
            api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
            api_url = self.config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1/openai/v1")
            model = self.config.get("ai_model", "gpt-4.1-mini")
            if not api_key:
                return

            # Gather recent interactions from all active contacts
            recent_interactions = []
            active_jids = self.contact_store.get_active_jids(min_samples=2)
            for jid, name in active_jids[:10]:  # Max 10 contacts
                samples = self.contact_store.get_recent_samples(jid, limit=5)
                for s in samples:
                    recent_interactions.append({
                        "role": s.get("role", "?"),
                        "content": s.get("content", ""),
                        "contact_name": name or jid,
                    })

            if len(recent_interactions) < 4:
                return  # Not enough data to reflect on

            lessons = await self.reflection.reflect(
                recent_interactions, api_url, api_key, model
            )
            # Expire old lessons periodically (alongside reflection)
            expired = self.reflection.expire_old_lessons()
            if expired:
                print(f"[reflection] Expired {expired} old lessons")
        except Exception as e:
            print(f"[reflection] Self-reflection error: {e}")

    async def _handle_admin_command(self, chat_id: str, command: str, metadata: dict | None = None) -> None:
        """Handle an admin slash command via WhatsApp (Theorem T_ADMCMD).

        Admin messages starting with / are routed here instead of the AI.
        Modifying commands persist changes to config.json immediately.
        """
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/help":
            help_text = (
                "*Admin Commands*\n\n"
                "/status - Bot status\n"
                "/mode <auto_reply|monitor_only|ask_before_reply> - Change mode\n"
                "/tone <casual_friendly|professional|concise_direct|warm_empathetic> - Change tone\n"
                "/allow <number> - Add to allowlist\n"
                "/unallow <number> - Remove from allowlist\n"
                "/block <number> - Add to blocklist\n"
                "/unblock <number> - Remove from blocklist\n"
                "/pause - Quick switch to monitor_only\n"
                "/resume - Quick switch to auto_reply\n"
                "/contacts - List known contacts (with WhatsApp names)\n"
                "/findcontact <name> - Search contacts by name\n"
                "/addcontact <number> <name> - Save a contact name\n"
                "/removecontact <number> - Remove a contact\n"
                "/busy - Set status to busy (auto-reply with template)\n"
                "/dnd - Set status to DND\n"
                "/available - Clear status override\n"
                "/queue - Show message queue stats\n"
                "/escalate - Show pending escalations\n"
                "/respond ESC-XXX <answer> - Reply to escalation\n"
                "/template list|add|del - Manage reply templates\n"
                "/audit - Show recent audit events\n"
                "/health - System health (uptime, memory, rates)\n"
                "/heartbeat - Force maintenance tick now\n"
                "/identity - View/manage bot identity files (SOUL.md, USER.md)\n"
                "/profile <number> - View full contact card\n"
                "/groups - List monitored groups\n"
                "/groupsearch <query> - Search group messages (FTS5)\n"
                "/grouprecent [group] - Recent group messages\n"
                "/greply <group> <msg> - Send message to group (owner-approved)\n"
                "/quiet - Show quiet hours status\n"
                "/quiet on|off - Enable/disable quiet hours\n"
                "/quiet set <start> <end> <timezone> - Configure times\n"
                "/delete <msg_id> - Delete a message (or quote + /delete)\n"
                "/session - Session stats (or /session reset|<phone>)\n"
                "/historysync - History sync stats (or /historysync <phone>)\n"
                "/remind <minutes> <message> - Set a one-shot reminder\n"
                "/cron list - Show scheduled jobs\n"
                "/cron del <id> - Delete a scheduled job\n"
                "/cron every <minutes> <message> - Add recurring job\n"
                "/memory - View MEMORY.md (long-term facts)\n"
                "/memory history - View recent event log\n"
                "/memory consolidate - Force memory consolidation\n"
                "/memorysearch <query> - Search memory history\n"
                "/kg - Knowledge graph stats (or /kg search|extract)\n"
                "/takeover <number> [minutes] - Pause bot for a contact (default 30m)\n"
                "/takeover list|clear - View/clear active takeovers\n"
                "/template - View/switch business templates\n"
                "/broadcast <message> - Quick broadcast to all contacts\n"
                "/broadcast <segment> <message> - Broadcast to a segment\n"
                "/campaign - List campaigns (or /campaign <id> for details)\n"
                "/campaign start|pause|cancel <id> - Control a campaign\n"
                "/segment - List available segments\n"
                "/segment preview <id> - Preview contacts in a segment\n"
                "/tools - Tool calling status (or /tools on|off)\n"
                "/reflect - Reflection engine stats (lessons learned)\n"
                "/help - This message"
            )
            await self.channel.send_text(chat_id, help_text)

        elif cmd == "/status":
            profiles_count = len(self.contact_store.get_all_profiles()) if self.contact_store else 0
            quiet_status = self.quiet_hours.status() if self.quiet_hours else "Quiet hours: N/A"
            status_text = (
                f"*Bot Status*\n\n"
                f"Mode: {self.config.get('mode', '?')}\n"
                f"Tone: {self.config.get('tone', '?')}\n"
                f"Allowlist: {len(self.config.get('allowlist', []))} contacts\n"
                f"Blocklist: {len(self.config.get('blocklist', []))} contacts\n"
                f"Contact profiles: {profiles_count}\n"
                f"Model: {self.config.get('ai_model', '?')}\n"
                f"{quiet_status}"
            )
            await self.channel.send_text(chat_id, status_text)

        elif cmd == "/mode":
            valid_modes = {"auto_reply", "monitor_only", "ask_before_reply"}
            if args in valid_modes:
                self.config["mode"] = args
                save_config(self.config)
                print(f"[admin] Mode -> {args}")
                await self.channel.send_text(chat_id, f"Mode changed to: {args}")
            else:
                await self.channel.send_text(chat_id, f"Usage: /mode <{' | '.join(sorted(valid_modes))}>")

        elif cmd == "/tone":
            valid_tones = {"casual_friendly", "professional", "concise_direct", "warm_empathetic"}
            if args in valid_tones:
                self.config["tone"] = args
                save_config(self.config)
                self.system_prompt = build_system_prompt(self.config)
                print(f"[admin] Tone -> {args}")
                await self.channel.send_text(chat_id, f"Tone changed to: {args}")
            else:
                await self.channel.send_text(chat_id, f"Usage: /tone <{' | '.join(sorted(valid_tones))}>")

        elif cmd == "/allow":
            if args and args.replace("+", "").isdigit():
                num = "".join(c for c in args if c.isdigit())
                allowlist = self.config.get("allowlist", [])
                if num not in allowlist:
                    allowlist.append(num)
                    self.config["allowlist"] = allowlist
                    save_config(self.config)
                    print(f"[admin] Allowlist + {num}")
                    await self.channel.send_text(chat_id, f"Added {num} to allowlist")
                else:
                    await self.channel.send_text(chat_id, f"{num} already in allowlist")
            else:
                await self.channel.send_text(chat_id, "Usage: /allow <phone_number>")

        elif cmd == "/unallow":
            if args and args.replace("+", "").isdigit():
                num = "".join(c for c in args if c.isdigit())
                allowlist = self.config.get("allowlist", [])
                if num in allowlist:
                    allowlist.remove(num)
                    self.config["allowlist"] = allowlist
                    save_config(self.config)
                    print(f"[admin] Allowlist - {num}")
                    await self.channel.send_text(chat_id, f"Removed {num} from allowlist")
                else:
                    await self.channel.send_text(chat_id, f"{num} not in allowlist")
            else:
                await self.channel.send_text(chat_id, "Usage: /unallow <phone_number>")

        elif cmd == "/block":
            if args and args.replace("+", "").isdigit():
                num = "".join(c for c in args if c.isdigit())
                blocklist = self.config.get("blocklist", [])
                if num not in blocklist:
                    blocklist.append(num)
                    self.config["blocklist"] = blocklist
                    save_config(self.config)
                    print(f"[admin] Blocklist + {num}")
                    await self.channel.send_text(chat_id, f"Blocked {num}")
                else:
                    await self.channel.send_text(chat_id, f"{num} already blocked")
            else:
                await self.channel.send_text(chat_id, "Usage: /block <phone_number>")

        elif cmd == "/unblock":
            if args and args.replace("+", "").isdigit():
                num = "".join(c for c in args if c.isdigit())
                blocklist = self.config.get("blocklist", [])
                if num in blocklist:
                    blocklist.remove(num)
                    self.config["blocklist"] = blocklist
                    save_config(self.config)
                    print(f"[admin] Blocklist - {num}")
                    await self.channel.send_text(chat_id, f"Unblocked {num}")
                else:
                    await self.channel.send_text(chat_id, f"{num} not in blocklist")
            else:
                await self.channel.send_text(chat_id, "Usage: /unblock <phone_number>")

        elif cmd == "/pause":
            self.config["mode"] = "monitor_only"
            save_config(self.config)
            print("[admin] Paused (monitor_only)")
            await self.channel.send_text(chat_id, "Bot paused. Monitoring only. Use /resume to restart.")

        elif cmd == "/resume":
            self.config["mode"] = "auto_reply"
            save_config(self.config)
            print("[admin] Resumed (auto_reply)")
            await self.channel.send_text(chat_id, "Bot resumed. Auto-replying to allowed contacts.")

        elif cmd == "/contacts":
            if self.contact_store:
                # Show WhatsApp contacts with names (from sync) + profiled contacts
                wa_count = self.contact_store.get_whatsapp_contact_count()
                profiles = self.contact_store.get_all_profiles()
                wa_contacts = self.contact_store.get_all_whatsapp_contacts()

                lines = [f"*Contacts* ({wa_count} synced, {len(profiles)} profiled)\n"]

                # Show profiled contacts with best names
                if profiles:
                    lines.append("*Profiled:*")
                    for p in profiles[:20]:
                        name = self.contact_store.get_contact_name(p.jid)
                        rel = p.relationship if p.relationship != "unknown" else ""
                        lang = p.language if p.language != "en" else ""
                        details = " | ".join(filter(None, [rel, lang, p.tone]))
                        lines.append(f"- {name} ({p.jid}): {details}" if details else f"- {name} ({p.jid})")
                    if len(profiles) > 20:
                        lines.append(f"... and {len(profiles) - 20} more")

                # Show recent WhatsApp contacts without profiles
                profiled_jids = {p.jid for p in profiles}
                unprofiled = [c for c in wa_contacts if c["jid"] not in profiled_jids]
                if unprofiled:
                    lines.append(f"\n*WhatsApp contacts (no profile yet):*")
                    for c in unprofiled[:15]:
                        name = c.get("saved_name") or c.get("push_name") or c.get("verified_name", "")
                        source = "saved" if c.get("saved_name") else ("push" if c.get("push_name") else "biz")
                        lines.append(f"- {name} [{source}] ({c['jid']})")
                    if len(unprofiled) > 15:
                        lines.append(f"... and {len(unprofiled) - 15} more")

                if not profiles and not wa_contacts:
                    lines.append("No contacts yet. Names sync automatically from WhatsApp.")

                await self.channel.send_text(chat_id, "\n".join(lines))
            else:
                await self.channel.send_text(chat_id, "Contact store not initialized.")

        elif cmd == "/findcontact":
            if not self.contact_store:
                await self.channel.send_text(chat_id, "Contact store not initialized.")
            elif not args:
                await self.channel.send_text(chat_id, "Usage: /findcontact <name>")
            else:
                results = self.contact_store.resolve_contact_by_name(args, limit=10)
                if results:
                    lines = [f"*Contacts matching '{args}':*\n"]
                    for r in results:
                        source_tag = f"[{r['name_source']}]"
                        push = f" (push: {r['push_name']})" if r.get("push_name") and r["push_name"] != r["name"] else ""
                        lines.append(f"- {r['name']} {source_tag} {r['jid']}{push}")
                    await self.channel.send_text(chat_id, "\n".join(lines))
                else:
                    await self.channel.send_text(chat_id, f"No contacts found matching '{args}'.")

        elif cmd == "/addcontact":
            if not args or " " not in args:
                await self.channel.send_text(chat_id, "Usage: /addcontact <number> <full name>")
            else:
                num_part, name_part = args.split(" ", 1)
                num = "".join(c for c in num_part if c.isdigit())
                if not num or len(num) < 7:
                    await self.channel.send_text(chat_id, "Invalid phone number.")
                else:
                    jid = f"{num}@s.whatsapp.net"
                    # Save to WhatsApp via bridge
                    await self.channel.add_contact(jid, name_part.strip())
                    # Also save locally in our contact directory
                    if self.contact_store:
                        self.contact_store.update_whatsapp_name(num, saved_name=name_part.strip())
                    await self.channel.send_text(chat_id, f"Contact saved: {name_part.strip()} ({num})")
                    print(f"[admin] Added contact: {num} -> {name_part.strip()}")

        elif cmd == "/removecontact":
            if not args:
                await self.channel.send_text(chat_id, "Usage: /removecontact <number>")
            else:
                num = "".join(c for c in args if c.isdigit())
                if not num:
                    await self.channel.send_text(chat_id, "Invalid phone number.")
                else:
                    jid = f"{num}@s.whatsapp.net"
                    await self.channel.remove_contact(jid)
                    await self.channel.send_text(chat_id, f"Contact removal requested for {num}.")
                    print(f"[admin] Removed contact: {num}")

        # ── Intelligence layer admin commands ──

        elif cmd == "/busy":
            self.config["status_override"] = "busy"
            save_config(self.config)
            print("[admin] Status -> busy")
            await self.channel.send_text(chat_id, "Status set to *busy*. Low-priority messages get auto-reply template.")

        elif cmd == "/dnd":
            self.config["status_override"] = "dnd"
            save_config(self.config)
            print("[admin] Status -> dnd")
            await self.channel.send_text(chat_id, "Status set to *DND*. Only urgent messages get through.")

        elif cmd == "/available":
            self.config["status_override"] = ""
            save_config(self.config)
            print("[admin] Status -> available")
            await self.channel.send_text(chat_id, "Status cleared. All messages processed normally.")

        elif cmd == "/queue":
            if self.message_queue:
                stats = self.message_queue.stats()
                pending = self.message_queue.list_pending(limit=5)
                lines = [f"*Message Queue*\n"]
                for status_name, count in sorted(stats.items()):
                    if status_name != "total":
                        lines.append(f"  {status_name}: {count}")
                lines.append(f"  *total: {stats.get('total', 0)}*\n")
                if pending:
                    lines.append("*Recent pending:*")
                    for msg in pending:
                        lines.append(f"  [{msg['priority']}] {msg.get('sender_name', msg['sender_id'][:8])}: {msg.get('content_preview', '')}")
                await self.channel.send_text(chat_id, "\n".join(lines))
            else:
                await self.channel.send_text(chat_id, "Message queue not initialized.")

        elif cmd == "/escalate":
            if self.escalation:
                pending = self.escalation.pending()
                if pending:
                    lines = [f"*Pending Escalations ({len(pending)})*\n"]
                    for esc in pending[:10]:
                        name = esc.get("sender_name") or esc["sender_id"]
                        lines.append(f"  [{esc['code']}] From: {name}")
                        lines.append(f"    Q: {esc.get('question_preview', '?')}")
                    await self.channel.send_text(chat_id, "\n".join(lines))
                else:
                    await self.channel.send_text(chat_id, "No pending escalations.")
            else:
                await self.channel.send_text(chat_id, "Escalation engine not initialized.")

        elif cmd == "/respond":
            if not self.escalation:
                await self.channel.send_text(chat_id, "Escalation engine not initialized.")
            elif not args or len(args.split(maxsplit=1)) < 2:
                await self.channel.send_text(chat_id, "Usage: /respond ESC-XXX <your answer>")
            else:
                esc_parts = args.split(maxsplit=1)
                code = esc_parts[0].upper()
                answer = esc_parts[1]
                result = self.escalation.respond(code, answer)
                if result:
                    # Route answer back to original sender
                    sender_jid = f"{result['sender_id']}@s.whatsapp.net"
                    sender_name = result.get("sender_name", result["sender_id"])
                    reply_text = f"Re: your question - {answer}"
                    try:
                        await self.channel.send_text(sender_jid, reply_text)
                        await self.channel.send_text(chat_id, f"[{code}] Reply sent to {sender_name}")
                        if self.audit_log:
                            self.audit_log.log("escalation", chat_id, "outbound", len(answer), "", {"code": code})
                        # Learn from this escalation answer for future reuse
                        if self.reflection:
                            esc_record = self.escalation.get(code)
                            if esc_record:
                                self.reflection.record_escalation_answer(
                                    question=esc_record.get("question_preview", ""),
                                    answer=answer,
                                    contact_id=result["sender_id"],
                                )
                    except Exception as e:
                        await self.channel.send_text(chat_id, f"[{code}] Failed to send reply: {e}")
                else:
                    await self.channel.send_text(chat_id, f"Escalation {code} not found or already answered.")

        elif cmd == "/template":
            if not self.templates:
                await self.channel.send_text(chat_id, "Template engine not initialized.")
            elif not args:
                await self.channel.send_text(chat_id, "Usage: /template list | /template add <name> <text> | /template del <name>")
            else:
                sub_parts = args.split(maxsplit=2)
                sub_cmd = sub_parts[0].lower()
                if sub_cmd == "list":
                    templates = self.templates.list_templates()
                    lines = ["*Reply Templates*\n"]
                    for t in templates:
                        builtin = " (built-in)" if t.get("builtin") else ""
                        lines.append(f"  *{t['name']}*{builtin}: {t['text'][:60]}...")
                    await self.channel.send_text(chat_id, "\n".join(lines))
                elif sub_cmd == "add" and len(sub_parts) >= 3:
                    name = sub_parts[1]
                    text = sub_parts[2]
                    if self.templates.add_template(name, text):
                        await self.channel.send_text(chat_id, f"Template '{name}' saved.")
                    else:
                        await self.channel.send_text(chat_id, f"Failed to save template '{name}'.")
                elif sub_cmd == "del" and len(sub_parts) >= 2:
                    name = sub_parts[1]
                    if self.templates.delete_template(name):
                        await self.channel.send_text(chat_id, f"Template '{name}' deleted.")
                    else:
                        await self.channel.send_text(chat_id, f"Template '{name}' not found (built-in templates cannot be deleted).")
                else:
                    await self.channel.send_text(chat_id, "Usage: /template list | /template add <name> <text> | /template del <name>")

        elif cmd == "/profile":
            if not self.contact_store:
                await self.channel.send_text(chat_id, "Contact store not initialized.")
            elif not args:
                await self.channel.send_text(chat_id, "Usage: /profile <phone_number>")
            else:
                num = "".join(c for c in args if c.isdigit())
                profile = self.contact_store.get_profile(num)
                if profile:
                    samples = self.contact_store.get_sample_count(num)
                    best_name = self.contact_store.get_contact_name(num)
                    lines = [f"*Contact Card: {best_name}*\n"]
                    lines.append(f"JID: {profile.jid}")
                    # Show all known names
                    wa_names = self.contact_store._name_cache.get(num)
                    if wa_names:
                        if wa_names[1]:
                            lines.append(f"Saved name: {wa_names[1]}")
                        if wa_names[0]:
                            lines.append(f"Push name: {wa_names[0]}")
                        if wa_names[2]:
                            lines.append(f"Business: {wa_names[2]}")
                    if profile.display_name:
                        lines.append(f"AI-inferred name: {profile.display_name}")
                    lines.append(f"Relationship: {profile.relationship}")
                    lines.append(f"Tone: {profile.tone} (formality: {profile.formality:.1f})")
                    lines.append(f"Emoji: {profile.emoji_usage}")
                    lines.append(f"Msg length: {profile.avg_message_length}")
                    lines.append(f"Language: {', '.join(profile.languages_used)}")
                    lines.append(f"Frequency: {profile.interaction_frequency}")
                    if profile.topics:
                        lines.append(f"Topics: {', '.join(profile.topics[:5])}")
                    if profile.response_style:
                        lines.append(f"\nStyle: {profile.response_style}")
                    if profile.summary:
                        lines.append(f"\nSummary: {profile.summary}")
                    if profile.sample_phrases:
                        lines.append(f"\nPhrases: {', '.join(repr(p) for p in profile.sample_phrases[:5])}")
                    lines.append(f"\nProfile v{profile.profile_version} | {samples} samples | Updated: {profile.last_updated[:16] if profile.last_updated else '?'}")
                    await self.channel.send_text(chat_id, "\n".join(lines))
                else:
                    samples = self.contact_store.get_sample_count(num)
                    if samples:
                        await self.channel.send_text(chat_id, f"No profile yet for {num} ({samples} samples collected, needs {self.contact_store.MIN_SAMPLES_FOR_PROFILE})")
                    else:
                        await self.channel.send_text(chat_id, f"No data for {num}.")

        elif cmd == "/groups":
            if not self.contact_store:
                await self.channel.send_text(chat_id, "Contact store not initialized.")
            else:
                groups = self.contact_store.get_all_group_cards()
                if groups:
                    lines = [f"*Monitored Groups ({len(groups)})*\n"]
                    for g in groups[:15]:
                        name = g.get("group_name") or g["group_jid"][:20]
                        last = g.get("last_active", "?")[:16]
                        members = self.contact_store.get_group_active_members(g["group_jid"], limit=3)
                        member_names = [m.get("display_name") or m["sender_id"][:8] for m in members]
                        lines.append(f"- {name}")
                        lines.append(f"  Last active: {last}")
                        if member_names:
                            lines.append(f"  Top members: {', '.join(member_names)}")
                    await self.channel.send_text(chat_id, "\n".join(lines))
                else:
                    await self.channel.send_text(chat_id, "No group data collected yet.")

        elif cmd == "/groupsearch":
            if not self.contact_store:
                await self.channel.send_text(chat_id, "Contact store not initialized.")
            elif not args:
                await self.channel.send_text(chat_id,
                    "Usage: /groupsearch <query> [group_name]\n"
                    "Examples:\n"
                    "  /groupsearch meeting\n"
                    "  /groupsearch deadline Project Team")
            else:
                # Parse: query is everything, optionally last word is group name
                parts = args.rsplit(" ", 1)
                query = args
                group_jid = ""
                # Try last word as group name
                if len(parts) > 1:
                    maybe_group = self.contact_store.resolve_group_by_name(parts[1])
                    if maybe_group:
                        group_jid = maybe_group
                        query = parts[0]

                results = self.contact_store.search_group_messages(query, group_jid=group_jid, limit=15)
                if results:
                    group_cards = {g["group_jid"]: g.get("group_name", "") for g in self.contact_store.get_all_group_cards()}
                    lines = [f"*Group Search: '{query}'* ({len(results)} results)\n"]
                    for r in results:
                        gname = group_cards.get(r["group_jid"], r["group_jid"][:15])
                        ts = r.get("timestamp", "")[:16]
                        sender = r["sender_id"][:12]
                        lines.append(f"  [{ts}] {gname} | {sender}: {r['content'][:100]}")
                    await self.channel.send_text(chat_id, "\n".join(lines))
                else:
                    await self.channel.send_text(chat_id, f"No results for '{query}' in group messages.")

        elif cmd == "/grouprecent":
            if not self.contact_store:
                await self.channel.send_text(chat_id, "Contact store not initialized.")
            else:
                group_jid = ""
                if args:
                    group_jid = self.contact_store.resolve_group_by_name(args) or ""
                    if not group_jid:
                        await self.channel.send_text(chat_id, f"No group matching '{args}'. Use /groups to list.")
                        return

                messages = self.contact_store.get_recent_group_messages(group_jid=group_jid, limit=20)
                if messages:
                    group_cards = {g["group_jid"]: g.get("group_name", "") for g in self.contact_store.get_all_group_cards()}
                    header = f"group '{args}'" if args else "all groups"
                    lines = [f"*Recent Messages ({header}, {len(messages)})*\n"]
                    for m in reversed(messages):  # Show oldest first
                        gname = group_cards.get(m["group_jid"], m["group_jid"][:15])
                        ts = m.get("timestamp", "")[:16]
                        sender = m["sender_id"][:12]
                        lines.append(f"  [{ts}] {gname} | {sender}: {m['content'][:100]}")
                    await self.channel.send_text(chat_id, "\n".join(lines))
                else:
                    await self.channel.send_text(chat_id, "No group messages collected yet.")

        elif cmd == "/greply":
            if not self.contact_store or not self.channel:
                await self.channel.send_text(chat_id, "Contact store or channel not initialized.")
            elif not args:
                await self.channel.send_text(chat_id,
                    "Usage: /greply <group_name> <message>\n"
                    "Examples:\n"
                    "  /greply Project Team Hello everyone!\n"
                    "  /greply Finance The report is ready.\n\n"
                    "Use /groups to list available groups.")
            else:
                # Parse: try to find group name in first 1-3 words
                group_jid = None
                message_text = ""
                words = args.split()
                # Try progressively longer group name prefixes (1, 2, 3 words)
                for n in range(min(3, len(words)), 0, -1):
                    candidate = " ".join(words[:n])
                    resolved = self.contact_store.resolve_group_by_name(candidate)
                    if resolved:
                        group_jid = resolved
                        message_text = " ".join(words[n:])
                        break

                if not group_jid:
                    await self.channel.send_text(chat_id,
                        f"Could not find a group matching the name. Use /groups to list.")
                elif not message_text.strip():
                    group_card = self.contact_store.get_group_card(group_jid)
                    gname = group_card.get("group_name", group_jid) if group_card else group_jid
                    await self.channel.send_text(chat_id, f"No message to send to '{gname}'. Usage: /greply <group> <message>")
                else:
                    group_card = self.contact_store.get_group_card(group_jid)
                    gname = group_card.get("group_name", group_jid) if group_card else group_jid
                    # Send with owner-approved flag (bypasses bridge group gate)
                    await self.channel.send_text_owner_approved(group_jid, message_text.strip())
                    # Audit the group send
                    if self.audit_log:
                        self.audit_log.log("msg_out", group_jid, "outbound", len(message_text),
                                          "", {"owner_approved": True, "group_name": gname})
                    await self.channel.send_text(chat_id, f"Sent to group '{gname}' ({len(message_text)} chars)")

        elif cmd == "/quiet":
            if not self.quiet_hours:
                await self.channel.send_text(chat_id, "Quiet hours system not initialized.")
            elif not args:
                # Show status
                await self.channel.send_text(chat_id, self.quiet_hours.status())
            elif args.lower() == "on":
                self.config["quiet_hours_enabled"] = True
                save_config(self.config)
                self.quiet_hours.update_config(self.config)
                print("[admin] Quiet hours -> enabled")
                await self.channel.send_text(chat_id, f"Quiet hours enabled ({self.config['quiet_hours_start']}-{self.config['quiet_hours_end']} {self.config['quiet_hours_timezone']})")
            elif args.lower() == "off":
                self.config["quiet_hours_enabled"] = False
                save_config(self.config)
                self.quiet_hours.update_config(self.config)
                print("[admin] Quiet hours -> disabled")
                # Flush any queued alerts immediately
                if self.quiet_hours.queue_size > 0:
                    queued = self.quiet_hours.check_and_flush()
                    if not queued:
                        # Force flush since check_and_flush won't work when disabled
                        queued = list(self.quiet_hours._queue)
                        self.quiet_hours._queue.clear()
                    if queued:
                        digest = self.quiet_hours.format_digest(queued)
                        if digest:
                            await self.channel.send_text(chat_id, f"Quiet hours disabled. Flushing queued alerts:\n\n{digest}")
                            return
                await self.channel.send_text(chat_id, "Quiet hours disabled.")
            elif args.lower().startswith("set"):
                # /quiet set 23:00 07:00 Asia/Hong_Kong
                set_parts = args.split()
                if len(set_parts) >= 4:
                    start_time = set_parts[1]
                    end_time = set_parts[2]
                    timezone = set_parts[3]
                    # Validate time format
                    import re
                    time_re = re.compile(r"^\d{1,2}:\d{2}$")
                    if not time_re.match(start_time) or not time_re.match(end_time):
                        await self.channel.send_text(chat_id, "Invalid time format. Use HH:MM (e.g. 23:00)")
                        return
                    # Validate timezone
                    try:
                        from zoneinfo import ZoneInfo
                        ZoneInfo(timezone)
                    except Exception:
                        await self.channel.send_text(chat_id, f"Invalid timezone: {timezone}\nExamples: UTC, Asia/Hong_Kong, America/New_York")
                        return
                    self.config["quiet_hours_start"] = start_time
                    self.config["quiet_hours_end"] = end_time
                    self.config["quiet_hours_timezone"] = timezone
                    save_config(self.config)
                    self.quiet_hours.update_config(self.config)
                    print(f"[admin] Quiet hours -> {start_time}-{end_time} {timezone}")
                    await self.channel.send_text(chat_id, f"Quiet hours set to {start_time}-{end_time} {timezone}")
                else:
                    await self.channel.send_text(chat_id, "Usage: /quiet set <start> <end> <timezone>\nExample: /quiet set 23:00 07:00 Asia/Hong_Kong")
            else:
                await self.channel.send_text(chat_id, "Usage: /quiet | /quiet on | /quiet off | /quiet set <start> <end> <timezone>")

        elif cmd == "/memory":
            if not self.memory:
                await self.channel.send_text(chat_id, "Memory system not initialized.")
            elif args == "consolidate":
                asyncio.create_task(self._consolidate_memory())
                await self.channel.send_text(chat_id, "Memory consolidation triggered (background).")
            elif args == "history":
                history = self.memory.get_recent_history(max_entries=10, max_chars=3000)
                if history:
                    await self.channel.send_text(chat_id, f"*Recent Memory History*\n\n{history}")
                else:
                    await self.channel.send_text(chat_id, "No memory history entries yet.")
            else:
                mem = self.memory.read_long_term()
                if mem:
                    # Truncate for WhatsApp if needed
                    if len(mem) > 3500:
                        mem = mem[:3500] + "\n\n... (truncated)"
                    await self.channel.send_text(chat_id, f"*MEMORY.md*\n\n{mem}")
                else:
                    await self.channel.send_text(chat_id, "MEMORY.md is empty. Consolidation runs automatically every 30 messages, or use /memory consolidate.")

        elif cmd == "/memorysearch":
            if not self.memory_search:
                await self.channel.send_text(chat_id, "Memory search not initialized.")
            elif not args:
                await self.channel.send_text(chat_id, "Usage: /memorysearch <query>\nExamples: /memorysearch API last week\n/memorysearch project plans")
            else:
                summary = self.memory_search.get_summary(args, max_results=5)
                await self.channel.send_text(chat_id, f"*Memory Search*\n\n{summary}")

        elif cmd == "/health":
            if self.health_monitor:
                await self.channel.send_text(chat_id, self.health_monitor.format_status())
            else:
                await self.channel.send_text(chat_id, "Health monitor not initialized.")

        elif cmd == "/heartbeat":
            if self.heartbeat:
                status = self.heartbeat.status()
                lines = [
                    "*Heartbeat Service*\n",
                    f"  Enabled: {status['enabled']}",
                    f"  Running: {status['running']}",
                    f"  Interval: {status['interval_s']}s",
                    f"  Ticks: {status['tick_count']}",
                    f"  Tasks: {', '.join(status['registered_tasks'])}",
                    "\nForcing maintenance tick now...",
                ]
                await self.channel.send_text(chat_id, "\n".join(lines))
                await self.heartbeat.force_tick()
                await self.channel.send_text(chat_id, "Maintenance tick complete.")
            else:
                await self.channel.send_text(chat_id, "Heartbeat service not initialized.")

        elif cmd == "/identity":
            if self.context_builder:
                if not args:
                    # Show identity file summary
                    await self.channel.send_text(chat_id, self.context_builder.get_identity_summary())
                elif args.startswith("show "):
                    # Show content of a specific identity file
                    filename = args[5:].strip()
                    if filename not in ("SOUL.md", "USER.md"):
                        await self.channel.send_text(chat_id, "Usage: /identity show SOUL.md or /identity show USER.md")
                    else:
                        content = self.context_builder._load_file(filename)
                        if content:
                            # Truncate for WhatsApp readability
                            if len(content) > 1500:
                                content = content[:1500] + "\n...(truncated)"
                            await self.channel.send_text(chat_id, f"*{filename}*\n\n{content}")
                        else:
                            await self.channel.send_text(chat_id, f"{filename} is empty.")
                else:
                    await self.channel.send_text(chat_id,
                        "*Identity Commands*\n"
                        "  /identity - Show identity file summary\n"
                        "  /identity show SOUL.md - View bot personality\n"
                        "  /identity show USER.md - View owner profile\n\n"
                        f"Edit files directly at:\n{self.context_builder.identity_dir}")
            else:
                await self.channel.send_text(chat_id, "Context builder not initialized.")

        elif cmd == "/audit":
            if self.audit_log:
                events = self.audit_log.recent(limit=10)
                if events:
                    lines = [f"*Recent Audit Events ({len(events)})*\n"]
                    for e in events:
                        lines.append(f"  [{e['timestamp'][:16]}] {e['event_type']} {e['direction'] or ''} ({e['content_length']} chars)")
                    await self.channel.send_text(chat_id, "\n".join(lines))
                else:
                    await self.channel.send_text(chat_id, "No audit events yet.")
            else:
                await self.channel.send_text(chat_id, "Audit log not initialized.")

        elif cmd == "/remind":
            if not self.cron:
                await self.channel.send_text(chat_id, "Cron service not initialized.")
                return
            if not args:
                await self.channel.send_text(chat_id, "Usage: /remind <minutes> <message>\nExample: /remind 30 Check the oven")
                return
            # Parse: /remind <minutes> <message>
            remind_parts = args.split(maxsplit=1)
            if len(remind_parts) < 2:
                await self.channel.send_text(chat_id, "Usage: /remind <minutes> <message>")
                return
            try:
                minutes = float(remind_parts[0])
                if minutes <= 0 or minutes > 525600:  # max 1 year
                    await self.channel.send_text(chat_id, "Minutes must be between 1 and 525600 (1 year).")
                    return
            except ValueError:
                await self.channel.send_text(chat_id, f"Invalid number: {remind_parts[0]}")
                return
            message = remind_parts[1]
            import time as _time
            run_at = _time.time() + (minutes * 60)
            job = self.cron.add_reminder(
                name=f"Reminder ({int(minutes)}m)",
                message=message,
                run_at=run_at,
                target_chat=chat_id,
            )
            from datetime import datetime
            dt = datetime.fromtimestamp(run_at)
            await self.channel.send_text(
                chat_id,
                f"Reminder set [{job['id']}]\n"
                f"  In: {int(minutes)} minutes\n"
                f"  At: {dt.strftime('%Y-%m-%d %H:%M')}\n"
                f"  Msg: {message[:80]}"
            )

        elif cmd == "/cron":
            if not self.cron:
                await self.channel.send_text(chat_id, "Cron service not initialized.")
                return
            if not args:
                # Show usage
                await self.channel.send_text(
                    chat_id,
                    "*Cron Commands*\n\n"
                    "/cron list - Show all scheduled jobs\n"
                    "/cron del <id> - Delete a job\n"
                    "/cron every <minutes> <message> - Add recurring job\n"
                    "/remind <minutes> <message> - One-shot reminder"
                )
                return
            cron_parts = args.split(maxsplit=1)
            subcmd = cron_parts[0].lower()
            cron_args = cron_parts[1].strip() if len(cron_parts) > 1 else ""

            if subcmd == "list":
                await self.channel.send_text(chat_id, self.cron.format_job_list())

            elif subcmd == "del":
                if not cron_args:
                    await self.channel.send_text(chat_id, "Usage: /cron del <job_id>")
                    return
                removed = self.cron.remove_job(cron_args.strip())
                if removed:
                    await self.channel.send_text(chat_id, f"Job {cron_args.strip()} deleted.")
                else:
                    await self.channel.send_text(chat_id, f"Job {cron_args.strip()} not found.")

            elif subcmd == "every":
                # /cron every <minutes> <message>
                every_parts = cron_args.split(maxsplit=1)
                if len(every_parts) < 2:
                    await self.channel.send_text(chat_id, "Usage: /cron every <minutes> <message>")
                    return
                try:
                    interval_m = float(every_parts[0])
                    if interval_m < 1 or interval_m > 525600:
                        await self.channel.send_text(chat_id, "Interval must be 1-525600 minutes.")
                        return
                except ValueError:
                    await self.channel.send_text(chat_id, f"Invalid number: {every_parts[0]}")
                    return
                message = every_parts[1]
                interval_s = interval_m * 60
                job = self.cron.add_recurring(
                    name=f"Recurring ({int(interval_m)}m)",
                    message=message,
                    interval_s=interval_s,
                    target_chat=chat_id,
                )
                if interval_m >= 60:
                    schedule_str = f"every {interval_m / 60:.1f}h"
                else:
                    schedule_str = f"every {int(interval_m)}m"
                await self.channel.send_text(
                    chat_id,
                    f"Recurring job set [{job['id']}]\n"
                    f"  Schedule: {schedule_str}\n"
                    f"  Msg: {message[:80]}"
                )

            else:
                await self.channel.send_text(chat_id, f"Unknown cron subcommand: {subcmd}\nUse /cron for help.")

        elif cmd == "/delete":
            # Delete a message (delete-for-everyone)
            # Two modes:
            # 1. /delete <msg_id> - delete by explicit message ID
            # 2. Quote a message + /delete - delete the quoted message
            _meta = metadata or {}
            quoted_msg_id = _meta.get("quoted_message_id", "")
            quoted_participant = _meta.get("quoted_participant", "")

            if args:
                # Explicit message ID
                msg_id_to_delete = args.strip()
                # Assume fromMe=True (deleting bot's own message) unless prefixed with "their:"
                from_me = True
                if msg_id_to_delete.startswith("their:"):
                    msg_id_to_delete = msg_id_to_delete[6:].strip()
                    from_me = False
                deleted = await self.channel.delete_message(chat_id, msg_id_to_delete, from_me=from_me)
                if deleted:
                    await self.channel.send_text(chat_id, f"Delete sent for msg {msg_id_to_delete}")
                    if self.audit_log:
                        self.audit_log.log("msg_delete", chat_id, "outbound", 0, msg_id_to_delete, {"from_me": from_me})
            elif quoted_msg_id:
                # Delete the quoted message
                # Determine fromMe: if quoted_participant is empty or matches our own JID, it's ours
                from_me = not quoted_participant or not quoted_participant.strip()
                deleted = await self.channel.delete_message(chat_id, quoted_msg_id, from_me=from_me, participant=quoted_participant)
                if deleted:
                    await self.channel.send_text(chat_id, f"Delete sent for quoted msg {quoted_msg_id}")
                    if self.audit_log:
                        self.audit_log.log("msg_delete", chat_id, "outbound", 0, quoted_msg_id, {"from_me": from_me})
            else:
                await self.channel.send_text(
                    chat_id,
                    "*Delete Message*\n\n"
                    "Usage:\n"
                    "  /delete <msg_id> - Delete bot's message by ID\n"
                    "  /delete their:<msg_id> - Delete other's message by ID\n"
                    "  Reply-quote a message + /delete - Delete the quoted message"
                )

        elif cmd == "/session":
            if not self.session_mgr:
                await self.channel.send_text(chat_id, "Session manager not initialized.")
                return
            if not args:
                # Show stats + active sessions
                stats = self.session_mgr.stats()
                active = self.session_mgr.get_active_sessions(10)
                session_list = self.session_mgr.format_session_list(active)
                await self.channel.send_text(
                    chat_id,
                    f"*Session Manager*\n\n"
                    f"Total sessions: {stats['total_sessions']}\n"
                    f"Active: {stats['active_sessions']}\n"
                    f"Total messages: {stats['total_messages']}\n"
                    f"Timeout: {stats['timeout_minutes']}min\n\n"
                    f"*Recent Sessions:*\n{session_list}"
                )
            elif args.strip() == "reset":
                # Reset all in-memory histories
                count = len(self.chat_histories)
                self.chat_histories.clear()
                await self.channel.send_text(chat_id, f"Cleared {count} in-memory chat histories.")
            else:
                # Show specific contact session
                target = args.strip().replace("+", "")
                sess = self.session_mgr.get_session(target)
                if sess:
                    await self.channel.send_text(
                        chat_id,
                        f"*Session for {target}*\n\n"
                        f"Start: {sess['session_start'][:16]}\n"
                        f"Last: {sess['last_activity'][:16]}\n"
                        f"Messages: {sess['message_count']}\n"
                        f"Topic: {sess['topic'] or '(none)'}"
                    )
                else:
                    await self.channel.send_text(chat_id, f"No session found for {target}")

        elif cmd == "/historysync":
            # Show history sync stats or request on-demand fetch
            stats = self._history_sync_stats
            if not args:
                await self.channel.send_text(
                    chat_id,
                    f"*History Sync Stats*\n\n"
                    f"Syncs received: {stats['syncs_received']}\n"
                    f"Messages stored: {stats['total_stored']}\n"
                    f"Messages skipped: {stats['total_skipped']}\n\n"
                    f"Use /historysync <phone> to fetch history for a contact."
                )
            else:
                # On-demand fetch for a specific contact
                target = args.strip().replace("+", "")
                target_jid = f"{target}@s.whatsapp.net" if "@" not in target else target
                fetched = await self.channel.fetch_history(target_jid, 50)
                if fetched:
                    await self.channel.send_text(chat_id, f"Requested history for {target}. Results will be stored automatically.")
                else:
                    await self.channel.send_text(chat_id, "Failed to request history (bridge not connected).")

        elif cmd == "/kg":
            if not self.kg:
                await self.channel.send_text(chat_id, "Knowledge graph not initialized.")
                return
            if not args or args.strip() == "stats":
                await self.channel.send_text(chat_id, self.kg.format_stats())
            elif args.strip().startswith("search"):
                query = args.strip()[6:].strip()
                if not query:
                    await self.channel.send_text(chat_id, "Usage: /kg search <query>")
                    return
                results = self.kg.search_entities(query)
                if not results:
                    await self.channel.send_text(chat_id, f"No entities found for: {query}")
                else:
                    lines = [f"*Entity Search: {query}*\n"]
                    for e in results[:15]:
                        line = f"  {e['name']} ({e['entity_type']}) [{e['mention_count']}x]"
                        if e.get("description"):
                            line += f"\n    {e['description'][:80]}"
                        lines.append(line)
                    await self.channel.send_text(chat_id, "\n".join(lines))
            elif args.strip() == "extract":
                api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
                api_url = self.config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1/openai/v1")
                if not api_key:
                    await self.channel.send_text(chat_id, "AI_GATEWAY_API_KEY not set.")
                    return
                await self.channel.send_text(chat_id, "Running KG extraction...")
                total_extracted = 0
                jids = [r[0] for r in self.kg._conn.execute(
                    "SELECT DISTINCT jid FROM conversation_samples"
                ).fetchall()]
                for jid in jids:
                    samples = self.kg.get_unprocessed_samples(jid)
                    if samples:
                        result = await self.kg.extract_from_samples(jid, samples, api_url, api_key)
                        total_extracted += result.get("entities_created", 0) + result.get("entities_updated", 0)
                await self.channel.send_text(
                    chat_id,
                    f"Extraction complete. Processed {len(jids)} contacts, {total_extracted} entities extracted/updated."
                )
            else:
                await self.channel.send_text(chat_id, "Usage: /kg [stats|search <query>|extract]")

        elif cmd == "/reflect":
            if not self.reflection:
                await self.channel.send_text(chat_id, "Reflection engine not initialized.")
            elif args.strip() == "run":
                await self.channel.send_text(chat_id, "Running self-reflection...")
                asyncio.create_task(self._run_self_reflection())
            else:
                stats = self.reflection.get_stats()
                lessons_text = self.reflection.get_lessons_for_prompt()
                text = (
                    f"*Reflection Engine*\n\n"
                    f"Total lessons: {stats['total_lessons']}\n"
                    f"  From corrections: {stats['from_corrections']}\n"
                    f"  From self-reflection: {stats['from_reflections']}\n"
                    f"Cached escalation answers: {stats['escalation_answers_cached']}\n"
                    f"Recent alerts tracked: {len(self._recent_escalation_alerts)}\n\n"
                )
                if lessons_text:
                    text += f"*Active Lessons:*\n{lessons_text[:2000]}"
                else:
                    text += "No active lessons yet."
                text += "\n\nForce self-reflection: /reflect run"
                await self.channel.send_text(chat_id, text)

        elif cmd == "/takeover":
            # /takeover <number> [minutes] - temporarily disable bot for a contact
            # /takeover list - show active takeovers
            # /takeover clear - clear all takeovers
            import time as _time
            if args.strip() == "list":
                if not self._takeover_contacts:
                    await self.channel.send_text(chat_id, "No active takeovers.")
                else:
                    lines = ["*Active Takeovers*\n"]
                    for jid, exp in self._takeover_contacts.items():
                        remaining = int(exp - _time.time())
                        if remaining > 0:
                            name = self.contact_store.get_contact_name(jid) if self.contact_store else jid
                            lines.append(f"- {name} ({jid}): {remaining // 60}m {remaining % 60}s left")
                    await self.channel.send_text(chat_id, "\n".join(lines))
            elif args.strip() == "clear":
                count = len(self._takeover_contacts)
                self._takeover_contacts.clear()
                await self.channel.send_text(chat_id, f"Cleared {count} takeover(s).")
            elif args:
                takeover_parts = args.split()
                number = "".join(c for c in takeover_parts[0] if c.isdigit())
                minutes = 30  # default 30 minutes
                if len(takeover_parts) > 1:
                    try:
                        minutes = int(takeover_parts[1])
                    except ValueError:
                        pass
                if number:
                    jid = f"{number}@s.whatsapp.net"
                    self._takeover_contacts[jid] = _time.time() + (minutes * 60)
                    name = self.contact_store.get_contact_name(jid) if self.contact_store else number
                    await self.channel.send_text(chat_id, f"Took over {name} for {minutes} minutes. Bot will not reply to them.")
                    print(f"[admin] Takeover: {name} ({jid}) for {minutes}m")
                else:
                    await self.channel.send_text(chat_id, "Usage: /takeover <number> [minutes]\n/takeover list\n/takeover clear")
            else:
                await self.channel.send_text(chat_id, "Usage: /takeover <number> [minutes]\n/takeover list\n/takeover clear")

        elif cmd == "/template":
            from src.business_templates import get_template, get_all_template_names, get_soul_md, apply_template
            current = self.config.get("business_template", "")
            if not args.strip():
                # Show current template and list available ones
                tmpl_list = get_all_template_names()
                lines = ["*Business Templates*\n"]
                if current:
                    ct = get_template(current)
                    lines.append(f"Current: {ct['name'] if ct else current}\n")
                else:
                    lines.append("Current: None (generic)\n")
                lines.append("Available templates:")
                for t in tmpl_list:
                    marker = " (active)" if t["id"] == current else ""
                    lines.append(f"  - {t['id']}: {t['name']} -- {t['description']}{marker}")
                lines.append("\nUsage: /template <id> to switch")
                await self.channel.send_text(chat_id, "\n".join(lines))
            else:
                new_id = resolve_business_type(args.strip())
                new_tmpl = get_template(new_id)
                if not new_tmpl:
                    await self.channel.send_text(chat_id, f"Unknown template: {args.strip()}\nUse /template to see available options.")
                else:
                    # Apply template: update config + rewrite SOUL.md
                    apply_template(new_tmpl, self.config)
                    save_config(self.config)
                    # Rewrite SOUL.md with template content
                    soul_content = get_soul_md(new_id)
                    if soul_content:
                        self.context_builder.update_identity_file("SOUL.md", soul_content)
                    await self.channel.send_text(
                        chat_id,
                        f"Switched to *{new_tmpl['name']}* template.\n"
                        f"Tone: {new_tmpl['config_overrides'].get('tone', 'unchanged')}\n"
                        f"SOUL.md updated with {new_tmpl['name']} personality.\n"
                        f"Integrations: {', '.join(new_tmpl['config_overrides'].get('enabled_integrations', ['core']))}"
                    )

        elif cmd == "/broadcast":
            if not self.broadcast:
                await self.channel.send_text(chat_id, "Broadcast engine not initialized.")
                return
            if not args:
                await self.channel.send_text(chat_id,
                    "Usage:\n"
                    "/broadcast <message> - Send to all contacts\n"
                    "/broadcast <segment> <message> - Send to a segment\n\n"
                    "Segments: all_contacts, active, recent, dormant, new_contacts, repeat_contacts, high_engagement"
                )
                return
            # Parse: /broadcast <segment_id> <message> OR /broadcast <message>
            first_word = args.split(maxsplit=1)[0].lower()
            segment_id = "all_contacts"
            message_text = args
            all_seg_ids = set(AUTO_SEGMENTS.keys())
            # Check custom segments too
            if self._broadcast_store:
                for seg in self._broadcast_store.list_segments():
                    all_seg_ids.add(seg.id)
            if first_word in all_seg_ids:
                segment_id = first_word
                message_text = args.split(maxsplit=1)[1] if len(args.split(maxsplit=1)) > 1 else ""
            if not message_text:
                await self.channel.send_text(chat_id, "Please provide a message to broadcast.")
                return
            try:
                from datetime import datetime as _dt_now
                campaign = await self.broadcast.create_campaign(
                    name=f"Quick broadcast ({_dt_now.now().strftime('%m/%d %H:%M')})",
                    message_template=message_text,
                    segment_id=segment_id,
                    personalize=True,
                    created_by=chat_id.split("@")[0],
                )
                result = await self.broadcast.start_campaign(campaign.id)
                await self.channel.send_text(chat_id,
                    f"Broadcast {campaign.id} created!\n"
                    f"Segment: {campaign.segment_name}\n"
                    f"Recipients: {campaign.total_recipients}\n"
                    f"{result}"
                )
            except ValueError as e:
                await self.channel.send_text(chat_id, f"Broadcast error: {e}")
            except Exception as e:
                await self.channel.send_text(chat_id, f"Broadcast failed: {type(e).__name__}: {e}")

        elif cmd == "/campaign":
            if not self.broadcast:
                await self.channel.send_text(chat_id, "Broadcast engine not initialized.")
                return
            if not args:
                # List recent campaigns
                campaigns = self.broadcast.store.list_campaigns(limit=10)
                if not campaigns:
                    await self.channel.send_text(chat_id, "No campaigns yet. Use /broadcast to create one.")
                    return
                lines = ["*Recent Campaigns*\n"]
                for c in campaigns:
                    emoji = {"draft": "📝", "sending": "📤", "completed": "✅", "paused": "⏸", "cancelled": "❌", "scheduled": "⏰"}.get(c.status, "❓")
                    lines.append(f"{emoji} [{c.id}] {c.name}")
                    lines.append(f"   {c.status} | {c.sent_count}/{c.total_recipients} sent | {c.replied_count} replies")
                lines.append(f"\nUse /campaign <id> for details")
                await self.channel.send_text(chat_id, "\n".join(lines))
                return
            # Sub-commands: start, pause, cancel, delete, or show detail
            sub_parts = args.split(maxsplit=1)
            sub_cmd = sub_parts[0].lower()
            sub_args = sub_parts[1].strip() if len(sub_parts) > 1 else ""
            if sub_cmd == "start" and sub_args:
                result = await self.broadcast.start_campaign(sub_args)
                await self.channel.send_text(chat_id, result)
            elif sub_cmd == "pause" and sub_args:
                result = await self.broadcast.pause_campaign(sub_args)
                await self.channel.send_text(chat_id, result)
            elif sub_cmd == "cancel" and sub_args:
                result = await self.broadcast.cancel_campaign(sub_args)
                await self.channel.send_text(chat_id, result)
            elif sub_cmd == "delete" and sub_args:
                ok = await self.broadcast.store.delete_campaign(sub_args)
                await self.channel.send_text(chat_id,
                    f"Campaign {sub_args} deleted." if ok else f"Campaign {sub_args} not found."
                )
            else:
                # Treat as campaign_id for detail view
                campaign_id = args.strip()
                report = self.broadcast.get_campaign_report(campaign_id)
                await self.channel.send_text(chat_id, report)

        elif cmd == "/segment":
            if not self.broadcast:
                await self.channel.send_text(chat_id, "Broadcast engine not initialized.")
                return
            if not args:
                # List all segments
                segments = self.broadcast.store.list_segments()
                lines = ["*Available Segments*\n"]
                for seg in segments:
                    contacts = self.broadcast.segmentation.resolve_segment(seg)
                    tag = "auto" if seg.segment_type == "auto" else "custom"
                    lines.append(f"  [{seg.id}] {seg.name} ({len(contacts)} contacts) [{tag}]")
                    if seg.description:
                        lines.append(f"    {seg.description}")
                lines.append(f"\nUse /segment preview <id> to see contacts")
                await self.channel.send_text(chat_id, "\n".join(lines))
                return
            sub_parts = args.split(maxsplit=1)
            sub_cmd = sub_parts[0].lower()
            sub_args = sub_parts[1].strip() if len(sub_parts) > 1 else ""
            if sub_cmd == "preview" and sub_args:
                segment = self.broadcast.store.get_segment(sub_args)
                if not segment:
                    await self.channel.send_text(chat_id, f"Segment '{sub_args}' not found.")
                    return
                preview = self.broadcast.segmentation.get_segment_preview(segment, max_show=15)
                await self.channel.send_text(chat_id, preview)
            else:
                await self.channel.send_text(chat_id,
                    "Usage:\n/segment - List all segments\n/segment preview <id> - Preview contacts"
                )

        elif cmd == "/tools":
            enabled = self.config.get("tool_calling_enabled", True)
            status = "enabled" if enabled else "disabled"
            tool_defs = self.tool_executor.get_tool_definitions() if self.tool_executor else TOOL_DEFINITIONS
            tool_names = [t["function"]["name"] for t in tool_defs]
            text = (
                f"*Tool Calling: {status}*\n\n"
                f"Available tools ({len(tool_names)}):\n"
                + "\n".join(f"  - {name}" for name in tool_names)
                + "\n\nToggle: /tools on|off"
            )
            if args.strip() == "on":
                self.config["tool_calling_enabled"] = True
                save_config(self.config)
                text = "Tool calling enabled."
            elif args.strip() == "off":
                self.config["tool_calling_enabled"] = False
                save_config(self.config)
                text = "Tool calling disabled."
            await self.channel.send_text(chat_id, text)

        else:
            await self.channel.send_text(chat_id, f"Unknown command: {cmd}\nType /help for available commands.")

    async def _handle_contacts_sync(self, contacts: list[dict]) -> None:
        """Handle contact sync events from the Baileys bridge.

        Receives contact data (pushName, saved name, verified name) from:
        - Initial history sync (messaging-history.set contacts array)
        - Incremental updates (contacts.update events on incoming messages)
        - Full contact upserts (contacts.upsert events)
        """
        if not self.contact_store:
            return
        try:
            count = await self.contact_store.sync_contacts(contacts)
            if count > 0:
                print(f"[contacts-sync] Synced {count} contact names")
        except Exception as e:
            print(f"[contacts-sync] Error: {e}")

    async def _handle_history_sync(
        self, messages: list[dict], sync_type: int, progress: float | None, is_latest: bool
    ) -> None:
        """Handle history sync messages from the bridge.

        Stores synced messages into conversation_samples for RAG context enrichment.
        Only stores DM messages (not group), deduplicates by checking existing timestamps.
        """
        if not self.contact_store:
            return

        stored = 0
        skipped = 0
        sync_names = {0: "INITIAL", 1: "STATUS", 2: "FULL", 3: "RECENT", 4: "PUSH_NAME", 5: "NON_BLOCKING", 6: "ON_DEMAND"}
        sync_name = sync_names.get(sync_type, f"TYPE_{sync_type}")

        for msg in messages:
            try:
                chat_jid = msg.get("chatJid", "")
                content = msg.get("content", "")
                from_me = msg.get("fromMe", False)
                timestamp = msg.get("timestamp", 0)

                # Skip empty, group messages, and status broadcasts
                if not content or not chat_jid:
                    skipped += 1
                    continue
                if chat_jid.endswith("@g.us") or chat_jid == "status@broadcast":
                    skipped += 1
                    continue
                # Skip very short content (system messages, reactions)
                if len(content.strip()) < 2:
                    skipped += 1
                    continue

                # Determine JID and role
                jid = chat_jid.split("@")[0] if "@" in chat_jid else chat_jid
                role = "assistant" if from_me else "user"

                # Convert epoch timestamp to ISO format
                from datetime import datetime, timezone
                ts_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat() if timestamp else ""

                # Store to conversation_samples (contact_store handles dedup implicitly)
                await self.contact_store.store_sample(jid, role, content[:2000], ts_str)
                stored += 1

            except Exception as e:
                skipped += 1
                if stored == 0 and skipped < 3:
                    print(f"[history-sync] Error processing message: {e}")

        self._history_sync_stats["total_stored"] += stored
        self._history_sync_stats["total_skipped"] += skipped
        self._history_sync_stats["syncs_received"] += 1

        print(f"[history-sync] {sync_name}: stored={stored}, skipped={skipped}, progress={progress}, latest={is_latest}")

        if self.audit_log:
            self.audit_log.log(
                "history_sync", "", "inbound", 0, "",
                {"sync_type": sync_name, "stored": stored, "skipped": skipped, "progress": progress},
            )

    async def handle_group_message(
        self, sender_id: str, group_jid: str, content: str, metadata: dict
    ) -> None:
        """Handle a group message for intelligence collection only (never reply).

        Fire-and-forget from the channel. Rate-limited + capped by ContactStore.
        Cross-pollinates: group sender's messages also enrich their DM profile.
        """
        if not self.contact_store:
            return
        try:
            group_name = metadata.get("group_subject", "")
            stored = await self.contact_store.store_group_sample(
                group_jid, sender_id, content, group_name=group_name,
            )
            if stored:
                print(f"[group-collect] {sender_id} in {group_jid[:12]}.. ({len(content)} chars)")
        except Exception as e:
            print(f"[group-collect] Error: {e}")

    async def _update_contact_profile(self, jid: str) -> None:
        """Background task to generate/update a contact profile.

        Theorem T_POOL: Pass shared client for connection reuse.
        Theorem T_PMODEL: Profile gen uses Haiku (configured in config_manager).
        """
        try:
            await self.contact_store.generate_profile(jid, self.config, client=self._http_client)
        except Exception as e:
            print(f"Contact profile update failed for {jid}: {e}")

    async def run(self) -> None:
        """Main run loop."""
        # Load or create config
        if config_exists():
            self.config = load_config()
            self.system_prompt = build_system_prompt(self.config)
            print(f"Loaded existing config (mode: {self.config['mode']})")
        else:
            # Print instructions - actual AskUserQuestion calls happen
            # from the SKILL.md instructions in the agent context
            self.print_setup_instructions()
            # Use defaults for now - the skill instructions will override
            self.config = dict(DEFAULT_CONFIG)
            save_config(self.config)
            self.system_prompt = build_system_prompt(self.config)

        # Initialize contact store for persistent per-contact profiles
        db_path = get_config_dir() / "contacts.db"
        self.contact_store = ContactStore(db_path)
        print(f"Contact store initialized ({db_path})")

        # Initialize intelligence layer modules (nanobot-inspired)
        self.audit_log = AuditLog(db_path)
        self.scorer = ImportanceScorer(self.config, self.contact_store)
        self.message_queue = MessageQueue(db_path)
        self.templates = AutoReplyTemplates(db_path)
        self.kg = KnowledgeGraph(db_path)
        self.escalation = EscalationEngine(db_path)
        # Reflection engine (learns from mistakes and corrections)
        reflection_db = get_config_dir() / "reflection.db"
        self.reflection = ReflectionEngine(reflection_db)
        # Two-layer memory system
        self.memory = MemoryStore(get_config_dir())
        self.memory_search = MemorySearch(self.memory)
        self.quiet_hours = QuietHours()
        self.quiet_hours.update_config(self.config)
        self.semantic_guard = SemanticGuard()
        # FabricationGuard only for impersonate mode (catches "I'm at the gym" etc.)
        # In assistant/business mode, the AI never pretends to be the owner.
        if self.config.get("personality_mode") == "impersonate":
            self.fabrication_guard = FabricationGuard()
        self.content_filter = ContentFilter()
        self.health_monitor = HealthMonitor()
        # Context builder: layered system prompt assembly with identity files
        self.context_builder = ContextBuilder(
            get_config_dir(),
            personality_mode=self.config.get("personality_mode", "impersonate"),
            config=self.config,
        )
        # Heartbeat service: periodic maintenance every 30 minutes
        self.heartbeat = HeartbeatService(interval_s=30 * 60)
        self.heartbeat.register_task("queue_cleanup", make_queue_cleanup_task(self.message_queue))
        self.heartbeat.register_task("audit_prune", make_audit_prune_task(self.audit_log))
        self.heartbeat.register_task("escalation_expire", make_escalation_expire_task(self.escalation))
        self.heartbeat.register_task("sample_prune", make_sample_prune_task(self.contact_store))
        # KG extraction heartbeat task: extract entities/relationships every 30 min
        def _make_kg_extraction_task(kg, contact_store, config):
            async def _task():
                api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
                api_url = config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1/openai/v1")
                if not api_key or not kg or not contact_store:
                    return
                jids = [r[0] for r in kg._conn.execute(
                    "SELECT DISTINCT jid FROM conversation_samples"
                ).fetchall()]
                for jid in jids:
                    samples = kg.get_unprocessed_samples(jid)
                    if samples:
                        await kg.extract_from_samples(jid, samples, api_url, api_key)
            return _task
        self.heartbeat.register_task("kg_extraction", _make_kg_extraction_task(self.kg, self.contact_store, self.config))

        # Daily summary: send activity summary to admin once per day
        self._last_daily_summary_date: str = ""
        self._daily_msg_count = 0
        self._daily_unique_contacts: set[str] = set()

        def _make_daily_summary_task(bot_ref):
            import datetime
            async def _task():
                today = datetime.date.today().isoformat()
                if bot_ref._last_daily_summary_date == today:
                    return  # Already sent today
                # Only send between 8-10 AM check (heartbeat runs every 30min)
                now = datetime.datetime.now()
                if now.hour < 8 or now.hour >= 10:
                    return
                admin_number = bot_ref.config.get("admin_number", "")
                if not admin_number or not bot_ref.channel:
                    return
                admin_jid = f"{admin_number}@s.whatsapp.net"
                # Gather stats
                msg_count = bot_ref._daily_msg_count
                unique_contacts = len(bot_ref._daily_unique_contacts)
                profiles_count = len(bot_ref.contact_store.get_all_profiles()) if bot_ref.contact_store else 0
                health = bot_ref.health_monitor.get_health() if bot_ref.health_monitor else {}
                uptime_h = int(health.get("uptime_seconds", 0)) // 3600
                pending_esc = bot_ref.escalation.pending_count() if bot_ref.escalation else 0
                summary = (
                    f"*Daily Summary*\n\n"
                    f"Messages yesterday: {msg_count}\n"
                    f"Unique contacts: {unique_contacts}\n"
                    f"Total profiles: {profiles_count}\n"
                    f"Pending escalations: {pending_esc}\n"
                    f"Uptime: {uptime_h}h\n"
                    f"Mode: {bot_ref.config.get('mode', '?')}"
                )
                try:
                    await bot_ref.channel.send_text(admin_jid, summary)
                    print(f"[daily-summary] Sent to admin")
                except Exception as e:
                    print(f"[daily-summary] Error: {e}")
                # Reset counters for new day
                bot_ref._last_daily_summary_date = today
                bot_ref._daily_msg_count = 0
                bot_ref._daily_unique_contacts = set()
            return _task
        self.heartbeat.register_task("daily_summary", _make_daily_summary_task(self))

        # Session manager (conversation continuity)
        self.session_mgr = SessionManager(db_path)
        # Cron/scheduling service
        self.cron = CronService(db_path)
        async def _cron_callback(job: dict) -> None:
            """Fire a cron job: send its message to the target chat or admin."""
            target = job.get("target_chat", "")
            if not target:
                admin_number = self.config.get("admin_number", "")
                if admin_number:
                    target = f"{admin_number}@s.whatsapp.net"
            if target and self.channel:
                msg = f"*Reminder: {job['name']}*\n{job['message']}"
                await self.channel.send_text(target, msg)
                if self.audit_log:
                    self.audit_log.log("cron_fire", target, "outbound", len(msg), "", {"job_id": job["id"], "kind": job["kind"]})
        self.cron.set_callback(_cron_callback)
        print("Intelligence layer initialized (scoring, queue, escalation, KG, templates, audit, memory, reflection, quiet_hours, guards, health, heartbeat, context, session, cron)")

        # Tool executor: image gen, video gen, PDF creation (needs http_client, set below)
        self.tool_executor = ToolExecutor(self.config)

        # Theorem T_POOL: Create shared HTTP client with connection pooling.
        # max_keepalive_connections=5 keeps warm connections to AI Gateway + Whisper API.
        if httpx:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(90.0, connect=10.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
            print("HTTP client pool initialized (T_POOL: connection reuse enabled)")
            # Share pool, channel, and escalation engine with tool executor
            if self.tool_executor:
                self.tool_executor._client = self._http_client
                self.tool_executor._channel = self.channel
                self.tool_executor._escalation = self.escalation

        # Broadcast campaign engine (needs contact_store, channel, memory, KG, http_client)
        try:
            self.broadcast, self._broadcast_store = create_broadcast_engine(
                config=self.config,
                contact_store=self.contact_store,
                channel=None,  # Set after channel is created
                memory_store=self.memory,
                knowledge_graph=self.kg,
                http_client=self._http_client,
            )
            # Register broadcast tools with tool executor
            if self.tool_executor and self.broadcast:
                bcast_integration = BroadcastIntegration(self.broadcast)
                for td in bcast_integration.tool_definitions():
                    tool_name = td["function"]["name"]
                    self.tool_executor._handlers[tool_name] = bcast_integration
                    self.tool_executor._integration_tools.add(tool_name)
                self.tool_executor._integrations["broadcast"] = bcast_integration
            # Register heartbeat task for scheduled campaign checks
            if self.heartbeat and self.broadcast:
                self.heartbeat.register_task("broadcast_scheduler", self.broadcast.check_scheduled_campaigns)
            print(f"Broadcast engine initialized")
        except Exception as e:
            print(f"Broadcast engine init error: {type(e).__name__}: {e}")

        # Mark bridge running in health monitor
        if self.health_monitor:
            self.health_monitor.set_bridge_running(True)

        # Start services
        self.start_bridge()
        qr_url = self.start_qr_server()

        print(f"\nScan the QR code at: {qr_url}")
        print("Waiting for WhatsApp connection...\n")

        # Media cleanup on startup (remove files older than configured max age)
        # Proof: Without cleanup, media dir grows unbounded at O(messages).
        # Default 24h = keeps recent context while bounding disk to ~1 day of media.
        from src.whatsapp_channel import WhatsAppChannel as _WC
        temp_channel = _WC(config=self.config)
        removed = temp_channel.cleanup_media()
        if removed:
            print(f"Startup media cleanup: removed {removed} expired files")

        # Start channel (with group collector callback)
        self.channel = WhatsAppChannel(
            config=self.config,
            on_message=self.handle_message,
            on_group_message=self.handle_group_message,
            on_history_sync=self._handle_history_sync,
            on_contacts_sync=self._handle_contacts_sync,
        )

        # Share channel with broadcast engine (created before channel exists)
        if self.broadcast:
            self.broadcast._channel = self.channel

        # Start heartbeat service (periodic maintenance)
        if self.heartbeat:
            await self.heartbeat.start()

        # Start cron/scheduling service
        if self.cron:
            await self.cron.start()

        # Startup consolidation: catch up on contacts that have samples but no memory
        # (fixes the issue where in-memory counter resets on restart, skipping consolidation)
        asyncio.create_task(self._startup_consolidation_check())

        # Handle shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Run channel (blocks until stopped)
        await self.channel.start()

    async def shutdown(self) -> None:
        """Gracefully shut down all services."""
        print("\nShutting down...")

        if self.channel:
            await self.channel.stop()

        if self.bridge:
            self.bridge.stop()

        if self.qr_server:
            self.qr_server.shutdown()

        if self.contact_store:
            self.contact_store.close()

        # Close intelligence layer modules
        if self.audit_log:
            self.audit_log.close()
        if self.message_queue:
            self.message_queue.close()
        if self.templates:
            self.templates.close()
        if self.kg:
            self.kg.close()
        if self.escalation:
            self.escalation.close()

        if self.session_mgr:
            self.session_mgr.close()

        if self.heartbeat:
            await self.heartbeat.stop()

        if self.cron:
            await self.cron.stop()
            self.cron.close()

        # Theorem T_POOL: Close shared HTTP client to release connections.
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        print("All services stopped.")


def main():
    """Entry point."""
    orchestrator = WhatsAppOrchestrator()
    asyncio.run(orchestrator.run())


if __name__ == "__main__":
    main()
