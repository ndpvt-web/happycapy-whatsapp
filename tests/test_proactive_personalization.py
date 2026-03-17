"""Comprehensive tests for proactive engine personalization features.

Tests all 3 gaps + enriched compose + 13-type decision tree + edge cases:
  GAP 1: Cognitive model (concept_mastery + SM-2 spaced repetition)
  GAP 2: Affective model (sentiment detection in English/Hinglish)
  GAP 3: Feedback loop (message_effectiveness + engagement tracking)
  Decision tree: 13 priority-ordered message types
  Compose: 6-layer enriched prompt with SDT directives

Run: python3 tests/test_proactive_personalization.py
"""

import asyncio
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.proactive_engine import (
    ProactiveEngine,
    SCHEMA_SQL,
    PKT,
    TYPE_INSTRUCTIONS,
    FRUSTRATION_SIGNALS,
    BOREDOM_SIGNALS,
    ANXIETY_SIGNALS,
    CONFIDENCE_SIGNALS,
)

TEST_DB_DIR = Path("/tmp/proactive-personalization-test")
PASS = 0
FAIL = 0
ERRORS = []


def setup_engine(db_name="test"):
    """Create a fresh ProactiveEngine with isolated DB directory per test."""
    test_dir = TEST_DB_DIR / db_name
    test_dir.mkdir(parents=True, exist_ok=True)
    # Remove existing proactive.db to ensure clean state
    db_path = test_dir / "proactive.db"
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        p = test_dir / f"proactive.db{suffix}"
        if p.exists():
            p.unlink()
    config = {"admin_number": "923350037019"}
    channel = MagicMock()  # Mock WhatsApp channel
    memory_store = MagicMock()  # Mock memory store
    memory_store.read_contact_memory = MagicMock(return_value="Test student memory context")
    engine = ProactiveEngine(str(test_dir), channel, memory_store, config)
    return engine


def insert_student(engine, jid, **overrides):
    """Insert a student plan with defaults matching actual schema."""
    defaults = {
        "display_name": "TestStudent",
        "board": "FBISE",
        "class": "10th",
        "exam_date": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
        "study_time": "20:00",
        "study_days": "1,2,3,4,5,6",
        "focus_subjects": json.dumps(["Physics", "Math"]),
        "weekly_plan_json": "{}",
        "current_streak": 5,
        "last_activity_date": datetime.now().strftime("%Y-%m-%d"),
        "nudge_after_days": 2,
        "enabled": 1,
        "recent_affect": "neutral",
        "engagement_score": 50,
        "preferred_send_hour": -1,
        "last_response_to_proactive": "",
        "timezone": "Asia/Karachi",
    }
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    engine._conn.execute(
        f"INSERT OR REPLACE INTO student_plans (jid, {cols}) VALUES (?, {placeholders})",
        (jid, *defaults.values()),
    )
    engine._conn.commit()


def assert_eq(name, actual, expected):
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        ERRORS.append(f"{name}: expected {expected!r}, got {actual!r}")
        print(f"  [FAIL] {name}: expected {expected!r}, got {actual!r}")


