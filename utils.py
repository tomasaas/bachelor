from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deep_copy(default)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return deep_copy(default)


def save_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
