"""CSV log-file reader – parses DataLogger output into a plottable data model.

Parsing runs in a worker thread so the GUI stays responsive for large log files.
"""

import csv
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal

logger = logging.getLogger(__name__)


@dataclass
class LogData:
    """In-memory representation of a parsed CSV log file.

    Attributes
    ----------
    headers:
        Clean column names (row 1), e.g. ``["timestamp", "VCU-0_ch1_pressure", ...]``.
    timestamps:
        Parsed timestamps, sorted ascending.
    values:
        Column-name → parallel list of float values.  Only numeric
        columns are included; non-numeric columns are silently skipped.
    filepath:
        Absolute path to the source CSV file.
    device_id:
        Always empty for unified files (was the device identifier in old format).
    date:
        Date string extracted from the filename (``YYYY-MM-DD``).
    """
    headers: List[str] = field(default_factory=list)
    timestamps: List[datetime] = field(default_factory=list)
    values: Dict[str, List[float]] = field(default_factory=dict)
    filepath: str = ""
    device_id: str = ""
    date: str = ""


_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:_(.+))?\.csv$")

# Timestamp-ish prefix for detecting old-format files (no unit row)
_TS_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]")


def _parse_filename(filepath: str) -> Tuple[str, str]:
    """Extract (date_str, device_id) from *filepath*, or ("", "")."""
    basename = os.path.basename(filepath)
    m = _FILENAME_RE.match(basename)
    if m:
        return m.group(1), m.group(2) or ""
    return "", ""


class _ParseWorker(QObject):
    """Worker that does the actual file-I/O + parsing in a background thread."""
    finished = Signal(object)   # LogData on success
    error = Signal(str)         # error message on failure

    def __init__(self, filepath: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._filepath = filepath

    def run(self) -> None:
        try:
            data = LogFileReader.read(self._filepath)
            self.finished.emit(data)
        except FileNotFoundError:
            self.error.emit(f"File not found: {self._filepath}")
        except Exception as exc:
            logger.exception("LogFileReader: error reading %s", self._filepath)
            self.error.emit(f"Failed to read log: {exc}")


class LogFileReader(QObject):
    """Parses CSV log files produced by :class:`DataLogger`.

    Supports both synchronous (``read``) and asynchronous (``load``)
    usage.  The async path runs file I/O in a worker thread and emits
    ``loaded`` / ``error_occurred`` on completion.
    """
    loaded = Signal(object)       # LogData
    error_occurred = Signal(str)  # error message

    @staticmethod
    def read(filepath: str) -> LogData:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Log file not found: {filepath}")
        date_str, device_id = _parse_filename(filepath)

        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                raw_headers = next(reader)
            except StopIteration:
                raise ValueError("CSV file is empty (no header row)")
            if not raw_headers:
                raise ValueError("CSV file has empty header row")
            headers = raw_headers

            # Peek at row 2: if it looks like a timestamp, the file is
            # old-format (no unit row).  Otherwise treat row 2 as a unit
            # row and skip it.
            data_start_row = 2
            try:
                row2 = next(reader)
            except StopIteration:
                row2 = None
            if row2 and row2[0] and _TS_LIKE_RE.match(row2[0].strip()):
                # Old format — row 2 is the first data row, re-process it
                first_data_row = row2
                data_start_row = 2
            else:
                # New format — row 2 is the unit row, skip it
                first_data_row = None
                data_start_row = 3

            numeric_cols: Dict[int, str] = {}
            for i, h in enumerate(headers):
                if i == 0:
                    continue
                numeric_cols[i] = h
            values = {h: [] for h in numeric_cols.values()}
            timestamps = []

            def _process_row(row, row_idx):
                if not row or not row[0].strip():
                    return
                ts_str = row[0].strip()
                try:
                    ts = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    logger.debug("Skipping row %d: invalid timestamp %r", row_idx, ts_str)
                    return
                timestamps.append(ts)
                for col_idx, col_name in numeric_cols.items():
                    if col_idx >= len(row):
                        values[col_name].append(float("nan"))
                        continue
                    cell = row[col_idx].strip()
                    if not cell:
                        values[col_name].append(float("nan"))
                        continue
                    try:
                        val = float(cell)
                    except (ValueError, TypeError):
                        val = float("nan")
                    values[col_name].append(val)

            if first_data_row is not None:
                _process_row(first_data_row, 2)

            for row_idx, row in enumerate(reader, start=data_start_row):
                _process_row(row, row_idx)
            if timestamps:
                indexed = list(enumerate(timestamps))
                indexed.sort(key=lambda x: x[1])
                perm = [i for i, _ in indexed]
                timestamps = [timestamps[i] for i in perm]
                for col_name in list(values):
                    old = values[col_name]
                    while len(old) < len(perm):
                        old.append(float("nan"))
                    values[col_name] = [old[i] for i in perm]
            # Remove columns where every value is NaN (non-numeric columns)
            values = {
                k: v for k, v in values.items()
                if not all(math.isnan(x) for x in v)
            }
            logger.info("LogFileReader: parsed %s — %d rows, %d columns", os.path.basename(filepath), len(timestamps), len(values))
            return LogData(headers=headers, timestamps=timestamps, values=values, filepath=os.path.abspath(filepath), device_id=device_id, date=date_str)

    def load(self, filepath: str) -> None:
        """Start asynchronous loading of *filepath*.

        Emits ``loaded(LogData)`` on success or ``error_occurred(str)``
        on failure.  The file is read and parsed in a worker thread.

        Calling ``load()`` while a previous load is still in progress
        is safe — the old worker is disconnected first.
        """
        # Guard against re-entrant calls
        self.cancel()
        self._thread = QThread(self)
        self._worker = _ParseWorker(filepath)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.error.connect(self._worker.deleteLater)
        self._thread.start()

    def cancel(self) -> None:
        """Cancel any in-progress async load."""
        worker = getattr(self, "_worker", None)
        thread = getattr(self, "_thread", None)
        if worker is not None:
            try:
                worker.finished.disconnect(self._on_worker_finished)
            except (TypeError, RuntimeError):
                pass
            try:
                worker.error.disconnect(self._on_worker_error)
            except (TypeError, RuntimeError):
                pass
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(1000)

    def _on_worker_finished(self, data: LogData) -> None:
        """Worker success — forward to public signal."""
        self.loaded.emit(data)

    def _on_worker_error(self, message: str) -> None:
        """Worker failure — forward to public signal."""
        self.error_occurred.emit(message)
