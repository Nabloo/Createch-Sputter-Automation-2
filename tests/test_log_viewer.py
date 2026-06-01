"""Tests for log-viewer features - gap detection and column matching."""

import math
import unittest
from datetime import datetime, timedelta

from src.data_logging.log_reader import LogData
from src.gui.plot_widget import _split_into_segments, PlotWidget


class TestSplitIntoSegments(unittest.TestCase):
    """Gap-detection helper splits (x, y) series at gaps > threshold."""

    def test_contiguous_data_no_split(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        ys = [10.0, 11.0, 12.0, 13.0, 14.0]
        segs = _split_into_segments(xs, ys, gap_threshold_s=60.0)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0][0], xs)
        self.assertEqual(segs[0][1], ys)

    def test_gap_splits_into_two_segments(self):
        xs = [0.0, 1.0, 2.0, 122.0, 123.0, 124.0]
        ys = [10.0, 11.0, 12.0, 20.0, 21.0, 22.0]
        segs = _split_into_segments(xs, ys)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0][0], [0.0, 1.0, 2.0])
        self.assertEqual(segs[0][1], [10.0, 11.0, 12.0])
        self.assertEqual(segs[1][0], [122.0, 123.0, 124.0])
        self.assertEqual(segs[1][1], [20.0, 21.0, 22.0])

    def test_multiple_gaps(self):
        xs = [0.0, 1.0, 62.0, 63.0, 124.0, 125.0]
        ys = [0.0, 1.0, 10.0, 11.0, 20.0, 21.0]
        segs = _split_into_segments(xs, ys)
        self.assertEqual(len(segs), 3)

    def test_empty_input(self):
        segs = _split_into_segments([], [])
        self.assertEqual(segs, [])

    def test_single_point(self):
        xs = [42.0]
        ys = [7.0]
        segs = _split_into_segments(xs, ys)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0][0], [42.0])
        self.assertEqual(segs[0][1], [7.0])

    def test_two_points_with_gap(self):
        xs = [0.0, 200.0]
        ys = [5.0, 6.0]
        segs = _split_into_segments(xs, ys)
        self.assertEqual(len(segs), 2)

    def test_exact_threshold_no_split(self):
        xs = [0.0, 60.0]
        ys = [1.0, 2.0]
        segs = _split_into_segments(xs, ys, gap_threshold_s=60.0)
        self.assertEqual(len(segs), 1)

    def test_just_over_threshold_splits(self):
        xs = [0.0, 60.001]
        ys = [1.0, 2.0]
        segs = _split_into_segments(xs, ys, gap_threshold_s=60.0)
        self.assertEqual(len(segs), 2)

    def test_custom_threshold(self):
        xs = [0.0, 10.0, 20.0]
        ys = [0.0, 1.0, 2.0]
        segs = _split_into_segments(xs, ys, gap_threshold_s=5.0)
        self.assertEqual(len(segs), 3)
        segs = _split_into_segments(xs, ys, gap_threshold_s=15.0)
        self.assertEqual(len(segs), 1)

    def test_segments_cover_all_data(self):
        xs = list(range(100))
        ys = [float(i * 2) for i in range(100)]
        segs = _split_into_segments(xs, ys, gap_threshold_s=60.0)
        total_points = sum(len(s[0]) for s in segs)
        self.assertEqual(total_points, 100)
        all_x = [x for seg in segs for x in seg[0]]
        self.assertEqual(all_x, xs)


class TestFindLogColumn(unittest.TestCase):
    """Matching channel names to CSV column headers — new format (device-prefixed)."""

    def _make_log_data(self, headers):
        return LogData(
            headers=headers,
            timestamps=[datetime(2026, 5, 21, 15, 30, 0)],
            values={h: [1.0] for h in headers[1:] if h != "timestamp"},
            filepath="/tmp/test.csv",
            device_id="",
            date="2026-05-21",
        )

    # ------------------------------------------------------------------
    # New format: device-prefixed columns
    # ------------------------------------------------------------------

    def test_prefixed_exact_match(self):
        log_data = self._make_log_data(
            ["timestamp", "VCU-0_ch1_pressure", "VCU-0_ch2_pressure"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure", "VCU-0"),
            "VCU-0_ch1_pressure",
        )

    def test_prefixed_no_match_wrong_device(self):
        log_data = self._make_log_data(
            ["timestamp", "VCU-0_ch1_pressure"]
        )
        self.assertIsNone(
            PlotWidget._find_log_column(log_data, "ch1_pressure", "SQM-0")
        )

    def test_prefixed_multi_device(self):
        log_data = self._make_log_data(
            ["timestamp", "VCU-0_ch1_pressure", "SQM-0_ch1_rate"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure", "VCU-0"),
            "VCU-0_ch1_pressure",
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_rate", "SQM-0"),
            "SQM-0_ch1_rate",
        )

    # ------------------------------------------------------------------
    # Old format: backwards compatibility (unit in brackets, no device prefix)
    # ------------------------------------------------------------------

    def test_old_format_exact_match(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure", "ch2_pressure"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure"),
            "ch1_pressure",
        )

    def test_old_format_prefix_with_unit_brackets(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure [mbar]", "ch2_pressure [mbar]"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure"),
            "ch1_pressure [mbar]",
        )

    def test_old_format_prefix_with_space_suffix(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure mbar", "status_code"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure"),
            "ch1_pressure mbar",
        )

    def test_old_format_no_match(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure [mbar]"]
        )
        self.assertIsNone(
            PlotWidget._find_log_column(log_data, "ch3_pressure")
        )

    def test_old_format_partial_match_avoided(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure [mbar]", "ch10_pressure [mbar]"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure"),
            "ch1_pressure [mbar]",
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch10_pressure"),
            "ch10_pressure [mbar]",
        )

    def test_old_format_status_code_column(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure [mbar]", "ch1_status_code"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_status_code"),
            "ch1_status_code",
        )

    def test_old_format_exact_preferred_over_prefix(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure", "ch1_pressure [mbar]"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure"),
            "ch1_pressure",
        )

    # ------------------------------------------------------------------
    # Mixed: new format fallback
    # ------------------------------------------------------------------

    def test_new_format_falls_back_to_old_when_no_device_prefix(self):
        """When device_id is empty, should match old-style headers."""
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure [mbar]"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure", ""),
            "ch1_pressure [mbar]",
        )


