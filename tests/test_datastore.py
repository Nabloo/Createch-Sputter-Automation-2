"""Integration tests for the DataStore - thread safety, rolling buffers, queries."""

import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from src.data.datastore import DataStore


class TestDataStoreBasics(unittest.TestCase):

    def setUp(self):
        self.store = DataStore(history_size=100)

    def test_initial_state(self):
        self.assertEqual(self.store.device_ids, [])
        self.assertEqual(self.store.subscriber_count, 0)
        self.assertEqual(self.store.history_size, 100)
        self.assertIsNone(self.store.get_latest("X"))
        self.assertIsNone(self.store.get_latest_timestamp("X"))
        self.assertIsNone(self.store.get_value("X", "pressure"))
        self.assertEqual(self.store.get_history("X", "pressure"), [])

    def test_on_update_flat_dict(self):
        ts = datetime.now(timezone.utc)
        data = {"pressure": 1.23e-3, "status_code": 0, "status_text": "OK"}
        self.store.on_update("VCU-0", ts, data)
        self.assertEqual(self.store.get_latest("VCU-0"), data)
        self.assertEqual(self.store.get_latest_timestamp("VCU-0"), ts)
        self.assertEqual(self.store.get_value("VCU-0", "pressure"), 1.23e-3)
        self.assertEqual(self.store.get_value("VCU-0", "status_code"), 0)

    def test_on_update_vcu_dict_of_dicts(self):
        ts = datetime.now(timezone.utc)
        data = {
            1: {"pressure": 1.0e-3, "status_code": 0, "status_text": "OK", "unit": "mbar"},
            2: {"pressure": 5.0e-6, "status_code": 0, "status_text": "OK", "unit": "mbar"},
        }
        self.store.on_update("VCU-0", ts, data)
        self.assertEqual(self.store.get_latest("VCU-0"), data)
        self.assertEqual(self.store.get_value("VCU-0", "ch1_pressure"), 1.0e-3)
        self.assertEqual(self.store.get_value("VCU-0", "ch2_pressure"), 5.0e-6)
        self.assertEqual(self.store.get_value("VCU-0", "ch1_unit"), "mbar")

    def test_device_ids_tracks_devices(self):
        self.store.on_update("A", datetime.now(timezone.utc), {"x": 1})
        self.store.on_update("B", datetime.now(timezone.utc), {"x": 2})
        self.assertCountEqual(self.store.device_ids, ["A", "B"])


class TestDataStoreHistory(unittest.TestCase):

    def setUp(self):
        self.store = DataStore(history_size=5)

    def test_history_buffers_values(self):
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for i in range(3):
            ts = base + timedelta(seconds=i)
            self.store.on_update("D", ts, {"pressure": float(i), "status": 0})
        hist = self.store.get_history("D", "pressure")
        self.assertEqual(len(hist), 3)
        self.assertEqual(hist[0], (base, 0.0))
        self.assertEqual(hist[2], (base + timedelta(seconds=2), 2.0))

    def test_rolling_buffer_enforces_maxlen(self):
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for i in range(10):
            ts = base + timedelta(seconds=i)
            self.store.on_update("D", ts, {"pressure": float(i), "status": 0})
        hist = self.store.get_history("D", "pressure")
        self.assertEqual(len(hist), 5)
        self.assertEqual(hist[0][1], 5.0)
        self.assertEqual(hist[-1][1], 9.0)

    def test_get_history_since(self):
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            ts = base + timedelta(seconds=i)
            self.store.on_update("D", ts, {"pressure": float(i), "status": 0})
        since = base + timedelta(seconds=2)
        hist = self.store.get_history_since("D", "pressure", since)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0][1], 3.0)
        self.assertEqual(hist[1][1], 4.0)

    def test_get_history_since_none_matching(self):
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.store.on_update("D", base, {"pressure": 1.0, "status": 0})
        future = base + timedelta(hours=1)
        hist = self.store.get_history_since("D", "pressure", future)
        self.assertEqual(hist, [])


