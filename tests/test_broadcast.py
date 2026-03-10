"""Comprehensive end-to-end tests for broadcast campaign engine.

Tests: CampaignStore, SegmentationEngine, BroadcastEngine, BroadcastIntegration,
template rendering, reply attribution, scheduled campaigns, and all admin command logic.
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent to path for imports
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from src.broadcast import (
    AUTO_SEGMENTS,
    BroadcastEngine,
    BroadcastIntegration,
    BROADCAST_TOOL_DEFINITIONS,
    Campaign,
    CampaignMessage,
    CampaignStore,
    Segment,
    SegmentationEngine,
    create_broadcast_engine,
)


# ── Helpers ──

def make_temp_db() -> tuple[Path, CampaignStore]:
    """Create a CampaignStore with a temporary database file."""
    tmp = tempfile.mktemp(suffix=".db")
    store = CampaignStore(tmp)
    return Path(tmp), store


def make_mock_contact_store(contacts: list[dict] | None = None):
    """Create a mock ContactStore with fake contacts.

    Each contact dict: {jid, push_name, saved_name, verified_name, messages, last_ts}
    """
    if contacts is None:
        contacts = [
            {"jid": "1234567890@s.whatsapp.net", "push_name": "Priya Sharma",
             "saved_name": "Priya", "messages": 35, "last_ts": time.time() - 86400},
            {"jid": "9876543210@s.whatsapp.net", "push_name": "Raj Kumar",
             "saved_name": "Raj", "messages": 5, "last_ts": time.time() - 86400 * 45},
            {"jid": "5555555555@s.whatsapp.net", "push_name": "Sara Ali",
             "saved_name": None, "messages": 80, "last_ts": time.time() - 3600},
            {"jid": "1111111111@s.whatsapp.net", "push_name": "New User",
             "saved_name": None, "messages": 2, "last_ts": time.time() - 3600 * 2},
            {"jid": "2222222222@s.whatsapp.net", "push_name": "Dormant Dave",
             "saved_name": "Dave", "messages": 15, "last_ts": time.time() - 86400 * 60},
        ]

    store = MagicMock()

    # get_all_whatsapp_contacts returns list of dicts
    store.get_all_whatsapp_contacts.return_value = contacts

    # get_sample_count returns message count for a JID
    def _sample_count(jid):
        bare = jid.split("@")[0] if "@" in jid else jid
        for c in contacts:
            c_bare = c["jid"].split("@")[0] if "@" in c["jid"] else c["jid"]
            if c_bare == bare:
                return c.get("messages", 0)
        return 0
    store.get_sample_count.side_effect = _sample_count

    # get_profile returns a mock profile or None
    def _get_profile(jid):
        return None  # Simplified: no profiles
    store.get_profile.side_effect = _get_profile

    # format_profile_for_prompt
    store.format_profile_for_prompt.return_value = ""

    # Mock the internal _conn for _get_last_active
    mock_conn = MagicMock()
    def _execute(sql, params=None):
        result = MagicMock()
        if "conversation_samples" in sql and "timestamp" in sql:
            jid = params[0] if params else ""
            bare = jid.split("@")[0] if "@" in jid else jid
            for c in contacts:
                c_bare = c["jid"].split("@")[0] if "@" in c["jid"] else c["jid"]
                if c_bare == bare:
                    from datetime import datetime, timezone
                    ts = datetime.fromtimestamp(c.get("last_ts", 0), tz=timezone.utc).isoformat()
                    row_mock = MagicMock()
                    row_mock.__getitem__ = lambda self, key: ts if key == "timestamp" else None
                    result.fetchone.return_value = row_mock
                    return result
            result.fetchone.return_value = None
            return result
        result.fetchone.return_value = None
        return result
    mock_conn.execute.side_effect = _execute
    store._conn = mock_conn

    return store


class MockChannel:
    """Mock WhatsApp channel that records all sent messages."""
    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []
        self.sent_media: list[tuple] = []

    async def send_text(self, jid: str, text: str):
        self.sent_messages.append((jid, text))

    async def send_media(self, *args, **kwargs):
        self.sent_media.append((args, kwargs))


# ── Test Functions ──

async def test_campaign_store_crud():
    """Test CampaignStore create, read, update, delete operations."""
    _, store = make_temp_db()
    try:
        # Create campaign
        campaign = Campaign(
            name="Test Sale",
            message_template="Hey {name}! Big sale today!",
            segment_id="all_contacts",
            segment_name="All Contacts",
            status="draft",
            personalize=True,
            total_recipients=5,
        )
        campaign = await store.create_campaign(campaign)
        assert campaign.id.startswith("BC-"), f"Campaign ID format wrong: {campaign.id}"
        assert campaign.created_at, "Created_at not set"

        # Get campaign
        fetched = store.get_campaign(campaign.id)
        assert fetched is not None, "Campaign not found"
        assert fetched.name == "Test Sale"
        assert fetched.segment_id == "all_contacts"
        assert fetched.total_recipients == 5

        # List campaigns
        campaigns = store.list_campaigns()
        assert len(campaigns) == 1
        assert campaigns[0].id == campaign.id

        # Update status
        ok = await store.update_campaign_status(campaign.id, "sending", started_at=time.time())
        assert ok, "Status update failed"
        fetched = store.get_campaign(campaign.id)
        assert fetched.status == "sending"
        assert fetched.started_at > 0

        # Increment counter
        await store.increment_campaign_counter(campaign.id, "sent_count", 3)
        fetched = store.get_campaign(campaign.id)
        assert fetched.sent_count == 3, f"Expected 3, got {fetched.sent_count}"

        # Invalid counter field
        await store.increment_campaign_counter(campaign.id, "invalid_field", 1)
        # Should not raise, just return

        # Delete
        ok = await store.delete_campaign(campaign.id)
        assert ok, "Delete failed"
        assert store.get_campaign(campaign.id) is None, "Campaign still exists after delete"

        print("  PASS: campaign store CRUD")
    finally:
        store.close()


async def test_campaign_messages():
    """Test campaign message CRUD and status tracking."""
    _, store = make_temp_db()
    try:
        # Create parent campaign
        campaign = await store.create_campaign(Campaign(
            name="Msg Test", message_template="Test", segment_id="all_contacts",
        ))

        # Add messages
        messages = [
            CampaignMessage(
                campaign_id=campaign.id,
                recipient_jid=f"{i}@s.whatsapp.net",
                recipient_name=f"User {i}",
                original_text=f"Hello User {i}!",
                status="pending",
            )
            for i in range(5)
        ]
        count = await store.add_messages(messages)
        assert count == 5, f"Expected 5 messages, got {count}"

        # Get pending
        pending = store.get_pending_messages(campaign.id, limit=3)
        assert len(pending) == 3, f"Expected 3 pending, got {len(pending)}"
        assert all(m.status == "pending" for m in pending)

        # Update status
        msg_id = pending[0].id
        await store.update_message_status(msg_id, "sent", sent_at=time.time())
        updated = store.get_campaign_messages(campaign.id, status="sent")
        assert len(updated) == 1
        assert updated[0].id == msg_id
        assert updated[0].sent_at > 0

        # Get stats
        stats = store.get_campaign_stats(campaign.id)
        assert stats.get("pending") == 4, f"Expected 4 pending, got {stats}"
        assert stats.get("sent") == 1, f"Expected 1 sent, got {stats}"

        # All messages
        all_msgs = store.get_campaign_messages(campaign.id)
        assert len(all_msgs) == 5

        # Cascade delete: delete campaign should delete messages
        await store.delete_campaign(campaign.id)
        remaining = store.get_campaign_messages(campaign.id)
        assert len(remaining) == 0, f"Messages not cascade deleted, got {len(remaining)}"

        print("  PASS: campaign messages")
    finally:
        store.close()


async def test_segments():
    """Test segment CRUD and auto-segment listing."""
    _, store = make_temp_db()
    try:
        # List auto-segments
        segments = store.list_segments()
        assert len(segments) == len(AUTO_SEGMENTS), \
            f"Expected {len(AUTO_SEGMENTS)} auto-segments, got {len(segments)}"

        # Verify all auto-segment IDs present
        seg_ids = {s.id for s in segments}
        for auto_id in AUTO_SEGMENTS:
            assert auto_id in seg_ids, f"Auto-segment {auto_id} missing"

        # Get specific auto-segment
        active = store.get_segment("active")
        assert active is not None
        assert active.name == "Active (last 7 days)"
        assert active.segment_type == "auto"
        filters = active.filters
        assert filters.get("active_within_days") == 7

        # Create custom segment
        custom = Segment(
            name="VIP Customers",
            description="Customers with big orders",
            segment_type="custom",
            filter_json=json.dumps({"min_messages": 30}),
        )
        custom = await store.save_segment(custom)
        assert custom.id.startswith("SEG-"), f"Segment ID format wrong: {custom.id}"

        # List should now include custom
        segments = store.list_segments()
        assert len(segments) == len(AUTO_SEGMENTS) + 1

        # Get custom
        fetched = store.get_segment(custom.id)
        assert fetched is not None
        assert fetched.name == "VIP Customers"

        # Cannot delete auto-segments
        assert not await store.delete_segment("all_contacts")

        # Can delete custom
        assert await store.delete_segment(custom.id)
        assert store.get_segment(custom.id) is None

        # Unknown segment returns None
        assert store.get_segment("nonexistent") is None

        print("  PASS: segments")
    finally:
        store.close()


async def test_reply_attribution():
    """Test finding recent broadcasts and recording replies."""
    _, store = make_temp_db()
    try:
        # Create campaign and messages
        campaign = await store.create_campaign(Campaign(
            name="Reply Test", message_template="Test", segment_id="all_contacts",
            total_recipients=2,
        ))
        msg1 = CampaignMessage(
            campaign_id=campaign.id,
            recipient_jid="111@s.whatsapp.net",
            recipient_name="Alice",
            original_text="Hey Alice!",
            status="sent",
        )
        msg2 = CampaignMessage(
            campaign_id=campaign.id,
            recipient_jid="222@s.whatsapp.net",
            recipient_name="Bob",
            original_text="Hey Bob!",
            status="sent",
        )
        await store.add_messages([msg1, msg2])

        # Mark as sent with timestamp
        msgs = store.get_campaign_messages(campaign.id)
        for m in msgs:
            await store.update_message_status(m.id, "sent", sent_at=time.time())

        # Find recent broadcast message for Alice
        found = store.find_recent_broadcast_message("111@s.whatsapp.net", window_hours=1)
        assert found is not None, "Should find message for Alice"
        assert found.recipient_name == "Alice"

        # Not found for unknown contact
        not_found = store.find_recent_broadcast_message("999@s.whatsapp.net", window_hours=1)
        assert not_found is None

        # Record reply
        await store.record_reply(found.id, campaign.id, "Yes, I'm interested!")
        replied_msg = store.get_campaign_messages(campaign.id, status="replied")
        assert len(replied_msg) == 1
        assert replied_msg[0].reply_content == "Yes, I'm interested!"
        assert replied_msg[0].replied_at > 0

        # Check campaign counter was incremented
        updated_campaign = store.get_campaign(campaign.id)
        assert updated_campaign.replied_count == 1

        # Old messages outside window should not be found
        old_msg = CampaignMessage(
            campaign_id=campaign.id,
            recipient_jid="333@s.whatsapp.net",
            recipient_name="Charlie",
            original_text="Old msg",
            status="sent",
        )
        await store.add_messages([old_msg])
        old_msgs = store.get_campaign_messages(campaign.id, status="pending")
        for m in old_msgs:
            if m.recipient_jid == "333@s.whatsapp.net":
                # Set sent_at to 3 days ago
                await store.update_message_status(
                    m.id, "sent", sent_at=time.time() - 86400 * 3
                )
        old_found = store.find_recent_broadcast_message("333@s.whatsapp.net", window_hours=48)
        assert old_found is None, "Should not find message outside attribution window"

        print("  PASS: reply attribution")
    finally:
        store.close()


async def test_segmentation_engine():
    """Test segment resolution with various filter combinations."""
    contact_store = make_mock_contact_store()
    engine = SegmentationEngine(contact_store)

    # all_contacts (min_messages: 0, so everyone with a name)
    seg = Segment(id="all_contacts", name="All", filter_json=json.dumps({"min_messages": 0}))
    contacts = engine.resolve_segment(seg)
    assert len(contacts) == 5, f"Expected 5 all contacts, got {len(contacts)}"

    # active (last 7 days)
    seg = Segment(id="active", name="Active", filter_json=json.dumps({"active_within_days": 7}))
    contacts = engine.resolve_segment(seg)
    # Priya (1 day ago), Sara (1 hour ago), New User (2 hours ago) should match
    names = {c["name"] for c in contacts}
    assert "Sara Ali" in names, f"Sara should be active: {names}"
    assert "New User" in names, f"New User should be active: {names}"
    assert "Dormant Dave" not in names, f"Dave should not be active: {names}"

    # dormant (30+ days inactive, with at least 1 message)
    seg = Segment(id="dormant", name="Dormant", filter_json=json.dumps({"inactive_beyond_days": 30}))
    contacts = engine.resolve_segment(seg)
    names = {c["name"] for c in contacts}
    # Raj (45 days ago, 5 msgs) has saved_name "Raj", Dave (60 days ago, 15 msgs) has saved_name "Dave"
    assert "Raj" in names, f"Raj should be dormant: {names}"
    assert "Dave" in names, f"Dave should be dormant: {names}"
    assert "Sara Ali" not in names, f"Sara should not be dormant: {names}"

    # new_contacts (max 10 messages)
    seg = Segment(id="new", name="New", filter_json=json.dumps({"max_messages": 10}))
    contacts = engine.resolve_segment(seg)
    names = {c["name"] for c in contacts}
    assert "New User" in names, f"New User (2 msgs) should be new: {names}"
    assert "Raj" in names, f"Raj (5 msgs) should be new: {names}"
    assert "Priya" not in names, f"Priya (35 msgs) should not be new: {names}"

    # repeat_contacts (20+ messages)
    seg = Segment(id="repeat", name="Repeat", filter_json=json.dumps({"min_messages": 20}))
    contacts = engine.resolve_segment(seg)
    names = {c["name"] for c in contacts}
    assert "Priya" in names, f"Priya (35 msgs) should be repeat: {names}"
    assert "Sara Ali" in names, f"Sara (80 msgs) should be repeat: {names}"
    assert "Raj" not in names, f"Raj (5 msgs) should not be repeat: {names}"

    # high_engagement (50+ messages)
    seg = Segment(id="high", name="High", filter_json=json.dumps({"min_messages": 50}))
    contacts = engine.resolve_segment(seg)
    names = {c["name"] for c in contacts}
    assert "Sara Ali" in names, f"Sara (80 msgs) should be high engagement: {names}"
    assert len(contacts) == 1, f"Only Sara should be high engagement, got {len(contacts)}"

    # Preview formatting
    seg = Segment(id="all_contacts", name="All Contacts", filter_json=json.dumps({"min_messages": 0}))
    preview = engine.get_segment_preview(seg, max_show=3)
    assert "All Contacts" in preview
    assert "5 contacts" in preview
    assert "... and 2 more" in preview

    # Empty segment
    seg = Segment(id="empty", name="Empty", filter_json=json.dumps({"min_messages": 9999}))
    contacts = engine.resolve_segment(seg)
    assert len(contacts) == 0
    preview = engine.get_segment_preview(seg)
    assert "0 contacts" in preview

    print("  PASS: segmentation engine")


async def test_template_rendering():
    """Test message template rendering with contact data."""
    _, store = make_temp_db()
    contact_store = make_mock_contact_store()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={},
    )

    # Basic template with {name}
    contact = {"name": "Priya Sharma", "full_jid": "123@s.whatsapp.net", "jid": "123", "messages": 10}
    rendered = engine._render_template("Hey {name}! Check this out.", contact)
    assert rendered == "Hey Priya Sharma! Check this out.", f"Got: {rendered}"

    # {first_name} template
    rendered = engine._render_template("Hi {first_name}, sale today!", contact)
    assert rendered == "Hi Priya, sale today!", f"Got: {rendered}"

    # No placeholder
    rendered = engine._render_template("Flash sale! 50% off everything!", contact)
    assert rendered == "Flash sale! 50% off everything!"

    # Both placeholders
    rendered = engine._render_template("{first_name} ({name}), we miss you!", contact)
    assert rendered == "Priya (Priya Sharma), we miss you!", f"Got: {rendered}"

    # Empty name
    contact_no_name = {"name": "", "full_jid": "123@s.whatsapp.net", "jid": "123", "messages": 0}
    rendered = engine._render_template("Hey {name}!", contact_no_name)
    assert rendered == "Hey !", f"Got: {rendered}"

    store.close()
    print("  PASS: template rendering")


async def test_campaign_lifecycle():
    """Test full campaign lifecycle: create -> start -> send -> complete."""
    _, store = make_temp_db()
    contact_store = make_mock_contact_store()
    channel = MockChannel()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={"admin_number": "9999999999"},
        channel=channel,
        contact_store=contact_store,
    )

    # Create campaign (without AI personalization for speed)
    campaign = await engine.create_campaign(
        name="Weekend Sale",
        message_template="Hey {first_name}! 30% off this weekend!",
        segment_id="all_contacts",
        personalize=False,
    )
    assert campaign.status == "draft"
    assert campaign.total_recipients == 5

    # Verify messages queued
    msgs = store.get_campaign_messages(campaign.id)
    assert len(msgs) == 5, f"Expected 5 queued messages, got {len(msgs)}"
    assert all(m.status == "pending" for m in msgs)

    # Check template rendering in messages
    # Note: segmentation uses saved_name if available, then push_name
    names = {m.recipient_name for m in msgs}
    assert "Priya" in names, f"Priya not found in names: {names}"
    assert "Sara Ali" in names, f"Sara Ali not found in names: {names}"

    # Start campaign with very fast rate limiting for test
    campaign.send_interval_s = 0.01  # Near-instant for testing
    campaign.batch_size = 100
    campaign.batch_pause_s = 0
    # Update the stored campaign's rate limits
    await store.update_campaign_status(campaign.id, campaign.status,
        send_interval_s=0.01, batch_size=100, batch_pause_s=0)

    result = await engine.start_campaign(campaign.id)
    assert "started" in result.lower() or "sending" in result.lower(), f"Unexpected start result: {result}"

    # Wait for send loop to complete
    # The task is running in the background
    task = engine._active_tasks.get(campaign.id)
    if task:
        await asyncio.wait_for(task, timeout=10.0)

    # Verify all messages sent (5 recipients + 1 admin notification = 6)
    assert len(channel.sent_messages) >= 5, f"Expected at least 5 sent, got {len(channel.sent_messages)}"

    # Check campaign completed
    final = store.get_campaign(campaign.id)
    assert final.status == "completed", f"Expected completed, got {final.status}"
    assert final.completed_at > 0

    # Separate recipient messages from admin notifications
    recipient_msgs = [m for m in channel.sent_messages if "9999999999" not in m[0]]
    admin_msgs = [m for m in channel.sent_messages if "9999999999" in m[0]]

    # Check recipient message content
    assert len(recipient_msgs) == 5, f"Expected 5 recipient msgs, got {len(recipient_msgs)}"
    for jid, text in recipient_msgs:
        assert jid.endswith("@s.whatsapp.net"), f"Invalid JID: {jid}"
        assert "30% off" in text, f"Template not rendered in: {text}"

    # Admin notification sent
    assert len(admin_msgs) >= 1, "Admin should receive completion notification"

    # Campaign report
    report = engine.get_campaign_report(campaign.id)
    assert "Weekend Sale" in report
    assert "COMPLETED" in report

    store.close()
    print("  PASS: campaign lifecycle")


async def test_campaign_pause_and_resume():
    """Test pausing and resuming a campaign."""
    _, store = make_temp_db()
    contact_store = make_mock_contact_store()
    channel = MockChannel()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={},
        channel=channel,
    )

    # Create campaign with slow rate to allow pausing
    campaign = await engine.create_campaign(
        name="Pausable Campaign",
        message_template="Test message for {name}",
        segment_id="all_contacts",
        personalize=False,
    )
    # Set very slow sending
    await store.update_campaign_status(campaign.id, "draft",
        send_interval_s=2.0, batch_size=100, batch_pause_s=0)

    # Start
    await engine.start_campaign(campaign.id)
    assert campaign.id in engine._active_tasks

    # Wait a tiny bit then pause
    await asyncio.sleep(0.1)
    result = await engine.pause_campaign(campaign.id)
    assert "paused" in result.lower(), f"Unexpected pause result: {result}"

    paused = store.get_campaign(campaign.id)
    assert paused.status == "paused"

    # Not all messages should be sent (rate was slow)
    stats = store.get_campaign_stats(campaign.id)
    pending = stats.get("pending", 0)
    assert pending > 0 or stats.get("sent", 0) > 0, "Some messages should exist"

    # Resume (start again)
    result = await engine.start_campaign(campaign.id)
    assert "started" in result.lower() or "sending" in result.lower()

    # Cancel
    result = await engine.cancel_campaign(campaign.id)
    assert "cancelled" in result.lower()

    cancelled = store.get_campaign(campaign.id)
    assert cancelled.status == "cancelled"

    store.close()
    print("  PASS: campaign pause/resume/cancel")


async def test_campaign_cancel():
    """Test cancelling a campaign."""
    _, store = make_temp_db()
    contact_store = make_mock_contact_store()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={},
    )

    campaign = await engine.create_campaign(
        name="Cancel Test",
        message_template="Test",
        segment_id="all_contacts",
        personalize=False,
    )

    # Cancel from draft
    result = await engine.cancel_campaign(campaign.id)
    assert "cancelled" in result.lower()

    cancelled = store.get_campaign(campaign.id)
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at > 0

    # Cancel nonexistent
    result = await engine.cancel_campaign("BC-NONEXIST")
    assert "not found" in result.lower()

    store.close()
    print("  PASS: campaign cancel")


async def test_reply_attribution_engine():
    """Test reply attribution through BroadcastEngine."""
    _, store = make_temp_db()
    contact_store = make_mock_contact_store()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={},
    )

    # Create and "send" a campaign
    campaign = await engine.create_campaign(
        name="Attribution Test",
        message_template="Hey {name}, check out our new menu!",
        segment_id="all_contacts",
        personalize=False,
    )

    # Manually mark some messages as sent
    msgs = store.get_campaign_messages(campaign.id)
    for m in msgs:
        await store.update_message_status(m.id, "sent", sent_at=time.time())
    await store.update_campaign_status(campaign.id, "completed")

    # Simulate reply from Priya
    attributed = await engine.check_reply_attribution(
        "1234567890@s.whatsapp.net",
        "Yes! I'd love to try the new menu"
    )
    assert attributed, "Reply should be attributed"

    # Check the reply was recorded
    c = store.get_campaign(campaign.id)
    assert c.replied_count == 1

    # Second reply from same person should not be attributed (already replied)
    attributed2 = await engine.check_reply_attribution(
        "1234567890@s.whatsapp.net",
        "When can I come?"
    )
    assert not attributed2, "Duplicate reply should not be attributed"

    # Reply from unknown contact
    attributed3 = await engine.check_reply_attribution(
        "unknown@s.whatsapp.net",
        "Hello"
    )
    assert not attributed3, "Unknown contact should not be attributed"

    store.close()
    print("  PASS: reply attribution engine")


async def test_scheduled_campaign_check():
    """Test heartbeat-triggered scheduled campaign checking."""
    _, store = make_temp_db()
    contact_store = make_mock_contact_store()
    channel = MockChannel()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={},
        channel=channel,
    )

    # Create a campaign scheduled 10s in the future, then manually set it to past
    campaign = await engine.create_campaign(
        name="Scheduled Test",
        message_template="Scheduled message for {name}",
        segment_id="all_contacts",
        personalize=False,
        scheduled_at=time.time() + 10,  # Future -> status = "scheduled"
    )
    assert campaign.status == "scheduled", f"Expected scheduled, got {campaign.status}"

    # Now move the scheduled_at to the past so the checker will fire it
    await store.update_campaign_status(campaign.id, "scheduled",
        scheduled_at=time.time() - 5,
        send_interval_s=0.01, batch_size=100, batch_pause_s=0)

    # Run scheduler check
    await engine.check_scheduled_campaigns()

    # Campaign should now be sending/completed
    updated = store.get_campaign(campaign.id)
    assert updated.status in ("sending", "completed"), \
        f"Scheduled campaign should be started, got {updated.status}"

    # Wait for send loop
    task = engine._active_tasks.get(campaign.id)
    if task:
        await asyncio.wait_for(task, timeout=10.0)

    final = store.get_campaign(campaign.id)
    assert final.status == "completed", f"Expected completed, got {final.status}"
    assert len(channel.sent_messages) >= 5

    # Future scheduled campaign should NOT start
    future = await engine.create_campaign(
        name="Future Campaign",
        message_template="Future msg",
        segment_id="all_contacts",
        personalize=False,
        scheduled_at=time.time() + 3600,  # 1 hour from now
    )
    assert future.status == "scheduled"

    await engine.check_scheduled_campaigns()
    still_scheduled = store.get_campaign(future.id)
    assert still_scheduled.status == "scheduled", "Future campaign should not start yet"

    store.close()
    print("  PASS: scheduled campaign check")


async def test_broadcast_integration_tools():
    """Test BroadcastIntegration LLM tool handler."""
    _, store = make_temp_db()
    contact_store = make_mock_contact_store()
    channel = MockChannel()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={},
        channel=channel,
    )

    integration = BroadcastIntegration(engine)

    # Tool definitions
    defs = integration.tool_definitions()
    assert len(defs) == 3, f"Expected 3 tool defs, got {len(defs)}"
    tool_names = {d["function"]["name"] for d in defs}
    assert "create_broadcast" in tool_names
    assert "campaign_status" in tool_names
    assert "list_segments" in tool_names

    # list_segments
    result = await integration.execute("list_segments", {})
    assert result.success, f"list_segments failed: {result.content}"
    assert "all_contacts" in result.content
    assert "active" in result.content

    # create_broadcast (start_now=False to avoid background task)
    result = await integration.execute("create_broadcast", {
        "name": "Tool Test Campaign",
        "message": "Hey {first_name}, check this out!",
        "segment": "all_contacts",
        "personalize": False,
        "start_now": False,
    })
    assert result.success, f"create_broadcast failed: {result.content}"
    assert "Tool Test Campaign" in result.content
    assert "5 contacts" in result.content

    # campaign_status (latest)
    result = await integration.execute("campaign_status", {})
    assert result.success, f"campaign_status failed: {result.content}"
    assert "Tool Test Campaign" in result.content

    # campaign_status with specific ID
    campaigns = store.list_campaigns()
    result = await integration.execute("campaign_status", {"campaign_id": campaigns[0].id})
    assert result.success

    # Missing required fields
    result = await integration.execute("create_broadcast", {"name": "", "message": ""})
    assert not result.success, "Should fail with missing name/message"

    # Unknown segment
    result = await integration.execute("create_broadcast", {
        "name": "Bad Campaign",
        "message": "test",
        "segment": "nonexistent_segment",
        "start_now": False,
    })
    assert not result.success, "Should fail with unknown segment"

    # Unknown tool
    result = await integration.execute("nonexistent_tool", {})
    assert not result.success

    store.close()
    print("  PASS: broadcast integration tools")


async def test_safety_limits():
    """Test safety limits: max recipients, max concurrent campaigns."""
    # Create a store with many contacts
    many_contacts = [
        {"jid": f"{i}@s.whatsapp.net", "push_name": f"User {i}",
         "saved_name": None, "messages": 10, "last_ts": time.time() - 3600}
        for i in range(1100)
    ]
    contact_store = make_mock_contact_store(many_contacts)
    _, store = make_temp_db()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={},
    )

    # Should fail with too many recipients
    try:
        await engine.create_campaign(
            name="Too Big",
            message_template="Test",
            segment_id="all_contacts",
            personalize=False,
        )
        assert False, "Should have raised ValueError for too many recipients"
    except ValueError as e:
        assert "Too many recipients" in str(e)
        print("  PASS: max recipients limit enforced")

    # Test max concurrent campaigns
    engine.MAX_CONCURRENT_CAMPAIGNS = 2
    engine._channel = MockChannel()

    # Create 2 active tasks (simulate)
    engine._active_tasks["fake1"] = asyncio.create_task(asyncio.sleep(100))
    engine._active_tasks["fake2"] = asyncio.create_task(asyncio.sleep(100))

    # Create a small campaign with fewer contacts
    small_contacts = [
        {"jid": f"{i}@s.whatsapp.net", "push_name": f"User {i}",
         "saved_name": None, "messages": 10, "last_ts": time.time() - 3600}
        for i in range(3)
    ]
    small_store = make_mock_contact_store(small_contacts)
    engine.segmentation = SegmentationEngine(small_store)

    campaign = await engine.create_campaign(
        name="Small Campaign",
        message_template="Test",
        segment_id="all_contacts",
        personalize=False,
    )
    result = await engine.start_campaign(campaign.id)
    assert "too many active" in result.lower(), f"Should reject due to concurrent limit: {result}"

    # Cleanup fake tasks
    for t in engine._active_tasks.values():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    engine._active_tasks.clear()

    store.close()
    print("  PASS: safety limits")


async def test_segment_dataclass():
    """Test Segment dataclass and filters property."""
    seg = Segment(
        id="test",
        name="Test",
        filter_json='{"min_messages": 5, "active_within_days": 7}',
    )
    assert seg.filters == {"min_messages": 5, "active_within_days": 7}

    # Invalid JSON
    seg_bad = Segment(id="bad", name="Bad", filter_json="not json")
    assert seg_bad.filters == {}

    # Empty filter_json
    seg_empty = Segment(id="e", name="E", filter_json="{}")
    assert seg_empty.filters == {}

    print("  PASS: segment dataclass")


async def test_campaign_report_formatting():
    """Test campaign report formatting with various states."""
    _, store = make_temp_db()
    contact_store = make_mock_contact_store()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={},
    )

    # Create and partially complete a campaign
    campaign = await engine.create_campaign(
        name="Report Test",
        message_template="Test",
        segment_id="all_contacts",
        personalize=True,
    )

    # Manually set some stats
    await store.update_campaign_status(
        campaign.id, "completed",
        started_at=time.time() - 120,
        completed_at=time.time(),
        sent_count=3,
        replied_count=1,
        failed_count=1,
    )

    report = engine.get_campaign_report(campaign.id)
    assert "Report Test" in report
    assert "COMPLETED" in report
    assert "AI Personalized: Yes" in report
    assert campaign.id in report

    # Nonexistent campaign
    report = engine.get_campaign_report("BC-NONEXIST")
    assert "not found" in report.lower()

    store.close()
    print("  PASS: campaign report formatting")


async def test_auto_segment_definitions():
    """Verify AUTO_SEGMENTS has correct structure."""
    assert len(AUTO_SEGMENTS) == 7, f"Expected 7 auto-segments, got {len(AUTO_SEGMENTS)}"

    required_ids = {"all_contacts", "active", "recent", "dormant",
                     "new_contacts", "repeat_contacts", "high_engagement"}
    assert set(AUTO_SEGMENTS.keys()) == required_ids

    for seg_id, seg_def in AUTO_SEGMENTS.items():
        assert "name" in seg_def, f"{seg_id} missing name"
        assert "description" in seg_def, f"{seg_id} missing description"
        assert "filters" in seg_def, f"{seg_id} missing filters"
        assert isinstance(seg_def["filters"], dict), f"{seg_id} filters not dict"

    print("  PASS: auto-segment definitions")


async def test_tool_definitions_format():
    """Verify broadcast tool definitions follow OpenAI format."""
    assert len(BROADCAST_TOOL_DEFINITIONS) == 3

    for td in BROADCAST_TOOL_DEFINITIONS:
        assert td["type"] == "function"
        func = td["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func
        params = func["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        assert "required" in params

    # Verify specific tools
    names = {td["function"]["name"] for td in BROADCAST_TOOL_DEFINITIONS}
    assert names == {"create_broadcast", "campaign_status", "list_segments"}

    # create_broadcast should require name and message
    create_def = next(td for td in BROADCAST_TOOL_DEFINITIONS
                       if td["function"]["name"] == "create_broadcast")
    assert "name" in create_def["function"]["parameters"]["required"]
    assert "message" in create_def["function"]["parameters"]["required"]

    print("  PASS: tool definitions format")


async def test_empty_segment_campaign():
    """Test creating a campaign with an empty segment."""
    # Contact store with no matching contacts for high engagement
    contacts = [
        {"jid": "123@s.whatsapp.net", "push_name": "User",
         "saved_name": None, "messages": 1, "last_ts": time.time()},
    ]
    contact_store = make_mock_contact_store(contacts)
    _, store = make_temp_db()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={},
    )

    # high_engagement requires 50+ messages, our user only has 1
    try:
        await engine.create_campaign(
            name="Empty Segment Test",
            message_template="Test",
            segment_id="high_engagement",
            personalize=False,
        )
        assert False, "Should raise ValueError for empty segment"
    except ValueError as e:
        assert "no matching contacts" in str(e).lower()

    store.close()
    print("  PASS: empty segment campaign")


async def test_factory_function():
    """Test create_broadcast_engine factory."""
    with patch("src.config_manager.get_config_dir") as mock_dir:
        tmp_dir = Path(tempfile.mkdtemp())
        mock_dir.return_value = tmp_dir

        contact_store = make_mock_contact_store()
        engine, store = create_broadcast_engine(
            config={"admin_number": "123"},
            contact_store=contact_store,
        )

        assert isinstance(engine, BroadcastEngine)
        assert isinstance(store, CampaignStore)
        assert engine.store is store
        assert engine.segmentation is not None
        assert engine.config == {"admin_number": "123"}

        # Cleanup
        store.close()
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("  PASS: factory function")


async def test_start_without_channel():
    """Test starting campaign without a WhatsApp channel."""
    _, store = make_temp_db()
    contact_store = make_mock_contact_store()

    engine = BroadcastEngine(
        store=store,
        segmentation=SegmentationEngine(contact_store),
        config={},
        channel=None,  # No channel
    )

    campaign = await engine.create_campaign(
        name="No Channel Test",
        message_template="Test",
        segment_id="all_contacts",
        personalize=False,
    )

    result = await engine.start_campaign(campaign.id)
    assert "not available" in result.lower(), f"Should reject without channel: {result}"

    store.close()
    print("  PASS: start without channel")


async def test_concurrent_campaign_counter():
    """Test atomic counter increments under concurrent access."""
    _, store = make_temp_db()
    campaign = await store.create_campaign(Campaign(
        name="Counter Test", message_template="Test", segment_id="all_contacts",
    ))

    # Concurrently increment
    tasks = [
        store.increment_campaign_counter(campaign.id, "sent_count", 1)
        for _ in range(50)
    ]
    await asyncio.gather(*tasks)

    final = store.get_campaign(campaign.id)
    assert final.sent_count == 50, f"Expected 50, got {final.sent_count}"

    store.close()
    print("  PASS: concurrent counter increments")


# ── Runner ──

async def run_all_tests():
    """Run all broadcast tests."""
    print("\n" + "=" * 60)
    print("BROADCAST ENGINE - END-TO-END TESTS")
    print("=" * 60 + "\n")

    tests = [
        ("Segment dataclass", test_segment_dataclass),
        ("Auto-segment definitions", test_auto_segment_definitions),
        ("Tool definitions format", test_tool_definitions_format),
        ("Campaign store CRUD", test_campaign_store_crud),
        ("Campaign messages", test_campaign_messages),
        ("Segments CRUD", test_segments),
        ("Reply attribution store", test_reply_attribution),
        ("Segmentation engine", test_segmentation_engine),
        ("Template rendering", test_template_rendering),
        ("Campaign lifecycle", test_campaign_lifecycle),
        ("Campaign pause/resume", test_campaign_pause_and_resume),
        ("Campaign cancel", test_campaign_cancel),
        ("Reply attribution engine", test_reply_attribution_engine),
        ("Scheduled campaign check", test_scheduled_campaign_check),
        ("Broadcast integration tools", test_broadcast_integration_tools),
        ("Safety limits", test_safety_limits),
        ("Campaign report formatting", test_campaign_report_formatting),
        ("Empty segment campaign", test_empty_segment_campaign),
        ("Factory function", test_factory_function),
        ("Start without channel", test_start_without_channel),
        ("Concurrent counter", test_concurrent_campaign_counter),
    ]

    passed = 0
    failed = 0
    errors = []

    for name, test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, e))
            print(f"  FAIL: {name}")
            import traceback
            traceback.print_exc()
            print()

    print("\n" + "-" * 60)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {type(err).__name__}: {err}")
    print("=" * 60 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
