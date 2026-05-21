"""Acquisition engine – background polling workers for all active devices.

One worker thread per device.  Handles reconnects (delegated to the
device driver), timestamps every measurement, and publishes updates
to subscribers (the shared data model, T4, will be the primary subscriber).
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.devices.base_device import BaseDevice

logger = logging.getLogger(__name__)

#: Signature for update callbacks: (device_id, timestamp, data_dict) -> None
UpdateCallback = Callable[[str, datetime, Dict[str, Any]], None]

#: Signature for error callbacks: (combined_message) -> None
#: The message includes the device_id, e.g. "VCU-0: Poll error: …"
ErrorCallback = Callable[[str], None]


class AcquisitionEngine:
    """Manages polling workers for all active devices.

    Usage::

        engine = AcquisitionEngine()
        engine.add_device(vcu_device, poll_interval=0.5)

        def on_update(device_id, ts, data):
            print(device_id, ts, data)

        engine.subscribe(on_update)
        engine.start()               # start all workers
        # …
        engine.stop()
        engine.remove_device("VCU-0")
    """

    def __init__(self) -> None:
        self._devices: Dict[str, BaseDevice] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._poll_interval: Dict[str, float] = {}
        self._subscribers: List[UpdateCallback] = []
        self._error_callback: Optional[ErrorCallback] = None
        self._polling_active: Dict[str, bool] = {}
        self._lock = threading.RLock()
        self._started = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_device(self, device: BaseDevice, poll_interval: float = 1.0) -> None:
        """Register a device and prepare its worker thread.

        The device is connected and its reconnection loop is started
        immediately.  Polling begins when :meth:`start` is called.

        Parameters
        ----------
        device:
            A concrete :class:`BaseDevice` instance (already configured).
        poll_interval:
            Seconds between consecutive :meth:`BaseDevice.poll` calls.
        """
        device_id = device.device_id

        # Remove if already registered (safe — remove_device handles missing).
        if device_id in self._devices:
            logger.warning("Device %s already registered; replacing", device_id)
            self.remove_device(device_id)

        with self._lock:
            self._devices[device_id] = device
            self._poll_interval[device_id] = poll_interval
            self._polling_active[device_id] = False

        # Connect and start background reconnection *outside* the lock
        # so that slow serial operations don't block other threads.
        try:
            device.connect()
            if not device.connected:
                logger.warning("%s: failed to connect", device_id)
                self._report_error(device_id, "Failed to connect")
        except Exception as exc:
            logger.warning("%s: connection error — %s", device_id, exc)
            self._report_error(device_id, f"Connection error — {exc}")

        device.start_reconnect_loop()

        logger.info(
            "AcquisitionEngine: added %s (poll interval %.2f s)",
            device_id,
            poll_interval,
        )

    def remove_device(self, device_id: str) -> None:
        """Stop polling, disconnect, and remove *device_id* from the engine."""
        # Signal the worker to stop (under lock).
        with self._lock:
            # Guard: nothing registered under this ID.  Covers both
            # pre-start and post-removal cases safely.
            if device_id not in self._devices:
                return
            if device_id in self._polling_active:
                self._polling_active[device_id] = False

        # Join the worker thread (outside lock to avoid deadlock with _publish).
        thread = self._threads.pop(device_id, None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

        # Disconnect and clean up device resources.
        device = self._devices.pop(device_id, None)
        if device is not None:
            device.stop_reconnect_loop()
            device.disconnect()

        # Clean up remaining tracking dicts.
        with self._lock:
            self._poll_interval.pop(device_id, None)
            self._polling_active.pop(device_id, None)

        logger.info("AcquisitionEngine: removed %s", device_id)

    def start(self) -> None:
        """Start polling all registered devices."""
        with self._lock:
            if self._started:
                logger.warning("AcquisitionEngine already started")
                return
            self._started = True

            for device_id in list(self._devices):
                self._polling_active[device_id] = True

                if device_id in self._threads and self._threads[device_id].is_alive():
                    logger.debug("Worker for %s already running", device_id)
                    continue

                interval = self._poll_interval.get(device_id, 1.0)
                thread = threading.Thread(
                    target=self._poll_worker,
                    args=(self._devices[device_id], device_id, interval),
                    name="poll-{}".format(device_id),
                    daemon=True,
                )
                self._threads[device_id] = thread
                thread.start()
                logger.info("Started polling worker for %s", device_id)

    def stop(self) -> None:
        """Stop all polling workers (keeps devices registered)."""
        with self._lock:
            if not self._started:
                return
            self._started = False
            for device_id in list(self._polling_active):
                self._polling_active[device_id] = False

        # Join all worker threads
        for device_id, thread in list(self._threads.items()):
            if thread.is_alive():
                thread.join(timeout=5.0)
            self._threads.pop(device_id, None)

        logger.info("AcquisitionEngine: all polling workers stopped")

    def set_error_callback(self, callback: Optional[ErrorCallback]) -> None:
        """Register an optional callback for poll & connection errors.

        The callback is invoked with a single combined string such as
        ``"VCU-0: Poll error — …"`` from the worker thread *and* at the
        :meth:`add_device` call site, so implementations must be
        thread-safe (e.g. use a ``QThread`` signal).
        """
        self._error_callback = callback

    def subscribe(self, callback: UpdateCallback) -> None:
        """Subscribe to timestamped measurement updates."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: UpdateCallback) -> None:
        """Remove a previously registered subscriber."""
        with self._lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    @property
    def device_ids(self) -> List[str]:
        """Return a snapshot of currently registered device IDs."""
        with self._lock:
            return list(self._devices.keys())

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _poll_worker(self, device: BaseDevice, device_id: str,
                     poll_interval: float) -> None:
        """Single-device polling loop – runs inside a dedicated daemon thread."""

        while self._polling_active.get(device_id, False):
            try:
                if not device.connected:
                    time.sleep(0.1)
                    continue

                data = device.poll()
                timestamp = datetime.now(timezone.utc)
                self._publish(device_id, timestamp, data)

            except (ConnectionError, TimeoutError, OSError) as exc:
                logger.debug("%s poll error: %s", device_id, exc)
                self._report_error(device_id, f"Poll error: {exc}")
                # Sleep in small steps so we can react to stop quickly
                deadline = time.monotonic() + poll_interval
                while time.monotonic() < deadline:
                    if not self._polling_active.get(device_id, False):
                        break
                    time.sleep(min(0.1, deadline - time.monotonic()))
            except Exception:
                logger.exception("%s unexpected poll error", device_id)
                self._report_error(device_id, f"Unexpected poll error")
                # Sleep in small steps so we can react to stop quickly
                deadline = time.monotonic() + poll_interval
                while time.monotonic() < deadline:
                    if not self._polling_active.get(device_id, False):
                        break
                    time.sleep(min(0.1, deadline - time.monotonic()))

    def _report_error(self, device_id: str, message: str) -> None:
        """Invoke the error callback, if set.

        Combines *device_id* and *message* into a single string so the
        callback only needs one argument (compatible with Qt Signals).
        """
        if self._error_callback is not None:
            try:
                self._error_callback(f"{device_id}: {message}")
            except Exception:
                logger.exception("Error callback raised an exception")

    def _publish(
        self, device_id: str, timestamp: datetime, data: Dict[str, Any]
    ) -> None:
        """Deliver a measurement to every subscriber."""
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(device_id, timestamp, data)
            except Exception:
                logger.exception(
                    "Subscriber %r raised an exception for %s", callback, device_id
                )
