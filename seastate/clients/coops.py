"""NOAA CO-OPS (Tides & Currents) client.

Keyless public APIs, verified live 2026-09-14:
  - Metadata API : https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/
  - Data API     : https://api.tidesandcurrents.noaa.gov/api/prod/datagetter

No account or token required.
"""

import json
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen

MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
DATAGETTER = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"

# NOAA caps a single 6-minute water-level request at 31 days.
WATER_LEVEL_MAX_DAYS = 31


def _get_json(url):
    with urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _date_chunks(begin_date, end_date, max_days):
    """Yield (begin, end) YYYYMMDD sub-ranges no longer than max_days."""
    b = datetime.strptime(begin_date, "%Y%m%d")
    e = datetime.strptime(end_date, "%Y%m%d")
    cur = b
    while cur <= e:
        stop = min(cur + timedelta(days=max_days - 1), e)
        yield cur.strftime("%Y%m%d"), stop.strftime("%Y%m%d")
        cur = stop + timedelta(days=1)


def list_stations(station_type="waterlevels"):
    """Return [{id, name, lat, lng}, ...] for the current extent to filter."""
    url = f"{MDAPI}/stations.json?type={station_type}"
    data = _get_json(url)
    return [
        {"id": s["id"], "name": s["name"], "lat": s["lat"], "lng": s["lng"]}
        for s in data.get("stations", [])
    ]


def water_level(station_id, begin_date, end_date, datum="MLLW",
                units="english", time_zone="lst_ldt"):
    """Observed water level. Dates are YYYYMMDD strings.

    Ranges longer than NOAA's 31-day 6-minute limit are fetched in chunks and
    concatenated. Returns rows of {t, v, s, f, q}: time, value, sigma, flags,
    quality.
    """
    rows = []
    for b, e in _date_chunks(begin_date, end_date, WATER_LEVEL_MAX_DAYS):
        params = {
            "station": station_id,
            "product": "water_level",
            "begin_date": b,
            "end_date": e,
            "datum": datum,
            "units": units,
            "time_zone": time_zone,
            "format": "json",
        }
        data = _get_json(f"{DATAGETTER}?{urlencode(params)}")
        rows.extend(data.get("data", []))
    return rows


def predictions(station_id, begin_date, end_date, datum="MLLW",
                interval="hilo", units="english", time_zone="lst_ldt"):
    """Tide predictions. interval='hilo' gives high/low; returns {t, v, type}."""
    params = {
        "station": station_id,
        "product": "predictions",
        "begin_date": begin_date,
        "end_date": end_date,
        "datum": datum,
        "interval": interval,
        "units": units,
        "time_zone": time_zone,
        "format": "json",
    }
    data = _get_json(f"{DATAGETTER}?{urlencode(params)}")
    return data.get("predictions", [])
