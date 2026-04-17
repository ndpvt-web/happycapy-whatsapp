"""Booking module — constraint-based meeting coordination.

Architecture principle: the LLM cannot take a wrong action because the
wrong action does not exist in its tool surface at that moment.

State machine enforces all valid transitions. Tools are gated by state.
Context is aggregated programmatically. The proactive engine fires
independently of the LLM conversation loop.
"""
from .engine import BookingEngine
from .models import BookingRecord, BookingState, BookingIntent

__all__ = ["BookingEngine", "BookingRecord", "BookingState", "BookingIntent"]
