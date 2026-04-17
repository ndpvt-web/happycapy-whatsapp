"""Job Tools integration -- per-contact isolated tools for proactive follow-ups.

Per-Contact Isolation (same principle as memory isolation):
- Each LLM invocation is scoped to ONE contact via set_request_context()
- create_followup_job has NO contact_query parameter -- it can ONLY create
  jobs for the contact in the current conversation (enforced at schema level)
- get_pending_jobs shows ONLY jobs for the current contact
- The LLM has zero knowledge of or ability to affect other contacts' jobs

These tools are available to ALL contacts (visibility="all") because any
conversation may need to defer work. The job consumer picks up these jobs
and delivers replies proactively.
"""

from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult


_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "create_followup_job",
            "description": (
                "Create a follow-up job for THIS conversation's contact. The job is automatically "
                "scoped to whoever you are currently talking to -- you cannot specify a different contact. "
                "Use ONLY when you genuinely cannot complete the task right now. "
                "A background process will re-invoke you later to complete the job and deliver the reply. "
                "Before creating a job, check get_pending_jobs to avoid duplicates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What needs to be done -- describe the task clearly so your future self can complete it.",
                    },
                    "job_type": {
                        "type": "string",
                        "enum": ["followup", "research", "confirmation", "delivery"],
                        "description": "Type of job: followup (general), research (needs investigation), confirmation (needs owner OK), delivery (send prepared content).",
                    },
                    "needs_admin": {
                        "type": "boolean",
                        "description": (
                            "true = this job REQUIRES the phone owner's input before completion (e.g. pricing, personal decisions). "
                            "false (default) = you can complete it independently when re-invoked with tools."
                        ),
                    },
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pending_jobs",
            "description": (
                "List pending follow-up jobs for THIS conversation's contact only. "
                "Use before creating a new job to check for duplicates, or to inform the contact "
                "about the status of tasks you previously deferred."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


class Integration(BaseIntegration):
    """Per-contact isolated job tools for proactive follow-ups.

    Isolation guarantee: set_request_context() binds all operations to the
    current contact's JID. No tool parameter can override this binding.
    Multiple contacts can have concurrent pending jobs -- each conversation
    only sees and affects its own jobs.
    """

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._job_queue = kwargs.get("job_queue")
        self._contact_store = kwargs.get("contact_store")
        self._sender_jid: str = ""
        self._sender_name: str = ""

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        """Bind this tool instance to the current contact. Called per-request."""
        self._sender_jid = sender_jid
        self._sender_name = ""
        if sender_jid and self._contact_store:
            try:
                profile = self._contact_store.get_profile(sender_jid)
                if profile:
                    self._sender_name = profile.get("push_name", "") or profile.get("name", "")
            except Exception:
                pass

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="job_tools",
            display_name="Job Tools",
            description="Per-contact isolated job creation and status (available to all contacts)",
        )

    @classmethod
    def visibility(cls) -> str:
        return "all"

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        return (
            "## Follow-Up Jobs\n"
            "You have access to a job queue for deferred tasks. When you cannot complete a task now:\n"
            "1. Call create_followup_job -- it automatically targets the contact you're talking to.\n"
            "2. A background process will re-invoke you within 30 seconds to complete the task.\n"
            "3. Your reply will be sent proactively to the contact without them needing to message again.\n"
            "4. Call get_pending_jobs first to avoid creating duplicate jobs.\n"
            "You CANNOT create jobs for other contacts -- each conversation is isolated."
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self._sender_jid:
            return ToolResult(False, tool_name, "No sender context -- cannot determine which contact this is for.")

        if tool_name == "create_followup_job":
            return await self._create_followup_job(arguments)
        elif tool_name == "get_pending_jobs":
            return await self._get_pending_jobs(arguments)
        return ToolResult(False, tool_name, f"Unknown tool: {tool_name}")

    async def _create_followup_job(self, args: dict) -> ToolResult:
        description = args.get("description", "").strip()
        job_type = args.get("job_type", "followup")
        needs_admin = bool(args.get("needs_admin", False))

        if not description:
            return ToolResult(False, "create_followup_job", "Missing description.")

        if not self._job_queue:
            return ToolResult(False, "create_followup_job", "Job queue not available.")

        # Check for duplicate: same contact, same description, still pending
        existing = self._job_queue.get_for_contact(self._sender_jid, include_delivered=False)
        for job in existing:
            if job.get("description", "").strip().lower() == description.lower():
                return ToolResult(
                    True, "create_followup_job",
                    f"Duplicate avoided: job #{job['id']} already exists with the same description. "
                    f"Status: {job['status']}."
                )

        contact_label = self._sender_name or self._sender_jid[:15]
        job_id = self._job_queue.create_job(
            contact_jid=self._sender_jid,
            contact_name=contact_label,
            description=description,
            job_type=job_type,
            needs_admin=needs_admin,
        )

        status_note = "(waiting for owner input)" if needs_admin else "(will be processed automatically within 30s)"
        return ToolResult(
            True, "create_followup_job",
            f"Job #{job_id} created for {contact_label}. {status_note} "
            f"Type: {job_type}. Task: {description[:100]}"
        )

    async def _get_pending_jobs(self, args: dict) -> ToolResult:
        if not self._job_queue:
            return ToolResult(False, "get_pending_jobs", "Job queue not available.")

        jobs = self._job_queue.get_for_contact(self._sender_jid, include_delivered=False)
        if not jobs:
            return ToolResult(True, "get_pending_jobs", "No pending jobs for this contact.")

        lines = [f"Pending jobs for {self._sender_name or self._sender_jid[:15]} ({len(jobs)}):"]
        for j in jobs:
            age_min = int(((__import__('time').time()) - j["created_at"]) / 60)
            lines.append(
                f"  #{j['id']} | {j['status']} | {j['description'][:60]} | {age_min}m ago"
            )
        return ToolResult(True, "get_pending_jobs", "\n".join(lines))
