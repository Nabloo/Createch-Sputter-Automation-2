"""Main application window with dock system, toolbar, and status bar."""

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QStatusBar,
    QToolBar,
    QWidget,
)

from src.gui.dock_manager import DockManager
from src.gui.theme import apply_dark_theme

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Top-level window for the Sputter Automation application.

    Provides:
    - Menu bar (File, View, Help)
    - Toolbar with common actions
    - Status bar showing system state
    - Dock-managed panels for plots, device controls, etc.

    Device panels and plot widgets are added later via
    :meth:`DockManager.add_panel`.
    """

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self.setWindowTitle("Sputter Automation")
        self.resize(1280, 800)

        # ---- Theme ----
        apply_dark_theme(app)

        # ---- Dock manager ----
        self.dock_manager = DockManager(self)

        # ---- Central placeholder ----
        central = QWidget()
        central.setMinimumSize(200, 200)
        self.setCentralWidget(central)

        # ---- UI components ----
        self._build_menu_bar()
        self._build_toolbar()
        self._build_status_bar()

        # ---- Status update timer ----
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(500)  # refresh every 500 ms

        logger.info("MainWindow initialized")

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menu_bar(self) -> None:
        menu_bar: QMenuBar = self.menuBar()

        # ---- File ----
        file_menu = menu_bar.addMenu("&File")

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.setStatusTip("Exit the application")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # ---- View ----
        view_menu = menu_bar.addMenu("&View")

        self._toggle_toolbar_action = QAction("Toggle &Toolbar", self)
        self._toggle_toolbar_action.setCheckable(True)
        self._toggle_toolbar_action.setChecked(True)
        self._toggle_toolbar_action.triggered.connect(self._on_toggle_toolbar)
        view_menu.addAction(self._toggle_toolbar_action)

        self._toggle_statusbar_action = QAction("Toggle &Status Bar", self)
        self._toggle_statusbar_action.setCheckable(True)
        self._toggle_statusbar_action.setChecked(True)
        self._toggle_statusbar_action.triggered.connect(self._on_toggle_statusbar)
        view_menu.addAction(self._toggle_statusbar_action)

        view_menu.addSeparator()

        reset_layout_action = QAction("&Reset Layout", self)
        reset_layout_action.setStatusTip("Restore the default dock layout")
        reset_layout_action.triggered.connect(self._on_reset_layout)
        view_menu.addAction(reset_layout_action)

        # ---- Help ----
        help_menu = menu_bar.addMenu("&Help")

        about_action = QAction("&About", self)
        about_action.setStatusTip("About Sputter Automation")
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        self._toolbar = QToolBar("Main Toolbar")
        self._toolbar.setObjectName("MainToolBar")
        self._toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, self._toolbar)

        # Placeholder actions – wired up when acquisition is running
        self._start_action = QAction("\u25b6  Start", self)
        self._start_action.setStatusTip("Start data acquisition")
        self._start_action.setEnabled(False)
        self._toolbar.addAction(self._start_action)

        self._stop_action = QAction("\u25a0  Stop", self)
        self._stop_action.setStatusTip("Stop data acquisition")
        self._stop_action.setEnabled(False)
        self._toolbar.addAction(self._stop_action)

        self._toolbar.addSeparator()

        self._connect_action = QAction("\u26a1  Connect All", self)
        self._connect_action.setStatusTip("Connect to all configured devices")
        self._connect_action.setEnabled(False)
        self._toolbar.addAction(self._connect_action)

        self._disconnect_action = QAction("\u23fb  Disconnect All", self)
        self._disconnect_action.setStatusTip("Disconnect all devices")
        self._disconnect_action.setEnabled(False)
        self._toolbar.addAction(self._disconnect_action)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _build_status_bar(self) -> None:
        self._status_bar: QStatusBar = self.statusBar()

        self._status_connected = QLabel("Devices: 0")
        self._status_connected.setMinimumWidth(100)
        self._status_bar.addPermanentWidget(self._status_connected)

        self._status_acq = QLabel("Idle")
        self._status_acq.setMinimumWidth(80)
        self._status_bar.addPermanentWidget(self._status_acq)

    def _refresh_status(self) -> None:
        """Periodically refresh status bar indicators."""
        pass  # wired up later when device manager / engine are connected

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_toggle_toolbar(self, checked: bool) -> None:
        self._toolbar.setVisible(checked)

    def _on_toggle_statusbar(self, checked: bool) -> None:
        self._status_bar.setVisible(checked)

    def _on_reset_layout(self) -> None:
        """Remove all docks and restore defaults."""
        for dock_id in self.dock_manager.panel_ids():
            self.dock_manager.remove_panel(dock_id)
        logger.info("Dock layout reset")

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About Sputter Automation",
            "<h3>Sputter Automation</h3>"
            "<p>Modular measurement and control system for sputter "
            "deposition processes.</p>"
            "<p>Built with PySide6 and pyqtgraph.</p>",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Save layout before closing."""
        self.dock_manager.save_layout()
        logger.info("MainWindow closing – layout saved")
        super().closeEvent(event)
