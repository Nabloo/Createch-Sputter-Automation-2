"""Thread-safe CSV data logger with daily rotation and buffered writes.

Subscribes to the DataStore and writes every measurement to a single CSV file
per day named ``logs/YYYY-MM-DD.csv``.  All devices share the same file.
A new file is created when the day changes (rotation at midnight UTC).

CSV format (3+ rows):
  Row 1 — clean column names: ``timestamp, VCU-0_ch1_pressure, SQM-0_ch1_rate, …``
  Row 2 — units per column:  ``, mbar, Hz, …``  (empty for unitless columns)
  Row 3+ — data rows

Columns are accumulated lazily: when a new device writes for the first time on
a given day, its columns are merged into the header and the file is rewritten.
"""

import csv
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.data.datastore import DataStore

logger = logging.getLogger(__name__)


class DataLogger:
    """Thread-safe CSV logger for device measurements.

    Subscribes to a :class:`~src.data.datastore.DataStore` and writes
    every measurement update to a single CSV file per day inside the
    configured log directory.

    Features
    --------
    - **Daily rotation**: a new file is opened when the date part of the
      timestamp changes (UTC midnight).
    - **Buffered writes**: rows are held in memory and flushed every
      *flush_interval* seconds, or when the file is rotated.
    - **Unit row**: row 2 of each file contains measurement units;
      row 1 has clean column names.
    - **Lazy column accumulation**: when a new device writes for the
      first time on a given day, its columns are merged in and the file
      is transparently rewritten.
    - **Thread-safe**: multiple polling workers can push data concurrently;
      internal locking ensures file operations are serialised.

    Configuration (from ``config["logging"]``)
    -------------------------------------------
    - ``enabled`` (bool, default True)
    - ``directory`` (str, default ``"logs"``)
    - ``rotation_enabled`` (bool, default True)
    - ``flush_interval`` (float, default 5.0) — seconds between forced flushes

    Usage::

        store = DataStore()
        logger = DataLogger(config, store)
        # … run acquisition …
        logger.shutdown()   # flush + close all files
    """

    def __init__(self, config: Dict[str, Any], store: DataStore) -> None:
        log_cfg: Dict[str, Any] = config.get("logging", {})

        self._enabled: bool = log_cfg.get("enabled", True)
        self._directory: str = log_cfg.get("directory", "logs")
        self._rotation_enabled: bool = log_cfg.get("rotation_enabled", True)
        self._flush_interval: float = float(log_cfg.get("flush_interval", 5.0))

        self._store = store
        self._lock = threading.RLock()

        # Buffered rows waiting for the next flush — stored as dicts
        # {header_name: value} so alignment to _file_headers is
        # always correct even when columns grow between buffer and flush.
        self._buffers: List[Dict[str, Any]] = []

        # ---- Active file state (per-date) ----
        self._file_handle: Any = None
        self._file_path: str = ""
        self._file_date: str = ""
        # Accumulated column names / units (grow as new devices appear)
        self._file_headers: List[str] = []   # row 1 column names
        self._file_units: List[str] = []     # row 2 unit strings
        # Dict-of-rows already flushed — kept in memory for potential
        # rewrite when columns are merged (new device writes).
        self._flushed_rows: List[Dict[str, Any]] = []

        # -------- Timezone resolution --------
        try:
            os_tz = datetime.now(timezone.utc).astimezone().tzinfo
            detected_tz = os_tz if os_tz is not None else timezone(timedelta(hours=2))
        except Exception:
            detected_tz = timezone(timedelta(hours=2))
        tz_offset = log_cfg.get("timezone_offset")
        self._tz = timezone(timedelta(hours=tz_offset)) if tz_offset is not None else detected_tz

        self._last_flush_time: float = time.monotonic()

        if self._enabled:
            os.makedirs(self._directory, exist_ok=True)
            self._store.subscribe(self._on_data)
            logger.info(
                "DataLogger enabled – directory=%s, flush_interval=%.1f s",
                self._directory, self._flush_interval,
            )

    # ------------------------------------------------------------------
    # DataStore callback
    # ------------------------------------------------------------------

    def _on_data(
        self, device_id: str, timestamp: datetime, data: Dict[str, Any]
    ) -> None:
        """Receive a measurement from the DataStore and enqueue it for writing."""
        if not self._enabled:
            return

        with self._lock:
            aware = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=timezone.utc)
            local_ts = aware.astimezone(self._tz)
            date_str = local_ts.strftime("%Y-%m-%d")

            # Derive the columns this device contributes
            dev_headers, dev_units = self._derive_device_columns(device_id, data)

            # Rotate or open file if needed
            if self._file_date != date_str:
                self._rotate(date_str, dev_headers, dev_units)
            else:
                # Check whether this device adds new columns
                self._merge_columns(dev_headers, dev_units)

            # Build row values and store as a dict keyed by header name.
            # Alignment to _file_headers happens at flush time.
            values = self._build_row(local_ts, data)
            row_dict: Dict[str, Any] = {}
            for header, val in zip(dev_headers, values):
                row_dict[header] = val
            self._buffers.append(row_dict)

            self._maybe_flush()

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def _rotate(self, date_str: str, dev_headers: List[str], dev_units: List[str]) -> None:
        """Close the current file and open a new one for *date_str*."""
        self._flush_all()
        self._close_file()
        self._file_date = date_str
        self._file_headers = list(dev_headers)
        self._file_units = list(dev_units)
        self._flushed_rows.clear()
        self._buffers.clear()
        self._open_file()

    def _merge_columns(self, dev_headers: List[str], dev_units: List[str]) -> None:
        """Merge device columns into the active file's column set.

        If new columns appear, the file is rewritten with the updated
        header rows and all previously flushed rows.
        """
        new_headers = list(self._file_headers)
        new_units = list(self._file_units)
        changed = False
        for h, u in zip(dev_headers, dev_units):
            if h not in new_headers:
                new_headers.append(h)
                new_units.append(u)
                changed = True

        if not changed:
            return

        # Close and rewrite the file with the expanded column set
        self._file_handle.close()
        self._file_headers = new_headers
        self._file_units = new_units
        self._rewrite_file()

    def _open_file(self) -> None:
        """Create or open the current-date file in append mode."""
        filename = f"{self._file_date}.csv"
        self._file_path = os.path.join(self._directory, filename)
        try:
            self._file_handle = open(self._file_path, "a", newline="", encoding="utf-8")
        except OSError:
            logger.exception("DataLogger: cannot open %s", self._file_path)
            self._file_handle = None
            return

        # Write headers if the file is new
        self._file_handle.seek(0, os.SEEK_END)
        if self._file_handle.tell() == 0:
            writer = csv.writer(self._file_handle)
            writer.writerow(self._file_headers)
            writer.writerow(self._file_units)
            self._file_handle.flush()
            logger.info("DataLogger: created %s (%d columns)", self._file_path, len(self._file_headers))

    def _rewrite_file(self) -> None:
        """Rewrite the entire file with updated headers and all flushed rows."""
        with open(self._file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(self._file_headers)
            writer.writerow(self._file_units)
            for row_dict in self._flushed_rows:
                row = [row_dict.get(h, "") for h in self._file_headers]
                writer.writerow(row)
        # Reopen in append mode
        self._file_handle = open(self._file_path, "a", newline="", encoding="utf-8")
        logger.debug(
            "DataLogger: rewrote %s with %d columns (+%d rows)",
            self._file_path, len(self._file_headers), len(self._flushed_rows),
        )

    def _close_file(self) -> None:
        """Close the active file handle if open."""
        if self._file_handle is not None:
            try:
                self._file_handle.close()
            except OSError:
                pass
            self._file_handle = None
            self._file_path = ""
            logger.debug("DataLogger: closed %s", self._file_path)

    # ------------------------------------------------------------------
    # Row formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_device_columns(
        device_id: str, data: Dict[str, Any]
    ) -> Tuple[List[str], List[str]]:
        """Build (header_names, unit_strings) for the columns *device_id* contributes.

        Column names are prefixed with ``<device_id>_`` so they are unique
        across devices (e.g. ``VCU-0_ch1_pressure``, ``SQM-0_ch1_rate``).

        Returns two parallel lists, always starting with ``"timestamp"`` / ``""``.
        """
        headers: List[str] = ["timestamp"]
        units: List[str] = [""]

        if data and all(isinstance(v, dict) for v in data.values()):
            # Dict-of-dicts (VCU style): keys are channel numbers.
            # Same _unit suffix convention as the flat-dict path:
            # any field ending in "_unit" is metadata and skipped;
            # its value becomes the unit for the corresponding base field.
            for ch_key in sorted(data.keys()):
                ch_data = data[ch_key]
                # Build unit map from _unit-suffixed fields
                unit_map: Dict[str, str] = {}
                for field in sorted(ch_data.keys()):
                    if field.endswith("_unit"):
                        base = field[:-5]
                        unit_map[base] = str(ch_data[field]) if ch_data[field] else ""
                for field in sorted(ch_data.keys()):
                    if field.endswith("_unit"):
                        continue
                    headers.append(f"{device_id}_ch{ch_key}_{field}")
                    units.append(unit_map.get(field, ""))
        else:
            # Flat dict (SQM style): keys like "ch1_rate", "ch1_rate_unit", …
            # Group non-unit fields and pair them with their units.
            skip_fields = set()
            for key in sorted(data.keys()):
                if key.endswith("_unit"):
                    skip_fields.add(key)
            for key in sorted(data.keys()):
                if key in skip_fields:
                    continue
                if isinstance(data[key], (int, float, str, type(None))):
                    headers.append(f"{device_id}_{key}")
                    unit_key = key + "_unit"
                    unit_val = data.get(unit_key, "")
                    units.append(unit_val if isinstance(unit_val, str) else "")

        return headers, units

    @staticmethod
    def _build_row(
        timestamp: datetime,
        data: Dict[str, Any],
    ) -> List[Any]:
        """Build a data row from *data*.

        Column order matches ``_derive_device_columns``.  Only the
        columns contributed by this device are filled; alignment to
        the file's header is handled later via ``_pad_row``.
        """
        row: List[Any] = [timestamp.isoformat()]

        if data and all(isinstance(v, dict) for v in data.values()):
            for ch_key in sorted(data.keys()):
                ch_data = data[ch_key]
                for field in sorted(ch_data.keys()):
                    if field.endswith("_unit"):
                        continue
                    row.append(ch_data[field])
        else:
            for key in sorted(data.keys()):
                if key.endswith("_unit"):
                    continue
                val = data[key]
                if isinstance(val, (int, float, str, type(None))):
                    row.append(val)

        return row

    # ------------------------------------------------------------------
    # Flush control
    # ------------------------------------------------------------------

    def _maybe_flush(self) -> None:
        """Flush all buffered rows if the flush interval has elapsed."""
        now = time.monotonic()
        if now - self._last_flush_time < self._flush_interval:
            return
        self._flush_all()
        self._last_flush_time = now

    def _flush_all(self) -> None:
        """Write all buffered rows to disk, aligning to the active file's columns."""
        if not self._buffers or self._file_handle is None:
            return

        writer = csv.writer(self._file_handle)
        for row_dict in self._buffers:
            # Align to _file_headers — each value goes to its named column
            row = [row_dict.get(h, "") for h in self._file_headers]
            writer.writerow(row)
            self._flushed_rows.append(row_dict)
        self._file_handle.flush()
        self._buffers.clear()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Force-flush all pending data to disk immediately."""
        with self._lock:
            self._flush_all()
            if self._file_handle is not None:
                try:
                    self._file_handle.flush()
                except OSError:
                    pass
            logger.debug("DataLogger: forced flush complete")

    def shutdown(self) -> None:
        """Flush all pending data and close all open log files."""
        with self._lock:
            self._enabled = False
            self._flush_all()
            self._close_file()
        logger.info("DataLogger: shutdown complete")
