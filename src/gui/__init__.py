"""GUI layer – PySide6 desktop application with dockable panels."""

from src.gui.main_window import MainWindow
from src.gui.dock_manager import DockManager
from src.gui.theme import apply_dark_theme
from src.gui.plot_widget import PlotWidget
from src.gui.plot_config_dialog import PlotConfigDialog
from src.gui.device_panel import DevicePanel, ConnectionIndicator

__all__ = [
    "MainWindow", "DockManager", "apply_dark_theme",
    "PlotWidget", "PlotConfigDialog",
    "DevicePanel", "ConnectionIndicator",
]
