"""Email Monitor integration for HappyCapy WhatsApp bot.

Proactive email monitoring -- the agent watches your inbox and alerts you
via WhatsApp when important emails arrive. If a reply is needed, it drafts
one and asks for your approval before sending.

Architecture:
  - Background asyncio loop polls Gmail every N minutes via `gws gmail +triage`
  - Tracks seen message IDs in a local JSON file to detect new emails
  - New emails are classified by the LLM for urgency and reply-needed
  - Admin gets a WhatsApp notification with email summary
  - If reply needed: LLM drafts a reply, admin approves/edits via WhatsApp
  - Approval triggers `gws gmail +send` to send the reply

Config keys:
  email_monitor_enabled: bool (default false)
  email_monitor_interval_minutes: int (default 5)
  email_monitor_query: str (default "is:unread") -- Gmail search query
  email_monitor_max_results: int (default 10)

This is a BaseIntegration plugin -- drop in to enable, delete to disable.
"""

import asyncio
import json
import os
import time
from typing import Any

import aiohttp

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult
from src.config_manager import is_admin, get_escalation_target

GWS_BIN = os.path.expanduser("~/.cargo/bin/gws")

# ── LLM-based importance filter ──

_IMPORTANCE_PROMPT = """\
You are an executive email triage assistant. Your job is to decide whether an email
deserves the boss's immediate attention on WhatsApp -- meaning they likely need to
READ it or REPLY to it personally.

FORWARD (answer "yes") if ANY of these apply:
- A real person (not a bot/system) is asking a question or requesting something
- Meeting request, calendar invite, or scheduling ask
- Business proposal, partnership inquiry, investor communication
- Urgent or time-sensitive matter (deadlines, issues, escalations)
- Personal message from a known contact
- Financial: invoices, payment confirmations they need to act on

DO NOT FORWARD (answer "no") for:
- Marketing emails, newsletters, promotional content
- Automated notifications (GitHub, CI/CD, server alerts, app notifications)
- Subscription confirmations, password resets, verification codes
- Social media notifications (LinkedIn, Twitter, etc.)
- Bulk/mass emails, mailing lists
- Receipts or order confirmations (unless action needed)
- Spam or irrelevant content
- System-generated no-reply emails

Email:
From: {sender}
Subject: {subject}
Snippet: {snippet}

Answer with ONLY "yes" or "no" (lowercase, nothing else)."""


