from src.devices.vcu_controller import (
    SENSOR_NAMES,
    STATUS_TEXTS,
    VCUController,
    VCUProtocolError,
)
import serial
import threading
import time
import unittest
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.acquisition.engine import AcquisitionEngine
from src.devices.base_device import BaseDevice

VCU_PORT = "COM6"
VCU_BAUDRATE = 19200
VCU_ADDRESS = 0


def create_device() -> VCUController:
    """Create a VCUController configured for the hardware test setup."""
    return VCUController({
        "port": VCU_PORT,
        "baudrate": VCU_BAUDRATE,
        "address": VCU_ADDRESS,
        "timeout": 1.0,
        "number of pressure sensors": 3,
    })

def print_measurement(device_id, timestamp, data):
    print("Device_id:", device_id)
    print("Timestamp:", timestamp)
    print("Measurement:", data)

vcu = create_device()
engine = AcquisitionEngine()
engine.add_device(vcu)
engine.subscribe(print_measurement)
engine.start()

time.sleep(10)
engine.stop()
engine.remove_device(vcu.device_id)

