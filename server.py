from __future__ import annotations

import atexit
import os
import threading
import time
from typing import Any, Dict, List, Tuple

from flask import Flask, Response, jsonify, render_template, request

try:
    import cv2
except Exception:  # pragma: no cover - optional dependency
    cv2 = None

try:
    import numpy as np
except Exception:  # pragma: no cover - optional dependency
    np = None

try:
    from picamera2 import Picamera2
except Exception:  # pragma: no cover - optional dependency
    Picamera2 = None

try:
    import kociemba
except Exception:  # pragma: no cover - optional dependency
    kociemba = None

try:
    import serial
except Exception:  # pragma: no cover - optional dependency
    serial = None

from camera_service import CameraManager
from config import (
    BASE_DIR,
    CAMERA_IDS,
    CAMERA_RECONNECT_INTERVAL,
    COLOR_NAMES,
    COLOR_PROTOTYPES_HSV,
    CUBE_STATE_FILE,
    LAST_SOLUTION_FILE,
    ROI_FILE,
    UART_BAUD,
    UART_PORT,
    UART_TIMEOUT,
)
from cube_service import build_face_state, cube_to_kociemba_input, default_cube_state
from roi_service import (
    build_default_roi_config,
    default_rois_for_camera,
    validate_camera_rois,
    validate_roi_config,
)
from uart_service import send_uart_command
from utils import clamp, deep_copy, load_json, save_json

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)

roi_lock = threading.Lock()
roi_config = validate_roi_config(load_json(ROI_FILE, build_default_roi_config()))
save_json(ROI_FILE, roi_config)

cube_state_lock = threading.Lock()
cube_state = load_json(CUBE_STATE_FILE, default_cube_state())
if not isinstance(cube_state, dict):
    cube_state = default_cube_state()
save_json(CUBE_STATE_FILE, cube_state)

camera_manager = CameraManager(
    camera_ids=CAMERA_IDS,
    cv2_module=cv2,
    np_module=np,
    picamera2_class=Picamera2,
    reconnect_interval=CAMERA_RECONNECT_INTERVAL,
)
atexit.register(camera_manager.close_all)


def frame_to_jpeg(frame) -> bytes | None:
    if frame is None or cv2 is None:
        return None
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return None
    return encoded.tobytes()


def classify_hsv(mean_hsv: Tuple[float, float, float]) -> Tuple[str, float]:
    h, s, v = mean_hsv

    if s < 35.0 and v > 120.0:
        return "W", 0.78

    distances: List[Tuple[float, str]] = []
    for color_code, prototype in COLOR_PROTOTYPES_HSV.items():
        p_h, p_s, p_v = prototype
        hue_dist = min(abs(h - p_h), 180.0 - abs(h - p_h)) / 90.0
        sat_dist = abs(s - p_s) / 255.0
        val_dist = abs(v - p_v) / 255.0
        score = 0.55 * hue_dist + 0.25 * sat_dist + 0.20 * val_dist
        distances.append((score, color_code))

    distances.sort(key=lambda item: item[0])
    best_score, best_color = distances[0]
    second_score = distances[1][0] if len(distances) > 1 else 1.0

    confidence = 1.0 - (best_score / (second_score + 1e-6))
    confidence = clamp(confidence, 0.05, 0.99)
    return best_color, confidence


def roi_to_pixels(roi: Dict[str, Any], frame_width: int, frame_height: int) -> Tuple[int, int, int, int]:
    x1 = int(clamp(float(roi["x"]), 0.0, 0.999) * frame_width)
    y1 = int(clamp(float(roi["y"]), 0.0, 0.999) * frame_height)
    x2 = int(clamp(float(roi["x"] + roi["w"]), 0.001, 1.0) * frame_width)
    y2 = int(clamp(float(roi["y"] + roi["h"]), 0.001, 1.0) * frame_height)

    x2 = max(x2, x1 + 1)
    y2 = max(y2, y1 + 1)
    return x1, y1, x2, y2


