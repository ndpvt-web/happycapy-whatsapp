"""Send a file (image, PDF, video, audio, document) to a WhatsApp contact.

Theorem T_SEND: Outbound files sent via existing bridge WebSocket media protocol.
Proof: P_MEDIA_OUT (bridge supports base64 media sending) + P_B64 (WhatsApp accepts
all media types via Baileys sendMedia).

Usage:
    python -m src.send_file --to 1234567890 --file /path/to/file.pdf
    python -m src.send_file --to 1234567890@s.whatsapp.net --file image.png --caption "Here you go"
    python -m src.send_file --to 1234567890 --text "Hello from the agent!"

The script connects to the bridge WebSocket, sends the file, waits for
confirmation, and disconnects. Designed for use by the HappyCapy agent.
"""

import argparse
import asyncio
import base64
import json
import mimetypes
import sys
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Error: websockets package required. Run: pip install websockets")
    sys.exit(1)


def normalize_jid(phone: str) -> str:
    """Convert a phone number to WhatsApp JID format.

    Proof: WhatsApp JIDs are phone@s.whatsapp.net for personal chats.
    Groups end in @g.us. If already a JID, pass through unchanged.
    """
    if "@" in phone:
        return phone
    # Strip any non-digit chars (spaces, dashes, plus sign)
    digits = "".join(c for c in phone if c.isdigit())
    return f"{digits}@s.whatsapp.net"


async def send_text(ws, jid: str, text: str) -> dict:
    """Send a text message via the bridge.

    Theorem T_REASONSTRIP: Apply reasoning filter before sending (P_REASONLEAK).
    This utility bypasses WhatsAppChannel.send_text(), so it must apply
    the same defense-in-depth filtering independently.
    """
    from .whatsapp_channel import WhatsAppChannel
    text = WhatsAppChannel._strip_reasoning(text)
    # Layer 4: Post-filter leak detection (Theorem T_REASONSTRIP)
    if WhatsAppChannel._LEAK_DETECTOR_RE.search(text):
        print("[warning] Possible reasoning leak detected in outbound text (post-filter)")
    payload = {"type": "send", "to": jid, "text": text}
    await ws.send(json.dumps(payload, ensure_ascii=False))
    response = await asyncio.wait_for(ws.recv(), timeout=30.0)
    return json.loads(response)


async def send_file(ws, jid: str, file_path: str, caption: str = "") -> dict:
    """Send a file via the bridge as base64 media."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Determine MIME type
    mime, _ = mimetypes.guess_type(file_path)
    if not mime:
        mime = "application/octet-stream"

    # Read and encode file
    b64_data = base64.b64encode(p.read_bytes()).decode("ascii")

    payload = {
        "type": "send",
        "to": jid,
        "text": caption,
        "media": {
            "data": b64_data,
            "mimetype": mime,
            "filename": p.name,
        },
    }
    await ws.send(json.dumps(payload, ensure_ascii=False))
    response = await asyncio.wait_for(ws.recv(), timeout=60.0)
    return json.loads(response)


async def main(args: argparse.Namespace) -> int:
    """Connect to bridge, send file/text, disconnect."""
    # Load config for bridge port and token
    config_file = Path.home() / ".happycapy-whatsapp" / "config.json"
    config = {}
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
        except json.JSONDecodeError:
            pass

    port = args.port or config.get("bridge_port", 3002)
    token = args.token or config.get("bridge_token", "")
    bridge_url = f"ws://127.0.0.1:{port}"

    jid = normalize_jid(args.to)

    try:
        async with websockets.connect(bridge_url) as ws:
            # Authenticate if token is set
            if token:
                await ws.send(json.dumps({"type": "auth", "token": token}))

            if args.file:
                result = await send_file(ws, jid, args.file, args.caption or "")
                print(f"File sent: {result}")
            elif args.text:
                result = await send_text(ws, jid, args.text)
                print(f"Text sent: {result}")
            else:
                print("Error: must specify --file or --text")
                return 1

            if result.get("type") == "error":
                print(f"Bridge error: {result.get('error')}")
                return 1

            return 0

    except ConnectionRefusedError:
        print(f"Error: Cannot connect to bridge at {bridge_url}. Is the WhatsApp service running?")
        return 1
    except asyncio.TimeoutError:
        print("Error: Bridge did not respond in time")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1


def cli():
    parser = argparse.ArgumentParser(
        description="Send a file or text message to a WhatsApp contact via the bridge.",
        epilog="Examples:\n"
               "  python -m src.send_file --to 1234567890 --file report.pdf\n"
               "  python -m src.send_file --to 1234567890 --file photo.jpg --caption 'Check this out'\n"
               "  python -m src.send_file --to 1234567890 --text 'Hello!'\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--to", required=True, help="Phone number or JID of recipient")
    parser.add_argument("--file", help="Path to file to send (image, PDF, video, audio, document)")
    parser.add_argument("--text", help="Text message to send (instead of file)")
    parser.add_argument("--caption", default="", help="Caption for media file")
    parser.add_argument("--port", type=int, help="Bridge WebSocket port (default: from config or 3002)")
    parser.add_argument("--token", default="", help="Bridge authentication token (default: from config)")

    args = parser.parse_args()

    if not args.file and not args.text:
        parser.error("Must specify either --file or --text")

    sys.exit(asyncio.run(main(args)))


if __name__ == "__main__":
    cli()
