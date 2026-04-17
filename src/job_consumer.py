"""
job_consumer.py -- Background job consumer service.

Processes deferred/queued jobs every 30 seconds:
  1. Expire stale jobs
  2. Deliver composed replies that are ready
  3. Re-invoke LLM for actionable jobs that still need processing
"""

import asyncio
import json
import re
import logging
from typing import Optional

from src.tool_executor import ToolExecutor, ToolResult

logger = logging.getLogger(__name__)

_REPLY_RE = re.compile(
    r"<reply(?:\s[^>]*)?>(?P<content>[\s\S]*?)</reply>",
    re.IGNORECASE,
)

POLL_INTERVAL = 30  # seconds
MAX_TOOL_ROUNDS = 3
MAX_RETRIES = 3


class JobConsumer:
    """Runs a background loop that processes deferred jobs from the job queue."""

    def __init__(
        self,
        job_queue,
        channel,
        config: dict,
        tool_executor: ToolExecutor,
        contact_store=None,
        memory=None,
        kg=None,
        http_client=None,
        context_builder=None,
    ):
        self.job_queue = job_queue
        self.channel = channel
        self.config = config
        self.tool_executor = tool_executor
        self.contact_store = contact_store
        self.memory = memory
        self.kg = kg
        self.http_client = http_client
        self.context_builder = context_builder

        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Launch the background processing loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        print("[job_consumer] started")

    def stop(self):
        """Signal the loop to stop and cancel the background task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            print("[job_consumer] stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self):
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[job_consumer] unhandled error in loop: {exc}")

            try:
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _tick(self):
        # 1. Expire stale jobs
        try:
            self.job_queue.expire_old_jobs()
        except Exception as exc:
            print(f"[job_consumer] expire_old_jobs error: {exc}")

        # 2. Deliver jobs with a composed reply ready
        try:
            ready_jobs = self.job_queue.get_ready_to_deliver(limit=5)
        except Exception as exc:
            print(f"[job_consumer] get_ready_to_deliver error: {exc}")
            ready_jobs = []

        for job in ready_jobs:
            try:
                await self._deliver_composed(job)
            except Exception as exc:
                print(f"[job_consumer] delivery error for job {job.get('id')}: {exc}")

        # 3. Process actionable jobs via LLM re-invocation
        try:
            actionable_jobs = self.job_queue.get_actionable(limit=3)
        except Exception as exc:
            print(f"[job_consumer] get_actionable error: {exc}")
            actionable_jobs = []

        for job in actionable_jobs:
            try:
                await self._process_job(job)
            except Exception as exc:
                print(f"[job_consumer] process error for job {job.get('id')}: {exc}")

    # ------------------------------------------------------------------
    # Delivery helpers
    # ------------------------------------------------------------------

    async def _deliver_composed(self, job: dict):
        """Send an already-composed reply to the contact."""
        reply = job.get("composed_reply", "").strip()
        if not reply:
            print(f"[job_consumer] job {job.get('id')} has no composed_reply, skipping")
            return

        if not await self._can_send(job):
            return

        await self.channel.send_text(job["contact_jid"], reply)
        self.job_queue.update_status(job["id"], "delivered")
        print(f"[job_consumer] delivered composed reply for job {job.get('id')} to {job.get('contact_jid')}")

    async def _can_send(self, job: dict) -> bool:
        """Return True if the channel is available for sending."""
        if self.channel is None:
            print(f"[job_consumer] channel not set, skipping job {job.get('id')}")
            return False
        return True

    # ------------------------------------------------------------------
    # LLM re-invocation
    # ------------------------------------------------------------------

    async def _process_job(self, job: dict):
        """Re-invoke the LLM to complete a deferred task."""
        job_id = job.get("id")
        contact_jid = job.get("contact_jid", "")

        # Parse retry state from context_json
        context = {}
        raw_ctx = job.get("context_json")
        if raw_ctx:
            try:
                context = json.loads(raw_ctx) if isinstance(raw_ctx, str) else raw_ctx
            except (json.JSONDecodeError, TypeError):
                context = {}

        retry_count = context.get("retry_count", 0)
        if retry_count >= MAX_RETRIES:
            print(f"[job_consumer] job {job_id} exceeded max retries ({MAX_RETRIES}), cancelling")
            self.job_queue.update_status(job_id, "cancelled")
            return

        self.job_queue.update_status(job_id, "processing")
        print(f"[job_consumer] processing job {job_id} for {contact_jid} (retry {retry_count})")

        # Lazy import to avoid circular imports at module load time
        try:
            from src.main import generate_ai_response, AIResponse  # noqa: F401
        except ImportError as exc:
            print(f"[job_consumer] cannot import generate_ai_response: {exc}")
            self._increment_retry(job_id, context)
            return

        system_prompt = self._build_followup_prompt(job)
        user_message = job.get("description", "Complete the deferred task.")

        # Determine tools available for this contact
        is_admin = str(contact_jid) == str(self.config.get("admin_number", ""))
        try:
            tools = self.tool_executor.get_tool_definitions(
                sender_id=contact_jid,
                is_admin=is_admin,
                is_elevated=False,
            )
        except Exception:
            tools = None

        # Build initial chat history
        chat_history = [{"role": "user", "content": user_message}]

        # Tool loop
        resp = None
        for round_idx in range(MAX_TOOL_ROUNDS):
            try:
                resp = await generate_ai_response(
                    message=user_message if round_idx == 0 else "",
                    system_prompt=system_prompt,
                    chat_history=chat_history if round_idx > 0 else [],
                    config=self.config,
                    media_content=None,
                    client=self.http_client,
                    tools=tools,
                )
            except Exception as exc:
                print(f"[job_consumer] generate_ai_response error (job {job_id}, round {round_idx}): {exc}")
                break

            if not resp.tool_calls:
                break

            # Execute tool calls and append results
            assistant_msg = {"role": "assistant", "tool_calls": resp.tool_calls}
            chat_history.append(assistant_msg)

            for tc in resp.tool_calls:
                tc_id = tc.get("id", "")
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except (json.JSONDecodeError, TypeError):
                    arguments = {}

                try:
                    tool_result: ToolResult = await self.tool_executor.execute(
                        tool_name, arguments, contact_jid
                    )
                    result_str = str(tool_result.result) if tool_result.success else f"Error: {tool_result.result}"
                except Exception as exc:
                    result_str = f"Tool execution error: {exc}"

                chat_history.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result_str,
                })

        # Extract reply from <reply>...</reply> tags
        final_content = (resp.content or "") if resp else ""
        match = _REPLY_RE.search(final_content)

        if match:
            reply = match.group("content").strip()
            if reply and await self._can_send(job):
                await self.channel.send_text(contact_jid, reply)
                self.job_queue.update_status(job_id, "delivered")
                print(f"[job_consumer] job {job_id} completed and delivered to {contact_jid}")
                return

        # No valid reply extracted -- increment retry counter
        print(f"[job_consumer] job {job_id} produced no extractable reply (round {retry_count + 1}/{MAX_RETRIES})")
        self._increment_retry(job_id, context)

    def _increment_retry(self, job_id, context: dict):
        """Increment retry_count; cancel the job if max retries reached."""
        context["retry_count"] = context.get("retry_count", 0) + 1
        if context["retry_count"] >= MAX_RETRIES:
            self.job_queue.update_status(
                job_id,
                "cancelled",
                context_json=json.dumps(context),
            )
            print(f"[job_consumer] job {job_id} cancelled after {MAX_RETRIES} failed retries")
        else:
            self.job_queue.update_status(
                job_id,
                "created",
                context_json=json.dumps(context),
            )

    # ------------------------------------------------------------------
    # System prompt builder
    # ------------------------------------------------------------------

    def _build_followup_prompt(self, job: dict) -> str:
        bot_name = self.config.get("bot_name", "Assistant")
        tone = self.config.get("tone", "casual_friendly")

        memory_ctx = ""
        if self.memory:
            try:
                memory_ctx = self.memory.get_memory_context(jid=job["contact_jid"]) or ""
            except Exception:
                memory_ctx = ""

        history_ctx = ""
        if self.contact_store:
            try:
                samples = self.contact_store.get_recent_samples(job["contact_jid"], limit=5)
                if samples:
                    history_ctx = "\n".join(
                        f"{s['role']}: {s['content']}" for s in samples[-5:]
                    )
            except Exception:
                history_ctx = ""

        return (
            f"You are {bot_name}, a WhatsApp assistant. Tone: {tone}.\n\n"
            "## Follow-Up Task\n"
            f"You previously deferred a task for contact {job.get('contact_name', job.get('contact_jid', 'unknown'))}. "
            "The task has been queued and now you need to complete it.\n\n"
            f"**Task:** {job.get('description', '')}\n\n"
            + (f"## Contact Memory\n{memory_ctx}\n\n" if memory_ctx else "")
            + (f"## Recent Conversation\n{history_ctx}\n\n" if history_ctx else "")
            + "CRITICAL: Wrap your entire response in <reply>...</reply> tags. "
            "Only content inside <reply> tags will be sent to the contact. "
            "Complete the task using available tools if needed."
        )