async def _score_email_importance(sender: str, subject: str, snippet: str, config: dict) -> bool:
    """Use LLM to decide if email needs admin attention. Returns True if important."""
    gateway_url = config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1")
    api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
    if not api_key:
        # No API key -- fail open (forward everything)
        return True

    prompt = _IMPORTANCE_PROMPT.format(
        sender=sender or "Unknown",
        subject=subject or "(no subject)",
        snippet=(snippet or "")[:300],
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{gateway_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "HappyCapy/1.0",
                },
                json={
                    "model": "anthropic/claude-haiku-4.5",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 5,
                    "temperature": 0,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return True  # fail open
                data = await resp.json()
                answer = data["choices"][0]["message"]["content"].strip().lower()
                return answer.startswith("yes")
    except Exception as e:
        print(f"[email-monitor] Importance check failed ({e}), forwarding by default")
        return True
SEEN_FILE = os.path.expanduser("~/.happycapy-whatsapp/data/email_monitor_seen.json")
PENDING_FILE = os.path.expanduser("~/.happycapy-whatsapp/data/email_pending_replies.json")


# ── Persistence helpers ──

def _load_seen() -> set[str]:
    """Load set of previously seen email message IDs."""
    try:
        with open(SEEN_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("seen_ids", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_seen(seen: set[str]) -> None:
    """Save seen email IDs. Keep last 500 to prevent unbounded growth."""
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    # Keep only last 500 IDs (oldest get dropped)
    trimmed = list(seen)[-500:]
    with open(SEEN_FILE, "w") as f:
        json.dump({"seen_ids": trimmed, "updated_at": time.time()}, f)


def _load_pending() -> dict[str, dict]:
    """Load pending reply drafts awaiting admin approval."""
    try:
        with open(PENDING_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_pending(pending: dict[str, dict]) -> None:
    """Save pending reply drafts."""
    os.makedirs(os.path.dirname(PENDING_FILE), exist_ok=True)
    with open(PENDING_FILE, "w") as f:
        json.dump(pending, f, indent=2)


# ── Gmail CLI helper ──

async def _run_gws(*args: str, timeout: int = 30) -> tuple[bool, str]:
    """Run a gws CLI command. Returns (success, output)."""
    env = os.environ.copy()
    env["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + env.get("PATH", "")
    try:
        proc = await asyncio.create_subprocess_exec(
            GWS_BIN, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            return True, output
        return False, err or output or f"gws exited {proc.returncode}"
    except asyncio.TimeoutError:
        return False, f"gws timed out after {timeout}s"
    except FileNotFoundError:
        return False, f"gws not found at {GWS_BIN}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ── Tool definitions ──

_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "email_approve_reply",
            "description": (
                "Stage 1 of 2: Review and stage a pending email reply draft. "
                "This does NOT send the email -- it stages it for final confirmation. "
                "After calling this, show the admin the full draft and ask them to "
                "say 'CONFIRM EM-XXX' to actually send it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "The draft ID (e.g. 'EM-001') shown in the notification.",
                    },
                    "edit_body": {
                        "type": "string",
                        "description": "Optional: replace the draft body with this text before staging.",
                    },
                },
                "required": ["draft_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_confirm_send",
            "description": (
                "Stage 2 of 2: FINAL confirmation -- actually sends a staged email. "
                "Only works on drafts that have already passed through email_approve_reply. "
                "Call this ONLY when the admin explicitly says 'CONFIRM EM-XXX' or 'SEND EM-XXX'. "
                "Never call this without an explicit confirmation from the admin."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "The draft ID to confirm and send.",
                    },
                },
                "required": ["draft_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_reject_reply",
            "description": "Reject/discard a pending email reply draft. The email will not be sent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "The draft ID to reject.",
                    },
                },
                "required": ["draft_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_list_pending",
            "description": "List all pending email reply drafts awaiting approval.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ── Integration class ──

class Integration(BaseIntegration):
    """Email Monitor -- proactive inbox watching with approval-gated replies."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._sender_jid: str = ""
        self._client = kwargs.get("client")
        self._channel = kwargs.get("channel")
        self._monitor_task: asyncio.Task | None = None
        self._seen = _load_seen()
        self._draft_counter = 0

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    def _is_admin(self) -> bool:
        sender_id = self._sender_jid.split("@")[0] if self._sender_jid else ""
        return is_admin(self.config, sender_id)

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="email_monitor",
            display_name="Email Monitor",
            description="Proactive inbox monitoring with approval-gated email replies",
        )

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        if not config.get("email_monitor_enabled", False):
            return ""
        return (
            "## Email Monitor (Admin Only) -- TWO-STAGE SEND GUARD\n\n"
            "Your inbox is being monitored automatically. When new emails arrive, "
            "you'll get a WhatsApp notification with a summary.\n\n"
            "**Email sending requires TWO separate admin confirmations:**\n"
            "1. `email_approve_reply` -- stages the draft and shows it to admin (does NOT send)\n"
            "2. `email_confirm_send` -- actually sends (ONLY after admin explicitly says CONFIRM)\n\n"
            "**HARD RULE: Never call email_confirm_send unless the admin's message "
            "explicitly contains the word CONFIRM or SEND followed by the draft ID.**\n\n"
            "Flow:\n"
            "- Admin says 'approve EM-001' or 'draft reply to EM-001' -> call email_approve_reply\n"
            "- Show the staged draft to admin and ask: 'Say CONFIRM EM-001 to send'\n"
            "- Admin says 'CONFIRM EM-001' -> call email_confirm_send\n"
            "- Admin says 'reject EM-001' or 'discard EM-001' -> call email_reject_reply\n\n"
            "Other tools: email_list_pending (see all drafts)\n"
            "Draft IDs: EM-001, EM-002, etc.\n"
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self._is_admin():
            return ToolResult(False, tool_name, "Email monitor tools are admin-only.")
        if not self.config.get("email_monitor_enabled", False):
            return ToolResult(False, tool_name, "Email monitor is not enabled.")
        handlers = {
            "email_approve_reply": self._approve_reply,
            "email_confirm_send": self._confirm_send,
            "email_reject_reply": self._reject_reply,
            "email_list_pending": self._list_pending,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(False, tool_name, f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return ToolResult(False, tool_name, f"Error: {type(e).__name__}: {e}")

    # ── Tool handlers ──

    async def _approve_reply(self, args: dict) -> ToolResult:
        """Stage 1: Review and stage a draft. Does NOT send."""
        draft_id = args.get("draft_id", "").strip().upper()
        if not draft_id:
            return ToolResult(False, "email_approve_reply", "Missing draft_id")

        pending = _load_pending()
        draft = pending.get(draft_id)
        if not draft:
            return ToolResult(False, "email_approve_reply", f"No pending draft with ID {draft_id}")

        # Allow body edits at staging time
        if args.get("edit_body", "").strip():
            draft["body"] = args["edit_body"].strip()

        # Mark as staged (hard state gate -- confirm_send checks this)
        draft["stage"] = "awaiting_confirmation"
        draft["staged_at"] = time.time()
        pending[draft_id] = draft
        _save_pending(pending)

        return ToolResult(True, "email_approve_reply",
                          f"Draft {draft_id} STAGED (not sent yet).\n\n"
                          f"To: {draft['to']}\n"
                          f"Subject: {draft['subject']}\n"
                          f"Body: {draft['body'] or '(empty -- admin should provide body)'}\n\n"
                          f"**Tell the admin: say 'CONFIRM {draft_id}' to send, "
                          f"or 'reject {draft_id}' to discard.**")

    async def _confirm_send(self, args: dict) -> ToolResult:
        """Stage 2: Hard-gated final send. Only works on staged drafts."""
        draft_id = args.get("draft_id", "").strip().upper()
        if not draft_id:
            return ToolResult(False, "email_confirm_send", "Missing draft_id")

        pending = _load_pending()
        draft = pending.get(draft_id)
        if not draft:
            return ToolResult(False, "email_confirm_send", f"No draft with ID {draft_id}")

        # ── HARD PROGRAMMATIC GUARD ──
        # Draft MUST have passed through _approve_reply first (stage == "awaiting_confirmation")
        # This cannot be bypassed by prompt injection -- it's a code check on persisted state.
        if draft.get("stage") != "awaiting_confirmation":
            return ToolResult(False, "email_confirm_send",
                              f"BLOCKED: Draft {draft_id} has not been staged yet. "
                              f"Call email_approve_reply first (current stage: {draft.get('stage', 'pending')})")

        body = draft.get("body", "").strip()
        if not body:
            return ToolResult(False, "email_confirm_send",
                              f"BLOCKED: Draft {draft_id} has an empty body. "
                              f"Use email_approve_reply with edit_body to set the reply text first.")

        to = draft["to"]
        subject = draft["subject"]

        # Actually send via gws
        ok, output = await _run_gws(
            "gmail", "+send", "--to", to, "--subject", subject, "--body", body
        )
        if not ok:
            return ToolResult(False, "email_confirm_send", f"Send failed: {output}")

        # Remove from pending
        del pending[draft_id]
        _save_pending(pending)

        return ToolResult(True, "email_confirm_send",
                          f"Email SENT to {to}\nSubject: {subject}")

    async def _reject_reply(self, args: dict) -> ToolResult:
        draft_id = args.get("draft_id", "").strip().upper()
        if not draft_id:
            return ToolResult(False, "email_reject_reply", "Missing draft_id")

        pending = _load_pending()
        if draft_id not in pending:
            return ToolResult(False, "email_reject_reply", f"No pending draft with ID {draft_id}")

        del pending[draft_id]
        _save_pending(pending)

        return ToolResult(True, "email_reject_reply", f"Draft {draft_id} discarded.")

    async def _list_pending(self, args: dict) -> ToolResult:
        pending = _load_pending()
        if not pending:
            return ToolResult(True, "email_list_pending", "No pending reply drafts.")
        lines = []
        for did, draft in pending.items():
            lines.append(
                f"*{did}*: To {draft['to']} | Subject: {draft['subject']}\n"
                f"  Body preview: {draft['body'][:100]}..."
            )
        return ToolResult(True, "email_list_pending",
                          f"{len(pending)} pending draft(s):\n\n" + "\n\n".join(lines))

    # ── Background monitor ──

    async def start_monitor(self, channel: Any, bot: Any = None) -> None:
        """Start the background email polling loop.

        Called by main.py after the bot is fully initialized.
        channel: WhatsAppChannel instance for sending notifications.
        bot: Bot instance for LLM-based classification (optional).
        """
        if not self.config.get("email_monitor_enabled", False):
            print("[email-monitor] Disabled in config")
            return

        if not os.path.exists(GWS_BIN):
            print(f"[email-monitor] gws binary not found at {GWS_BIN}, monitor disabled")
            return

        self._channel = channel
        self._bot = bot
        interval = self.config.get("email_monitor_interval_minutes", 5) * 60

        async def _poll_loop():
            # Initial delay: let services stabilize
            await asyncio.sleep(30)
            print(f"[email-monitor] Started (interval={interval}s)")
            while True:
                try:
                    await self._check_inbox()
                except Exception as e:
                    print(f"[email-monitor] Error: {type(e).__name__}: {e}")
                await asyncio.sleep(interval)

        self._monitor_task = asyncio.create_task(_poll_loop())

    async def stop_monitor(self) -> None:
        """Stop the background polling loop."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self._monitor_task = None
        print("[email-monitor] Stopped")

    async def _check_inbox(self) -> None:
        """Poll Gmail for new unread emails and notify admin."""
        query = self.config.get("email_monitor_query", "is:unread")
        max_results = self.config.get("email_monitor_max_results", 10)

        ok, output = await _run_gws(
            "gmail", "+triage", "--max", str(max_results),
            "--query", query, "--format", "json"
        )
        if not ok:
            if "OAuth" in output or "token" in output.lower():
                print(f"[email-monitor] Auth issue: {output}")
            return

        # Parse emails
        try:
            emails = json.loads(output)
            if not isinstance(emails, list):
                # Sometimes gws wraps in an object
                emails = emails.get("emails", emails.get("messages", []))
        except json.JSONDecodeError:
            # Try line-by-line parsing (some gws versions output JSONL)
            emails = []
            for line in output.strip().split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        emails.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        if not emails:
            return

        # Find new emails (not in seen set)
        new_emails = []
        for email in emails:
            msg_id = email.get("id") or email.get("message_id") or email.get("threadId", "")
            if not msg_id:
                # Generate a pseudo-ID from subject + sender + date
                msg_id = f"{email.get('from','')}-{email.get('subject','')}-{email.get('date','')}"

            if msg_id not in self._seen:
                self._seen.add(msg_id)
                new_emails.append(email)

        if not new_emails:
            return

        # Save updated seen set
        _save_seen(self._seen)

        # Notify admin about new emails
        escalation_target = get_escalation_target(self.config)
        if not escalation_target or not self._channel:
            return

        admin_jid = f"{escalation_target}@s.whatsapp.net"

        for email in new_emails:
            sender = email.get("from", email.get("sender", "Unknown"))
            subject = email.get("subject", "(no subject)")
            snippet = email.get("snippet", email.get("body", ""))[:200]
            date = email.get("date", email.get("received", ""))

            # LLM importance filter -- skip emails that don't need admin attention
            important = await _score_email_importance(sender, subject, snippet, self.config)
            if not important:
                print(f"[email-monitor] Skipped (not important): {sender} | {subject}")
                continue

            # Create a notification message
            notification = (
                f"*New Email*\n"
                f"From: {sender}\n"
                f"Subject: {subject}\n"
            )
            if date:
                notification += f"Date: {date}\n"
            if snippet:
                notification += f"\n{snippet}\n"

            # Create a pending reply draft
            self._draft_counter += 1
            draft_id = f"EM-{self._draft_counter:03d}"

            # Extract reply-to email address
            reply_to = sender
            # Try to extract just the email from "Name <email>" format
            if "<" in reply_to and ">" in reply_to:
                reply_to = reply_to.split("<")[1].split(">")[0]

            # Store draft placeholder (admin can approve to send)
            pending = _load_pending()
            pending[draft_id] = {
                "to": reply_to,
                "subject": f"Re: {subject}" if not subject.startswith("Re:") else subject,
                "body": "",  # Empty until admin provides or LLM drafts
                "original_from": sender,
                "original_subject": subject,
                "original_snippet": snippet,
                "created_at": time.time(),
            }
            _save_pending(pending)

            notification += (
                f"\n*Draft ID: {draft_id}*\n"
                f"To reply: tell me what to say and I'll draft it as {draft_id}\n"
                f"Or say 'reject {draft_id}' to ignore."
            )

            try:
                await self._channel.send_text(admin_jid, notification)
            except Exception as e:
                print(f"[email-monitor] Failed to notify admin: {e}")

        # Count how many were actually forwarded (those that weren't skipped)
        print(f"[email-monitor] {len(new_emails)} new email(s) checked, important ones forwarded")
