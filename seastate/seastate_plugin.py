"""SeaState US — main plugin class.

Registers a toolbar button / menu entry that toggles a dock panel. Ticking a
layer loads it immediately for the current map view and date range; unticking
removes it. "Refresh for current view" re-pulls the ticked layers after the
map or dates change. Data fetching lives in ``seastate.clients``; layer
construction in ``seastate.layers``.
"""

from qgis.PyQt.QtWidgets import (
    QAction,
    QApplication,
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QDateEdit,
    QPushButton,
    QToolButton,
    QGroupBox,
    QFormLayout,
    QMessageBox,
)
from qgis.PyQt.QtCore import Qt, QDate, QDateTime
from qgis.core import (
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMessageLog,
    QgsDateTimeRange,
    QgsInterval,
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
        self._checkboxes = {}          # key -> QCheckBox
        self._source_layers = {}       # key -> [QgsVectorLayer] currently loaded
        self._suspend_toggle = False   # guard against programmatic re-checks
        self._temporal_range = None    # (lo, hi) QDateTimes of loaded data

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

    # Per-layer metadata shown by the ⓘ button.
    LAYER_INFO = {
        "water_level": (
            "Water levels — NOAA CO-OPS",
            "Measured water height at coastal tide-gauge stations, recorded every "
            "6 minutes and referenced to the MLLW tidal datum. Each point is a "
            "fixed gauge on a pier or dock.\n\n"
            "Units: feet.\n"
            "Coverage: U.S. coasts, Great Lakes and territories.\n\n"
            "Source: NOAA Center for Operational Oceanographic Products and "
            "Services (CO-OPS)."),
        "predictions": (
            "Tide predictions — NOAA CO-OPS",
            "Predicted high- and low-tide times and heights (the 'hi/lo' product), "
            "computed from each station's harmonic constituents.\n\n"
            "Units: feet, MLLW datum.\n"
            "Coverage: U.S. tide-prediction stations.\n\n"
            "Source: NOAA CO-OPS."),
        "ndbc": (
            "Ocean buoys — NOAA NDBC",
            "Observations from offshore weather and wave buoys: wind "
            "direction/speed/gust, wave height and period, air and water "
            "temperature, and barometric pressure.\n\n"
            "The live feed spans roughly the most recent 45 days.\n"
            "Coverage: U.S. waters plus some open-ocean and partner buoys.\n\n"
            "Source: NOAA National Data Buoy Center (NDBC)."),
    }

    def _build_dock(self):
        dock = QDockWidget("SeaState US", self.iface.mainWindow())
        panel = QWidget()
        layout = QVBoxLayout(panel)

        intro = QLabel(
            "Live U.S. coastal observations from NOAA. Zoom to a U.S. coast, "
            "then tick a layer to load it. Points show right away; press "
            "Animate to play them over time."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        sources = QGroupBox("Layers")
        s_layout = QVBoxLayout(sources)
        self.cb_water_level = QCheckBox("Water levels — measured tide-gauge readings")
        self.cb_water_level.setToolTip("Observed 6-minute water level (NOAA CO-OPS).")
        self.cb_predictions = QCheckBox("Tide predictions — daily highs & lows")
        self.cb_predictions.setToolTip("Predicted high/low tides (NOAA CO-OPS).")
        self.cb_ndbc = QCheckBox("Ocean buoys — wind, waves & temperature")
        self.cb_ndbc.setToolTip("Offshore buoy observations, last ~45 days (NOAA NDBC).")
        self._checkboxes = {
            "water_level": self.cb_water_level,
            "predictions": self.cb_predictions,
            "ndbc": self.cb_ndbc,
        }
        for key, cb in self._checkboxes.items():
            s_layout.addLayout(self._source_row(key, cb))
        layout.addWidget(sources)

        window = QGroupBox("Date range")
        w_layout = QFormLayout(window)
        self.date_begin = QDateEdit(QDate.currentDate().addDays(-7))
        self.date_end = QDateEdit(QDate.currentDate())
        self.date_begin.setCalendarPopup(True)
        self.date_end.setCalendarPopup(True)
        w_layout.addRow("From", self.date_begin)
        w_layout.addRow("To", self.date_end)
        layout.addWidget(window)

        self.refresh_button = QPushButton("Refresh for current view")
        self.refresh_button.setToolTip(
            "Re-load the ticked layers for the current map view and date range.")
        self.refresh_button.clicked.connect(self._refresh)
        layout.addWidget(self.refresh_button)

        self.animate_button = QPushButton("Animate over time ▶")
        self.animate_button.setToolTip(
            "Play the loaded readings as a time-lapse on the Temporal "
            "Controller. Points show statically until you press this.")
        self.animate_button.clicked.connect(self._animate)
        layout.addWidget(self.animate_button)
        layout.addStretch(1)

        dock.setWidget(panel)
        return dock

    def _source_row(self, key, checkbox):
        """A checkbox + ⓘ info button, wired to load/unload on toggle."""
        row = QHBoxLayout()
        row.addWidget(checkbox, 1)
        info = QToolButton()
        info.setText("ⓘ")  # circled i
        info.setAutoRaise(True)
        info.setToolTip("About this layer")
        info.clicked.connect(lambda _=False, k=key: self._show_info(k))
        row.addWidget(info, 0)
        checkbox.toggled.connect(lambda checked, k=key: self._on_source_toggled(k, checked))
        return row

    def _show_info(self, key):
        title, body = self.LAYER_INFO[key]
        QMessageBox.information(self.iface.mainWindow(), title, body)

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

    def _dates(self):
        return (self.date_begin.date().toString("yyyyMMdd"),
                self.date_end.date().toString("yyyyMMdd"))

    def _ensure_group(self):
        root = QgsProject.instance().layerTreeRoot()
        grp = root.findGroup("SeaState")
        return grp if grp is not None else root.insertGroup(0, "SeaState")

    def _add(self, layer, group):
        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)

    def _set_checkbox(self, key, value):
        cb = self._checkboxes.get(key)
        if cb is None:
            return
        self._suspend_toggle = True
        cb.setChecked(value)
        self._suspend_toggle = False

    # --- toggle handling -------------------------------------------------
    def _on_source_toggled(self, key, checked):
        if self._suspend_toggle:
            return
        if checked:
            self._load_source(key)
        else:
            self._unload_source(key)

    def _refresh(self):
        keys = [k for k, cb in self._checkboxes.items() if cb.isChecked()]
        if not keys:
            self.iface.messageBar().pushInfo(
                "SeaState", "Tick a layer first, then Refresh.")
            return
        for key in keys:
            self._load_source(key)

    def _load_source(self, key):
        # Reloading a source clears its previous layers first.
        if key in self._source_layers:
            self._unload_source(key, reconfigure=False)

        bbox = self._canvas_bbox_wgs84()
        begin, end = self._dates()
        self._log(f"Load {key}: bbox={bbox} dates={begin}-{end}")
        problems = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if key == "water_level":
                built = self._build_water_level_layers(bbox, begin, end, problems)
            elif key == "predictions":
                built = self._build_predictions_layers(bbox, begin, end, problems)
            elif key == "ndbc":
                built = self._build_ndbc_layers(bbox, begin, end, problems)
            else:
                built = []
        finally:
            QApplication.restoreOverrideCursor()

        bar = self.iface.messageBar()
        if built:
            group = self._ensure_group()
            for lyr in built:
                self._add(lyr, group)
            self._source_layers[key] = built
            self._configure_temporal()
            label = self.LAYER_INFO[key][0].split(" — ")[0]
            note = f"Loaded {label} ({len(built)} layer(s))."
            if problems:
                note += f" {len(problems)} issue(s) — see Log Messages (SeaState)."
            bar.pushSuccess("SeaState", note)
        else:
            # Nothing to show — untick so the box reflects reality.
            self._set_checkbox(key, False)
            bar.pushWarning("SeaState", problems[0] if problems
                            else "Nothing found in the current view / date range.")

    def _unload_source(self, key, reconfigure=True):
        proj = QgsProject.instance()
        for lyr in self._source_layers.pop(key, []):
            try:
                proj.removeMapLayer(lyr.id())
            except Exception:  # noqa: BLE001
                pass
        if not self._source_layers:
            root = proj.layerTreeRoot()
            grp = root.findGroup("SeaState")
            if grp is not None:
                root.removeChildNode(grp)
        if reconfigure:
            self._configure_temporal()

    # --- per-source fetch/build ------------------------------------------
    def _coops_stations(self, bbox, problems):
        try:
            return [s for s in coops.list_stations()
                    if self._in_bbox(s["lat"], s["lng"], bbox)]
        except Exception as exc:  # noqa: BLE001
            problems.append(f"CO-OPS station list: {exc}")
            self._log(f"CO-OPS station list FAILED: {exc}", Qgis.Warning)
            return []

    def _build_water_level_layers(self, bbox, begin, end, problems):
        stations = self._coops_stations(bbox, problems)
        self._log(f"CO-OPS stations in view: {len(stations)}")
        if not stations:
            problems.append("No CO-OPS tide-gauge stations in the current view.")
            return []
        built = []
        for s in stations[:MAX_COOPS_STATIONS]:
            try:
                rows = coops.water_level(s["id"], begin, end)
                if rows:
                    built.append(layers.build_water_level_layer(rows, s))
                else:
                    self._log(f"No water level for {s['id']} {s['name']}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"water level {s['name']}: {exc}")
                self._log(f"water level {s['id']} FAILED: {exc}", Qgis.Warning)
        return built

    def _build_predictions_layers(self, bbox, begin, end, problems):
        stations = self._coops_stations(bbox, problems)
        self._log(f"CO-OPS stations in view: {len(stations)}")
        if not stations:
            problems.append("No CO-OPS tide-gauge stations in the current view.")
            return []
        built = []
        for s in stations[:MAX_COOPS_STATIONS]:
            try:
                rows = coops.predictions(s["id"], begin, end)
                if rows:
                    built.append(layers.build_predictions_layer(rows, s))
                else:
                    self._log(f"No predictions for {s['id']} {s['name']}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"predictions {s['name']}: {exc}")
                self._log(f"predictions {s['id']} FAILED: {exc}", Qgis.Warning)
        return built

    def _build_ndbc_layers(self, bbox, begin, end, problems):
        try:
            buoys = [b for b in ndbc.list_stations()
                     if self._in_bbox(b["lat"], b["lon"], bbox)]
        except Exception as exc:  # noqa: BLE001
            problems.append(f"NDBC station list: {exc}")
            self._log(f"NDBC station list FAILED: {exc}", Qgis.Warning)
            return []
        self._log(f"NDBC buoys in view: {len(buoys)}")
        if not buoys:
            problems.append("No NDBC buoys in the current view.")
            return []
        records = []
        for b in buoys[:MAX_NDBC_STATIONS]:
            try:
                rows = ndbc.observations(b["id"], begin, end)
            except Exception as exc:  # noqa: BLE001
                rows = []
                self._log(f"NDBC obs {b['id']} failed: {exc}", Qgis.Warning)
            for row in rows:
                records.append({"station_id": b["id"], "name": b["name"],
                                "lat": b["lat"], "lon": b["lon"], **row})
        self._log(f"NDBC readings in window: {len(records)}")
        if not records:
            problems.append("Buoys found, but no readings in the date range "
                            "(the live buoy feed spans only ~45 days).")
            return []
        return [layers.build_ndbc_timeseries_layer(records)]

    # --- temporal controller ---------------------------------------------
    def _configure_temporal(self):
        """Remember the loaded data's time span and pre-range the Temporal
        Controller, but leave navigation OFF so every point shows straight
        away. Animation is opt-in via the Animate button."""
        lo = hi = None
        for lyrs in self._source_layers.values():
            for layer in lyrs:
                idx = layer.fields().indexOf("time")
                if idx < 0:
                    continue
                mn, mx = layer.minimumValue(idx), layer.maximumValue(idx)
                if isinstance(mn, QDateTime) and mn.isValid():
                    lo = mn if lo is None or mn < lo else lo
                if isinstance(mx, QDateTime) and mx.isValid():
                    hi = mx if hi is None or mx > hi else hi
        self._temporal_range = (lo, hi) if lo is not None and hi is not None else None
        if self._temporal_range is None:
            return
        try:
            tc = self.iface.mapCanvas().temporalController()
            # Off = the map shows all readings at once (no time filter).
            tc.setNavigationMode(Qgis.TemporalNavigationMode.NavigationOff)
            tc.setTemporalExtents(QgsDateTimeRange(lo, hi))
            tc.setFrameDuration(QgsInterval(3600))
            self._log(f"Temporal range {lo.toString('yyyy-MM-dd HH:mm')} "
                      f"to {hi.toString('yyyy-MM-dd HH:mm')} (animation off)")
        except Exception as exc:  # noqa: BLE001
            self._log(f"Temporal controller setup skipped: {exc}", Qgis.Warning)

    def _animate(self):
        """Turn the loaded layers into a time-lapse on the Temporal Controller."""
        bar = self.iface.messageBar()
        if not self._temporal_range:
            bar.pushInfo("SeaState", "Load a layer first, then Animate.")
            return
        lo, hi = self._temporal_range
        try:
            tc = self.iface.mapCanvas().temporalController()
            tc.setTemporalExtents(QgsDateTimeRange(lo, hi))
            tc.setFrameDuration(QgsInterval(3600))  # 1-hour steps
            tc.setNavigationMode(Qgis.TemporalNavigationMode.Animated)
            tc.rewindToStart()
            tc.playForward()
            bar.pushInfo("SeaState", "Animating. Open the Temporal Controller "
                         "(clock icon) to pause, scrub, or change the step.")
        except Exception as exc:  # noqa: BLE001
            bar.pushWarning("SeaState", f"Could not start animation: {exc}")
