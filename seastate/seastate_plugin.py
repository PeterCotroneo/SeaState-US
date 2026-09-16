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
from qgis.PyQt.QtCore import Qt, QDate
from qgis.core import (
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMessageLog,
    Qgis,
)

from .clients import coops, ndbc
from . import layers, plot

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
        self._layer_key = {}           # layer id -> source key
        self._removing = False         # guard: we are the ones removing layers
        self._plot_dialogs = []        # keep plot windows alive

    def initGui(self):
        self.action = QAction("SeaState US", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("SeaState US", self.action)
        # Keep the plugin's checkboxes in sync when layers are removed in QGIS.
        QgsProject.instance().layersRemoved.connect(self._on_layers_removed)

    def unload(self):
        try:
            QgsProject.instance().layersRemoved.disconnect(self._on_layers_removed)
        except (TypeError, RuntimeError):
            pass
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
            "then tick a layer to load it. Press Plot to chart the readings "
            "over time."
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

        self.plot_button = QPushButton("Plot over time")
        self.plot_button.setToolTip(
            "Open a chart of the loaded readings — value on the vertical axis, "
            "time along the bottom.")
        self.plot_button.clicked.connect(self._plot)
        layout.addWidget(self.plot_button)
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
        return group.addLayer(layer)  # QgsLayerTreeLayer node

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
                node = self._add(lyr, group)
                self._layer_key[lyr.id()] = key
                if node is not None:
                    node.visibilityChanged.connect(
                        lambda _n, k=key: self._on_node_visibility(k))
            self._source_layers[key] = built
            self.iface.mapCanvas().refresh()
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
        self._removing = True  # so our own removals don't re-enter the sync handler
        try:
            for lyr in self._source_layers.pop(key, []):
                lid = self._safe_id(lyr)
                if lid is None:
                    continue
                self._layer_key.pop(lid, None)
                try:
                    proj.removeMapLayer(lid)
                except Exception:  # noqa: BLE001
                    pass
            self._cleanup_group()
        finally:
            self._removing = False
        self.iface.mapCanvas().refresh()  # repaint so removed markers disappear

    @staticmethod
    def _safe_id(layer):
        try:
            return layer.id()
        except (RuntimeError, AttributeError):
            return None

    def _cleanup_group(self):
        """Drop the empty 'SeaState' group once no sources remain."""
        if self._source_layers:
            return
        root = QgsProject.instance().layerTreeRoot()
        grp = root.findGroup("SeaState")
        if grp is not None:
            root.removeChildNode(grp)

    def _on_node_visibility(self, key):
        """Mirror a source's layer-visibility onto its plugin checkbox.

        The box stays ticked while any of the source's layers is shown, and
        unticks when they are all hidden (this only toggles the box — it does
        not remove the layers)."""
        if self._suspend_toggle:
            return
        root = QgsProject.instance().layerTreeRoot()
        any_visible = False
        for lyr in self._source_layers.get(key, []):
            lid = self._safe_id(lyr)
            if lid is None:
                continue
            node = root.findLayer(lid)
            if node is not None and node.itemVisibilityChecked():
                any_visible = True
                break
        cb = self._checkboxes.get(key)
        if cb is None:
            return
        if any_visible != cb.isChecked():
            self._set_checkbox(key, any_visible)

    def _on_layers_removed(self, ids):
        """A layer was removed in QGIS — keep the plugin checkboxes in sync."""
        if self._removing:
            return
        emptied = []
        for lid in ids:
            key = self._layer_key.pop(lid, None)
            if key is None:
                continue
            remaining = [l for l in self._source_layers.get(key, [])
                         if self._safe_id(l) not in (lid, None)]
            if remaining:
                self._source_layers[key] = remaining
            else:
                self._source_layers.pop(key, None)
                emptied.append(key)
        if not emptied:
            return
        for key in emptied:
            self._set_checkbox(key, False)  # untick without triggering another remove
        self._cleanup_group()
        self.iface.mapCanvas().refresh()

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

    # --- plotting --------------------------------------------------------
    def _plot(self):
        """Chart the loaded readings — value vs time — in a window."""
        bar = self.iface.messageBar()
        if not self._source_layers:
            bar.pushInfo("SeaState", "Load a layer first, then Plot.")
            return
        series = []
        for lyrs in self._source_layers.values():
            for layer in lyrs:
                s = plot.layer_series(layer)
                if s:
                    series.append(s)
        if not series:
            bar.pushWarning("SeaState", "No plottable values in the loaded layers.")
            return
        try:
            dlg = plot.show_time_series(
                self.iface.mainWindow(), "SeaState US — readings over time", series)
        except plot.PlottingUnavailable as exc:
            bar.pushWarning("SeaState", str(exc))
            self._log(f"Plot failed: {exc}", Qgis.Warning)
            return
        self._plot_dialogs.append(dlg)
