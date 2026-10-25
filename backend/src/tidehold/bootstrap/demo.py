"""The demo operator seed (plan step 9 unit 8, `step9-design.md` "Unit 8").

`POST /api/demo-operator` fills an empty workspace with a fictional operator, "Northline Dive
Co.", whose wording is Pure Dive Resort's published text, quoted with its source and access
date (`quoted_from`). Its one rule is the bench's gold rule for that text with what the operator
settles written into its fields: the "local authorities" are PAGASA, any tropical-cyclone
wind signal counts, the operator chooses between refund and credit, credit expires after 12
months. It is published as written by the operator (no compilation, so no rulings) through the
same checks as any version; the model's part is the live compile of the same wording on
`/policies`. No model and no PayPal call here: the web pays the guests through the guest route.

The trip starts today in Manila on Batanes, the province every recorded Super Typhoon Rosal
warning covers, so a replayed recorded warning (aligned to now, D12) is in force on day 1.
"""

from datetime import datetime
from importlib.resources import files
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from tidehold.identity.sessions import current_workspace
from tidehold.platform.db import Conn, connection
from tidehold.platform.errors import Conflict
from tidehold.policy import versions
from tidehold.policy.schema import Remedy, Rule, WarningCondition
from tidehold.trips import store
from tidehold.warnings import catalogue

router = APIRouter(prefix="/api")

Workspace = Annotated[UUID, Depends(current_workspace)]
Db = Annotated[Conn, Depends(connection)]

# `bench/fixtures/pure-dive.json` `wording`, verbatim (a test keeps the two equal); the file
# ends with one newline the fixture does not have.
WORDING = (
    files("tidehold.bootstrap")
    .joinpath("demo_wording.txt")
    .read_text(encoding="utf-8")
    .removesuffix("\n")
)
QUOTED_FROM = versions.QuotedFrom(
    name="Pure Dive Resort", url="https://purediveresort.com/deep-diving/", accessed="2026-10-19"
)
CLAUSE = (
    "We do not cancel our dive trips trips because of the weather (rain, clouds etc.) unless"
    " there has been an official storm warning issued by the local authorities. In this case"
    " your trip will be either rescheduled or refunded."
)
RULE = Rule(
    product=None,
    causes=["weather"],
    warning=WarningCondition(
        named_as="local authorities",
        source="pagasa",
        event_values=list(catalogue.PAGASA_SIGNALS),
        quote="an official storm warning issued by the local authorities",
    ),
    scope="per_trip",
    applies_to="both",
    remedies=[
        Remedy(kind="refund", percent=100, deductions=[], credit_expiry_months=None),
        Remedy(kind="credit", percent=100, deductions=[], credit_expiry_months=12),
    ],
    chooser="operator",
    quote=CLAUSE,
)

TIMEZONE = "Asia/Manila"
BATANES = "020900000"  # PSGC province code, as PAGASA's CAP geocodes carry it
GUESTS = ("Guest A", "Guest B")
DEPOSIT_MINOR = 5000
BALANCE_MINOR = 30000


def _trip(version_id: UUID, today: datetime) -> store.NewTrip:
    per_guest = DEPOSIT_MINOR + BALANCE_MINOR
    return store.NewTrip(
        name="Northline Dive Co.: Batanes weekend",
        starts_on=today.date(),
        days=2,
        # off Basco, Batan Island: where the Open-Meteo forecast is read
        site_lat=20.45,
        site_lon=121.95,
        warning_source="pagasa",
        warning_areas=[BATANES],
        deposit_minor=DEPOSIT_MINOR,
        balance_minor=BALANCE_MINOR,
        policy_version_id=version_id,
        timezone=TIMEZONE,
        # every settlement asks anyway: the rule has two remedies (`evaluate.decide`)
        mandate_guest_minor=per_guest,
        mandate_trip_minor=per_guest * len(GUESTS),
    )


async def seed(conn: Conn, workspace_id: UUID, now: datetime) -> UUID:
    """The demo operator's version, trip and unpaid guests, in an empty workspace only."""
    checked = versions.check(WORDING, [RULE], [], [])
    async with conn.transaction():
        # under the workspace row's lock, so two seeds cannot both find it empty
        await conn.execute("select 1 from workspaces where id = %s for update", (workspace_id,))
        row = await (
            await conn.execute(
                "select exists (select 1 from policy_versions where workspace_id = %(w)s)"
                " or exists (select 1 from trips where workspace_id = %(w)s) as used",
                {"w": workspace_id},
            )
        ).fetchone()
        if row is not None and row["used"]:
            raise Conflict("WORKSPACE_NOT_EMPTY")
        version_id = await versions.publish(conn, workspace_id, checked, None, QUOTED_FROM)
        trip_id = await store.create_trip(
            conn, workspace_id, _trip(version_id, now.astimezone(ZoneInfo(TIMEZONE)))
        )
        for name in GUESTS:
            await store.invite(conn, workspace_id, trip_id, name)
    return trip_id


class SeededGuest(BaseModel):
    name: str
    token: str


class Seeded(BaseModel):
    trip_id: UUID
    # the web pays each one through the guest route
    guests: list[SeededGuest]


@router.post("/demo-operator", status_code=status.HTTP_201_CREATED)
async def create_demo_operator(workspace_id: Workspace, conn: Db) -> Seeded:
    trip_id = await seed(conn, workspace_id, datetime.now(ZoneInfo(TIMEZONE)))
    guests = await store.guests(conn, trip_id)
    return Seeded(
        trip_id=trip_id, guests=[SeededGuest(name=g["name"], token=g["token"]) for g in guests]
    )
