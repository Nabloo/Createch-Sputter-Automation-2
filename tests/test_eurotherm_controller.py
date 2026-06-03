"""Unit tests for the Eurotherm 3504 temperature controller driver.

Uses mocked ModbusTcpClient to verify connection lifecycle, poll()
register parsing, signed 16-bit conversion, and error handling
without real hardware.
"""

import math
import unittest
from unittest.mock import MagicMock, patch

from pymodbus.exceptions import ModbusException

from src.devices.eurotherm_controller import EurothermController


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_config(**overrides):
    """Build a minimal config dict with sensible defaults."""
    cfg = {
        "host": "192.168.117.30",
        "modbus_port": 502,
        "address": 0,
        "slave_id": 255,
        "number_of_sensors": 1,
        "unit": "deg C",
        "timeout": 1.0,
        "reconnect_interval": 3.0,
    }
    cfg.update(overrides)
    return cfg


def _mock_register_response(registers):
    """Build a MagicMock that mimics a pymodbus register response."""
    resp = MagicMock()
    resp.isError.return_value = False
    resp.registers = list(registers)
    return resp


def _mock_error_response():
    """Build a MagicMock that mimics a pymodbus error response."""
    resp = MagicMock()
    resp.isError.return_value = True
    resp.registers = []
    return resp


# ------------------------------------------------------------------
# Construction & Properties
# ------------------------------------------------------------------


class TestProperties(unittest.TestCase):

    def test_device_id_default(self):
        ctrl = EurothermController(_make_config())
        self.assertEqual(ctrl.device_id, "Eurotherm-0")

    def test_device_id_with_address(self):
        ctrl = EurothermController(_make_config(address=3))
        self.assertEqual(ctrl.device_id, "Eurotherm-3")

    def test_plot_channels_default(self):
        ctrl = EurothermController(_make_config())
        self.assertEqual(ctrl.plot_channels, ["ch1_temperature"])

    def test_status_channels_default(self):
        ctrl = EurothermController(_make_config())
        self.assertEqual(ctrl.status_channels, ["ch1_temperature"])

    def test_channel_units_default(self):
        ctrl = EurothermController(_make_config())
        self.assertEqual(ctrl.channel_units, {"ch1_temperature": "deg C"})

    def test_multi_sensor_channels(self):
        ctrl = EurothermController(_make_config(number_of_sensors=3))
        expected = ["ch1_temperature", "ch2_temperature", "ch3_temperature"]
        self.assertEqual(ctrl.plot_channels, expected)
        self.assertEqual(ctrl.status_channels, expected)

    def test_multi_sensor_units(self):
        ctrl = EurothermController(_make_config(number_of_sensors=2))
        self.assertEqual(
            ctrl.channel_units,
            {"ch1_temperature": "deg C", "ch2_temperature": "deg C"},
        )

    def test_connected_initially_false(self):
        ctrl = EurothermController(_make_config())
        self.assertFalse(ctrl.connected)

    def test_address_property(self):
        ctrl = EurothermController(_make_config(address=5))
        self.assertEqual(ctrl.address, 5)

    def test_host_property(self):
        ctrl = EurothermController(_make_config(host="10.0.0.1"))
        self.assertEqual(ctrl.host, "10.0.0.1")

    def test_modbus_port_property(self):
        ctrl = EurothermController(_make_config(modbus_port=1502))
        self.assertEqual(ctrl.modbus_port, 1502)

    def test_slave_id_property(self):
        ctrl = EurothermController(_make_config(slave_id=1))
        self.assertEqual(ctrl.slave_id, 1)

    def test_unit_property(self):
        ctrl = EurothermController(_make_config(unit="K"))
        self.assertEqual(ctrl.unit, "K")

    def test_custom_unit_in_channels(self):
        ctrl = EurothermController(_make_config(unit="K"))
        self.assertEqual(ctrl.channel_units, {"ch1_temperature": "K"})

    def test_port_property_is_synthetic(self):
        """BaseDevice.port returns the synthetic host:port string."""
        ctrl = EurothermController(_make_config(host="10.0.0.5", modbus_port=502))
        self.assertEqual(ctrl.port, "10.0.0.5:502")


