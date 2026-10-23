import json
from pathlib import Path

from tests.integration.fake_paypal import FakePayPal
from tidehold.bootstrap import sandbox
from tidehold.platform.db import Conn


async def test_the_smoke_runs_the_three_money_flows(conn: Conn) -> None:
    report = await sandbox.smoke(conn, FakePayPal().client())

    assert {name: (r["outcomes"], r["state"]) for name, r in report.items()} == {
        "deposit_capture_then_two_refunds": (["succeeded", "succeeded"], "captured"),
        "balance_capture_then_refund": (["succeeded"], "captured"),
        "balance_hold_then_partial_final_capture": (["succeeded"], "captured"),
        "balance_hold_then_void": (["succeeded"], "voided"),
    }
    assert report["deposit_capture_then_two_refunds"]["refunded_minor"] == 2500
    assert report["balance_capture_then_refund"]["refunded_minor"] == 1000
    assert report["balance_hold_then_partial_final_capture"]["captured_minor"] == 2000
    # the order read back lists every refund by the invoice id tidehold sent
    listed = [
        (r["listed"], r["status"])
        for name in ("deposit_capture_then_two_refunds", "balance_capture_then_refund")
        for r in report[name]["refunds_in_order"]
    ]
    assert listed == [(True, "COMPLETED")] * 3


async def test_the_reauthorization_check_keeps_its_state_between_days(tmp_path: Path) -> None:
    fake = FakePayPal()
    state = tmp_path / "reauth.json"

    await sandbox.reauth_create(fake.client(), state)
    await sandbox.reauth_create(fake.client(), state)  # a rerun creates nothing new
    await sandbox.reauth_authorize(fake.client(), state)
    await sandbox.reauth_day4(fake.client(), state)

    saved = json.loads(state.read_text())
    assert saved["paypal_account"]["approve_url"].endswith(saved["paypal_account"]["order_id"])
    assert len([c for c in fake.calls if c == "POST /v2/checkout/orders"]) == 3
    assert [(r["step"], r["result"]) for r in saved["day4"]] == [
        ("card_reauthorize-reauthorize", "accepted"),
        ("card_reauthorize-capture", "accepted"),
        ("paypal_account-reauthorize", "accepted"),
        ("paypal_account-capture", "accepted"),
        ("card_capture_only-capture", "accepted"),
    ]
    renewed = saved["card_reauthorize"]["reauthorized_id"]
    assert renewed != saved["card_reauthorize"]["authorization_id"]
    assert f"POST /v2/payments/authorizations/{renewed}/capture" in fake.calls


async def test_a_day4_step_left_unknown_is_resent_under_its_request_id(tmp_path: Path) -> None:
    fake = FakePayPal()
    state = tmp_path / "reauth.json"
    await sandbox.reauth_create(fake.client(), state)
    fake.time_out_first = {"reauthorize"}

    await sandbox.reauth_day4(fake.client(), state)
    first = json.loads(state.read_text())
    assert ("card_reauthorize-reauthorize", "unknown") in [
        (r["step"], r["result"]) for r in first["day4"]
    ]
    assert "reauthorized_id" not in first["card_reauthorize"]

    await sandbox.reauth_day4(fake.client(), state)
    saved = json.loads(state.read_text())
    assert saved["request_ids"].items() >= first["request_ids"].items()
    assert [(r["step"], r["result"]) for r in saved["day4"] if r["step"].startswith("card_")] == [
        ("card_reauthorize-reauthorize", "unknown"),
        ("card_capture_only-capture", "accepted"),
        ("card_reauthorize-reauthorize", "accepted"),
        ("card_reauthorize-capture", "accepted"),
    ]
    original = saved["card_reauthorize"]["authorization_id"]
    assert fake.calls.count(f"POST /v2/payments/authorizations/{original}/reauthorize") == 2
