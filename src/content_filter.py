"""Outbound content filter: Block credentials, tokens, and sensitive data.

Scans all outbound messages before they are sent to prevent leaking
API keys, bearer tokens, private keys, system prompts, internal paths,
or financial data (card numbers).

Includes Unicode bypass prevention (NFKD normalization, zero-width stripping).

Ported from nanobot MessageTool filter pattern, generalized for HappyCapy.
"""

import re
import unicodedata
from dataclasses import dataclass, field


# ── Sensitive data patterns ──
# Each tuple: (compiled regex, category, description)

_CREDENTIAL_PATTERNS = [
    (re.compile(r"(?:api[_-]?key|apikey)\s*(?:[:=]|is)\s*\S{10,}", re.I),
     "credentials", "API key assignment"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-_.~+/]{20,}"),
     "credentials", "Bearer token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"),
     "credentials", "AWS access key"),
    (re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----"),
     "credentials", "Private key"),
    (re.compile(r"sk-proj-[a-zA-Z0-9_-]{20,}"),
     "credentials", "OpenAI API key"),
    (re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"),
     "credentials", "Anthropic API key"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36}"),
     "credentials", "GitHub personal access token"),
    (re.compile(r"xox[bpsa]-[a-zA-Z0-9\-]{10,}"),
     "credentials", "Slack token"),
]

_SYSTEM_INTERNAL_PATTERNS = [
    (re.compile(r"(?:SECTION\s+0|IMMUTABLE\s+SECURITY\s+PREAMBLE|NON-NEGOTIABLE\s+IDENTITY|ANTI-INJECTION\s+META-RULES)", re.I),
     "system_prompt", "System prompt section markers"),
    (re.compile(r"/nanobot-src/|/bridge/src/|\.nanobot/config\.json|\.happycapy-whatsapp/config\.json"),
     "internal_path", "Internal file path"),
    (re.compile(r"assistant\.db|contacts\.db|\.nanobot/workspace/sessions/"),
     "internal_path", "Database file path"),
    (re.compile(r"\.bridge-token"),
     "internal_path", "Bridge token file"),
]

_FINANCIAL_PATTERNS = [
    (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
     "financial", "Credit card number pattern"),
]

ALL_SENSITIVE_PATTERNS = _CREDENTIAL_PATTERNS + _SYSTEM_INTERNAL_PATTERNS + _FINANCIAL_PATTERNS

# ── Zero-width and invisible characters to strip ──
_INVISIBLE_CHARS = re.compile(
    "[\u200B-\u200F\u2028-\u202F\u2060-\u206F\uFEFF\u00AD]"
)


@dataclass
class FilterResult:
    """Result of content filtering."""
    is_blocked: bool = False
    category: str = "none"
    description: str = ""
    matches: list[str] = field(default_factory=list)


class ContentFilter:
    """Outbound content filter with Unicode bypass prevention.

    Checks all outbound messages for sensitive data before sending.
    Uses NFKD normalization and zero-width character stripping to
    prevent unicode-based bypass attacks.
    """

    def __init__(self):
        pass

    def _normalize(self, text: str) -> str:
        """Normalize text to defeat Unicode bypass attempts.

        1. NFKD normalization (decompose compatibles)
        2. Strip zero-width and invisible characters
        """
        normalized = unicodedata.normalize("NFKD", text)
        normalized = _INVISIBLE_CHARS.sub("", normalized)
        return normalized

    def check(self, text: str) -> FilterResult:
        """Check outbound message text for sensitive data.

        Checks both original and normalized versions to catch
        Unicode bypass attempts.

        Returns FilterResult. If is_blocked is True, the message
        should NOT be sent.
        """
        if not text:
            return FilterResult()

        # Check both original and normalized text
        normalized = self._normalize(text)
        texts_to_check = [text]
        if normalized != text:
            texts_to_check.append(normalized)

        matches = []
        category = "none"
        description = ""

        for check_text in texts_to_check:
            for pattern, cat, desc in ALL_SENSITIVE_PATTERNS:
                match = pattern.search(check_text)
                if match:
                    matches.append(match.group()[:50])  # Truncate match for logging
                    category = cat
                    description = desc

        if not matches:
            return FilterResult()

        return FilterResult(
            is_blocked=True,
            category=category,
            description=description,
            matches=matches,
        )

    def check_media_path(self, path: str) -> FilterResult:
        """Check if a media file path points to sensitive location."""
        sensitive_prefixes = [
            "/etc/", "/proc/", "/sys/", "/dev/",
            "/.ssh/", "/.gnupg/", "/.aws/", "/.kube/",
            "/.nanobot/config", "/credentials",
            "/.happycapy-whatsapp/config",
        ]
        for prefix in sensitive_prefixes:
            if prefix in path:
                return FilterResult(
                    is_blocked=True,
                    category="sensitive_path",
                    description=f"Sensitive file path: {prefix}",
                    matches=[path[:100]],
                )
        return FilterResult()
