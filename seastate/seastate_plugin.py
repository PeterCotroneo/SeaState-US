"""SeaState — main plugin class.

Registers a toolbar button / menu entry that toggles a dock panel. The panel
UI is a placeholder for v1: source pickers, a date range, and a Load button.
Data loading is wired to the clients in ``seastate.clients`` as those land.
"""

from qgis.PyQt.QtWidgets import (
    QAction,
    QApplication,
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QLabel,
    QCheckBox,
    QDateEdit,
    QPushButton,
    QGroupBox,
    QFormLayout,
)
from qgis.PyQt.QtCore import Qt, QDate
from qgis.core import (
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
)

from .clients import coops, ndbc
from . import layers

# v1 guardrails so a wide extent can't fire hundreds of requests.
MAX_COOPS_STATIONS = 5
MAX_NDBC_STATIONS = 40


class SeaStatePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None

    def initGui(self):
        self.action = QAction("SeaState", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("SeaState", self.action)

    def unload(self):
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("SeaState", self.action)
            self.action = None

    def _toggle_dock(self, checked):
        if checked:
            if self.dock is None:
                self.dock = self._build_dock()
                self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
            self.dock.show()
        elif self.dock is not None:
            self.dock.hide()

    def _build_dock(self):
        dock = QDockWidget("SeaState", self.iface.mainWindow())
        panel = QWidget()
        layout = QVBoxLayout(panel)

        sources = QGroupBox("Data sources")
        s_layout = QVBoxLayout(sources)
        self.cb_water_level = QCheckBox("CO-OPS water level (observed)")
        self.cb_predictions = QCheckBox("CO-OPS tide predictions (hi/lo)")
        self.cb_ndbc = QCheckBox("NDBC buoys (wind, waves, met)")
        self.cb_water_level.setChecked(True)
        for cb in (self.cb_water_level, self.cb_predictions, self.cb_ndbc):
            s_layout.addWidget(cb)
        layout.addWidget(sources)

        window = QGroupBox("Time window")
        w_layout = QFormLayout(window)
        self.date_begin = QDateEdit(QDate.currentDate().addDays(-7))
        self.date_end = QDateEdit(QDate.currentDate())
        self.date_begin.setCalendarPopup(True)
        self.date_end.setCalendarPopup(True)
        w_layout.addRow("From", self.date_begin)
        w_layout.addRow("To", self.date_end)
        layout.addWidget(window)

        layout.addWidget(QLabel("Stations are loaded for the current map extent."))

        self.load_button = QPushButton("Load")
        self.load_button.clicked.connect(self._on_load)
        layout.addWidget(self.load_button)
        layout.addStretch(1)

        dock.setWidget(panel)
        return dock

    def _canvas_bbox_wgs84(self):
        """Current canvas extent as (xmin, ymin, xmax, ymax) in lon/lat."""
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        src = canvas.mapSettings().destinationCrs()
        dst = QgsCoordinateReferenceSystem("EPSG:4326")
        if src != dst:
            xform = QgsCoordinateTransform(src, dst, QgsProject.instance())
            extent = xform.transformBoundingBox(extent)
        return (extent.xMinimum(), extent.yMinimum(),
                extent.xMaximum(), extent.yMaximum())

    @staticmethod
    def _in_bbox(lat, lng, bbox):
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            return False
        xmin, ymin, xmax, ymax = bbox
        return xmin <= lng <= xmax and ymin <= lat <= ymax

    def _on_load(self):
        bar = self.iface.messageBar()
        bbox = self._canvas_bbox_wgs84()
        begin = self.date_begin.date().toString("yyyyMMdd")
        end = self.date_end.date().toString("yyyyMMdd")
        project = QgsProject.instance()
        added = 0

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if self.cb_water_level.isChecked() or self.cb_predictions.isChecked():
                stations = [s for s in coops.list_stations()
                            if self._in_bbox(s["lat"], s["lng"], bbox)]
                if not stations:
                    bar.pushInfo("SeaState", "No CO-OPS stations in the current extent.")
                for s in stations[:MAX_COOPS_STATIONS]:
                    if self.cb_water_level.isChecked():
                        rows = coops.water_level(s["id"], begin, end)
                        if rows:
                            project.addMapLayer(layers.build_water_level_layer(rows, s))
                            added += 1
                    if self.cb_predictions.isChecked():
                        rows = coops.predictions(s["id"], begin, end)
                        if rows:
                            project.addMapLayer(layers.build_predictions_layer(rows, s))
                            added += 1

            if self.cb_ndbc.isChecked():
                buoys = [b for b in ndbc.list_stations()
                         if self._in_bbox(b["lat"], b["lon"], bbox)]
                records = []
                for b in buoys[:MAX_NDBC_STATIONS]:
                    obs = None
                    try:
                        obs = ndbc.latest_observation(b["id"])
                    except Exception:
                        pass
                    records.append({**b, **(obs or {})})
                if records:
                    project.addMapLayer(layers.build_ndbc_layer(records))
                    added += 1
                else:
                    bar.pushInfo("SeaState", "No NDBC buoys in the current extent.")

            if added:
                bar.pushSuccess("SeaState", f"Loaded {added} layer(s). "
                                "Use the Temporal Controller to animate CO-OPS layers.")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            bar.pushCritical("SeaState", f"Load failed: {exc}")
        finally:
            QApplication.restoreOverrideCursor()
