from src.devices.vcu_controller import (
    SENSOR_NAMES,
    STATUS_TEXTS,
    VCUController,
    VCUProtocolError,
)
import serial

VCU_PORT = "COM6"
VCU_BAUDRATE = 19200
VCU_ADDRESS = 0


def create_device() -> VCUController:
    """Create a VCUController configured for the hardware test setup."""
    return VCUController({
        "port": VCU_PORT,
        "baudrate": VCU_BAUDRATE,
        "address": VCU_ADDRESS,
        "unit": "mbar",
        "timeout": 1.0,
    })

vcu = create_device()
vcu.connect()
for i in range(1,4):
    print(vcu.read_pressure(i))
    print(vcu.read_sensor_id(i))

print(vcu.read_firmware())
print(vcu.read_setpoint_status())
