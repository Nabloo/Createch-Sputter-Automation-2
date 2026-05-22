"""Real-time pyqtgraph plot widget with configurable channels and device selection.

Supports two rendering modes:

- **Live** (default): buffers data from ``push_data`` into per-channel deques,
  renders with a rolling history window.
- **Log viewer**: displays pre-loaded CSV log data via ``load_log_data()``.
  Live data is paused (``_log_mode=True``) and gap detection >60 s splits
  curves into separate ``PlotDataItem`` segments.
"""

import logging
import math
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
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

from src.data_logging.log_reader import LogData
from src.gui.plot_config_dialog import PlotConfigDialog

logger = logging.getLogger(__name__)

TRACE_COLOURS = [
    "#00bfff", "#ff6b6b", "#51cf66", "#ffd43b",
    "#cc5de8", "#ff922b", "#20c997", "#f06595",
    "#748ffc", "#94d82d", "#ff8787", "#4dabf7",
]

_GAP_THRESHOLD_S = 60.0


def _split_into_segments(
    xs: List[float],
    ys: List[float],
    gap_threshold_s: float = _GAP_THRESHOLD_S,
) -> List[Tuple[List[float], List[float]]]:
    """Split (x, y) series at gaps larger than *gap_threshold_s*.

    Returns a list of ``(seg_x, seg_y)`` tuples, one per contiguous block.
    An empty input returns an empty list.
    """
    if not xs:
        return []
    segments: List[Tuple[List[float], List[float]]] = []
    seg_x, seg_y = [xs[0]], [ys[0]]
    for i in range(1, len(xs)):
        if xs[i] - xs[i - 1] > gap_threshold_s:
            segments.append((seg_x, seg_y))
            seg_x, seg_y = [xs[i]], [ys[i]]
        else:
            seg_x.append(xs[i])
            seg_y.append(ys[i])
    segments.append((seg_x, seg_y))
    return segments


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
        global_t0: Optional[List[Optional[float]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._device_id = device_id
        self._history_seconds = history_seconds
        # Shared mutable t0 container — when this plot first sees data,
        # it writes the timestamp here so other plots can stay in sync.
        self._shared_t0: Optional[List[Optional[float]]] = global_t0
        self._t0: Optional[float] = global_t0[0] if global_t0 else None
        self._y_label_set: bool = False
        self._all_device_ids: List[str] = []
        # Thread-safe selected device (read from worker threads in push_data)
        self._selected_device: str = device_id

        self._channels: Dict[str, Dict[str, Any]] = {}
        self._buffers: Dict[str, deque] = {}
        self._curves: Dict[str, pg.PlotDataItem] = {}

        # ---- Log-viewer mode -------------------------------------------------
        self._log_mode: bool = False
        # channel_name → (xs_list, ys_list) — pre-built x/y for the current
        # time range.  None means "no data for this channel".
        self._log_data_cache: Dict[str, Tuple[List[float], List[float]]] = {}
        # channel_name → list of PlotDataItem segments (one per gap-free block)
        self._log_curves: Dict[str, List[pg.PlotDataItem]] = {}
        self._x_axis_mode: str = "relative"  # "relative" | "absolute"

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
        Silently drops data if the underlying C++ object has been
        deleted (e.g. the plot was removed while acquisition runs).
        """
        # Use _selected_device (plain str, safe to read from any thread)
        # instead of _device_combo.currentText() (Qt widget, GUI thread only)
        if self._selected_device and device_id != self._selected_device:
            return
        ts_float = timestamp.timestamp()
        try:
            self._data_arrived.emit(device_id, ts_float, dict(data))
        except RuntimeError:
            # Signal source (C++ object) was deleted — plot removed
            # while the acquisition engine was still running.
            pass

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
                if name in self._curves:
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
        """Pre-load history data from DataStore.

        Converts absolute timestamps to seconds-relative-to-t0 so the
        x-axis aligns with live data that arrived earlier.

        Buffers data for every channel (including hidden ones) so
        re-enabling a channel shows its full, correctly-trimmed
        history.  Only *visible* channel curves are updated.
        """
        for channel_name, entries in history.items():
            if channel_name not in self._buffers:
                self._buffers[channel_name] = deque()
            for ts, value in entries:
                ts_float = ts.timestamp() if isinstance(ts, datetime) else float(ts)
                if self._t0 is not None:
                    ts_float -= self._t0
                self._buffers[channel_name].append((ts_float, value))
            # Only render visible channels; hidden ones stay in buffer only.
            if self._channels.get(channel_name, {}).get("visible", True):
                self._update_curve(channel_name)
        self._trim_buffers()

    def clear(self) -> None:
        """Clear all plot data (preserves t0 for shared timeline)."""
        self._y_label_set = False
        for buf in self._buffers.values():
            buf.clear()
        for curve in self._curves.values():
            curve.setData([], [])
        self._clear_log_curves()
        logger.debug("PlotWidget[%s]: cleared", self._device_id)

    def set_history_seconds(self, seconds: float) -> None:
        """Set the rolling history window in seconds."""
        self._history_seconds = seconds
        self._trim_buffers()
        for name, cfg in self._channels.items():
            if cfg.get("visible", True):
                self._update_curve(name)

    def backfill_from_store(self, store, device_id: str) -> None:
        """Clear buffers and reload all history from DataStore.

        Called when the history window is enlarged so the plot can
        display data that was previously trimmed.  The store is
        queried for the full history of every configured channel.
        """
        channels = list(self._channels.keys())
        if not channels:
            return

        # Clear existing buffers so we don't double up
        for buf in self._buffers.values():
            buf.clear()

        history: Dict[str, list] = {}
        for ch in channels:
            history[ch] = store.get_history(device_id, ch)
        if any(history.values()):
            self.load_history(history)

    # ------------------------------------------------------------------
    # Log-viewer mode (T2) — replace live data with CSV log data
    # ------------------------------------------------------------------

    @property
    def log_mode(self) -> bool:
        """``True`` when the plot is displaying CSV log data."""
        return self._log_mode

    @property
    def x_axis_mode(self) -> str:
        """Current x-axis mode: ``"relative"`` or ``"absolute"``."""
        return self._x_axis_mode

    def load_log_data(
        self,
        log_data: LogData,
        t_range_start: datetime,
        t_range_end: datetime,
        x_axis_mode: str = "relative",
    ) -> None:
        """Switch to log-viewer mode and display CSV data.

        Parameters
        ----------
        log_data:
            Parsed CSV log file (from ``LogFileReader.read()``).
        t_range_start / t_range_end:
            Only data points within this time window are displayed.
        x_axis_mode:
            ``"relative"`` → seconds from *t_range_start* (label ``"Time (s)"``).
            ``"absolute"`` → Unix epoch seconds (label ``"Time"``).
        """
        self._log_mode = True
        self._clear_log_curves()
        self._log_data_cache.clear()
        # Clear live curves from the screen
        for curve in self._curves.values():
            curve.setData([], [])
        for buf in self._buffers.values():
            buf.clear()

        timestamps = log_data.timestamps

        # ---------- filter to the requested time window ----------
        indices = [
            i for i, t in enumerate(timestamps) if t_range_start <= t <= t_range_end
        ]
        if not indices:
            # Nothing in range — all channels will show empty
            for ch_name in self._channels:
                self._log_data_cache[ch_name] = ([], [])
                self._render_log_channel(ch_name)
            self._update_x_axis_label(x_axis_mode)
            self._x_axis_mode = x_axis_mode
            self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)
            return

        filtered_ts = [timestamps[i] for i in indices]

        # ---------- convert timestamps to numeric x-values ----------
        if x_axis_mode == "absolute":
            xs = [t.timestamp() for t in filtered_ts]
        else:
            xs = [(t - t_range_start).total_seconds() for t in filtered_ts]

        # ---------- extract & render each channel ----------
        for ch_name in self._channels:
            col_name = self._find_log_column(log_data, ch_name)
            if col_name is None:
                self._log_data_cache[ch_name] = ([], [])
            else:
                ys = [log_data.values[col_name][i] for i in indices]
                # Drop NaN entries so gap detection works cleanly
                valid = [(x, y) for x, y in zip(xs, ys) if not math.isnan(y)]
                clean_xs = [v[0] for v in valid] if valid else []
                clean_ys = [v[1] for v in valid] if valid else []
                self._log_data_cache[ch_name] = (clean_xs, clean_ys)
            self._render_log_channel(ch_name)

        self._update_x_axis_label(x_axis_mode)
        self._x_axis_mode = x_axis_mode
        self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        logger.info(
            "PlotWidget[%s]: loaded log %s — %d points in range [%s … %s]",
            self._device_id,
            log_data.filepath,
            len(filtered_ts),
            t_range_start.isoformat(),
            t_range_end.isoformat(),
        )

    def clear_log_data(self) -> None:
        """Exit log-viewer mode and resume live data buffering."""
        self._log_mode = False
        self._clear_log_curves()
        self._log_data_cache.clear()
        # Clear any rendered data from the screen
        for curve in self._curves.values():
            curve.setData([], [])
        self._plot.setLabel("bottom", "Time (s)")
        self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        logger.debug("PlotWidget[%s]: switched back to live mode", self._device_id)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

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

    def set_global_t0(self, t0: Optional[List[Optional[float]]]) -> None:
        """Replace the shared t0 reference (used when restoring from config)."""
        self._shared_t0 = t0
        if t0 is not None and t0[0] is not None and self._t0 is None:
            self._t0 = t0[0]

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
        if self._log_mode:
            self._render_log_channel(name)
        else:
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
        if self._log_mode:
            for ch_name in self._channels:
                self._render_log_channel(ch_name)
        else:
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
    def t0(self) -> Optional[float]:
        """The x-axis origin (seconds since epoch).

        ``None`` until the first data point arrives.  Can be set externally
        to synchronise the x-axis across multiple plots.
        """
        return self._t0

    @t0.setter
    def t0(self, value: Optional[float]) -> None:
        self._t0 = value

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
        its full history.

        In log-viewer mode this method returns immediately — CSV data is
        displayed instead of live measurements.
        """
        if self._log_mode:
            return

        if self._t0 is None:
            self._t0 = ts_float
            # Only write the shared t0 once — the first-ever data point across
            # ALL plots.  If a plot switches devices and resets its own _t0,
            # it must NOT overwrite the shared origin that other plots rely on.
            if self._shared_t0 is not None and self._shared_t0[0] is None:
                self._shared_t0[0] = ts_float
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
            parent=self,
        )
        if dlg.exec():
            for name, cfg in self._channels.items():
                cfg["visible"] = dlg.channel_visibility.get(name, True)

            for i, ch in enumerate(dlg.selected_channels):
                if ch in self._channels:
                    new_colour = dlg.selected_colours[i]
                    self._channels[ch]["colour"] = new_colour
                    if ch in self._curves:
                        self._curves[ch].setPen(
                            pg.mkPen(color=new_colour, width=2)
                        )

            if self._log_mode:
                # Re-render all channels so colour/visibility changes take effect
                for name in self._channels:
                    self._render_log_channel(name)
            else:
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
        self._clear_log_curves()
        self._log_data_cache.clear()
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
            autoDownsample=True,
        )
        self._curves[name] = curve

    def _update_symbols(self, curve: pg.PlotDataItem) -> None:
        """Hide dot symbols when data density exceeds ~1 dot per 2 pixels."""
        view_range = self._plot.viewRange()  # [[xmin, xmax], [ymin, ymax]]
        x_range = view_range[0]
        if x_range[1] <= x_range[0]:
            return
        pixel_width = max(self._plot.width(), 1)
        x_data = curve.xData
        if x_data is None or len(x_data) == 0:
            return
        points_in_view = int(np.sum(
            (x_data >= x_range[0]) & (x_data <= x_range[1])
        ))
        if points_in_view / pixel_width > 0.5:
            curve.setSymbol(None)
        else:
            curve.setSymbol('o')

    def _remove_channel(self, name: str) -> None:
        self._channels.pop(name, None)
        self._buffers.pop(name, None)
        curve = self._curves.pop(name, None)
        if curve is not None:
            self._plot.removeItem(curve)
        # Clean up log-viewer curves for this channel as well
        for log_curve in self._log_curves.pop(name, []):
            self._plot.removeItem(log_curve)

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
        self._update_symbols(curve)

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

    # ------------------------------------------------------------------
    # Log-viewer helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_log_column(
        log_data: LogData, channel_name: str,
    ) -> Optional[str]:
        """Find the CSV column that corresponds to *channel_name*.

        Matching is done by prefix: ``ch1_pressure`` matches
        ``ch1_pressure [mbar]`` or ``ch1_pressure``.
        Returns ``None`` when the channel has no counterpart in the log.
        """
        for header in log_data.headers:
            if header == channel_name:
                return header
            if header.startswith(channel_name + " [") or header.startswith(channel_name + " "):
                return header
        return None

    def _render_log_channel(self, name: str) -> None:
        """Create or replace segmented PlotDataItem curves for *name*.

        Reads the pre-built (xs, ys) from ``_log_data_cache``, splits
        at gaps >60 s, and adds one ``PlotDataItem`` per contiguous
        segment.  The first segment carries the channel ``name`` (for
        the legend); subsequent segments are anonymous.
        """
        # Remove any previous log curves for this channel
        for curve in self._log_curves.pop(name, []):
            self._plot.removeItem(curve)

        xs, ys = self._log_data_cache.get(name, ([], []))
        if not xs:
            return

        cfg = self._channels.get(name, {})
        colour = cfg.get("colour", "#ffffff")
        visible = cfg.get("visible", True)

        segments = _split_into_segments(xs, ys)
        curves: List[pg.PlotDataItem] = []
        for i, (seg_x, seg_y) in enumerate(segments):
            if not seg_x:
                continue
            curve = self._plot.plot(
                list(seg_x),
                list(seg_y),
                pen=pg.mkPen(color=colour, width=2),
                name=name if i == 0 else None,
                symbol="o",
                symbolSize=4,
                autoDownsample=True,
            )
            if not visible:
                curve.setData([], [])
            curves.append(curve)

        self._log_curves[name] = curves

        # Apply dot-hiding (same as live-mode _update_curve does)
        for curve in curves:
            self._update_symbols(curve)

    def _clear_log_curves(self) -> None:
        """Remove all log-viewer PlotDataItem segments from the plot."""
        for curves in self._log_curves.values():
            for curve in curves:
                self._plot.removeItem(curve)
        self._log_curves.clear()

    def _update_x_axis_label(self, mode: str) -> None:
        """Set the x-axis label based on the current mode."""
        if mode == "absolute":
            self._plot.setLabel("bottom", "Time")
        else:
            self._plot.setLabel("bottom", "Time (s)")
