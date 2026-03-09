"""LightRAG-inspired Knowledge Graph for conversation context retrieval.

Replaces FTS5-based ConversationRAG with a graph-based approach:
1. Extracts entities and relationships from conversations via LLM
2. Stores them in SQLite as a knowledge graph
3. Retrieves relevant context via entity subgraph traversal
4. Falls back to recency when KG is empty (fresh start)

All SQLite, no external dependencies. Extraction runs periodically via heartbeat.
"""

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


# ── LLM Extraction Prompts ──

_EXTRACTION_SYSTEM = (
    "You are a knowledge graph extraction agent. "
    "Extract entities and relationships from conversation messages in JSON format. "
    "Be accurate and concise. Only extract clearly stated information."
)

_EXTRACTION_USER = """## Conversation Messages
{messages}

Extract entities (people, places, topics, events, preferences) and relationships between them.

Respond with JSON only:
{{
  "entities": [
    {{"name": "Entity Name", "type": "person|place|topic|event|organization|preference|other", "description": "Brief description"}}
  ],
  "relationships": [
    {{"source": "Entity A", "target": "Entity B", "type": "relationship_type", "evidence": "Brief evidence"}}
  ]
}}

Entity types: person, place, topic, event, organization, preference, other
Relationship types: interested_in, works_at, discussed, knows, prefers, attended, located_at, related_to, mentioned"""


