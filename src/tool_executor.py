"""LLM tool executor for WhatsApp bot.

Handles execution of AI-requested tools: image generation, video generation,
PDF creation. Integrates with WhatsApp media delivery pipeline.

All generated files are saved to ~/.happycapy-whatsapp/media/ to satisfy
the security path validation in WhatsAppChannel.send_media().
"""

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
except ImportError:
    SimpleDocTemplate = None


# ── Tool Definitions (OpenAI format) ──

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Generate an image from a text prompt using AI. "
                "Use when the user asks to create, generate, draw, or make an image or picture."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed description of the image to generate. Be specific and descriptive.",
                    }
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": (
                "Generate a short AI video (6 seconds) from a text prompt. "
                "This takes 1-5 minutes. Only use when the user explicitly asks for a video."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed description of the video to generate.",
                    },
                    "duration": {
                        "type": "integer",
                        "description": "Video duration in seconds. Default 6.",
                        "enum": [6, 8, 10],
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Video aspect ratio. Default 16:9.",
                        "enum": ["16:9", "9:16", "1:1"],
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_pdf",
            "description": (
                "Create a professional PDF document compiled from LaTeX source. "
                "Use when the user asks for a document, report, letter, resume, invoice, or PDF file. "
                "The content MUST be a complete LaTeX document starting with \\documentclass. "
                "Use packages like geometry, fancyhdr, titlesec, enumitem, hyperref for professional formatting. "
                "For tables use tabularx or longtable. For math use amsmath. "
                "Do NOT use any packages that require external files or network access."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the document (used for filename only)",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Complete LaTeX source code starting with \\documentclass. "
                            "Must be a self-contained .tex file that compiles with pdflatex. "
                            "Example: \\documentclass{article}\\begin{document}Hello\\end{document}"
                        ),
                    },
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": (
                "Send a WhatsApp message (text, image, PDF, or video) to a specific contact. "
                "Use when the user asks you to send something to someone, message a contact, "
                "or forward content to a phone number. You can send plain text, or generate "
                "and send media in one step."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {
                        "type": "string",
                        "description": "Phone number with country code, digits only (e.g. '919996126890' or '85292893658').",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text message to send. Optional if sending media.",
                    },
                    "media_type": {
                        "type": "string",
                        "description": "Type of media to generate and send. Omit for text-only messages.",
                        "enum": ["image", "pdf", "video"],
                    },
                    "media_prompt": {
                        "type": "string",
                        "description": "Prompt/content for media generation. Required if media_type is set. For image: description to generate. For pdf: document content. For video: video description.",
                    },
                    "media_title": {
                        "type": "string",
                        "description": "Title for PDF documents. Only used when media_type is 'pdf'.",
                    },
                },
                "required": ["phone_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_owner",
            "description": (
                "Ask the phone owner (admin) a question when you don't know the answer to something. "
                "Use this when someone asks about specific details you don't have (project info, plans, "
                "events, personal details), or when you need permission to share sensitive information. "
                "The owner will receive the question on WhatsApp and can reply with /respond. "
                "While waiting, give the contact a natural deflection like 'lemme check on that'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the owner. Be specific about what the contact is asking and who is asking.",
                    },
                    "contact_name": {
                        "type": "string",
                        "description": "Name or identifier of the contact who is asking.",
                    },
                    "urgency": {
                        "type": "string",
                        "description": "How urgent is this question.",
                        "enum": ["low", "normal", "high"],
                    },
                },
                "required": ["question"],
            },
        },
    },
]


# ── Data Classes ──


@dataclass
class ToolResult:
    """Result of a single tool execution."""

    success: bool
    tool_name: str
    content: str  # Text description for the LLM's tool result message
    media_path: str | None = None  # Path to generated file in MEDIA_DIR
    error_message: str | None = None


# ── Tool Executor ──


