"""Driver for JEVAmet VCU vacuum pressure controller (read-only).

Implements the full serial protocol for reading pressure values,
sensor identification, and setpoint status.

Protocol summary (from manual):
  - RS232 or RS485, 8N1, 9600/19200/38400 baud
  - adress is only needed for RS485
  - ASCII strings, comma delimited, CR terminated (0x0D)
  - Read:  [Address] Command <CR>  ->  Response <CR>
  - Error: ? <TAB> X <TAB> [...]  (X = I/P/C/S/K)
"""

import logging
from typing import Any, Dict, Optional, Tuple

import serial

from src.devices.base_device import BaseDevice

logger = logging.getLogger(__name__)


SENSOR_NAMES: Dict[int, str] = {
    0: "No sensor",
    1: "Ptr (Penning)",
    2: "TTR1 (Pirani 1)",
    3: "TTR (Pirani)",
    4: "CTR (Capacitance)",
    5: "BA (Bayard-Alpert)",
    6: "BEE (Beam)",
    7: "AT (Atm probe)",
    8: "Ptr90 (Penning 90\u00b0)",
    9: "DU200",
    10: "DU2000",
    11: "DUREL",
}


STATUS_TEXTS: Dict[int, str] = {
    0: "OK",
    1: "Below range",
    2: "Above range",
    3: "Error Lo",
    4: "Error Hi",
    5: "Sensor off",
    6: "HV on",
    7: "Sensor error",
    8: "BA error",
    9: "No sensor",
    10: "No trigger",
    11: "Pressure error",
    12: "Pirani error",
    13: "24V error",
    15: "Filament defective",
}


ERROR_CODE_MAP: Dict[str, str] = {
    "I": "Invalid command",
    "P": "Invalid parameter",
    "C": "Checksum error",
    "S": "Syntax error",
    "K": "Communication timeout",
}


class VCUProtocolError(RuntimeError):
    """Raised when the VCU returns an error response."""
    pass


class VCUController(BaseDevice):
    """Driver for the JEVAmet VCU vacuum pressure controller.

    Configuration keys (besides BaseDevice serial params):
        address (int): Optional device ID for display purposes, not sent
                       in commands (RS232 mode). Default 0.
        unit (str): Pressure unit string for display. Default "mbar".
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self._address: int = config.get("address", 0)
        self._sensor_id: Optional[int] = None
        self._firmware_version: Optional[str] = None
        self._number_of_pressure_sensors: int = config.get("number of pressure sensors", 1)
        self._pressure_unit: str = "mbar"

    @property
    def device_id(self) -> str:
        return f"VCU-{self._address}"

    @property
    def channels(self) -> list[str]:
        return ["pressure", "status_code", "status_text"]

    def poll(self) -> Dict[Dict[str, Any]]:
        """
        returns dict with the channel number as key. For each sensor there is a dict with the pressure, status_code, status text, and unit.
        """
        data = {}
        for channel in range(1, self._number_of_pressure_sensors + 1):
            pressure, status_code = self.read_pressure(channel)
            channel_data = {
                "pressure": pressure,
                "status_code": status_code,
                "status_text": STATUS_TEXTS.get(
                    status_code, f"Unknown ({status_code})"
                ),
                "unit": self._pressure_unit,
            }
            data[f"channel_{channel}"] = channel_data
        return data

    def _after_connect(self) -> None:
        try:
            self._sensor_id = self.read_sensor_id()
            self._pressure_unit = self.read_pressure_unit()
            logger.info(
                "%s: sensor ID = %d (%s)", self.device_id,
                self._sensor_id,
                SENSOR_NAMES.get(self._sensor_id, "Unknown"),
            )
        except (VCUProtocolError, TimeoutError, ConnectionError, OSError, serial.SerialException):
            logger.warning("%s: could not read sensor ID", self.device_id)
            self._sensor_id = None
        try:
            self._firmware_version = self.read_firmware()
            logger.info("%s: firmware = %s", self.device_id, self._firmware_version)
        except (VCUProtocolError, TimeoutError, ConnectionError, OSError, serial.SerialException):
            logger.warning("%s: could not read firmware version", self.device_id)
            self._firmware_version = None

    def _build_command(self, command: str, *params: str) -> str:
        """Build a command string. Address is omitted (RS232).

        For write commands, params are comma-separated (e.g. 'SHV,1').
        For read commands with channel (RPV, RID), pass the
        space-formatted command directly with no params.
        """
        cmd = command
        if params:
            cmd += "," + ",".join(params)
        return cmd

    def _send_and_verify(self, command: str, *params: str) -> str:
        cmd = self._build_command(command, *params)
        logger.debug("(VCU) Sending command: %s", cmd)
        response = self._send_command(cmd)
        logger.debug("(VCU) Received response: %s", response)
        if response.startswith("?\t"):
            self._raise_protocol_error(response)
        return response

    def _raise_protocol_error(self, response: str) -> None:
        parts = response.split("\t")
        if len(parts) >= 2:
            error_code = parts[1].strip()
            msg = ERROR_CODE_MAP.get(error_code, f"Unknown error ({error_code})")
            raise VCUProtocolError(f"{self.device_id}: {msg}")
        raise VCUProtocolError(f"{self.device_id}: {response.strip()}")

    def read_pressure(self, channel: int) -> Tuple[float, int]:
        resp = self._send_and_verify(f"RPV {channel}")
        parts = resp.strip().split(",")
        pressure = float(parts[1])
        status_code = int(parts[0])
        return pressure, status_code

    def read_sensor_id(self, channel: int = 1) -> int:
        resp = self._send_and_verify(f"RID {channel}")
        return int(resp.strip())

    def read_firmware(self) -> str:
        return self._send_and_verify("RVN")

    def read_setpoint_status(self) -> Dict[str, Any]:
        resp = self._send_and_verify("RSS")
        parts = resp.split(",")
        result: Dict[str, Any] = {
            "setpoint_active": bool(int(parts[0])) if parts[0].isdigit() else False,
        }
        if len(parts) > 1:
            try:
                result["setpoint_value"] = float(parts[1])
            except ValueError:
                pass
        return result

    def read_pressure_unit(self) -> str:
        """
        0 → mbar, 1 → Torr, 2 → Pa
        """
        resp = self._send_and_verify('RGP').split(',')[0]
        return {'0': 'mbar', '1': 'Torr', '2': 'Pa'}.get(resp, 'unknown')

    @property
    def sensor_id(self) -> Optional[int]:
        """Cached sensor ID read during (re)connect, or None if unavailable."""
        return self._sensor_id

    @property
    def sensor_name(self) -> str:
        if self._sensor_id is not None:
            return SENSOR_NAMES.get(self._sensor_id, f"Unknown ({self._sensor_id})")
        return "Unknown"

    @property
    def firmware_version(self) -> Optional[str]:
        return self._firmware_version

    @property
    def address(self) -> int:
        return self._address

    @property
    def pressure_unit(self) -> str:
        return self._pressure_unit
