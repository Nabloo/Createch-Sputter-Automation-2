"""Tests for the DataLogger — CSV output, rotation, buffering, and shutdown."""

import csv
import os
import tempfile
from datetime import datetime, timezone
from unittest import TestCase, main

from src.data.datastore import DataStore
from src.logging.data_logger import DataLogger


class TestDataLogger(TestCase):
    """Verify CSV format, daily rotation, and lifecycle of DataLogger."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="datalogger_test_")
        self._config: dict = {
            "logging": {
                "enabled": True,
                "directory": self._tmpdir,
                "rotation_enabled": True,
                "flush_interval": 0.05,
            }
        }
        self._store = DataStore(history_size=100)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_headers_vcu_style(self) -> None:
        dl = DataLogger(self._config, self._store)
        data = {
            1: {"pressure": 1.0e-5, "status_code": 0, "status_text": "OK", "unit": "mbar"},
            2: {"pressure": 2.0e-6, "status_code": 0, "status_text": "OK", "unit": "mbar"},
        }
        dl._on_data("VCU-0", datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc), data)
        dl.shutdown()
        files = os.listdir(self._tmpdir)
        self.assertEqual(len(files), 1)
        filepath = os.path.join(self._tmpdir, files[0])
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        self.assertGreaterEqual(len(rows), 2)
        headers = rows[0]
        self.assertIn("timestamp", headers)
        self.assertIn("ch1_pressure [mbar]", headers)
        self.assertIn("ch1_status_code", headers)
        self.assertIn("ch1_status_text", headers)
        self.assertIn("ch2_pressure [mbar]", headers)
        self.assertNotIn("ch1_unit", headers)

    def test_row_values_vcu_style(self) -> None:
        dl = DataLogger(self._config, self._store)
        data = {1: {"pressure": 1.23e-5, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        ts = datetime(2025, 3, 10, 14, 30, 15, tzinfo=timezone.utc)
        dl._on_data("VCU-0", ts, data)
        dl.shutdown()
        files = os.listdir(self._tmpdir)
        filepath = os.path.join(self._tmpdir, files[0])
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        data_row = rows[1]
        self.assertEqual(data_row[0], "2025-03-10T14:30:15+00:00")
        self.assertAlmostEqual(float(data_row[1]), 1.23e-5)
        self.assertEqual(int(data_row[2]), 0)
        self.assertEqual(data_row[3], "OK")

    def test_flat_dict(self) -> None:
        dl = DataLogger(self._config, self._store)
        data = {"temperature": 25.5, "humidity": 60.2, "note": "stable"}
        ts = datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        dl._on_data("Sensor-X", ts, data)
        dl.shutdown()
        files = os.listdir(self._tmpdir)
        filepath = os.path.join(self._tmpdir, files[0])
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        headers = rows[0]
        self.assertIn("timestamp", headers)
        self.assertIn("humidity", headers)
        self.assertIn("note", headers)
        self.assertIn("temperature", headers)
        data_row = rows[1]
        self.assertEqual(float(data_row[1]), 60.2)
        self.assertEqual(data_row[2], "stable")
        self.assertEqual(float(data_row[3]), 25.5)

    def test_daily_rotation_creates_new_file(self) -> None:
        dl = DataLogger(self._config, self._store)
        data = {1: {"pressure": 1.0, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        dl._on_data("VCU-0", datetime(2025, 5, 1, 23, 59, 0, tzinfo=timezone.utc), data)
        dl._on_data("VCU-0", datetime(2025, 5, 2, 0, 1, 0, tzinfo=timezone.utc), data)
        dl.shutdown()
        files = sorted(os.listdir(self._tmpdir))
        self.assertEqual(len(files), 2)
        self.assertIn("2025-05-01", files[0])
        self.assertIn("2025-05-02", files[1])

    def test_daily_rotation_same_day_same_file(self) -> None:
        dl = DataLogger(self._config, self._store)
        data = {1: {"pressure": 1.0, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        dl._on_data("VCU-0", datetime(2025, 7, 7, 8, 0, 0, tzinfo=timezone.utc), data)
        dl._on_data("VCU-0", datetime(2025, 7, 7, 12, 0, 0, tzinfo=timezone.utc), data)
        dl._on_data("VCU-0", datetime(2025, 7, 7, 18, 0, 0, tzinfo=timezone.utc), data)
        dl.shutdown()
        files = os.listdir(self._tmpdir)
        self.assertEqual(len(files), 1)
        filepath = os.path.join(self._tmpdir, files[0])
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        self.assertEqual(len(rows), 4)

    def test_filename_format(self) -> None:
        dl = DataLogger(self._config, self._store)
        data = {1: {"pressure": 1.0, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        dl._on_data("VCU-0", datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc), data)
        dl.shutdown()
        files = os.listdir(self._tmpdir)
        self.assertEqual(files[0], "2025-01-01_VCU-0.csv")

    def test_flush_writes_buffered_data(self) -> None:
        dl = DataLogger(self._config, self._store)
        data = {1: {"pressure": 5.0, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        dl._on_data("VCU-0", datetime(2025, 8, 8, 12, 0, 0, tzinfo=timezone.utc), data)
        dl.flush()
        files = os.listdir(self._tmpdir)
        filepath = os.path.join(self._tmpdir, files[0])
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        self.assertEqual(len(rows), 2)
        dl.shutdown()

    def test_shutdown_flushes_all_unwritten_data(self) -> None:
        dl = DataLogger(self._config, self._store)
        dl._flush_interval = 9999.0
        for i in range(10):
            data = {1: {"pressure": float(i), "status_code": 0, "status_text": "OK", "unit": "mbar"}}
            dl._on_data("VCU-0", datetime(2025, 9, 9, 12, 0, i, tzinfo=timezone.utc), data)
        dl.shutdown()
        files = os.listdir(self._tmpdir)
        filepath = os.path.join(self._tmpdir, files[0])
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        self.assertEqual(len(rows), 11)

    def test_disabled_logging_creates_no_files(self) -> None:
        config = {"logging": {"enabled": False, "directory": self._tmpdir}}
        dl = DataLogger(config, self._store)
        data = {1: {"pressure": 1.0, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        dl._on_data("VCU-0", datetime(2025, 10, 10, 12, 0, 0, tzinfo=timezone.utc), data)
        dl.shutdown()
        csv_files = [f for f in os.listdir(self._tmpdir) if f.endswith(".csv")]
        self.assertEqual(len(csv_files), 0)

    def test_multiple_devices_separate_files(self) -> None:
        dl = DataLogger(self._config, self._store)
        data_a = {1: {"pressure": 1.0, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        data_b = {1: {"pressure": 2.0, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        dl._on_data("VCU-0", datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc), data_a)
        dl._on_data("VCU-1", datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc), data_b)
        dl.shutdown()
        files = sorted(os.listdir(self._tmpdir))
        self.assertEqual(len(files), 2)
        self.assertIn("VCU-0", files[0])
        self.assertIn("VCU-1", files[1])


if __name__ == "__main__":
    main()
