"""Dialog for configuring a new plot -- channels only (device is chosen on the plot itself)."""

import logging
from typing import List, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class AddPlotDialog(QDialog):
    """Dialog to configure a new plot widget.

    The user picks which channels to display.  Device selection
    happens via the plot's own dropdown.
    """

    def __init__(
        self,
        channels: List[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Plot")
        self.setMinimumWidth(300)

        self._channel_names = list(channels)
        self._channel_checkboxes: List[QCheckBox] = []

        self._build_ui()

    @property
    def selected_channels(self) -> List[str]:
        return [cb.text() for cb in self._channel_checkboxes if cb.isChecked()]

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        channels_group = QGroupBox("Channels")
        channels_layout = QVBoxLayout(channels_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        channel_container = QWidget()
        channel_form = QVBoxLayout(channel_container)
        channel_form.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(channel_container)

        for ch in self._channel_names:
            cb = QCheckBox(ch)
            cb.setChecked(True)
            self._channel_checkboxes.append(cb)
            channel_form.addWidget(cb)

        channels_layout.addWidget(scroll)
        layout.addWidget(channels_group)

        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
