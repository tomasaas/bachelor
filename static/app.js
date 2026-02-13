const CAMERA_IDS = ["0", "1"];
const FACE_ORDER = ["U", "R", "F", "D", "L", "B"];
const ROI_FACE_COLOR_LABEL = {
  U: "W",
  F: "B",
  R: "G",
  D: "O",
  B: "Y",
  L: "R",
};

const overlays = {
  "0": document.getElementById("overlay-0"),
  "1": document.getElementById("overlay-1"),
};

const statusBox = document.getElementById("status-box");
const cubeStateView = document.getElementById("cube-state-view");
const solutionView = document.getElementById("solution-view");
const activeCameraSelect = document.getElementById("active-camera");

let activeCamera = activeCameraSelect.value;
let roisByCamera = {};
let predictionsByCamera = { "0": {}, "1": {} };

let dragState = null;

function setStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.style.color = isError ? "#9b2f0a" : "#3f6373";
}

async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const msg = data.error || `Request failed with status ${response.status}`;
    throw new Error(msg);
  }
  return data;
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(value, high));
}

function applyRoiElementStyle(element, roi) {
  element.style.left = `${roi.x * 100}%`;
  element.style.top = `${roi.y * 100}%`;
  element.style.width = `${roi.w * 100}%`;
  element.style.height = `${roi.h * 100}%`;
}

function predictionText(cameraId, roiId) {
  const prediction = predictionsByCamera[cameraId]?.[roiId];
  if (!prediction) {
    return "--";
  }
  return prediction.label;
}

function roiFaceLabel(faceCode) {
  return ROI_FACE_COLOR_LABEL[faceCode] || faceCode;
}

function renderOverlay(cameraId) {
  const overlay = overlays[cameraId];
  overlay.innerHTML = "";

  const isLocked = cameraId !== activeCamera;
  overlay.classList.toggle("locked", isLocked);

  const rois = roisByCamera[cameraId] || [];
  rois.forEach((roi, index) => {
    const box = document.createElement("div");
    box.className = "roi-box";
    box.dataset.cameraId = cameraId;
    box.dataset.roiIndex = String(index);
    applyRoiElementStyle(box, roi);

    const faceLabel = document.createElement("div");
    faceLabel.className = "roi-face";
    faceLabel.textContent = `${roiFaceLabel(roi.face)}${roi.index + 1}`;

    const detectLabel = document.createElement("div");
    detectLabel.className = "roi-detect";
    detectLabel.textContent = predictionText(cameraId, roi.id);

    const handle = document.createElement("div");
    handle.className = "roi-handle";

    box.appendChild(faceLabel);
    box.appendChild(detectLabel);
    box.appendChild(handle);
    overlay.appendChild(box);

    if (!isLocked) {
      box.addEventListener("pointerdown", (event) => {
        const mode = event.target.classList.contains("roi-handle") ? "resize" : "move";
        startDrag(event, cameraId, index, mode, box, overlay);
      });
    }
  });
}

function renderAllOverlays() {
  CAMERA_IDS.forEach((cameraId) => renderOverlay(cameraId));
}

function startDrag(event, cameraId, roiIndex, mode, boxElement, overlayElement) {
  event.preventDefault();

  const roi = roisByCamera[cameraId][roiIndex];
  const bounds = overlayElement.getBoundingClientRect();
  dragState = {
    cameraId,
    roiIndex,
    mode,
    startX: event.clientX,
    startY: event.clientY,
    startRoi: { ...roi },
    bounds,
    boxElement,
  };

  window.addEventListener("pointermove", onDragMove);
  window.addEventListener("pointerup", endDrag);
}

function onDragMove(event) {
  if (!dragState) {
    return;
  }

  const roi = roisByCamera[dragState.cameraId][dragState.roiIndex];
  const dx = (event.clientX - dragState.startX) / dragState.bounds.width;
  const dy = (event.clientY - dragState.startY) / dragState.bounds.height;

  if (dragState.mode === "move") {
    roi.x = clamp(dragState.startRoi.x + dx, 0, 1 - roi.w);
    roi.y = clamp(dragState.startRoi.y + dy, 0, 1 - roi.h);
  } else {
    roi.w = clamp(dragState.startRoi.w + dx, 0.02, 1 - roi.x);
    roi.h = clamp(dragState.startRoi.h + dy, 0.02, 1 - roi.y);
  }

  applyRoiElementStyle(dragState.boxElement, roi);
}

