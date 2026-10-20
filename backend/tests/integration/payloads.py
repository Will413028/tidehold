"""Request bodies shared by the HTTP tests."""

from datetime import date
from typing import Any

from httpx import AsyncClient, Response

WORDING = (
    "Cancellations by us: if we cancel a dive day because of the weather or sea conditions, "
    "you get a full refund for that day."
)
# A policy written by hand (the no-AI path): one rule, no warning condition, so it binds a
# trip of any warning source.
POLICY: dict[str, Any] = {
    "wording": WORDING,
    "rules": [
        {
            "product": None,
            "causes": ["weather"],
            "warning": None,
            "scope": "per_day",
            "applies_to": "both",
            "remedies": [
                {"kind": "refund", "percent": 100, "deductions": [], "credit_expiry_months": None}
            ],
            "chooser": None,
            "quote": "if we cancel a dive day because of the weather or sea conditions, "
            "you get a full refund for that day.",
        }
    ],
}

# A trip far enough out that its balance hold is not open yet (T-(29 - N) is in the future).
TRIP: dict[str, Any] = {
    "name": "Anilao weekend",
    "starts_on": "2026-12-05",
    "days": 3,
    "site_lat": 13.7565,
    "site_lon": 120.9137,
    "warning_source": "pagasa",
    "warning_areas": ["041000000"],
    "deposit_minor": 5000,
    "balance_minor": 30000,
    "timezone": "Asia/Manila",
    "mandate_guest_minor": 35000,
    "mandate_trip_minor": 200000,
}


def trip_starting(on: date, days: int = 3) -> dict[str, Any]:
    return {**TRIP, "starts_on": on.isoformat(), "days": days}


async def publish_policy(client: AsyncClient, policy: dict[str, Any] = POLICY) -> str:
    response = await client.post("/api/policy-versions", json=policy)
    assert response.status_code == 201, response.text
    version_id: str = response.json()["id"]
    return version_id


async def post_trip(client: AsyncClient, trip: dict[str, Any] = TRIP, **overrides: Any) -> Response:
    """Create a trip bound to a freshly published `POLICY` unless the body names a version."""
    body = {**trip, **overrides}
    if "policy_version_id" not in body:
        body["policy_version_id"] = await publish_policy(client)
    return await client.post("/api/trips", json=body)
