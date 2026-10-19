import itertools

import pytest

from tidehold.platform.errors import Conflict
from tidehold.settlement.plan import Payment, settle_payment


def test_settlement_conserves_money_for_every_small_case() -> None:
    for amount, refunded_share in itertools.product((1, 999, 10000, 30005), (0, 3)):
        refunded = amount // refunded_share if refunded_share else 0
        held = Payment("authorized", amount)
        paid = Payment("captured", amount, captured_minor=amount, refunded_minor=refunded)
        for owed in sorted({0, 1, amount // 3, amount // 2, amount - 1, amount}):
            hold = settle_payment(held, owed)
            assert hold.amount_minor + hold.returned_minor == amount
            assert hold.owed_minor == owed
            refund = settle_payment(paid, owed)
            assert refund.owed_minor + refund.returned_minor == amount - refunded
            assert refund.owed_minor == min(owed, amount - refunded)


@pytest.mark.parametrize(
    ("owed", "action"), [(0, "void"), (100, "partial_capture"), (300, "capture")]
)
def test_a_hold_is_released_or_captured_once(owed: int, action: str) -> None:
    assert settle_payment(Payment("authorized", 300), owed).action == action


def test_a_hold_never_captures_more_than_it_holds() -> None:
    with pytest.raises(Conflict, match="EXCEEDS_AUTHORIZATION"):
        settle_payment(Payment("authorized", 300), 301)


def test_a_payment_not_yet_placed_cannot_be_settled() -> None:
    with pytest.raises(Conflict, match="NOT_PLACED"):
        settle_payment(Payment("pending", 300), 0)