function endDrag() {
  if (!dragState) {
    return;
  }

  dragState = null;
  window.removeEventListener("pointermove", onDragMove);
  window.removeEventListener("pointerup", endDrag);
}

function normalizePredictions(apiData) {
  const normalized = { "0": {}, "1": {} };
  Object.entries(apiData.cameras || {}).forEach(([cameraId, items]) => {
    normalized[cameraId] = {};
    items.forEach((item) => {
      normalized[cameraId][item.id] = item;
    });
  });
  return normalized;
}

function formatFaceState(faces) {
  return FACE_ORDER.map((face) => `${face}: ${(faces[face] || []).join(" ")}`).join("\n");
}

async function loadRois() {
  const payload = await apiRequest("/api/rois");
  roisByCamera = payload.rois;
  renderAllOverlays();
  setStatus("ROI layout loaded.");
}

async function detectColors() {
  setStatus("Detecting colors from both video feeds...");
  const payload = await apiRequest("/api/detect", { method: "POST", body: "{}" });
  predictionsByCamera = normalizePredictions(payload);
  renderAllOverlays();
  setStatus("Detection updated. ROI labels now show color and certainty.");
}

async function captureCubeState() {
  setStatus("Capturing cube state from current ROI detections...");
  const payload = await apiRequest("/api/capture-state", { method: "POST", body: "{}" });
  cubeStateView.textContent = `${formatFaceState(payload.faces)}\n\ncomplete: ${payload.complete}\nkociemba_input: ${payload.kociemba_input || "n/a"}`;

  predictionsByCamera = normalizePredictions({ cameras: payload.detections || {} });
  renderAllOverlays();
  setStatus("Cube state captured and saved on-device.");
}

async function saveRois() {
  setStatus("Saving ROI layout to disk...");
  const body = JSON.stringify({ rois: roisByCamera });
  await apiRequest("/api/rois", { method: "POST", body });
  setStatus("ROI layout saved to backend/data/roi_config.json.");
}

async function resetRois() {
  setStatus("Resetting ROIs to default 27-box layouts per camera...");
  const body = JSON.stringify({ camera_id: activeCamera });
  const payload = await apiRequest("/api/rois/reset", { method: "POST", body });
  roisByCamera = payload.rois;
  predictionsByCamera = { "0": {}, "1": {} };
  renderAllOverlays();
  setStatus(`Camera ${activeCamera} ROIs reset to defaults.`);
}

async function solveCube() {
  setStatus("Running kociemba solver...");

  const captureFirst = document.getElementById("capture-first").checked;
  const sendUart = document.getElementById("send-uart").checked;
  const body = JSON.stringify({ capture_first: captureFirst, send_uart: sendUart });

  const payload = await apiRequest("/api/solve", { method: "POST", body });

  const lines = [
    `kociemba_input: ${payload.kociemba_input}`,
    `solution: ${payload.solution}`,
  ];

  if (payload.uart) {
    lines.push(`uart_port: ${payload.uart.port}`);
    lines.push(`uart_response: ${payload.uart.response || "<none>"}`);
  }

  solutionView.textContent = lines.join("\n");
  setStatus("Solve completed.");
}

function wireUi() {
  document.getElementById("detect-btn").addEventListener("click", async () => {
    try {
      await detectColors();
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  document.getElementById("capture-btn").addEventListener("click", async () => {
    try {
      await captureCubeState();
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  document.getElementById("save-rois-btn").addEventListener("click", async () => {
    try {
      await saveRois();
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  document.getElementById("reset-rois-btn").addEventListener("click", async () => {
    try {
      await resetRois();
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  document.getElementById("solve-btn").addEventListener("click", async () => {
    try {
      await solveCube();
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  activeCameraSelect.addEventListener("change", () => {
    activeCamera = activeCameraSelect.value;
    renderAllOverlays();
    setStatus(`Camera ${activeCamera} is now editable.`);
  });
}

async function bootstrap() {
  wireUi();
  try {
    await loadRois();
    await detectColors();
  } catch (error) {
    setStatus(error.message, true);
  }
}

bootstrap();
