"""LLM-based intent classifier for admin escalation decisions.

Theorem T_INTENTCLASS: The escalation decision must answer one question:
"Can the AI handle this confidently, or does the owner NEED to see it?"

This replaces the broken deterministic importance scorer for escalation.
The scorer (importance_scorer.py) still runs for queue priority and status
auto-reply, but escalation uses this LLM classifier.

Design axioms:
  A1: A known contact is NOT a reason to escalate -- familiarity reduces urgency.
  A2: The AI can handle routine conversation without owner involvement.
  A3: Only escalate when owner decision-making power is genuinely required.
  A4: Urgency is a separate dimension -- emergencies bypass debounce/window too.
  A5: Classification failure must fail SAFE (no spurious floods to admin).

Classification labels:
  HANDLE   -- AI can respond confidently; do NOT notify admin
  ESCALATE -- Owner needs to decide; notify admin (non-urgent)
  URGENT   -- Immediate owner attention; notify admin + bypass debounce

Fast-path fallback: if LLM call fails, use conservative heuristic that
only escalates on explicit urgent keywords -- not on mere "known contact".
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass
from enum import Enum


class EscalationLevel(Enum):
    HANDLE = "HANDLE"
    ESCALATE = "ESCALATE"
    URGENT = "URGENT"


@dataclass
class ClassificationResult:
    level: EscalationLevel
    reason: str
    confidence: float  # 0.0-1.0 for observability
    used_llm: bool     # True if LLM was used; False if fast-path


# Keywords that always trigger URGENT fast-path (true emergencies)
_URGENT_FASTPATH = frozenset({
    "emergency", "ambulance", "hospital", "dying", "suicide", "fire",
    "accident", "bleeding", "unconscious", "911", "999", "112",
})

# Keywords that suggest ESCALATE fast-path (owner decision required)
_ESCALATE_FASTPATH = frozenset({
    "payment", "invoice", "contract", "deposit", "wire", "transfer",
    "lawsuit", "legal", "court", "police", "arrest", "threat",
    "deadline today", "deadline tomorrow", "confirm by", "signature",
    "signed by", "agree to", "committed to",
})

# Classification prompt -- structured so the LLM returns a parseable label
_CLASSIFICATION_PROMPT = """You are a message routing assistant. Your job is to decide whether an incoming WhatsApp message needs the owner's attention or can be handled by the AI assistant.

Context about the contact:
{contact_context}

Incoming message:
{message}

Classification rules:
- HANDLE: Greetings, general questions, routine conversation, known FAQs, social chat, requests the AI can answer confidently from existing knowledge/memory.
- ESCALATE: Financial decisions (payments, contracts, invoices), personal commitments the owner must make, sensitive unknown topics that require owner judgment, requests for information only the owner would know, complaints requiring owner response.
- URGENT: Medical emergencies, safety threats, time-critical matters (happening right now), situations where delay causes real harm.

Respond with EXACTLY one line in this format:
LABEL: <HANDLE|ESCALATE|URGENT> | REASON: <one sentence>

