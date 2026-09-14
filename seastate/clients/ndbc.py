"""NOAA National Data Buoy Center (NDBC) client.

Keyless public feeds, verified live 2026-09-14:
  - Active stations : https://www.ndbc.noaa.gov/activestations.xml  (1353 stations)
  - Realtime obs    : https://www.ndbc.noaa.gov/data/realtime2/{id}.txt

The realtime feed is fixed-width text with 'MM' as the missing-value sentinel.
"""

import xml.etree.ElementTree as ET
from urllib.request import urlopen

ACTIVE_STATIONS = "https://www.ndbc.noaa.gov/activestations.xml"
REALTIME = "https://www.ndbc.noaa.gov/data/realtime2/{id}.txt"

MISSING = "MM"


def _get_text(url):
    with urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def list_stations(require_met=False):
    """Return [{id, lat, lon, name, type, met, currents, waterquality, dart}].

    Set require_met=True to keep only stations reporting meteorology.
    """
    root = ET.fromstring(_get_text(ACTIVE_STATIONS))
    out = []
    for s in root.findall("station"):
        rec = {
            "id": s.get("id"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "name": s.get("name"),
            "type": s.get("type"),
            "met": s.get("met"),
            "currents": s.get("currents"),
            "waterquality": s.get("waterquality"),
            "dart": s.get("dart"),
        }
        if require_met and rec["met"] != "y":
            continue
        out.append(rec)
    return out


def latest_observation(station_id):
    """Parse the most recent standard-meteorological row into a dict.

    Column order (per NDBC realtime2 header):
      YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS PTDY TIDE
    'MM' values become None.
    """
    text = _get_text(REALTIME.format(id=station_id))
    fields = [
        "year", "month", "day", "hour", "minute", "wind_dir_deg",
        "wind_speed_ms", "gust_ms", "wave_height_m", "dom_period_s",
        "avg_period_s", "mean_wave_dir_deg", "pressure_hpa", "air_temp_c",
        "water_temp_c", "dewpoint_c", "visibility_nmi", "pressure_tend_hpa",
        "tide_ft",
    ]
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < len(fields):
            continue
        row = dict(zip(fields, parts))
        return {k: (None if v == MISSING else v) for k, v in row.items()}
    return None