class TestExampleLogs(unittest.TestCase):
    """Integration tests loading real CSV log files and verifying the full
    pipeline: LogFileReader → _find_log_column → _split_into_segments."""

    @classmethod
    def setUpClass(cls):
        from src.data_logging.log_reader import LogFileReader
        cls._log_22 = LogFileReader.read(
            "tests/example_logs/2026-05-22_VCU-0.csv"
        )
        cls._log_01 = LogFileReader.read(
            "tests/example_logs/2026-06-01.csv"
        )

    # ------------------------------------------------------------------
    # Basic parsing
    # ------------------------------------------------------------------

    def test_parse_2026_05_22_basics(self):
        """CSV parsed with correct metadata and structures."""
        log = self._log_22
        self.assertEqual(log.device_id, "VCU-0")
        self.assertEqual(log.date, "2026-05-22")
        self.assertGreater(len(log.timestamps), 100)
        self.assertIn("timestamp", log.headers)
        self.assertIn("ch1_pressure [mbar]", log.headers)
        self.assertIn("ch2_pressure [mbar]", log.headers)
        self.assertIn("ch3_pressure [mbar]", log.headers)
        self.assertIn("ch1_pressure [mbar]", log.values)
        self.assertIn("ch2_pressure [mbar]", log.values)
        self.assertIn("ch3_pressure [mbar]", log.values)
        self.assertIn("ch1_status_code", log.values)
        n = len(log.timestamps)
        for col in log.values:
            self.assertEqual(
                len(log.values[col]), n,
                f"Column {col!r} has {len(log.values[col])} values, expected {n}"
            )

    def test_parse_2026_05_22_basics(self):
        """Second example log also parses correctly."""
        log = self._log_22
        self.assertEqual(log.device_id, "VCU-0")
        self.assertEqual(log.date, "2026-05-22")
        self.assertGreater(len(log.timestamps), 50)
        self.assertIn("ch1_pressure [mbar]", log.headers)
        n = len(log.timestamps)
        for col in log.values:
            self.assertEqual(len(log.values[col]), n)

    def test_timestamps_are_timezone_aware(self):
        """CSV timestamps carry timezone info (UTC or +02:00)."""
        log = self._log_22
        for ts in log.timestamps[:5]:
            self.assertIsNotNone(ts.tzinfo)
        for i in range(1, len(log.timestamps)):
            self.assertGreaterEqual(log.timestamps[i], log.timestamps[i - 1])

    # ------------------------------------------------------------------
    # Column matching with real headers (old format)
    # ------------------------------------------------------------------

    def test_find_log_column_real_headers(self):
        """_find_log_column matches channel names to bracketed CSV headers (old format)."""
        log = self._log_22
        self.assertEqual(
            PlotWidget._find_log_column(log, "ch1_pressure"),
            "ch1_pressure [mbar]",
        )
        self.assertEqual(
            PlotWidget._find_log_column(log, "ch2_pressure"),
            "ch2_pressure [mbar]",
        )
        self.assertEqual(
            PlotWidget._find_log_column(log, "ch3_pressure"),
            "ch3_pressure [mbar]",
        )
        self.assertEqual(
            PlotWidget._find_log_column(log, "ch1_status_code"),
            "ch1_status_code",
        )

    def test_find_log_column_no_match_real(self):
        """_find_log_column returns None for nonexistent channels."""
        log = self._log_22
        self.assertIsNone(PlotWidget._find_log_column(log, "ch99_pressure"))
        self.assertIsNone(PlotWidget._find_log_column(log, "temperature"))

    # ------------------------------------------------------------------
    # Full pipeline simulation (mirrors PlotWidget.load_log_data)
    # ------------------------------------------------------------------

    def test_full_pipeline_time_filter(self):
        log = self._log_22
        timestamps = [t.replace(tzinfo=None) for t in log.timestamps]
        t_start = timestamps[0]
        t_end = timestamps[99]

        indices = [i for i, t in enumerate(timestamps) if t_start <= t <= t_end]
        self.assertEqual(len(indices), 100)

        col = PlotWidget._find_log_column(log, "ch1_pressure")
        self.assertIsNotNone(col)
        ys = [log.values[col][i] for i in indices]
        self.assertEqual(len(ys), 100)
        # First 100 points of ch1_pressure in 2026-05-22 start at ~640 and descend

    def test_full_pipeline_nan_filtering(self):
        log = self._log_22
        col = PlotWidget._find_log_column(log, "ch1_pressure")
        ys = log.values[col]
        clean = [y for y in ys if not math.isnan(y)]
        self.assertGreater(len(clean), 0)
        self.assertLessEqual(len(clean), len(ys))

    def test_full_pipeline_gap_detection(self):
        log = self._log_22
        timestamps = [t.replace(tzinfo=None) for t in log.timestamps]
        t_start = timestamps[0]
        t_end = timestamps[-1]
        indices = [i for i, t in enumerate(timestamps) if t_start <= t <= t_end]
        filtered_ts = [timestamps[i] for i in indices]
        xs = [(t - t_start).total_seconds() for t in filtered_ts]

        col = PlotWidget._find_log_column(log, "ch1_pressure")
        ys = [log.values[col][i] for i in indices]
        valid = [(x, y) for x, y in zip(xs, ys) if not math.isnan(y)]
        clean_xs = [v[0] for v in valid]
        clean_ys = [v[1] for v in valid]

        segments = _split_into_segments(clean_xs, clean_ys)
        self.assertGreaterEqual(len(segments), 2)
        total = sum(len(s[0]) for s in segments)
        self.assertEqual(total, len(clean_xs))
        self.assertGreater(len(segments[0][0]), 10)

    def test_full_pipeline_relative_x_values(self):
        log = self._log_22
        timestamps = [t.replace(tzinfo=None) for t in log.timestamps]
        t_start = timestamps[0]
        t_end = timestamps[-1]
        indices = [i for i, t in enumerate(timestamps) if t_start <= t <= t_end]
        filtered_ts = [timestamps[i] for i in indices]
        xs = [(t - t_start).total_seconds() for t in filtered_ts]

        self.assertEqual(xs[0], 0.0)
        for i in range(1, len(xs)):
            self.assertGreaterEqual(xs[i], xs[i - 1])
        self.assertGreater(xs[-1], 10000)
        self.assertLess(xs[-1], 20000)

    def test_full_pipeline_absolute_x_values(self):
        log = self._log_22
        timestamps = [t.replace(tzinfo=None) for t in log.timestamps]
        t_start = timestamps[0]
        t_end = timestamps[-1]
        indices = [i for i, t in enumerate(timestamps) if t_start <= t <= t_end]
        filtered_ts = [timestamps[i] for i in indices]
        xs = [t.timestamp() for t in filtered_ts]

        for x in xs[:5]:
            self.assertGreater(x, 1.7e9)
        for i in range(1, len(xs)):
            self.assertGreaterEqual(xs[i], xs[i - 1])

    def test_full_pipeline_ch2_ch3_values(self):
        log = self._log_22
        timestamps = [t.replace(tzinfo=None) for t in log.timestamps]
        t_start = timestamps[0]
        t_end = timestamps[-1]
        indices = [i for i, t in enumerate(timestamps) if t_start <= t <= t_end]

        for ch, expected_min, expected_max in [
            ("ch2_pressure", 0, 1000),
            ("ch3_pressure", 0, 1000),
        ]:
            col = PlotWidget._find_log_column(log, ch)
            self.assertIsNotNone(col, f"Column not found for {ch}")
            ys = [log.values[col][i] for i in indices if not math.isnan(log.values[col][i])]
            self.assertGreater(len(ys), 0, f"No valid values for {ch}")
            self.assertGreaterEqual(min(ys), expected_min, f"{ch} min too low")
            self.assertLessEqual(max(ys), expected_max, f"{ch} max too high")

    def test_full_pipeline_empty_time_range(self):
        log = self._log_22
        timestamps = [t.replace(tzinfo=None) for t in log.timestamps]
        t_start = timestamps[0] - timedelta(hours=10)
        t_end = timestamps[0] - timedelta(hours=1)
        indices = [i for i, t in enumerate(timestamps) if t_start <= t <= t_end]
        self.assertEqual(len(indices), 0)


if __name__ == "__main__":
    unittest.main()
