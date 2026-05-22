"""Unit tests for the SQMController driver (T2 + T3).

Uses a mock serial port to verify packet I/O, _after_connect queries,
and poll() W-command parsing without real hardware.
"""

import math
import unittest
from unittest.mock import MagicMock, patch

from src.devices.sqm_controller import SQMController
from src.devices.sqm_protocol import SQMProtocolError, compute_crc14


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _pack_response(payload: str) -> bytes:
    """Build a valid SQM-160 response frame for a given payload."""
    payload_bytes = payload.encode("ascii")
    length_byte = len(payload_bytes) + 35
    crc_data = bytes([length_byte]) + payload_bytes
    crc1, crc2 = compute_crc14(crc_data)
    return bytes([0x21, length_byte]) + payload_bytes + bytes([crc1, crc2])


def _make_config(**overrides):
    cfg = {"port": "COM99", "baudrate": 19200, "address": 0, "number_of_sensors": 2}
    cfg.update(overrides)
    return cfg


# ------------------------------------------------------------------
# Construction & Properties
# ------------------------------------------------------------------


class TestConstruction(unittest.TestCase):

    def test_device_id_default(self):
        ctrl = SQMController(_make_config())
        self.assertEqual(ctrl.device_id, "SQM-0")

    def test_device_id_address_3(self):
        ctrl = SQMController(_make_config(address=3))
        self.assertEqual(ctrl.device_id, "SQM-3")

    def test_plot_channels_default_2_sensors(self):
        ctrl = SQMController(_make_config())
        expected = [
            "ch1_rate", "ch1_thickness", "ch1_frequency",
            "ch2_rate", "ch2_thickness", "ch2_frequency",
        ]
        self.assertEqual(ctrl.plot_channels, expected)
        self.assertEqual(ctrl.status_channels, expected)

    def test_plot_channels_max_6_sensors(self):
        ctrl = SQMController(_make_config(number_of_sensors=6))
        self.assertEqual(len(ctrl.plot_channels), 18)
        self.assertIn("ch6_rate", ctrl.plot_channels)
        self.assertIn("ch6_frequency", ctrl.plot_channels)

    def test_plot_channels_clamped_to_6(self):
        ctrl = SQMController(_make_config(number_of_sensors=99))
        self.assertEqual(len(ctrl.plot_channels), 18)

    def test_plot_channels_clamped_to_min_1(self):
        ctrl = SQMController(_make_config(number_of_sensors=0))
        self.assertEqual(len(ctrl.plot_channels), 3)
        self.assertIn("ch1_rate", ctrl.plot_channels)

    def test_firmware_version_none_initially(self):
        ctrl = SQMController(_make_config())
        self.assertIsNone(ctrl.firmware_version)

    def test_sensor_count_none_initially(self):
        ctrl = SQMController(_make_config())
        self.assertIsNone(ctrl.sensor_count)

    def test_address_property(self):
        ctrl = SQMController(_make_config(address=5))
        self.assertEqual(ctrl.address, 5)

    def test_sensor_name_property(self):
        ctrl = SQMController(_make_config())
        self.assertEqual(ctrl.sensor_name, "SQM-160 QCM")


# ------------------------------------------------------------------
# Packet I/O (_sqm_send)
# ------------------------------------------------------------------


class TestSqmSend(unittest.TestCase):

    def setUp(self):
        self.ctrl = SQMController(_make_config())
        self.mock_serial = MagicMock()
        self.mock_serial.is_open = True
        self.ctrl._serial = self.mock_serial

    def test_writes_correct_packet_for_J(self):
        """Verify _sqm_send receives the correct response for J command."""
        self.mock_serial.read.side_effect = self._make_response("A6")
        result = self.ctrl._sqm_send("J")
        self.assertEqual(result, "A6")
        # Verify the correct packet was written
        self.mock_serial.write.assert_called_once()
        written = self.mock_serial.write.call_args[0][0]
        self.assertEqual(written[0], 0x21)  # Sync
        self.assertEqual(written[1], 35)     # Length = 1+34
        self.assertEqual(written[2], 0x4A)   # 'J'

    def test_raises_connection_error_when_disconnected(self):
        self.ctrl._serial = None
        with self.assertRaises(ConnectionError):
            self.ctrl._sqm_send("J")

    def test_raises_timeout_on_no_response(self):
        self.mock_serial.read.return_value = b""
        with self.assertRaises(TimeoutError):
            self.ctrl._sqm_send("J")

    def test_raises_protocol_error_on_status_C(self):
        self.mock_serial.read.side_effect = self._make_response("C")
        with self.assertRaises(SQMProtocolError):
            self.ctrl._sqm_send("J")

    def _make_response(self, payload: str):
        """Return a side_effect list for mock_serial.read.

        _sqm_send does:
            1. read(1) -> sync byte
            2. read(1) -> length byte
            3. read(remaining) -> payload + CRC (as one chunk)
        """
        frame = _pack_response(payload)
        return [
            bytes([frame[0]]),  # Sync
            bytes([frame[1]]),  # Length
            frame[2:],          # Payload + CRC
        ]


