"""Tests for the JEVAmet VCU pressure controller driver."""

import unittest
from unittest.mock import patch

from src.devices.vcu_controller import VCUController, VCUProtocolError
from src.devices.vcu_controller import SENSOR_NAMES, STATUS_TEXTS


class TestVCUControllerProperties(unittest.TestCase):
    """Test basic properties and configuration."""

    def setUp(self):
        self.config = {
            "port": "COM6",
            "baudrate": 9600,
            "address": 0,
            "unit": "mbar",
            "timeout": 1.0,
        }
        self.device = VCUController(self.config)

    def test_device_id(self):
        self.assertEqual(self.device.device_id, "VCU-0")

    def test_device_id_with_address(self):
        cfg = dict(self.config, address=5)
        dev = VCUController(cfg)
        self.assertEqual(dev.device_id, "VCU-5")

    def test_channels(self):
        self.assertEqual(self.device.channels, ["pressure", "status_code", "status_text"])

    def test_address_property(self):
        self.assertEqual(self.device.address, 0)

    def test_pressure_unit_property(self):
        self.assertEqual(self.device.pressure_unit, "mbar")

    def test_sensor_name_before_connect(self):
        self.assertEqual(self.device.sensor_name, "Unknown")

    def test_firmware_before_connect(self):
        self.assertIsNone(self.device.firmware_version)


class TestVCUControllerProtocol(unittest.TestCase):
    """Test protocol command building and sending."""

    def setUp(self):
        self.config = {
            "port": "COM6",
            "baudrate": 9600,
            "address": 0,
            "unit": "mbar",
        }
        self.device = VCUController(self.config)

    def test_build_command_read(self):
        cmd = self.device._build_command("RPV")
        self.assertEqual(cmd, "0RPV")

    def test_build_command_read_with_address(self):
        dev = VCUController(dict(self.config, address=3))
        cmd = dev._build_command("RID")
        self.assertEqual(cmd, "3RID")

    def test_build_command_write(self):
        cmd = self.device._build_command("SHV", "1")
        self.assertEqual(cmd, "0SHV,1")

    def test_build_command_multiple_params(self):
        cmd = self.device._build_command("CMD", "a", "b", "c")
        self.assertEqual(cmd, "0CMD,a,b,c")


class TestVCUControllerSendAndVerify(unittest.TestCase):
    """Test _send_and_verify with mocked serial responses."""

    def setUp(self):
        self.config = {
            "port": "COM6",
            "baudrate": 9600,
            "address": 0,
            "unit": "mbar",
        }
        self.device = VCUController(self.config)

    def test_send_and_verify_ok(self):
        with patch.object(self.device, '_send_command', return_value="OK") as mock:
            result = self.device._send_and_verify("SHV", "1")
            self.assertEqual(result, "OK")
            mock.assert_called_once_with("0SHV,1")

    def test_send_and_verify_data(self):
        with patch.object(self.device, '_send_command', return_value="1.23E-3,0") as mock:
            result = self.device._send_and_verify("RPV")
            self.assertEqual(result, "1.23E-3,0")
            mock.assert_called_once_with("0RPV")

    def test_send_and_verify_error_invalid(self):
        with patch.object(self.device, '_send_command', return_value="?\tI\tInvalid command"):
            with self.assertRaises(VCUProtocolError) as ctx:
                self.device._send_and_verify("BAD")
            self.assertIn("Invalid command", str(ctx.exception))

    def test_send_and_verify_error_syntax(self):
        with patch.object(self.device, '_send_command', return_value="?\tS\tSyntax error"):
            with self.assertRaises(VCUProtocolError) as ctx:
                self.device._send_and_verify("RPV", "x")
            self.assertIn("Syntax error", str(ctx.exception))

    def test_send_and_verify_error_timeout(self):
        with patch.object(self.device, '_send_command', return_value="?\tK\tTimeout"):
            with self.assertRaises(VCUProtocolError) as ctx:
                self.device._send_and_verify("RPV")
            self.assertIn("Communication timeout", str(ctx.exception))


