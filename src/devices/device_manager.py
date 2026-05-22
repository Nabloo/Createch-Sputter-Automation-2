"""Central device registry – wires config, drivers, engine, store, and GUI.

Orchestrates the four-layer architecture:
  Device Drivers → Acquisition Engine → Shared Data Model → GUI

Responsibilities:
- Create device instances from config
- Wire engine → datastore → GUI panels/plots
- Manage connections (individual and bulk)
- Manage acquisition start/stop
- COM-port auto-detection / device discovery
- Toolbar and status-bar integration
"""

import logging
import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QDateTime, Qt, QTimer
from PySide6.QtWidgets import QFileDialog

try:
    import serial.tools.list_ports as list_ports
    _HAS_LIST_PORTS = True
except ImportError:
    _HAS_LIST_PORTS = False
    list_ports = None  # type: ignore[assignment]

from src.acquisition.engine import AcquisitionEngine
from src.config import get_device_configs, get_plot_configs, get_device_panel_configs, find_device_config, save_config, set_plot_configs
from src.data.datastore import DataStore
from src.data_logging.data_logger import DataLogger
from src.data_logging.log_reader import LogData, LogFileReader
from src.devices.base_device import BaseDevice
from src.devices.sqm_controller import SQMController
from src.devices.vcu_controller import VCUController
from src.gui.device_panel import DevicePanel
from src.gui.plot_widget import PlotWidget

logger = logging.getLogger(__name__)

_DEVICE_TYPE_MAP: Dict[str, type] = {
    "SQMController": SQMController,
    "VCUController": VCUController,
}

_AREA_MAP: Dict[str, Qt.DockWidgetArea] = {
    "left": Qt.LeftDockWidgetArea,
    "right": Qt.RightDockWidgetArea,
    "top": Qt.TopDockWidgetArea,
    "bottom": Qt.BottomDockWidgetArea,
}


