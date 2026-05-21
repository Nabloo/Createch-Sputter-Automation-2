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

from PySide6.QtCore import Qt

try:
    import serial.tools.list_ports as list_ports
    _HAS_LIST_PORTS = True
except ImportError:
    _HAS_LIST_PORTS = False
    list_ports = None  # type: ignore[assignment]

from src.acquisition.engine import AcquisitionEngine
from src.config import get_device_configs, get_plot_configs, get_device_panel_configs
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

        # Wire engine → data store
        self._engine.subscribe(self._store.on_update)

        # Build everything from config
        self._setup_devices()
        self._setup_plots()

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
        """Create PlotWidget instances from config and wire to DataStore."""
        for pc in get_plot_configs(self._config):
            device_id = pc.get("device_id", "")
            dock_id = pc.get("dock_id", f"plot_{device_id}")
            title = pc.get("title", f"{device_id} Plot")
            channels = pc.get("channels", [])
            colours = pc.get("colours")
            history = pc.get("history_seconds", 60.0)
            area = _AREA_MAP.get(pc.get("area", "right"), Qt.RightDockWidgetArea)

            plot = PlotWidget(
                device_id=device_id,
                history_seconds=history,
            )
            if channels:
                plot.set_channels(channels, colours)

            self._window.dock_manager.add_panel(dock_id, title, plot, area=area)
            self._store.subscribe(plot.push_data)
            self._plots[dock_id] = plot
            logger.debug("PlotWidget created for %s (dock %s)", device_id, dock_id)

    def _wire_toolbar(self) -> None:
        """Enable and connect MainWindow toolbar actions."""
        w = self._window
        w._start_action.setEnabled(True)
        w._stop_action.setEnabled(True)
        w._connect_action.setEnabled(True)
        w._disconnect_action.setEnabled(True)

        w._start_action.triggered.connect(self.start_all)
        w._stop_action.triggered.connect(self.stop_all)
        w._connect_action.triggered.connect(self.connect_all)
        w._disconnect_action.triggered.connect(self.disconnect_all)

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
