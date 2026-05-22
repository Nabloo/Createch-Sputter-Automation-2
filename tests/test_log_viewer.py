"""Tests for log-viewer features - gap detection and column matching."""

import unittest
from datetime import datetime

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
    """Matching channel names to CSV column headers."""

    def _make_log_data(self, headers):
        return LogData(
            headers=headers,
            timestamps=[datetime(2026, 5, 21, 15, 30, 0)],
            values={h: [1.0] for h in headers[1:] if h != "timestamp"},
            filepath="/tmp/test.csv",
            device_id="Test",
            date="2026-05-21",
        )

    def test_exact_match(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure", "ch2_pressure"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure"),
            "ch1_pressure",
        )

    def test_prefix_with_unit_brackets(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure [mbar]", "ch2_pressure [mbar]"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure"),
            "ch1_pressure [mbar]",
        )

    def test_prefix_with_space_suffix(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure mbar", "status_code"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure"),
            "ch1_pressure mbar",
        )

    def test_no_match(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure [mbar]"]
        )
        self.assertIsNone(
            PlotWidget._find_log_column(log_data, "ch3_pressure")
        )

    def test_partial_match_avoided(self):
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

    def test_status_code_column(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure [mbar]", "ch1_status_code"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_status_code"),
            "ch1_status_code",
        )

    def test_exact_preferred_over_prefix(self):
        log_data = self._make_log_data(
            ["timestamp", "ch1_pressure", "ch1_pressure [mbar]"]
        )
        self.assertEqual(
            PlotWidget._find_log_column(log_data, "ch1_pressure"),
            "ch1_pressure",
        )


if __name__ == "__main__":
    unittest.main()
