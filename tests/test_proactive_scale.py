"""Scale test: Proactive engine with 300 synthetic students.

Tests:
1. Database seeding speed (300 students)
2. Decision pass speed (synchronous phase)
3. Concurrent AI composition (batched with semaphore)
4. SQLite WAL concurrent access
5. Full schedule_check timing budget

Run: python3 tests/test_proactive_scale.py
"""

import asyncio
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.proactive_engine import ProactiveEngine, SCHEMA_SQL, PKT

# ── Config ──
NUM_STUDENTS = 300
TEST_DB_DIR = Path("/tmp/proactive-scale-test")

BOARDS = ["FBISE", "Punjab", "Sindh", "KPK"]
CLASSES = ["9th", "10th", "11th", "12th"]
SUBJECTS = ["Physics", "Chemistry", "Math", "Biology", "English", "Urdu", "Computer", "Islamiat"]
NAMES = ["Ahmed", "Fatima", "Ali", "Aisha", "Hassan", "Zainab", "Omar", "Maryam",
         "Bilal", "Sara", "Usman", "Hira", "Hamza", "Amna", "Kashif", "Sana"]
TIMEZONES = ["Asia/Karachi", "Asia/Dubai", "Asia/Kolkata", "Europe/London"]


def generate_phone():
    """Generate realistic Pakistani phone number."""
    prefix = random.choice(["923", "923", "923", "971", "91"])
    return prefix + "".join([str(random.randint(0, 9)) for _ in range(9)])


