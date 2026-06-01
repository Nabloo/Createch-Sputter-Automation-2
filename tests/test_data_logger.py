"""Tests for the DataLogger — CSV output, rotation, buffering, and shutdown."""

import csv
import os
import tempfile
from datetime import datetime, timezone
from unittest import TestCase, main

from src.data.datastore import DataStore
from src.data_logging.data_logger import DataLogger


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
                "timezone_offset": 0,  # UTC for deterministic tests
            }
        }
        self._store = DataStore(history_size=100)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Filename: single file per date
    # ------------------------------------------------------------------

    def test_filename_format(self) -> None:
        dl = DataLogger(self._config, self._store)
        data = {1: {"pressure": 1.0, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        dl._on_data("VCU-0", datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc), data)
        dl.shutdown()
        files = os.listdir(self._tmpdir)
        self.assertEqual(files[0], "2025-01-01.csv")

    # ------------------------------------------------------------------
    # Header format: device-prefixed columns + unit row
    # ------------------------------------------------------------------

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
        self.assertGreaterEqual(len(rows), 3)  # header, unit row, ≥1 data row

        # Row 1: clean column names with device prefix
        headers = rows[0]
        self.assertIn("timestamp", headers)
        self.assertIn("VCU-0_ch1_pressure", headers)
        self.assertIn("VCU-0_ch1_status_code", headers)
        self.assertIn("VCU-0_ch1_status_text", headers)
        self.assertIn("VCU-0_ch2_pressure", headers)
        self.assertNotIn("VCU-0_ch1_unit", headers)

        # Row 2: units
        units = rows[1]
        ts_idx = headers.index("timestamp")
        self.assertEqual(units[ts_idx], "")
        p1_idx = headers.index("VCU-0_ch1_pressure")
        self.assertEqual(units[p1_idx], "mbar")
        sc_idx = headers.index("VCU-0_ch1_status_code")
        self.assertEqual(units[sc_idx], "")

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

        headers = rows[0]
        data_row = rows[2]  # row 1=header, row 2=units, row 3=data
        ts_idx = headers.index("timestamp")
        self.assertEqual(data_row[ts_idx], "2025-03-10T14:30:15+00:00")
        p_idx = headers.index("VCU-0_ch1_pressure")
        self.assertAlmostEqual(float(data_row[p_idx]), 1.23e-5)
        sc_idx = headers.index("VCU-0_ch1_status_code")
        self.assertEqual(int(data_row[sc_idx]), 0)
        st_idx = headers.index("VCU-0_ch1_status_text")
        self.assertEqual(data_row[st_idx], "OK")

    # ------------------------------------------------------------------
    # Flat dict (SQM style) — no _unit columns
    # ------------------------------------------------------------------

    def test_flat_dict(self) -> None:
        dl = DataLogger(self._config, self._store)
        data = {
            "ch1_rate": 5.0, "ch1_rate_unit": "Hz",
            "ch1_thickness": 100.0, "ch1_thickness_unit": "kA",
            "ch1_frequency": 6000000.0, "ch1_frequency_unit": "Hz",
        }
        ts = datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        dl._on_data("SQM-0", ts, data)
        dl.shutdown()
        files = os.listdir(self._tmpdir)
        filepath = os.path.join(self._tmpdir, files[0])
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        headers = rows[0]
        units_row = rows[1]
        self.assertIn("SQM-0_ch1_rate", headers)
        self.assertIn("SQM-0_ch1_thickness", headers)
        self.assertIn("SQM-0_ch1_frequency", headers)
        # No _unit columns
        self.assertNotIn("SQM-0_ch1_rate_unit", headers)
        self.assertNotIn("SQM-0_ch1_thickness_unit", headers)

        # Unit row: verify units
        rate_idx = headers.index("SQM-0_ch1_rate")
        self.assertEqual(units_row[rate_idx], "Hz")
        thick_idx = headers.index("SQM-0_ch1_thickness")
        self.assertEqual(units_row[thick_idx], "kA")
        freq_idx = headers.index("SQM-0_ch1_frequency")
        self.assertEqual(units_row[freq_idx], "Hz")

        # Data row values
        data_row = rows[2]
        self.assertEqual(float(data_row[rate_idx]), 5.0)
        self.assertEqual(float(data_row[thick_idx]), 100.0)
        self.assertEqual(float(data_row[freq_idx]), 6000000.0)

    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------

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
        # header + unit + 3 data rows
        self.assertEqual(len(rows), 5)

    # ------------------------------------------------------------------
    # Flush / shutdown
    # ------------------------------------------------------------------

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
        self.assertEqual(len(rows), 3)  # header, unit, data
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
        self.assertEqual(len(rows), 12)  # header + unit + 10 data

    def test_disabled_logging_creates_no_files(self) -> None:
        config = {"logging": {"enabled": False, "directory": self._tmpdir}}
        dl = DataLogger(config, self._store)
        data = {1: {"pressure": 1.0, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        dl._on_data("VCU-0", datetime(2025, 10, 10, 12, 0, 0, tzinfo=timezone.utc), data)
        dl.shutdown()
        csv_files = [f for f in os.listdir(self._tmpdir) if f.endswith(".csv")]
        self.assertEqual(len(csv_files), 0)

    # ------------------------------------------------------------------
    # Multi-device single file
    # ------------------------------------------------------------------

    def test_multiple_devices_same_file(self) -> None:
        dl = DataLogger(self._config, self._store)
        data_vcu = {1: {"pressure": 1.0, "status_code": 0, "status_text": "OK", "unit": "mbar"}}
        data_sqm = {
            "ch1_rate": 5.0, "ch1_rate_unit": "Hz",
            "ch1_thickness": 100.0, "ch1_thickness_unit": "kA",
        }
        dl._on_data("VCU-0", datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc), data_vcu)
        dl._on_data("SQM-0", datetime(2025, 1, 1, 12, 0, 1, tzinfo=timezone.utc), data_sqm)
        dl.shutdown()
        files = os.listdir(self._tmpdir)
        self.assertEqual(len(files), 1, "Both devices must share a single CSV")

        filepath = os.path.join(self._tmpdir, files[0])
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        headers = rows[0]
        units = rows[1]
        # Both device columns present
        self.assertIn("VCU-0_ch1_pressure", headers)
        self.assertIn("SQM-0_ch1_rate", headers)

        # Units: VCU pressure has mbar, SQM rate has Hz
        vcu_idx = headers.index("VCU-0_ch1_pressure")
        self.assertEqual(units[vcu_idx], "mbar")
        sqm_idx = headers.index("SQM-0_ch1_rate")
        self.assertEqual(units[sqm_idx], "Hz")

        # Two data rows (one per device), all columns padded
        self.assertEqual(len(rows), 4)  # header, unit, 2 data rows


if __name__ == "__main__":
    main()
