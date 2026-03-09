"""Pluggable message importance scoring (Theorem T_SCOREPLUGIN).

Deterministic scoring returning (score, reasons) tuples.
DM scoring: 1-10 scale. Group scoring: 0-100 scale.
Pure functions + config -- no database needed.

Premise P_SCORE: Every inbound message has a computable importance score
that determines routing priority. The score must be deterministic and explainable.
"""

import re
import time


# Urgent keywords that boost importance (+2 each, max +4)
_URGENT_KEYWORDS = frozenset({
    "urgent", "emergency", "asap", "important", "help",
    "deadline", "payment", "money", "sick", "hospital",
    "accident", "critical", "immediate", "hurry",
})

# Question patterns for group scoring
_QUESTION_PATTERNS = [
    re.compile(r"\?\s*$"),
    re.compile(r"\b(?:can|could|would|will|do|does|is|are|what|when|where|how|who|why)\b.*\?", re.I),
]

# Urgent words for group scoring
_URGENT_WORDS = frozenset({
    "urgent", "asap", "important", "deadline", "emergency",
    "critical", "immediately",
})


class ImportanceScorer:
    """Configurable message importance scoring.

    Theorem T_SCOREPLUGIN: Scoring is pluggable -- different score ranges,
    factors, and thresholds for DM vs group contexts.
    """

    def __init__(self, config: dict, contact_store=None):
        self._config = config
        self._contact_store = contact_store
        # Track recent messages per sender for repetition detection
        self._recent: dict[str, list[float]] = {}  # sender_id -> [timestamps]

    def score_dm(self, content: str, sender_id: str) -> tuple[int, list[str]]:
        """Score a direct message on 1-10 scale.

        Returns (score, reasons) tuple where reasons explains each factor.
        """
        score = 5  # Base score
        reasons: list[str] = []
        text_lower = content.lower()

        # +2 per urgent keyword (max +4)
        urgent_found = [kw for kw in _URGENT_KEYWORDS if kw in text_lower]
        if urgent_found:
            boost = min(len(urgent_found) * 2, 4)
            score += boost
            reasons.append(f"urgent keywords: {', '.join(urgent_found[:3])}")

        # +2 if known contact (has profile)
        if self._contact_store:
            profile = self._contact_store.get_profile(sender_id)
            if profile:
                score += 2
                reasons.append("known contact")

        # +1 if repeated messages (>1 in last 5 min from same sender)
        now = time.time()
        recent = self._recent.get(sender_id, [])
        recent = [t for t in recent if now - t < 300]  # Keep last 5 min
        recent.append(now)
        self._recent[sender_id] = recent[-10:]  # Cap at 10 entries
        if len(recent) > 1:
            score += 1
            reasons.append(f"repeated ({len(recent)} in 5min)")

        # +1 if contains question mark
        if "?" in content:
            score += 1
            reasons.append("question")

        # +1 if very short (<20 chars -- likely urgent/terse)
        if len(content) < 20:
            score += 1
            reasons.append("short message")

        # +2 if ALL CAPS (len > 5 to avoid false positives on "OK", "HI")
        if content.isupper() and len(content) > 5:
            score += 2
            reasons.append("ALL CAPS")

        # +1 if multiple exclamation marks
        if content.count("!") > 1:
            score += 1
            reasons.append("multiple exclamations")

        return (min(score, 10), reasons)

    def score_group(
        self, content: str, sender_id: str,
        mentioned_jids: list[str] | None = None,
        quoted_participant: str = "",
    ) -> tuple[int, list[str]]:
        """Score a group message on 0-100 scale.

        Returns (score, reasons) tuple.
        """
        score = 0
        reasons: list[str] = []
        text_lower = content.lower()
        mentioned = mentioned_jids or []

        admin_number = self._config.get("admin_number", "")

        # +50 if admin @mentioned
        if admin_number:
            admin_jid = f"{admin_number}@s.whatsapp.net"
            if admin_jid in mentioned or admin_number in mentioned:
                score += 50
                reasons.append("admin @mentioned")

        # +40 if replying to owner's message (nanobot GroupAlerter pattern)
        if admin_number and quoted_participant:
            if admin_number in quoted_participant:
                score += 40
                reasons.append("reply to owner's message")

        # +30 if admin phone number appears in text
        if admin_number and admin_number in content:
            score += 30
            reasons.append("admin phone in message")

        # +20 per keyword match (configurable, max 40)
        group_keywords = self._config.get("group_keywords", [])
        matched_kw = [kw for kw in group_keywords if kw.lower() in text_lower]
        if matched_kw:
            kw_boost = min(len(matched_kw) * 20, 40)
            score += kw_boost
            reasons.append(f"keywords: {', '.join(matched_kw[:3])}")

        # +15 for urgent words
        urgent_found = [w for w in _URGENT_WORDS if w in text_lower]
        if urgent_found:
            score += 15
            reasons.append(f"urgent: {', '.join(urgent_found[:2])}")

        # +10 for question patterns
        if any(p.search(content) for p in _QUESTION_PATTERNS):
            score += 10
            reasons.append("question")

        # +10 for @everyone / @all
        if "@everyone" in text_lower or "@all" in text_lower:
            score += 10
            reasons.append("@everyone/@all")

        return (min(score, 100), reasons)