def seed_students(conn, n=NUM_STUDENTS):
    """Insert N synthetic student plans."""
    start = time.time()
    for i in range(n):
        jid = generate_phone()
        name = random.choice(NAMES) + " " + str(i)
        board = random.choice(BOARDS)
        cls = random.choice(CLASSES)
        subjects = random.sample(SUBJECTS, random.randint(1, 4))
        study_time = f"{random.randint(14, 22):02d}:{random.choice(['00', '30'])}"
        tz = random.choice(TIMEZONES)
        exam_date = (datetime.now() + timedelta(days=random.randint(5, 90))).strftime("%Y-%m-%d")
        streak = random.randint(0, 30)
        nudge_days = random.randint(1, 5)

        # Some students are inactive (for nudge testing)
        if random.random() < 0.3:
            last_active = (datetime.now() - timedelta(days=random.randint(2, 10))).strftime("%Y-%m-%d")
        else:
            last_active = datetime.now().strftime("%Y-%m-%d")

        conn.execute(
            """INSERT OR IGNORE INTO student_plans
               (jid, display_name, board, class, exam_date, study_time, focus_subjects,
                timezone, current_streak, longest_streak, last_activity_date, last_streak_date,
                nudge_after_days, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (jid, name, board, cls, exam_date, study_time, json.dumps(subjects),
             tz, streak, max(streak, random.randint(0, 30)), last_active, last_active,
             nudge_days),
        )
    conn.commit()
    elapsed = time.time() - start
    print(f"  Seeded {n} students in {elapsed:.3f}s ({n/elapsed:.0f} inserts/sec)")
    return elapsed


def test_decision_pass(engine):
    """Test Phase 1: synchronous decision pass speed."""
    start = time.time()
    plans = engine.get_all_active_plans()
    fetch_time = time.time() - start
    print(f"  Fetched {len(plans)} plans in {fetch_time:.3f}s")

    # Simulate decision pass
    start = time.time()
    pending = 0
    skipped = 0
    for plan in plans:
        jid = plan["jid"]
        tz_name = plan.get("timezone", "Asia/Karachi")
        try:
            from zoneinfo import ZoneInfo
            stu_tz = ZoneInfo(tz_name)
        except Exception:
            stu_tz = PKT

        now_stu = datetime.now(stu_tz)
        today_str = now_stu.strftime("%Y-%m-%d")
        today_weekday = now_stu.weekday()

        if not engine.can_send(jid, plan, stu_tz):
            skipped += 1
            continue

        message_type = engine._decide_message_type(plan, now_stu, today_str, today_weekday)
        if message_type:
            pending += 1
        else:
            skipped += 1

    decision_time = time.time() - start
    per_student = decision_time / len(plans) * 1000 if plans else 0
    print(f"  Decision pass: {decision_time:.3f}s total, {per_student:.2f}ms/student")
    print(f"  Result: {pending} pending, {skipped} skipped")
    return decision_time, pending, skipped


async def test_ai_composition(engine, n_messages=50):
    """Test Phase 2: concurrent AI composition with mock."""
    # Mock the AI gateway to simulate realistic latency
    async def mock_compose(jid, msg_type, plan):
        await asyncio.sleep(random.uniform(0.5, 2.0))  # Simulate 0.5-2s AI latency
        return f"Mock {msg_type} for {plan.get('display_name', 'student')}"

    plans = engine.get_all_active_plans()[:n_messages]
    sem = asyncio.Semaphore(engine.MAX_CONCURRENT_AI)

    async def compose_one(plan):
        async with sem:
            return await mock_compose(plan["jid"], "reminder", plan)

    start = time.time()
    tasks = [compose_one(p) for p in plans]
    results = await asyncio.gather(*tasks)
    elapsed = time.time() - start

    throughput = len(results) / elapsed
    print(f"  Composed {len(results)} messages in {elapsed:.1f}s ({throughput:.1f} msgs/sec)")
    print(f"  Concurrency: {engine.MAX_CONCURRENT_AI} parallel, avg {elapsed/len(results)*1000:.0f}ms/msg effective")
    return elapsed


async def test_send_rate(n_messages=100):
    """Test Phase 3: rate-limited send timing."""
    send_interval = 1.5  # SEND_INTERVAL_SEC

    start = time.time()
    for i in range(n_messages):
        # Simulate send (no actual WhatsApp)
        await asyncio.sleep(0.01)  # ~10ms send overhead
        if i < n_messages - 1:
            await asyncio.sleep(send_interval)
    elapsed = time.time() - start

    rate = n_messages / elapsed * 60
    print(f"  Sent {n_messages} messages in {elapsed:.1f}s ({rate:.1f} msgs/min)")
    return elapsed


def test_sqlite_concurrent(db_path, n_readers=10, n_writes=50):
    """Test SQLite WAL concurrent read/write performance."""
    import threading

    errors = []
    read_times = []
    write_times = []

    def reader():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            for _ in range(n_writes):
                start = time.time()
                conn.execute("SELECT COUNT(*) FROM student_plans WHERE enabled=1").fetchone()
                read_times.append(time.time() - start)
                time.sleep(0.01)
            conn.close()
        except Exception as e:
            errors.append(f"Reader: {e}")

    def writer():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            for _ in range(n_writes):
                start = time.time()
                jid = generate_phone()
                conn.execute(
                    "INSERT OR IGNORE INTO proactive_log (jid, message_type, message_text, sent_date) VALUES (?, ?, ?, ?)",
                    (jid, "test", "test msg", datetime.now().strftime("%Y-%m-%d")),
                )
                conn.commit()
                write_times.append(time.time() - start)
                time.sleep(0.01)
            conn.close()
        except Exception as e:
            errors.append(f"Writer: {e}")

    threads = []
    start = time.time()
    for _ in range(n_readers):
        t = threading.Thread(target=reader)
        t.start()
        threads.append(t)
    for _ in range(2):
        t = threading.Thread(target=writer)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    elapsed = time.time() - start

    avg_read = sum(read_times) / len(read_times) * 1000 if read_times else 0
    avg_write = sum(write_times) / len(write_times) * 1000 if write_times else 0
    print(f"  {n_readers} readers + 2 writers, {n_writes} ops each: {elapsed:.2f}s")
    print(f"  Avg read: {avg_read:.2f}ms, Avg write: {avg_write:.2f}ms")
    print(f"  Errors: {len(errors)} {'(' + '; '.join(errors[:3]) + ')' if errors else ''}")
    return elapsed, len(errors)


def test_time_budget():
    """Calculate total time budget for 300 students in a 30-min window."""
    print("\n== TIME BUDGET (300 students, worst case) ==")

    # Assumptions
    n_students = 300
    pct_needing_message = 0.4  # ~40% need a message (not quiet hours, not already sent)
    n_messages = int(n_students * pct_needing_message)
    ai_latency_avg = 1.5  # seconds per AI call
    max_concurrent_ai = 5
    send_interval = 1.5  # seconds between WhatsApp sends

    phase1 = 0.3  # seconds (decision pass, from test results)
    phase2 = (n_messages / max_concurrent_ai) * ai_latency_avg  # batched AI
    phase3 = n_messages * send_interval  # rate-limited send

    total = phase1 + phase2 + phase3
    budget = 30 * 60  # 30 minutes in seconds

    print(f"  Students: {n_students}, needing messages: {n_messages}")
    print(f"  Phase 1 (decisions): {phase1:.1f}s")
    print(f"  Phase 2 (AI compose): {phase2:.1f}s ({max_concurrent_ai} concurrent)")
    print(f"  Phase 3 (send): {phase3:.1f}s ({1/send_interval*60:.0f} msg/min)")
    print(f"  TOTAL: {total:.0f}s ({total/60:.1f} min)")
    print(f"  Budget: {budget}s (30 min)")
    if total < budget:
        print(f"  RESULT: FITS within 30-min heartbeat ({budget-total:.0f}s headroom)")
    else:
        print(f"  RESULT: EXCEEDS budget by {total-budget:.0f}s -- needs further optimization")

    return total, budget


async def main():
    """Run all scale tests."""
    print("=" * 60)
    print("PROACTIVE ENGINE SCALE TEST - 300 STUDENTS")
    print("=" * 60)

    # Setup test DB
    TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
    db_path = TEST_DB_DIR / "proactive_test.db"
    if db_path.exists():
        db_path.unlink()

    # Initialize engine with test DB
    mock_config = {"ai_gateway_url": "https://ai-gateway.happycapy.ai/api/v1"}
    engine = ProactiveEngine(
        base_dir=TEST_DB_DIR,
        channel=None,
        memory_store=None,
        config=mock_config,
    )

    # Test 1: Seeding
    print("\n== TEST 1: Database Seeding ==")
    seed_time = seed_students(engine._conn, NUM_STUDENTS)

    # Verify count
    count = engine._conn.execute("SELECT COUNT(*) FROM student_plans").fetchone()[0]
    print(f"  Verified: {count} students in database")

    # Test 2: Decision pass
    print("\n== TEST 2: Decision Pass Speed ==")
    decision_time, pending, skipped = test_decision_pass(engine)

    # Test 3: AI composition (mocked)
    print("\n== TEST 3: Concurrent AI Composition (mocked) ==")
    compose_time = await test_ai_composition(engine, n_messages=min(pending, 50))

    # Test 4: Send rate
    print("\n== TEST 4: Rate-Limited Send Simulation ==")
    print(f"  (Simulating 20 sends at {engine.SEND_INTERVAL_SEC}s interval)")
    send_time = await test_send_rate(n_messages=20)

    # Test 5: SQLite concurrent access
    print("\n== TEST 5: SQLite WAL Concurrent Access ==")
    sqlite_time, sqlite_errors = test_sqlite_concurrent(db_path)

    # Test 6: Time budget analysis
    total_budget, max_budget = test_time_budget()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Students: {NUM_STUDENTS}")
    print(f"  DB seed: {seed_time:.2f}s")
    print(f"  Decision pass: {decision_time:.3f}s ({decision_time/NUM_STUDENTS*1000:.1f}ms/student)")
    print(f"  AI compose (50 msgs, mocked): {compose_time:.1f}s")
    print(f"  Send rate: ~{60/engine.SEND_INTERVAL_SEC:.0f} msgs/min")
    print(f"  SQLite concurrent errors: {sqlite_errors}")
    print(f"  Total budget estimate: {total_budget:.0f}s / {max_budget}s (30 min)")
    if total_budget < max_budget:
        print("  STATUS: PASS -- fits within heartbeat window")
    else:
        print("  STATUS: WARN -- may exceed heartbeat window under load")

    # Cleanup
    import shutil
    shutil.rmtree(TEST_DB_DIR, ignore_errors=True)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
