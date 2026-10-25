"""The demo operator's data (plan step 9 unit 8)."""

import json
from pathlib import Path

from tidehold.bootstrap import demo

BENCH = Path(__file__).parents[3] / "bench" / "fixtures" / "pure-dive.json"


def test_the_demo_wording_is_the_quoted_operators_text_verbatim() -> None:
    fixture = json.loads(BENCH.read_text(encoding="utf-8"))

    assert fixture["wording"] == demo.WORDING
    assert (demo.QUOTED_FROM.url, demo.QUOTED_FROM.accessed) == (
        fixture["url"],
        fixture["accessed"],
    )
