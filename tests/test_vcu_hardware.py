"""Hardware integration tests for JEVAmet VCU on COM6 (read-only).

Connects to a real VCU device at COM6 (19200 baud) and exercises all
read protocol commands. Each test validates that the device returns a
valid response without protocol errors.

Usage:
    python -m unittest tests.test_vcu_hardware -v
"""

import unittest
from src.devices.vcu_controller import (
    SENSOR_NAMES,
    STATUS_TEXTS,
    VCUController,
    VCUProtocolError,
)


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
        "timeout": 2.0,
    })


class TestVCUHardwareReadOnly(unittest.TestCase):
    """Read-only hardware tests — safe to run anytime."""

    @classmethod
    def setUpClass(cls):
        cls.device = create_device()
        cls.device.connect()

    @classmethod
    def tearDownClass(cls):
        cls.device.disconnect()

    def test_connect_and_properties(self):
        """After connect, properties should be populated by _after_connect."""
        self.assertTrue(self.device.connected)
        self.assertEqual(self.device.device_id, "VCU-%d" % VCU_ADDRESS)
        self.assertEqual(self.device.address, VCU_ADDRESS)
        self.assertEqual(self.device.pressure_unit, "mbar")

    def test_sensor_id_valid(self):
        """Primary sensor should have a real sensor connected (ID != 0)."""
        sid = self.device.sensor_id
        self.assertIsNotNone(sid, "Sensor ID was not read during connect")
        self.assertNotEqual(sid, 0, "Sensor ID is 0 (No sensor) — expected a real sensor")

    def test_firmware_version_readable(self):
        """Firmware version string should be non-empty."""
        fw = self.device.firmware_version
        self.assertIsNotNone(fw)
        self.assertGreater(len(fw.strip()), 0)

    def test_poll_returns_valid_dict(self):
        """poll() should return all expected keys with correct types."""
        result = self.device.poll()
        self.assertIn("pressure", result)
        self.assertIn("status_code", result)
        self.assertIn("status_text", result)
        self.assertIn("unit", result)
        self.assertIsInstance(result["pressure"], float)
        self.assertIsInstance(result["status_code"], int)
        self.assertEqual(result["unit"], "mbar")
        self.assertIn(result["status_code"], STATUS_TEXTS)

    def test_all_three_sensors_read_pressure(self):
        """All 3 connected sensors should return valid pressure with a known status."""
        for idx in range(3):
            with self.subTest(sensor_index=idx):
                pressure, status_code = self.device.read_pressure(sensor_index=idx)
                self.assertIsInstance(pressure, float,
                    "Sensor %d: pressure is not a float" % idx)
                self.assertIn(
                    status_code, STATUS_TEXTS,
                    "Sensor %d: unknown status code %d" % (idx, status_code)
                )

    def test_read_sensor_id_standalone(self):
        """RID command should return a non-zero sensor ID (real sensor connected)."""
        sid = self.device.read_sensor_id()
        self.assertIsInstance(sid, int)
        self.assertNotEqual(sid, 0, "Sensor ID is 0 (No sensor) — expected a real sensor")

    def test_all_three_sensors_read_sensor_id(self):
        """All 3 connected sensors should return a valid sensor ID."""
        for idx in range(3):
            with self.subTest(sensor_index=idx):
                sid = self.device.read_sensor_id(sensor_index=idx)
                self.assertIsInstance(sid, int,
                    "Sensor %d: sensor ID is not an int" % idx)
                self.assertIn(sid, SENSOR_NAMES,
                    "Sensor %d: unknown sensor ID %d" % (idx, sid))

    def test_read_firmware_standalone(self):
        """RVN command should return a non-empty version string."""
        fw = self.device.read_firmware()
        self.assertIsInstance(fw, str)
        self.assertGreater(len(fw.strip()), 0)

    def test_read_setpoint_status(self):
        """RSS command should return a dict with setpoint_active."""
        result = self.device.read_setpoint_status()
        self.assertIn("setpoint_active", result)
        self.assertIsInstance(result["setpoint_active"], bool)

    def test_channels_property(self):
        """channels should list the expected measurement channels."""
        ch = self.device.channels
        self.assertIn("pressure", ch)
        self.assertIn("status_code", ch)
        self.assertIn("status_text", ch)


class TestVCUHardwareErrorHandling(unittest.TestCase):
    """Test error handling with the real device."""

    @classmethod
    def setUpClass(cls):
        cls.device = create_device()
        cls.device.connect()

    @classmethod
    def tearDownClass(cls):
        cls.device.disconnect()

    def test_invalid_command_raises_error(self):
        """An unknown command should raise VCUProtocolError."""
        with self.assertRaises(VCUProtocolError):
            self.device._send_and_verify("XYZ")

    def test_invalid_parameter_raises_error(self):
        """An out-of-range parameter value should raise VCUProtocolError."""
        with self.assertRaises(VCUProtocolError):
            self.device._send_and_verify("RPV", "99")


if __name__ == "__main__":
    unittest.main()