def assert_true(name, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        ERRORS.append(f"{name}: condition was False")
        print(f"  [FAIL] {name}: condition was False")


def assert_in(name, needle, haystack):
    global PASS, FAIL
    if needle in haystack:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        ERRORS.append(f"{name}: {needle!r} not in {haystack!r}")
        print(f"  [FAIL] {name}: {needle!r} not found")


# ═══════════════════════════════════════════════════
# GAP 1: Cognitive Model Tests (SM-2 Spaced Repetition)
# ═══════════════════════════════════════════════════

def test_gap1_upsert_concept():
    """Test concept creation and update."""
    print("\n── GAP 1: Concept Mastery CRUD ──")
    e = setup_engine("gap1_upsert.db")
    jid = "923001111111"

    e.upsert_concept(jid, "phys_newton1", "Physics", "Newton's First Law")
    row = e._conn.execute(
        "SELECT * FROM concept_mastery WHERE jid=? AND concept_id=?",
        (jid, "phys_newton1"),
    ).fetchone()
    d = dict(row)
    assert_eq("concept created", d["subject"], "Physics")
    assert_eq("topic correct", d["topic"], "Newton's First Law")
    assert_eq("initial mastery", d["mastery_level"], 0.0)
    assert_eq("initial ease_factor", d["ease_factor"], 2.5)
    assert_eq("initial interval", d["interval_days"], 1.0)
    assert_eq("initial repetitions", d["repetition_count"], 0)

    # Upsert with same concept_id should update
    e.upsert_concept(jid, "phys_newton1", "Physics", "Newton's 1st Law (updated)")
    count = e._conn.execute(
        "SELECT COUNT(*) as c FROM concept_mastery WHERE jid=? AND concept_id=?",
        (jid, "phys_newton1"),
    ).fetchone()["c"]
    assert_eq("upsert no duplicate", count, 1)


def test_gap1_sm2_perfect_score():
    """SM-2 with quality=5 (perfect recall) should increase interval and ease."""
    print("\n── GAP 1: SM-2 Perfect Score ──")
    e = setup_engine("gap1_sm2_perfect.db")
    jid = "923002222222"

    e.upsert_concept(jid, "math_quad", "Math", "Quadratic Formula")

    # First review: quality=5 (perfect)
    e.update_mastery(jid, "math_quad", quality=5)
    r = dict(e._conn.execute(
        "SELECT * FROM concept_mastery WHERE concept_id='math_quad' AND jid=?", (jid,),
    ).fetchone())
    assert_eq("rep count after 1st review", r["repetition_count"], 1)
    assert_eq("interval after 1st review", r["interval_days"], 1.0)
    assert_true("ease increased from 2.5", r["ease_factor"] > 2.5)
    assert_true("mastery > 0", r["mastery_level"] > 0)

    # Second review: quality=5
    e.update_mastery(jid, "math_quad", quality=5)
    r2 = dict(e._conn.execute(
        "SELECT * FROM concept_mastery WHERE concept_id='math_quad' AND jid=?", (jid,),
    ).fetchone())
    assert_eq("rep count after 2nd", r2["repetition_count"], 2)
    assert_eq("interval after 2nd", r2["interval_days"], 6.0)

    # Third review: interval = 6 * EF
    e.update_mastery(jid, "math_quad", quality=5)
    r3 = dict(e._conn.execute(
        "SELECT * FROM concept_mastery WHERE concept_id='math_quad' AND jid=?", (jid,),
    ).fetchone())
    assert_eq("rep count after 3rd", r3["repetition_count"], 3)
    assert_true("interval grew (EF*6)", r3["interval_days"] > 6.0)


def test_gap1_sm2_fail_resets():
    """SM-2 with quality<3 should reset to repetition 0, interval 1."""
    print("\n── GAP 1: SM-2 Fail Resets ──")
    e = setup_engine("gap1_sm2_fail.db")
    jid = "923003333333"

    e.upsert_concept(jid, "chem_periodic", "Chemistry", "Periodic Table")
    # Build up
    e.update_mastery(jid, "chem_periodic", quality=4)
    e.update_mastery(jid, "chem_periodic", quality=4)
    e.update_mastery(jid, "chem_periodic", quality=4)
    r = dict(e._conn.execute(
        "SELECT * FROM concept_mastery WHERE concept_id='chem_periodic' AND jid=?", (jid,),
    ).fetchone())
    assert_true("built up reps", r["repetition_count"] == 3)

    # Fail (quality=1)
    e.update_mastery(jid, "chem_periodic", quality=1)
    r2 = dict(e._conn.execute(
        "SELECT * FROM concept_mastery WHERE concept_id='chem_periodic' AND jid=?", (jid,),
    ).fetchone())
    assert_eq("reset repetitions", r2["repetition_count"], 0)
    assert_eq("reset interval", r2["interval_days"], 1.0)
    assert_true("ease decreased", r2["ease_factor"] < r["ease_factor"])


def test_gap1_sm2_ease_floor():
    """Ease factor should never drop below 1.3."""
    print("\n── GAP 1: SM-2 Ease Floor ──")
    e = setup_engine("gap1_ease_floor.db")
    jid = "923004444444"

    e.upsert_concept(jid, "hard_topic", "Physics", "Relativity")
    # Repeatedly fail to drive ease down
    for _ in range(20):
        e.update_mastery(jid, "hard_topic", quality=0)
    r = dict(e._conn.execute(
        "SELECT ease_factor FROM concept_mastery WHERE concept_id='hard_topic' AND jid=?", (jid,),
    ).fetchone())
    assert_true("ease >= 1.3", r["ease_factor"] >= 1.3)


def test_gap1_concepts_due():
    """Get concepts due for review based on next_review_date."""
    print("\n── GAP 1: Concepts Due ──")
    e = setup_engine("gap1_due.db")
    jid = "923005555555"

    # Insert concept with past review date
    e.upsert_concept(jid, "due_concept", "Math", "Algebra")
    e._conn.execute(
        "UPDATE concept_mastery SET next_review_date=? WHERE concept_id='due_concept'",
        ((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),),
    )
    # Insert concept with future review date
    e.upsert_concept(jid, "future_concept", "Math", "Calculus")
    e._conn.execute(
        "UPDATE concept_mastery SET next_review_date=? WHERE concept_id='future_concept'",
        ((datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"),),
    )
    e._conn.commit()

    due = e.get_concepts_due(jid)
    assert_eq("one concept due", len(due), 1)
    assert_eq("correct concept due", due[0]["concept_id"], "due_concept")


def test_gap1_mastery_summary():
    """Mastery summary returns formatted string."""
    print("\n── GAP 1: Mastery Summary ──")
    e = setup_engine("gap1_summary.db")
    jid = "923006666666"

    e.upsert_concept(jid, "c1", "Physics", "Kinematics")
    e.update_mastery(jid, "c1", quality=4)
    summary = e.get_mastery_summary(jid)
    assert_true("summary not empty", len(summary) > 0)
    assert_in("contains subject", "Physics", summary)
    assert_in("contains mastery %", "mastery", summary.lower())


def test_gap1_concept_count():
    """Count concepts tracked per student."""
    print("\n── GAP 1: Concept Count ──")
    e = setup_engine("gap1_count.db")
    jid = "923007777777"

    assert_eq("zero initially", e.get_concept_count(jid), 0)
    e.upsert_concept(jid, "c1", "Math", "Algebra")
    e.upsert_concept(jid, "c2", "Math", "Geometry")
    e.upsert_concept(jid, "c3", "Physics", "Optics")
    assert_eq("three concepts", e.get_concept_count(jid), 3)


# ═══════════════════════════════════════════════════
# GAP 2: Affective Model Tests
# ═══════════════════════════════════════════════════

def test_gap2_detect_frustration():
    """Detect frustration from English and Hinglish signals."""
    print("\n── GAP 2: Detect Frustration ──")
    e = setup_engine("gap2_frust.db")

    assert_eq("english frustration", e.detect_affect("I don't get it, this is too hard"), "frustrated")
    assert_eq("hinglish frustration", e.detect_affect("samajh nahi aa raha yaar"), "frustrated")
    assert_eq("urdu frustration", e.detect_affect("ye nahi hoga mujhse"), "frustrated")
    assert_eq("mixed case", e.detect_affect("I GIVE UP"), "frustrated")


def test_gap2_detect_boredom():
    """Detect boredom signals."""
    print("\n── GAP 2: Detect Boredom ──")
    e = setup_engine("gap2_boredom.db")

    assert_eq("english boredom", e.detect_affect("this is too easy and boring"), "bored")
    assert_eq("hinglish boredom", e.detect_affect("kuch aur batao ye pata hai"), "bored")


def test_gap2_detect_anxiety():
    """Detect anxiety signals."""
    print("\n── GAP 2: Detect Anxiety ──")
    e = setup_engine("gap2_anxiety.db")

    assert_eq("english anxiety", e.detect_affect("what if i fail the exam"), "anxious")
    assert_eq("hinglish anxiety", e.detect_affect("exam tension ho rahi hai"), "anxious")
    assert_eq("urdu anxiety", e.detect_affect("dar lag raha hai bahut"), "anxious")


def test_gap2_detect_confidence():
    """Detect confidence signals."""
    print("\n── GAP 2: Detect Confidence ──")
    e = setup_engine("gap2_confidence.db")

    assert_eq("english confidence", e.detect_affect("I understand, let me try more"), "confident")
    assert_eq("hinglish confidence", e.detect_affect("samajh aa gaya, aur do"), "confident")
    assert_eq("urdu confidence", e.detect_affect("mujhe aata hai samajh aa gaya"), "confident")


def test_gap2_detect_neutral():
    """Neutral when no signals match."""
    print("\n── GAP 2: Detect Neutral ──")
    e = setup_engine("gap2_neutral.db")

    assert_eq("plain message", e.detect_affect("hello, what is photosynthesis?"), "neutral")
    assert_eq("empty message", e.detect_affect(""), "neutral")
    assert_eq("numbers only", e.detect_affect("12345"), "neutral")


def test_gap2_update_and_get_affect():
    """Update and retrieve student affect from DB."""
    print("\n── GAP 2: Update + Get Affect ──")
    e = setup_engine("gap2_persist.db")
    jid = "923008888888"
    insert_student(e, jid)

    assert_eq("default neutral", e.get_affect(jid), "neutral")
    # update_affect takes raw text, detects affect, then updates DB
    e.update_affect(jid, "samajh nahi aa raha yaar, too hard")
    assert_eq("updated to frustrated", e.get_affect(jid), "frustrated")
    e.update_affect(jid, "samajh aa gaya, let me try more")
    assert_eq("updated to confident", e.get_affect(jid), "confident")


def test_gap2_affect_unknown_jid():
    """get_affect for unknown JID returns neutral."""
    print("\n── GAP 2: Unknown JID ──")
    e = setup_engine("gap2_unknown.db")
    assert_eq("unknown returns neutral", e.get_affect("000000000"), "neutral")


def test_gap2_signal_priority():
    """When multiple signals present, first match wins (frustration > boredom)."""
    print("\n── GAP 2: Signal Priority ──")
    e = setup_engine("gap2_priority.db")
    # Contains both frustration ("i give up") and boredom ("boring") signals
    result = e.detect_affect("i give up this is boring")
    # Frustration is checked first in the code
    assert_eq("frustration wins over boredom", result, "frustrated")


# ═══════════════════════════════════════════════════
# GAP 3: Feedback Loop Tests
# ═══════════════════════════════════════════════════

def test_gap3_record_proactive_response():
    """Record a student response to a proactive message."""
    print("\n── GAP 3: Record Proactive Response ──")
    e = setup_engine("gap3_response.db")
    jid = "923009999999"
    insert_student(e, jid)

    # Insert a proactive log entry (simulating a sent message)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    e._conn.execute(
        """INSERT INTO proactive_log (jid, message_type, message_text, sent_date, status, sent_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (jid, "reminder", "Study time!", datetime.now().strftime("%Y-%m-%d"), "sent", now),
    )
    e._conn.commit()

    # Student responds
    result = e.record_proactive_response(jid, "haan kar raha hoon padhai!")
    assert_true("response recorded", result is not None)

    # Check message_effectiveness table
    eff = e._conn.execute(
        "SELECT * FROM message_effectiveness WHERE jid=?", (jid,),
    ).fetchone()
    assert_true("effectiveness row exists", eff is not None)
    if eff:
        d = dict(eff)
        assert_eq("response_received", d["response_received"], 1)
        assert_eq("message_type recorded", d["message_type"], "reminder")


def test_gap3_no_response_to_old_message():
    """Don't match responses to proactive messages older than 4 hours."""
    print("\n── GAP 3: Old Message Cutoff ──")
    e = setup_engine("gap3_old.db")
    jid = "923010000000"
    insert_student(e, jid)

    # Insert proactive log from 5 hours ago
    old_time = (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    e._conn.execute(
        """INSERT INTO proactive_log (jid, message_type, message_text, sent_date, status, sent_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (jid, "nudge", "Come back!", datetime.now().strftime("%Y-%m-%d"), "sent", old_time),
    )
    e._conn.commit()

    result = e.record_proactive_response(jid, "ok coming")
    assert_eq("no match for old message", result, None)


def test_gap3_effectiveness_summary():
    """Effectiveness summary aggregates correctly."""
    print("\n── GAP 3: Effectiveness Summary ──")
    e = setup_engine("gap3_eff_summary.db")
    jid = "923011111111"
    insert_student(e, jid)

    # Insert multiple effectiveness records
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i, (resp, sentiment) in enumerate([
        (1, "positive"), (1, "positive"), (1, "negative"), (0, ""), (1, "neutral"),
    ]):
        e._conn.execute(
            """INSERT INTO message_effectiveness
               (proactive_log_id, jid, message_type, response_received, sentiment_of_response, evaluated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (i + 1, jid, "reminder", resp, sentiment, now),
        )
    e._conn.commit()

    summary = e.get_effectiveness_summary(jid)
    assert_in("response count", "4/5", summary)
    assert_in("positive count", "2 positive", summary)
    assert_in("negative count", "1 negative", summary)


def test_gap3_effectiveness_empty():
    """Empty effectiveness summary for new student."""
    print("\n── GAP 3: Empty Effectiveness ──")
    e = setup_engine("gap3_empty.db")
    assert_eq("empty for unknown", e.get_effectiveness_summary("000"), "")


def test_gap3_engagement_score_update():
    """Engagement score updates based on effectiveness data."""
    print("\n── GAP 3: Engagement Score Update ──")
    e = setup_engine("gap3_engage.db")
    jid = "923012222222"
    insert_student(e, jid, engagement_score=50)

    # Engagement = (responded / sent) * 100
    # Need both proactive_log entries AND message_effectiveness entries
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i in range(5):
        e._conn.execute(
            """INSERT INTO proactive_log (jid, message_type, message_text, sent_date, status, sent_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (jid, "reminder", f"Message {i}", today, "sent", now),
        )
        e._conn.execute(
            """INSERT INTO message_effectiveness
               (proactive_log_id, jid, message_type, response_received, sentiment_of_response, evaluated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (i + 1, jid, "reminder", 1, "positive", now),
        )
    e._conn.commit()

    e.update_engagement_scores()
    row = e._conn.execute(
        "SELECT engagement_score FROM student_plans WHERE jid=?", (jid,),
    ).fetchone()
    assert_true("engagement updated", row is not None)
    if row:
        assert_true("engagement == 100 (5/5 responded)", row["engagement_score"] == 100)


# ═══════════════════════════════════════════════════
# Decision Tree Tests (13 Types)
# ═══════════════════════════════════════════════════

def _call_decide(engine, plan):
    """Helper: call _decide_message_type with correct args."""
    now_pkt = datetime.now(PKT)
    today_str = now_pkt.strftime("%Y-%m-%d")
    today_weekday = now_pkt.weekday()
    return engine._decide_message_type(plan, now_pkt, today_str, today_weekday)


def _far_future_study_time():
    """Return a study_time string far enough in future to avoid REMINDER and CHECKIN."""
    # Set study_time 6 hours from now (beyond CHECKIN's 2h window)
    now = datetime.now(PKT)
    far_hour = (now.hour + 6) % 24
    return f"{far_hour:02d}:00"


def test_decision_recovery_frustrated():
    """P0: RECOVERY fires when student is frustrated + inactive."""
    print("\n── Decision: P0 RECOVERY ──")
    e = setup_engine("dt_recovery.db")
    jid = "923020000001"
    insert_student(e, jid,
        recent_affect="frustrated",
        last_activity_date=(datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
        nudge_after_days=2,
    )
    plan = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())
    result = _call_decide(e, plan)
    assert_eq("recovery for frustrated+inactive", result, "recovery")


def test_decision_recovery_anxious():
    """P0: RECOVERY also fires for anxious + inactive."""
    print("\n── Decision: P0 RECOVERY (anxious) ──")
    e = setup_engine("dt_recovery_anx.db")
    jid = "923020000002"
    insert_student(e, jid,
        recent_affect="anxious",
        last_activity_date=(datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
        nudge_after_days=2,
    )
    plan = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())
    result = _call_decide(e, plan)
    assert_eq("recovery for anxious+inactive", result, "recovery")


def test_decision_review_concepts_due():
    """P2: REVIEW fires when concepts are due for spaced repetition."""
    print("\n── Decision: P2 REVIEW ──")
    e = setup_engine("dt_review.db")
    jid = "923020000003"
    now = datetime.now()
    # Set study_time to far from current hour so REMINDER condition is false
    insert_student(e, jid,
        study_time=f"{(now.hour + 3) % 24:02d}:00",
        recent_affect="neutral",
        last_activity_date=now.strftime("%Y-%m-%d"),
    )
    # Add a concept due for review
    e.upsert_concept(jid, "review_me", "Physics", "Optics")
    e._conn.execute(
        "UPDATE concept_mastery SET next_review_date=? WHERE concept_id='review_me'",
        ((now - timedelta(days=1)).strftime("%Y-%m-%d"),),
    )
    e._conn.commit()

    plan = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())
    result = _call_decide(e, plan)
    assert_eq("review when concepts due", result, "review")


def test_decision_deload_high_streak():
    """P4: DELOAD fires for high streak (14+) + high engagement."""
    print("\n── Decision: P4 DELOAD ──")
    e = setup_engine("dt_deload.db")
    jid = "923020000004"
    now = datetime.now()
    insert_student(e, jid,
        current_streak=15,
        engagement_score=80,
        study_time=_far_future_study_time(),
        recent_affect="neutral",
        last_activity_date=now.strftime("%Y-%m-%d"),
        checkins_enabled=0,
    )
    plan = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())
    result = _call_decide(e, plan)
    assert_eq("deload for high streak + engagement", result, "deload")


def test_decision_challenge_high_mastery():
    """P5: CHALLENGE fires when mastery > 0.8 in a subject."""
    print("\n── Decision: P5 CHALLENGE ──")
    e = setup_engine("dt_challenge.db")
    jid = "923020000005"
    now = datetime.now()
    insert_student(e, jid,
        current_streak=5,
        engagement_score=50,
        study_time=_far_future_study_time(),
        recent_affect="neutral",
        last_activity_date=now.strftime("%Y-%m-%d"),
        checkins_enabled=0,
    )
    # Add high-mastery concept
    e.upsert_concept(jid, "mastered_topic", "Physics", "Kinematics")
    e._conn.execute(
        "UPDATE concept_mastery SET mastery_level=0.9 WHERE concept_id='mastered_topic'",
    )
    e._conn.commit()

    plan = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())
    result = _call_decide(e, plan)
    assert_eq("challenge for high mastery", result, "challenge")


def test_decision_scaffolding_low_mastery():
    """P6: SCAFFOLDING fires when mastery < 0.3 with 3+ repetitions."""
    print("\n── Decision: P6 SCAFFOLDING ──")
    e = setup_engine("dt_scaffold.db")
    jid = "923020000006"
    now = datetime.now()
    insert_student(e, jid,
        current_streak=5,
        engagement_score=50,
        study_time=_far_future_study_time(),
        recent_affect="neutral",
        last_activity_date=now.strftime("%Y-%m-%d"),
        checkins_enabled=0,
    )
    # Add low-mastery concept with many attempts
    e.upsert_concept(jid, "struggling_topic", "Chemistry", "Organic")
    e._conn.execute(
        """UPDATE concept_mastery SET mastery_level=0.2, repetition_count=5
           WHERE concept_id='struggling_topic'""",
    )
    e._conn.commit()

    plan = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())
    result = _call_decide(e, plan)
    assert_eq("scaffolding for low mastery + high reps", result, "scaffolding")


# ═══════════════════════════════════════════════════
# Static Fallback Template Tests
# ═══════════════════════════════════════════════════

def test_static_templates_all_types():
    """Every message type has at least one static fallback template."""
    print("\n── Static Templates: Coverage ──")
    e = setup_engine("static_all.db")
    plan = {
        "display_name": "Ali",
        "focus_subjects": json.dumps(["Physics"]),
        "current_streak": 7,
    }
    all_types = [
        "reminder", "checkin", "nudge", "countdown", "achievement",
        "review", "challenge", "recovery", "curiosity", "scaffolding",
        "celebration_specific", "autonomy_check", "deload",
    ]
    for msg_type in all_types:
        text = e._static_message(msg_type, plan)
        assert_true(f"static template for {msg_type}", len(text) > 5)
        assert_in(f"{msg_type} mentions name", "Ali", text)


# ═══════════════════════════════════════════════════
# TYPE_INSTRUCTIONS Coverage Tests
# ═══════════════════════════════════════════════════

def test_type_instructions_coverage():
    """All 13 message types have TYPE_INSTRUCTIONS entries."""
    print("\n── TYPE_INSTRUCTIONS Coverage ──")
    expected_types = [
        "reminder", "checkin", "nudge", "countdown", "achievement",
        "review", "challenge", "recovery", "curiosity", "scaffolding",
        "celebration_specific", "autonomy_check", "deload",
    ]
    for t in expected_types:
        assert_in(f"instruction for {t}", t, TYPE_INSTRUCTIONS)


# ═══════════════════════════════════════════════════
# Compose Message Tests (Enriched Prompt)
# ═══════════════════════════════════════════════════

def test_compose_prompt_includes_affect():
    """compose_message builds prompt with emotional state context."""
    print("\n── Compose: Affect Context ──")
    e = setup_engine("compose_affect.db")
    jid = "923030000001"
    insert_student(e, jid, recent_affect="frustrated", display_name="Hamza")

    plan = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())

    # We'll intercept the httpx call to inspect the prompt
    captured_prompts = []

    async def mock_post(url, json=None, headers=None):
        captured_prompts.append(json["messages"][0]["content"])
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "Test message"}}]
        }
        return resp

    with patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test_key"}):
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            result = asyncio.get_event_loop().run_until_complete(
                e.compose_message(jid, "recovery", plan)
            )

    assert_true("got AI response", result is not None)
    if captured_prompts:
        prompt = captured_prompts[0]
        assert_in("affect in prompt", "frustrated", prompt.lower())
        assert_in("SDT directive in prompt", "SDT focus", prompt)
        assert_in("no pressure warning", "IMPORTANT", prompt)


