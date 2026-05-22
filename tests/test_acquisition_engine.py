"""Integration tests for the AcquisitionEngine using a mock device."""

import threading
import time
import unittest
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.acquisition.engine import AcquisitionEngine
from src.devices.base_device import BaseDevice


class MockDevice(BaseDevice):
    """A minimal device driver for testing the acquisition engine."""

    def __init__(self, device_id: str = "Mock-0", config: Dict[str, Any] | None = None):
        _ = config  # unused – MockDevice is self-contained
        self._config: Dict[str, Any] = {"port": "MOCK"}
        self._serial = None
        self._lock = threading.RLock()
        self._connected = True
        self._running = False
        self._reconnect_thread = None
        self._reconnect_interval = 2.0
        self._max_retries = 0
        self._retry_count = 0
        self._device_id = device_id
        self._counter = 0

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def plot_channels(self) -> list:
        return ["pressure"]

    @property
    def status_channels(self) -> list:
        return ["pressure", "status_code", "status_text"]

    def poll(self) -> Dict[str, Any]:
        self._counter += 1
        return {
            "pressure": 1.0e-3 * self._counter,
            "status_code": 0,
            "status_text": "OK",
            "unit": "mbar",
        }

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def start_reconnect_loop(self) -> None:
        pass

    def stop_reconnect_loop(self, join_timeout: float = 3.0) -> None:
        pass

    @property
    def counter(self) -> int:
        return self._counter


class TestAcquisitionEngineLifecycle(unittest.TestCase):

    def setUp(self):
        self.engine = AcquisitionEngine()

    def tearDown(self):
        self.engine.stop()
        for did in list(self.engine.device_ids):
            self.engine.remove_device(did)

    def test_add_and_remove_device(self):
        dev = MockDevice("Mock-A")
        self.engine.add_device(dev, poll_interval=0.1)
        self.assertIn("Mock-A", self.engine.device_ids)
        self.engine.remove_device("Mock-A")
        self.assertNotIn("Mock-A", self.engine.device_ids)

    def test_add_multiple_devices(self):
        dev1 = MockDevice("Mock-1")
        dev2 = MockDevice("Mock-2")
        self.engine.add_device(dev1, poll_interval=0.1)
        self.engine.add_device(dev2, poll_interval=0.1)
        self.assertCountEqual(self.engine.device_ids, ["Mock-1", "Mock-2"])

    def test_replace_existing_device(self):
        self.engine.add_device(MockDevice("Mock-X"), poll_interval=0.1)
        self.engine.add_device(MockDevice("Mock-X"), poll_interval=0.1)
        self.assertEqual(len(self.engine.device_ids), 1)

    def test_start_stop_idempotent(self):
        dev = MockDevice("Mock-S")
        self.engine.add_device(dev, poll_interval=0.1)
        self.engine.start()
        self.assertTrue(self.engine.is_running)
        self.engine.start()
        self.assertTrue(self.engine.is_running)
        self.engine.stop()
        self.assertFalse(self.engine.is_running)
        self.engine.stop()

    def test_remove_nonexistent_device(self):
        self.engine.remove_device("Ghost")


