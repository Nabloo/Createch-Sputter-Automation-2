"""Test package — configures logging for visibility in PyCharm's Run window."""

import logging

# Configure root logger so log messages appear in PyCharm's console during tests.
# basicConfig is a no-op if already called (e.g. by main.py running first).
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
