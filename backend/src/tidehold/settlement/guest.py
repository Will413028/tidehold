"""What a guest's own link shows of the operator's cancellations (plan step 9, R12): the
cancellation, the official warnings in force at its decision, the clause behind the outcome and
this guest's outcome. Read from the proposal's stored facts, as visible at its decision time.
Only this guest's lines: no other guest's name or amount, and no internal ids."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.rows import DictRow

from tidehold.platform.db import Conn
from tidehold.trips import store as trips

CAUSE = {"weather": "for weather", "safety": "for safety", "other": "for another reason"}
DECIDING = "The operator is deciding what this means for your payments."


@dataclass(frozen=True)
class Outcome:
    purpose: str
    kind: str
    amount_minor: int
    credit_expiry_months: int | None
    clauses: list[str]


@dataclass(frozen=True)
class Settlement:
    state: str
    days: list[int]
    whole_trip: bool
    cause: str
    recorded_at: datetime
    warnings: list[dict[str, Any]]
    outcomes: list[Outcome]
    explanation: str


def _warnings(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The warnings in force at the decision, once each per timeline: a warning still in force
    live and its replay are both shown, each with its own label."""
    seen: dict[tuple[str, bool], dict[str, Any]] = {}
    for clause in facts["clauses"]:
        for warning in clause["matched"]:
            seen.setdefault((warning["identifier"], warning["replayed"]), warning)
    return list(seen.values())


def _outcomes(proposal: DictRow, guest_id: str) -> list[Outcome]:
    if proposal["state"] not in ("accepted", "executed") or proposal["choice"] is None:
        return []
    clauses = proposal["facts"]["clauses"]
    outcomes = []
    for purpose in ("deposit", "balance"):
        option = proposal["parts"][purpose][proposal["choice"][purpose]]
        line = next((x for x in option["lines"] if x["guest_id"] == guest_id), None)
        if line is None:  # invited after the decision
            continue
        outcomes.append(
            Outcome(
                purpose=purpose,
                kind=line["kind"],
                amount_minor=line["amount_minor"],
                credit_expiry_months=line["credit_expiry_months"],
                clauses=[clauses[i]["quote"] for i in option.get("rules", [])],
            )
        )
    return outcomes


def _what(days: Sequence[int], *, whole_trip: bool) -> str:
    if whole_trip:
        return "the whole trip"
    if len(days) == 1:
        return f"day {days[0]}"
    return f"days {days[0]} to {days[-1]}"


def _explanation(
    days: Sequence[int],
    facts: Mapping[str, Any],
    warnings: Sequence[Mapping[str, Any]],
    *,
    decided: bool,
) -> str:
    """The narrative only: what the operator cancelled and which warnings were in force. Amounts
    are shown from the stored lines (`outcomes`), never from this text, which step 11's model
    draft may replace (the model never supplies amounts)."""
    cancellation = facts["cancellation"]
    what = _what(days, whole_trip=cancellation["whole_trip"])
    parts = [f"The operator cancelled {what} {CAUSE[cancellation['cause']]}."]
    # a warning still in force live and its replay are one event to the guest
    names = list(dict.fromkeys(f"{w['event']} ({w['source'].upper()})" for w in warnings))
    if names:
        parts.append(f"{', '.join(names)} {'was' if len(names) == 1 else 'were'} in force.")
    if not decided:
        parts.append(DECIDING)
    return " ".join(parts)


async def view(conn: Conn, token: str) -> tuple[list[Settlement], list[DictRow]]:
    """The guest's settlements (a rejected one is withdrawn, so not shown) and its credits."""
    guest = await trips.guest_by_token(conn, token)
    rows = await (
        await conn.execute(
            "select * from proposals where trip_id = %s and kind = 'settlement'"
            " and state in ('asked', 'accepted', 'executed') order by created_at, id",
            (guest["trip_id"],),
        )
    ).fetchall()
    settlements = []
    for row in rows:
        facts, cancellation = row["facts"], row["facts"]["cancellation"]
        days = cancellation["covered_days"]
        warnings = _warnings(facts)
        outcomes = _outcomes(row, str(guest["id"]))
        decided = row["state"] != "asked"
        settlements.append(
            Settlement(
                state=row["state"],
                days=days,
                whole_trip=cancellation["whole_trip"],
                cause=cancellation["cause"],
                recorded_at=datetime.fromisoformat(cancellation["recorded_at"]),
                warnings=warnings,
                outcomes=outcomes,
                explanation=_explanation(days, facts, warnings, decided=decided),
            )
        )
    credits = await (
        await conn.execute(
            "select purpose, amount_minor, expires_at from credits where guest_id = %s"
            " order by created_at, id",
            (guest["id"],),
        )
    ).fetchall()
    return settlements, credits
