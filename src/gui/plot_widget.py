"""Real-time pyqtgraph plot widget with configurable channels and device selection.

Supports two rendering modes:

- **Live** (default): buffers data from ``push_data`` into per-channel deques,
  renders with a rolling history window.
- **Log viewer**: displays pre-loaded CSV log data via ``load_log_data()``.
  Live data is paused (``_log_mode=True``) and gap detection >60 s splits
  curves into separate ``PlotDataItem`` segments.
"""

import bisect
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
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.data_logging.log_reader import LogData

logger = logging.getLogger(__name__)

TRACE_COLOURS = [
    "#00bfff", "#ff6b6b", "#51cf66", "#ffd43b",
    "#cc5de8", "#ff922b", "#20c997", "#f06595",
    "#748ffc", "#94d82d", "#ff8787", "#4dabf7",
]

_DEFAULT_GAP_THRESHOLD_S = 60.0
MAX_SCATTER_PX_SPACING = 20  # target pixels between scatter markers
SCATTER_SYMBOL_SIZE = 2    # dot radius in pixels


def _channel_legend_name(channel: str) -> str:
    """Format a channel key as a legend-friendly name.

    Examples::
        ch1_pressure  → "Ch1 Pressure"
        ch1_rate      → "Ch1 Rate"
        ch2_thickness → "Ch2 Thickness"
    """
    parts = channel.split("_", 1)
    if len(parts) > 1 and parts[0].startswith("ch") and parts[0][2:].isdigit():
        ch_num = parts[0][2:]
        name = parts[1].replace("_", " ").title()
        return f"Ch{ch_num} {name}"
    return channel.replace("_", " ").title()


