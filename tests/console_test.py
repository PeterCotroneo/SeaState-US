"""SeaState in-QGIS test.

Run inside QGIS: Plugins -> Python Console -> Show Editor -> open this file ->
Run (or paste the whole thing). It pulls live NOAA data, builds the real
QgsVectorLayers, checks them, adds them to the project, and verifies each
yields a plottable time series.

No GUI extent needed — it uses the Nantucket station (8449130) and a nearby buoy.
"""

import sys

REPO = "/Users/pete/Dev/SeaState"
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from seastate.clients import coops, ndbc
from seastate import layers, plot
from qgis.core import QgsProject

results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))
    print(("PASS" if ok else "FAIL"), "-", label, detail)


NANTUCKET = {"id": "8449130", "name": "Nantucket Island", "lat": "41.285", "lng": "-70.0967"}

# --- CO-OPS water level (last 2 days) ---
begin, end = "20260912", "20260914"
rows = coops.water_level(NANTUCKET["id"], begin, end)
check("water_level fetch", len(rows) > 0, f"({len(rows)} readings)")
wl = layers.build_water_level_layer(rows, NANTUCKET)
check("water_level layer valid", wl.isValid())
check("water_level features", wl.featureCount() > 0, f"({wl.featureCount()})")
check("water_level plottable", len(plot.layer_series_list(wl)) > 0)
QgsProject.instance().addMapLayer(wl)

# --- CO-OPS predictions ---
preds = coops.predictions(NANTUCKET["id"], begin, end)
check("predictions fetch", len(preds) > 0, f"({len(preds)} hi/lo)")
pl = layers.build_predictions_layer(preds, NANTUCKET)
check("predictions layer valid", pl.isValid())
check("predictions plottable", len(plot.layer_series_list(pl)) > 0)
QgsProject.instance().addMapLayer(pl)

# --- NDBC buoy (44008, off Nantucket) — temporal time series over the window ---
buoys = {b["id"]: b for b in ndbc.list_stations()}
b = buoys.get("44008")
check("NDBC station present", b is not None)
if b:
    rows = ndbc.observations("44008", begin, end)
    check("NDBC windowed fetch", len(rows) > 0, f"({len(rows)} readings)")
    records = [{"station_id": b["id"], "name": b["name"],
                "lat": b["lat"], "lon": b["lon"], **r} for r in rows]
    nl = layers.build_ndbc_timeseries_layer(records)
    check("NDBC layer valid", nl.isValid())
    check("NDBC features == readings", nl.featureCount() == len(rows), f"({nl.featureCount()})")
    check("NDBC plottable", len(plot.layer_series_list(nl)) > 0)
    QgsProject.instance().addMapLayer(nl)

passed = sum(1 for _, ok, _ in results if ok)
print(f"\n=== {passed}/{len(results)} checks passed ===")
