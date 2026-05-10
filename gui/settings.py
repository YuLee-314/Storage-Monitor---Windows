"""Persistent settings stored in %APPDATA%/StorageMonitor/config.json."""

import json
import os
from pathlib import Path


CONFIG_DIR = Path(os.environ.get("APPDATA", "")) / "StorageMonitor"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "language": "en",
}


def load() -> dict:
    """Load settings from disk, filling in defaults for missing keys."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    # Merge defaults for any missing keys
    for k, v in DEFAULTS.items():
        data.setdefault(k, v)
    return data


def save(data: dict) -> None:
    """Save settings to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
