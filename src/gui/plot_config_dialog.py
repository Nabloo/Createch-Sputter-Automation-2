"""Configuration dialog for plot channels, colours, and history window."""

import logging
from typing import Dict, List, Optional

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

_COLOUR_PRESETS = [
    "#00bfff", "#ff6b6b", "#51cf66", "#ffd43b",
    "#cc5de8", "#ff922b", "#20c997", "#f06595",
    "#748ffc", "#94d82d", "#ff8787", "#4dabf7",
]


class PlotConfigDialog(QDialog):
    """Dialog for configuring plot channels, colours, and history window."""

    def __init__(
        self,
        channels: List[str],
        colours: Optional[Dict[str, str]] = None,
        visibility: Optional[Dict[str, bool]] = None,
        history_seconds: float = 60.0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Plot Channels")
        self.setMinimumWidth(400)

        self._channels = list(channels)
        self._colours = dict(colours) if colours else {}
        self._visibility = dict(visibility) if visibility else {}
        self._history_seconds = history_seconds

        self._checkboxes: Dict[str, QCheckBox] = {}
        self._colour_buttons: Dict[str, QPushButton] = {}

        self._build_ui()
        self._populate()

    @property
    def selected_channels(self) -> List[str]:
        return [ch for ch, cb in self._checkboxes.items() if cb.isChecked()]

    @property
    def selected_colours(self) -> List[str]:
        return [
            self._colours.get(ch, "#00bfff")
            for ch in self.selected_channels
        ]

    @property
    def channel_visibility(self) -> Dict[str, bool]:
        return {ch: cb.isChecked() for ch, cb in self._checkboxes.items()}

    @property
    def history_seconds(self) -> float:
        return float(self._history_spin.value())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        channels_group = QGroupBox("Channels")
        channels_layout = QVBoxLayout(channels_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self._channel_container = QWidget()
        self._channel_form = QFormLayout(self._channel_container)
        self._channel_form.setContentsMargins(0, 0, 0, 0)
        self._channel_form.setSpacing(4)
        scroll.setWidget(self._channel_container)

        channels_layout.addWidget(scroll)
        layout.addWidget(channels_group)

        history_group = QGroupBox("Display")
        history_layout = QFormLayout(history_group)

        self._history_spin = QSpinBox()
        self._history_spin.setRange(5, 3600)
        self._history_spin.setSuffix(" s")
        self._history_spin.setValue(int(self._history_seconds))
        self._history_spin.setToolTip("Rolling history window in seconds")
        history_layout.addRow("History window:", self._history_spin)

        layout.addWidget(history_group)

        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _populate(self) -> None:
        for i, ch in enumerate(self._channels):
            visible = self._visibility.get(ch, True)
            colour = self._colours.get(ch, _COLOUR_PRESETS[i % len(_COLOUR_PRESETS)])

            cb = QCheckBox(ch)
            cb.setChecked(visible)
            self._checkboxes[ch] = cb

            btn = QPushButton()
            btn.setFixedSize(32, 20)
            btn.setStyleSheet(
                f"background-color: {colour}; border: 1px solid #555; "
                f"border-radius: 3px;"
            )
            btn.setToolTip(f"Click to change colour for {ch}")
            btn.clicked.connect(lambda checked, c=ch: self._pick_colour(c))
            self._colour_buttons[ch] = btn

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(cb)
            row.addWidget(btn)
            row.addStretch()

            row_widget = QWidget()
            row_widget.setLayout(row)
            self._channel_form.addRow(row_widget)

    def _pick_colour(self, channel: str) -> None:
        current = QColor(self._colours.get(channel, "#00bfff"))
        colour = QColorDialog.getColor(current, self, f"Colour for {channel}")
        if colour.isValid():
            hex_str = colour.name()
            self._colours[channel] = hex_str
            btn = self._colour_buttons.get(channel)
            if btn:
                btn.setStyleSheet(
                    f"background-color: {hex_str}; border: 1px solid #555; "
                    f"border-radius: 3px;"
                )
