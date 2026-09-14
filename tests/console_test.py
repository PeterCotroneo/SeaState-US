"""SeaState in-QGIS test.

Run inside QGIS: Plugins -> Python Console -> Show Editor -> open this file ->
Run (or paste the whole thing). It pulls live NOAA data, builds the real
QgsVectorLayers, checks them, and adds them to the current project so you can
see the points and scrub the Temporal Controller.

No GUI extent needed — it uses the Nantucket station (8449130) and a nearby buoy.
"""

import sys

REPO = "/Users/pete/Dev/SeaState"
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from seastate.clients import coops, ndbc
from seastate import layers
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
check("water_level temporal active", wl.temporalProperties().isActive())
QgsProject.instance().addMapLayer(wl)

# --- CO-OPS predictions ---
preds = coops.predictions(NANTUCKET["id"], begin, end)
check("predictions fetch", len(preds) > 0, f"({len(preds)} hi/lo)")
pl = layers.build_predictions_layer(preds, NANTUCKET)
check("predictions layer valid", pl.isValid())
check("predictions temporal active", pl.temporalProperties().isActive())
QgsProject.instance().addMapLayer(pl)

# --- NDBC buoy (44008, off Nantucket) ---
buoys = {b["id"]: b for b in ndbc.list_stations()}
b = buoys.get("44008")
check("NDBC station present", b is not None)
if b:
    obs = ndbc.latest_observation("44008")
    rec = {**b, **(obs or {})}
    nl = layers.build_ndbc_layer([rec])
    check("NDBC layer valid", nl.isValid())
    check("NDBC features", nl.featureCount() == 1, f"({nl.featureCount()})")
    QgsProject.instance().addMapLayer(nl)

passed = sum(1 for _, ok, _ in results if ok)
print(f"\n=== {passed}/{len(results)} checks passed ===")
