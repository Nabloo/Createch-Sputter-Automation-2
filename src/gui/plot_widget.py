"""Real-time pyqtgraph plot widget with configurable channels and device selection."""

import logging
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.gui.plot_config_dialog import PlotConfigDialog

logger = logging.getLogger(__name__)

TRACE_COLOURS = [
    "#00bfff", "#ff6b6b", "#51cf66", "#ffd43b",
    "#cc5de8", "#ff922b", "#20c997", "#f06595",
    "#748ffc", "#94d82d", "#ff8787", "#4dabf7",
]


class PlotWidget(QWidget):
    """Real-time pyqtgraph plot widget with device switching.

    Displays time-series traces for a selected device with a rolling
    history window.  Supports dynamic trace selection via a
    configuration dialog, device switching via dropdown, auto-scaling,
    and thread-safe data push.

    Thread-safe: ``push_data`` can be called from any thread;
    the internal signal guarantees GUI updates happen in the main
    thread.
    """

    _data_arrived = Signal(str, float, object)
    remove_requested = Signal()
    state_changed = Signal()  # emitted when device, channels, visibility etc. change

    def __init__(
        self,
        device_id: str = "",
        history_seconds: float = 0,
        global_t0: Optional[float] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._device_id = device_id
        self._history_seconds = history_seconds
        self._t0: Optional[float] = global_t0
        self._y_label_set: bool = False
        self._all_device_ids: List[str] = []
        # Thread-safe selected device (read from worker threads in push_data)
        self._selected_device: str = device_id

        self._channels: Dict[str, Dict[str, Any]] = {}
        self._buffers: Dict[str, deque] = {}
        self._curves: Dict[str, pg.PlotDataItem] = {}

        self._build_ui()
        self._data_arrived.connect(self._on_data_arrived)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ---- Toolbar at the top ----
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(2, 2, 2, 2)
        toolbar.setSpacing(4)

        # Device selector dropdown
        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(80)
        self._device_combo.setToolTip("Select device to display")
        self._device_combo.currentTextChanged.connect(self._on_device_changed)
        toolbar.addWidget(self._device_combo)

        self._configure_btn = QPushButton("Channels")
        self._configure_btn.setToolTip("Configure visible channels and appearance")
        self._configure_btn.clicked.connect(self._on_configure)
        toolbar.addWidget(self._configure_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setToolTip("Clear all plot data")
        self._clear_btn.clicked.connect(self.clear)
        toolbar.addWidget(self._clear_btn)

        self._auto_btn = QPushButton("Auto")
        self._auto_btn.setToolTip("Reset auto-scaling")
        toolbar.addWidget(self._auto_btn)

        toolbar.addStretch()

        self._close_btn = QPushButton("\u00d7")
        self._close_btn.setToolTip("Remove this plot")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.clicked.connect(self.remove_requested.emit)
        toolbar.addWidget(self._close_btn)

        layout.addLayout(toolbar)

        # ---- Plot (fills remaining space) ----
        self._plot = pg.PlotWidget()
        # Wire the auto button now that _plot exists
        self._auto_btn.clicked.connect(self._plot.enableAutoRange)
        self._plot.setBackground("#1e1e1e")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.setLabel("left", "Value")
        self._plot.setLabel("bottom", "Time (s)")
        self._plot.addLegend(offset=(-10, 10))
        self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)

        axis_pen = pg.mkPen(color="#888888", width=1)
        for axis_name in ("left", "bottom"):
            axis = self._plot.getAxis(axis_name)
            axis.setPen(axis_pen)
            axis.setTextPen(axis_pen)

        layout.addWidget(self._plot, stretch=1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push_data(
        self,
        device_id: str,
        timestamp: datetime,
        data: Dict[str, Any],
    ) -> None:
        """Push a measurement update.  Thread-safe.

        Only forwards data matching the currently selected device.
        """
        # Use _selected_device (plain str, safe to read from any thread)
        # instead of _device_combo.currentText() (Qt widget, GUI thread only)
        if self._selected_device and device_id != self._selected_device:
            return
        ts_float = timestamp.timestamp()
        self._data_arrived.emit(device_id, ts_float, dict(data))

    def set_channels(
        self,
        channels: List[str],
        colours: Optional[List[str]] = None,
    ) -> None:
        """Configure which channels to display."""
        if colours is None:
            colours = TRACE_COLOURS[: len(channels)]
        else:
            while len(colours) < len(channels):
                colours.append(
                    TRACE_COLOURS[len(colours) % len(TRACE_COLOURS)]
                )

        for name in list(self._channels):
            if name not in channels:
                self._remove_channel(name)

        for name, colour in zip(channels, colours):
            if name in self._channels:
                self._channels[name]["colour"] = colour
                self._curves[name].setPen(pg.mkPen(color=colour, width=2))
            else:
                self._add_channel(name, colour)

        logger.debug(
            "PlotWidget[%s]: channels = %s", self._device_id, channels
        )

    def load_history(
        self,
        history: Dict[str, List[Tuple[datetime, Any]]],
    ) -> None:
        """Pre-load history data from DataStore."""
        for channel_name, entries in history.items():
            if channel_name not in self._buffers:
                self._buffers[channel_name] = deque()
            for ts, value in entries:
                ts_float = ts.timestamp() if isinstance(ts, datetime) else float(ts)
                self._buffers[channel_name].append((ts_float, value))
            self._update_curve(channel_name)

    def clear(self) -> None:
        """Clear all plot data (preserves t0 for shared timeline)."""
        self._y_label_set = False
        for buf in self._buffers.values():
            buf.clear()
        for curve in self._curves.values():
            curve.setData([], [])
        logger.debug("PlotWidget[%s]: cleared", self._device_id)

    def set_history_seconds(self, seconds: float) -> None:
        """Set the rolling history window in seconds."""
        self._history_seconds = seconds
        self._trim_buffers()
        for name in self._curves:
            self._update_curve(name)

    @property
    def device_id(self) -> str:
        return self._selected_device or self._device_id

    @device_id.setter
    def device_id(self, value: str) -> None:
        self._device_id = value
        self._selected_device = value
        if value and value in self._all_device_ids:
            idx = self._device_combo.findText(value)
            if idx >= 0:
                self._device_combo.setCurrentIndex(idx)

    def set_available_devices(self, device_ids: List[str]) -> None:
        """Update the device dropdown with available devices."""
        self._all_device_ids = list(device_ids)
        current = self._selected_device
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_combo.addItems(device_ids)
        if current and current in device_ids:
            self._device_combo.setCurrentText(current)
        elif device_ids:
            self._device_combo.setCurrentIndex(0)
            self._device_id = device_ids[0]
            self._selected_device = device_ids[0]
        self._device_combo.blockSignals(False)

    def set_global_t0(self, t0: float) -> None:
        """Set a shared t0 so all plots use the same x-axis origin."""
        if self._t0 is None:
            self._t0 = t0

    def set_dark_mode(self, dark: bool) -> None:
        """Update plot styling to match the application theme."""
        if dark:
            self._plot.setBackground("#1e1e1e")
            grid_alpha = 0.3
            axis_color = "#888888"
        else:
            self._plot.setBackground("#ffffff")
            grid_alpha = 0.15
            axis_color = "#555555"
        self._plot.showGrid(x=True, y=True, alpha=grid_alpha)
        axis_pen = pg.mkPen(color=axis_color, width=1)
        for axis_name in ("left", "bottom"):
            axis = self._plot.getAxis(axis_name)
            axis.setPen(axis_pen)
            axis.setTextPen(axis_pen)

    def set_channel_visibility(self, name: str, visible: bool) -> None:
        """Show or hide a single channel without clearing its data buffer."""
        cfg = self._channels.get(name)
        if cfg is None:
            return
        cfg["visible"] = visible
        curve = self._curves.get(name)
        if curve is None:
            return
        if visible:
            self._update_curve(name)
        else:
            curve.setData([], [])
        self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.state_changed.emit()

    def apply_visibility(self, visibility: Dict[str, bool]) -> None:
        """Apply a visibility map to all channels (used when restoring from config)."""
        for ch_name, vis in visibility.items():
            if ch_name in self._channels:
                self._channels[ch_name]["visible"] = vis
        for ch_name, cfg in self._channels.items():
            curve = self._curves.get(ch_name)
            if curve is None:
                continue
            if cfg.get("visible", True):
                self._update_curve(ch_name)
            else:
                curve.setData([], [])
        self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)

    @property
    def channels(self) -> List[str]:
        """Currently configured channel names (all, not just visible)."""
        return list(self._channels.keys())

    @property
    def visible_channels(self) -> List[str]:
        """Only channels whose visibility checkbox is checked."""
        return [
            name for name, cfg in self._channels.items()
            if cfg.get("visible", True)
        ]

    @property
    def channel_colours(self) -> List[str]:
        """Colours for all configured channels, in channel order."""
        return [cfg["colour"] for cfg in self._channels.values()]

    @property
    def channel_visibility(self) -> Dict[str, bool]:
        """Visibility map for all configured channels."""
        return {name: cfg.get("visible", True) for name, cfg in self._channels.items()}

    @property
    def history_seconds(self) -> float:
        return self._history_seconds

    @property
    def plot_widget(self) -> pg.PlotWidget:
        """Access the underlying pyqtgraph PlotWidget."""
        return self._plot

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _on_data_arrived(
        self, device_id: str, ts_float: float, data: Dict[str, Any]
    ) -> None:
        """Slot: runs in GUI thread.  Buffer data for ALL channels (even hidden),
        but only render visible curves.  This way re-enabling a channel shows
        its full history."""
        if self._t0 is None:
            self._t0 = ts_float
        t_rel = ts_float - self._t0

        flat = self._extract_values(data)
        self._auto_detect_unit(data)

        # Always buffer data for every known channel so historical data
        # is available when the user re-enables a hidden channel.
        for channel_name in self._channels:
            value = flat.get(channel_name)
            if value is None:
                continue
            if channel_name not in self._buffers:
                self._buffers[channel_name] = deque()
            self._buffers[channel_name].append((t_rel, value))

        self._trim_buffers()
        # Only update the rendered curve for visible channels.
        for channel_name, cfg in self._channels.items():
            if cfg.get("visible", True):
                self._update_curve(channel_name)

    def _on_configure(self) -> None:
        """Open the channel configuration dialog."""
        channels = list(self._channels.keys())
        colours = {n: c["colour"] for n, c in self._channels.items()}
        visibility = {n: c.get("visible", True) for n, c in self._channels.items()}

        dlg = PlotConfigDialog(
            channels=channels,
            colours=colours,
            visibility=visibility,
            history_seconds=self._history_seconds,
            parent=self,
        )
        if dlg.exec():
            for name, cfg in self._channels.items():
                cfg["visible"] = dlg.channel_visibility.get(name, True)
            self.set_history_seconds(dlg.history_seconds)

            for i, ch in enumerate(dlg.selected_channels):
                if ch in self._channels:
                    new_colour = dlg.selected_colours[i]
                    self._channels[ch]["colour"] = new_colour
                    if ch in self._curves:
                        self._curves[ch].setPen(
                            pg.mkPen(color=new_colour, width=2)
                        )

            # Hide/show curves based on new visibility.
            # Hidden curves get cleared from the screen but the buffer
            # is preserved so re-enabling shows all historical data.
            for name, cfg in self._channels.items():
                curve = self._curves.get(name)
                if curve is None:
                    continue
                if cfg.get("visible", True):
                    self._update_curve(name)
                else:
                    curve.setData([], [])
            self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)
            self.state_changed.emit()

    def _on_device_changed(self, new_device: str) -> None:
        """Handle device dropdown change -- clear data for the switch."""
        if not new_device or new_device == self._selected_device:
            return
        self._device_id = new_device
        self._selected_device = new_device
        self._t0 = None
        self._y_label_set = False
        for buf in self._buffers.values():
            buf.clear()
        for curve in self._curves.values():
            curve.setData([], [])
        self._plot.setLabel("left", "Value")
        self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.state_changed.emit()
        logger.debug("PlotWidget: switched to device %r", new_device)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_values(data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract scalar values from a measurement dict.

        Handles both flat dicts and VCU-style nested dicts
        (same convention as DataStore._flatten_data).
        """
        flat: Dict[str, Any] = {}

        if data and all(isinstance(v, dict) for v in data.values()):
            for ch_key, ch_data in data.items():
                if isinstance(ch_data, dict):
                    for field, value in ch_data.items():
                        if isinstance(value, (int, float)):
                            flat[f"ch{ch_key}_{field}"] = value
            return flat

        for key, value in data.items():
            if isinstance(value, (int, float)):
                flat[key] = value
        return flat

    def _add_channel(self, name: str, colour: str) -> None:
        self._channels[name] = {"colour": colour, "visible": True}
        self._buffers[name] = deque()
        curve = self._plot.plot(
            [], [],
            pen=pg.mkPen(color=colour, width=2),
            name=name,
            symbol='o',
            symbolSize=4,
        )
        self._curves[name] = curve

    def _remove_channel(self, name: str) -> None:
        self._channels.pop(name, None)
        self._buffers.pop(name, None)
        curve = self._curves.pop(name, None)
        if curve is not None:
            self._plot.removeItem(curve)

    def _update_curve(self, channel_name: str) -> None:
        curve = self._curves.get(channel_name)
        if curve is None:
            return
        buf = self._buffers.get(channel_name)
        if not buf:
            curve.setData([], [])
            return
        xs, ys = zip(*buf) if buf else ([], [])
        curve.setData(list(xs), list(ys))

    def _trim_buffers(self) -> None:
        if self._history_seconds <= 0 or self._t0 is None:
            return
        cutoff = time.time() - self._t0 - self._history_seconds
        for buf in self._buffers.values():
            while buf and buf[0][0] < cutoff:
                buf.popleft()

    def _auto_detect_unit(self, data: Dict[str, Any]) -> None:
        """Set y-axis label from the unit field in VCU-style data."""
        if self._y_label_set:
            return
        if not (data and all(isinstance(v, dict) for v in data.values())):
            return
        for ch_data in data.values():
            unit = ch_data.get("unit", "")
            if unit:
                self._plot.setLabel("left", f"Pressure ({unit})")
                self._y_label_set = True
                return
