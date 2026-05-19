"""Driver for JEVAmet VCU vacuum pressure controller.

Implements the full serial protocol for reading pressure values,
sensor identification, and controlling HV/Degas functions.

Protocol summary (from manual):
  - RS232 or RS485, 8N1, 9600/19200/38400 baud
  - ASCII strings, comma delimited, CR terminated (0x0D)
  - Read:  [Address] Command <CR>  ->  Response <CR>
  - Write: [Address] Command , [Parameter] <CR>  ->  OK <CR>
  - Error: ? <TAB> X <TAB> [...]  (X = I/P/C/S/K)
"""

import logging
from typing import Any, Dict, Optional, Tuple

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
        address (int): RS485 address (0-126). Default 0 (display).
        unit (str): Pressure unit string for display. Default "mbar".
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self._address: int = config.get("address", 0)
        self._pressure_unit: str = config.get("unit", "mbar")
        self._sensor_id: Optional[int] = None
        self._firmware_version: Optional[str] = None

    @property
    def device_id(self) -> str:
        return f"VCU-{self._address}"

    @property
    def channels(self) -> list[str]:
        return ["pressure", "status_code", "status_text"]

    def poll(self) -> Dict[str, Any]:
        pressure, status_code = self.read_pressure()
        return {
            "pressure": pressure,
            "status_code": status_code,
            "status_text": STATUS_TEXTS.get(
                status_code, f"Unknown ({status_code})"
            ),
            "unit": self._pressure_unit,
        }

    def _after_connect(self) -> None:
        try:
            self._sensor_id = self.read_sensor_id()
            logger.info(
                "%s: sensor ID = %d (%s)", self.device_id,
                self._sensor_id,
                SENSOR_NAMES.get(self._sensor_id, "Unknown"),
            )
        except Exception:
            logger.warning("%s: could not read sensor ID", self.device_id)
            self._sensor_id = None
        try:
            self._firmware_version = self.read_firmware()
            logger.info("%s: firmware = %s", self.device_id, self._firmware_version)
        except Exception:
            logger.warning("%s: could not read firmware version", self.device_id)
            self._firmware_version = None

    def _build_command(self, command: str, *params: str) -> str:
        cmd = f"{self._address}{command}"
        if params:
            cmd += "," + ",".join(params)
        return cmd

    def _send_and_verify(self, command: str, *params: str) -> str:
        cmd = self._build_command(command, *params)
        response = self._send_command(cmd)
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

    def read_pressure(self, sensor_index: int = 0) -> Tuple[float, int]:
        if sensor_index > 0:
            resp = self._send_and_verify("RPV", str(sensor_index))
        else:
            resp = self._send_and_verify("RPV")
        parts = resp.split(",")
        pressure = float(parts[0])
        status_code = int(parts[1]) if len(parts) > 1 else 0
        return pressure, status_code

    def read_sensor_id(self) -> int:
        resp = self._send_and_verify("RID")
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

    def hv_on(self) -> bool:
        resp = self._send_and_verify("SHV", "1")
        return resp == "OK"

    def hv_off(self) -> bool:
        resp = self._send_and_verify("SHV", "0")
        return resp == "OK"

    def degas_on(self) -> bool:
        resp = self._send_and_verify("SDG", "1")
        return resp == "OK"

    def degas_off(self) -> bool:
        resp = self._send_and_verify("SDG", "0")
        return resp == "OK"

    def save_config(self) -> bool:
        resp = self._send_and_verify("SAC")
        return resp == "OK"

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
