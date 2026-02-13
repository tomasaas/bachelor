from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

ROI_FILE = DATA_DIR / "roi_config.json"
CUBE_STATE_FILE = DATA_DIR / "cube_state.json"
LAST_SOLUTION_FILE = DATA_DIR / "last_solution.json"

CAMERA_IDS = ("0", "1")
FACE_ORDER = ["U", "R", "F", "D", "L", "B"]
CAMERA_FACE_MAP = {
    "0": ["U", "F", "R"],
    "1": ["D", "B", "L"],
}

COLOR_PROTOTYPES_HSV: Dict[str, Tuple[float, float, float]] = {
    "W": (0.0, 15.0, 235.0),
    "Y": (30.0, 220.0, 220.0),
    "R": (2.0, 230.0, 210.0),
    "O": (17.0, 235.0, 230.0),
    "B": (108.0, 230.0, 200.0),
    "G": (65.0, 225.0, 180.0),
}

COLOR_NAMES = {
    "W": "White",
    "Y": "Yellow",
    "R": "Red",
    "O": "Orange",
    "B": "Blue",
    "G": "Green",
    "?": "Unknown",
}

UART_PORT = os.getenv("UART_PORT", "/dev/ttyAMA0")
UART_BAUD = int(os.getenv("UART_BAUD", "115200"))
UART_TIMEOUT = float(os.getenv("UART_TIMEOUT", "1.0"))
CAMERA_RECONNECT_INTERVAL = float(os.getenv("CAMERA_RECONNECT_INTERVAL", "2.0"))
