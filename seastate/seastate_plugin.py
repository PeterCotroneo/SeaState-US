"""SeaState US — main plugin class.

Registers a toolbar button / menu entry that toggles a dock panel. Ticking a
layer loads it immediately for the current map view and date range; unticking
removes it. "Refresh for current view" re-pulls the ticked layers after the
map or dates change. Data fetching lives in ``seastate.clients``; layer
construction in ``seastate.layers``.
"""

from qgis.PyQt.QtWidgets import (
    QAction,
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
import os

from qgis.PyQt.QtCore import Qt, QDate, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMessageLog,
    QgsApplication,
    QgsTask,
    Qgis,
)

from .clients import coops, ndbc
from . import layers, plot

# v1 guardrails so a wide extent can't fire hundreds of requests.
MAX_COOPS_STATIONS = 5
MAX_NDBC_STATIONS = 40


class _FetchTask(QgsTask):
    """Runs a network fetch off the main thread.

    ``fetch_fn`` returns ``(data, problems)`` and must not touch the GUI.
    ``on_done(task, ok)`` is called on the main thread when finished.
    """

    def __init__(self, description, fetch_fn, on_done):
        super().__init__(description)
        self._fetch_fn = fetch_fn
        self._on_done = on_done
        self.data = None
        self.problems = []
        self.error = None

    def run(self):
        try:
            self.data, self.problems = self._fetch_fn()
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            return False

    def finished(self, result):
        self._on_done(self, result)


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
        self._auto_enabled = True      # re-fetch loaded layers as the map moves
        self._auto_timer = None        # debounce for extentsChanged
        self._tasks = {}               # key -> in-flight _FetchTask

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "SeaState US", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("SeaState US", self.action)
        # Keep the plugin's checkboxes in sync when layers are removed in QGIS.
        QgsProject.instance().layersRemoved.connect(self._on_layers_removed)
        # Debounced auto-refresh when the map view changes.
        self._auto_timer = QTimer()
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(700)
        self._auto_timer.timeout.connect(self._auto_refresh)
        self.iface.mapCanvas().extentsChanged.connect(self._schedule_auto_refresh)

    def unload(self):
        try:
            QgsProject.instance().layersRemoved.disconnect(self._on_layers_removed)
        except (TypeError, RuntimeError):
            pass
        try:
            self.iface.mapCanvas().extentsChanged.disconnect(self._schedule_auto_refresh)
        except (TypeError, RuntimeError):
            pass
        if self._auto_timer is not None:
            self._auto_timer.stop()
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
            "Units: feet. Coastal stations use the MLLW tidal datum; Great "
            "Lakes stations use the IGLD datum.\n"
            "Coverage: U.S. coasts, the Great Lakes, and territories.\n\n"
            "Source: NOAA Center for Operational Oceanographic Products and "
            "Services (CO-OPS)."),
        "predictions": (
            "Tide predictions — NOAA CO-OPS",
            "Predicted high- and low-tide times and heights (the 'hi/lo' product), "
            "computed from each station's harmonic constituents.\n\n"
            "Units: feet, MLLW datum.\n"
            "Coverage: U.S. coastal tide stations. Not available for the Great "
            "Lakes, which have no astronomical tides.\n\n"
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
            "U.S. coastal data from NOAA — observed water levels, tide "
            "predictions, and buoy readings. Zoom to a U.S. coast, tick a "
            "layer to load it, then Plot to chart it over time."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        sources = QGroupBox("Layers")
        s_layout = QVBoxLayout(sources)
        self.cb_water_level = QCheckBox("Water levels — measured tide-gauge readings")
        self.cb_water_level.setToolTip("Observed 6-minute water level (NOAA CO-OPS).")
        self.cb_predictions = QCheckBox("Tide predictions — daily highs and lows")
        self.cb_predictions.setToolTip("Predicted high/low tides (NOAA CO-OPS).")
        self.cb_ndbc = QCheckBox("Ocean buoys — wind, waves and temperature")
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

        self.cb_autoload = QCheckBox("Auto-load when the map moves")
        self.cb_autoload.setChecked(self._auto_enabled)
        self.cb_autoload.setToolTip(
            "Re-fetch the loaded layers automatically after you pan or zoom "
            "(a moment after you stop). Turn off if it feels heavy.")
        self.cb_autoload.toggled.connect(self._set_autoload)
        layout.addWidget(self.cb_autoload)

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

    def _set_autoload(self, enabled):
        self._auto_enabled = enabled
        if enabled:
            self._schedule_auto_refresh()

    def _schedule_auto_refresh(self):
        """Debounce map-move events: restart the timer, fire once things settle."""
        if not self._auto_enabled or not self._source_layers:
            return
        if self._auto_timer is not None:
            self._auto_timer.start()

    def _auto_refresh(self):
        if not self._auto_enabled:
            return
        for key in list(self._source_layers.keys()):
            self._load_source(key, quiet=True)

    def _load_source(self, key, quiet=False):
        """Fetch a source's data on a background thread; build layers when done."""
        bbox = self._canvas_bbox_wgs84()
        begin, end = self._dates()
        self._log(f"Load {key}: bbox={bbox} dates={begin}-{end}")

        def fetch():  # runs on a worker thread — network only, no GUI
            problems = []
            if key == "water_level":
                data = self._fetch_water_level(bbox, begin, end, problems)
            elif key == "predictions":
                data = self._fetch_predictions(bbox, begin, end, problems)
            elif key == "ndbc":
                data = self._fetch_ndbc(bbox, begin, end, problems)
            else:
                data = None
            return data, problems

        task = _FetchTask(
            f"SeaState: load {key}", fetch,
            lambda t, ok, k=key, q=quiet: self._on_fetched(k, t, ok, q))
        self._tasks[key] = task
        QgsApplication.taskManager().addTask(task)

    def _on_fetched(self, key, task, ok, quiet):
        """Runs on the main thread once the background fetch finishes."""
        if self._tasks.get(key) is not task:
            return  # a newer request superseded this one
        self._tasks.pop(key, None)

        bar = self.iface.messageBar()
        if task.error:
            self._log(f"{key} fetch failed: {task.error}", Qgis.Warning)
            if not quiet:
                bar.pushWarning("SeaState", f"{key}: {task.error}")
            return

        built = self._build_layers(key, task.data)
        problems = task.problems or []

        # Replace any previous layers for this source.
        if key in self._source_layers:
            self._unload_source(key, reconfigure=False)

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
            if not quiet:
                label = self.LAYER_INFO[key][0].split(" — ")[0]
                note = f"Loaded {label} ({len(built)} layer(s))."
                if problems:
                    note += f" {len(problems)} issue(s) — see Log Messages (SeaState)."
                bar.pushSuccess("SeaState", note)
        elif quiet:
            # Auto-refresh found nothing here — stay registered (and ticked) so
            # panning back to data reloads it.
            self._source_layers[key] = []
        else:
            self._set_checkbox(key, False)
            bar.pushWarning("SeaState", problems[0] if problems
                            else "Nothing found in the current view / date range.")

    def _build_layers(self, key, data):
        """Main-thread construction of QgsVectorLayers from fetched data."""
        if not data:
            return []
        if key == "water_level":
            return [layers.build_water_level_layer(rows, s) for s, rows in data]
        if key == "predictions":
            return [layers.build_predictions_layer(rows, s) for s, rows in data]
        if key == "ndbc":
            return [layers.build_ndbc_timeseries_layer(data)]
        return []

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

    def _fetch_water_level(self, bbox, begin, end, problems):
        """Worker-thread fetch. Returns [(station, rows), ...]."""
        stations = self._coops_stations(bbox, problems)
        self._log(f"CO-OPS stations in view: {len(stations)}")
        if not stations:
            problems.append("No CO-OPS tide-gauge stations in the current view.")
            return []
        out = []
        for s in stations[:MAX_COOPS_STATIONS]:
            datum = "IGLD" if s.get("greatlakes") else "MLLW"  # Great Lakes are non-tidal
            try:
                rows = coops.water_level(s["id"], begin, end, datum=datum)
                if rows:
                    out.append(({**s, "datum": datum}, rows))
                else:
                    self._log(f"No water level for {s['id']} {s['name']}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"water level {s['name']}: {exc}")
                self._log(f"water level {s['id']} FAILED: {exc}", Qgis.Warning)
        return out

    def _fetch_predictions(self, bbox, begin, end, problems):
        """Worker-thread fetch. Returns [(station, rows), ...].

        Great Lakes stations are non-tidal (no predictions), so they're skipped.
        """
        stations = self._coops_stations(bbox, problems)
        self._log(f"CO-OPS stations in view: {len(stations)}")
        if not stations:
            problems.append("No CO-OPS tide-gauge stations in the current view.")
            return []
        coastal = [s for s in stations if not s.get("greatlakes")]
        if not coastal:
            problems.append("Tide predictions aren't available for Great Lakes "
                            "stations (they have no astronomical tides).")
            return []
        out = []
        for s in coastal[:MAX_COOPS_STATIONS]:
            try:
                rows = coops.predictions(s["id"], begin, end)
                if rows:
                    out.append((s, rows))
                else:
                    self._log(f"No predictions for {s['id']} {s['name']}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"predictions {s['name']}: {exc}")
                self._log(f"predictions {s['id']} FAILED: {exc}", Qgis.Warning)
        return out

    def _fetch_ndbc(self, bbox, begin, end, problems):
        """Worker-thread fetch. Returns a flat list of buoy reading records."""
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
        return records

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
                series.extend(plot.layer_series_list(layer))
        if not series:
            bar.pushWarning("SeaState", "No plottable values in the loaded layers.")
            return
        series.sort(key=lambda s: s[0])
        max_charts = 12
        truncated = len(series) - max_charts
        series = series[:max_charts]
        try:
            dlg = plot.show_time_series(
                self.iface.mainWindow(), "SeaState US — readings over time", series)
        except plot.PlottingUnavailable as exc:
            bar.pushWarning("SeaState", str(exc))
            self._log(f"Plot failed: {exc}", Qgis.Warning)
            return
        self._plot_dialogs.append(dlg)
        if truncated > 0:
            bar.pushInfo("SeaState", f"Showing 12 charts; {truncated} more not "
                         "shown — zoom in or load fewer stations.")