# ------------------------------------------------------------------
# poll() — register value parsing
# ------------------------------------------------------------------


class TestPollRegisterValues(unittest.TestCase):

    def setUp(self):
        self.ctrl = EurothermController(_make_config())
        # Simulate a connected state
        self.ctrl._client = MagicMock()
        self.ctrl._connected = True

    def test_positive_temperature(self):
        """Register value 425 → 42.5 deg C."""
        self.ctrl._client.read_holding_registers.return_value = (
            _mock_register_response([425])
        )
        result = self.ctrl.poll()
        self.assertAlmostEqual(result["ch1_temperature"], 42.5)

    def test_zero_temperature(self):
        """Register value 0 → 0.0 deg C."""
        self.ctrl._client.read_holding_registers.return_value = (
            _mock_register_response([0])
        )
        result = self.ctrl.poll()
        self.assertAlmostEqual(result["ch1_temperature"], 0.0)

    def test_negative_temperature(self):
        """Register value 65486 (≡ -50 signed) → -5.0 deg C."""
        self.ctrl._client.read_holding_registers.return_value = (
            _mock_register_response([65486])
        )
        result = self.ctrl.poll()
        self.assertAlmostEqual(result["ch1_temperature"], -5.0)

    def test_max_positive(self):
        """Register value 32767 → 3276.7 deg C."""
        self.ctrl._client.read_holding_registers.return_value = (
            _mock_register_response([32767])
        )
        result = self.ctrl.poll()
        self.assertAlmostEqual(result["ch1_temperature"], 3276.7)

    def test_max_negative(self):
        """Register value 32768 (≡ -32768 signed) → -3276.8 deg C."""
        self.ctrl._client.read_holding_registers.return_value = (
            _mock_register_response([32768])
        )
        result = self.ctrl.poll()
        self.assertAlmostEqual(result["ch1_temperature"], -3276.8)

    def test_multi_sensor_returns_same_value(self):
        """With 3 sensors, all channels report the same temperature."""
        ctrl = EurothermController(_make_config(number_of_sensors=3))
        ctrl._client = MagicMock()
        ctrl._connected = True
        ctrl._client.read_holding_registers.return_value = (
            _mock_register_response([425])
        )
        result = ctrl.poll()
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result["ch1_temperature"], 42.5)
        self.assertAlmostEqual(result["ch2_temperature"], 42.5)
        self.assertAlmostEqual(result["ch3_temperature"], 42.5)

    def test_poll_calls_read_holding_registers_correctly(self):
        """Verify the correct Modbus call parameters."""
        ctrl = EurothermController(_make_config(slave_id=255))
        ctrl._client = MagicMock()
        ctrl._connected = True
        ctrl._client.read_holding_registers.return_value = (
            _mock_register_response([100])
        )
        ctrl.poll()
        ctrl._client.read_holding_registers.assert_called_once_with(
            address=1, count=1, slave=255,
        )


# ------------------------------------------------------------------
# poll() — error handling
# ------------------------------------------------------------------


