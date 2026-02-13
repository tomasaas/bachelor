from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Iterable, List


class CameraStream:
    def __init__(
        self,
        camera_id: int,
        cv2_module,
        np_module,
        picamera2_class,
        reconnect_interval: float,
        width: int = 960,
        height: int = 720,
        fps: int = 25,
    ) -> None:
        self.camera_id = camera_id
        self.width = width
        self.height = height
        self.fps = fps

        self._cv2 = cv2_module
        self._np = np_module
        self._picamera2_class = picamera2_class

        self._lock = threading.Lock()
        self._running = True
        self._latest_frame = None
        self._source = "none"
        self._error = "Initializing"

        self._opencv_cap = None
        self._active_opencv_device = str(camera_id)
        self._picamera = None
        self._reconnect_interval = max(reconnect_interval, 0.1)
        self._next_reconnect_at = 0.0

        self._open_source()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    @property
    def status(self) -> Dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "source": self._source,
            "opencv_device": self._active_opencv_device,
            "error": self._error,
        }

    def _build_placeholder(self, message: str):
        if self._np is None or self._cv2 is None:
            return None

        frame = self._np.full((self.height, self.width, 3), (233, 239, 242), dtype=self._np.uint8)
        self._cv2.putText(
            frame,
            f"Camera {self.camera_id}",
            (30, 80),
            self._cv2.FONT_HERSHEY_SIMPLEX,
            1.3,
            (36, 66, 81),
            3,
            self._cv2.LINE_AA,
        )
        self._cv2.putText(
            frame,
            message[:80],
            (30, 140),
            self._cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (36, 66, 81),
            2,
            self._cv2.LINE_AA,
        )
        self._cv2.rectangle(frame, (20, 20), (self.width - 20, self.height - 20), (68, 124, 145), 3)
        return frame

    def _close_source(self) -> None:
        if self._opencv_cap is not None:
            try:
                self._opencv_cap.release()
            except Exception:
                pass
        self._opencv_cap = None

        if self._picamera is not None:
            try:
                self._picamera.stop()
            except Exception:
                pass
            try:
                self._picamera.close()
            except Exception:
                pass
        self._picamera = None
        self._source = "none"

    def _open_source(self) -> None:
        now = time.time()
        if now < self._next_reconnect_at:
            return
        self._next_reconnect_at = now + self._reconnect_interval

        backend_pref = os.getenv(f"CAMERA_{self.camera_id}_BACKEND", os.getenv("CAMERA_BACKEND", "auto")).lower()
        self._close_source()

        if backend_pref in ("auto", "opencv") and self._cv2 is not None:
            open_errors: List[str] = []
            for device in self._opencv_candidate_devices():
                try:
                    cap = self._open_opencv_capture(device)
                    if cap is None:
                        open_errors.append(f"device {device}: not opened")
                        continue

                    self._opencv_cap = cap
                    self._active_opencv_device = str(device)
                    self._source = "opencv"
                    self._error = ""
                    self._next_reconnect_at = 0.0
                    return
                except Exception as exc:
                    open_errors.append(f"device {device}: {exc}")
            self._error = f"OpenCV camera unavailable: {'; '.join(open_errors)[:220]}"

        if backend_pref in ("auto", "picamera2") and self._picamera2_class is not None and self._cv2 is not None:
            try:
                camera = self._picamera2_class(self.camera_id)
                config = camera.create_video_configuration(
                    main={"size": (self.width, self.height), "format": "RGB888"}
                )
                camera.configure(config)
                camera.start()
                self._picamera = camera
                self._source = "picamera2"
                self._error = ""
                self._next_reconnect_at = 0.0
                return
            except Exception as exc:
                self._error = f"Picamera2 unavailable: {exc}"

        if self._cv2 is None or self._np is None:
            self._error = "opencv-python and numpy are required for camera streaming"
        elif not self._error:
            self._error = "No camera backend available"

    def _parse_device_token(self, token: str):
        token = token.strip()
        if not token:
            return None
        if token.startswith("/dev/video"):
            return token
        try:
            return int(token)
        except Exception:
            return None

    def _opencv_candidate_devices(self) -> List[str | int]:
        values: List[str | int] = []

        path_override = os.getenv(f"CAMERA_{self.camera_id}_DEVICE_PATH", "").strip()
        if path_override:
            values.append(path_override)

        single_override = os.getenv(f"CAMERA_{self.camera_id}_DEVICE", "").strip()
        parsed_single = self._parse_device_token(single_override)
        if parsed_single is not None:
            values.append(parsed_single)

        values.append(f"/dev/video{self.camera_id}")
        values.append(self.camera_id)

        per_camera_fallback = os.getenv(f"CAMERA_{self.camera_id}_FALLBACKS", "").strip()
        if per_camera_fallback:
            for part in per_camera_fallback.split(","):
                parsed = self._parse_device_token(part)
                if parsed is not None:
                    values.append(parsed)

        global_probe = os.getenv("CAMERA_PROBE_INDICES", "").strip()
        if global_probe:
            for part in global_probe.split(","):
                parsed = self._parse_device_token(part)
                if parsed is not None:
                    values.append(parsed)

        deduped: List[str | int] = []
        seen: set[str] = set()
        for value in values:
            key = str(value)
            if key in seen:
                continue
            if isinstance(value, int) and value < 0:
                continue
            seen.add(key)
            deduped.append(value)
        return deduped

    def _warmup_capture(self, cap, tries: int = 20) -> bool:
        for _ in range(tries):
            ok, frame = cap.read()
            if ok and frame is not None and getattr(frame, "size", 0) > 0:
                return True
            time.sleep(0.03)
        return False

    def _apply_capture_profile(self, cap, fourcc: str | None) -> bool:
        if fourcc and hasattr(self._cv2, "VideoWriter_fourcc"):
            cap.set(self._cv2.CAP_PROP_FOURCC, self._cv2.VideoWriter_fourcc(*fourcc))
        cap.set(self._cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(self._cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(self._cv2.CAP_PROP_FPS, self.fps)
        if hasattr(self._cv2, "CAP_PROP_BUFFERSIZE"):
            cap.set(self._cv2.CAP_PROP_BUFFERSIZE, 1)
        return self._warmup_capture(cap)

    def _open_opencv_capture(self, device):
        backend_ids: List[int | None] = [None]
        if hasattr(self._cv2, "CAP_V4L2"):
            backend_ids.insert(0, self._cv2.CAP_V4L2)

        for backend_id in backend_ids:
            cap = None
            try:
                if backend_id is None:
                    cap = self._cv2.VideoCapture(device)
                else:
                    cap = self._cv2.VideoCapture(device, backend_id)

                if cap is None or not cap.isOpened():
                    if cap is not None:
                        cap.release()
                    continue

                if self._apply_capture_profile(cap, "MJPG"):
                    return cap

                if self._apply_capture_profile(cap, "YUYV"):
                    return cap

                if self._apply_capture_profile(cap, None):
                    return cap

                cap.release()
            except Exception:
                if cap is not None:
                    cap.release()
                continue

        return None

    def _grab_frame(self):
        if self._source == "picamera2" and self._picamera is not None and self._cv2 is not None:
            try:
                rgb = self._picamera.capture_array("main")
                return self._cv2.cvtColor(rgb, self._cv2.COLOR_RGB2BGR)
            except Exception as exc:
                self._error = f"Picamera2 read failed: {exc}"
                self._open_source()
                return None

        if self._source == "opencv" and self._opencv_cap is not None:
            ok, frame = self._opencv_cap.read()
            if ok:
                return frame
            self._error = f"OpenCV read failed (device {self._active_opencv_device})"
            self._open_source()
            return None

        self._open_source()
        return None

    def _reader_loop(self) -> None:
        frame_delay = 1.0 / max(self.fps, 1)

        while self._running:
            frame = self._grab_frame()
            if frame is None:
                frame = self._build_placeholder(self._error or "Waiting for camera")
                time.sleep(0.1)

            with self._lock:
                self._latest_frame = frame

            time.sleep(frame_delay)

    def get_frame(self):
        with self._lock:
            if self._latest_frame is None:
                return self._build_placeholder("No frame yet")
            if self._np is not None:
                return self._latest_frame.copy()
            return self._latest_frame

    def close(self) -> None:
        self._running = False
        if hasattr(self, "_thread") and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._close_source()


class CameraManager:
    def __init__(
        self,
        camera_ids: Iterable[str],
        cv2_module,
        np_module,
        picamera2_class,
        reconnect_interval: float,
    ) -> None:
        self._streams: Dict[str, CameraStream] = {
            camera_id: CameraStream(
                int(camera_id),
                cv2_module=cv2_module,
                np_module=np_module,
                picamera2_class=picamera2_class,
                reconnect_interval=reconnect_interval,
            )
            for camera_id in camera_ids
        }

    def get_stream(self, camera_id: str) -> CameraStream:
        return self._streams[camera_id]

    def get_frame(self, camera_id: str):
        return self._streams[camera_id].get_frame()

    def status(self) -> Dict[str, Dict[str, Any]]:
        return {camera_id: stream.status for camera_id, stream in self._streams.items()}

    def close_all(self) -> None:
        for stream in self._streams.values():
            stream.close()
