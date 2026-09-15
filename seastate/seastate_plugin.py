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
    QgsMessageLog,
    Qgis,
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
        self.action = QAction("SeaState US", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("SeaState US", self.action)

    def unload(self):
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("SeaState US", self.action)
            self.action = None

    def _toggle_dock(self, checked):
        if checked:
            if self.dock is None:
                self.dock = self._build_dock()
                self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.show()
        elif self.dock is not None:
            self.dock.hide()

    def _build_dock(self):
        dock = QDockWidget("SeaState US", self.iface.mainWindow())
        panel = QWidget()
        layout = QVBoxLayout(panel)

        intro = QLabel(
            "Live U.S. coastal observations from NOAA. Zoom the map to a "
            "U.S. coast, choose what to load, and press Load."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        sources = QGroupBox("What to load")
        s_layout = QVBoxLayout(sources)
        self.cb_water_level = QCheckBox("Water levels — measured tide-gauge readings")
        self.cb_water_level.setToolTip(
            "Observed water level from NOAA CO-OPS coastal tide-gauge stations "
            "(6-minute readings). Each station is a fixed gauge on a pier or dock."
        )
        self.cb_predictions = QCheckBox("Tide predictions — daily highs & lows")
        self.cb_predictions.setToolTip(
            "Predicted high- and low-tide times and heights from NOAA CO-OPS."
        )
        self.cb_ndbc = QCheckBox("Ocean buoys — wind, waves & temperature")
        self.cb_ndbc.setToolTip(
            "Readings over the selected time window from NOAA NDBC offshore "
            "buoys: wind, wave height and period, air and water temperature, "
            "and pressure. The live buoy feed spans only the most recent ~45 days."
        )
        self.cb_water_level.setChecked(True)
        for cb in (self.cb_water_level, self.cb_predictions, self.cb_ndbc):
            s_layout.addWidget(cb)
        layout.addWidget(sources)

        about = QLabel(
            "<b>Where this data comes from</b><br>"
            "<b>Tides &amp; water levels</b> — NOAA <i>CO-OPS</i> "
            "(Center for Operational Oceanographic Products and Services), the "
            "national network of coastal tide-gauge stations.<br>"
            "<b>Buoys</b> — NOAA <i>NDBC</i> (National Data Buoy Center), "
            "offshore weather and wave buoys.<br>"
            "Coverage: U.S. coasts, Great Lakes and territories."
        )
        about.setWordWrap(True)
        about.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(about)

        window = QGroupBox("Time window")
        w_layout = QFormLayout(window)
        self.date_begin = QDateEdit(QDate.currentDate().addDays(-7))
        self.date_end = QDateEdit(QDate.currentDate())
        self.date_begin.setCalendarPopup(True)
        self.date_end.setCalendarPopup(True)
        w_layout.addRow("From", self.date_begin)
        w_layout.addRow("To", self.date_end)
        layout.addWidget(window)

        extent_note = QLabel(
            "Loads stations within the current map view, over the time window "
            "above. All three layers are time-aware — open the Temporal "
            "Controller (clock icon) to animate them."
        )
        extent_note.setWordWrap(True)
        layout.addWidget(extent_note)

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

    def _log(self, msg, level=Qgis.Info):
        QgsMessageLog.logMessage(msg, "SeaState", level)

    def _fresh_group(self):
        """A clean 'SeaState' layer group, replacing any previous results."""
        root = QgsProject.instance().layerTreeRoot()
        existing = root.findGroup("SeaState")
        if existing is not None:
            root.removeChildNode(existing)
        return root.insertGroup(0, "SeaState")

    def _add(self, layer, group):
        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)

    def _on_load(self):
        bar = self.iface.messageBar()
        bbox = self._canvas_bbox_wgs84()
        begin = self.date_begin.date().toString("yyyyMMdd")
        end = self.date_end.date().toString("yyyyMMdd")
        self._log(f"Load: bbox={bbox} dates={begin}-{end}")

        group = self._fresh_group()
        added = 0
        problems = []

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # --- CO-OPS (each station/source isolated so one failure isn't fatal) ---
            if self.cb_water_level.isChecked() or self.cb_predictions.isChecked():
                try:
                    stations = [s for s in coops.list_stations()
                                if self._in_bbox(s["lat"], s["lng"], bbox)]
                except Exception as exc:  # noqa: BLE001
                    stations = []
                    problems.append(f"CO-OPS station list: {exc}")
                self._log(f"CO-OPS stations in extent: {len(stations)}")
                if not stations and (self.cb_water_level.isChecked()
                                     or self.cb_predictions.isChecked()):
                    problems.append("No CO-OPS stations in the current extent.")
                for s in stations[:MAX_COOPS_STATIONS]:
                    if self.cb_water_level.isChecked():
                        try:
                            rows = coops.water_level(s["id"], begin, end)
                            if rows:
                                self._add(layers.build_water_level_layer(rows, s), group)
                                added += 1
                            else:
                                self._log(f"No water level for {s['id']} {s['name']}")
                        except Exception as exc:  # noqa: BLE001
                            problems.append(f"water level {s['name']}: {exc}")
                            self._log(f"water level {s['id']} FAILED: {exc}", Qgis.Warning)
                    if self.cb_predictions.isChecked():
                        try:
                            rows = coops.predictions(s["id"], begin, end)
                            if rows:
                                self._add(layers.build_predictions_layer(rows, s), group)
                                added += 1
                            else:
                                self._log(f"No predictions for {s['id']} {s['name']}")
                        except Exception as exc:  # noqa: BLE001
                            problems.append(f"predictions {s['name']}: {exc}")
                            self._log(f"predictions {s['id']} FAILED: {exc}", Qgis.Warning)

            # --- NDBC (fully independent of the CO-OPS block above) ---
            if self.cb_ndbc.isChecked():
                try:
                    buoys = [b for b in ndbc.list_stations()
                             if self._in_bbox(b["lat"], b["lon"], bbox)]
                    self._log(f"NDBC buoys in extent: {len(buoys)}")
                    records = []
                    for b in buoys[:MAX_NDBC_STATIONS]:
                        try:
                            rows = ndbc.observations(b["id"], begin, end)
                        except Exception as exc:  # noqa: BLE001
                            rows = []
                            self._log(f"NDBC obs {b['id']} failed: {exc}", Qgis.Warning)
                        for row in rows:
                            records.append({
                                "station_id": b["id"], "name": b["name"],
                                "lat": b["lat"], "lon": b["lon"], **row,
                            })
                    self._log(f"NDBC readings in window: {len(records)}")
                    if records:
                        self._add(layers.build_ndbc_timeseries_layer(records), group)
                        added += 1
                    elif buoys:
                        problems.append("Buoys found, but no readings in the time "
                                        "window (NDBC live feed only spans ~45 days).")
                    else:
                        problems.append("No NDBC buoys in the current extent.")
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"NDBC: {exc}")
                    self._log(f"NDBC block FAILED: {exc}", Qgis.Warning)
        finally:
            QApplication.restoreOverrideCursor()

        if added == 0 and group is not None:
            QgsProject.instance().layerTreeRoot().removeChildNode(group)

        if added:
            note = f"Loaded {added} layer(s)."
            if problems:
                note += f" {len(problems)} issue(s) — see Log Messages (SeaState)."
            bar.pushSuccess("SeaState", note)
        elif problems:
            bar.pushWarning("SeaState", "; ".join(problems[:3]))
        else:
            bar.pushInfo("SeaState", "Nothing to load — check a data source.")