Do not add any other text."""


class IntentClassifier:
    """LLM-based escalation classifier.

    Usage:
        classifier = IntentClassifier(config)
        result = await classifier.classify(content, sender_id, contact_context)
        if result.level in (EscalationLevel.ESCALATE, EscalationLevel.URGENT):
            # notify admin
    """

    def __init__(self, config: dict):
        self._config = config
        # Cache recent classifications to avoid repeated LLM calls for same contact
        # key: (sender_id, content_hash) -> (result, timestamp)
        self._cache: dict[tuple, tuple] = {}
        self._CACHE_TTL = 300  # 5 minutes -- don't re-classify same message
        self._CACHE_MAX = 200

    def _fast_path(self, content: str) -> ClassificationResult | None:
        """Check explicit keyword fast-paths before calling LLM.

        Returns None if no fast-path applies (proceed to LLM).
        Returns a ClassificationResult if fast-path matches.
        """
        lower = content.lower()

        # URGENT keywords: always escalate immediately, no LLM needed
        for kw in _URGENT_FASTPATH:
            if kw in lower:
                return ClassificationResult(
                    level=EscalationLevel.URGENT,
                    reason=f"urgent keyword detected: '{kw}'",
                    confidence=0.95,
                    used_llm=False,
                )

        # ESCALATE keywords: high-confidence escalation fast-path
        for kw in _ESCALATE_FASTPATH:
            if kw in lower:
                return ClassificationResult(
                    level=EscalationLevel.ESCALATE,
                    reason=f"escalation keyword detected: '{kw}'",
                    confidence=0.85,
                    used_llm=False,
                )

        # ALL CAPS + long enough to be intentional: might be urgent but check LLM
        # Don't fast-path HANDLE here -- let LLM make that call

        return None  # No fast-path -- proceed to LLM

    def _parse_llm_response(self, response: str) -> tuple[EscalationLevel, str, float]:
        """Parse 'LABEL: X | REASON: Y' format from LLM."""
        # Extract label
        label_match = re.search(r'LABEL:\s*(HANDLE|ESCALATE|URGENT)', response, re.I)
        reason_match = re.search(r'REASON:\s*(.+)', response)

        if not label_match:
            # Fallback: look for bare label anywhere
            for label in ("URGENT", "ESCALATE", "HANDLE"):
                if label in response.upper():
                    reason = reason_match.group(1).strip() if reason_match else "LLM classified"
                    return EscalationLevel[label], reason, 0.6

            # Complete parse failure: fail safe (HANDLE, don't flood admin)
            return EscalationLevel.HANDLE, "parse failure -- defaulting to HANDLE", 0.3

        label = label_match.group(1).upper()
        reason = reason_match.group(1).strip() if reason_match else "LLM classified"
        return EscalationLevel[label], reason, 0.85

    async def classify(
        self,
        content: str,
        sender_id: str,
        contact_context: str = "",
        http_client=None,
    ) -> ClassificationResult:
        """Classify a message for admin escalation.

        Args:
            content: The message text.
            sender_id: Sender's phone number (for caching).
            contact_context: Contact profile/memory context (for LLM).
            http_client: httpx.AsyncClient instance (reused from orchestrator).

        Returns:
            ClassificationResult with level, reason, confidence, used_llm.
        """
        # Check cache first
        content_key = content[:200]  # Truncate for cache key
        cache_key = (sender_id, content_key)
        now = time.time()
        if cache_key in self._cache:
            cached_result, cached_at = self._cache[cache_key]
            if now - cached_at < self._CACHE_TTL:
                return cached_result

        # Fast-path keyword check (no LLM cost)
        fast = self._fast_path(content)
        if fast is not None:
            self._store_cache(cache_key, fast, now)
            return fast

        # LLM classification
        api_key = __import__("os").environ.get("AI_GATEWAY_API_KEY", "")
        api_url = self._config.get(
            "ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1"
        )
        # Use fast/cheap model for classification (haiku, not sonnet)
        model = self._config.get("intent_classifier_model", "anthropic/claude-haiku-4-5-20251001")

        if not api_key or http_client is None:
            # No LLM available -- use conservative fallback
            result = ClassificationResult(
                level=EscalationLevel.HANDLE,
                reason="no LLM available -- defaulting to HANDLE",
                confidence=0.3,
                used_llm=False,
            )
            self._store_cache(cache_key, result, now)
            return result

        prompt = _CLASSIFICATION_PROMPT.format(
            contact_context=contact_context[:500] if contact_context else "No prior context.",
            message=content[:500],
        )

        try:
            # Normalize URL -- avoid double /openai/v1 appended elsewhere
            base_url = api_url.rstrip("/")
            if not base_url.endswith("/chat/completions"):
                chat_url = f"{base_url}/chat/completions"
            else:
                chat_url = base_url

            resp = await http_client.post(
                chat_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                    "temperature": 0.0,  # Deterministic for classification
                },
                timeout=10.0,  # Fast timeout -- don't block message pipeline
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
            level, reason, confidence = self._parse_llm_response(raw)
            result = ClassificationResult(
                level=level, reason=reason, confidence=confidence, used_llm=True
            )
        except Exception as e:
            # LLM call failed -- fail safe
            print(f"[intent-classifier] LLM call failed: {type(e).__name__}: {e}")
            result = ClassificationResult(
                level=EscalationLevel.HANDLE,
                reason=f"LLM error -- defaulting to HANDLE: {type(e).__name__}",
                confidence=0.3,
                used_llm=False,
            )

        self._store_cache(cache_key, result, now)
        return result

    def _store_cache(
        self, key: tuple, result: ClassificationResult, ts: float
    ) -> None:
        """Store result in cache, evicting oldest entry if full."""
        if len(self._cache) >= self._CACHE_MAX:
            # Evict oldest entry (dict insertion order in Python 3.7+)
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[key] = (result, ts)
