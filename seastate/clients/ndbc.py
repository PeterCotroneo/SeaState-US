"""NOAA National Data Buoy Center (NDBC) client.

Keyless public feeds, verified live 2026-09-14:
  - Active stations : https://www.ndbc.noaa.gov/activestations.xml  (1353 stations)
  - Realtime obs    : https://www.ndbc.noaa.gov/data/realtime2/{id}.txt

The realtime feed is fixed-width text with 'MM' as the missing-value sentinel.
"""

import re

from ._http import fetch_bytes

ACTIVE_STATIONS = "https://www.ndbc.noaa.gov/activestations.xml"
REALTIME = "https://www.ndbc.noaa.gov/data/realtime2/{id}.txt"

MISSING = "MM"

# activestations.xml is a flat list of self-closing <station .../> elements.
# Parse attributes directly rather than via an XML parser (avoids XXE surface).
_STATION_RE = re.compile(r"<station\b([^>]*?)/?>", re.IGNORECASE)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _get_text(url):
    return fetch_bytes(url).decode("utf-8", errors="replace")


def list_stations(require_met=False):
    """Return [{id, lat, lon, name, type, met, currents, waterquality, dart}].

    Set require_met=True to keep only stations reporting meteorology.
    """
    text = _get_text(ACTIVE_STATIONS)
    out = []
    for match in _STATION_RE.finditer(text):
        attrs = dict(_ATTR_RE.findall(match.group(1)))
        if not attrs.get("id"):
            continue
        rec = {k: attrs.get(k) for k in
               ("id", "lat", "lon", "name", "type", "met",
                "currents", "waterquality", "dart")}
        if require_met and rec["met"] != "y":
            continue
        out.append(rec)
    return out


# Column order per the NDBC realtime2 standard-meteorological header:
#   YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS PTDY TIDE
FIELDS = [
    "year", "month", "day", "hour", "minute", "wind_dir_deg",
    "wind_speed_ms", "gust_ms", "wave_height_m", "dom_period_s",
    "avg_period_s", "mean_wave_dir_deg", "pressure_hpa", "air_temp_c",
    "water_temp_c", "dewpoint_c", "visibility_nmi", "pressure_tend_hpa",
    "tide_ft",
]


def _parse_row(parts):
    """One whitespace-split data row -> dict, with 'MM' -> None and a 'time' string."""
    row = {k: (None if v == MISSING else v) for k, v in zip(FIELDS, parts)}
    row["time"] = "{year}-{month}-{day} {hour}:{minute}".format(**row)  # UTC
    return row


def _iter_rows(station_id):
    text = _get_text(REALTIME.format(id=station_id))
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < len(FIELDS):
            continue
        yield _parse_row(parts)


def latest_observation(station_id):
    """Most recent standard-meteorological row as a dict (newest row is first)."""
    for row in _iter_rows(station_id):
        return row
    return None


def observations(station_id, begin=None, end=None):
    """All rows for a buoy, optionally filtered to a date window.

    begin/end are 'YYYYMMDD' strings (inclusive). The realtime feed spans only
    the most recent ~45 days; windows older than that come back empty.
    """
    rows = list(_iter_rows(station_id))
    if begin or end:
        def key(r):
            return "{year}{month}{day}".format(**r)
        if begin:
            rows = [r for r in rows if key(r) >= begin]
        if end:
            rows = [r for r in rows if key(r) <= end]
    return rows