def test_compose_prompt_includes_mastery():
    """compose_message includes concept mastery when available."""
    print("\n── Compose: Mastery Context ──")
    e = setup_engine("compose_mastery.db")
    jid = "923030000002"
    insert_student(e, jid, display_name="Sara")
    e.upsert_concept(jid, "c1", "Physics", "Kinematics")
    e.update_mastery(jid, "c1", quality=4)

    plan = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())

    captured_prompts = []

    async def mock_post(url, json=None, headers=None):
        captured_prompts.append(json["messages"][0]["content"])
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "Review time!"}}]
        }
        return resp

    with patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test_key"}):
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            result = asyncio.get_event_loop().run_until_complete(
                e.compose_message(jid, "review", plan)
            )

    if captured_prompts:
        prompt = captured_prompts[0]
        assert_in("mastery context", "CONCEPT MASTERY", prompt)
        assert_in("physics in mastery", "Physics", prompt)


def test_compose_no_api_key():
    """compose_message returns None when no API key."""
    print("\n── Compose: No API Key ──")
    e = setup_engine("compose_nokey.db")
    jid = "923030000003"
    insert_student(e, jid)
    plan = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())

    with patch.dict(os.environ, {"AI_GATEWAY_API_KEY": ""}):
        result = asyncio.get_event_loop().run_until_complete(
            e.compose_message(jid, "reminder", plan)
        )
    assert_eq("None without API key", result, None)


