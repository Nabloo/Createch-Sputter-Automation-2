"""GUI layer – PySide6 desktop application with dockable panels."""

from src.gui.main_window import MainWindow
from src.gui.dock_manager import DockManager
from src.gui.theme import apply_dark_theme

__all__ = ["MainWindow", "DockManager", "apply_dark_theme"]
