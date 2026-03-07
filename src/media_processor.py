"""Media processor for inbound WhatsApp media understanding.

Aristotelian Foundation:
- P_VISION: Modern LLMs understand images via multimodal message format.
- P_PDF: PDFs contain extractable text (structured data).
- P_FFMPEG: ffmpeg extracts audio tracks and keyframes from video.
- P_B64_VISION: AI Gateway accepts base64 image URLs in OpenAI-compatible format.

Theorems:
- T_IMG: Inbound images sent to LLM as multimodal content (P_VISION + P_B64_VISION).
- T_PDF: Inbound PDFs have text extracted and included as context (P_PDF).
- T_VID: Inbound videos have audio extracted for transcription + keyframe for vision
         (P_FFMPEG + P_VISION).
"""

import asyncio
import base64
import mimetypes
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


# ── Constants with proofs ──

# Max pages to extract from PDF. Most WhatsApp-shared PDFs are short documents.
# 50 pages * ~500 words/page = ~25K words = ~33K tokens. Fits in context window.
_PDF_MAX_PAGES = 50

# Max chars of extracted PDF text to include in prompt.
# 30K chars ≈ 10K tokens. Leaves room for system prompt + history + response.
_PDF_MAX_CHARS = 30000

# Video keyframe extraction: take 1 frame.
# Single frame is sufficient for scene understanding; more would bloat payload.
_VIDEO_KEYFRAME_COUNT = 1

# Max image dimension for vision API. Larger images waste tokens without
# proportional understanding gain. 1024px is the sweet spot for most models.
_VISION_MAX_DIMENSION = 1024

# Max file size for media processing (20MB). WhatsApp's own limit is 16MB for
# most media; 20MB provides headroom for documents.
_MAX_MEDIA_SIZE_BYTES = 20 * 1024 * 1024


def _mime_from_path(file_path: str) -> str:
    """Guess MIME type from file path."""
    mime, _ = mimetypes.guess_type(file_path)
    return mime or "application/octet-stream"


def _file_to_base64(file_path: str) -> str:
    """Read a file and return base64-encoded string."""
    return base64.b64encode(Path(file_path).read_bytes()).decode("ascii")


def _is_image_mime(mime: str) -> bool:
    return mime.startswith("image/") and "webp" not in mime  # stickers are webp


def _is_pdf(mime: str, file_path: str) -> bool:
    return mime == "application/pdf" or file_path.lower().endswith(".pdf")


def _is_video_mime(mime: str) -> bool:
    return mime.startswith("video/")


def _is_audio_mime(mime: str) -> bool:
    return mime.startswith("audio/")


# ── Image Processing (Theorem T_IMG) ──


def process_image(file_path: str, mime: str = "") -> dict[str, Any]:
    """Process an image for multimodal AI understanding.

    Returns a dict with:
    - content_parts: list of message content parts for OpenAI-compatible API
    - description: human-readable description of what was processed
    """
    if not mime:
        mime = _mime_from_path(file_path)

    # Normalize mime for data URL (strip parameters like codecs)
    base_mime = mime.split(";")[0].strip()

    b64 = _file_to_base64(file_path)
    data_url = f"data:{base_mime};base64,{b64}"

    return {
        "type": "image",
        "content_parts": [
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
        "description": f"Image ({base_mime})",
    }


# ── PDF Processing (Theorem T_PDF) ──


def process_pdf(file_path: str) -> dict[str, Any]:
    """Extract text from a PDF for inclusion in AI context.

    Returns a dict with:
    - extracted_text: the extracted text content
    - page_count: number of pages processed
    - description: human-readable description
    """
    if not pdfplumber:
        return {
            "type": "pdf",
            "extracted_text": "[PDF received but text extraction unavailable - pdfplumber not installed]",
            "page_count": 0,
            "description": "PDF (extraction unavailable)",
        }

    try:
        text_parts = []
        page_count = 0

        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= _PDF_MAX_PAGES:
                    text_parts.append(f"\n[... truncated at {_PDF_MAX_PAGES} pages, {len(pdf.pages)} total ...]")
                    break

                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"--- Page {i + 1} ---\n{page_text}")
                    page_count += 1

            total_pages = len(pdf.pages)

        full_text = "\n\n".join(text_parts)

        # Truncate if too long
        if len(full_text) > _PDF_MAX_CHARS:
            full_text = full_text[:_PDF_MAX_CHARS] + "\n[... text truncated ...]"

        return {
            "type": "pdf",
            "extracted_text": full_text if full_text.strip() else "[PDF contains no extractable text - may be scanned/image-based]",
            "page_count": page_count,
            "total_pages": total_pages,
            "description": f"PDF ({page_count}/{total_pages} pages extracted)",
        }
    except Exception as e:
        # Theorem T_ERRREDACT: Don't include raw exception in user-facing text (P_LOGPII).
        print(f"PDF extraction error: {type(e).__name__}")
        return {
            "type": "pdf",
            "extracted_text": "[PDF extraction failed]",
            "page_count": 0,
            "description": "PDF (extraction error)",
        }


