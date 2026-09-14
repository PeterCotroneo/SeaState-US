# SeaState

A QGIS plugin that loads NOAA coastal observations — tides, water levels, tide
predictions, and NDBC buoy data — into QGIS as styled, time-aware point layers,
animated on the QGIS Temporal Controller, with CSV export.

Uses keyless public NOAA APIs. No account or token required.

## Why

QGIS has strong loaders for satellite imagery (Sentinel Hub, NASA Earthdata) but
no first-class way to pull *coastal observations* — the values behind the
station dots. Station locations are already reachable via NOAA's ArcGIS REST
service; SeaState fills the gap above that line: the observation and prediction
time series for the stations in your map extent, ready to style, animate, and
export.

## Data sources

| Source | Feed | Auth |
| --- | --- | --- |
| CO-OPS tides & water levels | `api.tidesandcurrents.noaa.gov` (data + metadata APIs) | none |
| NDBC buoys | `ndbc.noaa.gov` active-stations XML + realtime2 text | none |

## v1 scope

- CO-OPS station layer (302 water-level stations) with datum / flood-level attributes
- Observed water level and hi/lo tide predictions for a chosen date range
- NDBC buoy layer (1,353 active stations) filterable by capability (met / waves / currents / DART)
- Latest + recent buoy observations (wind, gust, wave height/period, pressure, temps)
- Layers wired to the QGIS Temporal Controller for scrub/animation
- CSV export of any loaded series

Deferred to phase 2: historical NetCDF/THREDDS archives, currents-station time
series, harmonic tide prediction, water-quality data.

## Status

Early scaffold. The plugin loads and shows its dock panel; data loading is being
wired to the clients in `seastate/clients/`.

## Layout

```
seastate/
  metadata.txt          QGIS plugin metadata
  __init__.py           classFactory entry point
  seastate_plugin.py    plugin class + dock panel
  clients/
    coops.py            NOAA CO-OPS client (verified endpoints)
    ndbc.py             NOAA NDBC client (verified endpoints)
```

## Install (development)

Symlink or copy the `seastate/` directory into your QGIS profile plugins folder,
then enable **SeaState** in Plugins → Manage and Install Plugins.

## License

GPL-2.0-or-later.
