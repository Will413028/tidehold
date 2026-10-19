import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from tidehold.warnings.cap import UnreadableWarning, from_envelope, parse_cap

FIXTURES = Path(__file__).parent.parent / "fixtures" / "warnings"


def _envelope(prefix: str) -> dict[str, Any]:
    [path] = FIXTURES.glob(f"{prefix}*.json.gz")
    envelope: dict[str, Any] = json.loads(gzip.decompress(path.read_bytes()))
    return envelope


def test_a_recorded_pagasa_cap_document_is_read() -> None:
    [warning] = from_envelope(_envelope("pagasa-20261018T175149"))

    assert warning.source == "pagasa"
    assert warning.identifier == "c21d1c01-5cd6-4f30-ac89-5cda7d3f2c2d"
    assert warning.event == "General Flood Advisory (Final)"
    assert warning.sent.isoformat() == "2026-10-15T05:18:07+08:00"
    assert warning.expires.isoformat() == "2026-10-15T17:18:07+08:00"
    assert warning.area_codes
    assert all(code.isdigit() for code in warning.area_codes)
    assert warning.url.endswith(".cap")


def test_a_failed_fetch_carries_no_warning() -> None:
    assert from_envelope(_envelope("pagasa-20261018T175224")) == []


def test_a_recorded_nws_marine_collection_is_read() -> None:
    warnings = from_envelope(_envelope("nws-marine"))

    assert len(warnings) == 259
    assert {"Gale Warning", "Small Craft Advisory"} <= {w.event for w in warnings}
    assert all(w.area_codes and w.onset.tzinfo and w.expires > w.onset for w in warnings)


METEOALARM = """<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>2.49.0.0.191.0.HR.TEST.1</identifier><sender>test</sender>
  <sent>2026-10-17T00:01:01+02:00</sent><status>Actual</status><msgType>Alert</msgType>
  <scope>Public</scope>
  <info><language>hr-HR</language><category>Met</category><event>Upozorenje na vjetar</event>
    <urgency>Future</urgency><severity>Moderate</severity><certainty>Likely</certainty>
    <onset>2026-10-17T00:01:01+02:00</onset><expires>2026-10-17T04:00:00+02:00</expires>
    <area><areaDesc>Srednja Dalmacija</areaDesc>
      <geocode><valueName>EMMA_ID</valueName><value>HR805</value></geocode></area></info>
  <info><language>en-GB</language><category>Met</category><event>Yellow wind warning</event>
    <urgency>Future</urgency><severity>Moderate</severity><certainty>Likely</certainty>
    <onset>2026-10-17T00:01:01+02:00</onset><expires>2026-10-17T04:00:00+02:00</expires>
    <headline>Yellow wind warning</headline>
    <parameter><valueName>awareness_level</valueName><value>2; yellow; Moderate</value></parameter>
    <parameter><valueName>awareness_type</valueName><value>1; Wind</value></parameter>
    <area><areaDesc>Middle Dalmatia</areaDesc>
      <geocode><valueName>EMMA_ID</valueName><value>HR805</value></geocode>
      <geocode><valueName>NUTS3</valueName><value>HR035</value></geocode></area></info>
</alert>"""


def test_meteoalarm_keeps_the_english_info_and_its_awareness_level() -> None:
    [warning] = parse_cap(METEOALARM, "meteoalarm", "https://feeds.meteoalarm.org/x")

    assert warning.event == "Yellow wind warning"
    assert warning.area_codes == ("HR805",)
    assert warning.parameters == {
        "awareness_level": "2; yellow; Moderate",
        "awareness_type": "1; Wind",
    }


BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>&lol2;</identifier></alert>"""


@pytest.mark.parametrize("body", [BOMB, "<html/>", "not xml at all"])
def test_untrusted_xml_that_is_not_a_plain_cap_alert_is_refused(body: str) -> None:
    with pytest.raises(UnreadableWarning):
        parse_cap(body, "pagasa", "https://example.org/x.cap")
