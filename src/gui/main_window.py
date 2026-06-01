"""Main application window with dock system, toolbar, and status bar."""

import logging
import os
import shutil
import sys

from PySide6.QtCore import Qt, QProcess, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateTimeEdit,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.dock_manager import DockManager
from src.gui.theme import apply_dark_theme, apply_light_theme
from src.config import BACKUP_CONFIG_PATH, DEFAULT_CONFIG_PATH

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

    # Thread-safe signal for error display.  Emitting from any thread
    # will invoke ``show_error`` on the GUI thread automatically.
    error_occurred = Signal(str)

    def __init__(self, app: QApplication) -> None:
        super().__init__()

        # Wire the thread-safe error signal to the slots on the GUI thread
        self.error_occurred.connect(self.show_error)
        self._app = app
        self.setWindowTitle("Sputter Automation")
        self.resize(1280, 800)

        # ---- Theme ----
        apply_dark_theme(app)

        # ---- Dock manager ----
        self.dock_manager = DockManager(self)

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

        self._logdir_action = QAction("Choose &Log Directory…", self)
        self._logdir_action.setStatusTip("Select where CSV log files are saved")
        file_menu.addAction(self._logdir_action)

        file_menu.addSeparator()

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

        self._theme_action = QAction("&Dark Theme", self)
        self._theme_action.setCheckable(True)
        self._theme_action.setChecked(True)
        self._theme_action.setStatusTip("Toggle between dark and light theme")
        self._theme_action.triggered.connect(self._on_toggle_theme)
        view_menu.addAction(self._theme_action)

        view_menu.addSeparator()

        self._gap_action = QAction("&Gap Threshold…", self)
        self._gap_action.setStatusTip("Set the gap-detection threshold for all plots")
        self._gap_action.triggered.connect(self._on_gap_threshold_dialog)
        view_menu.addAction(self._gap_action)

        view_menu.addSeparator()

        self._reset_action = QAction("&Reset to Defaults …", self)
        self._reset_action.setStatusTip("Restore default configuration and restart")
        self._reset_action.triggered.connect(self._on_reset_defaults)
        view_menu.addAction(self._reset_action)

        # ---- Devices ----
        devices_menu = menu_bar.addMenu("&Devices")

        self._connect_all_menu_action = QAction("\u26a1  Connect &All", self)
        self._connect_all_menu_action.setStatusTip("Connect to all configured devices")
        self._connect_all_menu_action.setEnabled(False)
        devices_menu.addAction(self._connect_all_menu_action)

        self._disconnect_all_menu_action = QAction("\u23fb  &Disconnect All", self)
        self._disconnect_all_menu_action.setStatusTip("Disconnect all devices")
        self._disconnect_all_menu_action.setEnabled(False)
        devices_menu.addAction(self._disconnect_all_menu_action)

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
        from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

        # ---- Single toolbar with a two-row container widget ----
        self._toolbar = QToolBar("Main Toolbar")
        self._toolbar.setObjectName("MainToolBar")
        self._toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, self._toolbar)

        # Container widget with two rows
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # ---- Row 1: core actions ----
        row1 = QWidget()
        row1_layout = QHBoxLayout(row1)
        row1_layout.setContentsMargins(4, 2, 4, 2)
        row1_layout.setSpacing(2)

        # Placeholder actions – wired up when acquisition is running
        self._start_action = QAction("\u25b6  Start", self)
        self._start_action.setStatusTip("Start data acquisition")
        self._start_action.setEnabled(False)
        start_btn = QPushButton("\u25b6  Start")
        start_btn.setEnabled(False)
        start_btn.setFlat(True)
        start_btn.clicked.connect(self._start_action.trigger)
        self._start_action.changed.connect(
            lambda: start_btn.setEnabled(self._start_action.isEnabled())
        )
        row1_layout.addWidget(start_btn)
        self._start_btn = start_btn

        self._stop_action = QAction("\u25a0  Stop", self)
        self._stop_action.setStatusTip("Stop data acquisition")
        self._stop_action.setEnabled(False)
        stop_btn = QPushButton("\u25a0  Stop")
        stop_btn.setEnabled(False)
        stop_btn.setFlat(True)
        stop_btn.clicked.connect(self._stop_action.trigger)
        self._stop_action.changed.connect(
            lambda: stop_btn.setEnabled(self._stop_action.isEnabled())
        )
        row1_layout.addWidget(stop_btn)
        self._stop_btn = stop_btn

        sep1 = QLabel(" ")
        sep1.setFixedWidth(8)
        row1_layout.addWidget(sep1)

        self._add_plot_action = QAction("\ud83d\udcca  Add Plot", self)
        self._add_plot_action.setStatusTip("Add a new plot widget")
        self._add_plot_action.setEnabled(False)
        add_btn = QPushButton("\ud83d\udcca  Add Plot")
        add_btn.setEnabled(False)
        add_btn.setFlat(True)
        add_btn.clicked.connect(self._add_plot_action.trigger)
        self._add_plot_action.changed.connect(
            lambda: add_btn.setEnabled(self._add_plot_action.isEnabled())
        )
        row1_layout.addWidget(add_btn)
        self._add_plot_btn = add_btn

        self._clear_all_action = QAction("\u267b  Clear All", self)
        self._clear_all_action.setStatusTip("Clear data from all plots")
        self._clear_all_action.setEnabled(False)
        clear_btn = QPushButton("\u267b  Clear All")
        clear_btn.setEnabled(False)
        clear_btn.setFlat(True)
        clear_btn.clicked.connect(self._clear_all_action.trigger)
        self._clear_all_action.changed.connect(
            lambda: clear_btn.setEnabled(self._clear_all_action.isEnabled())
        )
        row1_layout.addWidget(clear_btn)
        self._clear_all_btn = clear_btn

        # X-axis mode toggle (Seconds / HH:MM:SS) — always visible
        xlabel = QLabel("X-axis:")
        xlabel.setStyleSheet("padding: 0 2px 0 6px;")
        row1_layout.addWidget(xlabel)

        self._xaxis_combo = QComboBox()
        self._xaxis_combo.addItems(["Seconds", "HH:MM:SS"])
        self._xaxis_combo.setMinimumWidth(80)
        self._xaxis_combo.setToolTip("X-axis display mode (live and log viewer)")
        row1_layout.addWidget(self._xaxis_combo)

        sep2 = QLabel(" ")
        sep2.setFixedWidth(8)
        row1_layout.addWidget(sep2)

        # History window spinner (applies to all plots)
        history_label = QLabel("  History:")
        row1_layout.addWidget(history_label)

        self._history_spin = QSpinBox()
        self._history_spin.setRange(0, 3600*24)
        #self._history_spin.setSpecialValueText("All")
        self._history_spin.setSuffix(" s")
        self._history_spin.setToolTip("Rolling history window (0 = show everything)")
        self._history_spin.setMinimumWidth(80)
        row1_layout.addWidget(self._history_spin)

        # Gap threshold spinner (applies to all plots)
        gap_label = QLabel("  Gap:")
        row1_layout.addWidget(gap_label)

        self._gap_spin = QDoubleSpinBox()
        self._gap_spin.setRange(0.1, 3600.0)
        self._gap_spin.setValue(60.0)
        self._gap_spin.setSingleStep(5.0)
        self._gap_spin.setSuffix(" s")
        self._gap_spin.setToolTip("Gap threshold — breaks the line when data points are farther apart")
        self._gap_spin.setMinimumWidth(80)
        row1_layout.addWidget(self._gap_spin)

        row1_layout.addStretch()
        container_layout.addWidget(row1)

        # ---- Row 2: mode / log controls ----
        row2 = QWidget()
        row2_layout = QHBoxLayout(row2)
        row2_layout.setContentsMargins(4, 2, 4, 2)
        row2_layout.setSpacing(2)

        # Mode toggle (Live / View Log)
        self._mode_toggle = QPushButton("\u26ab  View Log")
        self._mode_toggle.setCheckable(True)
        self._mode_toggle.setToolTip("Switch between live data and log viewer")
        self._mode_toggle.setMinimumWidth(80)
        self._mode_toggle.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: #ffffff;"
            " border: 1px solid #555; border-radius: 3px; padding: 2px 8px;"
            " font-weight: bold; }"
            "QPushButton:checked { background-color: #e65100; }"
        )
        row2_layout.addWidget(self._mode_toggle)

        # Log controls (hidden in Live mode)
        self._load_btn = QPushButton("\ud83d\udcc2  Load…")
        self._load_btn.setToolTip("Open a CSV log file")
        self._load_btn.setStyleSheet(
            "QPushButton { padding: 2px 8px; }"
        )
        row2_layout.addWidget(self._load_btn)

        self._file_label = QLabel("No file")
        self._file_label.setMinimumWidth(140)
        self._file_label.setStyleSheet("color: #aaa; padding: 0 4px;")
        self._file_label.setToolTip("Currently loaded log file")
        row2_layout.addWidget(self._file_label)

        from_label = QLabel("From:")
        from_label.setStyleSheet("padding: 0 2px 0 6px;")
        row2_layout.addWidget(from_label)
        self._from_dt = QDateTimeEdit()
        self._from_dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._from_dt.setCalendarPopup(True)
        self._from_dt.setMinimumWidth(170)
        self._from_dt.setToolTip("Start of displayed time range")
        row2_layout.addWidget(self._from_dt)

        to_label = QLabel("To:")
        to_label.setStyleSheet("padding: 0 2px 0 6px;")
        row2_layout.addWidget(to_label)
        self._to_dt = QDateTimeEdit()
        self._to_dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._to_dt.setCalendarPopup(True)
        self._to_dt.setMinimumWidth(170)
        self._to_dt.setToolTip("End of displayed time range")
        row2_layout.addWidget(self._to_dt)

        self._log_controls = [
            self._load_btn,
            self._file_label,
            from_label,
            self._from_dt,
            to_label,
            self._to_dt,
        ]
        # Hidden by default (live mode)
        for w in self._log_controls:
            w.setVisible(False)

        sep3 = QLabel(" ")
        sep3.setFixedWidth(8)
        row2_layout.addWidget(sep3)

        row2_layout.addStretch()
        container_layout.addWidget(row2)

        self._toolbar.addWidget(container)

    def _on_gap_threshold_dialog(self) -> None:
        """Open a dialog to set the gap threshold for all plots."""
        from PySide6.QtWidgets import QInputDialog
        current = self._gap_spin.value()
        value, ok = QInputDialog.getDouble(
            self,
            "Gap Threshold",
            "Break plot lines when data points are farther apart than (s):",
            current,
            0.1, 3600.0, 1,
        )
        if ok:
            self._gap_spin.setValue(value)

    def set_log_controls_visible(self, visible: bool) -> None:
        """Show or hide the log-viewer controls in the toolbar."""
        for w in self._log_controls:
            w.setVisible(visible)

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

        self._status_error = QLabel("")
        self._status_error.setObjectName("statusError")
        self._status_error.setStyleSheet(
            "color: #ff6b6b; font-weight: bold; padding: 0 8px;"
        )
        self._status_error.setVisible(False)
        self._status_bar.addPermanentWidget(self._status_error)

        # Auto-clear timer for error messages
        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self.clear_error)

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

    def _on_toggle_theme(self, checked: bool) -> None:
        """Switch between dark and light Fusion themes."""
        if checked:
            apply_dark_theme(self._app)
            self._theme_action.setText("&Dark Theme")
        else:
            apply_light_theme(self._app)
        # Persist theme preference immediately (not just on quit)
        if hasattr(self, '_config'):
            self._config.setdefault("gui", {})["theme"] = "dark" if checked else "light"
        # Propagate theme to all plot widgets
        for panel_id in self.dock_manager.panel_ids():
            dock = self.dock_manager.panel(panel_id)
            if dock and hasattr(dock.widget(), 'set_dark_mode'):
                dock.widget().set_dark_mode(checked)
        logger.info("Theme switched to %s", "dark" if checked else "light")

    def set_config(self, config: dict) -> None:
        """Store a reference to the app config for theme persistence."""
        self._config = config

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About Sputter Automation",
            "<h3>Sputter Automation</h3>"
            "<p>Modular measurement and control system for sputter "
            "deposition processes.</p>"
            "<p>Built with PySide6 and pyqtgraph.</p>",
        )

    def _on_reset_defaults(self) -> None:
        """Restore backup_config.json over config.json and restart."""
        reply = QMessageBox.question(
            self,
            "Reset to Defaults",
            "This will restore the startup configuration and restart "
            "the application.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if not os.path.exists(BACKUP_CONFIG_PATH):
            QMessageBox.warning(
                self,
                "Reset to Defaults",
                f"Backup configuration not found at\n{BACKUP_CONFIG_PATH}",
            )
            return

        # Copy backup over the live config file
        try:
            shutil.copy(BACKUP_CONFIG_PATH, DEFAULT_CONFIG_PATH)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Reset Failed",
                f"Could not restore configuration:\n{exc}",
            )
            return
        logger.info("Restored default configuration from %s", BACKUP_CONFIG_PATH)

        # Disconnect the quit handler so it doesn't overwrite the file
        # with the (now-stale) runtime config before we restart.
        app = QApplication.instance()
        if app is not None:
            try:
                app.aboutToQuit.disconnect()
            except (RuntimeError, TypeError):
                pass

        # Launch a fresh instance and kill this one
        ok = QProcess.startDetached(sys.executable, sys.argv)
        if not ok:
            logger.error("Failed to restart application via %s", sys.executable)
            QMessageBox.warning(
                self,
                "Restart Failed",
                "Could not start a new instance. Please restart the application manually.",
            )
            return
        QApplication.quit()

    def show_error(self, message: str, timeout_ms: int = 20000) -> None:
        """Display an error message in the status bar.

        The message auto-clears after *timeout_ms* milliseconds.
        A subsequent call restarts the timer so transient errors
        don't disappear too quickly.
        """
        self._status_error.setText(message)
        self._status_error.setVisible(True)
        self._error_timer.stop()
        if timeout_ms > 0:
            self._error_timer.start(timeout_ms)

    def clear_error(self) -> None:
        """Hide and clear the error label."""
        self._status_error.setText("")
        self._status_error.setVisible(False)

    def temporary_status(self, message: str, timeout_ms: int = 3000) -> None:
        """Show a temporary message in the status bar's temporary area."""
        self._status_bar.showMessage(message, timeout_ms)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Save layout before closing."""
        self.dock_manager.save_layout()
        logger.info("MainWindow closing – layout saved")
        super().closeEvent(event)
