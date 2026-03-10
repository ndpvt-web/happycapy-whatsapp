"""Two-layer memory system for HappyCapy WhatsApp skill.

Architecture (ported from nanobot):
- Per-contact MEMORY.md: Long-term facts about THIS contact only. Injected into prompt.
- Per-contact HISTORY.md: Append-only timestamped event log per contact.
- Global MEMORY.md/HISTORY.md: Legacy fallback, used by admin commands only.

Memory Isolation: Each contact has their own memory directory under
memory/contacts/{jid_hash}/. Contact A's memory is NEVER shown to Contact B.
This prevents cross-contact information leakage.

Consolidation: LLM periodically summarizes conversation samples into
per-contact MEMORY.md (facts) + HISTORY.md (events). Runs in background.

Memory Search: Keyword + date-range + topic search over HISTORY.md with
fuzzy matching and recency scoring.
"""

import asyncio
import hashlib
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class MemoryStore:
    """Persistent two-layer memory with LLM consolidation and per-contact isolation."""

    def __init__(self, base_dir: str | Path | None = None):
        if base_dir is None:
            base_dir = Path.home() / ".happycapy-whatsapp"
        self._dir = Path(base_dir) / "memory"
        self._dir.mkdir(parents=True, exist_ok=True)
        # Global files (legacy, admin-only)
        self._memory_path = self._dir / "MEMORY.md"
        self._history_path = self._dir / "HISTORY.md"
        # Per-contact isolation directory
        self._contacts_dir = self._dir / "contacts"
        self._contacts_dir.mkdir(parents=True, exist_ok=True)
        self._consolidation_lock = asyncio.Lock()
        self._last_consolidated_count = 0

    # ── JID helpers ──

    @staticmethod
    def _jid_key(jid: str) -> str:
        """Convert JID to filesystem-safe directory name (12-char hash)."""
        return hashlib.md5(jid.encode()).hexdigest()[:12]

    def _contact_dir(self, jid: str) -> Path:
        """Get/create per-contact memory directory."""
        d = self._contacts_dir / self._jid_key(jid)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Global reading (legacy, admin-only) ──

    def read_long_term(self) -> str:
        """Read global MEMORY.md content (admin/legacy use only)."""
        if self._memory_path.exists():
            try:
                return self._memory_path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return ""

    def read_history(self) -> str:
        """Read global HISTORY.md content (admin/legacy use only)."""
        if self._history_path.exists():
            try:
                return self._history_path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return ""

    # ── Global writing (legacy) ──

    def write_long_term(self, content: str) -> None:
        """Overwrite global MEMORY.md with new content."""
        self._memory_path.write_text(content.strip() + "\n", encoding="utf-8")

    def append_history(self, entry: str) -> None:
        """Append a timestamped entry to global HISTORY.md."""
        entry = entry.strip()
        if not entry:
            return
        with open(self._history_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n{entry}\n")

    # ── Per-contact reading ──

    def read_contact_memory(self, jid: str) -> str:
        """Read per-contact MEMORY.md content."""
        path = self._contact_dir(jid) / "MEMORY.md"
        if path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return ""

    def read_contact_history(self, jid: str) -> str:
        """Read per-contact HISTORY.md content."""
        path = self._contact_dir(jid) / "HISTORY.md"
        if path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return ""

    # ── Per-contact writing ──

    def write_contact_memory(self, jid: str, content: str) -> None:
        """Overwrite per-contact MEMORY.md with new content."""
        path = self._contact_dir(jid) / "MEMORY.md"
        path.write_text(content.strip() + "\n", encoding="utf-8")

    def append_contact_history(self, jid: str, entry: str) -> None:
        """Append a timestamped entry to per-contact HISTORY.md."""
        entry = entry.strip()
        if not entry:
            return
        path = self._contact_dir(jid) / "HISTORY.md"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n\n{entry}\n")

    # ── Context injection (per-contact isolated) ──

    def get_memory_context(self, jid: str | None = None) -> str:
        """Get MEMORY.md content for system prompt.

        When jid is provided, returns ONLY that contact's isolated memory.
        Falls back to global memory if no jid given (legacy/admin).
        """
        if jid:
            content = self.read_contact_memory(jid)
        else:
            content = self.read_long_term()
        if content:
            return f"## Long-term Memory\n{content}"
        return ""

    def get_recent_history(self, jid: str | None = None, max_entries: int = 5, max_chars: int = 2000) -> str:
        """Get recent HISTORY.md entries for prompt injection.

        When jid is provided, returns ONLY that contact's isolated history.
        Falls back to global history if no jid given (legacy/admin).
        """
        if jid:
            raw = self.read_contact_history(jid)
        else:
            raw = self.read_history()
        if not raw:
            return ""
        # Split on double-newline (entries separated by blank lines)
        entries = [e.strip() for e in raw.split("\n\n") if e.strip()]
        if not entries:
            return ""
        recent = entries[-max_entries:]
        text = "\n\n".join(recent)
        if len(text) > max_chars:
            text = text[-max_chars:]
            # Clean up partial first entry
            idx = text.find("\n\n")
            if idx > 0:
                text = text[idx + 2:]
        return text

    # ── Consolidation (per-contact) ──

    async def consolidate_contact(
        self,
        jid: str,
        contact_name: str,
        samples: list[dict[str, str]],
        api_url: str,
        api_key: str,
        model: str = "gpt-4.1-mini",
    ) -> dict[str, Any]:
        """Consolidate conversation samples for a SPECIFIC contact.

        Memory isolation: each contact's memory is stored separately.
        Contact A's facts never leak into Contact B's prompt.

        Args:
            jid: Contact JID (used for per-contact file storage).
            contact_name: Human-readable name for the consolidation prompt.
            samples: List of {"role": ..., "content": ..., "timestamp": ...}
            api_url: AI Gateway URL.
            api_key: API key.
            model: Model to use for consolidation.
        """
        if not samples:
            return {"success": True, "messages_consolidated": 0, "error": None}

        async with self._consolidation_lock:
            return await self._do_consolidate_contact(
                jid, contact_name, samples, api_url, api_key, model
            )

    async def _do_consolidate_contact(
        self, jid, contact_name, samples, api_url, api_key, model
    ) -> dict[str, Any]:
        try:
            import httpx
        except ImportError:
            return {"success": False, "messages_consolidated": 0,
                    "error": "httpx not installed"}

        current_memory = self.read_contact_memory(jid)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Format conversation for LLM
        convo_lines = []
        for s in samples:
            ts = s.get("timestamp", "?")
            role = s.get("role", "user").upper()
            content = s.get("content", "")[:500]
            convo_lines.append(f"[{ts}] {role}: {content}")
        conversation_text = "\n".join(convo_lines)

        system_prompt = (
            "You are a memory consolidation agent. You process conversation history "
            f"with a SPECIFIC contact ({contact_name}) and extract information.\n\n"
            "IMPORTANT: This memory is PRIVATE to this contact. Only store facts "
            "relevant to this specific person and your conversations with them.\n\n"
            "Produce two outputs:\n\n"
            "1. **history_entry**: A concise paragraph summarizing the conversation. "
            f"Start with [{now}] timestamp. Focus on what happened, decisions made, "
            "topics discussed, and action items.\n\n"
            "2. **memory_update**: The complete updated MEMORY.md for this contact. "
            "Include: relationship details, their preferences, topics discussed, "
            "context you'd need for future conversations. Merge new info with "
            "existing memory. Remove outdated facts. Use markdown headers.\n\n"
            "Respond with a JSON object: {\"history_entry\": \"...\", \"memory_update\": \"...\"}\n"
            "ONLY output the JSON object, nothing else."
        )

        user_prompt = f"## Contact: {contact_name}\n"
        user_prompt += f"## Current Memory for this contact\n{current_memory or '(empty)'}\n\n"
        user_prompt += f"## Recent conversation to process\n{conversation_text}"

        url = api_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 2000,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            reply = data["choices"][0]["message"]["content"].strip()
            if reply.startswith("```"):
                reply = re.sub(r"^```(?:json)?\s*", "", reply)
                reply = re.sub(r"\s*```$", "", reply)

            import json
            result = json.loads(reply)
            history_entry = result.get("history_entry", "")
            memory_update = result.get("memory_update", "")

            if history_entry:
                self.append_contact_history(jid, history_entry)
                # Also append to global history for admin visibility
                self.append_history(f"[{contact_name}] {history_entry}")
            if memory_update and memory_update.strip() != current_memory.strip():
                self.write_contact_memory(jid, memory_update)

            self._last_consolidated_count += len(samples)
            return {
                "success": True,
                "messages_consolidated": len(samples),
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "messages_consolidated": 0,
                "error": str(e),
            }

    # Legacy global consolidation (kept for backward compat, used by admin)
    async def consolidate(
        self,
        samples: list[dict[str, str]],
        api_url: str,
        api_key: str,
        model: str = "gpt-4.1-mini",
    ) -> dict[str, Any]:
        """Legacy global consolidation (for admin/backward compat only)."""
        if not samples:
            return {"success": True, "messages_consolidated": 0, "error": None}
        async with self._consolidation_lock:
            return await self._do_consolidate(samples, api_url, api_key, model)

    async def _do_consolidate(
        self, samples, api_url, api_key, model
    ) -> dict[str, Any]:
        try:
            import httpx
        except ImportError:
            return {"success": False, "messages_consolidated": 0,
                    "error": "httpx not installed"}

        current_memory = self.read_long_term()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        convo_lines = []
        for s in samples:
            ts = s.get("timestamp", "?")
            role = s.get("role", "user").upper()
            content = s.get("content", "")[:500]
            convo_lines.append(f"[{ts}] {role}: {content}")
        conversation_text = "\n".join(convo_lines)

        system_prompt = (
            "You are a memory consolidation agent. Your job is to process conversation "
            "history and extract important information into two outputs:\n\n"
            "1. **history_entry**: A concise paragraph summarizing the conversation events. "
            f"Start with [{now}] timestamp. Focus on what happened, decisions made, "
            "topics discussed, and action items.\n\n"
            "2. **memory_update**: The complete updated MEMORY.md content. This should "
            "contain all persistent facts, user preferences, relationship details, "
            "project context, and important information. Merge new information with "
            "existing memory. Remove outdated facts. Keep it organized with markdown headers.\n\n"
            "Respond with a JSON object: {\"history_entry\": \"...\", \"memory_update\": \"...\"}\n"
            "ONLY output the JSON object, nothing else."
        )

        user_prompt = f"## Current MEMORY.md\n{current_memory or '(empty)'}\n\n"
        user_prompt += f"## Conversation to process\n{conversation_text}"

        url = api_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 2000,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            reply = data["choices"][0]["message"]["content"].strip()
            if reply.startswith("```"):
                reply = re.sub(r"^```(?:json)?\s*", "", reply)
                reply = re.sub(r"\s*```$", "", reply)

            import json
            result = json.loads(reply)
            history_entry = result.get("history_entry", "")
            memory_update = result.get("memory_update", "")

            if history_entry:
                self.append_history(history_entry)
            if memory_update and memory_update.strip() != current_memory.strip():
                self.write_long_term(memory_update)

            self._last_consolidated_count += len(samples)
            return {
                "success": True,
                "messages_consolidated": len(samples),
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "messages_consolidated": 0,
                "error": str(e),
            }

    @property
    def last_consolidated_count(self) -> int:
        return self._last_consolidated_count


class MemorySearch:
    """Search HISTORY.md with keyword, date, and topic scoring."""

    # Stop words to filter from keyword extraction
    _STOP_WORDS = frozenset({
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "about", "that",
        "this", "it", "i", "we", "you", "they", "he", "she", "what", "which",
        "who", "when", "where", "how", "and", "or", "but", "not", "if",
        "then", "than", "so", "no", "up", "out", "just", "also", "very",
        "my", "me", "your", "his", "her", "our", "its", "their",
    })

    # Date parsing patterns
    _DATE_PATTERNS = [
        (re.compile(r"\blast\s+week\b", re.I), lambda: (datetime.now() - timedelta(days=7), datetime.now())),
        (re.compile(r"\blast\s+month\b", re.I), lambda: (datetime.now() - timedelta(days=30), datetime.now())),
        (re.compile(r"\blast\s+year\b", re.I), lambda: (datetime.now() - timedelta(days=365), datetime.now())),
        (re.compile(r"\btoday\b", re.I), lambda: (datetime.now().replace(hour=0, minute=0, second=0), datetime.now())),
        (re.compile(r"\byesterday\b", re.I), lambda: (
            (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0),
            datetime.now().replace(hour=0, minute=0, second=0),
        )),
    ]

    # Month name patterns
    _MONTHS = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
        "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
        "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    }

    _ENTRY_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\]")

    def __init__(self, memory_store: MemoryStore):
        self._store = memory_store

    def _parse_entries(self) -> list[tuple[datetime | None, str]]:
        """Parse HISTORY.md into (timestamp, text) tuples."""
        raw = self._store.read_history()
        if not raw:
            return []
        entries = []
        for block in raw.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            ts = None
            m = self._ENTRY_TS_RE.match(block)
            if m:
                try:
                    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
                except ValueError:
                    pass
            entries.append((ts, block))
        return entries

    def _extract_date_range(self, query: str) -> tuple[datetime | None, datetime | None, str]:
        """Extract date range from query, return (start, end, remaining_query)."""
        for pattern, fn in self._DATE_PATTERNS:
            if pattern.search(query):
                start, end = fn()
                cleaned = pattern.sub("", query).strip()
                return start, end, cleaned

        # Check month names
        for name, month in self._MONTHS.items():
            pat = re.compile(rf"\b{name}\b", re.I)
            if pat.search(query):
                year = datetime.now().year
                start = datetime(year, month, 1)
                if month == 12:
                    end = datetime(year + 1, 1, 1)
                else:
                    end = datetime(year, month + 1, 1)
                cleaned = pat.sub("", query).strip()
                return start, end, cleaned

        return None, None, query

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from text."""
        words = re.findall(r"\b\w+\b", text.lower())
        return [w for w in words if w not in self._STOP_WORDS and len(w) > 1]

    def _word_similarity(self, a: str, b: str) -> float:
        """Simple character-level similarity ratio."""
        if a == b:
            return 1.0
        if not a or not b:
            return 0.0
        # Simple Levenshtein-ish: shared chars / max length
        common = sum(1 for c in a if c in b)
        return common / max(len(a), len(b))

    def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search HISTORY.md with keywords + optional date filtering.

        Returns list of {"timestamp": str, "text": str, "score": float}.
        """
        start, end, cleaned = self._extract_date_range(query)
        keywords = self._extract_keywords(cleaned)
        entries = self._parse_entries()

        results = []
        now = datetime.now()

        for ts, text in entries:
            # Date filter
            if start and ts and ts < start:
                continue
            if end and ts and ts > end:
                continue

            score = 0.0
            text_lower = text.lower()

            # Keyword scoring
            for kw in keywords:
                # Exact match
                count = text_lower.count(kw)
                score += count * 10

                # Fuzzy matching against words in entry
                for word in re.findall(r"\b\w+\b", text_lower):
                    sim = self._word_similarity(kw, word)
                    if sim > 0.8 and word != kw:
                        score += 5 * sim

            if score == 0 and keywords:
                continue  # No keyword match

            # Recency bonus
            if ts:
                age_days = (now - ts).total_seconds() / 86400
                if age_days < 7:
                    score *= 2.0
                elif age_days < 30:
                    score *= 1.5
                elif age_days < 90:
                    score *= 1.2

            ts_str = ts.strftime("%Y-%m-%d %H:%M") if ts else "unknown"
            results.append({"timestamp": ts_str, "text": text, "score": score})

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:max_results]

    def search_by_topic(self, topic: str, max_results: int = 10) -> list[dict[str, Any]]:
        """TF-IDF-like topic search."""
        terms = self._extract_keywords(topic)
        if not terms:
            return []

        entries = self._parse_entries()
        results = []

        for ts, text in entries:
            text_lower = text.lower()
            score = 0.0
            for term in terms:
                count = text_lower.count(term)
                score += count * 10
                for word in re.findall(r"\b\w+\b", text_lower):
                    sim = self._word_similarity(term, word)
                    if sim > 0.8 and word != term:
                        score += 5 * sim * count if count else 5 * sim

            if score > 0:
                ts_str = ts.strftime("%Y-%m-%d %H:%M") if ts else "unknown"
                results.append({"timestamp": ts_str, "text": text, "score": score})

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:max_results]

    def search_by_date(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Return all entries within a date range."""
        entries = self._parse_entries()
        results = []
        for ts, text in entries:
            if ts and start <= ts <= end:
                results.append({
                    "timestamp": ts.strftime("%Y-%m-%d %H:%M"),
                    "text": text,
                    "score": 0.0,
                })
        results.sort(key=lambda r: r["timestamp"], reverse=True)
        return results

    def get_summary(self, query: str, max_results: int = 5) -> str:
        """Get formatted search results for LLM context."""
        results = self.search(query, max_results)
        if not results:
            return f"No memory entries found for: {query}"
        lines = [f"Found {len(results)} entries for '{query}':"]
        for r in results:
            text = r["text"][:300]
            lines.append(f"\n[{r['timestamp']}] (score: {r['score']:.0f})\n{text}")
        return "\n".join(lines)