class TestPollErrors(unittest.TestCase):

    def setUp(self):
        self.ctrl = EurothermController(_make_config())

    def test_raises_connection_error_when_disconnected(self):
        """poll() raises ConnectionError if client is None."""
        self.ctrl._client = None
        self.ctrl._connected = False
        with self.assertRaises(ConnectionError):
            self.ctrl.poll()

    def test_raises_connection_error_when_connected_flag_false(self):
        """poll() raises ConnectionError even if client exists but flagged disconnected."""
        self.ctrl._client = MagicMock()
        self.ctrl._connected = False
        with self.assertRaises(ConnectionError):
            self.ctrl.poll()

    def test_returns_nan_on_modbus_exception(self):
        """ModbusException during read → float('nan')."""
        self.ctrl._client = MagicMock()
        self.ctrl._connected = True
        self.ctrl._client.read_holding_registers.side_effect = (
            ModbusException("connection lost")
        )
        result = self.ctrl.poll()
        self.assertTrue(math.isnan(result["ch1_temperature"]))

    def test_returns_nan_on_modbus_error_response(self):
        """Modbus error response (isError=True) → float('nan')."""
        self.ctrl._client = MagicMock()
        self.ctrl._connected = True
        self.ctrl._client.read_holding_registers.return_value = (
            _mock_error_response()
        )
        result = self.ctrl.poll()
        self.assertTrue(math.isnan(result["ch1_temperature"]))

    def test_multi_sensor_nan_on_error(self):
        """With 3 sensors, all get NaN on error."""
        ctrl = EurothermController(_make_config(number_of_sensors=3))
        ctrl._client = MagicMock()
        ctrl._connected = True
        ctrl._client.read_holding_registers.side_effect = (
            ModbusException("timeout")
        )
        result = ctrl.poll()
        self.assertEqual(len(result), 3)
        for ch in ["ch1_temperature", "ch2_temperature", "ch3_temperature"]:
            self.assertTrue(math.isnan(result[ch]))


# ------------------------------------------------------------------
# connect() lifecycle
# ------------------------------------------------------------------


