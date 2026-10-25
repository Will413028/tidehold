"""The event values a warning condition may name, per source (D16).

A rule's warning condition is a closed set of these values, proposed by the model and confirmed
by the operator; settlement matches a warning by membership, never by reading its text. The
policy module validates rules against it (`policy` sits above `warnings`). A value outside the
catalogue is refused, and a warning whose value is not in the confirmed set does not match (the
operator is asked instead).

- NWS: the `event` of an alert, from the controlled list at https://api.weather.gov/alerts/types
  (read 2026-10-19), marine and coastal entries.
- MeteoAlarm: `<awareness type>:<colour>` from the `awareness_type` and `awareness_level`
  parameters (MeteoAlarm CAP profile; types as recorded, e.g. "1; Wind", levels 2-4).
- PAGASA: the event stem before any "(...)" status, as recorded; a Tropical Cyclone Warning adds
  its wind signal from the `Public Storm Warning Signal Level` parameter ("... : Signal #2").
  Signals #1 and #2 were recorded 2026-10-20 to 10-23 (Super Typhoon Rosal); #3 to #5 are the
  rest of PAGASA's five-level scheme and are assumed to carry the same parameter. A warning
  without a signal, or with one outside the scheme, has a value outside the catalogue.
"""

from collections.abc import Mapping

from tidehold.warnings.cap import PAGASA_SIGNAL, OfficialWarning, Source

# Each ladder lists one hazard from the least to the most severe; "X or stronger" in an
# operator's wording means X and everything after it on X's ladder.
NWS_LADDERS: tuple[tuple[str, ...], ...] = (
    ("Small Craft Advisory", "Gale Warning", "Storm Warning", "Hurricane Force Wind Warning"),
    ("Tropical Storm Warning", "Hurricane Warning"),
    ("Tropical Storm Warning", "Typhoon Warning"),
    ("High Surf Advisory", "High Surf Warning"),
    ("Hazardous Seas Watch", "Hazardous Seas Warning"),
    ("Storm Surge Watch", "Storm Surge Warning"),
    ("Tsunami Advisory", "Tsunami Warning"),
)
NWS_OTHER = ("Special Marine Warning", "Gale Watch", "Storm Watch", "Hurricane Force Wind Watch")

METEOALARM_TYPES = (
    "Wind",
    "Thunderstorm",
    "Fog",
    "Coastal event",
    "Rain",
    "Flood",
    "Rain-Flood",
)
METEOALARM_COLOURS = ("yellow", "orange", "red")

PAGASA_EVENTS = ("General Flood Advisory",)
PAGASA_CYCLONE = "Tropical Cyclone Warning"
PAGASA_SIGNALS = tuple(f"{PAGASA_CYCLONE}: Signal #{n}" for n in range(1, 6))


# Per source, groups of values: each ladder from least to most severe, then a group of the rest.
GROUPS: dict[Source, tuple[tuple[str, ...], ...]] = {
    "nws": (*NWS_LADDERS, NWS_OTHER),
    "meteoalarm": tuple(
        tuple(f"{kind}:{colour}" for colour in METEOALARM_COLOURS) for kind in METEOALARM_TYPES
    ),
    "pagasa": (PAGASA_SIGNALS, PAGASA_EVENTS),
}


def values(source: Source) -> frozenset[str]:
    return frozenset(v for group in GROUPS[source] for v in group)


def _after_number(value: str) -> str:
    """MeteoAlarm parameters read "1; Wind" and "2; yellow; Moderate"."""
    parts = [p.strip() for p in value.split(";")]
    return parts[1] if len(parts) > 1 else parts[0]


def value_of(source: str, event: str, parameters: Mapping[str, str]) -> str:
    """The catalogue value of a warning's stored fields (a recorded row or a parsed one)."""
    match source:
        case "meteoalarm":
            kind = _after_number(parameters.get("awareness_type", ""))
            colour = _after_number(parameters.get("awareness_level", "")).lower()
            return f"{kind}:{colour}"
        case "pagasa":
            stem = event.split("(")[0].strip()
            signal = parameters.get(PAGASA_SIGNAL)
            return f"{stem}: Signal #{signal}" if stem == PAGASA_CYCLONE and signal else stem
        case _:
            return event


def event_value(warning: OfficialWarning) -> str:
    """The catalogue value a recorded warning carries."""
    return value_of(warning.source, warning.event, warning.parameters)
