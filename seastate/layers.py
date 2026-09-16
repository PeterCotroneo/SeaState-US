"""Build QGIS memory layers from NOAA client data.

Kept separate from the plugin UI so it can be exercised directly in the QGIS
Python Console (see tests/console_test.py). Only imported inside QGIS — it
depends on PyQGIS.

Notes on QGIS 4 / Qt6: field types use QVariant.* (QGIS keeps this working for
QgsField); temporal mode uses the scoped Qgis.VectorTemporalMode enum.
"""

from qgis.PyQt.QtCore import QVariant, QDateTime, Qt
from qgis.core import (
    Qgis,
    QgsField,
    QgsFields,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsVectorLayer,
    QgsGraduatedSymbolRenderer,
    QgsClassificationQuantile,
    QgsStyle,
)

WGS84 = "EPSG:4326"


def _parse_dt(text):
    """CO-OPS timestamp 'YYYY-MM-DD HH:MM' -> QDateTime (invalid if unparsable)."""
    return QDateTime.fromString(text, "yyyy-MM-dd HH:mm")


def _memory_point_layer(name, fields):
    layer = QgsVectorLayer(f"Point?crs={WGS84}", name, "memory")
    dp = layer.dataProvider()
    dp.addAttributes(fields)
    layer.updateFields()
    return layer


def _apply_temporal_instant(layer, time_field):
    """Drive the layer from a datetime field on the QGIS Temporal Controller."""
    tp = layer.temporalProperties()
    tp.setMode(Qgis.VectorTemporalMode.FeatureDateTimeInstantFromField)
    tp.setStartField(time_field)
    tp.setIsActive(True)


def _graduate(layer, value_field, ramp_name="Blues"):
    """Default graduated styling on a numeric field, quantile classes."""
    try:
        renderer = QgsGraduatedSymbolRenderer(value_field)
        renderer.setClassificationMethod(QgsClassificationQuantile())
        ramp = QgsStyle.defaultStyle().colorRamp(ramp_name)
        if ramp is not None:
            renderer.updateColorRamp(ramp)
        renderer.updateClasses(layer, 5)
        layer.setRenderer(renderer)
    except Exception:
        # Styling is cosmetic; never let it block the data load.
        pass


def build_water_level_layer(rows, station):
    """rows: CO-OPS water_level dicts {t, v, ...}; station: {id, name, lat, lng}."""
    fields = QgsFields()
    fields.append(QgsField("station_id", QVariant.String))
    fields.append(QgsField("station", QVariant.String))
    fields.append(QgsField("time", QVariant.DateTime))
    fields.append(QgsField("water_level", QVariant.Double))
    layer = _memory_point_layer(f"Water level, ft (MLLW) — {station['name']}", fields.toList())

    pt = QgsGeometry.fromPointXY(QgsPointXY(float(station["lng"]), float(station["lat"])))
    feats = []
    for r in rows:
        try:
            v = float(r["v"])
        except (TypeError, ValueError):
            continue
        f = QgsFeature(layer.fields())
        f.setGeometry(pt)
        f.setAttributes([station["id"], station["name"], _parse_dt(r["t"]), v])
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    _graduate(layer, "water_level", "Blues")
    return layer


def build_predictions_layer(rows, station):
    """rows: CO-OPS predictions dicts {t, v, type}; station: {id, name, lat, lng}."""
    fields = QgsFields()
    fields.append(QgsField("station_id", QVariant.String))
    fields.append(QgsField("station", QVariant.String))
    fields.append(QgsField("time", QVariant.DateTime))
    fields.append(QgsField("prediction", QVariant.Double))
    fields.append(QgsField("hilo", QVariant.String))
    layer = _memory_point_layer(f"Tide predictions, ft (MLLW) — {station['name']}", fields.toList())

    pt = QgsGeometry.fromPointXY(QgsPointXY(float(station["lng"]), float(station["lat"])))
    feats = []
    for r in rows:
        try:
            v = float(r["v"])
        except (TypeError, ValueError):
            continue
        f = QgsFeature(layer.fields())
        f.setGeometry(pt)
        f.setAttributes([station["id"], station["name"], _parse_dt(r["t"]), v, r.get("type", "")])
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    _graduate(layer, "prediction", "Blues")
    return layer


# Fields carried from an NDBC latest_observation() dict onto the buoy layer.
_NDBC_NUMERIC = [
    "wind_dir_deg", "wind_speed_ms", "gust_ms", "wave_height_m",
    "dom_period_s", "pressure_hpa", "air_temp_c", "water_temp_c",
]


def build_ndbc_layer(records):
    """records: list of dicts merging station metadata + latest_observation().

    Each dict has station fields (id, lat, lon, name, met/currents/... flags)
    plus optional latest-obs numeric fields. Non-temporal snapshot layer.
    """
    fields = QgsFields()
    fields.append(QgsField("station_id", QVariant.String))
    fields.append(QgsField("name", QVariant.String))
    fields.append(QgsField("type", QVariant.String))
    for flag in ("met", "currents", "waterquality", "dart"):
        fields.append(QgsField(flag, QVariant.String))
    for num in _NDBC_NUMERIC:
        fields.append(QgsField(num, QVariant.Double))
    layer = _memory_point_layer("NDBC buoys", fields.toList())

    feats = []
    for rec in records:
        try:
            geom = QgsGeometry.fromPointXY(QgsPointXY(float(rec["lon"]), float(rec["lat"])))
        except (TypeError, ValueError):
            continue
        f = QgsFeature(layer.fields())
        f.setGeometry(geom)
        attrs = [rec.get("id"), rec.get("name"), rec.get("type"),
                 rec.get("met"), rec.get("currents"),
                 rec.get("waterquality"), rec.get("dart")]
        for num in _NDBC_NUMERIC:
            val = rec.get(num)
            try:
                attrs.append(float(val) if val is not None else None)
            except (TypeError, ValueError):
                attrs.append(None)
        f.setAttributes(attrs)
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    _graduate(layer, "wave_height_m", "Spectral")
    return layer


def build_ndbc_timeseries_layer(records):
    """Temporal buoy layer: one feature per (buoy, timestamp) reading.

    records: dicts with station_id, name, lat, lon, a 'time' string
    ('YYYY-MM-DD HH:MM', UTC) and the numeric obs fields. Animates on the
    Temporal Controller like the CO-OPS layers.
    """
    fields = QgsFields()
    fields.append(QgsField("station_id", QVariant.String))
    fields.append(QgsField("name", QVariant.String))
    fields.append(QgsField("time", QVariant.DateTime))
    for num in _NDBC_NUMERIC:
        fields.append(QgsField(num, QVariant.Double))
    layer = _memory_point_layer("Ocean buoys — wave height, m", fields.toList())

    feats = []
    for rec in records:
        try:
            geom = QgsGeometry.fromPointXY(QgsPointXY(float(rec["lon"]), float(rec["lat"])))
        except (TypeError, ValueError):
            continue
        f = QgsFeature(layer.fields())
        f.setGeometry(geom)
        attrs = [rec.get("station_id"), rec.get("name"), _parse_dt(rec.get("time", ""))]
        for num in _NDBC_NUMERIC:
            val = rec.get(num)
            try:
                attrs.append(float(val) if val is not None else None)
            except (TypeError, ValueError):
                attrs.append(None)
        f.setAttributes(attrs)
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    _graduate(layer, "wave_height_m", "Spectral")
    return layer
