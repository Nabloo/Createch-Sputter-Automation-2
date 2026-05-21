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
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer

try:
    import serial.tools.list_ports as list_ports
    _HAS_LIST_PORTS = True
except ImportError:
    _HAS_LIST_PORTS = False
    list_ports = None  # type: ignore[assignment]

from src.acquisition.engine import AcquisitionEngine
from src.config import get_device_configs, get_plot_configs, get_device_panel_configs, find_device_config, save_config, set_plot_configs
from src.data.datastore import DataStore
from src.devices.base_device import BaseDevice
from src.devices.vcu_controller import VCUController
from src.gui.device_panel import DevicePanel
from src.gui.plot_widget import PlotWidget

logger = logging.getLogger(__name__)

_DEVICE_TYPE_MAP: Dict[str, type] = {
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

        # Build everything from config
        self._setup_devices()
        self._setup_plots()

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
            logger.warning("connect_device: unknown device %r", device_id)
            return False
        if device.connected:
            logger.info("%s already connected", device_id)
            return True
        ok = device.connect()
        logger.info("connect_device: %s -> %s", device_id, ok)
        panel = self._panels.get(device_id)
        if panel:
            panel.set_connected(ok)
            if ok and device.sensor_name != "Unknown":  # type: ignore[attr-defined]
                panel.set_device_info(
                    sensor_name=device.sensor_name,  # type: ignore[attr-defined]
                    firmware=device.firmware_version or "",  # type: ignore[attr-defined]
                )
        self._refresh_status()
        if ok:
            logger.info("%s connected", device_id)
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
            logger.warning("Unknown device type %r; skipping", dev_type)
            return None
        try:
            return cls(cfg)
        except Exception:
            logger.exception("Failed to create %s", dev_type)
            return None

    def _create_panel(
        self,
        device: BaseDevice,
        dev_cfg: Dict[str, Any],
        panel_cfg: Optional[Dict[str, Any]],
    ) -> Optional[DevicePanel]:
        """Create a DevicePanel and add it to the dock."""
        num_channels = dev_cfg.get("number of pressure sensors", 1)
        dev_type = dev_cfg.get("type", "Unknown")
        panel = DevicePanel(
            device.device_id,
            device_type=dev_type,
            num_channels=num_channels,
        )
        area = Qt.LeftDockWidgetArea
        if panel_cfg:
            area = _AREA_MAP.get(panel_cfg.get("area", "left"), Qt.LeftDockWidgetArea)
        dock_id = panel_cfg.get("dock_id", f"device_{device.device_id}") if panel_cfg else f"device_{device.device_id}"
        self._window.dock_manager.add_panel(
            dock_id,
            device.device_id,
            panel,
            area=area,
        )
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

            # Always derive the full channel list from the device config.
            # The saved "channels" / "colours" lists may be incomplete
            # (e.g. from an older config that only stored visible channels).
            dev_cfg = find_device_config(self._config, device_id)
            if dev_cfg:
                num_sensors = dev_cfg.get("number of pressure sensors", 1)
                all_channels = [f"ch{i}_pressure" for i in range(1, num_sensors + 1)]
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

            # Wire remove button
            plot.remove_requested.connect(lambda did=dock_id: self.remove_plot(did))
            # Persist state changes (device switch, channel config, etc.)
            plot.state_changed.connect(lambda did=dock_id: self._on_plot_state_changed(did))

            self._window.dock_manager.add_panel(dock_id, "", plot, area=area)
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
        from existing plots so it stays in sync.
        """
        device_ids = list(self._devices.keys())
        if not device_ids:
            logger.warning("No devices available; cannot add plot")
            return

        default_device = device_ids[0]
        dev_cfg = find_device_config(self._config, default_device)
        num = dev_cfg.get("number of pressure sensors", 1) if dev_cfg else 1
        channels = [f"ch{i}_pressure" for i in range(1, num + 1)]

        # Inherit history and x-axis origin from existing plots
        existing = list(self._plots.values())
        shared_t0 = existing[0].t0 if existing else self._global_t0[0]
        shared_history = existing[0].history_seconds if existing else 0

        dock_id = f"plot_{len(self._plots)}"

        plot = PlotWidget(
            device_id=default_device,
            history_seconds=shared_history,
            global_t0=self._global_t0,
        )
        plot.set_available_devices(device_ids)
        plot.set_channels(channels)

        self._window.dock_manager.add_panel(
            dock_id, "", plot, area=Qt.RightDockWidgetArea,
        )
        self._store.subscribe(plot.push_data)
        self._plots[dock_id] = plot

        # Wire remove button
        plot.remove_requested.connect(lambda did=dock_id: self.remove_plot(did))
        # Persist state changes
        plot.state_changed.connect(lambda did=dock_id: self._on_plot_state_changed(did))

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
        """Called when a plot's device, channels, visibility, or history changes."""
        plot = self._plots.get(dock_id)
        if plot is not None:
            self._sync_history(plot.history_seconds)
        self._persist_plots()

    def _sync_history(self, seconds: float) -> None:
        """Propagate *seconds* as the history window to every plot."""
        for plot in self._plots.values():
            if plot.history_seconds != seconds:
                plot.set_history_seconds(seconds)

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
        for device_id, device in list(self._devices.items()):
            device.stop_reconnect_loop()
            device.disconnect()
            panel = self._panels.get(device_id)
            if panel:
                panel.set_connected(False)
        self._store.clear()
        logger.info("DeviceManager shutdown complete")
