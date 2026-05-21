"""Application entry point for Sputter Automation."""

import logging
import os
import sys
from typing import Optional

# Ensure project root is on sys.path for package imports
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from PySide6.QtWidgets import QApplication

from src.config import (
    DEFAULT_CONFIG_PATH,
    get_window_geometry,
    load_config,
    save_config,
    set_window_geometry,
)
from src.gui.main_window import MainWindow
from src.gui.theme import apply_dark_theme, apply_light_theme
from src.devices.device_manager import DeviceManager

logger = logging.getLogger(__name__)

# Module-level references so the quit handler can access them.
_config: dict = {}
_config_path: str = DEFAULT_CONFIG_PATH
_window: Optional[MainWindow] = None
_device_manager: Optional[DeviceManager] = None


def setup_logging() -> None:
    """Configure root logger."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _on_about_to_quit() -> None:
    """Persist window geometry and save configuration before exit."""
    global _config, _config_path, _window, _device_manager
    try:
        if _device_manager is not None:
            _device_manager.shutdown()
        if _window is not None:
            # Persist theme preference
            if hasattr(_window, '_theme_action'):
                _config.setdefault("gui", {})["theme"] = "dark" if _window._theme_action.isChecked() else "light"
            if _window.isMaximized():
                geom = _window.normalGeometry()
                set_window_geometry(
                    _config,
                    geom.x(), geom.y(),
                    geom.width(), geom.height(),
                    maximized=True,
                )
            else:
                set_window_geometry(
                    _config,
                    _window.x(), _window.y(),
                    _window.width(), _window.height(),
                    maximized=False,
                )
        save_config(_config, _config_path)
        logger.info("Configuration saved to %s", _config_path)
    except Exception:
        logger.exception("Failed to save configuration")


def main() -> None:
    """Application entry point."""
    global _config, _config_path, _window

    setup_logging()
    logger.info("Sputter Automation starting...")

    if len(sys.argv) > 1:
        _config_path = sys.argv[1]

    _config = load_config(_config_path)
    num_devices = len(_config.get("devices", []))
    logger.info("Configuration loaded from %s (%d device(s) configured)", _config_path, num_devices)

    # ---- Launch GUI ----
    app = QApplication(sys.argv)
    app.setApplicationName("SputterAutomation")
    app.setOrganizationName("SputterAutomation")

    _window = MainWindow(app)
    _window.set_config(_config)

    # Apply saved theme preference (defaults to dark)
    theme = _config.get("gui", {}).get("theme", "dark")
    if theme == "light":
        apply_light_theme(app)
        _window._theme_action.setChecked(False)

    # ---- Create DeviceManager (wires everything: devices, engine, store, GUI) ----
    _device_manager = DeviceManager(_config, _window)

    # Restore window geometry
    x, y, width, height, maximized = get_window_geometry(_config)
    _window.resize(width, height)
    _window.move(x, y)
    if maximized:
        _window.showMaximized()
    else:
        _window.show()
    logger.info("Window geometry restored: %dx%d at (%d, %d)", width, height, x, y)

    # Restore persisted dock layout
    _window.dock_manager.restore_layout()

    # Save config when the app quits (handles geometry + config save)
    app.aboutToQuit.connect(_on_about_to_quit)

    logger.info("Main window shown – entering event loop")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
