"""Application entry point for Sputter Automation."""

import logging
import os
import sys

# Ensure project root is on sys.path for package imports
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from PySide6.QtWidgets import QApplication

from src.config import load_config, DEFAULT_CONFIG_PATH
from src.gui.main_window import MainWindow

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Configure root logger."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    """Application entry point."""
    setup_logging()
    logger.info("Sputter Automation starting...")

    config_path = DEFAULT_CONFIG_PATH
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    config = load_config(config_path)
    num_devices = len(config.get("devices", []))
    logger.info("Configuration loaded from %s (%d device(s) configured)", config_path, num_devices)

    # ---- Launch GUI ----
    app = QApplication(sys.argv)
    app.setApplicationName("SputterAutomation")
    app.setOrganizationName("SputterAutomation")

    window = MainWindow(app)
    window.show()

    # Restore persisted dock layout
    window.dock_manager.restore_layout()

    logger.info("Main window shown – entering event loop")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
