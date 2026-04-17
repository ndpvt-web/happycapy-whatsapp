"""Mac Bridge integration for HappyCapy WhatsApp bot.

Gives the WhatsApp AI agent remote access to the user's Mac via Capy Bridge.
Commands are routed through the HappyCapy relay API (not direct tunnel).

Security: Only the authorized phone number (config["mac_bridge_authorized_jid"])
can trigger Mac commands. All other senders get a polite refusal.
"""

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import aiohttp

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult


# ── Config from environment ──
_BASE_URL = os.environ.get("CAPY_BASE_URL", "https://happycapy.ai")
_SECRET = os.environ.get("CAPY_SECRET", "")
_USER_ID = os.environ.get("CAPY_USER_ID", "")


_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "mac_status",
            "description": (
                "Check if the user's Mac is connected and online via Capy Bridge. "
                "Returns machine name, hostname, arch, and online status."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mac_exec",
            "description": (
                "Execute a shell command on the user's Mac. Use for running terminal "
                "commands like checking processes, git operations, running scripts, "
                "system administration, opening apps, etc. "
                "Returns stdout, stderr, and exit code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute on the Mac.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for the command. Defaults to home dir.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 120, max 600).",
                        "default": 120,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mac_read_file",
            "description": (
                "Read a file from the user's Mac. Supports text (utf8) and binary (base64) encoding. "
                "Use for retrieving documents, configs, logs, or any file content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file (e.g., ~/Documents/notes.txt).",
                    },
                    "encoding": {
                        "type": "string",
                        "enum": ["utf8", "base64"],
                        "description": "File encoding. Use utf8 for text, base64 for binary.",
                        "default": "utf8",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mac_write_file",
            "description": (
                "Write content to a file on the user's Mac. Creates parent directories "
                "if needed. Use for saving documents, configs, scripts, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to write to (e.g., ~/Desktop/output.txt).",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mac_list_files",
            "description": (
                "List files and directories on the user's Mac. Returns names, types, "
                "sizes, and modification times."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (e.g., ~/Desktop).",
                    },
                    "details": {
                        "type": "boolean",
                        "description": "Include size and modification time. Default true.",
                        "default": True,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mac_system_info",
            "description": (
                "Get system information from the user's Mac: hostname, OS version, "
                "architecture, CPU, memory, disk usage, battery status, Xcode version."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mac_screenshot",
            "description": (
                "Take a screenshot of the user's Mac screen and send it as an image "
                "on WhatsApp. Can capture the full screen or a specific application window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "description": (
                            "Optional: name of the app window to capture (e.g., 'Safari', "
                            "'Xcode', 'Terminal'). If omitted, captures the full screen."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
]

# Inject 'device' parameter into every tool (multi-Mac support)
_DEVICE_PROP = {
    "type": "string",
    "description": (
        "Which Mac to target: 'air' for MacBook Air, 'pro' for MacBook Pro. "
        "Omit to auto-pick whichever is online."
    ),
}
for _td in _TOOL_DEFINITIONS:
    props = _td["function"]["parameters"].get("properties")
    if props is None:
        _td["function"]["parameters"]["properties"] = {"device": _DEVICE_PROP}
    else:
        props["device"] = _DEVICE_PROP


class Integration(BaseIntegration):
    """Mac Bridge integration -- remote Mac control via WhatsApp."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._sender_jid: str = ""
        self._machines_cache: list[dict] | None = None  # Cached machine list

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="mac_bridge",
            display_name="Mac Bridge",
            description="Remote access to the user's Mac via Capy Bridge",
        )

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        if not config.get("mac_bridge_enabled", False):
            return ""
        return (
            "## Mac Bridge\n"
            "You have remote access to the user's Mac computer via Capy Bridge.\n"
            "Available tools: mac_status, mac_exec, mac_read_file, mac_write_file, "
            "mac_list_files, mac_system_info, mac_screenshot.\n"
            "Use these ONLY when the user explicitly asks you to do something on their Mac.\n"
            "Examples:\n"
            "- 'check if my Mac is on' -> mac_status\n"
            "- 'run ls on my Mac' -> mac_exec\n"
            "- 'send me that file from my Desktop' -> mac_read_file\n"
            "- 'check my disk space' -> mac_system_info\n"
            "- 'open Slack on my Mac' -> mac_exec with 'open -a Slack'\n"
            "- 'screenshot my Mac' -> mac_screenshot (sends image on WhatsApp)\n"
            "- 'screenshot Safari' -> mac_screenshot with window='Safari'\n"
            "NEVER use Mac tools proactively. Only when the user asks.\n"
        )

    def _is_authorized(self) -> bool:
        """Check if the current sender is authorized for Mac commands."""
        authorized_jid = self.config.get("mac_bridge_authorized_jid", "")
        if not authorized_jid:
            return False
        # Normalize: strip @s.whatsapp.net and compare phone numbers
        sender_phone = self._sender_jid.split("@")[0]
        auth_phone = authorized_jid.split("@")[0]
        # Also strip leading + for comparison
        sender_clean = sender_phone.lstrip("+")
        auth_clean = auth_phone.lstrip("+")
        return sender_clean == auth_clean

    async def _fetch_machines(self) -> list[dict]:
        """Fetch all registered Mac bridges for this user. Cached per request."""
        if self._machines_cache is not None:
            return self._machines_cache
        try:
            url = f"{_BASE_URL}/api/bridge/machines?userId={_USER_ID}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={"Authorization": f"Bearer {_SECRET}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    self._machines_cache = data.get("machines", [])
                    return self._machines_cache
        except Exception:
            return []

    async def _get_bridge_id(self, device: str = "") -> str | None:
        """Find the right Mac bridge. If device is given, fuzzy-match by name/hostname.

        When no device hint is given, prefers the most-recently-connected Mac
        (highest lastConnectedAt) so the actively-used machine is targeted first.
        """
        machines = await self._fetch_machines()
        online = [m for m in machines if m.get("online")]
        if not online:
            return None

        if device:
            # Fuzzy match: "air" matches "MacBook Air", "pro" matches "MacBook Pro"
            needle = device.lower()
            for m in online:
                name = (m.get("name") or "").lower()
                hostname = (m.get("hostname") or "").lower()
                if needle in name or needle in hostname:
                    return m["bridgeId"]
            # No match -- fall through to most-recently-connected

        # Prefer the Mac that connected most recently (likely the one in active use)
        online.sort(key=lambda m: m.get("lastConnectedAt", 0), reverse=True)
        return online[0]["bridgeId"]

    async def _send_command(self, command: str, payload: dict, device: str = "") -> dict:
        """Send a command to the Mac via the Bridge relay API."""
        bridge_id = await self._get_bridge_id(device)
        if not bridge_id:
            return {"error": "No Mac connected. Ask the user to connect via Capy Bridge."}

        url = f"{_BASE_URL}/api/bridge/command"
        body = {
            "userId": _USER_ID,
            "bridgeId": bridge_id,
            "command": command,
            "payload": payload,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {_SECRET}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=130),
                ) as resp:
                    if resp.status == 503:
                        # Mac went offline -- invalidate cache
                        self._machines_cache = None
                        return {"error": "Mac is offline. It may have gone to sleep or disconnected."}
                    raw = await resp.json()
                    # Normalize: Bridge API wraps payload in {"success":true,"data":{...}}
                    # Unwrap so callers can do result.get("stdout") directly.
                    if isinstance(raw.get("data"), dict):
                        return raw["data"]
                    return raw
        except Exception as e:
            return {"error": f"Bridge communication failed: {type(e).__name__}: {e}"}

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        # Feature flag check
        if not self.config.get("mac_bridge_enabled", False):
            return ToolResult(False, tool_name, "Mac Bridge is disabled in configuration.")

        # Security gate
        if not self._is_authorized():
            return ToolResult(
                False, tool_name,
                "Mac Bridge access denied. Only the authorized user can control the Mac."
            )

        # Extract device targeting (e.g., "air", "pro") -- shared across all tools
        # Invalidate machine cache per request for fresh data
        self._machines_cache = None
        device = arguments.get("device", "")

        if tool_name == "mac_status":
            return await self._handle_status()
        elif tool_name == "mac_exec":
            return await self._handle_exec(arguments, device)
        elif tool_name == "mac_read_file":
            return await self._handle_read_file(arguments, device)
        elif tool_name == "mac_write_file":
            return await self._handle_write_file(arguments, device)
        elif tool_name == "mac_list_files":
            return await self._handle_list_files(arguments, device)
        elif tool_name == "mac_system_info":
            return await self._handle_system_info(device)
        elif tool_name == "mac_screenshot":
            return await self._handle_screenshot(arguments, device)
        else:
            return ToolResult(False, tool_name, f"Unknown Mac Bridge tool: {tool_name}")

    async def _handle_status(self) -> ToolResult:
        """Check Mac connection status."""
        bridge_id = await self._get_bridge_id()
        if bridge_id:
            # Get more details
            try:
                url = f"{_BASE_URL}/api/bridge/machines?userId={_USER_ID}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        headers={"Authorization": f"Bearer {_SECRET}"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        data = await resp.json()
                        for m in data.get("machines", []):
                            if m.get("bridgeId") == bridge_id:
                                return ToolResult(True, "mac_status", (
                                    f"Mac is ONLINE\n"
                                    f"Name: {m.get('name', 'Unknown')}\n"
                                    f"Hostname: {m.get('hostname', 'Unknown')}\n"
                                    f"Architecture: {m.get('arch', 'Unknown')}\n"
                                    f"Last connected: {m.get('lastConnectedAt', 'Unknown')}"
                                ))
            except Exception:
                pass
            return ToolResult(True, "mac_status", "Mac is ONLINE (connected via Capy Bridge)")
        return ToolResult(True, "mac_status", "Mac is OFFLINE. No connected Mac found.")

    async def _handle_exec(self, args: dict, device: str = "") -> ToolResult:
        """Execute a terminal command on the Mac."""
        command = args.get("command", "").strip()
        if not command:
            return ToolResult(False, "mac_exec", "Command is required.")

        payload = {"command": command}
        if args.get("cwd"):
            payload["cwd"] = args["cwd"]
        if args.get("timeout"):
            payload["timeout"] = min(int(args["timeout"]), 600)

        result = await self._send_command("terminal/exec", payload, device)
        if "error" in result:
            return ToolResult(False, "mac_exec", result["error"])

        # Format output (_send_command unwraps "data" envelope)
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        exit_code = result.get("exitCode", result.get("exit_code", "?"))

        lines = [f"Exit code: {exit_code}"]
        if stdout:
            lines.append(f"stdout:\n{stdout[:3000]}")
        if stderr:
            lines.append(f"stderr:\n{stderr[:1000]}")
        return ToolResult(exit_code == 0, "mac_exec", "\n".join(lines))

    async def _handle_read_file(self, args: dict, device: str = "") -> ToolResult:
        """Read a file from the Mac."""
        path = args.get("path", "").strip()
        if not path:
            return ToolResult(False, "mac_read_file", "File path is required.")

        encoding = args.get("encoding", "utf8")
        result = await self._send_command("files/read", {"path": path, "encoding": encoding}, device)
        if "error" in result:
            return ToolResult(False, "mac_read_file", result["error"])

        content = result.get("content", "")
        if encoding == "base64":
            return ToolResult(True, "mac_read_file", f"File read (base64, {len(content)} chars). Content available.")
        # Truncate large files for LLM context
        if len(content) > 5000:
            content = content[:5000] + f"\n... (truncated, {len(content)} total chars)"
        return ToolResult(True, "mac_read_file", f"File: {path}\n\n{content}")

    async def _handle_write_file(self, args: dict, device: str = "") -> ToolResult:
        """Write a file to the Mac."""
        path = args.get("path", "").strip()
        content = args.get("content", "")
        if not path:
            return ToolResult(False, "mac_write_file", "File path is required.")

        result = await self._send_command("files/write", {"path": path, "content": content, "mkdir": True}, device)
        if "error" in result:
            return ToolResult(False, "mac_write_file", result["error"])
        return ToolResult(True, "mac_write_file", f"File written successfully: {path}")

    async def _handle_list_files(self, args: dict, device: str = "") -> ToolResult:
        """List directory contents on the Mac."""
        path = args.get("path", "").strip()
        if not path:
            return ToolResult(False, "mac_list_files", "Directory path is required.")

        details = args.get("details", True)
        result = await self._send_command("files/list", {"path": path, "details": details}, device)
        if "error" in result:
            return ToolResult(False, "mac_list_files", result["error"])

        items = result.get("entries", result.get("items", []))
        if not items:
            return ToolResult(True, "mac_list_files", f"Directory is empty: {path}")

        lines = [f"Contents of {path}:"]
        for item in items[:50]:  # Limit to 50 entries
            name = item.get("name", "?")
            kind = item.get("type", "file")
            size = item.get("size", "")
            prefix = "d" if kind == "directory" else "f"
            size_str = f" ({size} bytes)" if size else ""
            lines.append(f"  [{prefix}] {name}{size_str}")
        if len(items) > 50:
            lines.append(f"  ... and {len(items) - 50} more")
        return ToolResult(True, "mac_list_files", "\n".join(lines))

    async def _handle_system_info(self, device: str = "") -> ToolResult:
        """Get Mac system information."""
        result = await self._send_command("system/info", {}, device)
        if "error" in result:
            return ToolResult(False, "mac_system_info", result["error"])

        lines = ["Mac System Information:"]
        for key, val in result.items():
            if val:
                lines.append(f"  {key}: {val}")
        return ToolResult(True, "mac_system_info", "\n".join(lines))

    async def _try_screenshot_on(self, device: str, window: str, ts: int) -> tuple[bool, str, str]:
        """Attempt a screenshot on a specific device. Returns (success, b64_content, error_msg)."""
        remote_path = f"/tmp/capy_screenshot_{ts}.png"

        if window:
            cmd = (
                f'WINDOW_ID=$(osascript -e \'tell application "System Events" to '
                f'set wid to id of first window of (first process whose name contains "{window}")\' '
                f'-e \'return wid\' 2>/dev/null) && '
                f'screencapture -l "$WINDOW_ID" -x "{remote_path}" 2>&1 || '
                f'screencapture -x "{remote_path}" 2>&1'
            )
        else:
            cmd = f'screencapture -x "{remote_path}" 2>&1; ls -la "{remote_path}" 2>/dev/null'

        exec_result = await self._send_command("terminal/exec", {"command": cmd, "timeout": 15}, device)
        if "error" in exec_result:
            return False, "", f"Command failed: {exec_result['error']}"

        # Check for known macOS screencapture errors in stdout
        stdout = exec_result.get("stdout", "")
        if "could not create image" in stdout.lower():
            return False, "", "Screen is locked or display unavailable"

        # Read the screenshot file
        read_result = await self._send_command("files/read", {"path": remote_path, "encoding": "base64"}, device)
        if "error" in read_result:
            return False, "", f"File not created (screen may be locked)"

        b64_content = read_result.get("content", "")
        if not b64_content:
            # Clean up empty file
            await self._send_command("files/delete", {"path": remote_path}, device)
            return False, "", "Screenshot file empty (screen likely locked)"

        # Clean up remote file
        await self._send_command("files/delete", {"path": remote_path}, device)
        return True, b64_content, ""

    async def _handle_screenshot(self, args: dict, device: str = "") -> ToolResult:
        """Take a screenshot on the Mac and return it as a WhatsApp image.

        If the targeted Mac fails (locked screen, no display), automatically
        tries other online Macs before giving up.
        """
        window = args.get("window", "").strip()
        ts = int(time.time())

        # Try the requested device first
        success, b64_content, error_msg = await self._try_screenshot_on(device, window, ts)

        # If it failed, try other online Macs as fallback
        if not success:
            machines = await self._fetch_machines()
            online = [m for m in machines if m.get("online")]
            primary_id = await self._get_bridge_id(device)
            tried_name = device or "default"

            for m in online:
                if m["bridgeId"] == primary_id:
                    continue  # Skip the one that already failed
                fallback_name = m.get("name", m["bridgeId"])
                # Derive a device hint from the machine name
                fb_hint = ""
                name_lower = fallback_name.lower()
                if "air" in name_lower:
                    fb_hint = "air"
                elif "pro" in name_lower:
                    fb_hint = "pro"

                success, b64_content, fb_error = await self._try_screenshot_on(fb_hint or fallback_name, window, ts)
                if success:
                    tried_name = fallback_name
                    break

        if not success:
            return ToolResult(
                False, "mac_screenshot",
                f"Screenshot failed on all Macs. Last error: {error_msg}. "
                "The screen may be locked or the display is off. "
                "Ask the user to unlock their Mac and try again."
            )

        # Save to local media directory
        media_dir = Path.home() / ".happycapy-whatsapp" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        local_path = media_dir / f"mac_screenshot_{ts}.png"
        local_path.write_bytes(base64.b64decode(b64_content))

        caption = f"Screenshot of {'window: ' + window if window else 'full screen'}"
        return ToolResult(
            True, "mac_screenshot", caption,
            media_path=str(local_path),
        )
