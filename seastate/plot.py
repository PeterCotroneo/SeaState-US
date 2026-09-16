"""Self-contained time-series plotting for SeaState US.

Builds a Matplotlib line chart (value vs time) in a QDialog — no external
QGIS plugins required. Matplotlib ships with QGIS; if it is somehow missing
we raise PlottingUnavailable so the caller can show a friendly message.
"""

from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QScrollArea
from qgis.core import NULL


class PlottingUnavailable(RuntimeError):
    pass


# Preferred value field per layer, most specific first: (field, unit, metric).
_PRIMARY = [
    ("water_level", "ft", "Water level"),
    ("prediction", "ft", "Tide prediction"),
    ("wave_height_m", "m", "Wave height"),
    ("wind_speed_ms", "m/s", "Wind speed"),
    ("water_temp_c", "°C", "Water temp"),
]


def _clean(value):
    return None if value == NULL else value


def layer_series_list(layer):
    """Plottable series from a SeaState layer, one per station.

    A single layer can hold many stations (e.g. all buoys), so we group by
    station and return a list of (label, xs, ys, unit) — each label naming the
    metric and the station. Returns [] if the layer has no time field or no
    numeric readings.
    """
    fields = layer.fields()
    tidx = fields.indexOf("time")
    if tidx < 0:
        return []
    name_idx = fields.indexOf("name")
    if name_idx < 0:
        name_idx = fields.indexOf("station")
    id_idx = fields.indexOf("station_id")

    for fld, unit, metric in _PRIMARY:
        vidx = fields.indexOf(fld)
        if vidx < 0:
            continue
        groups = {}  # station key -> {"name", "xs", "ys"}
        for feat in layer.getFeatures():
            try:
                dt = feat[tidx].toPyDateTime()
            except (AttributeError, TypeError):
                continue
            try:
                yv = float(feat[vidx])
            except (TypeError, ValueError):
                continue
            name = _clean(feat[name_idx]) if name_idx >= 0 else None
            key = _clean(feat[id_idx]) if id_idx >= 0 else name
            if key is None:
                key = layer.name()
            g = groups.setdefault(key, {"name": name, "xs": [], "ys": []})
            g["xs"].append(dt)
            g["ys"].append(yv)
        if groups:
            out = []
            for key, g in groups.items():
                order = sorted(range(len(g["xs"])), key=lambda i: g["xs"][i])
                xs = [g["xs"][i] for i in order]
                ys = [g["ys"][i] for i in order]
                station = g["name"] or key
                out.append((f"{metric} — {station}", xs, ys, unit))
            out.sort(key=lambda s: s[0])
            return out
    return []


def _import_matplotlib():
    try:
        import matplotlib
        matplotlib.use("QtAgg")
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import (
            FigureCanvasQTAgg as Canvas,
            NavigationToolbar2QT as NavBar,
        )
        return Figure, Canvas, NavBar
    except Exception as exc:  # noqa: BLE001
        raise PlottingUnavailable(
            "Matplotlib is not available in this QGIS Python. Install it via "
            "the QGIS Python console: import pip; pip.main(['install', "
            "'matplotlib']).") from exc


def show_time_series(parent, title, series):
    """series: list of (label, xs[datetime], ys[float], unit).

    Draws one chart per series, stacked in a single scrollable window (each
    with its own y-axis and units). Returns a non-modal QDialog — keep a
    reference so it stays open."""
    Figure, Canvas, NavBar = _import_matplotlib()

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(920, 620)
    lay = QVBoxLayout(dlg)

    n = len(series)
    fig = Figure(figsize=(9, 2.6 * n), tight_layout=True)
    canvas = Canvas(fig)
    canvas.setMinimumHeight(int(230 * n))  # so subplots keep height when scrolled

    lay.addWidget(NavBar(canvas, dlg))
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(canvas)
    lay.addWidget(scroll)

    axes = fig.subplots(n, 1, sharex=True, squeeze=False)[:, 0]
    for ax, (label, xs, ys, unit) in zip(axes, series):
        ax.plot(xs, ys, marker=".", markersize=2, linewidth=1, color="#1f77b4")
        ax.set_title(label, fontsize=10, loc="left")
        ax.set_ylabel(unit, fontsize=9)
        ax.grid(True, alpha=0.3)
    axes[-1].set_xlabel("Time")
    fig.autofmt_xdate()

    dlg.setModal(False)
    dlg.show()
    return dlg