def detect_for_camera(camera_id: str, rois: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    frame = camera_manager.get_frame(camera_id)
    if frame is None or cv2 is None or np is None:
        return [
            {
                "id": roi["id"],
                "face": roi["face"],
                "index": roi["index"],
                "color": "?",
                "color_name": COLOR_NAMES["?"],
                "confidence": 0.0,
                "label": "?0%",
            }
            for roi in rois
        ]

    frame_h, frame_w = frame.shape[:2]
    results: List[Dict[str, Any]] = []

    for roi in rois:
        x1, y1, x2, y2 = roi_to_pixels(roi, frame_w, frame_h)
        crop = frame[y1:y2, x1:x2]

        if crop.size == 0:
            color_code = "?"
            confidence = 0.0
        else:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            mean_hsv = hsv.mean(axis=(0, 1))
            color_code, confidence = classify_hsv((float(mean_hsv[0]), float(mean_hsv[1]), float(mean_hsv[2])))

        confidence_pct = int(round(confidence * 100))
        results.append(
            {
                "id": roi["id"],
                "face": roi["face"],
                "index": roi["index"],
                "color": color_code,
                "color_name": COLOR_NAMES.get(color_code, "Unknown"),
                "confidence": round(confidence, 3),
                "label": f"{color_code}{confidence_pct}%",
            }
        )

    return results


def detect_all_cameras() -> Dict[str, List[Dict[str, Any]]]:
    with roi_lock:
        rois_snapshot = deep_copy(roi_config)

    output: Dict[str, List[Dict[str, Any]]] = {}
    for camera_id in CAMERA_IDS:
        output[camera_id] = detect_for_camera(camera_id, rois_snapshot[camera_id])
    return output


def capture_cube_state() -> Dict[str, Any]:
    detections = detect_all_cameras()
    faces, complete = build_face_state(detections)

    kociemba_input = None
    kociemba_error = None
    if complete:
        try:
            kociemba_input = cube_to_kociemba_input(faces)
        except Exception as exc:
            kociemba_error = str(exc)

    state = {
        "captured_at": time.time(),
        "faces": faces,
        "complete": complete,
        "kociemba_input": kociemba_input,
        "kociemba_error": kociemba_error,
        "detections": detections,
    }

    with cube_state_lock:
        global cube_state
        cube_state = state
        save_json(CUBE_STATE_FILE, cube_state)

    return state


def save_last_solution(solution_payload: Dict[str, Any]) -> None:
    save_json(LAST_SOLUTION_FILE, solution_payload)


def stream_generator(camera_id: str):
    while True:
        frame = camera_manager.get_frame(camera_id)
        jpeg = frame_to_jpeg(frame)

        if jpeg is None:
            time.sleep(0.05)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        )


@app.route("/")
def index():
    return render_template("index.html", camera_ids=CAMERA_IDS)


