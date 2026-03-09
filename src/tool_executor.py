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
                "Create a PDF document with formatted text content. "
                "Use when the user asks for a document, report, letter, or PDF file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the document",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content of the document. Use \\n\\n for paragraph breaks.",
                    },
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information. "
                "Use when you need up-to-date info you don't have."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    }
                },
                "required": ["query"],
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
    """

    MEDIA_DIR = Path.home() / ".happycapy-whatsapp" / "media"
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB WhatsApp limit
    VIDEO_TIMEOUT = 300  # 5 minutes max for video generation

    def __init__(self, config: dict[str, Any], client: "httpx.AsyncClient | None" = None):
        self.config = config
        self._client = client
        self.MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool by name and return the result.

        Never raises -- all exceptions are caught and returned as ToolResult with success=False.
        """
        handlers = {
            "generate_image": self._generate_image,
            "generate_video": self._generate_video,
            "create_pdf": self._create_pdf,
            "web_search": self._web_search,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(
                success=False,
                tool_name=tool_name,
                content=f"Unknown tool: {tool_name}",
                error_message=f"Unknown tool: {tool_name}",
            )

        try:
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
        """Create a PDF document using reportlab."""
        if SimpleDocTemplate is None:
            return ToolResult(False, "create_pdf", "PDF creation unavailable (reportlab not installed)")

        title = args.get("title", "Document").strip()
        content = args.get("content", "").strip()

        if not content:
            return ToolResult(False, "create_pdf", "No content provided for PDF")

        filename = f"generated_pdf_{int(time.time())}.pdf"
        filepath = self.MEDIA_DIR / filename

        try:
            doc = SimpleDocTemplate(str(filepath), pagesize=letter)
            styles = getSampleStyleSheet()
            story = []

            # Title
            story.append(Paragraph(self._escape_html(title), styles["Title"]))
            story.append(Spacer(1, 12))

            # Content paragraphs
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

    # ── Web Search (placeholder) ──

    async def _web_search(self, args: dict[str, Any]) -> ToolResult:
        """Placeholder for web search. Not yet implemented."""
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(False, "web_search", "No search query provided")

        return ToolResult(
            success=False,
            tool_name="web_search",
            content="Web search is not yet available. Please try again later.",
            error_message="Not implemented",
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
