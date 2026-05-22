"""Tests for src.data_logging.log_reader — CSV log file parsing."""

import csv
import os
import shutil
import tempfile
import unittest
from datetime import datetime

from src.data_logging.log_reader import LogData, LogFileReader, _parse_filename


class TestParseFilename(unittest.TestCase):
    """Filename parsing extracts date and device_id correctly."""

    def test_standard_filename(self):
        date_str, dev_id = _parse_filename("2026-05-21_VCU-0.csv")
        self.assertEqual(date_str, "2026-05-21")
        self.assertEqual(dev_id, "VCU-0")

    def test_full_path(self):
        date_str, dev_id = _parse_filename(
            "C:/logs/2026-01-01_MyDevice.csv"
        )
        self.assertEqual(date_str, "2026-01-01")
        self.assertEqual(dev_id, "MyDevice")

    def test_non_matching(self):
        date_str, dev_id = _parse_filename("readme.txt")
        self.assertEqual(date_str, "")
        self.assertEqual(dev_id, "")


class TestLogFileReader(unittest.TestCase):
    """CSV parsing correctness."""

    def _write_csv(self, name, lines):
        path = os.path.join(self._tmpdir, name)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for line in lines:
                writer.writerow(line)
        return path

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_parse_valid_csv(self):
        path = self._write_csv("2026-05-21_VCU-0.csv", [
            ["timestamp", "ch1_pressure [mbar]", "ch1_status_code"],
            ["2026-05-21T15:30:00+02:00", "1.23e-05", "0"],
            ["2026-05-21T15:30:01+02:00", "1.24e-05", "0"],
            ["2026-05-21T15:30:02+02:00", "1.25e-05", "0"],
        ])
        data = LogFileReader.read(path)
        self.assertEqual(data.device_id, "VCU-0")
        self.assertEqual(data.date, "2026-05-21")
        self.assertEqual(len(data.timestamps), 3)
        self.assertEqual(len(data.values["ch1_pressure [mbar]"]), 3)
        self.assertEqual(len(data.values["ch1_status_code"]), 3)
        self.assertAlmostEqual(data.values["ch1_pressure [mbar]"][0], 1.23e-05)

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            LogFileReader.read("nonexistent.csv")

    def test_empty_csv(self):
        path = self._write_csv("empty.csv", [])
        with self.assertRaises(ValueError):
            LogFileReader.read(path)

    def test_header_only(self):
        path = self._write_csv("hdr.csv", [
            ["timestamp", "ch1_pressure [mbar]"],
        ])
        data = LogFileReader.read(path)
        self.assertEqual(data.headers, ["timestamp", "ch1_pressure [mbar]"])
        self.assertEqual(len(data.timestamps), 0)

    def test_unsorted_timestamps(self):
        path = self._write_csv("unsorted.csv", [
            ["timestamp", "val"],
            ["2026-05-21T15:30:02+02:00", "3.0"],
            ["2026-05-21T15:30:00+02:00", "1.0"],
            ["2026-05-21T15:30:01+02:00", "2.0"],
        ])
        data = LogFileReader.read(path)
        ts = data.timestamps
        self.assertEqual(ts[0], datetime.fromisoformat("2026-05-21T15:30:00+02:00"))
        self.assertEqual(ts[1], datetime.fromisoformat("2026-05-21T15:30:01+02:00"))
        self.assertEqual(ts[2], datetime.fromisoformat("2026-05-21T15:30:02+02:00"))
        # Values must follow the same sort order
        self.assertEqual(data.values["val"], [1.0, 2.0, 3.0])

    def test_non_numeric_columns_skipped(self):
        path = self._write_csv("mixed.csv", [
            ["timestamp", "pressure", "note"],
            ["2026-05-21T15:30:00+02:00", "1.5", "ok"],
            ["2026-05-21T15:30:01+02:00", "2.0", "warning"],
        ])
        data = LogFileReader.read(path)
        self.assertIn("pressure", data.values)
        self.assertNotIn("note", data.values)
        self.assertEqual(data.values["pressure"], [1.5, 2.0])

    def test_blank_lines_skipped(self):
        path = self._write_csv("blanks.csv", [
            ["timestamp", "val"],
            ["2026-05-21T15:30:00+02:00", "1.0"],
            [],
            ["", ""],
            ["2026-05-21T15:30:01+02:00", "2.0"],
        ])
        data = LogFileReader.read(path)
        self.assertEqual(len(data.timestamps), 2)

    def test_filepath_is_absolute(self):
        path = self._write_csv("rel.csv", [
            ["timestamp", "val"],
            ["2026-05-21T15:30:00+02:00", "1.0"],
        ])
        data = LogFileReader.read(path)
        self.assertTrue(os.path.isabs(data.filepath))


if __name__ == "__main__":
    unittest.main()
