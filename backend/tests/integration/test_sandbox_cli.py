from tests.integration.fake_paypal import FakePayPal
from tidehold.bootstrap import sandbox
from tidehold.platform.db import Conn


async def test_the_smoke_runs_the_money_flows(conn: Conn) -> None:
    report = await sandbox.smoke(conn, FakePayPal().client())

    assert {name: (r["outcomes"], r["state"]) for name, r in report.items()} == {
        "deposit_capture_then_two_refunds": (["succeeded", "succeeded"], "captured"),
        "balance_capture_then_refund": (["succeeded"], "captured"),
    }
    assert report["deposit_capture_then_two_refunds"]["refunded_minor"] == 2500
    assert report["balance_capture_then_refund"]["refunded_minor"] == 1000
    # the order read back lists every refund by the invoice id tidehold sent
    listed = [
        (r["listed"], r["status"])
        for name in ("deposit_capture_then_two_refunds", "balance_capture_then_refund")
        for r in report[name]["refunds_in_order"]
    ]
    assert listed == [(True, "COMPLETED")] * 3
