"""When a guest's balance falls due: the trip's `balance_due_days` before its first local day,
or at once for a guest invited later than that (D18 ADR, decision 1: "at once for later
bookings"; D20).

Every guest can pay the balance from its invitation; the due date only says when an unpaid
balance becomes the operator's question (step 8b's unpaid-balance ask reads it). The
invitation time is the database's clock (`guests.created_at`), not a due action's `now`.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def balance_due_at(trip: Mapping[str, Any], invited_at: datetime) -> datetime:
    """Local midnight, in the trip's time zone, `balance_due_days` before the first trip day;
    the guest's invitation when that is later. In UTC."""
    due_on = trip["starts_on"] - timedelta(days=trip["balance_due_days"])
    local = datetime.combine(due_on, time(), ZoneInfo(trip["timezone"]))
    return max(local, invited_at).astimezone(UTC)


# A guest invited after its due date gets a day to pay before the operator hears of it
# (developer, 2026-10-24; step 8b design, review finding 8).
UNPAID_GRACE = timedelta(hours=24)


def unpaid_ask_at(trip: Mapping[str, Any], invited_at: datetime) -> datetime:
    """When an unpaid balance becomes the operator's question: its due date, and for a guest
    invited near or after it, not before a day has passed since the invitation. In UTC."""
    return max(balance_due_at(trip, invited_at), (invited_at + UNPAID_GRACE).astimezone(UTC))
