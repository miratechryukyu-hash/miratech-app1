let currentStream = null;
let useEnvironment = true;
let frameHeight = 520;
let platform = { isIOS: false, isAndroid: false, isIPad: false, isMobile: false };

function sendValue(value) {
  Streamlit.setComponentValue(value);
}

function showError(message) {
  const errorEl = document.getElementById("error");
  errorEl.hidden = false;
  errorEl.textContent = message;
}

function clearError() {
  const errorEl = document.getElementById("error");
  errorEl.hidden = true;
  errorEl.textContent = "";
}

function detectPlatform() {
  const ua = navigator.userAgent || "";
  const isIPad =
    /iPad/.test(ua) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isIOS = /iPhone|iPod/.test(ua) || isIPad;
  const isAndroid = /Android/.test(ua);
  return {
    isIOS,
    isAndroid,
    isIPad,
    isMobile: isIOS || isAndroid,
  };
}

function applyPlatformUI() {
  const container = document.getElementById("container");
  const hint = document.getElementById("hint");
  const platformHint = document.getElementById("platform-hint");
  const previewSection = document.getElementById("preview-section");

  container.classList.remove("ios-mode", "android-mode", "ipad-mode");

  if (platform.isIPad) {
    container.classList.add("ios-mode", "ipad-mode");
    platformHint.textContent =
      "iPad：「アウトカメラで撮影」を押すと、背面カメラが起動します（推奨）。";
    hint.textContent =
      "銘板がはっきり写るよう、明るい場所で撮影してください。";
    previewSection.removeAttribute("open");
  } else if (platform.isIOS) {
    container.classList.add("ios-mode");
    platformHint.textContent =
      "iPhone：「アウトカメラで撮影」を押すと、背面カメラが起動します（推奨）。";
    hint.textContent =
      "プレビューがインカメラになる場合は、上の青いボタンをご利用ください。";
    previewSection.removeAttribute("open");
  } else if (platform.isAndroid) {
    container.classList.add("android-mode");
    platformHint.textContent =
      "Android：「アウトカメラで撮影」で背面カメラが起動します（推奨）。";
    hint.textContent =
      "プレビューがインカメラの場合は「アウトカメラに切替」を押すか、上の青いボタンをご利用ください。";
    previewSection.setAttribute("open", "");
  } else {
    platformHint.textContent =
      "スマートフォン・タブレットでは「アウトカメラで撮影」が最も確実です。";
    hint.textContent =
      "PCの場合はプレビューから撮影できます。";
    previewSection.setAttribute("open", "");
  }
}

function stopStream() {
  if (currentStream) {
    currentStream.getTracks().forEach((track) => track.stop());
    currentStream = null;
  }
}

async function pickBackDeviceId() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const videoDevices = devices.filter((device) => device.kind === "videoinput");

  const backDevice = videoDevices.find((device) =>
    /back|rear|environment|アウト|背面|wide|trifocal|dual|camera 2|カメラ 2/i.test(
      device.label
    )
  );
  if (backDevice && backDevice.deviceId) {
    return backDevice.deviceId;
  }

  const frontDevice = videoDevices.find((device) =>
    /front|user|facetime|イン|前面|selfie|camera 1|カメラ 1/i.test(device.label)
  );

  if (videoDevices.length > 1) {
    const nonFront = videoDevices.find(
      (device) => !frontDevice || device.deviceId !== frontDevice.deviceId
    );
    if (nonFront) {
      return nonFront.deviceId;
    }
    return videoDevices[videoDevices.length - 1].deviceId;
  }
  return null;
}

async function buildConstraints() {
  if (!useEnvironment) {
    return { video: { facingMode: { ideal: "user" } }, audio: false };
  }

  const backDeviceId = await pickBackDeviceId();
  if (backDeviceId) {
    return {
      video: {
        deviceId: { ideal: backDeviceId },
        facingMode: { ideal: "environment" },
      },
      audio: false,
    };
  }

  return { video: { facingMode: { ideal: "environment" } }, audio: false };
}

async function startCamera() {
  const video = document.getElementById("video");
  stopStream();
  clearError();

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showError("プレビュー非対応のブラウザです。「アウトカメラで撮影」をご利用ください。");
    return;
  }

  try {
    const probe = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: useEnvironment ? "environment" : "user" } },
      audio: false,
    });
    probe.getTracks().forEach((track) => track.stop());
  } catch (err) {
    // continue with other constraint attempts
  }

  const attempts = [
    await buildConstraints(),
    { video: { facingMode: { ideal: "environment" } }, audio: false },
    { video: { facingMode: "environment" }, audio: false },
  ];

  if (!platform.isIOS) {
    attempts.push({ video: { facingMode: { exact: "environment" } }, audio: false });
  }

  for (const constraints of attempts) {
    try {
      currentStream = await navigator.mediaDevices.getUserMedia(constraints);
      video.srcObject = currentStream;
      await video.play();
      return;
    } catch (err) {
      // try next constraint set
    }
  }

  showError("プレビューを起動できませんでした。「アウトカメラで撮影」ボタンをご利用ください。");
}

function takePicture() {
  const video = document.getElementById("video");
  const canvas = document.getElementById("canvas");

  if (!video.srcObject) {
    showError("カメラが準備できていません。「アウトカメラで撮影」をお試しください。");
    return;
  }

  const track = video.srcObject.getVideoTracks()[0];
  const settings = track ? track.getSettings() : {};
  const width = settings.width || video.videoWidth || 1280;
  const height = settings.height || video.videoHeight || 720;

  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage(video, 0, 0, width, height);
  sendValue(canvas.toDataURL("image/jpeg", 0.92));
}

function readFileAsDataUrl(file) {
  const reader = new FileReader();
  reader.onload = () => {
    clearError();
    sendValue(reader.result);
  };
  reader.onerror = () => showError("画像の読み込みに失敗しました。もう一度お試しください。");
  reader.readAsDataURL(file);
}

function setupCamera() {
  platform = detectPlatform();
  applyPlatformUI();

  const video = document.getElementById("video");
  const captureBtn = document.getElementById("capture-btn");
  const flipBtn = document.getElementById("flip-btn");
  const fileInputBack = document.getElementById("file-input-back");
  const previewSection = document.getElementById("preview-section");

  fileInputBack.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) {
      readFileAsDataUrl(file);
    }
    event.target.value = "";
  });

  captureBtn.addEventListener("click", takePicture);
  video.addEventListener("click", takePicture);
  flipBtn.addEventListener("click", async () => {
    useEnvironment = true;
    await startCamera();
  });

  previewSection.addEventListener("toggle", () => {
    if (previewSection.open) {
      useEnvironment = !platform.isIOS;
      startCamera();
    } else {
      stopStream();
    }
  });

  if (!platform.isIOS && !platform.isIPad) {
    startCamera();
  }
}

function onRender(event) {
  const { height } = event.detail.args;
  frameHeight = height || 520;
  Streamlit.setFrameHeight(frameHeight);

  if (!window.cameraInitialized) {
    window.cameraInitialized = true;
    setupCamera();
  }
}

Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, onRender);
Streamlit.setComponentReady();
Streamlit.setFrameHeight(520);
