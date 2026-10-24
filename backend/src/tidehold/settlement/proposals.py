"""Cancellations and the proposals code computes from them (plan step 8a, D16, D17).

Settlement starts only here, from the operator's recorded cancellation: the bound policy is
evaluated against the evidence visible at that moment, with no model call, and the result is
one proposal. Inside the trip's mandate it is accepted at once; otherwise it asks, and the
operator chooses an option or rejects. Asks of other kinds (a warning without a cancellation,
a balance unpaid at its due date, a refused or undecided PayPal
command) are proposals too, one per trip,
kind and subject. Design: docs/plans/2026-10-19-tidehold-hackathon/step8a-design.md, and
step8b-design.md for the money each accepted settlement moves when it is decided.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from tidehold.money import commands
from tidehold.platform import due_actions as due
from tidehold.platform.db import Conn, insert_returning
from tidehold.platform.errors import Conflict, Invalid, NotFound
from tidehold.policy import versions
from tidehold.policy.schema import Rule
from tidehold.settlement import matching, moves
from tidehold.settlement.evaluate import (
    PURPOSES,
    Cancelled,
    GuestMoney,
    Mandate,
    Reason,
    RuleIn,
    Span,
    decide,
    evaluate,
    ruled_in_text,
)
from tidehold.trips import store as trips

Kind = Literal["settlement", "warning", "unpaid_balance", "money_refused", "command_open"]
Cause = Literal["weather", "safety", "other"]

REASON_TEXT: dict[str, str] = {
    "NO_RULE": "no rule of the policy covers this cancellation",
    "CONFLICTING_RULES": "rules of the policy disagree on the outcome",
    "REMEDY_CHOICE": "the policy offers a choice of remedies",
    "WARNING_NOT_MATCHED": "the rule requires an official warning and none matched",
    "RULED_IN_TEXT": "a ruling answered in free text covers this clause",
    "FIXED_DEDUCTION": "the rule deducts a fixed amount whose currency the policy does not state",
    "MANDATE_GUEST_CAP": "a guest's return exceeds the trip's per-guest mandate",
    "MANDATE_TRIP_CAP": "the trip's returns exceed its mandate",
}


@dataclass(frozen=True)
class NewCancellation:
    scope: Literal["day", "rest"]
    trip_day: int
    cause: Cause


async def _trip_locked(conn: Conn, workspace_id: UUID, trip_id: UUID) -> DictRow:
    row = await (
        await conn.execute(
            "select * from trips where id = %s and workspace_id = %s for update",
            (trip_id, workspace_id),
        )
    ).fetchone()
    if row is None:
        raise NotFound
    return row


def _span(stored: Sequence[int] | None) -> Span | None:
    return None if not stored else (stored[0], stored[1])


def _rules_in(version: DictRow, product: str | None) -> list[tuple[Rule, bool]]:
    """The rules of the trip's product, each with whether a free-text ruling covers it."""
    # every occurrence of a ruling's quote (step 5b); a ruling stored before `spans` has one
    ruling_spans = [_span(at) for r in version["rulings"] for at in r.get("spans", [r["span"]])]
    out: list[tuple[Rule, bool]] = []
    for item in version["rules"]:
        rule = Rule.model_validate(item["rule"])
        if product is not None and rule.product not in (None, product):
            continue
        span = (item["span"][0], item["span"][1])
        out.append((rule, ruled_in_text(span, _span(item["warning_span"]), ruling_spans)))
    return out