@app.route("/stream/<camera_id>")
def stream(camera_id: str):
    if camera_id not in CAMERA_IDS:
        return jsonify({"error": "Unknown camera id"}), 404

    return Response(
        stream_generator(camera_id),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/api/health")
def api_health():
    return jsonify(
        {
            "status": "ok",
            "camera_status": camera_manager.status(),
            "libraries": {
                "opencv": cv2 is not None,
                "numpy": np is not None,
                "picamera2": Picamera2 is not None,
                "kociemba": kociemba is not None,
                "pyserial": serial is not None,
            },
            "uart": {
                "port": UART_PORT,
                "baud": UART_BAUD,
            },
        }
    )


@app.route("/api/rois", methods=["GET", "POST"])
def api_rois():
    global roi_config

    if request.method == "GET":
        with roi_lock:
            snapshot = deep_copy(roi_config)
        return jsonify({"rois": snapshot})

    payload = request.get_json(silent=True) or {}

    with roi_lock:
        if isinstance(payload.get("camera_id"), (str, int)) and isinstance(payload.get("rois"), list):
            camera_id = str(payload["camera_id"])
            if camera_id not in CAMERA_IDS:
                return jsonify({"error": "Unknown camera id"}), 400
            roi_config[camera_id] = validate_camera_rois(camera_id, payload["rois"])
        else:
            posted_rois = payload.get("rois")
            if not isinstance(posted_rois, dict):
                return jsonify({"error": "Provide {'rois': {'0': [...], '1': [...]}}"}), 400

            for camera_id in CAMERA_IDS:
                if camera_id in posted_rois:
                    roi_config[camera_id] = validate_camera_rois(camera_id, posted_rois[camera_id])

        save_json(ROI_FILE, roi_config)
        snapshot = deep_copy(roi_config)

    return jsonify({"ok": True, "rois": snapshot})


@app.route("/api/rois/reset", methods=["POST"])
def api_rois_reset():
    global roi_config

    payload = request.get_json(silent=True) or {}
    selected_camera = payload.get("camera_id")

    with roi_lock:
        if selected_camera is None:
            roi_config = build_default_roi_config()
        else:
            camera_id = str(selected_camera)
            if camera_id not in CAMERA_IDS:
                return jsonify({"error": "Unknown camera id"}), 400
            roi_config[camera_id] = default_rois_for_camera(camera_id)

        save_json(ROI_FILE, roi_config)
        snapshot = deep_copy(roi_config)

    return jsonify({"ok": True, "rois": snapshot})


@app.route("/api/detect", methods=["GET", "POST"])
def api_detect():
    payload = request.get_json(silent=True) or {}
    requested_camera = payload.get("camera_id") or request.args.get("camera_id")

    if requested_camera is None:
        detections = detect_all_cameras()
    else:
        camera_id = str(requested_camera)
        if camera_id not in CAMERA_IDS:
            return jsonify({"error": "Unknown camera id"}), 400
        with roi_lock:
            rois = deep_copy(roi_config[camera_id])
        detections = {camera_id: detect_for_camera(camera_id, rois)}

    return jsonify({"timestamp": time.time(), "cameras": detections})


@app.route("/api/capture-state", methods=["POST"])
def api_capture_state():
    state = capture_cube_state()
    return jsonify(state)


@app.route("/api/cube-state", methods=["GET"])
def api_cube_state():
    with cube_state_lock:
        snapshot = deep_copy(cube_state)
    return jsonify(snapshot)


@app.route("/api/solve", methods=["POST"])
def api_solve():
    payload = request.get_json(silent=True) or {}
    capture_first = bool(payload.get("capture_first", False))
    send_uart = bool(payload.get("send_uart", False))

    if capture_first:
        state = capture_cube_state()
    else:
        with cube_state_lock:
            state = deep_copy(cube_state)

    if not state.get("complete"):
        return jsonify({"error": "Cube state is incomplete. Capture a full state first."}), 400

    kociemba_input = state.get("kociemba_input")
    if not kociemba_input:
        try:
            kociemba_input = cube_to_kociemba_input(state["faces"])
        except Exception as exc:
            return jsonify({"error": f"Failed to generate kociemba input: {exc}"}), 400

    if kociemba is None:
        return jsonify({"error": "kociemba package is not installed"}), 500

    try:
        solution = kociemba.solve(kociemba_input)
    except Exception as exc:
        return jsonify({"error": f"kociemba.solve failed: {exc}"}), 500

    uart_result = None
    if send_uart:
        try:
            uart_result = send_uart_command(
                solution,
                serial_module=serial,
                port=UART_PORT,
                baud=UART_BAUD,
                timeout=UART_TIMEOUT,
            )
        except Exception as exc:
            return jsonify({"error": f"UART send failed: {exc}", "solution": solution}), 500

    response = {
        "ok": True,
        "captured_at": state.get("captured_at"),
        "kociemba_input": kociemba_input,
        "solution": solution,
        "uart": uart_result,
    }
    save_last_solution(response)
    return jsonify(response)


@app.route("/api/uart/send", methods=["POST"])
def api_uart_send():
    payload = request.get_json(silent=True) or {}
    command = payload.get("command", "")

    try:
        result = send_uart_command(
            str(command),
            serial_module=serial,
            port=UART_PORT,
            baud=UART_BAUD,
            timeout=UART_TIMEOUT,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "uart": result})


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug, threaded=True)
