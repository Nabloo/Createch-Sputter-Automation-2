"""Per-device panel showing live values, connection status, and controls."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Status indicator colours
_STATUS_COLOURS = {
    "connected": "#51cf66",
    "disconnected": "#ff6b6b",
    "connecting": "#ffd43b",
    "error": "#ff6b6b",
}


class ConnectionIndicator(QWidget):
    """Small coloured LED indicator for connection status."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._colour = _STATUS_COLOURS["disconnected"]
        self._update_style()

    def set_status(self, status: str) -> None:
        """Set the indicator colour by status name."""
        self._colour = _STATUS_COLOURS.get(status, "#888888")
        self._update_style()

    def _update_style(self) -> None:
        self.setStyleSheet(
            f"background-color: {self._colour}; "
            f"border-radius: 7px; "
            f"border: 1px solid #555;"
        )


class DevicePanel(QWidget):
    """Panel for a single device showing live values, status, and controls.

    Designed to be embedded in a QDockWidget via DockManager.

    Signals
    -------
    connect_requested(device_id: str)
        Emitted when the user clicks Connect.
    disconnect_requested(device_id: str)
        Emitted when the user clicks Disconnect.

    Usage::

        panel = DevicePanel("VCU-0", device_type="JEVAmet VCU", num_channels=2)
        dock = dock_manager.add_panel("device_VCU-0", "VCU-0", panel)

        # Wire to DataStore:
        store.subscribe(panel.push_data)
    """

    connect_requested = Signal(str)
    disconnect_requested = Signal(str)
    port_changed = Signal(str, str)         # device_id, new_port
    baudrate_changed = Signal(str, int)     # device_id, new_baudrate
    _data_arrived = Signal(str, float, object)

    def __init__(
        self,
        device_id: str,
        device_type: str = "Unknown",
        num_channels: int = 1,
        port: str = "",
        baudrate: int = 9600,
        available_ports: Optional[list] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._device_id = device_id
        self._device_type = device_type
        self._num_channels = num_channels
        self._connected = False

        # Per-channel value labels
        self._value_labels: Dict[str, QLabel] = {}

        self._build_ui(port, baudrate, available_ports or [])
        self._data_arrived.connect(self._on_data_arrived)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Baudrate presets
    # ------------------------------------------------------------------
    _BAUDRATES = [9600, 19200, 38400, 57600, 115200]

    def _build_ui(self, port: str, baudrate: int, available_ports: list) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ---- Header ----
        header = QHBoxLayout()

        self._indicator = ConnectionIndicator()
        header.addWidget(self._indicator)

        self._title_label = QLabel(self._device_id)
        self._title_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #dcdcde;"
        )
        header.addWidget(self._title_label)

        self._type_label = QLabel(f"({self._device_type})")
        self._type_label.setStyleSheet("color: #888;")
        header.addWidget(self._type_label)

        self._status_text = QLabel("Disconnected")
        self._status_text.setStyleSheet("color: #ff6b6b;")
        header.addWidget(self._status_text)

        header.addStretch()
        layout.addLayout(header)

        # ---- Port & Baudrate ----
        port_layout = QHBoxLayout()
        port_layout.setSpacing(8)

        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(100)
        self._populate_ports(available_ports, port)
        self._port_combo.currentTextChanged.connect(self._on_port_changed)
        port_layout.addWidget(QLabel("Port:"))
        port_layout.addWidget(self._port_combo, 1)

        self._baud_combo = QComboBox()
        for br in self._BAUDRATES:
            self._baud_combo.addItem(str(br), br)
        idx = self._baud_combo.findData(baudrate)
        if idx >= 0:
            self._baud_combo.setCurrentIndex(idx)
        self._baud_combo.currentIndexChanged.connect(self._on_baudrate_changed)
        port_layout.addWidget(QLabel("Baud:"))
        port_layout.addWidget(self._baud_combo)

        layout.addLayout(port_layout)

        # ---- Live values ----
        self._unit_label = QLabel("")
        self._unit_label.setStyleSheet("color: #888; font-size: 12px;")
        values_group = QGroupBox()
        values_group.setLayout(QVBoxLayout())
        values_group.layout().setContentsMargins(0, 0, 0, 0)

        values_layout = QVBoxLayout()
        values_layout.setSpacing(4)

        for ch in range(1, self._num_channels + 1):
            ch_box = QGroupBox(f"Channel {ch}")
            ch_form = QFormLayout(ch_box)
            ch_form.setSpacing(4)

            pressure_label = QLabel("--")
            pressure_label.setStyleSheet(
                "font-size: 16px; font-weight: bold; font-family: monospace;"
            )
            ch_form.addRow("Pressure:", pressure_label)
            self._value_labels[f"ch{ch}_pressure"] = pressure_label

            status_label = QLabel("--")
            ch_form.addRow("Status:", status_label)
            self._value_labels[f"ch{ch}_status"] = status_label

            values_layout.addWidget(ch_box)

        values_group.layout().addLayout(values_layout)

        layout.addWidget(values_group)

        # ---- Controls ----
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; "
            "padding: 6px 16px; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background-color: #388e3c; }"
            "QPushButton:disabled { background-color: #555; color: #888; }"
        )
        self._connect_btn.clicked.connect(
            lambda: self.connect_requested.emit(self._device_id)
        )
        controls.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; "
            "padding: 6px 16px; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background-color: #d32f2f; }"
            "QPushButton:disabled { background-color: #555; color: #888; }"
        )
        self._disconnect_btn.clicked.connect(
            lambda: self.disconnect_requested.emit(self._device_id)
        )
        controls.addWidget(self._disconnect_btn)

        controls.addStretch()
        layout.addLayout(controls)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_available_ports(self, ports: list) -> None:
        """Refresh the COM-port dropdown with a new port list."""
        current = self._port_combo.currentText()
        self._populate_ports(ports, current)

    def _populate_ports(self, ports: list, selected: str) -> None:
        """Fill the port combo with *ports* (list of dicts with 'port' key)."""
        self._port_combo.blockSignals(True)
        self._port_combo.clear()
        for p in ports:
            port_name = p.get("port", "")
            desc = p.get("description", "")
            if desc:
                self._port_combo.addItem(f"{port_name} ({desc})", port_name)
            else:
                self._port_combo.addItem(port_name, port_name)
        # Ensure selected port is in the list; add it if missing
        if selected:
            idx = self._port_combo.findData(selected)
            if idx >= 0:
                self._port_combo.setCurrentIndex(idx)
            else:
                self._port_combo.insertItem(0, selected, selected)
                self._port_combo.setCurrentIndex(0)
        self._port_combo.blockSignals(False)

    def _on_port_changed(self, text: str) -> None:
        """Slot: COM port dropdown changed."""
        port = self._port_combo.currentData()
        if port:
            self.port_changed.emit(self._device_id, port)

    def _on_baudrate_changed(self, index: int) -> None:
        """Slot: baudrate dropdown changed."""
        baud = self._baud_combo.currentData()
        if baud:
            self.baudrate_changed.emit(self._device_id, baud)

    def push_data(
        self,
        device_id: str,
        timestamp: datetime,
        data: Dict[str, Any],
    ) -> None:
        """Push a measurement update. Thread-safe."""
        if device_id != self._device_id:
            return
        ts_float = timestamp.timestamp()
        self._data_arrived.emit(device_id, ts_float, dict(data))

    def set_connected(self, connected: bool) -> None:
        """Update connection state and UI indicators."""
        self._connected = connected
        if connected:
            self._indicator.set_status("connected")
            self._status_text.setText("Connected")
            self._status_text.setStyleSheet("color: #51cf66;")
            self._connect_btn.setEnabled(False)
            self._disconnect_btn.setEnabled(True)
        else:
            self._indicator.set_status("disconnected")
            self._status_text.setText("Disconnected")
            self._status_text.setStyleSheet("color: #ff6b6b;")
            self._connect_btn.setEnabled(True)
            self._disconnect_btn.setEnabled(False)


    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_data_arrived(
        self, device_id: str, ts_float: float, data: Dict[str, Any]
    ) -> None:
        """Slot: runs in GUI thread. Update value labels from data."""
        self._update_values(data)

    def _update_values(self, data: Dict[str, Any]) -> None:
        """Parse measurement data and update value labels.

        Handles VCU-style nested dicts: {1: {pressure, status_code, ...}, 2: ...}
        """
        if data and all(isinstance(v, dict) for v in data.values()):
            for ch_key, ch_data in data.items():
                if not isinstance(ch_data, dict):
                    continue
                pressure = ch_data.get("pressure")
                if pressure is not None and isinstance(pressure, (int, float)):
                    lbl = self._value_labels.get(f"ch{ch_key}_pressure")
                    if lbl:
                        lbl.setText(f"{pressure:.4e}")

                status_text = ch_data.get("status_text", "")
                status_code = ch_data.get("status_code", 0)
                lbl = self._value_labels.get(f"ch{ch_key}_status")
                if lbl:
                    colour = "#51cf66" if status_code == 0 else "#ffd43b"
                    lbl.setText(status_text)
                    lbl.setStyleSheet(f"color: {colour};")

                unit = ch_data.get("unit", "")
                if unit and self._unit_label.text() != unit:
                    self._unit_label.setText(unit)
            return

        for key, value in data.items():
            lbl = self._value_labels.get(key)
            if lbl and isinstance(value, (int, float, str)):
                if isinstance(value, float):
                    lbl.setText(f"{value:.4e}")
                else:
                    lbl.setText(str(value))