class TestAcquisitionEnginePolling(unittest.TestCase):

    def setUp(self):
        self.engine = AcquisitionEngine()
        self.received: List[tuple] = []

    def tearDown(self):
        self.engine.stop()
        for did in list(self.engine.device_ids):
            self.engine.remove_device(did)

    def _on_update(self, device_id, timestamp, data):
        self.received.append((device_id, timestamp, data))

    def test_polling_publishes_updates(self):
        dev = MockDevice("Mock-P")
        self.engine.add_device(dev, poll_interval=0.05)
        self.engine.subscribe(self._on_update)
        self.engine.start()
        deadline = time.monotonic() + 1.0
        while len(self.received) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.engine.stop()
        self.assertGreaterEqual(len(self.received), 2)
        for device_id, ts, data in self.received:
            self.assertEqual(device_id, "Mock-P")
            self.assertIsInstance(ts, datetime)
            self.assertIn("pressure", data)

    def test_timestamps_are_utc(self):
        dev = MockDevice("Mock-T")
        self.engine.add_device(dev, poll_interval=0.05)
        self.engine.subscribe(self._on_update)
        self.engine.start()
        deadline = time.monotonic() + 1.0
        while len(self.received) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.engine.stop()
        for _, ts, _ in self.received:
            self.assertEqual(ts.tzinfo, timezone.utc)

    def test_no_polling_before_start(self):
        dev = MockDevice("Mock-Wait")
        self.engine.add_device(dev, poll_interval=0.05)
        self.engine.subscribe(self._on_update)
        time.sleep(0.3)
        self.assertEqual(len(self.received), 0)

    def test_unsubscribe_stops_receiving(self):
        dev = MockDevice("Mock-U")
        self.engine.add_device(dev, poll_interval=0.05)
        self.engine.subscribe(self._on_update)
        self.engine.start()
        deadline = time.monotonic() + 0.5
        while len(self.received) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.engine.unsubscribe(self._on_update)
        count_before = len(self.received)
        time.sleep(0.3)
        self.assertEqual(len(self.received), count_before)
        self.engine.stop()

    def test_subscriber_count(self):
        self.assertEqual(self.engine.subscriber_count, 0)
        self.engine.subscribe(self._on_update)
        self.assertEqual(self.engine.subscriber_count, 1)
        self.engine.subscribe(self._on_update)
        self.assertEqual(self.engine.subscriber_count, 1)
        self.engine.unsubscribe(self._on_update)
        self.assertEqual(self.engine.subscriber_count, 0)

    def test_multiple_subscribers(self):
        dev = MockDevice("Mock-Multi")
        self.engine.add_device(dev, poll_interval=0.05)
        results_a: List[tuple] = []
        results_b: List[tuple] = []
        def sub_a(did, ts, data):
            results_a.append((did, ts, data))
        def sub_b(did, ts, data):
            results_b.append((did, ts, data))
        self.engine.subscribe(sub_a)
        self.engine.subscribe(sub_b)
        self.engine.start()
        deadline = time.monotonic() + 0.8
        while len(results_a) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.engine.stop()
        self.assertEqual(len(results_a), len(results_b))


class TestAcquisitionEngineErrorHandling(unittest.TestCase):

    def setUp(self):
        self.engine = AcquisitionEngine()

    def tearDown(self):
        self.engine.stop()
        for did in list(self.engine.device_ids):
            self.engine.remove_device(did)

    def test_poll_exception_does_not_crash_worker(self):
        dev = MockDevice("Mock-Err")
        call_count = [0]
        original_poll = dev.poll
        def flaky_poll():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Simulated failure")
            return original_poll()
        dev.poll = flaky_poll
        received: List[tuple] = []
        def on_update(did, ts, data):
            received.append((did, ts, data))
        self.engine.add_device(dev, poll_interval=0.05)
        self.engine.subscribe(on_update)
        self.engine.start()
        deadline = time.monotonic() + 1.0
        while len(received) < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.engine.stop()
        self.assertGreaterEqual(len(received), 1)

    def test_subscriber_exception_does_not_crash_engine(self):
        dev = MockDevice("Mock-SubErr")
        received: List[tuple] = []
        def bad_subscriber(did, ts, data):
            raise RuntimeError("Subscriber on fire")
        def good_subscriber(did, ts, data):
            received.append((did, ts, data))
        self.engine.add_device(dev, poll_interval=0.05)
        self.engine.subscribe(bad_subscriber)
        self.engine.subscribe(good_subscriber)
        self.engine.start()
        deadline = time.monotonic() + 1.0
        while len(received) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.engine.stop()
        self.assertGreaterEqual(len(received), 2)

    def test_disconnected_device_not_polled(self):
        dev = MockDevice("Mock-Disc")
        self.engine.add_device(dev, poll_interval=0.05)
        # Simulate disconnection *after* add_device (which called connect()).
        dev._connected = False
        received: List[tuple] = []
        def on_update(did, ts, data):
            received.append((did, ts, data))
        self.engine.subscribe(on_update)
        self.engine.start()
        time.sleep(0.3)
        self.engine.stop()
        self.assertEqual(len(received), 0)

    def test_add_device_connect_failure_does_not_crash(self):
        """Regression test: add_device with a failing connect() must not crash.

        The _report_error method previously referenced an undefined
        "poll_interval" variable, causing a NameError that propagated
        to the caller and crashed the application.
        """
        class FailingDevice(MockDevice):
            def connect(self) -> bool:
                raise RuntimeError("COM6 not found")

        dev = FailingDevice("Mock-Fail")
        errors: list[str] = []

        def on_error(message: str) -> None:
            errors.append(message)

        self.engine.set_error_callback(on_error)
        # Must not raise any exception
        self.engine.add_device(dev, poll_interval=0.5)

        self.assertTrue(any("Mock-Fail" in e for e in errors))
        # Device should still be registered even if connect failed
        self.assertIn("Mock-Fail", self.engine.device_ids)


if __name__ == "__main__":
    unittest.main()