class TestDataStoreSubscriptions(unittest.TestCase):

    def setUp(self):
        self.store = DataStore()
        self.received = []

    def _callback(self, device_id, timestamp, data):
        self.received.append((device_id, timestamp, data))

    def test_subscribe_and_notify(self):
        self.store.subscribe(self._callback)
        ts = datetime.now(timezone.utc)
        self.store.on_update("X", ts, {"v": 42})
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0], ("X", ts, {"v": 42}))

    def test_unsubscribe_stops_notifications(self):
        self.store.subscribe(self._callback)
        self.store.unsubscribe(self._callback)
        self.store.on_update("X", datetime.now(timezone.utc), {"v": 1})
        self.assertEqual(len(self.received), 0)

    def test_multiple_subscribers(self):
        r2 = []
        def cb2(did, ts, data):
            r2.append((did, ts, data))
        self.store.subscribe(self._callback)
        self.store.subscribe(cb2)
        ts = datetime.now(timezone.utc)
        self.store.on_update("X", ts, {"v": 1})
        self.assertEqual(len(self.received), 1)
        self.assertEqual(len(r2), 1)

    def test_bad_subscriber_does_not_block_others(self):
        r2 = []
        def bad(did, ts, data):
            raise RuntimeError("boom")
        def good(did, ts, data):
            r2.append((did, ts, data))
        self.store.subscribe(bad)
        self.store.subscribe(good)
        self.store.on_update("X", datetime.now(timezone.utc), {"v": 1})
        self.assertEqual(len(r2), 1)

    def test_subscriber_count(self):
        self.assertEqual(self.store.subscriber_count, 0)
        self.store.subscribe(self._callback)
        self.assertEqual(self.store.subscriber_count, 1)
        self.store.subscribe(self._callback)
        self.assertEqual(self.store.subscriber_count, 1)


class TestDataStoreLifecycle(unittest.TestCase):

    def test_clear_removes_data_keeps_subscribers(self):
        store = DataStore()
        received = []
        store.subscribe(lambda did, ts, d: received.append((did, ts, d)))
        store.on_update("D", datetime.now(timezone.utc), {"v": 1})
        store.clear()
        self.assertEqual(store.device_ids, [])
        self.assertIsNone(store.get_latest("D"))
        self.assertEqual(store.subscriber_count, 1)

    def test_remove_device(self):
        store = DataStore()
        store.on_update("A", datetime.now(timezone.utc), {"v": 1})
        store.on_update("B", datetime.now(timezone.utc), {"v": 2})
        store.remove_device("A")
        self.assertEqual(store.device_ids, ["B"])
        self.assertIsNone(store.get_latest("A"))
        self.assertIsNotNone(store.get_latest("B"))

    def test_remove_nonexistent_device_no_error(self):
        store = DataStore()
        store.remove_device("Ghost")


class TestDataStoreThreadSafety(unittest.TestCase):

    def test_concurrent_writes_and_reads(self):
        store = DataStore(history_size=1000)
        errors = []
        done = threading.Event()

        def writer(device_id):
            try:
                for i in range(50):
                    store.on_update(
                        device_id,
                        datetime.now(timezone.utc),
                        {"count": i, "device": device_id},
                    )
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                while not done.is_set():
                    for did in list(store.device_ids):
                        store.get_latest(did)
                        store.get_value(did, "count")
                        store.get_history(did, "count")
                    time.sleep(0.002)
            except Exception as e:
                errors.append(e)

        # Separate lists for clarity rather than relying on daemon flags
        writers = [
            threading.Thread(target=writer, args=(wid,))
            for wid in ["W1", "W2", "W3"]
        ]
        readers = [
            threading.Thread(target=reader, daemon=True)
            for _ in range(3)
        ]

        # Start all threads
        for t in writers + readers:
            t.start()

        # Wait for writer threads to finish
        for t in writers:
            t.join()

        # Signal readers to stop
        done.set()

        # Wait for reader threads
        for t in readers:
            t.join(timeout=2.0)

        # Assertions
        self.assertEqual(len(errors), 0, f"Errors during concurrent access: {errors}")
        self.assertCountEqual(store.device_ids, ["W1", "W2", "W3"])
        for wid in ["W1", "W2", "W3"]:
            self.assertEqual(store.get_value(wid, "count"), 49)
            self.assertEqual(len(store.get_history(wid, "count")), 50)