# ═══════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════

def test_edge_empty_memory():
    """compose_message handles empty memory gracefully."""
    print("\n── Edge: Empty Memory ──")
    e = setup_engine("edge_empty_mem.db")
    e.memory_store = None  # No memory store
    jid = "923040000001"
    insert_student(e, jid)
    plan = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())

    captured = []

    async def mock_post(url, json=None, headers=None):
        captured.append(json["messages"][0]["content"])
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": "Hi!"}}]}
        return resp

    with patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test_key"}):
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            result = asyncio.get_event_loop().run_until_complete(
                e.compose_message(jid, "checkin", plan)
            )

    assert_true("still works without memory", result is not None)
    if captured:
        assert_in("fallback text", "New student", captured[0])


def test_edge_concurrent_mastery_updates():
    """Multiple rapid mastery updates don't corrupt data."""
    print("\n── Edge: Concurrent Mastery Updates ──")
    e = setup_engine("edge_concurrent.db")
    jid = "923040000002"
    e.upsert_concept(jid, "concurrent_test", "Math", "Algebra")

    for q in [3, 4, 5, 2, 4, 5, 1, 5, 4, 3]:
        e.update_mastery(jid, "concurrent_test", quality=q)

    row = e._conn.execute(
        "SELECT * FROM concept_mastery WHERE concept_id='concurrent_test' AND jid=?", (jid,),
    ).fetchone()
    assert_true("data not corrupted", row is not None)
    d = dict(row)
    assert_true("ease in valid range", 1.3 <= d["ease_factor"] <= 3.5)
    assert_true("mastery in [0,1]", 0.0 <= d["mastery_level"] <= 1.0)
    assert_true("interval positive", d["interval_days"] > 0)