class KnowledgeGraph:
    """SQLite-backed knowledge graph with LLM extraction and graph retrieval.

    Drop-in replacement for ConversationRAG. Same retrieve() interface but
    returns structured entity/relationship context instead of raw messages.
    """

    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Create KG tables if they don't exist."""
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS kg_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT 'other',
                jid TEXT NOT NULL,
                description TEXT DEFAULT '',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                mention_count INTEGER DEFAULT 1,
                metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS kg_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_entity_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
                target_entity_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
                relationship_type TEXT NOT NULL DEFAULT 'related_to',
                weight REAL DEFAULT 1.0,
                jid TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                evidence TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS kg_extraction_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                jid TEXT NOT NULL,
                last_sample_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                samples_processed INTEGER DEFAULT 0,
                entities_found INTEGER DEFAULT 0,
                relationships_found INTEGER DEFAULT 0,
                status TEXT DEFAULT 'success'
            );
        """)

        # Create indexes (ignore if already exist)
        for stmt in [
            "CREATE INDEX IF NOT EXISTS idx_kg_ent_jid ON kg_entities(jid)",
            "CREATE INDEX IF NOT EXISTS idx_kg_rel_src ON kg_relationships(source_entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_kg_rel_tgt ON kg_relationships(target_entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_kg_rel_jid ON kg_relationships(jid)",
            "CREATE INDEX IF NOT EXISTS idx_kg_log_jid ON kg_extraction_log(jid)",
        ]:
            self._conn.execute(stmt)

        # FTS5 on entity names + descriptions for retrieval
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS kg_entities_fts
                USING fts5(name, description, content='kg_entities', content_rowid='id')
            """)
            # Auto-sync triggers
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS kg_ent_fts_insert AFTER INSERT ON kg_entities
                BEGIN INSERT INTO kg_entities_fts(rowid, name, description)
                VALUES (new.id, new.name, new.description); END
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS kg_ent_fts_delete AFTER DELETE ON kg_entities
                BEGIN INSERT INTO kg_entities_fts(kg_entities_fts, rowid, name, description)
                VALUES ('delete', old.id, old.name, old.description); END
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS kg_ent_fts_update AFTER UPDATE ON kg_entities
                BEGIN
                    INSERT INTO kg_entities_fts(kg_entities_fts, rowid, name, description)
                    VALUES ('delete', old.id, old.name, old.description);
                    INSERT INTO kg_entities_fts(rowid, name, description)
                    VALUES (new.id, new.name, new.description);
                END
            """)
        except Exception:
            pass  # FTS5 may not be available

        self._conn.commit()

    # ── Retrieval ──

    def retrieve(self, jid: str, query: str, max_chars: int = 2000) -> tuple[str, dict]:
        """Main retrieval interface. Returns (context_string, stats_dict).

        Algorithm:
        1. If KG has entities for this jid -> graph-based retrieval + recent messages
        2. If KG is empty -> fallback to last 10 conversation_samples
        """
        entity_count = self._conn.execute(
            "SELECT COUNT(*) FROM kg_entities WHERE jid = ?", (jid,)
        ).fetchone()[0]

        if entity_count == 0:
            return self._recency_fallback(jid, max_chars)

        # Hybrid: 70% KG context, 30% recent messages
        kg_budget = int(max_chars * 0.7)
        recency_budget = max_chars - kg_budget

        kg_context, kg_meta = self.retrieve_local(jid, query, max_chars=kg_budget)
        recency_context = self._get_recent_samples(jid, max_chars=recency_budget, limit=5)

        if kg_context and recency_context:
            combined = f"{kg_context}\n\n## Recent Messages\n{recency_context}"
        elif kg_context:
            combined = kg_context
        else:
            combined = recency_context

        meta = {**kg_meta, "has_kg_data": True, "recency_included": bool(recency_context)}
        return combined, meta

    def retrieve_local(
        self, jid: str, query: str, max_entities: int = 10, max_chars: int = 1400
    ) -> tuple[str, dict]:
        """Entity-based local retrieval with 1-hop relationship traversal."""
        fts_query = self._sanitize_fts_query(query)
        if not fts_query:
            # No usable query terms -- return top entities by mention count
            return self._top_entities_fallback(jid, max_entities, max_chars)

        try:
            matching = self._conn.execute(
                """SELECT e.id, e.name, e.entity_type, e.description, e.mention_count
                   FROM kg_entities_fts fts
                   JOIN kg_entities e ON e.id = fts.rowid
                   WHERE kg_entities_fts MATCH ? AND e.jid = ?
                   ORDER BY e.mention_count DESC
                   LIMIT ?""",
                (fts_query, jid, max_entities),
            ).fetchall()
        except Exception:
            matching = []

        if not matching:
            return self._top_entities_fallback(jid, max_entities, max_chars)

        return self._format_subgraph(jid, matching, max_chars)

    def _top_entities_fallback(
        self, jid: str, max_entities: int, max_chars: int
    ) -> tuple[str, dict]:
        """When FTS query yields nothing, return top entities by mention count."""
        rows = self._conn.execute(
            """SELECT id, name, entity_type, description, mention_count
               FROM kg_entities WHERE jid = ?
               ORDER BY mention_count DESC LIMIT ?""",
            (jid, max_entities),
        ).fetchall()

        if not rows:
            return "", {"total_entities": 0, "total_relationships": 0}

        return self._format_subgraph(jid, rows, max_chars)

    def _format_subgraph(
        self, jid: str, entities: list, max_chars: int
    ) -> tuple[str, dict]:
        """Format entities + their relationships as context string."""
        entity_ids = [e["id"] for e in entities]
        if not entity_ids:
            return "", {"total_entities": 0, "total_relationships": 0}

        placeholders = ",".join("?" * len(entity_ids))

        # Get 1-hop relationships
        relationships = self._conn.execute(
            f"""SELECT r.relationship_type, r.weight, r.evidence,
                       e1.name as source_name, e2.name as target_name
                FROM kg_relationships r
                JOIN kg_entities e1 ON r.source_entity_id = e1.id
                JOIN kg_entities e2 ON r.target_entity_id = e2.id
                WHERE (r.source_entity_id IN ({placeholders})
                       OR r.target_entity_id IN ({placeholders}))
                AND r.jid = ?
                ORDER BY r.weight DESC
                LIMIT 20""",
            (*entity_ids, *entity_ids, jid),
        ).fetchall()

        # Build context
        parts = ["## Knowledge Graph Context\n\n### Entities"]
        total_chars = sum(len(p) for p in parts)

        for ent in entities:
            line = f"- **{ent['name']}** ({ent['entity_type']})"
            if ent["description"]:
                line += f": {ent['description']}"
            line += f" [mentioned {ent['mention_count']}x]"

            if total_chars + len(line) + 1 > max_chars:
                break
            parts.append(line)
            total_chars += len(line) + 1

        if relationships and total_chars < max_chars:
            parts.append("\n### Relationships")
            total_chars += 20

            for rel in relationships:
                line = f"- {rel['source_name']} **{rel['relationship_type']}** {rel['target_name']}"
                if rel["evidence"]:
                    ev = rel["evidence"][:80]
                    line += f' ("{ev}")'

                if total_chars + len(line) + 1 > max_chars:
                    break
                parts.append(line)
                total_chars += len(line) + 1

        meta = {
            "total_entities": len(entities),
            "total_relationships": len(relationships),
            "retrieval_method": "local",
        }
        return "\n".join(parts), meta

    def _recency_fallback(self, jid: str, max_chars: int) -> tuple[str, dict]:
        """Fallback when KG is empty: return recent conversation samples."""
        context = self._get_recent_samples(jid, max_chars=max_chars, limit=10)
        if not context:
            return "", {"has_kg_data": False, "fallback": "no_data"}
        return context, {"has_kg_data": False, "fallback": "recency"}

    def _get_recent_samples(self, jid: str, max_chars: int, limit: int = 5) -> str:
        """Get recent conversation samples formatted as text."""
        try:
            rows = self._conn.execute(
                """SELECT role, content, timestamp
                   FROM conversation_samples
                   WHERE jid = ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (jid, limit),
            ).fetchall()
        except Exception:
            return ""

        if not rows:
            return ""

        rows = list(reversed(rows))  # Chronological order
        lines = []
        total = 0
        for r in rows:
            role = r["role"] or "user"
            ts = r["timestamp"] or ""
            ts_part = f" [{ts}]" if ts else ""
            line = f"[{role}]{ts_part}: {r['content']}"
            if total + len(line) > max_chars and lines:
                break
            lines.append(line)
            total += len(line)

        return "\n".join(lines)

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Sanitize query for FTS5 MATCH. Keep alphanumeric words >= 3 chars."""
        words = []
        for word in query.split():
            clean = re.sub(r"[^\w]", "", word)
            if len(clean) >= 3:
                words.append(clean)
        if not words:
            return ""
        # OR-join for broader matching
        return " OR ".join(words)

    # ── Extraction ──

    async def extract_from_samples(
        self,
        jid: str,
        samples: list[dict],
        api_url: str,
        api_key: str,
        model: str = "gpt-4.1-mini",
    ) -> dict[str, Any]:
        """LLM-based entity/relationship extraction from conversation samples.

        Args:
            jid: WhatsApp JID.
            samples: List of dicts with keys: id, role, content, timestamp.
            api_url: AI Gateway URL.
            api_key: API key.
            model: Model to use.

        Returns:
            Stats dict: {entities_created, entities_updated, relationships_created, status}.
        """
        if not samples:
            return {"entities_created": 0, "entities_updated": 0,
                    "relationships_created": 0, "status": "no_samples"}

        try:
            import httpx
        except ImportError:
            return {"entities_created": 0, "entities_updated": 0,
                    "relationships_created": 0, "status": "error",
                    "error": "httpx not installed"}

        # Format messages for LLM
        msg_lines = []
        for s in samples:
            role = "You" if s.get("role") == "assistant" else "Contact"
            ts = s.get("timestamp", "")
            ts_part = f" [{ts}]" if ts else ""
            content = s.get("content", "")[:500]  # Truncate long messages
            msg_lines.append(f"{role}{ts_part}: {content}")
        messages_text = "\n".join(msg_lines)

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
                            {"role": "system", "content": _EXTRACTION_SYSTEM},
                            {"role": "user", "content": _EXTRACTION_USER.format(messages=messages_text)},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 2000,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            reply = data["choices"][0]["message"]["content"].strip()

            # Parse JSON (handle markdown code blocks)
            if reply.startswith("```"):
                reply = re.sub(r"^```(?:json)?\s*", "", reply)
                reply = re.sub(r"\s*```$", "", reply)

            parsed = json.loads(reply)
            entities = parsed.get("entities", [])
            relationships = parsed.get("relationships", [])

        except Exception as e:
            # Log extraction failure
            now = datetime.now().isoformat()
            last_id = max(s.get("id", 0) for s in samples)
            self._conn.execute(
                """INSERT INTO kg_extraction_log
                   (jid, last_sample_id, timestamp, samples_processed, entities_found, relationships_found, status)
                   VALUES (?, ?, ?, ?, 0, 0, ?)""",
                (jid, last_id, now, len(samples), f"error: {str(e)[:100]}"),
            )
            self._conn.commit()
            return {"entities_created": 0, "entities_updated": 0,
                    "relationships_created": 0, "status": "error", "error": str(e)}

        # Persist to SQLite
        return self._persist_extraction(jid, entities, relationships, samples)

    def _persist_extraction(
        self,
        jid: str,
        entities: list[dict],
        relationships: list[dict],
        samples: list[dict],
    ) -> dict[str, Any]:
        """Persist extracted entities and relationships with deduplication."""
        now = datetime.now().isoformat()
        entity_map: dict[str, int] = {}  # lowercase name -> entity_id
        entities_created = 0
        entities_updated = 0

        try:
            # Process entities
            for ent in entities:
                name = str(ent.get("name", "")).strip()
                if not name:
                    continue

                name_lower = name.lower()
                ent_type = str(ent.get("type", "other")).strip().lower()
                if ent_type not in ("person", "place", "topic", "event",
                                     "organization", "preference", "other"):
                    ent_type = "other"
                description = str(ent.get("description", "")).strip()[:500]

                # Check for existing entity (case-insensitive)
                existing = self._conn.execute(
                    "SELECT id, mention_count FROM kg_entities WHERE LOWER(name) = ? AND jid = ?",
                    (name_lower, jid),
                ).fetchone()

                if existing:
                    entity_id = existing["id"]
                    new_count = existing["mention_count"] + 1
                    self._conn.execute(
                        """UPDATE kg_entities SET last_seen = ?, mention_count = ?,
                           description = CASE WHEN ? != '' THEN ? ELSE description END
                           WHERE id = ?""",
                        (now, new_count, description, description, entity_id),
                    )
                    entities_updated += 1
                else:
                    cursor = self._conn.execute(
                        """INSERT INTO kg_entities
                           (name, entity_type, jid, description, first_seen, last_seen, mention_count)
                           VALUES (?, ?, ?, ?, ?, ?, 1)""",
                        (name, ent_type, jid, description, now, now),
                    )
                    entity_id = cursor.lastrowid
                    entities_created += 1

                entity_map[name_lower] = entity_id

            # Process relationships
            relationships_created = 0
            for rel in relationships:
                source_name = str(rel.get("source", "")).strip().lower()
                target_name = str(rel.get("target", "")).strip().lower()
                rel_type = str(rel.get("type", "related_to")).strip().lower()
                evidence = str(rel.get("evidence", "")).strip()[:200]

                if not source_name or not target_name:
                    continue

                source_id = entity_map.get(source_name)
                target_id = entity_map.get(target_name)
                if not source_id or not target_id:
                    continue

                # Check for existing relationship
                existing_rel = self._conn.execute(
                    """SELECT id, weight FROM kg_relationships
                       WHERE source_entity_id = ? AND target_entity_id = ?
                       AND relationship_type = ? AND jid = ?""",
                    (source_id, target_id, rel_type, jid),
                ).fetchone()

                if existing_rel:
                    new_weight = existing_rel["weight"] + 1.0
                    self._conn.execute(
                        "UPDATE kg_relationships SET weight = ?, last_seen = ? WHERE id = ?",
                        (new_weight, now, existing_rel["id"]),
                    )
                else:
                    self._conn.execute(
                        """INSERT INTO kg_relationships
                           (source_entity_id, target_entity_id, relationship_type, weight,
                            jid, first_seen, last_seen, evidence)
                           VALUES (?, ?, ?, 1.0, ?, ?, ?, ?)""",
                        (source_id, target_id, rel_type, jid, now, now, evidence),
                    )
                    relationships_created += 1

            # Log extraction
            last_id = max(s.get("id", 0) for s in samples) if samples else 0
            self._conn.execute(
                """INSERT INTO kg_extraction_log
                   (jid, last_sample_id, timestamp, samples_processed, entities_found, relationships_found, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'success')""",
                (jid, last_id, now, len(samples),
                 entities_created + entities_updated, relationships_created),
            )
            self._conn.commit()

            return {
                "entities_created": entities_created,
                "entities_updated": entities_updated,
                "relationships_created": relationships_created,
                "status": "success",
            }

        except Exception as e:
            self._conn.rollback()
            return {"entities_created": 0, "entities_updated": 0,
                    "relationships_created": 0, "status": "error", "error": str(e)}

    def get_unprocessed_samples(self, jid: str, limit: int = 50) -> list[dict]:
        """Get conversation_samples not yet processed for a given jid."""
        # Find the last processed sample ID for this jid
        row = self._conn.execute(
            "SELECT MAX(last_sample_id) FROM kg_extraction_log WHERE jid = ? AND status = 'success'",
            (jid,),
        ).fetchone()
        last_id = row[0] if row and row[0] else 0

        try:
            rows = self._conn.execute(
                """SELECT id, role, content, timestamp
                   FROM conversation_samples
                   WHERE jid = ? AND id > ?
                   ORDER BY id ASC
                   LIMIT ?""",
                (jid, last_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ── Search & Stats ──

    def search_entities(self, query: str, jid: str = "", limit: int = 20) -> list[dict]:
        """Search entities by name/description. For /kg search admin command."""
        fts_query = self._sanitize_fts_query(query)
        if not fts_query:
            return []

        try:
            if jid:
                rows = self._conn.execute(
                    """SELECT e.id, e.name, e.entity_type, e.description, e.mention_count, e.jid
                       FROM kg_entities_fts fts
                       JOIN kg_entities e ON e.id = fts.rowid
                       WHERE kg_entities_fts MATCH ? AND e.jid = ?
                       ORDER BY e.mention_count DESC LIMIT ?""",
                    (fts_query, jid, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT e.id, e.name, e.entity_type, e.description, e.mention_count, e.jid
                       FROM kg_entities_fts fts
                       JOIN kg_entities e ON e.id = fts.rowid
                       WHERE kg_entities_fts MATCH ?
                       ORDER BY e.mention_count DESC LIMIT ?""",
                    (fts_query, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def stats(self) -> dict[str, Any]:
        """Return KG statistics."""
        try:
            entity_count = self._conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0]
            rel_count = self._conn.execute("SELECT COUNT(*) FROM kg_relationships").fetchone()[0]
            extraction_runs = self._conn.execute("SELECT COUNT(*) FROM kg_extraction_log").fetchone()[0]

            # Type breakdown
            type_rows = self._conn.execute(
                "SELECT entity_type, COUNT(*) as cnt FROM kg_entities GROUP BY entity_type ORDER BY cnt DESC"
            ).fetchall()
            type_breakdown = {r["entity_type"]: r["cnt"] for r in type_rows}

            # Unique jids with KG data
            jid_count = self._conn.execute(
                "SELECT COUNT(DISTINCT jid) FROM kg_entities"
            ).fetchone()[0]

            # Last extraction
            last_log = self._conn.execute(
                "SELECT timestamp, status FROM kg_extraction_log ORDER BY id DESC LIMIT 1"
            ).fetchone()

            return {
                "entities": entity_count,
                "relationships": rel_count,
                "extraction_runs": extraction_runs,
                "contacts_with_kg": jid_count,
                "entity_types": type_breakdown,
                "last_extraction": dict(last_log) if last_log else None,
            }
        except Exception:
            return {"entities": 0, "relationships": 0, "extraction_runs": 0}

    def format_stats(self) -> str:
        """Format stats for WhatsApp admin display."""
        s = self.stats()
        lines = [
            f"*Knowledge Graph*\n",
            f"Entities: {s['entities']}",
            f"Relationships: {s['relationships']}",
            f"Contacts with KG: {s.get('contacts_with_kg', 0)}",
            f"Extraction runs: {s['extraction_runs']}",
        ]

        types = s.get("entity_types", {})
        if types:
            lines.append("\nEntity types:")
            for t, c in types.items():
                lines.append(f"  {t}: {c}")

        last = s.get("last_extraction")
        if last:
            lines.append(f"\nLast extraction: {last.get('timestamp', '?')} ({last.get('status', '?')})")

        return "\n".join(lines)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
