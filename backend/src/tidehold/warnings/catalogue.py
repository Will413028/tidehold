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
- PAGASA: the event stem before any "(...)" status, as recorded. Only flood advisories have been
  recorded up to 2026-10-19; tropical-cyclone wind signals are added when one is recorded
  (feeds-matrix.md), not from guesswork.
"""

from collections.abc import Mapping

from tidehold.warnings.cap import OfficialWarning, Source

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


# Per source, groups of values: each ladder from least to most severe, then a group of the rest.
GROUPS: dict[Source, tuple[tuple[str, ...], ...]] = {
    "nws": (*NWS_LADDERS, NWS_OTHER),
    "meteoalarm": tuple(
        tuple(f"{kind}:{colour}" for colour in METEOALARM_COLOURS) for kind in METEOALARM_TYPES
    ),
    "pagasa": (PAGASA_EVENTS,),
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
            return event.split("(")[0].strip()
        case _:
            return event


def event_value(warning: OfficialWarning) -> str:
    """The catalogue value a recorded warning carries."""
    return value_of(warning.source, warning.event, warning.parameters)
