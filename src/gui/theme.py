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

    Clean, warm light theme for well-lit environments — soft off-white
    background, gentle grey borders, and a crisp blue accent.
    """
    app.setStyle("Fusion")

    palette = QPalette()

    warm_white = QColor(248, 246, 242)       # soft off-white window bg
    card_bg    = QColor(255, 255, 255)       # pure white for input fields
    text       = QColor(45, 45, 48)          # near-black, not pure
    disabled_text = QColor(170, 168, 164)    # muted
    accent     = QColor(0, 103, 192)         # warm blue

    palette.setColor(QPalette.Window,          warm_white)
    palette.setColor(QPalette.WindowText,      text)
    palette.setColor(QPalette.Base,            card_bg)
    palette.setColor(QPalette.AlternateBase,   QColor(242, 240, 236))
    palette.setColor(QPalette.ToolTipBase,     QColor(255, 255, 220))
    palette.setColor(QPalette.ToolTipText,     text)

    palette.setColor(QPalette.Text,            text)
    palette.setColor(QPalette.Disabled, QPalette.Text,       disabled_text)
    palette.setColor(QPalette.Disabled, QPalette.WindowText, disabled_text)

    palette.setColor(QPalette.Button,          QColor(236, 234, 230))
    palette.setColor(QPalette.ButtonText,      text)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled_text)

    palette.setColor(QPalette.Highlight,          accent)
    palette.setColor(QPalette.HighlightedText,    QColor(255, 255, 255))
    palette.setColor(QPalette.Disabled, QPalette.HighlightedText, disabled_text)

    palette.setColor(QPalette.Link,         accent)
    palette.setColor(QPalette.LinkVisited,  accent.darker(120))

    palette.setColor(QPalette.BrightText, QColor(200, 50, 50))
    palette.setColor(QPalette.PlaceholderText, disabled_text)

    palette.setColor(QPalette.Light,     QColor(248, 246, 242))
    palette.setColor(QPalette.Midlight,  QColor(228, 226, 222))
    palette.setColor(QPalette.Dark,      QColor(190, 188, 184))
    palette.setColor(QPalette.Mid,       QColor(200, 198, 194))
    palette.setColor(QPalette.Shadow,    QColor(160, 158, 154))

    app.setPalette(palette)

    app.setStyleSheet("""
        QToolTip {
            border: 1px solid #c0bfbb;
            padding: 4px;
            border-radius: 3px;
            background-color: #ffffdc;
            color: #2d2d30;
        }
        QMainWindow::separator {
            width: 2px;
            height: 2px;
            background: #d2d0cc;
        }
        QPushButton {
            background-color: #eceaec;
            border: 1px solid #d2d0cc;
            border-radius: 4px;
            padding: 4px 12px;
            color: #2d2d30;
        }
        QPushButton:hover {
            background-color: #e0ded8;
            border-color: #0067c0;
        }
        QPushButton:pressed {
            background-color: #d4d2cc;
        }
        QPushButton:disabled {
            background-color: #f0eee8;
            border-color: #e0ded8;
            color: #b8b6b2;
        }
        QComboBox {
            background-color: #ffffff;
            border: 1px solid #d2d0cc;
            border-radius: 4px;
            padding: 3px 6px;
            color: #2d2d30;
        }
        QComboBox:hover {
            border-color: #0067c0;
        }
        QComboBox:disabled {
            background-color: #f5f4f0;
            border-color: #e0ded8;
            color: #b8b6b2;
        }
        QComboBox::drop-down {
            border: none;
            width: 20px;
        }
        QLineEdit {
            background-color: #ffffff;
            border: 1px solid #d2d0cc;
            border-radius: 4px;
            padding: 3px 6px;
            color: #2d2d30;
        }
        QLineEdit:focus {
            border-color: #0067c0;
        }
        QLineEdit:disabled {
            background-color: #f5f4f0;
            border-color: #e0ded8;
            color: #b8b6b2;
        }
        QCheckBox {
            spacing: 6px;
            color: #2d2d30;
        }
        QCheckBox:disabled {
            color: #b8b6b2;
        }
        QSpinBox, QDoubleSpinBox {
            background-color: #ffffff;
            border: 1px solid #d2d0cc;
            border-radius: 4px;
            padding: 3px 6px;
            color: #2d2d30;
        }
        QSpinBox:focus, QDoubleSpinBox:focus {
            border-color: #0067c0;
        }
        QSpinBox:disabled, QDoubleSpinBox:disabled {
            background-color: #f5f4f0;
            border-color: #e0ded8;
            color: #b8b6b2;
        }
        QDateTimeEdit {
            background-color: #ffffff;
            border: 1px solid #d2d0cc;
            border-radius: 4px;
            padding: 3px 6px;
            color: #2d2d30;
        }
        QDateTimeEdit:focus {
            border-color: #0067c0;
        }
        QDateTimeEdit:disabled {
            background-color: #f5f4f0;
            border-color: #e0ded8;
            color: #b8b6b2;
        }
        QStatusBar {
            background-color: #0067c0;
            color: #ffffff;
        }
        QStatusBar QLabel {
            color: #ffffff;
            padding: 2px 6px;
        }
        QToolBar {
            spacing: 4px;
            padding: 2px;
            border-bottom: 1px solid #d2d0cc;
        }
        QDockWidget {
            titlebar-close-icon: none;
            titlebar-normal-icon: none;
            color: #2d2d30;
        }
        QDockWidget::title {
            background-color: #f0eee8;
            padding: 4px 8px;
            border-bottom: 1px solid #d2d0cc;
        }
        QMenuBar {
            background-color: #f0eee8;
            border-bottom: 1px solid #d2d0cc;
            color: #2d2d30;
        }
        QMenuBar::item:selected {
            background-color: #d2d0cc;
        }
        QMenu {
            background-color: #ffffff;
            border: 1px solid #d2d0cc;
            color: #2d2d30;
        }
        QMenu::item:selected {
            background-color: #0067c0;
            color: #ffffff;
        }
    """)
