from __future__ import annotations

from typing import Any, Dict, List

from config import CAMERA_FACE_MAP, CAMERA_IDS, FACE_ORDER
from utils import clamp


def default_rois_for_camera(camera_id: str) -> List[Dict[str, Any]]:
    faces = CAMERA_FACE_MAP[camera_id]
    rois: List[Dict[str, Any]] = []

    face_origins = [0.05, 0.37, 0.69]
    origin_y = 0.17
    face_block = 0.26
    cell = face_block / 3.0
    box_size = cell * 0.82

    for face_idx, face in enumerate(faces):
        origin_x = face_origins[face_idx]
        for row in range(3):
            for col in range(3):
                sticker_index = row * 3 + col
                x = origin_x + col * cell + (cell - box_size) / 2.0
                y = origin_y + row * cell + (cell - box_size) / 2.0
                rois.append(
                    {
                        "id": f"{face}{sticker_index}",
                        "face": face,
                        "index": sticker_index,
                        "x": round(x, 5),
                        "y": round(y, 5),
                        "w": round(box_size, 5),
                        "h": round(box_size, 5),
                    }
                )

    return rois


def build_default_roi_config() -> Dict[str, List[Dict[str, Any]]]:
    return {camera_id: default_rois_for_camera(camera_id) for camera_id in CAMERA_IDS}


def normalize_roi(raw: Dict[str, Any]) -> Dict[str, Any]:
    face = str(raw.get("face", "U")).upper()
    if face not in FACE_ORDER:
        face = "U"

    try:
        index = int(raw.get("index", 0))
    except Exception:
        index = 0
    index = int(clamp(float(index), 0.0, 8.0))

    x = clamp(float(raw.get("x", 0.10)), 0.0, 0.98)
    y = clamp(float(raw.get("y", 0.10)), 0.0, 0.98)
    w = clamp(float(raw.get("w", 0.08)), 0.02, 0.60)
    h = clamp(float(raw.get("h", 0.08)), 0.02, 0.60)

    if x + w > 1.0:
        x = 1.0 - w
    if y + h > 1.0:
        y = 1.0 - h

    roi_id = str(raw.get("id", f"{face}{index}"))

    return {
        "id": roi_id,
        "face": face,
        "index": index,
        "x": round(x, 5),
        "y": round(y, 5),
        "w": round(w, 5),
        "h": round(h, 5),
    }


def validate_camera_rois(camera_id: str, candidate: Any) -> List[Dict[str, Any]]:
    if not isinstance(candidate, list):
        return default_rois_for_camera(camera_id)

    normalized = [normalize_roi(item) for item in candidate if isinstance(item, dict)]
    if len(normalized) != 27:
        return default_rois_for_camera(camera_id)

    return normalized


def validate_roi_config(candidate: Any) -> Dict[str, List[Dict[str, Any]]]:
    defaults = build_default_roi_config()
    if not isinstance(candidate, dict):
        return defaults

    clean: Dict[str, List[Dict[str, Any]]] = {}
    for camera_id in CAMERA_IDS:
        clean[camera_id] = validate_camera_rois(camera_id, candidate.get(camera_id))

    return clean
