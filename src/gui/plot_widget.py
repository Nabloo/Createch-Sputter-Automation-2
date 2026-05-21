"""Real-time pyqtgraph plot widget with configurable channels."""

import logging
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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
    """Real-time plotting widget using pyqtgraph.

    Displays time-series traces for a specific device with a rolling
    history window.  Supports dynamic trace selection via a
    configuration dialog, auto-scaling, and thread-safe data push.

    Thread-safe: ``push_data`` can be called from any thread;
    the internal signal guarantees GUI updates happen in the main
    thread.
    """

    _data_arrived = Signal(str, float, object)
    remove_requested = Signal()

    def __init__(
        self,
        device_id: str = "",
        history_seconds: float = 60.0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._device_id = device_id
        self._history_seconds = history_seconds

        self._channels: Dict[str, Dict[str, Any]] = {}
        self._buffers: Dict[str, deque] = {}
        self._curves: Dict[str, pg.PlotDataItem] = {}
        self._t0: Optional[float] = None
        self._y_label_set: bool = False

        self._build_ui()
        self._data_arrived.connect(self._on_data_arrived)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._plot = pg.PlotWidget()
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

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

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
        self._auto_btn.clicked.connect(self._plot.enableAutoRange)
        toolbar.addWidget(self._auto_btn)

        toolbar.addStretch()

        self._close_btn = QPushButton("\u00d7")
        self._close_btn.setToolTip("Remove this plot")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.clicked.connect(self.remove_requested.emit)
        toolbar.addWidget(self._close_btn)

        layout.addLayout(toolbar)

    def push_data(
        self,
        device_id: str,
        timestamp: datetime,
        data: Dict[str, Any],
    ) -> None:
        """Push a measurement update.  Thread-safe."""
        if self._device_id and device_id != self._device_id:
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
        """Clear all plot data."""
        self._t0 = None
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
        return self._device_id

    @device_id.setter
    def device_id(self, value: str) -> None:
        self._device_id = value

    @property
    def channels(self) -> List[str]:
        return list(self._channels.keys())

    @property
    def history_seconds(self) -> float:
        return self._history_seconds

    @property
    def plot_widget(self) -> pg.PlotWidget:
        """Access the underlying pyqtgraph PlotWidget."""
        return self._plot

    def _on_data_arrived(
        self, device_id: str, ts_float: float, data: Dict[str, Any]
    ) -> None:
        """Slot: runs in GUI thread.  Append data and update curves."""
        # Track first timestamp for relative x-axis
        if self._t0 is None:
            self._t0 = ts_float
        t_rel = ts_float - self._t0

        flat = self._extract_values(data)

        # Auto-detect unit from VCU-style data and set y-axis label once
        self._auto_detect_unit(data)

        for channel_name, cfg in self._channels.items():
            if not cfg.get("visible", True):
                continue
            value = flat.get(channel_name)
            if value is None:
                continue
            if channel_name not in self._buffers:
                self._buffers[channel_name] = deque()
            self._buffers[channel_name].append((t_rel, value))

        self._trim_buffers()
        for channel_name in self._channels:
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

            # Hide/show curves based on new visibility and re-range y-axis
            for name, cfg in self._channels.items():
                curve = self._curves.get(name)
                if curve is None:
                    continue
                if cfg.get("visible", True):
                    self._update_curve(name)
                else:
                    curve.setData([], [])
            self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)

    @staticmethod
    def _extract_values(data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract scalar values from a measu
rement dict.

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
