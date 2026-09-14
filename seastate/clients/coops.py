"""NOAA CO-OPS (Tides & Currents) client.

Keyless public APIs, verified live 2026-09-14:
  - Metadata API : https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/
  - Data API     : https://api.tidesandcurrents.noaa.gov/api/prod/datagetter

No account or token required.
"""

import json
from urllib.parse import urlencode
from urllib.request import urlopen

MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
DATAGETTER = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"


def _get_json(url):
    with urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


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

    Returns rows of {t, v, s, f, q}: time, value, sigma, flags, quality.
    """
    params = {
        "station": station_id,
        "product": "water_level",
        "begin_date": begin_date,
        "end_date": end_date,
        "datum": datum,
        "units": units,
        "time_zone": time_zone,
        "format": "json",
    }
    data = _get_json(f"{DATAGETTER}?{urlencode(params)}")
    return data.get("data", [])


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
