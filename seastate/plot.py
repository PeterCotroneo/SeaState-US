"""Self-contained time-series plotting for SeaState US.

Builds a Matplotlib line chart (value vs time) in a QDialog — no external
QGIS plugins required. Matplotlib ships with QGIS; if it is somehow missing
we raise PlottingUnavailable so the caller can show a friendly message.
"""

from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout


class PlottingUnavailable(RuntimeError):
    pass


# Preferred value field per layer, most specific first, with axis label.
_PRIMARY = [
    ("water_level", "Water level (ft)"),
    ("prediction", "Predicted tide (ft)"),
    ("wave_height_m", "Wave height (m)"),
    ("wind_speed_ms", "Wind speed (m/s)"),
    ("water_temp_c", "Water temp (°C)"),
]


def layer_series(layer):
    """Extract a plottable series from a SeaState layer.

    Returns (label, xs, ys, unit) sorted by time, or None if the layer has no
    time field or no numeric readings in any candidate field.
    """
    fields = layer.fields()
    tidx = fields.indexOf("time")
    if tidx < 0:
        return None
    for fld, unit in _PRIMARY:
        idx = fields.indexOf(fld)
        if idx < 0:
            continue
        xs, ys = [], []
        for feat in layer.getFeatures():
            t = feat[tidx]
            v = feat[idx]
            try:
                dt = t.toPyDateTime()
            except (AttributeError, TypeError):
                continue
            try:
                yv = float(v)
            except (TypeError, ValueError):
                continue
            xs.append(dt)
            ys.append(yv)
        if xs:
            order = sorted(range(len(xs)), key=lambda i: xs[i])
            xs = [xs[i] for i in order]
            ys = [ys[i] for i in order]
            return (layer.name(), xs, ys, unit)
    return None


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
    """series: list of (label, xs[datetime], ys[float], unit). Returns a
    non-modal QDialog holding the chart (keep a reference so it stays open)."""
    Figure, Canvas, NavBar = _import_matplotlib()

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(900, 500)
    lay = QVBoxLayout(dlg)

    fig = Figure(figsize=(9, 4.5), tight_layout=True)
    canvas = Canvas(fig)
    lay.addWidget(NavBar(canvas, dlg))
    lay.addWidget(canvas)

    ax = fig.add_subplot(111)
    for label, xs, ys, _unit in series:
        ax.plot(xs, ys, marker=".", markersize=2, linewidth=1, label=label)
    ax.set_xlabel("Time")
    units = sorted({unit for _l, _x, _y, unit in series})
    ax.set_ylabel(" / ".join(units) if units else "Value")
    if len(units) > 1:
        ax.set_title("Note: series use different units — compare shapes, not levels",
                     fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8, loc="best")
    fig.autofmt_xdate()

    dlg.setModal(False)
    dlg.show()
    return dlg