class TestConnectLifecycle(unittest.TestCase):

    def setUp(self):
        self.ctrl = EurothermController(_make_config())

    def test_connect_creates_client_and_connects(self):
        """connect() creates ModbusTcpClient and calls connect()."""
        with patch(
            "src.devices.eurotherm_controller.ModbusTcpClient"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.connect.return_value = True
            mock_client_cls.return_value = mock_client

            # Also mock _after_connect to avoid real Modbus call
            with patch.object(self.ctrl, "_after_connect"):
                result = self.ctrl.connect()

            self.assertTrue(result)
            self.assertTrue(self.ctrl.connected)
            mock_client_cls.assert_called_once_with(
                "192.168.117.30", port=502, timeout=1.0,
            )
            mock_client.connect.assert_called_once()

    def test_connect_failure_returns_false(self):
        """When client.connect() returns False, connect() returns False."""
        with patch(
            "src.devices.eurotherm_controller.ModbusTcpClient"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.connect.return_value = False
            mock_client_cls.return_value = mock_client

            result = self.ctrl.connect()

            self.assertFalse(result)
            self.assertFalse(self.ctrl.connected)

    def test_connect_runs_after_connect(self):
        """Successful connect() calls _after_connect() for verification."""
        with patch(
            "src.devices.eurotherm_controller.ModbusTcpClient"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.connect.return_value = True
            mock_client_cls.return_value = mock_client

            with patch.object(
                self.ctrl, "_after_connect"
            ) as mock_after:
                self.ctrl.connect()
                mock_after.assert_called_once()

    def test_connect_verification_failure_disconnects(self):
        """If _after_connect raises, connect() calls disconnect() and returns False."""
        with patch(
            "src.devices.eurotherm_controller.ModbusTcpClient"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.connect.return_value = True
            mock_client_cls.return_value = mock_client

            with patch.object(
                self.ctrl, "_after_connect",
                side_effect=ConnectionError("verification failed"),
            ):
                result = self.ctrl.connect()

            self.assertFalse(result)
            self.assertFalse(self.ctrl.connected)
            mock_client.close.assert_called_once()

    def test_connect_skips_if_already_connected(self):
        """Calling connect() when already connected returns True immediately."""
        self.ctrl._client = MagicMock()
        self.ctrl._connected = True

        # _after_connect should NOT be called
        with patch.object(self.ctrl, "_after_connect") as mock_after:
            result = self.ctrl.connect()
            self.assertTrue(result)
            mock_after.assert_not_called()


# ------------------------------------------------------------------
# disconnect()
# ------------------------------------------------------------------


class TestDisconnect(unittest.TestCase):

    def setUp(self):
        self.ctrl = EurothermController(_make_config())

    def test_disconnect_closes_client(self):
        mock_client = MagicMock()
        self.ctrl._client = mock_client
        self.ctrl._connected = True

        self.ctrl.disconnect()

        self.assertFalse(self.ctrl.connected)
        mock_client.close.assert_called_once()
        self.assertIsNone(self.ctrl._client)

    def test_disconnect_handles_close_error_gracefully(self):
        """If client.close() raises, disconnect() suppresses the error."""
        mock_client = MagicMock()
        mock_client.close.side_effect = OSError("socket error")
        self.ctrl._client = mock_client
        self.ctrl._connected = True

        # Should not raise
        self.ctrl.disconnect()

        self.assertFalse(self.ctrl.connected)
        self.assertIsNone(self.ctrl._client)

    def test_disconnect_when_not_connected(self):
        """disconnect() is a no-op when not connected."""
        self.ctrl._client = None
        self.ctrl._connected = False

        # Should not raise
        self.ctrl.disconnect()

        self.assertFalse(self.ctrl.connected)


# ------------------------------------------------------------------
# _after_connect() verification
# ------------------------------------------------------------------


class TestAfterConnect(unittest.TestCase):

    def setUp(self):
        self.ctrl = EurothermController(_make_config())
        self.mock_client = MagicMock()
        self.ctrl._client = self.mock_client

    def test_successful_verification(self):
        """_after_connect reads register and logs temperature."""
        self.mock_client.read_holding_registers.return_value = (
            _mock_register_response([425])
        )
        # Should not raise
        self.ctrl._after_connect()
        self.mock_client.read_holding_registers.assert_called_once_with(
            address=1, count=1, slave=255,
        )

    def test_verification_error_response_raises(self):
        """Modbus error response during verification raises ConnectionError."""
        self.mock_client.read_holding_registers.return_value = (
            _mock_error_response()
        )
        with self.assertRaises(ConnectionError):
            self.ctrl._after_connect()

    def test_verification_modbus_exception_raises(self):
        """ModbusException during verification raises ConnectionError."""
        self.mock_client.read_holding_registers.side_effect = (
            ModbusException("timeout")
        )
        with self.assertRaises(ConnectionError):
            self.ctrl._after_connect()

    def test_verification_negative_temperature(self):
        """Verification works with negative register values."""
        self.mock_client.read_holding_registers.return_value = (
            _mock_register_response([65486])  # -50 signed
        )
        # Should not raise
        self.ctrl._after_connect()


# ------------------------------------------------------------------
# Context Manager (__enter__ / __exit__)
# ------------------------------------------------------------------


class TestContextManager(unittest.TestCase):

    def test_context_manager_connects_and_disconnects(self):
        """Using 'with' statement calls connect() and disconnect()."""
        ctrl = EurothermController(_make_config())

        with patch.object(ctrl, "connect", return_value=True) as mock_connect:
            with patch.object(ctrl, "disconnect") as mock_disconnect:
                with ctrl:
                    pass
                mock_connect.assert_called_once()
                mock_disconnect.assert_called_once()


# ------------------------------------------------------------------
# reconnect loop integration
# ------------------------------------------------------------------


class TestReconnectLoop(unittest.TestCase):

    def setUp(self):
        self.ctrl = EurothermController(_make_config())

    def test_start_reconnect_loop_starts_thread(self):
        """start_reconnect_loop() creates a daemon thread."""
        self.ctrl.start_reconnect_loop()
        self.assertTrue(self.ctrl._running)
        self.assertIsNotNone(self.ctrl._reconnect_thread)
        self.assertTrue(self.ctrl._reconnect_thread.daemon)
        # Clean up
        self.ctrl.stop_reconnect_loop()

    def test_start_reconnect_loop_idempotent(self):
        """Calling start_reconnect_loop() twice doesn't create a second thread."""
        self.ctrl.start_reconnect_loop()
        thread = self.ctrl._reconnect_thread
        self.ctrl.start_reconnect_loop()
        self.assertIs(self.ctrl._reconnect_thread, thread)
        self.ctrl.stop_reconnect_loop()

    def test_stop_reconnect_loop(self):
        """stop_reconnect_loop() sets _running=False."""
        self.ctrl.start_reconnect_loop()
        self.ctrl.stop_reconnect_loop()
        self.assertFalse(self.ctrl._running)
