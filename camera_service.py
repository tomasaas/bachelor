from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Iterable


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

        if backend_pref in ("auto", "opencv") and self._cv2 is not None:
            try:
                cap = self._cv2.VideoCapture(self.camera_id)
                if cap is not None and cap.isOpened():
                    cap.set(self._cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    cap.set(self._cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    cap.set(self._cv2.CAP_PROP_FPS, self.fps)
                    self._opencv_cap = cap
                    self._source = "opencv"
                    self._error = ""
                    self._next_reconnect_at = 0.0
                    return
                if cap is not None:
                    cap.release()
            except Exception as exc:
                self._error = f"OpenCV camera unavailable: {exc}"

        if self._cv2 is None or self._np is None:
            self._error = "opencv-python and numpy are required for camera streaming"
        elif not self._error:
            self._error = "No camera backend available"

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
            self._error = "OpenCV read failed"
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