# ------------------------------------------------------------------
# _after_connect
# ------------------------------------------------------------------


class TestAfterConnect(unittest.TestCase):

    def setUp(self):
        self.ctrl = SQMController(_make_config())
        self.mock_serial = MagicMock()
        self.mock_serial.is_open = True
        self.ctrl._serial = self.mock_serial

    def test_queries_firmware_and_channels(self):
        with patch.object(self.ctrl, "_sqm_send") as mock_send:
            mock_send.side_effect = ["AMON_Ver_4.13", "A6"]
            self.ctrl._after_connect()
            self.assertEqual(self.ctrl.firmware_version, "MON_Ver_4.13")
            self.assertEqual(self.ctrl.sensor_count, 6)
            mock_send.assert_any_call("@")
            mock_send.assert_any_call("J")

    def test_handles_firmware_failure_gracefully(self):
        with patch.object(self.ctrl, "_sqm_send") as mock_send:
            mock_send.side_effect = TimeoutError("no response")
            self.ctrl._after_connect()
            self.assertIsNone(self.ctrl.firmware_version)
            self.assertIsNone(self.ctrl.sensor_count)

    def test_handles_version_without_A_prefix(self):
        with patch.object(self.ctrl, "_sqm_send") as mock_send:
            mock_send.side_effect = ["MON_Ver_5.00", "A2"]
            self.ctrl._after_connect()
            self.assertEqual(self.ctrl.firmware_version, "MON_Ver_5.00")


# ------------------------------------------------------------------
# poll() - W command parsing
# ------------------------------------------------------------------


class TestPoll(unittest.TestCase):

    def setUp(self):
        self.ctrl = SQMController(_make_config(number_of_sensors=2))

    def test_poll_parses_2_sensor_response(self):
        """Realistic W response for 2 active sensors, skip dummy."""
        payload = (
            "A00.00_"
            "7.10_3076.190_5497894.642_"
            "5.20_1500.500_5500000.123"
        )
        with patch.object(self.ctrl, "_sqm_send", return_value=payload):
            data = self.ctrl.poll()

        self.assertAlmostEqual(data["ch1_rate"], 7.10)
        self.assertAlmostEqual(data["ch1_thickness"], 3076.190)
        self.assertAlmostEqual(data["ch1_frequency"], 5497894.642)
        self.assertAlmostEqual(data["ch2_rate"], 5.20)
        self.assertAlmostEqual(data["ch2_thickness"], 1500.500)
        self.assertAlmostEqual(data["ch2_frequency"], 5500000.123)

    def test_poll_skips_dummy_first_value(self):
        """Ensure dummy 00.00 is not in any channel data."""
        payload = "A00.00_1.0_2.0_3.0_4.0_5.0_6.0"
        with patch.object(self.ctrl, "_sqm_send", return_value=payload):
            data = self.ctrl.poll()
        self.assertAlmostEqual(data["ch1_rate"], 1.0)

    def test_poll_returns_only_configured_channels(self):
        """With 2 sensors configured, only ch1 and ch2 present."""
        # W response with 3 sensors worth of data (9 values), but only 2
        # configured — ch3 data must be absent from the result.
        payload = (
            "A00.00_"
            "1.0_10.0_100.0_"
            "2.0_20.0_200.0_"
            "3.0_30.0_300.0"
        )
        with patch.object(self.ctrl, "_sqm_send", return_value=payload):
            data = self.ctrl.poll()
        self.assertEqual(len(data), 6)  # 2 sensors × 3 values
        self.assertIn("ch1_rate", data)
        self.assertIn("ch2_rate", data)
        self.assertNotIn("ch3_rate", data)

    def test_poll_handles_missing_parts_gracefully(self):
        """Empty response after stripping — all fields become NaN."""
        payload = "A"  # Empty after stripping A → one empty string in parts
        with patch.object(self.ctrl, "_sqm_send", return_value=payload):
            data = self.ctrl.poll()
        # The empty string "" fails float() → NaN for rate;
        # thickness and frequency IndexError → NaN too.
        self.assertEqual(len(data), 3)
        self.assertTrue(math.isnan(data["ch1_rate"]))
        self.assertTrue(math.isnan(data["ch1_thickness"]))
        self.assertTrue(math.isnan(data["ch1_frequency"]))

    def test_poll_short_response_nans(self):
        """Single value — second and third fields become NaN."""
        payload = "A1.0"  # After stripping A: "1.0", only one value
        with patch.object(self.ctrl, "_sqm_send", return_value=payload):
            data = self.ctrl.poll()
        self.assertAlmostEqual(data["ch1_rate"], 1.0)
        self.assertTrue(math.isnan(data["ch1_thickness"]))
        self.assertTrue(math.isnan(data["ch1_frequency"]))

