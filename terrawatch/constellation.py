"""Constellation status awareness (spec §4.4). The JSON file is user-editable."""
import json, os

PATH = os.environ.get("TW_CONSTELLATION_EVENTS",
                      os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "constellation_events.json"))


def events(sensor: str | None = None) -> list[dict]:
    doc = json.load(open(PATH))
    return [e for e in doc["events"] if sensor is None or e["sensor"] == sensor]


def spans_event(start: str, end: str, sensor: str | None = None) -> list[dict]:
    """Events whose window overlaps [start, end]. Empty list means no caveat."""
    s, e = start[:10], end[:10]
    return [ev for ev in events(sensor) if ev["start"] <= e and ev["end"] >= s]