class ToolExecutor:
    """Executes LLM-requested tools and manages generated media files.

    All generated files are saved to MEDIA_DIR to satisfy WhatsAppChannel.send_media()
    security path validation.

    Supports pluggable integrations: core tools are built-in, additional tools
    are loaded from src.integrations based on config["enabled_integrations"].
    """

    MEDIA_DIR = Path.home() / ".happycapy-whatsapp" / "media"
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB WhatsApp limit
    VIDEO_TIMEOUT = 300  # 5 minutes max for video generation

    def __init__(self, config: dict[str, Any], client: "httpx.AsyncClient | None" = None, channel=None, escalation=None):
        self.config = config
        self._client = client
        self._channel = channel  # WhatsAppChannel for send_message tool
        self._escalation = escalation  # EscalationEngine for ask_owner tool
        self.MEDIA_DIR.mkdir(parents=True, exist_ok=True)

        # Build unified handler map: core + integration tools
        self._integrations: dict = {}
        self._handlers: dict[str, Any] = {
            "generate_image": self._generate_image,
            "generate_video": self._generate_video,
            "create_pdf": self._create_pdf,
            "send_message": self._send_message,
            "ask_owner": self._ask_owner,
        }
        self._integration_tools: set[str] = set()
        self._load_integrations()

    def _load_integrations(self) -> None:
        """Load enabled integrations and register their tool handlers."""
        enabled = self.config.get("enabled_integrations", ["core"])
        non_core = [n for n in enabled if n != "core"]
        if not non_core:
            return
        try:
            from src.integrations import load_integrations
            self._integrations = load_integrations(
                non_core, self.config,
                client=self._client, channel=self._channel,
            )
            for name, integ in self._integrations.items():
                for td in integ.tool_definitions():
                    tool_name = td["function"]["name"]
                    self._handlers[tool_name] = integ
                    self._integration_tools.add(tool_name)
        except Exception as e:
            print(f"[tool-executor] Failed to load integrations: {type(e).__name__}: {e}")

    def get_tool_definitions(self) -> list[dict]:
        """Get all tool definitions: core + integration tools."""
        all_defs = list(TOOL_DEFINITIONS)
        for integ in self._integrations.values():
            all_defs.extend(integ.tool_definitions())
        return all_defs

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool by name and return the result.

        Never raises -- all exceptions are caught and returned as ToolResult with success=False.
        """
        handler = self._handlers.get(tool_name)
        if not handler:
            return ToolResult(
                success=False,
                tool_name=tool_name,
                content=f"Unknown tool: {tool_name}",
                error_message=f"Unknown tool: {tool_name}",
            )

        try:
            if tool_name in self._integration_tools:
                # Integration handler: handler is an integration instance
                return await handler.execute(tool_name, arguments)
            else:
                # Core handler: handler is a bound method
                return await handler(arguments)
        except Exception as e:
            print(f"[tool-executor] {tool_name} error: {type(e).__name__}")
            return ToolResult(
                success=False,
                tool_name=tool_name,
                content=f"Tool execution failed: {type(e).__name__}",
                error_message=str(e),
            )

    # ── Image Generation ──

    async def _generate_image(self, args: dict[str, Any]) -> ToolResult:
        """Generate an image via AI Gateway image generation API."""
        if not httpx:
            return ToolResult(False, "generate_image", "Image generation unavailable (httpx not installed)")

        prompt = args.get("prompt", "").strip()
        if not prompt:
            return ToolResult(False, "generate_image", "No image prompt provided")

        api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
        if not api_key:
            return ToolResult(False, "generate_image", "API key not configured")

        url = "https://ai-gateway.happycapy.ai/api/v1/images/generations"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Origin": "https://trickle.so",
        }
        payload = {
            "model": "google/gemini-3.1-flash-image-preview",
            "prompt": prompt[:2000],  # Cap prompt length
            "response_format": "b64_json",
            "n": 1,
        }

        try:
            if self._client:
                resp = await self._client.post(url, headers=headers, json=payload, timeout=90.0)
            else:
                async with httpx.AsyncClient() as c:
                    resp = await c.post(url, headers=headers, json=payload, timeout=90.0)

            if resp.status_code != 200:
                return ToolResult(False, "generate_image", f"Image API returned HTTP {resp.status_code}")

            data = resp.json()
            b64_data = data["data"][0].get("b64_json", "")
            if not b64_data:
                # Try URL fallback
                img_url = data["data"][0].get("url", "")
                if img_url:
                    return await self._download_to_media(img_url, "generate_image", prompt, ".png")
                return ToolResult(False, "generate_image", "No image data in API response")

            # Save to media directory
            filename = f"generated_image_{int(time.time())}.png"
            filepath = self.MEDIA_DIR / filename
            filepath.write_bytes(base64.b64decode(b64_data))

            if filepath.stat().st_size > self.MAX_FILE_SIZE:
                filepath.unlink()
                return ToolResult(False, "generate_image", "Generated image exceeds 20MB limit")

            return ToolResult(
                success=True,
                tool_name="generate_image",
                content=f"Image generated successfully for prompt: {prompt[:100]}",
                media_path=str(filepath),
            )

        except httpx.TimeoutException:
            return ToolResult(False, "generate_image", "Image generation timed out")
        except (KeyError, IndexError):
            return ToolResult(False, "generate_image", "Unexpected image API response format")

    # ── Video Generation ──

    async def _generate_video(self, args: dict[str, Any]) -> ToolResult:
        """Generate a video via AI Gateway (async polling)."""
        if not httpx:
            return ToolResult(False, "generate_video", "Video generation unavailable (httpx not installed)")

        prompt = args.get("prompt", "").strip()
        if not prompt:
            return ToolResult(False, "generate_video", "No video prompt provided")

        duration = args.get("duration", 6)
        aspect_ratio = args.get("aspect_ratio", "16:9")

        api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
        if not api_key:
            return ToolResult(False, "generate_video", "API key not configured")

        base_url = "https://ai-gateway.trickle-lab.tech/api/v1"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Origin": "https://trickle.so",
        }
        create_payload = {
            "model": "google/veo-3.1-generate-preview",
            "prompt": prompt[:2000],
            "duration": duration,
            "aspectRatio": aspect_ratio,
        }

        try:
            # Step 1: Start video generation
            if self._client:
                resp = await self._client.post(
                    f"{base_url}/videos", headers=headers, json=create_payload, timeout=30.0
                )
            else:
                async with httpx.AsyncClient() as c:
                    resp = await c.post(
                        f"{base_url}/videos", headers=headers, json=create_payload, timeout=30.0
                    )

            if resp.status_code != 200:
                return ToolResult(False, "generate_video", f"Video API returned HTTP {resp.status_code}")

            video_id = resp.json().get("id")
            if not video_id:
                return ToolResult(False, "generate_video", "No video ID in API response")

            # Step 2: Wait initial delay then poll
            await asyncio.sleep(35)

            start_time = time.time()
            while time.time() - start_time < self.VIDEO_TIMEOUT:
                if self._client:
                    poll_resp = await self._client.get(
                        f"{base_url}/videos/{video_id}", headers=headers, timeout=30.0
                    )
                else:
                    async with httpx.AsyncClient() as c:
                        poll_resp = await c.get(
                            f"{base_url}/videos/{video_id}", headers=headers, timeout=30.0
                        )

                poll_data = poll_resp.json()
                status = poll_data.get("status", "")

                if status == "succeeded":
                    video_url = poll_data.get("url", "")
                    if not video_url:
                        return ToolResult(False, "generate_video", "Video succeeded but no URL returned")
                    return await self._download_to_media(video_url, "generate_video", prompt, ".mp4")

                elif status == "failed":
                    error = poll_data.get("error", "Unknown error")
                    return ToolResult(False, "generate_video", f"Video generation failed: {error}")

                await asyncio.sleep(5)

            return ToolResult(False, "generate_video", "Video generation timed out (5 minute limit)")

        except httpx.TimeoutException:
            return ToolResult(False, "generate_video", "Video API request timed out")

    # ── PDF Creation ──

    async def _create_pdf(self, args: dict[str, Any]) -> ToolResult:
        """Create a PDF document by compiling LaTeX source with pdflatex."""
        import shutil
        import subprocess
        import tempfile

        title = args.get("title", "Document").strip()
        content = args.get("content", "").strip()

        if not content:
            return ToolResult(False, "create_pdf", "No content provided for PDF")

        filename = f"generated_pdf_{int(time.time())}.pdf"
        filepath = self.MEDIA_DIR / filename

        # Detect if content is LaTeX source
        is_latex = (
            "\\documentclass" in content
            or "\\begin{document}" in content
        )

        if is_latex:
            return await self._compile_latex(content, title, filepath)
        else:
            return await self._create_pdf_reportlab(content, title, filepath)

    # Path to the latex-document skill's compile script
    COMPILE_LATEX_SH = Path.home() / ".claude" / "skills" / "latex-document" / "scripts" / "compile_latex.sh"

    async def _compile_latex(
        self, latex_src: str, title: str, filepath: Path,
    ) -> ToolResult:
        """Compile LaTeX source to PDF using the latex-document skill's compile script."""
        import shutil
        import subprocess
        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="latex_")
        tex_file = Path(tmpdir) / "document.tex"

        try:
            tex_file.write_text(latex_src, encoding="utf-8")

            # Use the latex-document skill's compile_latex.sh if available
            # (handles engine detection, multi-pass, bibliography, auto-fix)
            if self.COMPILE_LATEX_SH.exists():
                compile_cmd = [
                    "bash", str(self.COMPILE_LATEX_SH),
                    str(tex_file), "--quiet",
                ]
            else:
                # Fallback: direct pdflatex
                pdflatex = shutil.which("pdflatex")
                if not pdflatex:
                    return ToolResult(False, "create_pdf", "pdflatex not found on this system")
                compile_cmd = [
                    pdflatex,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-output-directory", tmpdir,
                    str(tex_file),
                ]

            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    compile_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=tmpdir,
                ),
            )

            pdf_output = Path(tmpdir) / "document.pdf"
            if not pdf_output.exists():
                # Extract useful error from log
                log_file = Path(tmpdir) / "document.log"
                error_lines = []
                if log_file.exists():
                    for line in log_file.read_text(errors="replace").splitlines():
                        if line.startswith("!") or "Error" in line:
                            error_lines.append(line)
                            if len(error_lines) >= 5:
                                break
                error_msg = "\n".join(error_lines[:5]) if error_lines else (proc.stderr or proc.stdout or "Unknown LaTeX error")[-500:]
                return ToolResult(
                    False, "create_pdf",
                    f"LaTeX compilation failed:\n{error_msg}",
                )

            # Move compiled PDF to media directory
            shutil.copy2(str(pdf_output), str(filepath))

            if filepath.stat().st_size > self.MAX_FILE_SIZE:
                filepath.unlink()
                return ToolResult(False, "create_pdf", "Generated PDF exceeds 20MB limit")

            return ToolResult(
                success=True,
                tool_name="create_pdf",
                content=f"PDF document created: {title}",
                media_path=str(filepath),
            )

        except subprocess.TimeoutExpired:
            return ToolResult(False, "create_pdf", "LaTeX compilation timed out (120s limit)")
        except Exception as e:
            filepath.unlink(missing_ok=True)
            return ToolResult(False, "create_pdf", f"PDF creation failed: {type(e).__name__}: {e}")
        finally:
            # Cleanup temp directory
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _create_pdf_reportlab(
        self, content: str, title: str, filepath: Path,
    ) -> ToolResult:
        """Fallback: create a simple PDF using reportlab for plain text content."""
        if SimpleDocTemplate is None:
            return ToolResult(
                False, "create_pdf",
                "PDF creation unavailable: provide LaTeX source (\\documentclass) or install reportlab",
            )

        try:
            doc = SimpleDocTemplate(str(filepath), pagesize=letter)
            styles = getSampleStyleSheet()
            story = []

            story.append(Paragraph(self._escape_html(title), styles["Title"]))
            story.append(Spacer(1, 12))

            for para in content.split("\n\n"):
                text = para.strip()
                if text:
                    story.append(Paragraph(self._escape_html(text), styles["Normal"]))
                    story.append(Spacer(1, 8))

            doc.build(story)

            if filepath.stat().st_size > self.MAX_FILE_SIZE:
                filepath.unlink()
                return ToolResult(False, "create_pdf", "Generated PDF exceeds 20MB limit")

            return ToolResult(
                success=True,
                tool_name="create_pdf",
                content=f"PDF document created: {title}",
                media_path=str(filepath),
            )

        except Exception as e:
            filepath.unlink(missing_ok=True)
            return ToolResult(False, "create_pdf", f"PDF creation failed: {type(e).__name__}")

    # ── Send Message to Contact ──

    async def _send_message(self, args: dict[str, Any]) -> ToolResult:
        """Send a text message and/or generated media to a specific WhatsApp contact."""
        if not self._channel:
            return ToolResult(False, "send_message", "WhatsApp channel not available")

        phone = "".join(c for c in args.get("phone_number", "") if c.isdigit())
        if not phone or len(phone) < 7:
            return ToolResult(False, "send_message", "Invalid phone number")

        # Security: only allow sending to contacts in the allowlist (if configured)
        allowlist = self.config.get("allowlist", [])
        admin_number = self.config.get("admin_number", "")
        if allowlist and phone not in allowlist and phone != admin_number:
            return ToolResult(False, "send_message", f"Cannot send to {phone}: not in allowlist")

        chat_jid = f"{phone}@s.whatsapp.net"
        text = args.get("text", "").strip()
        media_type = args.get("media_type", "")
        media_prompt = args.get("media_prompt", "").strip()
        media_title = args.get("media_title", "Document")

        sent_items = []

        # Generate and send media if requested
        if media_type and media_prompt:
            if media_type == "image":
                media_result = await self._generate_image({"prompt": media_prompt})
            elif media_type == "pdf":
                media_result = await self._create_pdf({"title": media_title, "content": media_prompt})
            elif media_type == "video":
                media_result = await self._generate_video({"prompt": media_prompt})
            else:
                return ToolResult(False, "send_message", f"Unknown media_type: {media_type}")

            if not media_result.success:
                return ToolResult(False, "send_message", f"Media generation failed: {media_result.content}")

            if media_result.media_path:
                try:
                    await self._channel.send_media(chat_jid, media_result.media_path)
                    sent_items.append(f"{media_type} sent")
                except Exception as e:
                    return ToolResult(False, "send_message", f"Failed to send media: {type(e).__name__}")

        # Send text message if provided
        if text:
            try:
                await self._channel.send_text(chat_jid, text)
                sent_items.append("text sent")
            except Exception as e:
                return ToolResult(False, "send_message", f"Failed to send text: {type(e).__name__}")

        if not sent_items:
            return ToolResult(False, "send_message", "Nothing to send - provide text and/or media_type+media_prompt")

        summary = f"Sent to {phone}: {', '.join(sent_items)}"
        return ToolResult(True, "send_message", summary)

    # ── Ask Owner (escalation to admin) ──

    async def _ask_owner(self, args: dict[str, Any]) -> ToolResult:
        """Escalate a question to the phone owner (admin) via WhatsApp.

        Uses the EscalationEngine to create a tracked escalation record,
        then sends the question to the admin's WhatsApp. The admin can
        reply with /respond ESC-XXX <answer> to route the answer back.
        """
        if not self._escalation:
            return ToolResult(False, "ask_owner", "Escalation system not available")
        if not self._channel:
            return ToolResult(False, "ask_owner", "WhatsApp channel not available")

        question = args.get("question", "").strip()
        if not question:
            return ToolResult(False, "ask_owner", "No question provided")

        contact_name = args.get("contact_name", "unknown contact")
        urgency = args.get("urgency", "normal")

        admin_number = self.config.get("admin_number", "")
        if not admin_number:
            return ToolResult(False, "ask_owner", "No admin number configured")

        # Create escalation record
        code, admin_msg = self._escalation.escalate(
            sender_id=contact_name,
            sender_name=contact_name,
            question=question,
            context=f"urgency: {urgency}",
        )

        # Send to admin via WhatsApp
        admin_jid = f"{admin_number}@s.whatsapp.net"
        try:
            await self._channel.send_text(admin_jid, admin_msg)
        except Exception as e:
            return ToolResult(False, "ask_owner", f"Failed to reach owner: {type(e).__name__}")

        return ToolResult(
            success=True,
            tool_name="ask_owner",
            content=(
                f"Question forwarded to owner [{code}]. "
                f"Tell the contact you'll check and get back to them. "
                f"Do NOT make up an answer — wait for the owner's response."
            ),
        )

    # ── Helpers ──

    async def _download_to_media(
        self, url: str, tool_name: str, prompt: str, ext: str
    ) -> ToolResult:
        """Download a URL to the media directory."""
        try:
            if self._client:
                resp = await self._client.get(url, timeout=120.0)
            else:
                async with httpx.AsyncClient() as c:
                    resp = await c.get(url, timeout=120.0)

            if resp.status_code != 200:
                return ToolResult(False, tool_name, f"Download failed: HTTP {resp.status_code}")

            filename = f"generated_{tool_name}_{int(time.time())}{ext}"
            filepath = self.MEDIA_DIR / filename
            filepath.write_bytes(resp.content)

            if filepath.stat().st_size > self.MAX_FILE_SIZE:
                filepath.unlink()
                return ToolResult(False, tool_name, f"Downloaded file exceeds 20MB limit")

            return ToolResult(
                success=True,
                tool_name=tool_name,
                content=f"File generated successfully for: {prompt[:100]}",
                media_path=str(filepath),
            )

        except httpx.TimeoutException:
            return ToolResult(False, tool_name, "File download timed out")

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special chars for reportlab Paragraph (uses XML parser)."""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
