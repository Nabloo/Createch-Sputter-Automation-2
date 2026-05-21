"""Dark Fusion-style theme optimized for laboratory use."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_dark_theme(app: QApplication) -> None:
    """Apply a dark Fusion-style palette to *app*.

    Designed for high contrast in dim lab environments:
    - Dark backgrounds reduce glare and eye strain.
    - Bright accent colours for alerts and status indicators.
    - Comfortable text contrast without pure white.
    """
    app.setStyle("Fusion")

    palette = QPalette()

    # ---- Base colours ----
    dark_bg   = QColor(30, 30, 30)
    mid_bg    = QColor(45, 45, 48)
    light_bg  = QColor(55, 55, 58)
    text      = QColor(220, 220, 222)
    highlight = QColor(0, 120, 212)      # blue accent
    highlight_text = QColor(255, 255, 255)
    disabled_text  = QColor(128, 128, 128)

    # ---- Window & general ----
    palette.setColor(QPalette.Window,          dark_bg)
    palette.setColor(QPalette.WindowText,      text)
    palette.setColor(QPalette.Base,            QColor(37, 37, 38))
    palette.setColor(QPalette.AlternateBase,   mid_bg)
    palette.setColor(QPalette.ToolTipBase,     dark_bg)
    palette.setColor(QPalette.ToolTipText,     text)

    # ---- Text ----
    palette.setColor(QPalette.Text,            text)
    palette.setColor(QPalette.Disabled, QPalette.Text,       disabled_text)
    palette.setColor(QPalette.Disabled, QPalette.WindowText, disabled_text)

    # ---- Buttons ----
    palette.setColor(QPalette.Button,          mid_bg)
    palette.setColor(QPalette.ButtonText,      text)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled_text)

    # ---- Highlights ----
    palette.setColor(QPalette.Highlight,          highlight)
    palette.setColor(QPalette.HighlightedText,    highlight_text)
    palette.setColor(QPalette.Disabled, QPalette.HighlightedText, disabled_text)

    # ---- Links ----
    palette.setColor(QPalette.Link,         highlight)
    palette.setColor(QPalette.LinkVisited,  highlight.darker(120))

    # ---- Role-specific tweaks ----
    palette.setColor(QPalette.BrightText, QColor(255, 80, 80))   # red for errors
    palette.setColor(QPalette.PlaceholderText, disabled_text)

    # ---- Light role (used by some widgets for frame highlights) ----
    palette.setColor(QPalette.Light,  light_bg)
    palette.setColor(QPalette.Midlight, QColor(66, 66, 69))
    palette.setColor(QPalette.Dark,   QColor(25, 25, 27))
    palette.setColor(QPalette.Mid,    QColor(40, 40, 42))
    palette.setColor(QPalette.Shadow, QColor(15, 15, 17))

    app.setPalette(palette)

    # ---- Stylesheet fine-tuning ----
    app.setStyleSheet("""
        QToolTip {
            border: 1px solid #3c3c3e;
            padding: 4px;
            border-radius: 3px;
            background-color: #2d2d30;
            color: #dcdcde;
        }
        QMainWindow::separator {
            width: 2px;
            height: 2px;
            background: #3c3c3e;
        }
        QMenuBar {
            background-color: #2d2d30;
            border-bottom: 1px solid #3c3c3e;
        }
        QMenuBar::item:selected {
            background-color: #3c3c3e;
        }
        QMenu {
            background-color: #2d2d30;
            border: 1px solid #3c3c3e;
        }
        QMenu::item:selected {
            background-color: #0078d4;
        }
        QStatusBar {
            background-color: #0078d4;
            color: #ffffff;
        }
        QStatusBar QLabel {
            color: #ffffff;
            padding: 2px 6px;
        }
        QToolBar {
            background-color: #2d2d30;
            border-bottom: 1px solid #3c3c3e;
            spacing: 4px;
            padding: 2px;
        }
        QDockWidget {
            titlebar-close-icon: none;
            titlebar-normal-icon: none;
            color: #dcdcde;
        }
        QDockWidget::title {
            background-color: #2d2d30;
            padding: 4px 8px;
            border-bottom: 1px solid #3c3c3e;
        }
    """)


def apply_light_theme(app: QApplication) -> None:
    """Apply a light Fusion-style palette to *app*.

    Clean light theme for well-lit environments.
    """
    app.setStyle("Fusion")
    app.setPalette(app.style().standardPalette())

    app.setStyleSheet("""
        QToolTip {
            border: 1px solid #c0c0c0;
            padding: 4px;
            border-radius: 3px;
            background-color: #ffffff;
            color: #1e1e1e;
        }
        QMainWindow::separator {
            width: 2px;
            height: 2px;
            background: #c0c0c0;
        }
        QStatusBar {
            background-color: #0078d4;
            color: #ffffff;
        }
        QStatusBar QLabel {
            color: #ffffff;
            padding: 2px 6px;
        }
        QToolBar {
            spacing: 4px;
            padding: 2px;
        }
        QDockWidget {
            titlebar-close-icon: none;
            titlebar-normal-icon: none;
        }
        QDockWidget::title {
            padding: 4px 8px;
            border-bottom: 1px solid #c0c0c0;
        }
    """)
