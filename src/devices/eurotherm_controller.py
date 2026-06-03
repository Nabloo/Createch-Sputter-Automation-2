"""Driver for the Eurotherm 3504 temperature controller (read-only).

Implements Modbus TCP protocol for reading process temperature via
holding register 1.  This is the first Ethernet-based device driver �
BaseDevice's serial infrastructure is overridden at connect/disconnect.

Protocol summary (from manual + manual_test.py):
  - Modbus TCP, port 502
  - Read Holding Registers (FC 03)
  - Address 1, 1 register, slave/unit ID 255
  - 16-bit signed integer, scaled x0.1 (div 10 = deg C)
"""

import logging
from typing import Any, Dict, Optional

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from src.devices.base_device import BaseDevice

logger = logging.getLogger(__name__)


class EurothermController(BaseDevice):
    """Driver for the Eurotherm 3504 temperature controller.

    Configuration keys (besides BaseDevice serial params):
        address (int): Optional device ID for display. Default 0.
        host (str): IP address or hostname. Default "192.168.117.30".
        modbus_port (int): TCP port. Default 502.
        slave_id (int): Modbus slave/unit ID. Default 255.
        number_of_sensors (int): Number of temperature channels. Default 1.
        unit (str): Temperature unit for display. Default "deg C".
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        # Inject a synthetic port string before super().__init__() so
        # BaseDevice's serial-param extraction doesn't crash.
        # Work on a copy to avoid mutating the caller's config dict.
        host = config.get("host", "192.168.117.30")
        modbus_port = config.get("modbus_port", 502)
        own_config = dict(config)
        own_config["port"] = f"{host}:{modbus_port}"

        super().__init__(own_config)

        self._address: int = config.get("address", 0)
        self._host: str = host
        self._modbus_port: int = modbus_port
        self._slave_id: int = config.get("slave_id", 255)
        self._number_of_sensors: int = config.get("number_of_sensors", 1)
        self._unit: str = config.get("unit", "deg C")
        self._client: Optional[ModbusTcpClient] = None

    # ------------------------------------------------------------------
    # BaseDevice interface
    # ------------------------------------------------------------------

    @property
    def device_id(self) -> str:
        return f"Eurotherm-{self._address}"

    @property
    def plot_channels(self) -> list[str]:
        return [f"ch{i}_temperature" for i in range(1, self._number_of_sensors + 1)]

    @property
    def status_channels(self) -> list[str]:
        return [f"ch{i}_temperature" for i in range(1, self._number_of_sensors + 1)]

    @property
    def channel_units(self) -> Dict[str, str]:
        return {
            f"ch{i}_temperature": self._unit
            for i in range(1, self._number_of_sensors + 1)
        }

    # ------------------------------------------------------------------
    # Connection management (Modbus TCP overrides)
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Open Modbus TCP connection. Returns True on success."""
        with self._lock:
            if self._connected:
                return True
            try:
                logger.info(
                    "Connecting to %s on %s:%d",
                    self.device_id, self._host, self._modbus_port,
                )
                self._client = ModbusTcpClient(
                    self._host, port=self._modbus_port,
                    timeout=self._timeout,
                )
                if not self._client.connect():
                    logger.error(
                        "Modbus TCP connection failed to %s:%d",
                        self._host, self._modbus_port,
                    )
                    self._client = None
                    return False

                self._connected = True
                self._retry_count = 0
                logger.info(
                    "Connected to %s on %s:%d",
                    self.device_id, self._host, self._modbus_port,
                )
                self._after_connect()
                return True
            except Exception:
                logger.error(
                    "Connection verification failed for %s on %s:%d "
                    "— device not responding",
                    self.device_id, self._host, self._modbus_port,
                )
                self.disconnect()
                return False

    def disconnect(self) -> None:
        """Close Modbus TCP connection."""
        with self._lock:
            self._connected = False
            if self._client is not None:
                try:
                    self._client.close()
                except Exception as e:
                    logger.warning(
                        "Error closing %s: %s", self.device_id, e,
                    )
            self._client = None
            logger.info("Disconnected from %s", self.device_id)

    # ------------------------------------------------------------------
    # Data acquisition
    # ------------------------------------------------------------------

    def poll(self) -> Dict[str, Any]:
        """Poll the Eurotherm via Modbus TCP and return temperature.

        Returns:
            Dict mapping channel names to temperature values in deg C.
            Returns float("nan") on communication errors so the plot
            shows a gap.

        Raises:
            ConnectionError: if device is not connected.
        """
        # Grab a local reference so disconnect() from another thread
        # cannot replace self._client with None between the guard and
        # the Modbus call.
        client = self._client
        if client is None or not self._connected:
            raise ConnectionError(f"{self.device_id} is not connected")

        result: Dict[str, Any] = {}
        try:
            response = client.read_holding_registers(
                address=1, count=1, slave=self._slave_id,
            )
            if response.isError():
                logger.warning(
                    "%s: Modbus read error: %s", self.device_id, response,
                )
                for i in range(1, self._number_of_sensors + 1):
                    result[f"ch{i}_temperature"] = float("nan")
                return result

            raw = response.registers[0]
            # Signed 16-bit two's complement conversion
            if raw > 32767:
                raw -= 65536
            temperature = raw / 10.0

            for i in range(1, self._number_of_sensors + 1):
                result[f"ch{i}_temperature"] = temperature
            return result

        except ModbusException as e:
            logger.error(
                "%s: Modbus exception during poll: %s", self.device_id, e,
            )
            for i in range(1, self._number_of_sensors + 1):
                result[f"ch{i}_temperature"] = float("nan")
            return result

    # ------------------------------------------------------------------
    # Post-connection verification
    # ------------------------------------------------------------------

    def _after_connect(self) -> None:
        """Verify communication by reading temperature once.

        Called automatically after a successful (re)connection.
        Raises ConnectionError if the device does not respond.
        """
        try:
            response = self._client.read_holding_registers(
                address=1, count=1, slave=self._slave_id,
            )
            if response.isError():
                raise ConnectionError(
                    f"Modbus read error: {response}"
                )
            raw = response.registers[0]
            if raw > 32767:
                raw -= 65536
            temp = raw / 10.0
            logger.info(
                "%s: verified — temperature = %.1f deg C",
                self.device_id, temp,
            )
        except ModbusException as e:
            logger.error(
                "%s: verification failed: %s", self.device_id, e,
            )
            raise ConnectionError(f"Verification failed: {e}") from e

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def address(self) -> int:
        return self._address

    @property
    def host(self) -> str:
        return self._host

    @property
    def modbus_port(self) -> int:
        return self._modbus_port

    @property
    def slave_id(self) -> int:
        return self._slave_id

    @property
    def unit(self) -> str:
        return self._unit
