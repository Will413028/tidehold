"""Which PayPal action settles a payment once code has computed what the guest owes.

Ported from dive-trip-agent@8065eba `modules/deposits/domain.py` (`settle_diver`). How much
is owed comes from the compiled policy (steps 5 and 8); this module only turns that amount
into a void, a partial or full capture, or a refund, and never moves money twice.
"""

from dataclasses import dataclass
from typing import Literal

from tidehold.platform.errors import Conflict

State = Literal["pending", "authorized", "captured", "voided"]
Action = Literal["void", "partial_capture", "capture", "refund", "none"]


@dataclass(frozen=True)
class Payment:
    state: State
    amount_minor: int  # what was authorized, or captured at booking
    captured_minor: int = 0
    refunded_minor: int = 0


@dataclass(frozen=True)
class Settlement:
    action: Action
    amount_minor: int  # captured or refunded by this action
    owed_minor: int  # what the guest ends up paying on this payment
    returned_minor: int  # released or refunded to the guest by this action


def settle_payment(payment: Payment, owed: int) -> Settlement:
    """Pick the PayPal action; never capture then refund when a void or a smaller capture
    would do."""
    if owed < 0:
        raise ValueError("owed must not be negative")
    match payment.state:
        case "pending":
            raise Conflict("PAYMENT_NOT_PLACED")
        case "voided":
            return Settlement("none", 0, 0, 0)
        case "authorized":
            held = payment.amount_minor
            if owed > held:
                raise Conflict("SETTLEMENT_EXCEEDS_AUTHORIZATION")
            action: Action = (
                "void" if owed == 0 else "partial_capture" if owed < held else "capture"
            )
            return Settlement(action, owed, owed, held - owed)
        case "captured":
            net = payment.captured_minor - payment.refunded_minor
            refund = max(net - owed, 0)
            return Settlement("refund" if refund else "none", refund, net - refund, refund)
