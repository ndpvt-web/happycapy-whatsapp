"""Dashboard API -- FastAPI backend for the WhatsApp bot dashboard.

Exposes read/write endpoints over the bot's SQLite databases, config,
spreadsheets, memory files, and health metrics.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ── Paths ──

BASE_DIR = Path.home() / ".happycapy-whatsapp"
CONTACTS_DB = BASE_DIR / "contacts.db"
REFLECTION_DB = BASE_DIR / "reflection.db"
BROADCAST_DB = BASE_DIR / "broadcast.db"
CONFIG_FILE = BASE_DIR / "config.json"
MEMORY_DIR = BASE_DIR / "memory"
SPREADSHEET_DIR = BASE_DIR / "data" / "spreadsheets"
LOG_FILE = BASE_DIR / "logs" / "daemon.log"
IDENTITY_DIR = BASE_DIR / "identity"

app = FastAPI(title="HappyCapy WhatsApp Dashboard API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ──

def _db(path: Path) -> sqlite3.Connection:
    """Open a read-only SQLite connection with row factory."""
    if not path.exists():
        raise HTTPException(404, f"Database not found: {path.name}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _db_rw(path: Path) -> sqlite3.Connection:
    """Open a read-write SQLite connection."""
    if not path.exists():
        raise HTTPException(404, f"Database not found: {path.name}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def _get_jid_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Build a JID -> display name mapping from whatsapp_contacts.

    Handles both raw JIDs ('85292893658') and suffixed forms
    ('85292893658@s.whatsapp.net', '167087800602664@lid').
    """
    try:
        rows = conn.execute(
            "SELECT jid, push_name, saved_name, verified_name FROM whatsapp_contacts WHERE push_name IS NOT NULL OR saved_name IS NOT NULL"
        ).fetchall()
    except Exception:
        return {}
    mapping = {}
    for r in rows:
        name = r["saved_name"] or r["push_name"] or r["verified_name"]
        if not name:
            continue
        jid = r["jid"]
        mapping[jid] = name
        # Also map suffixed forms so audit_log chat_ids resolve
        mapping[f"{jid}@s.whatsapp.net"] = name
        mapping[f"{jid}@lid"] = name
    return mapping


def _resolve_jid(jid: str | None, jid_names: dict[str, str]) -> str:
    """Resolve a JID to a display name, falling back to cleaned JID."""
    if not jid:
        return "Unknown"
    if jid in jid_names:
        return jid_names[jid]
    # Strip suffix and try bare number
    bare = jid.split("@")[0] if "@" in jid else jid
    if bare in jid_names:
        return jid_names[bare]
    return bare


def _jid_hash(jid: str) -> str:
    """Match the memory_store hashing: MD5 of JID, first 12 chars."""
    return hashlib.md5(jid.encode()).hexdigest()[:12]


# ══════════════════════════════════════════════════════════════
# HEALTH & STATUS
# ══════════════════════════════════════════════════════════════

