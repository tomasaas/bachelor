from __future__ import annotations

from typing import Any, Dict, List, Tuple

from config import COLOR_PROTOTYPES_HSV, FACE_ORDER


def default_cube_state() -> Dict[str, Any]:
    return {
        "captured_at": None,
        "complete": False,
        "kociemba_input": None,
        "faces": {face: ["?"] * 9 for face in FACE_ORDER},
        "detections": {"0": [], "1": []},
    }


def build_face_state(detections: Dict[str, List[Dict[str, Any]]]) -> Tuple[Dict[str, List[str]], bool]:
    faces: Dict[str, List[str]] = {face: ["?"] * 9 for face in FACE_ORDER}

    for camera_results in detections.values():
        for sticker in camera_results:
            face = sticker["face"]
            index = int(sticker["index"])
            if face in faces and 0 <= index <= 8:
                faces[face][index] = sticker["color"]

    complete = all(
        len(face_values) == 9 and all(color in COLOR_PROTOTYPES_HSV for color in face_values)
        for face_values in faces.values()
    )
    return faces, complete


def cube_to_kociemba_input(face_state: Dict[str, List[str]]) -> str:
    for face in FACE_ORDER:
        if face not in face_state or len(face_state[face]) != 9:
            raise ValueError(f"Face {face} is missing or incomplete")

    centers: Dict[str, str] = {}
    for face in FACE_ORDER:
        center_color = face_state[face][4]
        if center_color not in COLOR_PROTOTYPES_HSV:
            raise ValueError(f"Center sticker of face {face} is unknown ({center_color})")
        centers[face] = center_color

    if len(set(centers.values())) != 6:
        raise ValueError("Center colors are not unique; cube orientation cannot be inferred")

    color_to_face = {color: face for face, color in centers.items()}

    result: List[str] = []
    for face in FACE_ORDER:
        for color in face_state[face]:
            mapped = color_to_face.get(color)
            if mapped is None:
                raise ValueError(f"Color {color} has no matching center face")
            result.append(mapped)

    return "".join(result)
