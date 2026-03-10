"""FabricationGuard: Detect fabricated personal information in AI responses.

Prevents the AI from fabricating specific personal claims (location, activity,
companions, availability, emotional state) that it doesn't actually know.

Vague deflections are allowed ("lemme get back to you").
Specific fabrications are blocked ("I'm at the gym").

Ported from nanobot FabricationGuard pattern, generalized for HappyCapy.
"""

import re
from dataclasses import dataclass, field


# ── Fabrication detection patterns ──
# Each tuple: (compiled regex, category, base confidence)

_LOCATION_PATTERNS = [
    (re.compile(r"\b(?:i(?:'?m| am)|im)\s+(?:at|in|near|heading to|going to|on my way to)\s+(?:the\s+)?\w+", re.I),
     "location", 0.65),
    (re.compile(r"\b(?:i(?:'?m| am)|im)\s+(?:home|out|outside|here|there)\b", re.I),
     "location", 0.60),
    (re.compile(r"\bjust\s+(?:at|in)\s+(?:the\s+)?\w+", re.I),
     "location", 0.60),
    (re.compile(r"\b(?:currently|right now|at the moment)\b.{0,30}\b(?:at|in|near)\s+\w+", re.I),
     "location", 0.70),
]

_ACTIVITY_PATTERNS = [
    (re.compile(r"\b(?:i(?:'?m| am)|im)\s+(?:working|handling|dealing|caught up|doing|watching|eating|cooking|studying|exercising|driving|sleeping|cleaning|shopping|reading)\b", re.I),
     "activity", 0.60),
    (re.compile(r"\bjust\s+(?:handling|doing|working|watching|chilling|relaxing|hanging)\b", re.I),
     "activity", 0.55),
    (re.compile(r"\b(?:handling|dealing with|caught up with|tied up with)\s+(?:some\s+)?(?:stuff|things|work)\b", re.I),
     "activity", 0.50),
    (re.compile(r"\b(?:working on|busy with|handling|dealing with)\s+\w+\s+\w+", re.I),
     "activity", 0.65),
]

_COMPANION_PATTERNS = [
    (re.compile(r"\b(?:i(?:'?m| am)|im)\s+(?:with|hanging out with|meeting)\s+(?:some\s+|my\s+|a\s+)?\w+", re.I),
     "companion", 0.70),
]

_TIMELINE_PATTERNS = [
    (re.compile(r"\b(?:i(?:'ll| will)|ill)\s+be\s+(?:free|available|done|back|there)\s+(?:at|by|in|around|after)\s+\d", re.I),
     "timeline", 0.65),
    (re.compile(r"\bin\s+(?:an?\s+)?(?:hour|couple|few)\b", re.I),
     "timeline", 0.50),
]

_AVAILABILITY_PATTERNS = [
    (re.compile(r"\b(?:i(?:'?m| am)|im)\s+(?:kinda|pretty|super|really)?\s*(?:busy|tied up|swamped|occupied)\b", re.I),
     "availability", 0.55),
]

_STATE_PATTERNS = [
    (re.compile(r"\b(?:i(?:'?m| am)|im)\s+(?:feeling|not feeling|kinda|pretty)\s+(?:tired|sick|unwell|stressed|exhausted|sleepy|lazy)\b", re.I),
     "state", 0.60),
]

ALL_PATTERNS = (
    _LOCATION_PATTERNS + _ACTIVITY_PATTERNS + _COMPANION_PATTERNS +
    _TIMELINE_PATTERNS + _AVAILABILITY_PATTERNS + _STATE_PATTERNS
)

# ── Safe patterns: override detection (these bypass fabrication check) ──

_SAFE_PATTERNS = [
    re.compile(r"\blet me (?:check|get back|find out)\b", re.I),
    re.compile(r"\blemme (?:check|get back)\b", re.I),
    re.compile(r"\bi(?:'ll| will) (?:check|get back to you|let you know|confirm)\b", re.I),
    re.compile(r"\baccording to\b|\bbased on (?:my )?(?:calendar|schedule|notes)\b", re.I),
    re.compile(r"\bi (?:don'?t|do not) (?:know|remember|recall)\b", re.I),
]

# ── Allowed deflections: generic busy/working patterns that are OK ──

_ALLOWED_DEFLECTIONS = [
    re.compile(r"\bi(?:'ll| will) (?:get back|hit you up|let you know|text you|msg you)\b", re.I),
    re.compile(r"\bgimme (?:a )?(?:sec|min|moment|bit)\b", re.I),
    re.compile(r"\bone sec\b", re.I),
    re.compile(r"\b(?:im|i'?m) busy\b(?!\s+(?:at|with|in|doing))", re.I),  # "im busy" alone is OK
    re.compile(r"\bworking on (?:it|getting|that)\b", re.I),
    re.compile(r"\bbusy (?:rn|right now|atm|with (?:stuff|things|work))\b", re.I),
]

# Default safe deflection replacement
_DEFLECTION_POOL = [
    "hmm let me check on that",
    "not sure about that tbh",
    "lemme get back to you",
    "i'll check and let you know",
    "hold on, let me find out",
]

DEFAULT_DEFLECTION = "ill get back to you on that"


@dataclass
class FabricationResult:
    """Result of fabrication detection."""
    is_fabrication: bool = False
    category: str = "none"
    confidence: float = 0.0
    matches: list[str] = field(default_factory=list)
    replacement: str = ""


class FabricationGuard:
    """Detect fabricated personal claims in outbound AI responses.

    Checks AI response text for specific personal fabrications (location,
    activity, companions, timeline, availability, emotional state).
    Safe deflections and vague responses are allowed.
    """

    def __init__(self, confidence_threshold: float = 0.70):
        self.confidence_threshold = confidence_threshold
        self._deflection_idx = 0

    def check(self, text: str) -> FabricationResult:
        """Check AI response text for fabricated personal claims.

        Returns FabricationResult. If is_fabrication is True, the response
        should be replaced with the `replacement` text.
        """
        # Check safe patterns first - if any match, skip fabrication check
        for pattern in _SAFE_PATTERNS:
            if pattern.search(text):
                return FabricationResult()

        # Check allowed deflections
        for pattern in _ALLOWED_DEFLECTIONS:
            if pattern.search(text):
                return FabricationResult()

        # Check fabrication patterns
        matches = []
        categories = set()
        max_confidence = 0.0

        for pattern, category, base_confidence in ALL_PATTERNS:
            match = pattern.search(text)
            if match:
                matches.append(match.group())
                categories.add(category)
                if base_confidence > max_confidence:
                    max_confidence = base_confidence

        if not matches or max_confidence < self.confidence_threshold:
            return FabricationResult()

        # Cycle through varied deflections instead of the same one every time
        deflection = _DEFLECTION_POOL[self._deflection_idx % len(_DEFLECTION_POOL)]
        self._deflection_idx += 1

        return FabricationResult(
            is_fabrication=True,
            category=", ".join(sorted(categories)),
            confidence=max_confidence,
            matches=matches,
            replacement=deflection,
        )
