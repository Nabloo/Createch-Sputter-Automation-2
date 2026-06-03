"""Configuration loader, saver, and state-persistence helpers."""

import copy
import json
import os
from typing import Any, Dict, List, Optional, Tuple

# Resolve to project root so config.json always lives there,
# regardless of the working directory at launch time.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")
BACKUP_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "backup_config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
  "devices": [
    {
      "type": "VCUController",
      "device_id": "VCU-0",
      "address": 0,
      "port": "COM6",
      "baudrate": 19200,
      "bytesize": 8,
      "parity": "N",
      "stopbits": 1,
      "timeout": 1.0,
      "write_timeout": 1.0,
      "reconnect_interval": 3.0,
      "max_retries": 5,
      "number_of_sensors": 3,
      "poll_interval": 0.5
    },
    {
      "type": "EurothermController",
      "device_id": "Eurotherm-0",
      "address": 0,
      "host": "192.168.117.30",
      "modbus_port": 502,
      "slave_id": 255,
      "timeout": 1.0,
      "reconnect_interval": 3.0,
      "max_retries": 5,
      "number_of_sensors": 1,
      "poll_interval": 1.0,
      "unit": "deg C"
    },
    {
      "type": "SQMController",
      "device_id": "SQM-0",
      "address": 0,
      "port": "COM6",
      "baudrate": 19200,
      "timeout": 1.0,
      "write_timeout": 1.0,
      "reconnect_interval": 3.0,
      "max_retries": 5,
      "number_of_sensors": 1,
      "poll_interval": 1.0,
      "units": {
        "rate": "\u00c5/s",
        "thickness": "k\u00c5",
        "frequency": "Hz"
      }
    }
  ],
  "logging": {
    "enabled": True,
    "directory": "logs",
    "rotation_enabled": True
  },
  "gui": {
    "theme": "dark",
    "gap_threshold_seconds": 60.0,
    "window": {
      "width": 1424,
      "height": 813,
      "x": 153,
      "y": 111,
      "maximized": False
    },
    "plots": [
      {
        "dock_id": "plot_2",
        "device_id": "VCU-0",
        "area": "right",
        "channels": [
          "ch1_pressure",
          "ch2_pressure",
          "ch3_pressure"
        ],
        "colours": [
          "#00bfff",
          "#ff6b6b",
          "#51cf66"
        ],
        "visibility": {
          "ch1_pressure": True,
          "ch2_pressure": True,
          "ch3_pressure": True
        },
        "history_seconds": 0.0,
        "y_log": False,
        "show_scatters": True
      }
    ],
    "device_panels": [
      {
        "device_id": "VCU-0",
        "dock_id": "device_VCU-0",
        "area": "left"
      },
      {
        "device_id": "Eurotherm-0",
        "dock_id": "device_Eurotherm-0",
        "area": "left"
      }
    ],
    "last_log_open_dir": ""
  }
}

# ------------------------------------------------------------------
# Core file I/O
# ------------------------------------------------------------------


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override dict into base dict. Returns a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load configuration from a JSON file.

    Returns DEFAULT_CONFIG if file not found.
    If the file exists, its contents are deep-merged into DEFAULT_CONFIG,
    so missing keys retain their default values.
    """
    if not os.path.exists(path) and os.path.exists(BACKUP_CONFIG_PATH):
        path = BACKUP_CONFIG_PATH
    elif not os.path.exists(path):
        return copy.deepcopy(DEFAULT_CONFIG)

    with open(path, "r", encoding="utf-8") as f:
        cfg: Dict[str, Any] = json.load(f)
    return _deep_merge(DEFAULT_CONFIG, cfg)


def save_config(config: Dict[str, Any], path: str = DEFAULT_CONFIG_PATH) -> None:
    """Save configuration to a JSON file."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# ------------------------------------------------------------------
# Device helpers
# ------------------------------------------------------------------


def get_device_configs(config: Dict[str, Any]) -> list:
    """Extract list of device configurations."""
    return config.get("devices", [])


def find_device_config(
    config: Dict[str, Any], device_id: str
) -> Optional[Dict[str, Any]]:
    """Return the device config dict for *device_id*, or None."""
    for dev in config.get("devices", []):
        if dev.get("device_id") == device_id:
            return dev
    return None


def update_device_config(
    config: Dict[str, Any],
    device_id: str,
    updates: Dict[str, Any],
) -> bool:
    """Merge *updates* into the config entry for *device_id*.

    Returns True if the device was found and updated.
    """
    dev = find_device_config(config, device_id)
    if dev is None:
        return False
    dev.update(updates)
    return True


# ------------------------------------------------------------------
# Window geometry persistence
# ------------------------------------------------------------------


def get_window_geometry(config: Dict[str, Any]) -> Tuple[int, int, int, int, bool]:
    """Return (x, y, width, height, maximized) from config."""
    win = config.get("gui", {}).get("window", {})
    return (
        win.get("x", 100),
        win.get("y", 100),
        win.get("width", 1280),
        win.get("height", 800),
        win.get("maximized", False),
    )


def set_window_geometry(
    config: Dict[str, Any],
    x: int,
    y: int,
    width: int,
    height: int,
    maximized: bool = False,
) -> None:
    """Store window geometry into the config dict."""
    gui = config.setdefault("gui", {})
    win = gui.setdefault("window", {})
    win["x"] = x
    win["y"] = y
    win["width"] = width
    win["height"] = height
    win["maximized"] = maximized


# ------------------------------------------------------------------
# Plot configuration persistence
# ------------------------------------------------------------------


def get_plot_configs(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the list of saved plot configurations."""
    return config.get("gui", {}).get("plots", [])


def set_plot_configs(
    config: Dict[str, Any], plots: List[Dict[str, Any]]
) -> None:
    """Replace the plot configuration list."""
    config.setdefault("gui", {})["plots"] = plots


def upsert_plot_config(
    config: Dict[str, Any],
    dock_id: str,
    device_id: str,
    title: str,
    area: str,
    channels: List[str],
    colours: List[str],
    history_seconds: float,
) -> None:
    """Insert or update a single plot entry identified by *dock_id*."""
    plots = config.setdefault("gui", {}).setdefault("plots", [])
    for entry in plots:
        if entry.get("dock_id") == dock_id:
            entry.update({
                "device_id": device_id,
                "title": title,
                "area": area,
                "channels": channels,
                "colours": colours,
                "history_seconds": history_seconds,
            })
            return
    plots.append({
        "dock_id": dock_id,
        "device_id": device_id,
        "title": title,
        "area": area,
        "channels": channels,
        "colours": colours,
        "history_seconds": history_seconds,
    })


# ------------------------------------------------------------------
# Device panel persistence
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Gap threshold
# ------------------------------------------------------------------


def get_gap_threshold(config: Dict[str, Any]) -> float:
    """Return the plot gap-detection threshold in seconds."""
    return float(config.get("gui", {}).get("gap_threshold_seconds", 60.0))


def get_device_panel_configs(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the list of saved device panel configurations."""
    return config.get("gui", {}).get("device_panels", [])
