"""Thread-safe CSV data logger with daily rotation and buffered writes.

Subscribes to the DataStore and writes every measurement to a CSV file
named ``logs/YYYY-MM-DD_<device_id>.csv``.  A new file is created when
the day changes (rotation at midnight UTC).  Writes are buffered and
flushed periodically to minimise data loss on crash.

CSV headers include the measurement unit in the column name for
pressure channels (e.g. ``ch1_pressure [mbar]``).
"""

import csv
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.data.datastore import DataStore

logger = logging.getLogger(__name__)


class DataLogger:
    """Thread-safe CSV logger for device measurements.

    Subscribes to a :class:`~src.data.datastore.DataStore` and writes
    every measurement update to a device-specific CSV file inside the
    configured log directory.

    Features
    --------
    - **Daily rotation**: a new file is opened when the date part of the
      timestamp changes (UTC midnight).
    - **Buffered writes**: rows are held in memory and flushed every
      *flush_interval* seconds, or when the device file is rotated.
    - **Metadata headers**: the first row of each file is a header
      that includes channel names and measurement units.
    - **Thread-safe**: multiple polling workers can push data concurrently;
      internal locking ensures file operations are serialised.

    Configuration (from ``config["logging"]``)
    -------------------------------------------
    - ``enabled`` (bool, default True)
    - ``directory`` (str, default ``"logs"``)
    - ``rotation_enabled`` (bool, default True)
    - ``flush_interval`` (float, default 5.0) – seconds between forced flushes

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

        # device_id → list of CSV rows (each row is a list) waiting to be flushed
        self._buffers: Dict[str, List[List[Any]]] = {}
        # "{device_id}_{date_str}" → {"file": handle, "headers": [...]}
        self._files: Dict[str, Dict[str, Any]] = {}
        # device_id → last date string (for midnight-rotation detection)
        self._last_date: Dict[str, str] = {}

        # -------- Timezone resolution --------
        # Try to auto-detect OS timezone, fall back to GMT+2
        try:
            os_tz = datetime.now(timezone.utc).astimezone().tzinfo
            detected_tz = os_tz if os_tz is not None else timezone(timedelta(hours=2))
        except Exception:
            detected_tz = timezone(timedelta(hours=2))
        # Config can override (handy for deterministic tests)
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
    # DataStore callback (called from acquisition worker threads)
    # ------------------------------------------------------------------

    def _on_data(
        self, device_id: str, timestamp: datetime, data: Dict[str, Any]
    ) -> None:
        """Receive a measurement from the DataStore and enqueue it for writing."""
        if not self._enabled:
            return

        with self._lock:
            # Determine the date in the log timezone for rotation tracking
            aware = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=timezone.utc)
            local_ts = aware.astimezone(self._tz)
            date_str = local_ts.strftime("%Y-%m-%d")
            file_info = self._ensure_file(device_id, date_str, data)
            if file_info is None:
                return  # headers not yet determined – row buffered for next call

            # Build the CSV row (timestamp in log timezone)
            row = self._build_row(local_ts, data, file_info["headers"])
            self._buffers.setdefault(device_id, []).append(row)

            # Flush if enough time has passed
            self._maybe_flush()

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def _ensure_file(
        self,
        device_id: str,
        date_str: str,
        data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Return the open-file info for *device_id* on *date_str*.

        Opens a new file and writes headers on first use or day change.
        Returns ``None`` if headers are not yet determined (first data
        point for a device) — the caller should buffer the row and retry
        on the next call after headers are set.
        """
        key = f"{device_id}_{date_str}"

        # Already open for this device + date?
        if key in self._files:
            return self._files[key]

        # Rotation: close the previous day's file for this device
        if self._rotation_enabled and device_id in self._last_date:
            if self._last_date[device_id] != date_str:
                self._close_device(device_id)

        # Open new file
        filename = f"{date_str}_{device_id}.csv"
        filepath = os.path.join(self._directory, filename)
        try:
            f = open(filepath, "a", newline="", encoding="utf-8")
        except OSError:
            logger.exception("DataLogger: cannot open %s", filepath)
            return None

        # Generate headers from the first data point's structure
        headers = self._generate_headers(data)
        file_info: Dict[str, Any] = {
            "file": f,
            "headers": headers,
            "path": filepath,
        }
        self._files[key] = file_info
        self._last_date[device_id] = date_str

        # Write headers (only if the file is new / empty)
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            writer = csv.writer(f)
            writer.writerow(headers)
            f.flush()
            logger.info("DataLogger: created %s", filepath)

        return file_info

    def _close_device(self, device_id: str) -> None:
        """Flush and close all open files for *device_id*."""
        # Flush buffered rows first
        buf = self._buffers.pop(device_id, None)
        if buf:
            # Find the current file for this device and write buffered rows
            for key, info in list(self._files.items()):
                if key.startswith(f"{device_id}_"):
                    writer = csv.writer(info["file"])
                    writer.writerows(buf)
                    info["file"].flush()
                    break
            else:
                logger.warning(
                    "DataLogger: buffered rows for %s but no open file – dropped",
                    device_id,
                )

        for key in list(self._files):
            if key.startswith(f"{device_id}_"):
                info = self._files.pop(key)
                try:
                    info["file"].close()
                except OSError:
                    pass
                logger.debug("DataLogger: closed %s", info["path"])

    # ------------------------------------------------------------------
    # Row formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_headers(data: Dict[str, Any]) -> List[str]:
        """Derive CSV column names from the structure of *data*.

        For VCU-style nested dicts each channel contributes columns
        like ``ch1_pressure [mbar]``, ``ch1_status_code``, …
        For flat dicts the keys are used directly.

        The ``unit`` field is folded into the pressure column name
        rather than getting its own column.
        """
        headers: List[str] = ["timestamp"]

        if data and all(isinstance(v, dict) for v in data.values()):
            # Dict-of-dicts (VCU style)
            for ch_key in sorted(data.keys()):
                ch_data = data[ch_key]
                unit = ch_data.get("unit", "")
                for field in sorted(ch_data.keys()):
                    if field == "unit":
                        continue
                    if field == "pressure" and unit:
                        headers.append(f"ch{ch_key}_{field} [{unit}]")
                    else:
                        headers.append(f"ch{ch_key}_{field}")
        else:
            # Flat dict
            for key in sorted(data.keys()):
                if isinstance(data[key], (int, float, str, type(None))):
                    headers.append(key)

        return headers

    @staticmethod
    def _build_row(
        timestamp: datetime,
        data: Dict[str, Any],
        headers: List[str],
    ) -> List[Any]:
        """Format a measurement as a list suitable for ``csv.writer.writerow``.

        Values are written in the same order as *headers*.
        The first column is the ISO-8601 timestamp.
        """
        row: List[Any] = [timestamp.isoformat()]

        if data and all(isinstance(v, dict) for v in data.values()):
            for ch_key in sorted(data.keys()):
                ch_data = data[ch_key]
                for field in sorted(ch_data.keys()):
                    if field == "unit":
                        continue
                    row.append(ch_data[field])
        else:
            for key in sorted(data.keys()):
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
        """Write all buffered rows to disk and flush file handles."""
        for device_id, rows in list(self._buffers.items()):
            if not rows:
                continue
            # Find the open file for this device
            for key, info in self._files.items():
                if key.startswith(f"{device_id}_"):
                    try:
                        writer = csv.writer(info["file"])
                        writer.writerows(rows)
                        info["file"].flush()
                    except OSError:
                        logger.exception(
                            "DataLogger: error writing to %s", info["path"]
                        )
                    break
            self._buffers[device_id] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Force-flush all pending data to disk immediately."""
        with self._lock:
            self._flush_all()
            # Also flush any open files that have no buffered rows
            # (belt-and-suspenders)
            for info in self._files.values():
                try:
                    info["file"].flush()
                except OSError:
                    pass
            logger.debug("DataLogger: forced flush complete")

    def shutdown(self) -> None:
        """Flush all pending data and close all open log files."""
        with self._lock:
            self._enabled = False
            self._flush_all()
            for device_id in list(self._buffers):
                self._close_device(device_id)
            # Any remaining open files (shouldn't be any, but be safe)
            for info in self._files.values():
                try:
                    info["file"].close()
                except OSError:
                    pass
            self._files.clear()
        logger.info("DataLogger: shutdown complete")
