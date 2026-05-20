"""Thread-safe shared data model for measurements, status, and history.

The DataStore sits between the AcquisitionEngine and the GUI layer.
It subscribes to the engine, stores latest values and rolling
time-series buffers, and publishes updates to GUI subscribers.
"""

import logging
from collections import deque
from datetime import datetime
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: (device_id, timestamp, data_dict) -> None
DataCallback = Callable[[str, datetime, Dict[str, Any]], None]


class DataStore:
    """Thread-safe central storage for device measurements.

    - Maintains the *latest* measurement snapshot per device.
    - Keeps rolling time-series buffers (configurable max length).
    - Fires callbacks to GUI subscribers on every update.

    Usage::

        store = DataStore(history_size=10_000)
        engine.subscribe(store.on_update)        # wire acquisition -> store

        def on_gui_update(device_id, ts, data):
            plot.update(device_id, ts, data)

        store.subscribe(on_gui_update)
    """

    def __init__(self, history_size: int = 10_000) -> None:
        self._history_size = history_size
        self._lock = RLock()

        # device_id -> latest {channel: value, ...}
        self._latest: Dict[str, Dict[str, Any]] = {}

        # device_id -> {channel: deque of (timestamp, value)}
        self._history: Dict[str, Dict[str, deque]] = {}

        # device_id -> latest raw data dict (as received from engine)
        self._raw_latest: Dict[str, Dict[str, Any]] = {}

        # device_id -> latest timestamp
        self._timestamps: Dict[str, datetime] = {}

        # Subscribers notified on each update
        self._subscribers: List[DataCallback] = []

    # ------------------------------------------------------------------
    # Acquisition engine callback
    # ------------------------------------------------------------------

    def on_update(
        self, device_id: str, timestamp: datetime, data: Dict[str, Any]
    ) -> None:
        """Accept a measurement from the AcquisitionEngine.

        Called from a polling worker thread - must be fast and never block.
        """
        with self._lock:
            # Store raw data snapshot
            self._raw_latest[device_id] = data
            self._timestamps[device_id] = timestamp

            # Extract flat channel->value mapping from the data dict
            flat = self._flatten_data(data)
            self._latest[device_id] = flat

            # Append to rolling history buffers
            if device_id not in self._history:
                self._history[device_id] = {}
            for channel, value in flat.items():
                if channel not in self._history[device_id]:
                    self._history[device_id][channel] = deque(
                        maxlen=self._history_size
                    )
                self._history[device_id][channel].append((timestamp, value))

            # Snapshot subscriber list for safe iteration outside lock
            subscribers = list(self._subscribers)

        # Publish to GUI subscribers (outside lock)
        for callback in subscribers:
            try:
                callback(device_id, timestamp, data)
            except Exception:
                logger.exception(
                    "DataStore subscriber %r raised an exception", callback
                )

    # ------------------------------------------------------------------
    # Queries (thread-safe reads)
    # ------------------------------------------------------------------

    def get_latest(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest raw measurement dict for *device_id*, or None."""
        with self._lock:
            return self._raw_latest.get(device_id)

    def get_latest_timestamp(self, device_id: str) -> Optional[datetime]:
        """Return the timestamp of the most recent update for *device_id*."""
        with self._lock:
            return self._timestamps.get(device_id)

    def get_value(self, device_id: str, channel: str) -> Optional[Any]:
        """Return the latest scalar value for a specific channel."""
        with self._lock:
            dev = self._latest.get(device_id)
            if dev is None:
                return None
            return dev.get(channel)

    def get_history(
        self, device_id: str, channel: str
    ) -> List[Tuple[datetime, Any]]:
        """Return the rolling history for a channel as a list of (ts, value)."""
        with self._lock:
            dev_history = self._history.get(device_id, {})
            buf = dev_history.get(channel)
            if buf is None:
                return []
            return list(buf)

    def get_history_since(
        self, device_id: str, channel: str, since: datetime
    ) -> List[Tuple[datetime, Any]]:
        """Return history entries newer than *since*."""
        with self._lock:
            dev_history = self._history.get(device_id, {})
            buf = dev_history.get(channel)
            if buf is None:
                return []
            return [(ts, v) for ts, v in buf if ts > since]

    @property
    def device_ids(self) -> List[str]:
        """Snapshot of currently tracked device IDs."""
        with self._lock:
            return list(self._raw_latest.keys())

    @property
    def history_size(self) -> int:
        return self._history_size

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, callback: DataCallback) -> None:
        """Register *callback* to be called on every measurement update."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: DataCallback) -> None:
        """Remove a previously registered callback."""
        with self._lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _flatten_data(data: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten a measurement dict into channel->value pairs.

        Handles two conventions:

        1. **Dict-of-dicts** (VCU style):
           ``{1: {"pressure": ..., "unit": ...}, 2: {...}}``
           → ``{"ch1_pressure": ..., "ch1_unit": ..., "ch2_pressure": ...}``

        2. **Flat dict**: ``{"pressure": 1e-3, "status": 0}``
           -> returned as-is for scalar values.
        """
        flat: Dict[str, Any] = {}

        # Detect dict-of-dicts: all values are dicts -> channel-per-key format
        if data and all(isinstance(v, dict) for v in data.values()):
            for ch_key, ch_data in data.items():
                if isinstance(ch_data, dict):
                    for field, value in ch_data.items():
                        flat[f"ch{ch_key}_{field}"] = value
            return flat

        # Flat dict: copy scalar values only
        for key, value in data.items():
            if isinstance(value, (int, float, str, type(None))):
                flat[key] = value
        return flat

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset all stored data (keeps subscribers)."""
        with self._lock:
            self._latest.clear()
            self._history.clear()
            self._raw_latest.clear()
            self._timestamps.clear()
            logger.info("DataStore cleared")

    def remove_device(self, device_id: str) -> None:
        """Purge all data for a device."""
        with self._lock:
            self._latest.pop(device_id, None)
            self._history.pop(device_id, None)
            self._raw_latest.pop(device_id, None)
            self._timestamps.pop(device_id, None)