class DeviceManager:
    """Central registry and coordinator for all hardware devices.

    Usage::

        dm = DeviceManager(config, main_window)
        dm.start_all()          # connect + start polling all devices
        dm.stop_all()           # stop polling, keep devices connected
        dm.shutdown()           # stop + disconnect + cleanup
    """

    def __init__(self, config: Dict[str, Any], main_window) -> None:
        self._config = config
        self._window = main_window

        self._engine = AcquisitionEngine()
        self._store = DataStore(history_size=10_000)

        # device_id → BaseDevice
        self._devices: Dict[str, BaseDevice] = {}
        # device_id → DevicePanel
        self._panels: Dict[str, DevicePanel] = {}
        # plot dock_id → PlotWidget
        self._plots: Dict[str, PlotWidget] = {}
        # Shared x-axis origin — a mutable container updated by the first
        # PlotWidget that receives data.  All plots share the same list
        # reference; when any plot writes ts_float into [0], everyone
        # (including plots added later) sees the same origin.
        self._global_t0: List[Optional[float]] = [None]

        # Wire engine → data store
        self._engine.subscribe(self._store.on_update)

        # Route engine poll errors to the status bar (thread-safe via Signal)
        self._engine.set_error_callback(self._window.error_occurred.emit)

        # Build everything from config
        self._setup_devices()
        self._setup_plots()

        # CSV data logger (subscribes to DataStore for automated logging)
        self._data_logger = DataLogger(config, self._store)

        # ---- Log-viewer state ---------------------------------------------------
        self._view_mode_live = True
        self._log_reader = LogFileReader()
        self._log_data: Optional[LogData] = None
        self._log_filepath: str = ""
        # Remember the last folder used for opening log files.
        # Initialise from config so the user's last-used folder persists
        # across restarts.  Falls back to logging.directory (for saving)
        # then to "logs" as ultimate default.
        self._last_log_dir: str = (
            config.get("gui", {}).get("last_log_open_dir")
            or config.get("logging", {}).get("directory", "logs")
        )
        # Wire log-reader signals
        self._log_reader.loaded.connect(self._on_log_loaded)
        self._log_reader.error_occurred.connect(self._window.error_occurred.emit)

        # Balance dock sizes after the event loop starts (when heights are known)
        QTimer.singleShot(0, self._balance_docks)

        # Wire toolbar and status bar
        self._wire_toolbar()
        self._wire_status_timer()

        logger.info(
            "DeviceManager initialized: %d device(s), %d plot(s)",
            len(self._devices), len(self._plots),
        )

    # ------------------------------------------------------------------
    # Device registry
    # ------------------------------------------------------------------

    @property
    def device_ids(self) -> List[str]:
        return list(self._devices.keys())

    @property
    def engine(self) -> AcquisitionEngine:
        return self._engine

    @property
    def store(self) -> DataStore:
        return self._store

    def device(self, device_id: str) -> Optional[BaseDevice]:
        return self._devices.get(device_id)

    def panel(self, device_id: str) -> Optional[DevicePanel]:
        return self._panels.get(device_id)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect_device(self, device_id: str) -> bool:
        """Connect a single device. Returns True on success."""
        device = self._devices.get(device_id)
        if device is None:
            msg = f"Unknown device {device_id}"
            logger.warning("connect_device: %s", msg)
            self._window.error_occurred.emit(msg)
            return False
        if device.connected:
            logger.info("%s already connected", device_id)
            return True
        try:
            ok = device.connect()
        except Exception as exc:
            msg = f"{device_id}: connection error — {exc}"
            logger.warning("connect_device: %s", msg)
            self._window.error_occurred.emit(msg)
            return False
        if not ok:
            msg = f"{device_id}: failed to connect"
            logger.warning("connect_device: %s", msg)
            self._window.error_occurred.emit(msg)
        else:
            logger.info("%s connected", device_id)
        panel = self._panels.get(device_id)
        if panel:
            panel.set_connected(ok)
        self._refresh_status()
        return ok

    def disconnect_device(self, device_id: str) -> None:
        """Disconnect a single device and stop its reconnect loop."""
        device = self._devices.get(device_id)
        if device is None:
            return
        device.stop_reconnect_loop()
        device.disconnect()
        panel = self._panels.get(device_id)
        if panel:
            panel.set_connected(False)
        self._refresh_status()
        logger.info("%s disconnected", device_id)

    def connect_all(self) -> None:
        """Connect every registered device."""
        for device_id in self._devices:
            self.connect_device(device_id)

    def disconnect_all(self) -> None:
        """Disconnect every registered device."""
        for device_id in self._devices:
            self.disconnect_device(device_id)

    # ------------------------------------------------------------------
    # Acquisition control
    # ------------------------------------------------------------------

    def start_all(self) -> None:
        """Start polling all registered devices."""
        self._engine.start()
        for device_id, panel in self._panels.items():
            dev = self._devices.get(device_id)
            if dev:
                panel.set_connected(dev.connected)
        self._refresh_status()

    def stop_all(self) -> None:
        """Stop polling (devices stay connected)."""
        self._engine.stop()
        self._refresh_status()

    # ------------------------------------------------------------------
    # COM-port discovery
    # ------------------------------------------------------------------

    @staticmethod
    def list_available_ports() -> List[Dict[str, Any]]:
        """Return a list of available serial ports with metadata.

        Each entry: {"port": "COM3", "description": "...", "hwid": "..."}
        """
        if not _HAS_LIST_PORTS:
            logger.warning("serial.tools.list_ports not available")
            return []
        ports = []
        for port in list_ports.comports():
            ports.append({
                "port": port.device,
                "description": port.description or "",
                "hwid": port.hwid or "",
            })
        return ports

    @staticmethod
    def probe_port(port: str, baudrate: int = 9600, timeout: float = 1.0) -> Optional[str]:
        """Try to identify a device on *port* by sending RVN and reading response.

        Returns the firmware version string if a VCU responds, or None.
        """
        try:
            import serial
            ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
            ser.write(b"RVN\r")
            response = ser.readline().decode("ascii", errors="replace").strip()
            ser.close()
            if response and not response.startswith("?"):
                return response
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Log-viewer mode orchestration (T4)
    # ------------------------------------------------------------------

    def set_view_mode(self, live: bool) -> None:
        """Switch the application between Live and View Log mode.

        Parameters
        ----------
        live:
            ``True`` → Live mode (plots show real-time data).
            ``False`` → View Log mode (plots show CSV log data).
        """
        if self._view_mode_live == live:
            return  # no-op

        self._view_mode_live = live
        w = self._window

        if not live:
            # ---- Switch to View Log mode ----
            w._mode_toggle.blockSignals(True)
            w._mode_toggle.setChecked(True)
            w._mode_toggle.setText("\u26ab  View Log")
            w._mode_toggle.blockSignals(False)
            w.set_log_controls_visible(True)

            # If a log file was previously loaded, display it
            if self._log_data is not None:
                self._apply_log_data_to_plots()
        else:
            # ---- Switch to Live mode ----
            w._mode_toggle.blockSignals(True)
            w._mode_toggle.setChecked(False)
            w._mode_toggle.setText("\u26ab  Live")
            w._mode_toggle.blockSignals(False)
            w.set_log_controls_visible(False)

            # Restore live rendering on all plots
            for plot in self._plots.values():
                plot.clear_log_data()

                # Replay all historical data so the new plot shows everything
                # from the beginning of the measurement.
                history: Dict[str, list] = {}
                for ch in plot.channels:
                    history[ch] = self._store.get_history(plot.device_id, ch)
                if any(history.values()):
                    plot.load_history(history)

        logger.info("View mode switched to %s", "Live" if live else "View Log")

    def _load_log_file(self, filepath: str) -> None:
        """Start loading a CSV log file in a worker thread."""
        self._log_filepath = filepath
        self._window.temporary_status(f"Loading {os.path.basename(filepath)} …")
        self._log_reader.load(filepath)

    def _on_log_loaded(self, log_data: LogData) -> None:
        """Slot: worker thread finished parsing the CSV."""
        self._log_data = log_data
        w = self._window
        basename = os.path.basename(self._log_filepath)
        w._file_label.setText(basename)
        w._file_label.setStyleSheet("color: #66bb6a; padding: 0 4px;")

        # Set From/To to the full time range of the log.
        # Strip timezone so comparisons in load_log_data work
        # regardless of timezone-aware vs naive datetime mismatch.
        if log_data.timestamps:
            t0 = log_data.timestamps[0].replace(tzinfo=None)
            t1 = log_data.timestamps[-1].replace(tzinfo=None)
            from_qdt = QDateTime(t0.year, t0.month, t0.day,
                                 t0.hour, t0.minute, t0.second)
            to_qdt = QDateTime(t1.year, t1.month, t1.day,
                               t1.hour, t1.minute, t1.second)
            w._from_dt.blockSignals(True)
            w._to_dt.blockSignals(True)
            w._from_dt.setDateTime(from_qdt)
            w._to_dt.setDateTime(to_qdt)
            w._from_dt.blockSignals(False)
            w._to_dt.blockSignals(False)

        self._apply_log_data_to_plots()
        self._window.temporary_status(
            f"Loaded {basename} — {len(log_data.timestamps)} rows"
        )
        logger.info(
            "Log file loaded: %s (%d rows, %d columns)",
            basename, len(log_data.timestamps), len(log_data.values),
        )

    def _apply_log_data_to_plots(self) -> None:
        """Render the currently-loaded LogData on every PlotWidget."""
        if self._log_data is None:
            return

        w = self._window
        from_qdt = w._from_dt.dateTime()
        to_qdt = w._to_dt.dateTime()
        # Strip timezone to match the naive datetimes in LogData (we
        # already stripped timezone in _on_log_loaded when creating
        # the QDateTime bounds).
        t_start = from_qdt.toPython().replace(tzinfo=None)
        t_end = to_qdt.toPython().replace(tzinfo=None)

        x_axis_mode = "relative" if w._xaxis_combo.currentIndex() == 0 else "absolute"

        for plot in self._plots.values():
            plot.load_log_data(
                self._log_data,
                t_range_start=t_start,
                t_range_end=t_end,
                x_axis_mode=x_axis_mode,
            )

        logger.debug(
            "Applied log data to %d plots (range %s … %s, %s)",
            len(self._plots), from_qdt.toString("yyyy-MM-dd HH:mm"),
            to_qdt.toString("yyyy-MM-dd HH:mm"), x_axis_mode,
        )

    # ------------------------------------------------------------------
    # GUI wiring (internal)
    # ------------------------------------------------------------------

    def _setup_devices(self) -> None:
        """Create device instances and panels from config."""
        panel_cfgs = {pc["device_id"]: pc for pc in get_device_panel_configs(self._config)}

        for dev_cfg in get_device_configs(self._config):
            device = self._create_device(dev_cfg)
            if device is None:
                continue

            device_id = device.device_id
            self._devices[device_id] = device

            poll_interval = dev_cfg.get("poll_interval", 1.0)
            self._engine.add_device(device, poll_interval)

            panel = self._create_panel(device, dev_cfg, panel_cfgs.get(device_id))
            if panel:
                self._panels[device_id] = panel
                # Wire panel buttons → DeviceManager actions
                panel.connect_requested.connect(self.connect_device)
                panel.disconnect_requested.connect(self.disconnect_device)
                # Wire store → panel (live values)
                self._store.subscribe(panel.push_data)

    def _create_device(self, cfg: Dict[str, Any]) -> Optional[BaseDevice]:
        """Instantiate a device driver from its config dict."""
        dev_type = cfg.get("type", "")
        cls = _DEVICE_TYPE_MAP.get(dev_type)
        if cls is None:
            msg = f"Unknown device type {dev_type!r}; skipping"
            logger.warning(msg)
            self._window.error_occurred.emit(msg)
            return None
        try:
            return cls(cfg)
        except Exception as exc:
            msg = f"Failed to create {dev_type}: {exc}"
            logger.exception(msg)
            self._window.error_occurred.emit(msg)
            return None

    def _create_panel(
        self,
        device: BaseDevice,
        dev_cfg: Dict[str, Any],
        panel_cfg: Optional[Dict[str, Any]],
    ) -> Optional[DevicePanel]:
        """Create a DevicePanel and add it to the dock."""
        num_channels = dev_cfg.get("number_of_sensors", 1)
        dev_type = dev_cfg.get("type", "Unknown")
        panel = DevicePanel(
            device.device_id,
            device_type=dev_type,
            num_channels=num_channels,
        )
        area = Qt.LeftDockWidgetArea
        allowedAreas = Qt.LeftDockWidgetArea
        if panel_cfg:
            area = _AREA_MAP.get(panel_cfg.get("area", "left"), Qt.LeftDockWidgetArea)
        dock_id = panel_cfg.get("dock_id", f"device_{device.device_id}") if panel_cfg else f"device_{device.device_id}"
        dock = self._window.dock_manager.add_panel(
            dock_id,
            device.device_id,
            panel,
            area=area,
            allowed_areas=allowedAreas,
        )
        # Give the device dock a maximum width so it doesn't take up
        # unnecessary horizontal space (Qt splits left/right areas
        # equally by default, but the panel only needs ~300 px).
        dock.setMaximumWidth(340)
        logger.debug("DevicePanel created for %s", device.device_id)
        return panel

    def _setup_plots(self) -> None:
        """Create PlotWidget instances from config and wire to DataStore.

        Always derives the *full* channel list from the device config so
        every channel is available for re-enabling, even if only a subset
        was visible when the config was saved.
        """
        device_ids = list(self._devices.keys())
        for pc in get_plot_configs(self._config):
            device_id = pc.get("device_id", "")
            dock_id = pc.get("dock_id", f"plot_{device_id}")
            history = pc.get("history_seconds", 0)
            area = _AREA_MAP.get(pc.get("area", "right"), Qt.RightDockWidgetArea)
            allowedAreas = Qt.RightDockWidgetArea

            # Always derive the full channel list from the device config.
            # The saved "channels" / "colours" lists may be incomplete
            # (e.g. from an older config that only stored visible channels).
            dev_cfg = find_device_config(self._config, device_id)
            if dev_cfg:
                # Use the device's actual channel list if we have the device
                dev = self._devices.get(device_id)
                if dev:
                    all_channels = list(dev.channels)
                else:
                    num_sensors = dev_cfg.get("number_of_sensors", 1)
                    all_channels = [
                        f"ch{i}_pressure" for i in range(1, num_sensors + 1)
                    ]
            else:
                all_channels = pc.get("channels", [])

            # Merge saved colours: saved channels keep their colour;
            # newly-derived channels get auto-assigned later.
            saved_channels = pc.get("channels", [])
            saved_colours = pc.get("colours", []) or []
            colour_map: Dict[str, str] = {}
            for ch, col in zip(saved_channels, saved_colours):
                colour_map[ch] = col
            merged_colours = [colour_map[ch] for ch in all_channels if ch in colour_map]

            plot = PlotWidget(
                device_id=device_id,
                history_seconds=history,
                global_t0=self._global_t0,
            )
            plot.set_available_devices(device_ids)
            if all_channels:
                plot.set_channels(all_channels, merged_colours or None)

            # Restore visibility state from saved config.
            visibility = pc.get("visibility")
            if visibility:
                plot.apply_visibility(visibility)

            # Restore y-axis log scale
            if pc.get("y_log", False):
                plot.set_y_log(True)

            # Wire remove button
            plot.remove_requested.connect(lambda did=dock_id: self.remove_plot(did))
            # Persist state changes (device switch, channel config, etc.)
            plot.state_changed.connect(lambda did=dock_id: self._on_plot_state_changed(did))

            self._window.dock_manager.add_panel(dock_id, "", plot, area=area, allowed_areas=allowedAreas)
            self._store.subscribe(plot.push_data)
            self._plots[dock_id] = plot
            logger.debug("PlotWidget created for %s (dock %s)", device_id, dock_id)

    # ------------------------------------------------------------------
    # Dynamic plot management
    # ------------------------------------------------------------------

    def add_plot(self) -> None:
        """Add a new plot widget for the first device with all channels enabled.

        No dialog — the user can change device/channels later via the
        plot's own dropdown and Channels button.

        The new plot inherits the history window and x-axis origin
        from existing plots so it stays in sync.  All historical data
        already in the DataStore is replayed so the plot shows data
        from the beginning of the measurement, not just from now.
        """
        device_ids = list(self._devices.keys())
        if not device_ids:
            logger.warning("No devices available; cannot add plot")
            return

        default_device = device_ids[0]
        dev = self._devices.get(default_device)
        channels = list(dev.channels) if dev else ["ch1_pressure"]

        # Inherit history and x-axis origin from existing plots
        existing = list(self._plots.values())
        shared_history = existing[0].history_seconds if existing else 0

        last_plot_id = list(self._plots.keys())[-1].split("_")[-1]
        try:
            last_plot_id = int(last_plot_id)
        except ValueError:
            last_plot_id = 0

        dock_id = f"plot_{last_plot_id+1}"

        plot = PlotWidget(
            device_id=default_device,
            history_seconds=shared_history,
            global_t0=self._global_t0,
        )
        plot.set_available_devices(device_ids)
        plot.set_channels(channels)

        self._window.dock_manager.add_panel(
            panel_id=dock_id,
            title="",
            widget=plot,
            area=Qt.RightDockWidgetArea,
            allowed_areas=Qt.RightDockWidgetArea,
        )
        self._store.subscribe(plot.push_data)
        self._plots[dock_id] = plot

        # Replay all historical data so the new plot shows everything
        # from the beginning of the measurement.
        history: Dict[str, list] = {}
        for ch in channels:
            history[ch] = self._store.get_history(default_device, ch)
        if any(history.values()):
            plot.load_history(history)

        # Wire remove button
        plot.remove_requested.connect(lambda did=dock_id: self.remove_plot(did))
        # Persist state changes
        plot.state_changed.connect(lambda did=dock_id: self._on_plot_state_changed(did))

        if not self._view_mode_live:
            self._apply_log_data_to_plots()

        self._balance_docks()

        # Persist the new plot config immediately
        self._persist_plots()

        logger.info("Added plot %r for %s: %s", dock_id, default_device, channels)

    def remove_plot(self, dock_id: str) -> None:
        """Remove a plot by its dock ID."""
        plot = self._plots.pop(dock_id, None)
        if plot is None:
            return
        self._store.unsubscribe(plot.push_data)
        self._window.dock_manager.remove_panel(dock_id)
        self._balance_docks()
        self._persist_plots()
        logger.info("Removed plot %r", dock_id)

    def _on_plot_state_changed(self, dock_id: str) -> None:
        """Called when a plot's device, channels or visibility changes."""
        self._persist_plots()

    def _sync_history(self, seconds: float) -> None:
        """Propagate *seconds* as the history window to every plot.

        When the window is enlarged, backfills historical data from
        the DataStore so previously trimmed data reappears.
        """
        # Keep toolbar in sync (avoid signal loop)
        w = self._window
        if int(w._history_spin.value()) != int(seconds):
            w._history_spin.blockSignals(True)
            w._history_spin.setValue(int(seconds))
            w._history_spin.blockSignals(False)

        for plot in self._plots.values():
            if plot.history_seconds != seconds:
                was_smaller = plot.history_seconds > 0 and (
                    seconds <= 0 or seconds > plot.history_seconds
                )
                plot.set_history_seconds(seconds)
                # Backfill from DataStore when the window was enlarged
                if was_smaller:
                    plot.backfill_from_store(
                        self._store, plot.device_id,
                    )
        self._persist_plots()

    def _on_history_changed(self, value: int) -> None:
        """Toolbar history spinner changed — propagate to all plots."""
        self._sync_history(float(value))

    def _persist_plots(self) -> None:
        """Save all current plot configurations to config.json.

        Only visible channels are saved; hidden channels are omitted so they
        don't reappear on next launch.
        """
        plots_data: List[Dict[str, Any]] = []
        for dock_id, plot in self._plots.items():
            dock = self._window.dock_manager.panel(dock_id)
            area = "right"
            if dock is not None:
                wa = self._window.dockWidgetArea(dock)
                for name, val in _AREA_MAP.items():
                    if val == wa:
                        area = name
                        break
            all_channels = plot.channels
            all_colours = plot.channel_colours
            visibility = plot.channel_visibility
            plots_data.append({
                "dock_id": dock_id,
                "device_id": plot.device_id,
                "area": area,
                "channels": all_channels,
                "colours": all_colours,
                "visibility": visibility,
                "history_seconds": plot.history_seconds,
                "y_log": plot.y_log,
            })
        set_plot_configs(self._config, plots_data)
        save_config(self._config)
        logger.debug("Plot configurations persisted (%d plots)", len(plots_data))

    def _balance_docks(self) -> None:
        """Give device panels minimal space and distribute the rest among
        plot docks, so plots get as much screen real estate as possible."""
        device_docks = [
            self._window.dock_manager.panel(f"device_{did}")
            for did in self._devices
        ]
        plot_docks = [
            self._window.dock_manager.panel(did)
            for did in self._plots
        ]
        device_docks = [d for d in device_docks if d is not None]
        plot_docks = [d for d in plot_docks if d is not None]

        all_docks = device_docks + plot_docks
        if len(all_docks) < 2:
            return

        # Only balance vertical docks (left/right areas)
        first_area = self._window.dockWidgetArea(all_docks[0])
        if first_area not in (Qt.LeftDockWidgetArea, Qt.RightDockWidgetArea):
            return

        total_height = sum(d.height() for d in all_docks)
        # Device panels get exactly their size-hint height (never more)
        device_sizes: List[int] = []
        for d in device_docks:
            hint = d.widget().sizeHint().height() if d.widget() else 80
            device_sizes.append(min(hint, d.height()))

        device_allocated = sum(device_sizes)
        remaining = max(0, total_height - device_allocated)
        if plot_docks:
            each_plot = max(100, remaining // len(plot_docks))
            plot_sizes = [each_plot] * len(plot_docks)
            sizes = device_sizes + plot_sizes
            self._window.resizeDocks(all_docks, sizes, Qt.Vertical)

        # ---- Horizontal balancing: device docks capped, plots fill the rest ----
        for d in device_docks:
            d.setMinimumWidth(0)
            d.setMaximumWidth(340)
        for d in plot_docks:
            d.setMinimumWidth(300)
            d.setMaximumWidth(10000)

    def clear_all_plots(self) -> None:
        """Clear all plot data without removing the plot widgets."""
        for plot in self._plots.values():
            plot.clear()
        logger.info("Cleared all %d plot(s)", len(self._plots))

    def _wire_toolbar(self) -> None:
        """Enable and connect MainWindow toolbar actions."""
        w = self._window
        w._start_action.setEnabled(True)
        w._stop_action.setEnabled(True)
        w._connect_action.setEnabled(True)
        w._disconnect_action.setEnabled(True)
        w._add_plot_action.setEnabled(True)
        w._clear_all_action.setEnabled(True)

        w._start_action.triggered.connect(self.start_all)
        w._stop_action.triggered.connect(self.stop_all)
        w._connect_action.triggered.connect(self.connect_all)
        w._disconnect_action.triggered.connect(self.disconnect_all)
        w._add_plot_action.triggered.connect(self.add_plot)
        w._clear_all_action.triggered.connect(self.clear_all_plots)

        # History window spinner — same for all plots
        first_plot = next(iter(self._plots.values()), None)
        if first_plot is not None:
            w._history_spin.setValue(int(first_plot.history_seconds))
        w._history_spin.valueChanged.connect(self._on_history_changed)

        # ---- Log viewer controls ----
        w._mode_toggle.toggled.connect(lambda checked: self.set_view_mode(not checked))
        w._load_btn.clicked.connect(self._on_load_clicked)
        w._logdir_action.triggered.connect(self._on_choose_log_directory)
        w._xaxis_combo.currentIndexChanged.connect(self._on_xaxis_changed)
        # Use editingFinished to avoid re-rendering on every keystroke
        w._from_dt.editingFinished.connect(self._on_time_range_changed)
        w._to_dt.editingFinished.connect(self._on_time_range_changed)

    def _on_load_clicked(self) -> None:
        """Open a QFileDialog and start loading the selected CSV.

        Remembers the last-used folder across invocations and persists
        it via the logging.directory config entry.
        """
        filepath, _ = QFileDialog.getOpenFileName(
            self._window,
            "Open Log File",
            self._last_log_dir,
            "CSV Files (*.csv);;All Files (*)",
        )
        if filepath:
            # Remember this folder for next time (separate key from the
            # menu-chosen logging.directory, so browsing doesn't overwrite
            # the user's preferred save location).
            self._last_log_dir = os.path.dirname(filepath)
            self._config.setdefault("gui", {})["last_log_open_dir"] = self._last_log_dir
            save_config(self._config)
            self._load_log_file(filepath)

    def _on_xaxis_changed(self, _index: int) -> None:
        """X-axis mode combo changed — re-render log data."""
        if not self._view_mode_live and self._log_data is not None:
            self._apply_log_data_to_plots()

    def _on_time_range_changed(self) -> None:
        """From/To datetime changed — re-render log data."""
        if not self._view_mode_live and self._log_data is not None:
            self._apply_log_data_to_plots()

    def _on_choose_log_directory(self) -> None:
        """Open a folder picker and persist the chosen log directory."""
        directory = QFileDialog.getExistingDirectory(
            self._window,
            "Choose Log Directory",
            self._last_log_dir,
        )
        if directory:
            self._last_log_dir = directory
            self._config.setdefault("logging", {})["directory"] = directory
            save_config(self._config)
            self._window.temporary_status(f"Log directory: {directory}")
            logger.info("Log directory changed to %s", directory)

    def _wire_status_timer(self) -> None:
        """Wire MainWindow's existing status refresh timer to our method."""
        self._window._status_timer.timeout.connect(self._refresh_status)

    def _refresh_status(self) -> None:
        """Update status bar and panel connection states."""
        connected = sum(1 for d in self._devices.values() if d.connected)
        total = len(self._devices)
        self._window._status_connected.setText(f"Devices: {connected}/{total}")
        self._window._status_acq.setText(
            "Running" if self._engine.is_running else "Idle"
        )
        # Keep panels in sync with actual device connection states
        # (e.g. after silent auto-reconnect by BaseDevice's reconnect loop)
        for device_id, device in self._devices.items():
            panel = self._panels.get(device_id)
            if panel and panel.is_connected != device.connected:
                panel.set_connected(device.connected)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop acquisition, stop reconnect loops, disconnect, and clean up."""
        self._engine.stop()
        self._data_logger.shutdown()
        for device_id, device in list(self._devices.items()):
            device.stop_reconnect_loop()
            device.disconnect()
            panel = self._panels.get(device_id)
            if panel:
                panel.set_connected(False)
        self._store.clear()
        logger.info("DeviceManager shutdown complete")