def _split_into_segments(
    xs: List[float],
    ys: List[float],
    gap_threshold_s: float = _DEFAULT_GAP_THRESHOLD_S,
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
    device_changed = Signal(str)  # emitted when user selects a different device

    def __init__(
        self,
        device_id: str = "",
        history_seconds: float = 0,
        gap_threshold_s: float = _DEFAULT_GAP_THRESHOLD_S,
        global_t0: Optional[List[Optional[float]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._device_id = device_id
        self._history_seconds = history_seconds
        self._gap_threshold_s = gap_threshold_s
        # Shared mutable t0 container — when this plot first sees data,
        # it writes the timestamp here so other plots can stay in sync.
        self._shared_t0: Optional[List[Optional[float]]] = global_t0
        self._t0: Optional[float] = global_t0[0] if global_t0 else None
        self._y_label_set: bool = False
        self._all_device_ids: List[str] = []
        # Thread-safe selected device (read from worker threads in push_data)
        self._selected_device: str = device_id

        self._channels: Dict[str, Dict[str, Any]] = {}
        self._channel_units: Dict[str, str] = {}
        # device_id → {channel → unit} — allows restoring units on device switch
        self._all_channel_units: Dict[str, Dict[str, str]] = {}
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
        self._y_log: bool = False  # y-axis log scale
        # channel_name → ScatterPlotItem (decimated data-point markers)
        self._scatters: Dict[str, pg.ScatterPlotItem] = {}
        # channel_name → single ScatterPlotItem for log-viewer (decimated across all segments)
        self._log_scatters: Dict[str, pg.ScatterPlotItem] = {}

        # ---- Hover tooltip --------------------------------------------------
        self._tooltip: Optional[pg.TextItem] = None
        self._mouse_proxy: Optional[pg.SignalProxy] = None
        # Cached x/y lists per channel (populated in _update_curve_data,
        # read in _on_mouse_moved to avoid re-allocating from deques at 60 Hz)
        self._cached_xs: Dict[str, List[float]] = {}
        self._cached_ys: Dict[str, List[float]] = {}

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

        self._log_btn = QCheckBox("Y-Axis Log.")
        self._log_btn.setToolTip("Toggle y-axis logarithmic scale")
        self._log_btn.toggled.connect(self._on_toggle_y_log)
        toolbar.addWidget(self._log_btn)

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

        # Re-decimate scatter markers when the user zooms/pans
        self._plot.getViewBox().sigRangeChanged.connect(self._on_view_range_changed)

        # Hover tooltip for data-point values
        self._tooltip = pg.TextItem(
            "", anchor=(0.5, 1.0), color="#ffffff",
            fill=pg.mkBrush(30, 30, 30, 200),
        )
        self._tooltip.setZValue(100)
        self._tooltip.hide()
        self._plot.addItem(self._tooltip)

        self._mouse_proxy = pg.SignalProxy(
            self._plot.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._on_mouse_moved,
        )

    # ------------------------------------------------------------------
    # Viewport-aware scatter decimation
    # ------------------------------------------------------------------

    def _decimate_viewport(
        self, xs: List[float], ys: List[float]
    ) -> Tuple[List[float], List[float]]:
        """Decimate to visible x-range, spacing markers ~8 px apart.

        Filters (xs, ys) to the ViewBox's visible x-range, then evenly
        subsamples so markers are at most one per *MAX_SCATTER_PX_SPACING*
        pixels.  Returns the original list when there is no visible
        range yet (ViewBox not ready) or all points are outside view.
        """
        if not xs:
            return xs, ys
        vb = self._plot.getViewBox()
        if vb is None:
            return xs, ys
        view_range = vb.viewRange()
        if not view_range or len(view_range[0]) < 2:
            return xs, ys
        x_min, x_max = view_range[0][0], view_range[0][1]
        if x_min >= x_max:
            return xs, ys

        # Filter to visible x-range (use binary-search-friendly assumptions
        # — the data is sorted in ascending x order).
        vis_x: List[float] = []
        vis_y: List[float] = []
        for x, y in zip(xs, ys):
            if math.isnan(y):
                continue
            if x_min <= x <= x_max:
                vis_x.append(x)
                vis_y.append(y)

        if not vis_x:
            return [], []

        # One marker every ~px_spacing pixels — but based on the data's
        # actual pixel span within the view, not the full viewport width.
        # This prevents markers from piling up when data is clustered
        # in a small portion of a wide zoomed-out view.
        px_width = vb.width()
        if px_width <= 0:
            px_width = 800
        view_x_span = x_max - x_min
        data_x_span = vis_x[-1] - vis_x[0]
        if view_x_span > 0 and data_x_span > 0:
            data_px_width = px_width * (data_x_span / view_x_span)
        else:
            data_px_width = px_width
        max_points = max(5, int(data_px_width / MAX_SCATTER_PX_SPACING))

        n = len(vis_x)
        if n <= max_points:
            return vis_x, vis_y

        stride = n / max_points
        result_x: List[float] = []
        result_y: List[float] = []
        idx = 0.0
        while idx < n:
            i = int(idx)
            result_x.append(vis_x[i])
            result_y.append(vis_y[i])
            idx += stride
        return result_x, result_y

    def _refresh_scatters(self, channel_name: Optional[str] = None) -> None:
        """Refresh decimated scatter markers — the single entry-point for all
        scatter updates (zoom/pan, new data, visibility toggles, y-log, etc.).

        Parameters
        ----------
        channel_name:
            Refresh only this channel.  ``None`` refreshes every configured
            channel (both visible and hidden — hidden ones are cleared).
        """
        channels = [channel_name] if channel_name else list(self._channels.keys())

        if self._log_mode:
            for name in channels:
                if name not in self._channels:
                    continue
                visible = self._channels[name].get("visible", True)
                scatter = self._log_scatters.get(name)
                if scatter is None:
                    continue
                if not visible:
                    scatter.setData([], [])
                    continue
                xs, ys = self._log_data_cache.get(name, ([], []))
                if not xs:
                    scatter.setData([], [])
                    continue
                # Log scale cannot display y ≤ 0 — filter before decimation
                if self._y_log:
                    filtered = [(x, y) for x, y in zip(xs, ys) if y > 0]
                    if filtered:
                        f_xs, f_ys = zip(*filtered)
                        xs, ys = list(f_xs), list(f_ys)
                    else:
                        scatter.setData([], [])
                        continue
                dec_xs, dec_ys = self._decimate_viewport(xs, ys)
                scatter.setData(dec_xs, dec_ys)
        else:
            for name in channels:
                if name not in self._channels:
                    continue
                visible = self._channels[name].get("visible", True)
                scatter = self._scatters.get(name)
                if scatter is None:
                    continue
                if not visible:
                    scatter.setData([], [])
                    continue
                buf = self._buffers.get(name)
                if not buf:
                    scatter.setData([], [])
                    continue
                xs_list = [p[0] for p in buf]
                ys_list = [p[1] for p in buf]
                if self._x_axis_mode == "absolute" and self._t0 is not None:
                    xs_list = [x + self._t0 for x in xs_list]
                # Log scale cannot display y ≤ 0 — filter before decimation
                if self._y_log:
                    filtered = [(x, y) for x, y in zip(xs_list, ys_list) if y > 0]
                    if filtered:
                        f_xs, f_ys = zip(*filtered)
                        xs_list, ys_list = list(f_xs), list(f_ys)
                    else:
                        scatter.setData([], [])
                        continue
                dec_xs, dec_ys = self._decimate_viewport(xs_list, ys_list)
                scatter.setData(dec_xs, dec_ys)

    def _refresh_curves(self, channel_name: Optional[str] = None) -> None:
        """Refresh curve data — the single entry-point for all curve updates.

        Handles both live-mode (``_update_curve_data``) and log-viewer mode
        (``_render_log_channel``).  Visible channels get their curves refreshed
        from buffers / cache; hidden channels get cleared.

        Parameters
        ----------
        channel_name:
            Refresh only this channel.  ``None`` refreshes every configured
            channel.
        """
        channels = [channel_name] if channel_name else list(self._channels.keys())

        if self._log_mode:
            for name in channels:
                if name in self._channels:
                    self._render_log_channel(name)
        else:
            for name in channels:
                if name not in self._channels:
                    continue
                cfg = self._channels[name]
                visible = cfg.get("visible", True)
                curve = self._curves.get(name)
                if curve is None:
                    continue
                if visible:
                    self._update_curve_data(name)
                else:
                    curve.setData([], [])
                    self._refresh_scatters(name)

    def _on_view_range_changed(self) -> None:
        """Re-decimate scatter markers after zoom/pan."""
        self._refresh_scatters()

    def _on_mouse_moved(self, evt) -> None:
        """Track mouse and show a tooltip above the nearest data point.

        Searches through all *visible* channels' raw data (live buffers or
        log-data cache) to find the point closest to the cursor.  The
        tooltip is only shown when the nearest point falls within 3 %
        of the visible x-range.
        """
        if self._tooltip is None:
            return
        pos = evt[0]
        if not self._plot.sceneBoundingRect().contains(pos):
            self._tooltip.hide()
            return

        mouse_point = self._plot.getViewBox().mapSceneToView(pos)
        mx, my = mouse_point.x(), mouse_point.y()

        # ---- determine search threshold from visible x-range ----
        vb = self._plot.getViewBox()
        view_range = vb.viewRange()
        if not view_range or len(view_range[0]) < 2:
            self._tooltip.hide()
            return
        x_min, x_max = view_range[0][0], view_range[0][1]
        threshold = (x_max - x_min) * 0.03  # 3 % of visible x-range

        best_channel: Optional[str] = None
        best_x: Optional[float] = None
        best_y: Optional[float] = None
        best_dist = float("inf")

        for name in self.visible_channels:
            if self._log_mode:
                xs, ys = self._log_data_cache.get(name, ([], []))
            else:
                xs = self._cached_xs.get(name)
                ys = self._cached_ys.get(name)
                if xs is None or ys is None:
                    continue
            if not xs:
                continue

            # Binary search for the nearest x-index
            idx = bisect.bisect_left(xs, mx)
            for i in (idx, idx - 1):
                if 0 <= i < len(xs):
                    dx = xs[i] - mx
                    dy = ys[i] - my
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist < best_dist:
                        best_dist = dist
                        best_channel = name
                        best_x = xs[i]
                        best_y = ys[i]

        if best_channel is None or best_dist >= threshold:
            self._tooltip.hide()
            return

        # ---- format tooltip text ----
        channel_name = _channel_legend_name(best_channel)
        if self._x_axis_mode == "absolute":
            x_str = datetime.fromtimestamp(best_x).strftime("%H:%M:%S")
        else:
            x_str = f"{best_x:.2f} s"
        unit = self._channel_units.get(best_channel, "")
        y_str = f"{best_y:.3g}" if not unit else f"{best_y:.3g} {unit}"
        text = f"{channel_name}\n{x_str}, {y_str}"
        self._tooltip.setText(text)

        # ---- position above the data point ----
        # anchor (0.5, 1.0) places text centre-bottom at pos, so text
        # appears above the point.  Add a small vertical offset so the
        # marker doesn't overlap the label.
        y_range = view_range[1][1] - view_range[1][0] if view_range[1][1] > view_range[1][0] else 1.0
        y_offset = y_range * 0.04
        self._tooltip.setPos(best_x, best_y + y_offset)
        self._tooltip.show()

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
                if name in self._scatters:
                    self._scatters[name].setBrush(pg.mkBrush(colour))
            else:
                self._add_channel(name, colour)

        self._ensure_single_unit_group()

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
            # Delegate to _refresh_curves (handles visibility internally).
            self._refresh_curves(channel_name)
        self._trim_buffers()

    def clear(self) -> None:
        """Clear all plot data (preserves t0 for shared timeline)."""
        self._y_label_set = False
        for buf in self._buffers.values():
            buf.clear()
        self._refresh_curves()
        for scatter in self._scatters.values():
            self._plot.removeItem(scatter)
        self._scatters.clear()
        self._cached_xs.clear()
        self._cached_ys.clear()
        self._clear_log_curves()
        self._plot.setLabel("left", "Value")
        logger.debug("PlotWidget[%s]: cleared", self._device_id)

    def set_history_seconds(self, seconds: float) -> None:
        """Set the rolling history window in seconds."""
        self._history_seconds = seconds
        self._trim_buffers()
        self._refresh_curves()

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
        self._clear_btn.setEnabled(False)
        self._clear_log_curves()
        self._log_data_cache.clear()
        self._cached_xs.clear()
        self._cached_ys.clear()
        # Clear live curves from the screen
        self._refresh_curves()
        for buf in self._buffers.values():
            buf.clear()

        # Strip timezone so we can compare with the naive bounds
        # passed by DeviceManager (QDateTime → Python datetime loses tz).
        timestamps = [t.replace(tzinfo=None) for t in log_data.timestamps]

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
        dev_id = self._selected_device or self._device_id
        for ch_name in self._channels:
            col_name = self._find_log_column(log_data, ch_name, dev_id)
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
        self._clear_btn.setEnabled(True)
        self._clear_log_curves()
        self._log_data_cache.clear()
        self._cached_xs.clear()
        self._cached_ys.clear()
        # Clear any rendered data from the screen
        self._refresh_curves()
        for scatter in self._scatters.values():
            scatter.setData([], [])
        logger.debug("PlotWidget[%s]: switched back to live mode", self._device_id)

    def set_x_axis_mode(self, mode: str) -> None:
        """Switch x-axis display between relative (Seconds) and absolute (HH:MM:SS).

        Works in both live and log-viewer mode.

        Parameters
        ----------
        mode:
            ``"relative"`` → seconds from t0 (label ``"Time (s)"``).
            ``"absolute"`` → Unix epoch seconds displayed as HH:MM:SS
            (label ``"Time"``).
        """
        self._x_axis_mode = mode
        self._apply_x_axis_mode()

    def _apply_x_axis_mode(self) -> None:
        """Apply the current x-axis mode to the axis type and re-render live curves.

        This bypasses ``_update_x_axis_label`` because it may return early when
        ``_x_axis_mode`` already equals the requested mode (set_x_axis_mode sets
        it before calling this method).  We always need the axis swapped here.

        In log-viewer mode the axis was already configured by ``load_log_data``
        via ``_update_x_axis_label``, so only live curves need re-rendering.
        """
        mode = self._x_axis_mode
        plot_item = self._plot.getPlotItem()
        axis_pen = pg.mkPen(color="#888888", width=1)
        if mode == "absolute":
            axis = pg.DateAxisItem(orientation="bottom")
            plot_item.setAxisItems({"bottom": axis})
            axis.setPen(axis_pen)
            axis.setTextPen(axis_pen)
            self._plot.setLabel("bottom", "Time")
        else:
            self._set_relative_axis(axis_pen)

        if not self._log_mode:
            self._refresh_curves()
            self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)

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

    def set_gap_threshold(self, gap_threshold_s: float) -> None:
        """Update the gap-detection threshold and re-render all curves."""
        self._gap_threshold_s = max(0.0, gap_threshold_s)
        self._refresh_curves()

    def set_y_log(self, enabled: bool) -> None:
        """Enable or disable logarithmic y-axis scale."""
        self._y_log = enabled
        self._plot.getPlotItem().setLogMode(y=enabled)
        self._log_btn.blockSignals(True)
        self._log_btn.setChecked(enabled)
        self._log_btn.blockSignals(False)
        self._log_btn.setToolTip(
            "Y-axis is logarithmic" if enabled else "Y-axis is linear"
        )
        # Re-decimate scatters: log scale filters out y ≤ 0 points
        self._refresh_scatters()
        if enabled:
            # Auto-range with log scale — must re-enable to recalculate
            self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)

    @property
    def y_log(self) -> bool:
        return self._y_log

    def _on_toggle_y_log(self, checked: bool) -> None:
        """Toggle the y-axis log scale."""
        self.set_y_log(checked)
        self.state_changed.emit()

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
        self._refresh_curves(name)
        self._ensure_single_unit_group()
        self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.state_changed.emit()

    def apply_visibility(self, visibility: Dict[str, bool]) -> None:
        """Apply a visibility map to all channels (used when restoring from config)."""
        for ch_name, vis in visibility.items():
            if ch_name in self._channels:
                self._channels[ch_name]["visible"] = vis
        self._refresh_curves()
        self._ensure_single_unit_group()
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
        # Only refresh curves for visible channels — hidden ones stay in
        # buffer only and don't need clearing on every data arrival.
        for channel_name, cfg in self._channels.items():
            if cfg.get("visible", True):
                self._refresh_curves(channel_name)

    def _on_configure(self) -> None:
        """Open the channel configuration dialog."""
        channels = list(self._channels.keys())
        colours = {n: c["colour"] for n, c in self._channels.items()}
        visibility = {n: c.get("visible", True) for n, c in self._channels.items()}

        from src.gui.plot_config_dialog import PlotConfigDialog

        dlg = PlotConfigDialog(
            channels=channels,
            colours=colours,
            visibility=visibility,
            channel_units=self._channel_units,
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
                    if ch in self._scatters:
                        self._scatters[ch].setBrush(pg.mkBrush(new_colour))

            self._refresh_curves()
            self._ensure_single_unit_group()
            self._update_y_label()
            self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)
            self.state_changed.emit()

    def _on_device_changed(self, new_device: str) -> None:
        """Handle device dropdown change -- clear data for the switch."""
        if not new_device or new_device == self._selected_device:
            return
        self._device_id = new_device
        self._selected_device = new_device
        # Preserve the global timeline — all devices record simultaneously,
        # so the same t0 applies.  Falls back to None if no plot has ever
        # received data (no global origin set yet).
        self._t0 = self._shared_t0[0] if self._shared_t0 and self._shared_t0[0] is not None else None
        self._y_label_set = False
        for buf in self._buffers.values():
            buf.clear()
        self._refresh_curves()
        # Remove all scatter items from the plot entirely (not just
        # clear their data) so no dots from the old device can survive
        # a view-range change or channel-name overlap.
        for scatter in self._scatters.values():
            self._plot.removeItem(scatter)
        self._scatters.clear()
        self._cached_xs.clear()
        self._cached_ys.clear()
        self._clear_log_curves()
        self._log_data_cache.clear()
        # Restore channel_units for the new device if known
        self._channel_units = dict(
            self._all_channel_units.get(new_device, {})
        )
        self._update_y_label()
        self._plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.state_changed.emit()
        self.device_changed.emit(new_device)
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
            name=_channel_legend_name(name),
            autoDownsample=True,
        )
        self._curves[name] = curve
        # Scatter for data-point markers (decimated, no connecting lines)
        scatter = pg.ScatterPlotItem(
            [], [], symbol="o", symbolSize=SCATTER_SYMBOL_SIZE,
            brush=pg.mkBrush(colour), pen=None,
        )
        self._plot.addItem(scatter)
        self._scatters[name] = scatter

    def _remove_channel(self, name: str) -> None:
        self._channels.pop(name, None)
        self._buffers.pop(name, None)
        self._cached_xs.pop(name, None)
        self._cached_ys.pop(name, None)
        curve = self._curves.pop(name, None)
        if curve is not None:
            self._plot.removeItem(curve)
        scatter = self._scatters.pop(name, None)
        if scatter is not None:
            self._plot.removeItem(scatter)
        # Clean up log-viewer curves for this channel as well
        for log_curve in self._log_curves.pop(name, []):
            self._plot.removeItem(log_curve)
        log_scatter = self._log_scatters.pop(name, None)
        if log_scatter is not None:
            self._plot.removeItem(log_scatter)

    def _update_curve_data(self, channel_name: str) -> None:
        curve = self._curves.get(channel_name)
        if curve is None:
            return
        buf = self._buffers.get(channel_name)
        if not buf:
            curve.setData([], [])
            self._refresh_scatters(channel_name)
            return
        xs, ys = zip(*buf) if buf else ([], [])
        xs_list = list(xs)
        ys_list = list(ys)
        # In live mode with absolute x-axis, convert relative → absolute
        if self._x_axis_mode == "absolute" and self._t0 is not None:
            xs_list = [x + self._t0 for x in xs_list]

        # Cache for hover tooltip (before NaN gap insertion mutates the lists)
        self._cached_xs[channel_name] = list(xs_list)
        self._cached_ys[channel_name] = list(ys_list)

        # Insert NaN at gaps > threshold to create visual line breaks
        gap_s = self._gap_threshold_s
        i = 1
        while i < len(xs_list):
            if xs_list[i] - xs_list[i - 1] > gap_s:
                xs_list.insert(i, xs_list[i - 1])
                ys_list.insert(i, float("nan"))
                i += 1
            i += 1
        curve.setData(xs_list, ys_list)
        # Update viewport-decimated scatter markers
        self._refresh_scatters(channel_name)

    def _trim_buffers(self) -> None:
        if self._history_seconds <= 0 or self._t0 is None:
            return
        cutoff = time.time() - self._t0 - self._history_seconds
        for buf in self._buffers.values():
            while buf and buf[0][0] < cutoff:
                buf.popleft()

    def set_channel_units(self, channel_units: Dict[str, str]) -> None:
        """Set the unit mapping for each plot channel.

        Used by DeviceManager to tell the plot what units its device
        channels use, so the y-axis label can be auto-set and the
        config dialog can group channels by unit.
        """
        self._channel_units = dict(channel_units)
        self._update_y_label()

    def set_all_channel_units(
        self, all_channel_units: Dict[str, Dict[str, str]]
    ) -> None:
        """Set the unit mapping for *all* devices.

        Stored so that when the user switches the plot to a different
        device, the correct channel_units are automatically restored.
        """
        self._all_channel_units = {
            did: dict(units) for did, units in all_channel_units.items()
        }
        # If we already have a channel_units dict for the current device,
        # immediately apply it (in case the call arrives after device
        # selection but before data starts flowing).
        current = self._all_channel_units.get(
            self._selected_device or self._device_id
        )
        if current and not self._channel_units:
            self._channel_units = dict(current)
            self._update_y_label()

    @staticmethod
    def _channel_display_name(channel: str) -> str:
        """Extract a human-readable display name from a channel key.

        Examples::
            ch1_pressure  → "Pressure"
            ch1_rate      → "Rate"
            ch2_thickness → "Thickness"
        """
        parts = channel.split("_", 1)
        if len(parts) > 1 and parts[0].startswith("ch") and parts[0][2:].isdigit():
            name = parts[1]
        else:
            name = channel
        return name.replace("_", " ").title()

    def _unit_for_channels(self, channels: List[str]) -> str:
        """Return the common unit string for *channels*, or empty if not uniform."""
        units = {self._channel_units.get(ch, "") for ch in channels}
        non_empty = {u for u in units if u}
        return non_empty.pop() if len(non_empty) == 1 else (units.pop() if len(units) == 1 else "")

    def _ensure_single_unit_group(self) -> None:
        """Ensure only channels with the same unit are visible.

        If channels span multiple unit groups (e.g. after a device switch),
        collapses visibility to the first group so only same-unit channels
        are plotted together.  Always syncs the legend afterward.
        """
        if not self._channels:
            return
        # If we have unit info, enforce single-unit grouping
        if self._channel_units:
            # Group channels by unit, preserving insertion order
            groups: Dict[str, List[str]] = {}
            for ch in self._channels:
                unit = self._channel_units.get(ch, "")
                if unit not in groups:
                    groups[unit] = []
                groups[unit].append(ch)
            if len(groups) > 1:
                # Find groups that have at least one visible channel
                groups_with_visible = [
                    u for u, chs in groups.items()
                    if any(self._channels[ch].get("visible", True) for ch in chs)
                ]
                if len(groups_with_visible) > 1:
                    # Collapse to the first group (by insertion order) that has visible channels
                    keep_unit = groups_with_visible[0]
                    for unit, chs in groups.items():
                        visible = (unit == keep_unit)
                        for ch in chs:
                            cfg = self._channels.get(ch)
                            if cfg and cfg.get("visible", True) != visible:
                                cfg["visible"] = visible
                                self._refresh_curves(ch)
        self._refresh_scatters()
        # Always sync the legend, even for single-unit-group devices
        # where visibility may have changed via apply_visibility / set_channel_visibility.
        self._update_legend()

    def _update_legend(self) -> None:
        """Sync legend entries with current channel visibility.

        Rebuilds the legend from scratch — only visible channels appear,
        each with a friendly name like "Ch1 Pressure".  Hidden channels
        are absent from the legend entirely.
        """
        legend = self._plot.getPlotItem().legend
        if legend is None:
            return
        legend.clear()
        for name, cfg in self._channels.items():
            if not cfg.get("visible", True):
                continue
            curve = self._curves.get(name)
            if curve is not None:
                legend.addItem(curve, _channel_legend_name(name))

    def _update_y_label(self) -> None:
        """Set the y-axis label based on the common unit of visible channels."""
        visible = self.visible_channels
        if not visible:
            return

        unit = self._unit_for_channels(visible)
        name = self._channel_display_name(visible[0])

        if unit:
            self._plot.setLabel("left", f"{name} [{unit}]")
        else:
            self._plot.setLabel("left", name)
        self._y_label_set = True

    # ------------------------------------------------------------------
    # Log-viewer helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_log_column(
        log_data: LogData, channel_name: str, device_id: str = "",
    ) -> Optional[str]:
        """Find the CSV column that corresponds to *channel_name*.

        In new-format CSVs columns are prefixed with the device ID
        (e.g. ``VCU-0_ch1_pressure``).  The method tries:

        1. Exact match against ``{device_id}_{channel_name}``
        2. Prefix match for old-format files where the unit was in brackets
           (e.g. ``ch1_pressure [mbar]``).
        3. Fallback: unprefixed exact match.

        Returns ``None`` when the channel has no counterpart in the log.
        """
        prefixed = f"{device_id}_{channel_name}" if device_id else channel_name
        for header in log_data.headers:
            if header == prefixed:
                return header
        # Old-format: channel_name may appear with " [unit]" suffix
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
        segment.  Also adds a single decimated ``ScatterPlotItem``
        showing data-point markers across all segments.
        """
        # Remove any previous log curves / scatter for this channel
        for curve in self._log_curves.pop(name, []):
            self._plot.removeItem(curve)
        scatter = self._log_scatters.pop(name, None)
        if scatter is not None:
            self._plot.removeItem(scatter)

        xs, ys = self._log_data_cache.get(name, ([], []))
        if not xs:
            return

        cfg = self._channels.get(name, {})
        colour = cfg.get("colour", "#ffffff")
        visible = cfg.get("visible", True)

        segments = _split_into_segments(xs, ys, self._gap_threshold_s)
        curves: List[pg.PlotDataItem] = []
        for i, (seg_x, seg_y) in enumerate(segments):
            if not seg_x:
                continue
            curve = self._plot.plot(
                list(seg_x),
                list(seg_y),
                pen=pg.mkPen(color=colour, width=2),
                autoDownsample=True,
            )
            if not visible:
                curve.setData([], [])
            curves.append(curve)

        self._log_curves[name] = curves
        # Create scatter, then delegate to _refresh_scatters for decimation
        # (so y-log filtering is applied consistently).
        scatter = pg.ScatterPlotItem(
            [], [], symbol="o", symbolSize=SCATTER_SYMBOL_SIZE,
            brush=pg.mkBrush(colour), pen=None,
        )
        self._plot.addItem(scatter)
        self._log_scatters[name] = scatter
        self._refresh_scatters(name)

    def _clear_log_curves(self) -> None:
        """Remove all log-viewer PlotDataItem segments and scatters from the plot."""
        for curves in self._log_curves.values():
            for curve in curves:
                self._plot.removeItem(curve)
        self._log_curves.clear()
        for scatter in self._log_scatters.values():
            self._plot.removeItem(scatter)
        self._log_scatters.clear()

    def _update_x_axis_label(self, mode: str) -> None:
        """Set the x-axis label and axis type based on the current mode.

        In "absolute" mode the bottom axis is replaced with a
        ``DateAxisItem`` so Unix timestamps are displayed as HH:MM:SS
        instead of raw epoch numbers.
        """
        self._x_axis_mode = mode
        plot_item = self._plot.getPlotItem()
        axis_pen = pg.mkPen(color="#888888", width=1)
        if mode == "absolute":
            axis = pg.DateAxisItem(orientation="bottom")
            plot_item.setAxisItems({"bottom": axis})
            axis.setPen(axis_pen)
            axis.setTextPen(axis_pen)
            self._plot.setLabel("bottom", "Time")
        else:
            self._set_relative_axis(axis_pen)

    def _set_relative_axis(self, axis_pen=None) -> None:
        """Install a regular ``AxisItem`` for relative-seconds display."""
        if axis_pen is None:
            axis_pen = pg.mkPen(color="#888888", width=1)
        axis = pg.AxisItem(orientation="bottom")
        self._plot.getPlotItem().setAxisItems({"bottom": axis})
        axis.setPen(axis_pen)
        axis.setTextPen(axis_pen)
        self._plot.setLabel("bottom", "Time (s)")