async def _returned(conn: Conn, trip_id: UUID) -> dict[str, dict[str, int]]:
    """What the trip's accepted settlements return, per guest and purpose."""
    rows = await (
        await conn.execute(
            "select parts, choice from proposals where trip_id = %s and kind = 'settlement'"
            " and state in ('accepted', 'executed')",
            (trip_id,),
        )
    ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        for purpose, index in row["choice"].items():
            for line in row["parts"][purpose][index]["lines"]:
                if line["kind"] != "none":
                    guest = out.setdefault(line["guest_id"], {})
                    guest[purpose] = guest.get(purpose, 0) + line["amount_minor"]
    return out


def _money(guest: DictRow, returned: Mapping[str, int]) -> GuestMoney:
    """What the guest owes on each payment, paid yet or not: a refund line on a payment not yet
    paid lowers what is due on it, and later settlements net only through `returned` (step 8b
    design, "The plan's text does not add up")."""
    return GuestMoney(str(guest["id"]), guest["deposit_minor"], guest["balance_minor"], returned)


@dataclass(frozen=True)
class NewAsk:
    kind: Kind
    subject: str
    question: str
    facts: Mapping[str, Any]


async def ask(conn: Conn, trip: DictRow, new: NewAsk) -> UUID | None:
    """Open an ask once per trip, kind and subject; None when it already exists. The template
    question is stored first, and a model draft is requested after it."""
    row = await (
        await conn.execute(
            "insert into proposals (workspace_id, trip_id, kind, subject, state, question, facts)"
            " values (%s, %s, %s, %s, 'asked', %s, %s)"
            " on conflict (trip_id, kind, subject) do nothing returning id",
            (
                trip["workspace_id"],
                trip["id"],
                new.kind,
                new.subject,
                new.question,
                Jsonb(dict(new.facts)),
            ),
        )
    ).fetchone()
    if row is None:
        return None
    await due.schedule(conn, "draft_ask", row["id"], due_at=None)
    proposal_id: UUID = row["id"]
    return proposal_id


async def _accept(
    conn: Conn,
    proposal: DictRow,
    choice: Mapping[str, int],
    by: Literal["mandate", "operator"],
    now: datetime,
) -> None:
    """Accept a settlement and move what needs no PayPal call at once, day and whole trip
    alike: a refund of a payment not yet paid lowers what is due on it (its unsent checkouts
    close), and a credit on a captured payment is written. The rest (refunds through PayPal, a
    payment at PayPal, a credit waiting for its payment) goes to `settle_proposal`."""
    accepted = await (
        await conn.execute(
            "update proposals set state = 'accepted', choice = %s, decided_by = %s,"
            " decided_at = %s where id = %s returning *",
            (Jsonb(dict(choice)), by, now, proposal["id"]),
        )
    ).fetchone()
    if accepted is None:
        raise NotFound
    final = [
        await moves.run_without_paypal(conn, accepted, line)
        for line in moves.lines(accepted, await trips.guests(conn, accepted["trip_id"]))
    ]
    if all(final):
        await conn.execute(
            "update proposals set state = 'executed' where id = %s", (accepted["id"],)
        )
    else:
        await due.schedule(conn, "settle_proposal", accepted["id"], due_at=now)


def _what(cancellation: NewCancellation, starts_on: date) -> str:
    if cancellation.scope == "day":
        return f"trip day {cancellation.trip_day}"
    if cancellation.trip_day == 1:
        return f"the whole trip from {starts_on.isoformat()}"
    return f"the rest of the trip from day {cancellation.trip_day}"


def _question(cancellation: NewCancellation, starts_on: date, reasons: Sequence[Reason]) -> str:
    head = f"Cancellation of {_what(cancellation, starts_on)} ({cancellation.cause})"
    if not reasons:
        return f"{head}: settled by the policy within the trip's mandate."
    why = "; ".join(REASON_TEXT[r] for r in reasons)
    return f"{head}: {why}. Choose an outcome for the deposit and the balance, or reject."


async def _covered(conn: Conn, trip: DictRow, new: NewCancellation) -> list[int]:
    if not 1 <= new.trip_day <= trip["days"]:
        raise Invalid("TRIP_DAY_OUT_OF_RANGE")
    rows = await (
        await conn.execute(
            "select scope, trip_day from cancellations where trip_id = %s and withdrawn_at is null",
            (trip["id"],),
        )
    ).fetchall()
    if any(r["scope"] == "rest" for r in rows):
        raise Conflict("TRIP_ALREADY_CANCELLED")
    cancelled = {r["trip_day"] for r in rows}
    days = [new.trip_day] if new.scope == "day" else list(range(new.trip_day, trip["days"] + 1))
    covered = [d for d in days if d not in cancelled]
    if not covered or (new.scope == "day" and new.trip_day in cancelled):
        raise Conflict("DAY_ALREADY_CANCELLED")
    if new.scope == "rest":
        open_ask = await (
            await conn.execute(
                "select 1 from proposals where trip_id = %s and kind = 'settlement'"
                " and state = 'asked'",
                (trip["id"],),
            )
        ).fetchone()
        if open_ask is not None:
            # the rest of the trip is netted against accepted settlements only
            raise Conflict("SETTLEMENT_ASK_OPEN")
    return covered


async def _rules(
    conn: Conn, trip: DictRow, covered: Sequence[int], now: datetime
) -> tuple[list[RuleIn], list[dict[str, Any]]]:
    """The product's rules with their warning conditions matched, and the clauses shown."""
    version = await versions.version(conn, trip["workspace_id"], trip["policy_version_id"])
    in_force = await matching.in_force(conn, trip, covered, now)
    rules: list[RuleIn] = []
    clauses: list[dict[str, Any]] = []
    for rule, in_text in _rules_in(version, trip["policy_product"]):
        found = matching.matching(in_force, rule.warning.event_values) if rule.warning else []
        rules.append(RuleIn(rule, in_text, (bool(found) if rule.warning else None)))
        clauses.append(
            {
                "quote": rule.quote,
                "warning": rule.warning.model_dump() if rule.warning else None,
                "matched": [matching.summary(row) for row in found],
            }
        )
    return rules, clauses


async def record_cancellation(
    conn: Conn, workspace_id: UUID, trip_id: UUID, new: NewCancellation, now: datetime
) -> UUID:
    """Record the operator's cancellation and its settlement proposal; returns the proposal.
    Cancellations of one trip are serialised on the trip row, so netting and the mandate see
    every earlier acceptance."""
    async with conn.transaction():
        trip = await _trip_locked(conn, workspace_id, trip_id)
        covered = await _covered(conn, trip, new)
        rules, clauses = await _rules(conn, trip, covered, now)
        returned = await _returned(conn, trip_id)
        guests = [
            _money(g, returned.get(str(g["id"]), {}))
            for g in await trips.guests(conn, trip_id)
            if g["released_at"] is None  # a released guest owes nothing more
        ]
        cancelled = Cancelled(new.scope, new.cause, new.trip_day, len(covered), trip["days"])
        evaluation = evaluate(rules, cancelled, guests)
        mandate = Mandate(
            trip["mandate_guest_minor"],
            trip["mandate_trip_minor"],
            {g: sum(p.values()) for g, p in returned.items()},
            sum(sum(p.values()) for p in returned.values()),
        )
        decision = decide(evaluation, mandate)
        cancellation = await insert_returning(
            conn,
            "insert into cancellations (workspace_id, trip_id, scope, trip_day, cause,"
            " recorded_at) values (%s, %s, %s, %s, %s, %s) returning id",
            (workspace_id, trip_id, new.scope, covered[0], new.cause, now),
        )
        if cancelled.whole_trip:
            await trips.cancel_whole_trip(conn, trip_id, now)
        facts = {
            "cancellation": {
                "scope": new.scope,
                "trip_day": new.trip_day,
                "covered_days": covered,
                "whole_trip": cancelled.whole_trip,
                "cause": new.cause,
                "recorded_at": now.isoformat(),
            },
            "clauses": clauses,
        }
        proposal = await insert_returning(
            conn,
            "insert into proposals (workspace_id, trip_id, kind, subject, cancellation_id,"
            " state, reasons, parts, question, facts)"
            " values (%s, %s, 'settlement', %s, %s, 'asked', %s, %s, %s, %s) returning *",
            (
                workspace_id,
                trip_id,
                f"cancellation:{cancellation['id']}",
                cancellation["id"],
                list(decision.reasons),
                Jsonb(evaluation.parts),
                _question(new, trip["starts_on"], decision.reasons),
                Jsonb(facts),
            ),
        )
        await conn.execute(
            "update proposals set state = 'answered', decided_at = %s"
            " where trip_id = %s and kind = 'warning' and state = 'asked'"
            " and split_part(subject, ':', 2)::int = any(%s)",
            (now, trip_id, covered),
        )
        if decision.choice is not None:
            await _accept(conn, proposal, decision.choice, "mandate", now)
        else:
            await due.schedule(conn, "draft_ask", proposal["id"], due_at=None)
    proposal_id: UUID = proposal["id"]
    return proposal_id


async def proposals(conn: Conn, workspace_id: UUID, trip_id: UUID) -> list[DictRow]:
    await trips.trip(conn, workspace_id, trip_id)
    return await (
        await conn.execute(
            "select * from proposals where trip_id = %s order by created_at, id", (trip_id,)
        )
    ).fetchall()


@dataclass(frozen=True)
class Ref:
    """A proposal as an operator route names it: inside a trip of a workspace."""

    workspace_id: UUID
    trip_id: UUID
    proposal_id: UUID


async def proposal(conn: Conn, ref: Ref) -> DictRow:
    row = await (
        await conn.execute(
            "select * from proposals where id = %s and trip_id = %s and workspace_id = %s",
            (ref.proposal_id, ref.trip_id, ref.workspace_id),
        )
    ).fetchone()
    if row is None:
        raise NotFound
    return row


async def _withdraw(conn: Conn, settlement: DictRow, now: datetime) -> None:
    """A rejected settlement withdraws its cancellation: no money moved, and the operator can
    record the cancellation again (a whole trip reopens to guests and payments)."""
    row = await (
        await conn.execute(
            "update cancellations set withdrawn_at = %s where id = %s returning scope, trip_day",
            (now, settlement["cancellation_id"]),
        )
    ).fetchone()
    if row is not None and row["scope"] == "rest":
        await trips.reopen(conn, settlement["trip_id"])
    # the warning asks this cancellation answered are open again
    await conn.execute(
        "update proposals set state = 'asked', decided_at = null"
        " where trip_id = %s and kind = 'warning' and state = 'answered'"
        " and split_part(subject, ':', 2)::int = any(%s)",
        (settlement["trip_id"], settlement["facts"]["cancellation"]["covered_days"]),
    )


@dataclass(frozen=True)
class OperatorDecision:
    """Exactly one of: accept these option indexes, reject a settlement, dismiss an ask (for
    an unpaid balance: wait), release the guest of an unpaid balance, or resolve an open
    command from what the operator found at PayPal."""

    accept: Mapping[str, int] | None = None
    reject: bool = False
    dismiss: bool = False
    release: bool = False
    resolve: Literal["found", "not_found"] | None = None


def _subject_id(ask: DictRow) -> UUID:
    return UUID(ask["subject"].split(":", 1)[1])


async def _release(conn: Conn, ask: DictRow, now: datetime) -> None:
    """Release the guest of an unpaid balance (D21): it owes nothing more, so what is left of
    its balance is waived under this ask; no money moves. Its deposit stays with the operator
    under its own terms, except what a settlement already decided for it, which still moves.
    Later settlements leave the guest out, and the accepted ones still waiting on its balance
    run again to finish without it."""
    guest = await trips.guest(conn, _subject_id(ask))
    if guest["released_at"] is not None:
        raise Conflict("GUEST_ALREADY_RELEASED")
    due_minor = guest["balance_minor"] - guest["balance_waived_minor"]
    if guest["balance_state"] == "captured":
        raise Conflict("BALANCE_PAID")
    if due_minor > 0:
        match await commands.waive(
            conn, guest["balance_id"], amount_minor=due_minor, proposal_id=ask["id"]
        ):
            case "captured":
                raise Conflict("BALANCE_PAID")
            case "in_flight":
                raise Conflict("BALANCE_PAYMENT_IN_FLIGHT")
            case "waived" | "already":
                pass
    await conn.execute(
        "update guests set released_at = %s, released_by = %s where id = %s",
        (now, ask["id"], guest["id"]),
    )
    for row in await (
        await conn.execute(
            "select id from proposals where trip_id = %s and kind = 'settlement'"
            " and state = 'accepted'",
            (ask["trip_id"],),
        )
    ).fetchall():
        await due.wake(conn, "settle_proposal", row["id"], due_at=now)


async def _resolve(conn: Conn, ask: DictRow, *, found: bool, now: datetime) -> None:
    """Close a command no read could decide from what the operator found at PayPal; the
    settlement that sent it finishes from there (a refund not found asks as refused). A command
    a read settled since the ask opened needs no answer: the ask is answered as it stands."""
    try:
        command = await commands.resolve_by_operator(conn, _subject_id(ask), found=found)
    except Conflict as error:
        if error.code != "MONEY_COMMAND_NOT_OPEN":
            raise
        return
    if command["proposal_id"] is not None:
        await due.wake(conn, "settle_proposal", command["proposal_id"], due_at=now)


Answer = Literal["accept", "reject", "dismiss", "release", "resolve"]
# Which answers each kind of proposal takes: a settlement is accepted or rejected (it is never
# dismissed: the cancellation stands or is withdrawn); an unpaid balance is waited for
# (dismiss) or its guest released; an open command is resolved from what PayPal shows, never
# dismissed, which would leave it open for ever (step 8b design, review finding 2).
ANSWERS: dict[str, frozenset[Answer]] = {
    "settlement": frozenset({"accept", "reject"}),
    "warning": frozenset({"dismiss"}),
    "money_refused": frozenset({"dismiss"}),
    "unpaid_balance": frozenset({"dismiss", "release"}),
    "command_open": frozenset({"resolve"}),
}


STATE_AFTER: dict[Answer, str] = {
    "accept": "accepted",
    "reject": "rejected",
    "release": "answered",
    "resolve": "answered",
    "dismiss": "dismissed",
}


def _answer(decision: OperatorDecision) -> Answer:
    if decision.accept is not None:
        return "accept"
    if decision.resolve is not None:
        return "resolve"
    if decision.release:
        return "release"
    return "reject" if decision.reject else "dismiss"


def _choice(row: DictRow, accept: Mapping[str, int]) -> dict[str, int]:
    choice = dict(accept)
    if set(choice) != set(PURPOSES) or any(
        not 0 <= i < len(row["parts"][p]) for p, i in choice.items()
    ):
        raise Invalid("CHOICE_INVALID")
    return choice


async def decide_proposal(conn: Conn, ref: Ref, decision: OperatorDecision, now: datetime) -> None:
    async with conn.transaction():
        await _trip_locked(conn, ref.workspace_id, ref.trip_id)
        row = await proposal(conn, ref)
        if row["state"] != "asked":
            raise Conflict("PROPOSAL_ALREADY_DECIDED")
        answer = _answer(decision)
        if answer not in ANSWERS[row["kind"]]:
            raise Conflict(f"{row['kind'].upper()}_TAKES_NO_{answer.upper()}")
        match answer:
            case "accept":
                await _accept(conn, row, _choice(row, decision.accept or {}), "operator", now)
                return
            case "reject":
                await _withdraw(conn, row, now)
            case "release":
                await _release(conn, row, now)
            case "resolve":
                await _resolve(conn, row, found=decision.resolve == "found", now=now)
            case "dismiss":
                pass
        await conn.execute(
            "update proposals set state = %s, decided_by = 'operator', decided_at = %s"
            " where id = %s",
            (STATE_AFTER[answer], now, ref.proposal_id),
        )
