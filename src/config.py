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
    if not os.path.exists(path):
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


def get_device_configs(config: Dict[str, Any]) -> list:
    """Extract list of device configurations."""
    return config.get("devices", [])
