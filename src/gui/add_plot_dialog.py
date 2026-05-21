"""Dialog for adding a new plot -- select device, channels, and title."""

import logging
from typing import Dict, List, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class AddPlotDialog(QDialog):
    """Dialog to configure a new plot widget.

    The user selects a device, chooses which channels to display, and
    optionally customises the title.
    """

    def __init__(
        self,
        device_ids: List[str],
        device_channels: Dict[str, List[str]],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Plot")
        self.setMinimumWidth(400)

        self._device_ids = list(device_ids)
        self._device_channels = dict(device_channels)
        self._channel_checkboxes: Dict[str, QCheckBox] = {}

        self._build_ui()
        if self._device_ids:
            self._on_device_changed(0)

    @property
    def selected_device_id(self) -> str:
        return self._device_combo.currentText()

    @property
    def selected_channels(self) -> List[str]:
        return [
            ch for ch, cb in self._channel_checkboxes.items()
            if cb.isChecked()
        ]

    @property
    def plot_title(self) -> str:
        t = self._title_edit.text().strip()
        return t or f"{self.selected_device_id} Plot"

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        device_group = QGroupBox("Device")
        device_layout = QFormLayout(device_group)

        self._device_combo = QComboBox()
        self._device_combo.addItems(self._device_ids)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        device_layout.addRow("Device:", self._device_combo)
        layout.addWidget(device_group)

        title_group = QGroupBox("Title")
        title_layout = QFormLayout(title_group)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Auto-generated from device ID")
        title_layout.addRow("Plot title:", self._title_edit)
        layout.addWidget(title_group)

        channels_group = QGroupBox("Channels")
        channels_layout = QVBoxLayout(channels_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        self._channel_container = QWidget()
        self._channel_form = QVBoxLayout(self._channel_container)
        self._channel_form.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self._channel_container)

        self._no_channels_label = QLabel("No channels available for this device.")
        self._no_channels_label.setVisible(False)
        self._channel_form.addWidget(self._no_channels_label)

        channels_layout.addWidget(scroll)
        layout.addWidget(channels_group)

        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_device_changed(self, index: int) -> None:
        for cb in self._channel_checkboxes.values():
            self._channel_form.removeWidget(cb)
            cb.deleteLater()
        self._channel_checkboxes.clear()

        device_id = self._device_combo.itemText(index) if index >= 0 else ""
        channels = self._device_channels.get(device_id, [])

        self._no_channels_label.setVisible(len(channels) == 0)

        for ch in channels:
            cb = QCheckBox(ch)
            cb.setChecked(True)
            self._channel_checkboxes[ch] = cb
            self._channel_form.addWidget(cb)
