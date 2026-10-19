"""Official warnings from the recorder's envelopes (plan step 7, D4).

PAGASA and MeteoAlarm publish CAP 1.2 XML; NWS publishes GeoJSON carrying the CAP fields.
Each becomes one `OfficialWarning` per CAP `info` (MeteoAlarm repeats `info` per language: the
English one is kept). Area codes are what a trip's warning area is matched on: PAGASA's
SAME/PSGC geocodes, NWS UGC marine zones, MeteoAlarm EMMA_IDs (feeds-matrix.md).
Feed content is untrusted: XML goes through defusedxml, and nothing here is executed.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException, ElementTree

Source = Literal["pagasa", "nws", "meteoalarm"]
CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"
# Recorder source names (recorder/record.py) and the warning source each feeds.
RECORDER_SOURCES: dict[str, Source] = {
    "pagasa": "pagasa",
    "nws-marine": "nws",
    "meteoalarm-gr": "meteoalarm",
    "meteoalarm-hr": "meteoalarm",
    "meteoalarm-mt": "meteoalarm",
}
AREA_CODES: dict[Source, tuple[str, ...]] = {
    "pagasa": ("SAME", "PSGC"),
    "nws": ("UGC",),
    "meteoalarm": ("EMMA_ID",),
}


@dataclass(frozen=True)
class OfficialWarning:
    source: Source
    identifier: str
    sent: datetime
    onset: datetime
    expires: datetime
    event: str
    severity: str
    msg_type: str
    headline: str
    area_desc: str
    area_codes: tuple[str, ...]
    url: str
    # MeteoAlarm's awareness type and level; empty for the other sources.
    parameters: dict[str, str] = field(default_factory=dict)


class UnreadableWarning(ValueError):
    """A recorded document that is not the CAP or GeoJSON shape this module reads."""


def _time(value: str | None) -> datetime:
    if not value:
        raise UnreadableWarning("missing time")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise UnreadableWarning("time without offset")
    return parsed


def _text(element: Element, tag: str) -> str:
    found = element.find(f"{CAP}{tag}")
    return (found.text or "").strip() if found is not None else ""


def _english_infos(alert: Element) -> list[Element]:
    infos = alert.findall(f"{CAP}info")
    english = [i for i in infos if _text(i, "language").lower().startswith("en")]
    return english or infos[:1]


def parse_cap(body: str, source: Source, url: str) -> list[OfficialWarning]:
    try:
        alert = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise UnreadableWarning("not XML") from error
    except DefusedXmlException as error:  # entities, DTDs: refused, never expanded
        raise UnreadableWarning("XML with entities or a DTD") from error
    if alert.tag != f"{CAP}alert":
        raise UnreadableWarning("not a CAP 1.2 alert")
    sent = _time(_text(alert, "sent"))
    warnings = []
    for info in _english_infos(alert):
        codes: list[str] = []
        descriptions: list[str] = []
        for area in info.findall(f"{CAP}area"):
            descriptions.append(_text(area, "areaDesc"))
            for geocode in area.findall(f"{CAP}geocode"):
                if _text(geocode, "valueName") in AREA_CODES[source]:
                    codes.append(_text(geocode, "value"))
        parameters = {
            _text(p, "valueName"): _text(p, "value") for p in info.findall(f"{CAP}parameter")
        }
        warnings.append(
            OfficialWarning(
                source=source,
                identifier=_text(alert, "identifier"),
                sent=sent,
                onset=_time(
                    _text(info, "onset") or _text(info, "effective") or _text(alert, "sent")
                ),
                expires=_time(_text(info, "expires")),
                event=_text(info, "event"),
                severity=_text(info, "severity"),
                msg_type=_text(alert, "msgType"),
                headline=_text(info, "headline"),
                area_desc="; ".join(d for d in descriptions if d),
                area_codes=tuple(dict.fromkeys(codes)),
                url=url,
                parameters={k: v for k, v in parameters.items() if k.startswith("awareness_")},
            )
        )
    return warnings


def _nws_feature(feature: dict[str, Any]) -> OfficialWarning:
    p = feature["properties"]
    return OfficialWarning(
        source="nws",
        identifier=str(p["id"]),
        sent=_time(p.get("sent")),
        onset=_time(p.get("onset") or p.get("effective") or p.get("sent")),
        expires=_time(p.get("ends") or p.get("expires")),
        event=str(p.get("event", "")),
        severity=str(p.get("severity", "")),
        msg_type=str(p.get("messageType", "")),
        headline=str(p.get("headline") or ""),
        area_desc=str(p.get("areaDesc", "")),
        area_codes=tuple(p.get("geocode", {}).get("UGC", [])),
        url=str(feature.get("id") or p.get("@id") or ""),
    )


def parse_nws(body: str) -> list[OfficialWarning]:
    try:
        features = json.loads(body)["features"]
        return [_nws_feature(f) for f in features]
    except (ValueError, KeyError, TypeError) as error:
        raise UnreadableWarning("not an NWS alerts collection") from error


def from_envelope(envelope: dict[str, Any]) -> list[OfficialWarning]:
    """The warnings in one recorder envelope; an index of links, an error or a source this
    module does not read gives none."""
    source = RECORDER_SOURCES.get(str(envelope.get("source")))
    if source is None or envelope.get("http_status") != 200:
        return []
    if envelope.get("body_encoding", "utf-8") != "utf-8":
        return []
    body, url = str(envelope["body"]), str(envelope["url"])
    if source == "nws":
        return parse_nws(body)
    if envelope.get("kind") != "document":
        return []  # PAGASA and MeteoAlarm indexes only link to the CAP documents
    return parse_cap(body, source, url)