class TestVCUControllerReadCommands(unittest.TestCase):
    """Test read command responses."""

    def setUp(self):
        self.config = {
            "port": "COM6",
            "baudrate": 9600,
            "address": 0,
            "unit": "mbar",
        }
        self.device = VCUController(self.config)

    def test_read_pressure_normal(self):
        with patch.object(self.device, '_send_command', return_value="1.23E-3,0"):
            pressure, status = self.device.read_pressure()
            self.assertAlmostEqual(pressure, 1.23e-3)
            self.assertEqual(status, 0)

    def test_read_pressure_high_vacuum(self):
        with patch.object(self.device, '_send_command', return_value="5.0E-10,0"):
            pressure, status = self.device.read_pressure()
            self.assertAlmostEqual(pressure, 5.0e-10)
            self.assertEqual(status, 0)

    def test_read_pressure_below_range(self):
        with patch.object(self.device, '_send_command', return_value="0.0,1"):
            pressure, status = self.device.read_pressure()
            self.assertAlmostEqual(pressure, 0.0)
            self.assertEqual(status, 1)

    def test_read_pressure_above_range(self):
        with patch.object(self.device, '_send_command', return_value="9999.99,2"):
            pressure, status = self.device.read_pressure()
            self.assertAlmostEqual(pressure, 9999.99)
            self.assertEqual(status, 2)

    def test_read_pressure_sensor_off(self):
        with patch.object(self.device, '_send_command', return_value="0.0,5"):
            pressure, status = self.device.read_pressure()
            self.assertEqual(status, 5)

    def test_read_pressure_no_status(self):
        with patch.object(self.device, '_send_command', return_value="1.0E-6"):
            pressure, status = self.device.read_pressure()
            self.assertAlmostEqual(pressure, 1.0e-6)
            self.assertEqual(status, 0)

    def test_read_pressure_with_sensor_index(self):
        with patch.object(self.device, '_send_command', return_value="2.5E-4,0") as mock:
            pressure, status = self.device.read_pressure(sensor_index=2)
            self.assertAlmostEqual(pressure, 2.5e-4)
            mock.assert_called_once_with("0RPV,2")

    def test_read_sensor_id_ptr(self):
        with patch.object(self.device, '_send_command', return_value="1"):
            sid = self.device.read_sensor_id()
            self.assertEqual(sid, 1)

    def test_read_sensor_id_ba(self):
        with patch.object(self.device, '_send_command', return_value="5"):
            sid = self.device.read_sensor_id()
            self.assertEqual(sid, 5)

    def test_read_firmware(self):
        with patch.object(self.device, '_send_command', return_value="VCU-2.1"):
            fw = self.device.read_firmware()
            self.assertEqual(fw, "VCU-2.1")

    def test_read_setpoint_status_inactive(self):
        with patch.object(self.device, '_send_command', return_value="0"):
            result = self.device.read_setpoint_status()
            self.assertFalse(result["setpoint_active"])
            self.assertNotIn("setpoint_value", result)

    def test_read_setpoint_status_active_with_value(self):
        with patch.object(self.device, '_send_command', return_value="1,1.5E-3"):
            result = self.device.read_setpoint_status()
            self.assertTrue(result["setpoint_active"])
            self.assertAlmostEqual(result["setpoint_value"], 1.5e-3)



class TestVCUControllerPoll(unittest.TestCase):
    """Test the poll() method."""

    def setUp(self):
        self.config = {
            "port": "COM6",
            "baudrate": 9600,
            "address": 0,
            "unit": "mbar",
        }
        self.device = VCUController(self.config)

    def test_poll_normal(self):
        with patch.object(self.device, '_send_command', return_value="1.23E-3,0"):
            result = self.device.poll()
            self.assertAlmostEqual(result["pressure"], 1.23e-3)
            self.assertEqual(result["status_code"], 0)
            self.assertEqual(result["status_text"], "OK")
            self.assertEqual(result["unit"], "mbar")

    def test_poll_error_status(self):
        with patch.object(self.device, '_send_command', return_value="0.0,7"):
            result = self.device.poll()
            self.assertEqual(result["status_code"], 7)
            self.assertEqual(result["status_text"], "Sensor error")

    def test_poll_unknown_status(self):
        with patch.object(self.device, '_send_command', return_value="0.0,99"):
            result = self.device.poll()
            self.assertEqual(result["status_code"], 99)
            self.assertEqual(result["status_text"], "Unknown (99)")


class TestVCUControllerConnectLifecycle(unittest.TestCase):
    """Test connection lifecycle._after_connect behavior."""

    def setUp(self):
        self.config = {
            "port": "COM6",
            "baudrate": 9600,
            "address": 1,
            "unit": "mbar",
        }
        self.device = VCUController(self.config)

    def test_after_connect_with_sensor_and_firmware(self):
        with patch.object(self.device, '_send_command') as mock_send:
            mock_send.side_effect = ["1", "VCU-3.0"]
            self.device._after_connect()
            self.assertEqual(self.device._sensor_id, 1)
            self.assertEqual(self.device.sensor_name, "Ptr (Penning)")
            self.assertEqual(self.device._firmware_version, "VCU-3.0")
            self.assertEqual(self.device.firmware_version, "VCU-3.0")

    def test_after_connect_sensor_failure(self):
        with patch.object(self.device, '_send_command', side_effect=TimeoutError("Timeout")):
            self.device._after_connect()
            self.assertIsNone(self.device._sensor_id)
            self.assertEqual(self.device.sensor_name, "Unknown")
            self.assertIsNone(self.device._firmware_version)

    def test_sensor_name_after_connect(self):
        with patch.object(self.device, '_send_command', side_effect=["5", "VCU-3.0"]):
            self.device._after_connect()
            self.assertEqual(self.device.sensor_name, "BA (Bayard-Alpert)")


class TestVCUControllerConfigVariants(unittest.TestCase):
    """Test different config combinations."""

    def test_default_baudrate(self):
        dev = VCUController({"port": "COM6"})
        self.assertEqual(dev.address, 0)
        self.assertEqual(dev.pressure_unit, "mbar")

    def test_custom_unit(self):
        dev = VCUController({"port": "COM6", "unit": "Torr"})
        self.assertEqual(dev.pressure_unit, "Torr")

    def test_high_address(self):
        dev = VCUController({"port": "COM6", "address": 126})
        self.assertEqual(dev.device_id, "VCU-126")


class TestStatusTextsAndSensorNames(unittest.TestCase):
    """Verify all status codes and sensor IDs have entries."""

    def test_all_status_codes_covered(self):
        expected_codes = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15}
        self.assertEqual(set(STATUS_TEXTS.keys()), expected_codes)

    def test_all_sensor_ids_covered(self):
        expected_ids = set(range(0, 12))
        self.assertEqual(set(SENSOR_NAMES.keys()), expected_ids)

    def test_status_texts_content(self):
        self.assertEqual(STATUS_TEXTS[0], "OK")
        self.assertEqual(STATUS_TEXTS[1], "Below range")
        self.assertEqual(STATUS_TEXTS[6], "HV on")
        self.assertEqual(STATUS_TEXTS[15], "Filament defective")


if __name__ == "__main__":
    unittest.main()
