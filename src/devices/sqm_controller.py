"""Driver for the INFICON SQM-160 quartz crystal microbalance deposition monitor.

Implements the full serial packet protocol for monitoring rate, thickness,
and frequency on up to six sensor channels.  Read-only - no parameter
updates, shutter control, or zeroing commands.
"""

import logging
import time
from typing import Any, Dict, List, Optional

import serial

from src.devices.base_device import BaseDevice
from src.devices.sqm_protocol import (
    SQMProtocolError,
    build_command_packet,
    parse_response_packet,
)

logger = logging.getLogger(__name__)


class SQMController(BaseDevice):
    """Driver for the INFICON SQM-160 QCM deposition monitor."""

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self._address: int = config.get("address", 0)
        self._num_sensors: int = config.get("number_of_sensors", 2)
        self._num_sensors = max(1, min(self._num_sensors, 6))
        self._version: Optional[str] = None
        self._num_channels: Optional[int] = None
        self._units: Dict[str, str] = config.get("units", {})
        self._channels: List[str] = []
        for n in range(1, self._num_sensors + 1):
            self._channels.append(f"ch{n}_rate")
            self._channels.append(f"ch{n}_thickness")
            self._channels.append(f"ch{n}_frequency")

    @property
    def device_id(self) -> str:
        return f"SQM-{self._address}"

    @property
    def plot_channels(self) -> List[str]:
        return list(self._channels)

    @property
    def status_channels(self) -> List[str]:
        return list(self._channels)

    @property
    def address(self) -> int:
        return self._address

    @property
    def sensor_name(self) -> str:
        return "SQM-160 QCM"

    @property
    def firmware_version(self) -> Optional[str]:
        return self._version

    @property
    def sensor_count(self) -> Optional[int]:
        return self._num_channels

    def _sqm_send(self, command: str, timeout: float = 2.0) -> str:
        if not self._serial or not self._serial.is_open:
            raise ConnectionError(f"Serial port {self._port} is not open")
        packet = build_command_packet(command)
        logger.debug("(SQM) TX: %s", packet.hex())
        with self._lock:
            self._write(packet)
            buf = bytearray()
            sync = self._serial.read(1)
            if not sync:
                raise TimeoutError(f"SQM-160 {self.device_id}: no response")
            if sync[0] != 0x21:
                deadline = time.monotonic() + timeout
                while sync[0] != 0x21:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"SQM-160 {self.device_id}: sync not found")
                    sync = self._serial.read(1)
                    if not sync:
                        raise TimeoutError(f"SQM-160 {self.device_id}: timeout waiting for sync")
            buf.append(sync[0])
            len_byte = self._serial.read(1)
            if not len_byte:
                raise TimeoutError(f"SQM-160 {self.device_id}: timeout reading Length")
            buf.append(len_byte[0])
            payload_len = len_byte[0] - 35
            if payload_len < 1:
                raise SQMProtocolError(f"SQM-160 {self.device_id}: invalid Length {len_byte[0]}")
            remaining = payload_len + 2
            chunk = bytearray()
            deadline = time.monotonic() + timeout
            while len(chunk) < remaining:
                more = self._serial.read(remaining - len(chunk))
                if not more:
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            f"SQM-160 {self.device_id}: short read "
                            f"({len(chunk)}/{remaining})"
                        )
                    continue
                chunk.extend(more)
            buf.extend(chunk)
        frame = bytes(buf)
        logger.debug("(SQM) RX: %s", frame.hex())
        return parse_response_packet(frame)

    def _after_connect(self) -> None:
        # Firmware query is the connection verification step — failure means
        # the device is not actually responding, even though the serial port
        # opened.  Let the exception propagate so connect() marks as
        # disconnected.
        resp = self._sqm_send("@")
        self._version = resp[1:] if resp.startswith("A") else resp
        logger.info("%s: firmware = %s", self.device_id, self._version)

        # Channel count is secondary — graceful failure ok.
        try:
            resp = self._sqm_send("J")
            if resp.startswith("A") and resp[1:].isdigit():
                self._num_channels = int(resp[1:])
                logger.info("%s: %d sensor channels installed", self.device_id, self._num_channels)
            else:
                self._num_channels = None
        except (SQMProtocolError, TimeoutError, ConnectionError, OSError, serial.SerialException):
            logger.warning("%s: could not read channel count", self.device_id)
            self._num_channels = None

    def poll(self) -> Dict[str, Any]:
        resp = self._sqm_send("W")
        if resp.startswith("A"):
            resp = resp[1:]
        parts = resp.split("_")
        if parts and parts[0] == "00.00":
            parts = parts[1:]
        data: Dict[str, Any] = {}
        for n in range(1, self._num_sensors + 1):
            idx = (n - 1) * 3
            if idx >= len(parts):
                break
            try:
                rate = float(parts[idx])
            except (ValueError, IndexError):
                rate = float("nan")
            try:
                thickness = float(parts[idx + 1])
            except (ValueError, IndexError):
                thickness = float("nan")
            try:
                frequency = float(parts[idx + 2])
            except (ValueError, IndexError):
                frequency = float("nan")
            data[f"ch{n}_rate"] = rate
            data[f"ch{n}_thickness"] = thickness
            data[f"ch{n}_frequency"] = frequency
            # Per-field units
            data[f"ch{n}_rate_unit"] = self._units.get("rate", "")
            data[f"ch{n}_thickness_unit"] = self._units.get("thickness", "")
            data[f"ch{n}_frequency_unit"] = self._units.get("frequency", "")
        return data
