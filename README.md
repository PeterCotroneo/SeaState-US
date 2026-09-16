# SeaState US

A QGIS plugin that loads U.S. NOAA coastal data — tide-gauge water levels, tide
predictions, and offshore buoy readings — as point layers on the map, and charts
any of them as a time series.

Coverage is U.S. coasts, Great Lakes and territories. Uses keyless public NOAA
APIs — no account or token required.

## What it loads

Three layer types, each fetched for the current map view and date range:

| Layer | What it is | Units | Source |
| --- | --- | --- | --- |
| **Water levels** | *Measured* water height at tide-gauge stations, every 6 minutes | feet, MLLW datum | NOAA CO-OPS |
| **Tide predictions** | *Predicted* daily high/low tides (not measured) | feet, MLLW datum | NOAA CO-OPS |
| **Ocean buoys** | Offshore buoy readings: wind, waves, air/water temperature, pressure | mixed (m, m/s, °C, hPa) | NOAA NDBC |

Water levels are **observations**; predictions are a **forecast/astronomical
calculation** — they are different products and are kept as separate layers. The
NDBC live buoy feed spans only the most recent ~45 days.

## Using it

1. Add a basemap (Browser → XYZ Tiles → OpenStreetMap) and zoom to a U.S. coast.
2. Open the panel: **Plugins → SeaState US → SeaState US**.
3. **Tick a layer** to load it for the current view and date range; untick to
   remove it. Use **Refresh for current view** after moving the map or changing
   dates.
4. **Plot over time** opens a chart (one per loaded layer) of value vs. time.

Ticking loads immediately (a network fetch tied to the map view), so a wide
extent or long date range can be slow. NOAA's 6-minute water-level product is
capped at 31 days per request; longer ranges are fetched in chunks automatically.

## Install (development)

Tested on **QGIS 4.2** (macOS). Matplotlib (bundled with QGIS 4.2) is required
for the plot.

Symlink or copy the `seastate/` directory into your QGIS profile plugins folder,
e.g. on macOS:

```
ln -s /path/to/SeaState-US/seastate \
  "$HOME/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins/seastate"
```

Then enable **SeaState US** in Plugins → Manage and Install Plugins (tick "Show
also experimental plugins" in that dialog's Settings tab, as this is flagged
experimental).

## Data sources

| Source | Feed | Auth |
| --- | --- | --- |
| CO-OPS tides & water levels | `api.tidesandcurrents.noaa.gov` (data + metadata APIs) | none |
| NDBC buoys | `ndbc.noaa.gov` active-stations XML + realtime2 text | none |

## Layout

```
seastate/
  metadata.txt          QGIS plugin metadata
  __init__.py           classFactory entry point
  seastate_plugin.py    plugin class + dock panel + load/plot logic
  layers.py             builds the QGIS point layers
  plot.py               Matplotlib time-series charts
  clients/
    coops.py            NOAA CO-OPS client
    ndbc.py             NOAA NDBC client
tests/
  console_test.py       run inside the QGIS Python Console
```

## Status

Working early release. Known rough edges: fetching runs on the main thread (a
large pull briefly freezes the UI); no CSV export yet; the toolbar button has no
icon.

## License

GPL-2.0-or-later.
