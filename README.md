# SeaState US

A QGIS plugin that loads U.S. NOAA coastal data — tide-gauge water levels, tide
predictions, and offshore buoy readings — as point layers on the map, and charts
any of them as a time series.

Uses keyless public NOAA APIs — no account or token required.

![SeaState US panel with NOAA buoys and stations loaded on the map](docs/screenshot-map.png)

Plotting a station's readings over time:

![Chatham water level plotted over the date range](docs/screenshot-plot.png)

## What it loads

Three layer types, each fetched for the current map view and date range:

| Layer | What it is | Units | Source |
| --- | --- | --- | --- |
| **Water levels** | *Measured* water height at tide-gauge stations, every 6 minutes | feet, MLLW datum | NOAA CO-OPS |
| **Tide predictions** | *Predicted* daily high/low tides (not measured) | feet, MLLW datum | NOAA CO-OPS |
| **Ocean buoys** | Offshore readings: wind, waves, air/water temperature, pressure | m, m/s, °C, hPa | NOAA NDBC |

Water levels are **observations**; predictions are an **astronomical
calculation** — different products, kept as separate layers. The NDBC live buoy
feed spans only the most recent ~45 days.

**Coverage:** U.S. tidal coastal stations and territories (Puerto Rico, Guam,
etc.). Great Lakes stations use a non-tidal datum (IGLD) and are **not supported
yet** — see [Limitations](#limitations).

## Using it

1. Add a basemap (Browser → XYZ Tiles → OpenStreetMap) and zoom to a U.S. coast.
2. Open the panel: **Plugins → SeaState US → SeaState US**.
3. **Tick a layer** to load it for the current view and date range; untick to
   remove it. Use **Refresh for current view** after moving the map or changing
   dates.
4. **Plot over time** charts the loaded data.

### What gets plotted

Each loaded station or buoy becomes its **own chart**, stacked vertically in one
scrollable window (they share the time axis). The value on each chart is:

- Water-level and prediction layers → water height (ft).
- Buoy layers → wave height (m), falling back to wind speed or water temperature
  when a buoy reports no waves.

So loading three tide gauges gives three separate water-level charts; loading a
buoy adds a wave-height chart below them. Multiple buoy measurements are not
combined on one axis — one metric per chart keeps units honest.

## Install

Tested on **QGIS 4.2** (macOS). Matplotlib — bundled with QGIS 4.2 — is required
for the plot.

Copy or symlink the `seastate/` directory into your QGIS profile plugins folder.
On macOS:

```bash
ln -s /path/to/SeaState-US/seastate \
  "$HOME/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins/seastate"
```

Then enable **SeaState US** in Plugins → Manage and Install Plugins (tick "Show
also experimental plugins" in that dialog's Settings tab).

## Data sources

| Source | Feed | Auth |
| --- | --- | --- |
| CO-OPS tides & water levels | `api.tidesandcurrents.noaa.gov` (data + metadata APIs) | none |
| NDBC buoys | `ndbc.noaa.gov` active-stations XML + `realtime2` text | none |

NOAA's 6-minute water-level product is capped at 31 days per request; longer
ranges are fetched in chunks automatically.

## Limitations

- **Fetching blocks the QGIS interface** until the request finishes. Start with a
  small map area and a short date range. Moving fetching into a background task
  is the top planned improvement.
- **Great Lakes stations are not supported** (they use the IGLD datum rather than
  the tidal MLLW datum this plugin requests).
- No CSV export yet; the toolbar button has no icon.

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

## License

GPL-2.0-or-later.