# ── Video Processing (Theorem T_VID) ──


def extract_video_keyframe(file_path: str) -> str | None:
    """Extract a single keyframe from a video using ffmpeg.

    Returns path to extracted JPEG, or None on failure.
    """
    try:
        # Theorem T_TMPFILE: Use mkstemp() to atomically create temp file (P_TMPRACE).
        # mktemp() has TOCTOU race: another process can create the file between
        # name generation and use, causing data corruption or symlink attacks.
        fd, output_path = tempfile.mkstemp(suffix=".jpg", prefix="wa_keyframe_")
        os.close(fd)  # ffmpeg will write to this path
        result = subprocess.run(
            [
                "ffmpeg", "-i", file_path,
                "-vf", "select=eq(ptype\\,I)",  # Select I-frames (keyframes)
                "-vframes", str(_VIDEO_KEYFRAME_COUNT),
                "-q:v", "2",  # High quality JPEG
                "-y", output_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and Path(output_path).exists():
            return output_path
    except Exception as e:
        print(f"Keyframe extraction error: {e}")

    # Fallback: try simpler approach (grab frame at 1 second)
    try:
        fd, output_path = tempfile.mkstemp(suffix=".jpg", prefix="wa_keyframe_")
        os.close(fd)
        result = subprocess.run(
            [
                "ffmpeg", "-i", file_path,
                "-ss", "1",  # Seek to 1 second
                "-vframes", "1",
                "-q:v", "2",
                "-y", output_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and Path(output_path).exists():
            return output_path
    except Exception as e:
        print(f"Keyframe fallback error: {e}")

    return None


def extract_video_audio(file_path: str) -> str | None:
    """Extract audio track from video using ffmpeg.

    Returns path to extracted audio file, or None on failure.
    """
    try:
        # Theorem T_TMPFILE: Use mkstemp() for atomic temp file creation (P_TMPRACE).
        fd, output_path = tempfile.mkstemp(suffix=".ogg", prefix="wa_audio_")
        os.close(fd)
        result = subprocess.run(
            [
                "ffmpeg", "-i", file_path,
                "-vn",  # No video
                "-acodec", "libopus",
                "-b:a", "64k",  # 64kbps is sufficient for speech
                "-y", output_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            return output_path
    except Exception as e:
        print(f"Audio extraction error: {e}")

    return None


async def process_video(file_path: str, config: dict) -> dict[str, Any]:
    """Process a video: extract keyframe for vision + audio for transcription.

    Theorem T_VPARA: Run keyframe + audio extraction in parallel (P_PARA).
    Theorem T_LAZY: Removed separate ffprobe duration call (P_LAZY).
    Uses asyncio.to_thread to avoid blocking the event loop.

    Returns a dict with:
    - content_parts: multimodal content parts (keyframe image)
    - audio_path: path to extracted audio (for separate transcription)
    - description: human-readable description
    """
    # Run keyframe + audio extraction in parallel (Theorem T_VPARA).
    # asyncio.to_thread offloads blocking subprocess.run to thread pool,
    # preventing event loop starvation for concurrent contacts.
    keyframe_path, audio_path = await asyncio.gather(
        asyncio.to_thread(extract_video_keyframe, file_path),
        asyncio.to_thread(extract_video_audio, file_path),
    )

    result: dict[str, Any] = {
        "type": "video",
        "content_parts": [],
        "audio_path": None,
        "description": "Video",
    }

    # Process keyframe for visual understanding
    if keyframe_path:
        try:
            img_result = process_image(keyframe_path, "image/jpeg")
            result["content_parts"] = img_result["content_parts"]
            result["keyframe_path"] = keyframe_path
        except Exception:
            pass

    if audio_path:
        result["audio_path"] = audio_path

    return result


# ── Audio Transcription ──


async def transcribe_audio(
    file_path: str, config: dict, client: "httpx.AsyncClient | None" = None,
) -> str:
    """Transcribe an audio file using the configured Whisper API.

    Theorem T_POOL: Reuses shared httpx client when provided (P_POOL).
    Falls back to creating a one-shot client for backwards compatibility.
    """
    if not httpx:
        return "[Transcription unavailable - httpx not installed]"

    api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
    if not api_key:
        return "[Transcription unavailable - no API key]"

    whisper_url = config.get(
        "whisper_api_url", "https://api.groq.com/openai/v1/audio/transcriptions"
    )

    try:
        with open(file_path, "rb") as f:
            request_kwargs = dict(
                url=whisper_url,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (Path(file_path).name, f, "audio/ogg")},
                data={"model": "whisper-large-v3"},
                timeout=60.0,
            )
            if client:
                resp = await client.post(**request_kwargs)
            else:
                async with httpx.AsyncClient() as _c:
                    resp = await _c.post(**request_kwargs)

            if resp.status_code == 200:
                text = resp.json().get("text", "")
                return text if text else "[Audio contained no speech]"
            else:
                return f"[Transcription failed: HTTP {resp.status_code}]"
    except Exception as e:
        # Theorem T_ERRREDACT: Log error type only, not full details (P_LOGPII).
        print(f"Transcription error: {type(e).__name__}")
        return "[Transcription failed]"


# ── Sticker Processing ──


def process_sticker(file_path: str) -> dict[str, Any]:
    """Process a sticker image for vision understanding."""
    b64 = _file_to_base64(file_path)
    data_url = f"data:image/webp;base64,{b64}"

    return {
        "type": "sticker",
        "content_parts": [
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
        "description": "Sticker",
    }


# ── Document Processing (non-PDF) ──


def process_document(file_path: str, mime: str = "", filename: str = "") -> dict[str, Any]:
    """Process a document file. PDFs get text extraction; others get metadata only."""
    if not mime:
        mime = _mime_from_path(file_path)

    if _is_pdf(mime, file_path):
        return process_pdf(file_path)

    # For non-PDF documents, we can't do much beyond acknowledging
    name = filename or Path(file_path).name
    size = Path(file_path).stat().st_size if Path(file_path).exists() else 0
    size_str = f"{size / 1024:.1f}KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f}MB"

    return {
        "type": "document",
        "extracted_text": f"[Document received: {name} ({size_str}, {mime})]",
        "description": f"Document: {name} ({size_str})",
    }


# ── Unified Media Processing ──


async def process_media(
    file_path: str,
    media_type: str,
    mime: str,
    filename: str,
    config: dict,
    client: "httpx.AsyncClient | None" = None,
) -> dict[str, Any]:
    """Process any inbound media file and return structured understanding.

    Theorem T_POOL: Accepts shared httpx client for connection reuse (P_POOL).

    Returns a dict with:
    - type: media type (image, pdf, video, audio, sticker, document)
    - content_parts: multimodal content parts for vision API (if applicable)
    - extracted_text: text content extracted from media (if applicable)
    - audio_transcription: transcribed text from audio/video (if applicable)
    - description: human-readable summary
    """
    # Safety: check file size
    try:
        size = Path(file_path).stat().st_size
        if size > _MAX_MEDIA_SIZE_BYTES:
            return {
                "type": media_type,
                "description": f"Media too large ({size / (1024 * 1024):.1f}MB > {_MAX_MEDIA_SIZE_BYTES / (1024 * 1024):.0f}MB limit)",
            }
    except OSError:
        return {"type": media_type, "description": "Media file not accessible"}

    result: dict[str, Any] = {"type": media_type}

    if media_type == "image":
        img = process_image(file_path, mime)
        result.update(img)

    elif media_type == "audio":
        if config.get("voice_transcription", False):
            transcription = await transcribe_audio(file_path, config, client=client)
            result["audio_transcription"] = transcription
            result["description"] = "Voice message (transcribed)"
        else:
            result["description"] = "Voice message received"

    elif media_type == "video":
        vid = await process_video(file_path, config)
        result.update(vid)

        # Transcribe video audio if voice transcription is enabled
        if vid.get("audio_path") and config.get("voice_transcription", False):
            transcription = await transcribe_audio(vid["audio_path"], config, client=client)
            result["audio_transcription"] = transcription
            # Cleanup extracted audio
            try:
                Path(vid["audio_path"]).unlink(missing_ok=True)
            except OSError:
                pass

    elif media_type == "sticker":
        stk = process_sticker(file_path)
        result.update(stk)

    elif media_type == "document":
        doc = process_document(file_path, mime, filename)
        result.update(doc)

    else:
        result["description"] = f"Unknown media type: {media_type}"

    return result


def cleanup_temp_files(*paths: str | None) -> None:
    """Remove temporary files created during media processing."""
    for p in paths:
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
