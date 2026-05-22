"""Abstract base class for all hardware device drivers.

Provides serial port management, connection state tracking,
reconnect logic, and the interface that all device drivers must implement.
"""

from abc import ABC, abstractmethod
import logging
import threading
import time
from typing import Any, Dict, Optional

import serial

logger = logging.getLogger(__name__)


class BaseDevice(ABC):
    """Abstract base class for all hardware device drivers.

    Subclasses must implement:
        - device_id property
        - channels property
        - poll() method

    Serial port management, connect/disconnect, and reconnection
    logic are handled here.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.RLock()  # reentrant: _after_connect may call _send_command
        self._connected = False
        self._running = False
        self._reconnect_thread: Optional[threading.Thread] = None

        self._port: str = config["port"]
        self._baudrate: int = config.get("baudrate", 9600)
        self._bytesize: int = config.get("bytesize", serial.EIGHTBITS)
        self._parity: str = config.get("parity", serial.PARITY_NONE)
        self._stopbits: float = config.get("stopbits", serial.STOPBITS_ONE)
        self._timeout: float = config.get("timeout", 1.0)
        self._write_timeout: float = config.get("write_timeout", 1.0)

        self._reconnect_interval: float = config.get("reconnect_interval", 2.0)
        self._max_retries: int = config.get("max_retries", 0)
        self._retry_count: int = 0

    @property
    @abstractmethod
    def device_id(self) -> str:
        """Unique identifier for this device instance."""
        ...

    @property
    @abstractmethod
    def plot_channels(self) -> list[str]:
        """Names of channels suitable for plotting (numeric measurement values)."""
        ...

    @property
    @abstractmethod
    def status_channels(self) -> list[str]:
        """Names of all channels to display in the device-status panel.

        May include non-plottable channels such as status codes and
        status text strings.  Every key returned by ``poll()`` (after
        flattening) that has a corresponding label here will be shown.
        """
        ...

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port(self) -> str:
        return self._port

    @property
    def config(self) -> Dict[str, Any]:
        return dict(self._config)

    def connect(self) -> bool:
        """Open serial connection. Returns True on success."""
        with self._lock:
            if self._connected:
                return True
            try:
                logger.info("Connecting to %s on %s at %d baud", self.device_id, self._port, self._baudrate)
                self._serial = serial.Serial(
                    port=self._port,
                    baudrate=self._baudrate,
                    bytesize=self._bytesize,
                    parity=self._parity,
                    stopbits=self._stopbits,
                    timeout=self._timeout,
                    write_timeout=self._write_timeout,
                )
                self._connected = True
                self._retry_count = 0
                logger.info("Connected to %s on %s", self.device_id, self._port)
                self._after_connect()
                return True
            except serial.SerialException as e:
                logger.error("Failed to connect to %s on %s: %s", self.device_id, self._port, e)
                self._serial = None
                self._connected = False
                return False

    def __enter__(self) -> "BaseDevice":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    def disconnect(self) -> None:
        """Close serial connection."""
        with self._lock:
            self._connected = False
            if self._serial and self._serial.is_open:
                try:
                    self._serial.close()
                except serial.SerialException as e:
                    logger.warning("Error closing %s: %s", self._port, e)
            self._serial = None
            logger.info("Disconnected from %s", self.device_id)

    def start_reconnect_loop(self) -> None:
        """Start background reconnection thread."""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._running = True
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_worker,
            name=f"reconnect-{self.device_id}",
            daemon=True,
        )
        self._reconnect_thread.start()

    def stop_reconnect_loop(self, join_timeout: float = 3.0) -> None:
        """Stop background reconnection thread and wait for it to finish."""
        self._running = False
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            self._reconnect_thread.join(timeout=join_timeout)

    @abstractmethod
    def poll(self) -> Dict[str, Any]:
        """Poll device and return current readings.

        Returns:
            Dict mapping channel names to values.
            Example: {"CH1": 1.2e-5, "CH2": 3.4e-6}

        Raises:
            ConnectionError: if device is not connected.
            TimeoutError: if device does not respond.
        """
        ...

    def _write(self, data: bytes) -> None:
        if not self._serial or not self._serial.is_open:
            raise ConnectionError(f"Serial port {self._port} is not open")
        self._serial.write(data)

    def _readline(self) -> bytes:
        if not self._serial or not self._serial.is_open:
            raise ConnectionError(f"Serial port {self._port} is not open")
        line = self._serial.readline()
        if not line:
            raise TimeoutError(f"Read timeout on {self._port}")
        return line

    def _send_command(self, command: str) -> str:
        """Send ASCII command + CR and return response line."""
        with self._lock:
            self._write(command.encode("ascii") + b"\r")
            response = self._readline().decode("ascii").strip()
        return response

    def _reconnect_worker(self) -> None:
        while self._running:
            if not self._connected:
                if self._max_retries > 0 and self._retry_count >= self._max_retries:
                    logger.warning("Max retries (%d) reached for %s", self._max_retries, self.device_id)
                    break
                if self.connect():
                    pass  # _after_connect is called within connect()
                else:
                    self._retry_count += 1
            time.sleep(self._reconnect_interval)

    def _after_connect(self) -> None:
        """Called after a successful connection or reconnection.

        Override in subclasses to perform device-specific initialization
        (e.g., reading sensor IDs, configuring parameters).
        """
        pass

    def __del__(self) -> None:
        self.stop_reconnect_loop()
        self.disconnect()
