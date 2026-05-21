"""Dialog for configuring a new plot -- channels and title only."""

import logging
from typing import List, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class AddPlotDialog(QDialog):
    """Dialog to configure a new plot widget.

    The user picks which channels to display and optionally customises
    the title.  Device selection happens via the plot's own dropdown.
    """

    def __init__(
        self,
        channels: List[str],
        default_title: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Plot")
        self.setMinimumWidth(350)

        self._channel_names = list(channels)
        self._channel_checkboxes: List[QCheckBox] = []

        self._build_ui(default_title)

    @property
    def selected_channels(self) -> List[str]:
        return [cb.text() for cb in self._channel_checkboxes if cb.isChecked()]

    @property
    def plot_title(self) -> str:
        t = self._title_edit.text().strip()
        return t or "Plot"

    def _build_ui(self, default_title: str) -> None:
        layout = QVBoxLayout(self)

        title_group = QGroupBox("Title")
        title_layout = QFormLayout(title_group)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText(default_title or "Plot")
        title_layout.addRow("Plot title:", self._title_edit)
        layout.addWidget(title_group)

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
