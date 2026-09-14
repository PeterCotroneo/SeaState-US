"""SeaState — main plugin class.

Registers a toolbar button / menu entry that toggles a dock panel. The panel
UI is a placeholder for v1: source pickers, a date range, and a Load button.
Data loading is wired to the clients in ``seastate.clients`` as those land.
"""

from qgis.PyQt.QtWidgets import (
    QAction,
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

    def _on_load(self):
        # TODO: wire to seastate.clients.coops / seastate.clients.ndbc,
        # build memory layers, and register them with the Temporal Controller.
        self.iface.messageBar().pushInfo(
            "SeaState", "Loading is not wired up yet — scaffold only."
        )