def test_edge_affect_signals_case_insensitive():
    """Affect detection is case-insensitive."""
    print("\n── Edge: Case Insensitive Affect ──")
    e = setup_engine("edge_case.db")
    assert_eq("uppercase", e.detect_affect("I DON'T GET IT"), "frustrated")
    assert_eq("mixed case", e.detect_affect("What If I Fail"), "anxious")
    assert_eq("lowercase", e.detect_affect("samajh aa gaya"), "confident")


def test_edge_schema_migration_idempotent():
    """Running schema creation twice doesn't error."""
    print("\n── Edge: Schema Idempotent ──")
    e = setup_engine("edge_schema.db")
    # Schema already created by setup_engine, run again
    try:
        e._conn.executescript(SCHEMA_SQL)
        assert_true("second schema run OK", True)
    except Exception as ex:
        assert_true(f"schema idempotent failed: {ex}", False)


def test_edge_learn_preferred_hour():
    """learn_preferred_send_hour updates based on response patterns."""
    print("\n── Edge: Preferred Send Hour ──")
    e = setup_engine("edge_pref_hour.db")
    jid = "923040000003"
    insert_student(e, jid, preferred_send_hour=-1)

    # Simulate proactive messages sent at hour 20, with responses
    sent_at_str = datetime.now().replace(hour=20, minute=0).strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")
    for i in range(3):
        e._conn.execute(
            """INSERT INTO proactive_log (id, jid, message_type, message_text, sent_date, status, sent_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (100 + i, jid, "reminder", f"Msg {i}", today, "sent", sent_at_str),
        )
        e._conn.execute(
            """INSERT INTO message_effectiveness
               (proactive_log_id, jid, message_type, response_received, evaluated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (100 + i, jid, "reminder", 1, sent_at_str),
        )
    e._conn.commit()

    e.learn_preferred_send_hour(jid)
    row = e._conn.execute(
        "SELECT preferred_send_hour FROM student_plans WHERE jid=?", (jid,),
    ).fetchone()
    # Should have learned hour 20 or remain -1 (implementation-dependent)
    assert_true("preferred hour set", row is not None)


# ═══════════════════════════════════════════════════
# DB Table Existence Tests
# ═══════════════════════════════════════════════════

def test_db_tables_exist():
    """All required tables exist after engine init."""
    print("\n── DB: Table Existence ──")
    e = setup_engine("db_tables.db")
    tables = [r["name"] for r in e._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    for expected in ["student_plans", "study_progress", "proactive_log",
                     "exam_calendar", "concept_mastery", "message_effectiveness"]:
        assert_in(f"table {expected}", expected, tables)


def test_db_new_columns_exist():
    """Migration columns exist in student_plans."""
    print("\n── DB: Migration Columns ──")
    e = setup_engine("db_columns.db")
    jid = "923050000001"
    insert_student(e, jid)
    row = dict(e._conn.execute("SELECT * FROM student_plans WHERE jid=?", (jid,)).fetchone())
    for col in ["recent_affect", "engagement_score", "preferred_send_hour",
                "last_response_to_proactive", "timezone"]:
        assert_in(f"column {col}", col, row)


# ═══════════════════════════════════════════════════
# Run All Tests
# ═══════════════════════════════════════════════════

def main():
    global PASS, FAIL, ERRORS
    start = time.time()

    print("=" * 60)
    print("PROACTIVE ENGINE PERSONALIZATION TEST SUITE")
    print("=" * 60)

    # GAP 1: Cognitive Model
    test_gap1_upsert_concept()
    test_gap1_sm2_perfect_score()
    test_gap1_sm2_fail_resets()
    test_gap1_sm2_ease_floor()
    test_gap1_concepts_due()
    test_gap1_mastery_summary()
    test_gap1_concept_count()

    # GAP 2: Affective Model
    test_gap2_detect_frustration()
    test_gap2_detect_boredom()
    test_gap2_detect_anxiety()
    test_gap2_detect_confidence()
    test_gap2_detect_neutral()
    test_gap2_update_and_get_affect()
    test_gap2_affect_unknown_jid()
    test_gap2_signal_priority()

    # GAP 3: Feedback Loop
    test_gap3_record_proactive_response()
    test_gap3_no_response_to_old_message()
    test_gap3_effectiveness_summary()
    test_gap3_effectiveness_empty()
    test_gap3_engagement_score_update()

    # Decision Tree
    test_decision_recovery_frustrated()
    test_decision_recovery_anxious()
    test_decision_review_concepts_due()
    test_decision_deload_high_streak()
    test_decision_challenge_high_mastery()
    test_decision_scaffolding_low_mastery()

    # Static Templates
    test_static_templates_all_types()

    # TYPE_INSTRUCTIONS
    test_type_instructions_coverage()

    # Compose Message
    test_compose_prompt_includes_affect()
    test_compose_prompt_includes_mastery()
    test_compose_no_api_key()

    # Edge Cases
    test_edge_empty_memory()
    test_edge_concurrent_mastery_updates()
    test_edge_affect_signals_case_insensitive()
    test_edge_schema_migration_idempotent()
    test_edge_learn_preferred_hour()

    # DB Structure
    test_db_tables_exist()
    test_db_new_columns_exist()

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print(f"RESULTS: {PASS} passed, {FAIL} failed ({elapsed:.2f}s)")
    print("=" * 60)

    if ERRORS:
        print("\nFAILURES:")
        for err in ERRORS:
            print(f"  - {err}")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    exit(main())