@app.get("/api/health")
def get_health():
    """Bot health status and uptime."""
    pid_file = BASE_DIR / "daemon.pid"
    running = False
    pid = None
    uptime_seconds = 0

    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # Check if process exists
            running = True
            # Approximate uptime from PID file mtime
            uptime_seconds = int(time.time() - pid_file.stat().st_mtime)
        except (ValueError, ProcessLookupError, PermissionError):
            running = False

    # Check WhatsApp auth status
    auth_dir = BASE_DIR / "whatsapp-auth"
    wa_authenticated = (auth_dir / "creds.json").exists() if auth_dir.exists() else False

    # Database sizes
    db_sizes = {}
    for name, path in [("contacts", CONTACTS_DB), ("reflection", REFLECTION_DB), ("broadcast", BROADCAST_DB)]:
        if path.exists():
            db_sizes[name] = round(path.stat().st_size / 1024, 1)  # KB

    # Recent errors from log
    error_count = 0
    if LOG_FILE.exists():
        try:
            text = LOG_FILE.read_text(errors="replace")
            one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
            for line in text.split("\n")[-500:]:
                if "Error" in line or "error" in line or "Traceback" in line:
                    if one_hour_ago < line[:16] if len(line) > 16 else False:
                        error_count += 1
        except Exception:
            pass

    return {
        "running": running,
        "pid": pid,
        "uptime_seconds": uptime_seconds,
        "whatsapp_authenticated": wa_authenticated,
        "database_sizes_kb": db_sizes,
        "errors_last_hour": error_count,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/logs")
def get_logs(lines: int = Query(100, le=500)):
    """Recent daemon log lines."""
    if not LOG_FILE.exists():
        return {"lines": [], "total": 0}
    try:
        text = LOG_FILE.read_text(errors="replace")
        all_lines = text.strip().split("\n")
        return {"lines": all_lines[-lines:], "total": len(all_lines)}
    except Exception as e:
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

@app.get("/api/config")
def get_config():
    """Current bot configuration."""
    if not CONFIG_FILE.exists():
        raise HTTPException(404, "Config not found")
    return json.loads(CONFIG_FILE.read_text())


class ConfigUpdate(BaseModel):
    updates: dict[str, Any]


@app.put("/api/config")
def update_config(body: ConfigUpdate):
    """Update specific config fields (merge, not replace)."""
    if not CONFIG_FILE.exists():
        raise HTTPException(404, "Config not found")
    config = json.loads(CONFIG_FILE.read_text())
    config.update(body.updates)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    return {"status": "ok", "config": config}


# ══════════════════════════════════════════════════════════════
# CONTACTS & PROFILES
# ══════════════════════════════════════════════════════════════

@app.get("/api/contacts")
def get_contacts(limit: int = Query(100, le=500)):
    """List contacts with profiles."""
    with _db(CONTACTS_DB) as conn:
        rows = conn.execute("""
            SELECT cp.jid, cp.display_name, cp.total_messages_analyzed,
                   cp.profile_json, cp.updated_at,
                   wc.push_name, wc.saved_name, wc.verified_name
            FROM contact_profiles cp
            LEFT JOIN whatsapp_contacts wc ON cp.jid = wc.jid
            ORDER BY cp.updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

    contacts = []
    for r in rows:
        d = dict(r)
        try:
            d["profile"] = json.loads(d.pop("profile_json", "{}"))
        except Exception:
            d["profile"] = {}
        contacts.append(d)
    return {"contacts": contacts, "total": len(contacts)}


@app.get("/api/contacts/{jid}")
def get_contact_detail(jid: str):
    """Detailed contact view with profile, samples, KG entities."""
    with _db(CONTACTS_DB) as conn:
        profile = conn.execute(
            "SELECT * FROM contact_profiles WHERE jid = ?", (jid,)
        ).fetchone()
        if not profile:
            raise HTTPException(404, "Contact not found")

        samples = conn.execute(
            "SELECT role, content, timestamp FROM conversation_samples WHERE jid = ? ORDER BY timestamp DESC LIMIT 20",
            (jid,)
        ).fetchall()

        entities = conn.execute(
            "SELECT name, entity_type, mention_count, last_seen FROM kg_entities WHERE jid = ? ORDER BY mention_count DESC LIMIT 20",
            (jid,)
        ).fetchall()

        sessions = conn.execute(
            "SELECT * FROM sessions WHERE jid = ?", (jid,)
        ).fetchone()

    result = dict(profile)
    try:
        result["profile_data"] = json.loads(result.pop("profile_json", "{}"))
    except Exception:
        result["profile_data"] = {}

    result["recent_samples"] = _rows_to_dicts(samples)
    result["knowledge_entities"] = _rows_to_dicts(entities)
    result["session"] = dict(sessions) if sessions else None
    return result


# ══════════════════════════════════════════════════════════════
# AUDIT LOG & ANALYTICS
# ══════════════════════════════════════════════════════════════

@app.get("/api/audit")
def get_audit(
    limit: int = Query(50, le=500),
    event_type: str | None = None,
    hours: int = Query(24, le=168),
):
    """Audit log entries."""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    with _db(CONTACTS_DB) as conn:
        if event_type:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE timestamp > ? AND event_type = ? ORDER BY timestamp DESC LIMIT ?",
                (cutoff, event_type, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE timestamp > ? ORDER BY timestamp DESC LIMIT ?",
                (cutoff, limit)
            ).fetchall()
    return {"events": _rows_to_dicts(rows), "total": len(rows)}


@app.get("/api/analytics")
def get_analytics():
    """Dashboard analytics: message counts, top contacts, activity by hour."""
    with _db(CONTACTS_DB) as conn:
        # Total messages
        total = conn.execute("SELECT COUNT(*) as cnt FROM audit_log").fetchone()

        # Messages by direction (last 7 days)
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        by_direction = conn.execute("""
            SELECT direction, COUNT(*) as cnt
            FROM audit_log WHERE timestamp > ? AND direction IS NOT NULL
            GROUP BY direction
        """, (week_ago,)).fetchall()

        # Messages per day (last 7 days)
        per_day = conn.execute("""
            SELECT DATE(timestamp) as day, COUNT(*) as cnt
            FROM audit_log WHERE timestamp > ?
            GROUP BY DATE(timestamp)
            ORDER BY day
        """, (week_ago,)).fetchall()

        # Top contacts by message count (with name resolution)
        top_contacts_raw = conn.execute("""
            SELECT chat_id, COUNT(*) as cnt
            FROM audit_log WHERE chat_id IS NOT NULL AND chat_id != '' AND timestamp > ?
            GROUP BY chat_id ORDER BY cnt DESC LIMIT 10
        """, (week_ago,)).fetchall()
        jid_names = _get_jid_names(conn)
        top_contacts = []
        for r in top_contacts_raw:
            d = dict(r)
            d["display_name"] = _resolve_jid(d["chat_id"], jid_names)
            top_contacts.append(d)

        # Event type breakdown
        by_type = conn.execute("""
            SELECT event_type, COUNT(*) as cnt
            FROM audit_log WHERE timestamp > ?
            GROUP BY event_type ORDER BY cnt DESC
        """, (week_ago,)).fetchall()

        # Queue stats
        queue_stats = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM message_queue GROUP BY status
        """).fetchall()

        # Escalation stats
        esc_stats = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM escalations GROUP BY status
        """).fetchall()

        # Active sessions
        active_sessions = conn.execute(
            "SELECT COUNT(*) as cnt FROM sessions WHERE is_active = 1"
        ).fetchone()

    return {
        "total_events": dict(total)["cnt"] if total else 0,
        "messages_by_direction": _rows_to_dicts(by_direction),
        "messages_per_day": _rows_to_dicts(per_day),
        "top_contacts": top_contacts,
        "events_by_type": _rows_to_dicts(by_type),
        "queue_stats": _rows_to_dicts(queue_stats),
        "escalation_stats": _rows_to_dicts(esc_stats),
        "active_sessions": dict(active_sessions)["cnt"] if active_sessions else 0,
    }


# ══════════════════════════════════════════════════════════════
# MESSAGE QUEUE
# ══════════════════════════════════════════════════════════════

@app.get("/api/queue")
def get_queue(status: str | None = None, limit: int = Query(50, le=200)):
    """Message queue entries with resolved sender names."""
    with _db(CONTACTS_DB) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM message_queue WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM message_queue ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        jid_names = _get_jid_names(conn)
    messages = []
    for r in rows:
        d = dict(r)
        if not d.get("sender_name"):
            d["sender_name"] = _resolve_jid(d.get("sender_id"), jid_names)
        messages.append(d)
    return {"messages": messages, "total": len(messages)}


# ══════════════════════════════════════════════════════════════
# ESCALATIONS
# ══════════════════════════════════════════════════════════════

@app.get("/api/escalations")
def get_escalations(status: str | None = None):
    """Escalation entries with resolved sender names."""
    with _db(CONTACTS_DB) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM escalations WHERE status = ? ORDER BY created_at DESC",
                (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM escalations ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        jid_names = _get_jid_names(conn)
    escalations = []
    for r in rows:
        d = dict(r)
        if not d.get("sender_name"):
            d["sender_name"] = _resolve_jid(d.get("sender_id"), jid_names)
        escalations.append(d)
    return {"escalations": escalations, "total": len(escalations)}


# ══════════════════════════════════════════════════════════════
# SPREADSHEETS
# ══════════════════════════════════════════════════════════════

@app.get("/api/spreadsheets")
def list_spreadsheets():
    """List available spreadsheet files."""
    if not SPREADSHEET_DIR.exists():
        return {"spreadsheets": []}
    files = []
    for f in sorted(SPREADSHEET_DIR.glob("*.xlsx")):
        files.append({
            "name": f.stem,
            "filename": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return {"spreadsheets": files}


@app.get("/api/spreadsheets/{name}")
def read_spreadsheet(name: str, sheet: str | None = None, limit: int = Query(100, le=1000)):
    """Read spreadsheet data as JSON rows."""
    filepath = SPREADSHEET_DIR / f"{name}.xlsx"
    if not filepath.exists():
        raise HTTPException(404, f"Spreadsheet '{name}' not found")
    try:
        import openpyxl
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        sheet_names = wb.sheetnames
        ws = wb[sheet] if sheet and sheet in sheet_names else wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return {"name": name, "sheets": sheet_names, "headers": [], "rows": [], "total": 0}

        headers = [str(h) if h else f"col_{i}" for i, h in enumerate(rows[0])]
        data = []
        for row in rows[1:limit + 1]:
            data.append({headers[i]: cell for i, cell in enumerate(row) if i < len(headers)})

        wb.close()
        return {
            "name": name,
            "sheets": sheet_names,
            "headers": headers,
            "rows": data,
            "total": len(rows) - 1,
        }
    except ImportError:
        raise HTTPException(500, "openpyxl not installed")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/spreadsheets/{name}/download")
def download_spreadsheet(name: str):
    """Download spreadsheet file."""
    filepath = SPREADSHEET_DIR / f"{name}.xlsx"
    if not filepath.exists():
        raise HTTPException(404, f"Spreadsheet '{name}' not found")
    return FileResponse(filepath, filename=f"{name}.xlsx")


# ══════════════════════════════════════════════════════════════
# KNOWLEDGE GRAPH
# ══════════════════════════════════════════════════════════════

@app.get("/api/knowledge-graph")
def get_knowledge_graph(limit: int = Query(100, le=500)):
    """Knowledge graph entities and relationships with resolved contact names."""
    with _db(CONTACTS_DB) as conn:
        jid_names = _get_jid_names(conn)

        # Load config for owner name
        owner_name = "Owner"
        try:
            if CONFIG_FILE.exists():
                cfg = json.loads(CONFIG_FILE.read_text())
                owner_name = cfg.get("owner_name") or cfg.get("admin_number", "Owner")
        except Exception:
            pass

        def resolve_kg_name(name: str, jid: str) -> str:
            """Replace generic 'Contact'/'You' with real names."""
            if name == "You":
                return owner_name
            if name == "Contact":
                return _resolve_jid(jid, jid_names)
            return name

        entities_raw = conn.execute("""
            SELECT id, name, entity_type, jid, description, mention_count, last_seen
            FROM kg_entities ORDER BY mention_count DESC LIMIT ?
        """, (limit,)).fetchall()

        entities = []
        for e in entities_raw:
            d = dict(e)
            d["display_name"] = resolve_kg_name(d["name"], d["jid"])
            d["contact_name"] = _resolve_jid(d["jid"], jid_names)
            entities.append(d)

        relationships_raw = conn.execute("""
            SELECT r.id, s.name as source, t.name as target,
                   r.relationship_type, r.weight, r.jid,
                   s.jid as source_jid, t.jid as target_jid
            FROM kg_relationships r
            JOIN kg_entities s ON r.source_entity_id = s.id
            JOIN kg_entities t ON r.target_entity_id = t.id
            ORDER BY r.weight DESC LIMIT ?
        """, (limit,)).fetchall()

        relationships = []
        for r in relationships_raw:
            d = dict(r)
            d["source_display"] = resolve_kg_name(d["source"], d["source_jid"])
            d["target_display"] = resolve_kg_name(d["target"], d["target_jid"])
            d["contact_name"] = _resolve_jid(d["jid"], jid_names)
            relationships.append(d)

        # Stats
        entity_types = conn.execute("""
            SELECT entity_type, COUNT(*) as cnt FROM kg_entities GROUP BY entity_type
        """).fetchall()

    return {
        "entities": entities,
        "relationships": relationships,
        "entity_type_stats": _rows_to_dicts(entity_types),
    }


# ══════════════════════════════════════════════════════════════
# MEMORY
# ══════════════════════════════════════════════════════════════

@app.get("/api/memory")
def get_memory():
    """Memory files overview with resolved contact names."""
    result = {"global": {}, "contacts": []}

    # Build hash -> display name mapping
    hash_names = {}
    try:
        with _db(CONTACTS_DB) as conn:
            jid_names = _get_jid_names(conn)
        for jid, name in jid_names.items():
            if "@" not in jid:  # Only bare JIDs, not suffixed
                h = _jid_hash(jid)
                hash_names[h] = name
    except Exception:
        pass

    # Global memory
    for name in ("MEMORY.md", "HISTORY.md"):
        path = MEMORY_DIR / name
        if path.exists():
            content = path.read_text(errors="replace").strip()
            result["global"][name] = {
                "size_bytes": len(content),
                "lines": content.count("\n") + 1,
                "preview": content[:500],
            }

    # Per-contact memory
    contacts_dir = MEMORY_DIR / "contacts"
    if contacts_dir.exists():
        for d in sorted(contacts_dir.iterdir()):
            if d.is_dir():
                entry = {
                    "hash": d.name,
                    "display_name": hash_names.get(d.name, d.name),
                    "files": {},
                }
                for name in ("MEMORY.md", "HISTORY.md"):
                    fpath = d / name
                    if fpath.exists():
                        content = fpath.read_text(errors="replace").strip()
                        entry["files"][name] = {
                            "size_bytes": len(content),
                            "lines": content.count("\n") + 1,
                            "modified": datetime.fromtimestamp(fpath.stat().st_mtime).isoformat(),
                        }
                if entry["files"]:
                    result["contacts"].append(entry)

    return result


@app.get("/api/memory/read")
def read_memory_file(scope: str = Query(...), filename: str = Query(...)):
    """Read the actual content of a memory file.

    scope: 'global' or a contact hash like '214396ece5fb'
    filename: 'MEMORY.md' or 'HISTORY.md'
    """
    if filename not in ("MEMORY.md", "HISTORY.md"):
        raise HTTPException(400, "Only MEMORY.md and HISTORY.md are readable")
    if scope == "global":
        path = MEMORY_DIR / filename
    else:
        # Sanitize hash to prevent path traversal
        safe_hash = "".join(c for c in scope if c.isalnum())
        path = MEMORY_DIR / "contacts" / safe_hash / filename
    if not path.exists():
        raise HTTPException(404, f"File not found: {scope}/{filename}")
    return {"content": path.read_text(errors="replace"), "scope": scope, "filename": filename}


# ══════════════════════════════════════════════════════════════
# IDENTITY FILES (SOUL.md, USER.md)
# ══════════════════════════════════════════════════════════════

@app.get("/api/identity")
def get_identity():
    """Current identity files."""
    result = {}
    for name in ("SOUL.md", "USER.md"):
        path = IDENTITY_DIR / name
        if path.exists():
            result[name] = path.read_text(errors="replace")
        else:
            result[name] = ""
    return result


class IdentityUpdate(BaseModel):
    filename: str
    content: str


@app.put("/api/identity")
def update_identity(body: IdentityUpdate):
    """Update an identity file."""
    if body.filename not in ("SOUL.md", "USER.md"):
        raise HTTPException(400, "Only SOUL.md and USER.md can be edited")
    path = IDENTITY_DIR / body.filename
    path.write_text(body.content)
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════
# CAMPAIGNS (BROADCAST)
# ══════════════════════════════════════════════════════════════

@app.get("/api/campaigns")
def get_campaigns():
    """Broadcast campaigns."""
    if not BROADCAST_DB.exists():
        return {"campaigns": []}
    with _db(BROADCAST_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    return {"campaigns": _rows_to_dicts(rows)}


# ══════════════════════════════════════════════════════════════
# REFLECTION / LEARNING
# ══════════════════════════════════════════════════════════════

@app.get("/api/lessons")
def get_lessons():
    """Reflection lessons learned."""
    if not REFLECTION_DB.exists():
        return {"lessons": [], "stats": {}}
    with _db(REFLECTION_DB) as conn:
        lessons = conn.execute(
            "SELECT * FROM lessons ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        by_category = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM lessons GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
    return {
        "lessons": _rows_to_dicts(lessons),
        "stats": _rows_to_dicts(by_category),
    }


# ══════════════════════════════════════════════════════════════
# CRON JOBS
# ══════════════════════════════════════════════════════════════

@app.get("/api/cron")
def get_cron_jobs():
    """Scheduled cron jobs."""
    with _db(CONTACTS_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM cron_jobs ORDER BY next_run_at ASC"
        ).fetchall()
    return {"jobs": _rows_to_dicts(rows)}


# ══════════════════════════════════════════════════════════════
# BOT CONTROL
# ══════════════════════════════════════════════════════════════

@app.post("/api/restart")
def restart_bot():
    """Restart the daemon."""
    script = Path.home() / ".claude" / "skills" / "happycapy-whatsapp" / "scripts" / "start.sh"
    if not script.exists():
        raise HTTPException(404, "Start script not found")
    try:
        result = subprocess.run(
            ["bash", str(script), "restart"],
            capture_output=True, text=True, timeout=15
        )
        return {"status": "ok", "output": result.stdout + result.stderr}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "message": "Restart command timed out"}
    except Exception as e:
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════
# GROUPS
# ══════════════════════════════════════════════════════════════

@app.get("/api/groups")
def get_groups(limit: int = Query(50, le=200)):
    """WhatsApp group cards."""
    with _db(CONTACTS_DB) as conn:
        rows = conn.execute("""
            SELECT group_jid, group_name, member_count, message_rate,
                   last_active, updated_at
            FROM group_cards ORDER BY updated_at DESC LIMIT ?
        """, (limit,)).fetchall()
    return {"groups": _rows_to_dicts(rows)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3456)
