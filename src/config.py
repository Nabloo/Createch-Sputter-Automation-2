"""Configuration loader and saver."""

import copy
import json
import os
from typing import Any, Dict

DEFAULT_CONFIG_PATH = "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "devices": [],
    "logging": {
        "enabled": True,
        "directory": "logs",
        "rotation_enabled": True,
    },
    "gui": {
        "theme": "dark",
        "plots": [],
    },
}


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load configuration from a JSON file. Returns deep copy of defaults if file not found."""
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as f:
        cfg: Dict[str, Any] = json.load(f)
    return cfg


def save_config(config: Dict[str, Any], path: str = DEFAULT_CONFIG_PATH) -> None:
    """Save configuration to a JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_device_configs(config: Dict[str, Any]) -> list:
    """Extract list of device configurations."""
    return config.get("devices", [])
