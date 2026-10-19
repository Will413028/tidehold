import json
from pathlib import Path

from tests.integration.fake_paypal import FakePayPal
from tidehold.bootstrap import sandbox
from tidehold.platform.db import Conn


async def test_the_smoke_runs_the_three_money_flows(conn: Conn) -> None:
    report = await sandbox.smoke(conn, FakePayPal().client())

    assert {name: (r["outcome"], r["state"]) for name, r in report.items()} == {
        "deposit_capture_then_refund": ("succeeded", "captured"),
        "balance_hold_then_partial_final_capture": ("succeeded", "captured"),
        "balance_hold_then_void": ("succeeded", "voided"),
    }
    assert report["deposit_capture_then_refund"]["refunded_minor"] == 1500
    assert report["balance_hold_then_partial_final_capture"]["captured_minor"] == 2000


async def test_the_reauthorization_check_keeps_its_state_between_days(tmp_path: Path) -> None:
    fake = FakePayPal()
    state = tmp_path / "reauth.json"

    await sandbox.reauth_create(fake.client(), state)
    await sandbox.reauth_create(fake.client(), state)  # a rerun creates nothing new
    await sandbox.reauth_authorize(fake.client(), state)
    await sandbox.reauth_day4(fake.client(), state)

    saved = json.loads(state.read_text())
    assert len([c for c in fake.calls if c == "POST /v2/checkout/orders"]) == 3
    assert [(r["step"], r["result"]) for r in saved["day4"]] == [
        ("card_reauthorize-reauthorize", "accepted"),
        ("paypal_account-reauthorize", "accepted"),
        ("card_capture_only-capture", "accepted"),
    ]
