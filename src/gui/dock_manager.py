"""Dock widget manager – creates, arranges, and persists dockable panels."""

import logging
from typing import Dict, List, Optional

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QMainWindow,
    QWidget,
)

logger = logging.getLogger(__name__)


class DockManager:
    """Manages dock widgets for a QMainWindow.

    Panels are stored by unique ID and can be shown, hidden, or arranged
    by the user.  Layout state is persisted via QSettings.

    Usage::

        manager = DockManager(main_window)
        manager.add_panel("plot_1", "Pressure Plot", plot_widget)
        manager.restore_layout()
    """

    _SETTINGS_GROUP = "DockManager"
    _STATE_KEY = "window_state"

    def __init__(self, main_window: QMainWindow) -> None:
        self._window = main_window
        self._docks: Dict[str, QDockWidget] = {}
        self._settings = QSettings("SputterAutomation", "GUI")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_panel(
        self,
        panel_id: str,
        title: str,
        widget: QWidget,
        area: Qt.DockWidgetArea = Qt.RightDockWidgetArea,
        allowed_areas: Qt.DockWidgetAreas = Qt.AllDockWidgetAreas,
    ) -> QDockWidget:
        """Create a dock widget containing *widget* and add it to the window.

        Parameters
        ----------
        panel_id:
            Unique identifier used for layout persistence.
        title:
            Displayed in the dock title bar.
        widget:
            Widget to embed inside the dock.
        area:
            Initial dock area.
        allowed_areas:
            Bitmask of areas the user may drag the dock to.

        Returns
        -------
        The created QDockWidget.
        """
        if panel_id in self._docks:
            logger.warning("Dock panel %r already exists; replacing", panel_id)
            self.remove_panel(panel_id)

        dock = QDockWidget(title, self._window)
        dock.setObjectName(panel_id)
        dock.setWidget(widget)
        dock.setAllowedAreas(allowed_areas)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        self._window.addDockWidget(area, dock)
        self._docks[panel_id] = dock
        logger.debug("Added dock panel %r", panel_id)
        return dock

    def remove_panel(self, panel_id: str) -> None:
        """Remove and delete a dock panel by ID."""
        dock = self._docks.pop(panel_id, None)
        if dock is not None:
            self._window.removeDockWidget(dock)
            dock.deleteLater()

    def panel(self, panel_id: str) -> Optional[QDockWidget]:
        """Return the dock widget for *panel_id*, or None."""
        return self._docks.get(panel_id)

    def panel_ids(self) -> List[str]:
        """Return all registered panel IDs."""
        return list(self._docks.keys())

    # ------------------------------------------------------------------
    # Layout persistence
    # ------------------------------------------------------------------

    def save_layout(self) -> None:
        """Persist the current dock layout to QSettings."""
        state = self._window.saveState()
        self._settings.beginGroup(self._SETTINGS_GROUP)
        self._settings.setValue(self._STATE_KEY, state)
        self._settings.endGroup()
        logger.debug("Dock layout saved")

    def restore_layout(self) -> bool:
        """Restore a previously saved dock layout.

        Returns True if a saved state was found and applied.
        """
        self._settings.beginGroup(self._SETTINGS_GROUP)
        state: QByteArray = self._settings.value(self._STATE_KEY)
        self._settings.endGroup()
        if state and isinstance(state, QByteArray):
            self._window.restoreState(state)
            logger.info("Dock layout restored")
            return True
        logger.debug("No saved dock layout found")
        return False
