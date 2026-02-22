// Remote desktop client logic.
(function () {
  "use strict";

  var cfg = window.RemoteDesktopConfig || {};
  if (!cfg.streamUrl) {
    return;
  }

  var statusEl = document.getElementById("rd-status");
  var screen = document.getElementById("rd-screen");
  var screenWrapper = document.getElementById("rd-screen-wrapper");
  var canvas = document.getElementById("rd-canvas");
  var canvasCtx = canvas && canvas.getContext ? canvas.getContext("2d") : null;
  var overlay = document.getElementById("rd-overlay");
  var monitorSelect = document.getElementById("rd-monitor");
  var qualitySelect = document.getElementById("rd-quality");
  var fpsSelect = document.getElementById("rd-fps");
  var scaleSelect = document.getElementById("rd-scale");
  var reloadBtn = document.getElementById("rd-reload");
  var fullscreenBtn = document.getElementById("rd-fullscreen-toggle");
  var sessionToggle = document.getElementById("rd-session-toggle");
  var controlBtn = document.getElementById("rd-control-toggle");
  var keyboardBtn = document.getElementById("rd-keyboard-toggle");
  var clearFocusBtn = document.getElementById("rd-clear-focus");
  var clipboardText = document.getElementById("rd-clipboard-text");
  var clipboardGetBtn = document.getElementById("rd-clipboard-get");
  var clipboardSendBtn = document.getElementById("rd-clipboard-send");
  var clipboardLocalBtn = document.getElementById("rd-clipboard-local");
  var textInput = document.getElementById("rd-text-input");
  var textSendBtn = document.getElementById("rd-text-send");
  var fileInput = document.getElementById("rd-file-input");
  var filesRefreshBtn = document.getElementById("rd-files-refresh");
  var filesContainer = document.getElementById("rd-files");
  var errorBox = document.getElementById("rd-errors");
  var errorLive = document.getElementById("rd-errors-live");
  var captureBtn = document.getElementById("rd-capture");
  var modeInputs = document.querySelectorAll("input[name='rd-render-mode']");

  var frameTimer = null;
  var statusTimer = null;
  var frameAbort = null;
  var frameInFlight = false;
  var renderMode = "mjpeg";
  var streamCanvasMode = false;
  var pointerActiveId = null;
  var mjpegWatchdog = null;
  var mjpegLoaded = false;
  var mjpegProbe = null;
  var mjpegTransport = "img";
  var mjpegFetchController = null;
  var frameStartupWatchdog = null;
  var frameStartupLoaded = false;
  var streamRunId = 0;
  var modeStorageKey = "rd_render_mode";
  var MOVE_SEND_MAX_FPS = 30;

  var state = {
    sessionActive: false,
    monitors: [],
    monitor: null,
    sharePath: "",
    control: false,
    keyboard: false,
    screenError: "",
    lastMove: 0,
    pendingMove: null,
    moveScheduled: false,
    lastGestureAt: 0,
    streamAttempt: "",
    streamRunId: 0,
    frameCount: 0
  };

  var KEY_MAP = {
    ControlLeft: "ctrl",
    ControlRight: "ctrl",
    ShiftLeft: "shift",
    ShiftRight: "shift",
    AltLeft: "alt",
    AltRight: "alt",
    MetaLeft: "win",
    MetaRight: "win",
    Enter: "enter",
    Tab: "tab",
    Escape: "esc",
    Backspace: "backspace",
    Space: "space",
    Delete: "delete",
    Insert: "insert",
    Home: "home",
    End: "end",
    PageUp: "pageup",
    PageDown: "pagedown",
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
    PrintScreen: "printscreen",
    Pause: "pause",
    CapsLock: "capslock",
    NumLock: "numlock",
    ScrollLock: "scrolllock",
    ContextMenu: "apps",
    F1: "f1",
    F2: "f2",
    F3: "f3",
    F4: "f4",
    F5: "f5",
    F6: "f6",
    F7: "f7",
    F8: "f8",
    F9: "f9",
    F10: "f10",
    F11: "f11",
    F12: "f12"
  };

  function setStatus(text) {
    if (!statusEl) return;
    statusEl.textContent = text;
  }

  function toLiveErrorText(message) {
    var text = String(message || "").replace(/\s+/g, " ").trim();
    if (!text) {
      return "";
    }
    return "Ошибка удаленного рабочего стола. " + text;
  }

  function showError(message) {
    var liveText = toLiveErrorText(message);
    if (errorLive) {
      errorLive.textContent = liveText;
    }
    if (!errorBox) return;
    if (message) {
      errorBox.textContent = message;
      errorBox.hidden = false;
      errorBox.setAttribute("aria-hidden", "false");
      errorBox.setAttribute("aria-label", liveText || "Ошибка удаленного рабочего стола");
    } else {
      errorBox.textContent = "";
      errorBox.hidden = true;
      errorBox.setAttribute("aria-hidden", "true");
    }
  }

  function markUserGesture() {
    state.lastGestureAt = Date.now();
  }

  function hasRecentGesture() {
    return Date.now() - state.lastGestureAt < 2500;
  }

  function nextStreamRunId() {
    streamRunId += 1;
    state.streamRunId = streamRunId;
    return streamRunId;
  }

  function isCurrentStreamRun(runId) {
    return typeof runId === "number" && runId === state.streamRunId;
  }

  function detectBrowserName() {
    var ua = String((window.navigator && window.navigator.userAgent) || "");
    if (ua.indexOf("Firefox/") !== -1) return "firefox";
    if (ua.indexOf("Edg/") !== -1) return "edge";
    if (ua.indexOf("Chrome/") !== -1) return "chrome";
    if (ua.indexOf("Safari/") !== -1 && ua.indexOf("Version/") !== -1) return "safari";
    return "unknown";
  }

  function collectRuntimeDetails() {
    var nav = window.navigator || {};
    var conn = nav.connection || nav.mozConnection || nav.webkitConnection || null;
    return {
      browser: detectBrowserName(),
      ua: String(nav.userAgent || "").slice(0, 220),
      platform: String(nav.platform || ""),
      language: String(nav.language || ""),
      protocol: String((window.location && window.location.protocol) || ""),
      secureContext: !!window.isSecureContext,
      visibility: String(document.visibilityState || ""),
      online: nav.onLine !== false,
      effectiveType: conn && conn.effectiveType ? String(conn.effectiveType) : "",
      downlink: conn && typeof conn.downlink === "number" ? conn.downlink : ""
    };
  }

  function clearFrameStartupWatchdog() {
    if (frameStartupWatchdog) {
      clearTimeout(frameStartupWatchdog);
      frameStartupWatchdog = null;
    }
  }

  function sanitizeRenderMode(value) {
    if (value === "frames" || value === "mjpeg") {
      return value;
    }
    return null;
  }

  function readStoredRenderMode() {
    try {
      return sanitizeRenderMode(localStorage.getItem(modeStorageKey));
    } catch (err) {
      return null;
    }
  }

  function readSelectedRenderMode() {
    if (!modeInputs || !modeInputs.length) return null;
    var selected = null;
    Array.prototype.forEach.call(modeInputs, function (input) {
      if (input.checked) {
        selected = sanitizeRenderMode(input.value);
      }
    });
    return selected;
  }

  function setRenderMode(mode, persist) {
    renderMode = mode;
    if (modeInputs && modeInputs.length) {
      Array.prototype.forEach.call(modeInputs, function (input) {
        input.checked = input.value === mode;
      });
    }
    if (persist !== false) {
      try {
        localStorage.setItem(modeStorageKey, mode);
      } catch (err) {
      }
    }
    if (!screenWrapper) return;
    screenWrapper.classList.toggle("canvas-mode", mode === "frames");
    screenWrapper.classList.toggle("image-mode", mode === "mjpeg");
    if (mode !== "mjpeg") {
      setStreamCanvasMode(false);
    }
  }

  function setStreamCanvasMode(active) {
    streamCanvasMode = !!active;
    if (!screenWrapper) return;
    screenWrapper.classList.toggle("stream-canvas-mode", streamCanvasMode);
  }

  function activeSurface() {
    if ((renderMode === "frames" || streamCanvasMode) && canvas) {
      return canvas;
    }
    return screen;
  }

  function clearCanvas() {
    if (!canvasCtx || !canvas) return;
    canvasCtx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function updateOverlay(message) {
    if (!overlay) return;
    var text = "";
    if (message) {
      text = message;
    } else if (state.screenError) {
      text = state.screenError;
    } else if (!state.sessionActive) {
      text = "Сеанс выключен. Нажмите кнопку «Включить удаленный рабочий стол».";
    } else if (!cfg.hasControl) {
      text = "Управление недоступно.";
    } else if (!state.control) {
      text = "Нажмите, чтобы захватить управление.";
    }
    overlay.textContent = text;
    overlay.classList.toggle("active", !!text);
  }

  function updateButtons() {
    if (sessionToggle) {
      sessionToggle.textContent = state.sessionActive
        ? "Выключить удаленный рабочий стол"
        : "Включить удаленный рабочий стол";
      sessionToggle.setAttribute("aria-pressed", state.sessionActive ? "true" : "false");
    }
    if (reloadBtn) {
      reloadBtn.disabled = !state.sessionActive;
    }
    if (captureBtn) {
      captureBtn.disabled = !cfg.hasControl || !state.sessionActive;
      captureBtn.textContent = state.control ? "Управление активно" : "Захватить управление";
    }
    if (controlBtn) {
      controlBtn.disabled = !cfg.hasControl || !state.sessionActive;
      controlBtn.textContent = state.control ? "Управление: вкл" : "Управление: выкл";
    }
    if (keyboardBtn) {
      keyboardBtn.disabled = !cfg.hasControl || !state.sessionActive;
      keyboardBtn.textContent = state.keyboard ? "Клавиатура: вкл" : "Клавиатура: выкл";
    }
    if (screenWrapper) {
      screenWrapper.classList.toggle("touch-control", state.control && cfg.hasControl);
    }
    updateFullscreenButton();
    updateOverlay();
  }


  function fullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement;
  }

  function isFullscreenActive() {
    return !!screenWrapper && fullscreenElement() === screenWrapper;
  }

  function updateFullscreenButton() {
    if (!fullscreenBtn) return;
    var active = isFullscreenActive();
    fullscreenBtn.textContent = active ? "Exit Fullscreen" : "Fullscreen";
    fullscreenBtn.setAttribute("aria-pressed", active ? "true" : "false");
  }

  function requestFullscreen(element) {
    if (!element) return;
    if (element.requestFullscreen) {
      element.requestFullscreen();
    } else if (element.webkitRequestFullscreen) {
      element.webkitRequestFullscreen();
    } else if (element.msRequestFullscreen) {
      element.msRequestFullscreen();
    }
  }

  function exitFullscreen() {
    if (document.exitFullscreen) {
      document.exitFullscreen();
    } else if (document.webkitExitFullscreen) {
      document.webkitExitFullscreen();
    } else if (document.msExitFullscreen) {
      document.msExitFullscreen();
    }
  }

  function toggleFullscreen() {
    if (!screenWrapper) return;
    if (isFullscreenActive()) {
      exitFullscreen();
    } else {
      requestFullscreen(screenWrapper);
    }
  }

  function startSession() {
    if (state.sessionActive) {
      return;
    }
    state.sessionActive = true;
    state.screenError = "";
    state.streamAttempt = "";
    state.frameCount = 0;
    nextStreamRunId();
    setStatus("Подключение…");
    showError("");
    updateButtons();
    refreshMonitors();
    refreshFiles();
    checkStatus(true);
    startStatusLoop();
    sendSessionState("start");
    sendClientLog("session_start", { renderMode: renderMode });
    sendClientLog("client_runtime", collectRuntimeDetails());
  }

  function stopSession() {
    if (!state.sessionActive) {
      return;
    }
    state.sessionActive = false;
    stopStatusLoop();
    stopStream();
    state.control = false;
    state.keyboard = false;
    state.screenError = "";
    state.streamAttempt = "";
    state.frameCount = 0;
    updateButtons();
    showError("");
    setStatus("Сеанс выключен");
    sendSessionState("stop");
    sendClientLog("session_stop", { renderMode: renderMode });
  }

  function buildUrl(base, params) {
    var url = new URL(base, window.location.href);
    Object.keys(params || {}).forEach(function (key) {
      if (params[key] !== undefined && params[key] !== null) {
        url.searchParams.set(key, String(params[key]));
      }
    });
    return url.toString();
  }

  function startStatusLoop() {
    stopStatusLoop();
    statusTimer = setInterval(function () {
      checkStatus(false);
    }, 8000);
  }

  function stopStatusLoop() {
    if (statusTimer) {
      clearInterval(statusTimer);
      statusTimer = null;
    }
  }

  function frameInterval() {
    var fpsValue = parseInt(fpsSelect ? fpsSelect.value : "10", 10);
    if (!Number.isNaN(fpsValue) && fpsValue > 0) {
      return Math.max(16, Math.round(1000 / fpsValue));
    }
    return 150;
  }

  function moveInterval() {
    var fpsValue = parseInt(fpsSelect ? fpsSelect.value : "15", 10);
    if (Number.isNaN(fpsValue) || fpsValue <= 0) {
      fpsValue = 15;
    }
    fpsValue = Math.min(MOVE_SEND_MAX_FPS, Math.max(8, fpsValue));
    return Math.max(16, Math.round(1000 / fpsValue));
  }

  function stopFrameLoop() {
    clearFrameStartupWatchdog();
    frameStartupLoaded = false;
    if (frameTimer) {
      clearTimeout(frameTimer);
      frameTimer = null;
    }
    if (frameAbort) {
      frameAbort.abort();
      frameAbort = null;
    }
    frameInFlight = false;
    clearCanvas();
  }

  function loadImageBlob(blob) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      var url = URL.createObjectURL(blob);
      img.onload = function () {
        URL.revokeObjectURL(url);
        resolve(img);
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error("Не удалось декодировать кадр."));
      };
      img.src = url;
    });
  }

  function drawFrame(image) {
    if (!canvasCtx || !canvas) return;
    var width = image.width || 0;
    var height = image.height || 0;
    if (!width || !height) return;
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    canvasCtx.drawImage(image, 0, 0, width, height);
    frameStartupLoaded = true;
    clearFrameStartupWatchdog();
  }

  function scheduleNextFrame(delayOverride) {
    if (!state.sessionActive || renderMode !== "frames") return;
    var delay = typeof delayOverride === "number" ? delayOverride : frameInterval();
    if (frameTimer) {
      clearTimeout(frameTimer);
    }
    frameTimer = setTimeout(fetchFrame, delay);
  }

  function fetchFrame() {
    if (!state.sessionActive || renderMode !== "frames") return;
    if (!cfg.snapshotUrl || !state.monitor || !canvasCtx) return;
    if (frameInFlight) {
      scheduleNextFrame();
      return;
    }
    var runId = state.streamRunId;
    var attemptTag = state.streamAttempt || "";
    frameInFlight = true;
    var url = buildUrl(cfg.snapshotUrl, {
      monitor: state.monitor.id,
      quality: qualitySelect ? qualitySelect.value : "",
      scale: scaleSelect ? scaleSelect.value : "",
      t: Date.now()
    });
    var controller = new AbortController();
    frameAbort = controller;
    fetch(url, { credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(function (resp) {
        if (!resp.ok) {
          return parseResponseDetail(resp, "/remote-desktop/snapshot")
            .then(function (detail) {
              throw new Error(detail || "Не удалось получить кадр.");
            });
        }
        return resp.blob();
      })
      .then(function (blob) {
        if (!isCurrentStreamRun(runId)) {
          return null;
        }
        if (window.createImageBitmap) {
          return createImageBitmap(blob);
        }
        return loadImageBlob(blob);
      })
      .then(function (image) {
        if (!image) {
          return;
        }
        if (!isCurrentStreamRun(runId)) {
          if (image && image.close) {
            image.close();
          }
          return;
        }
        state.frameCount += 1;
        drawFrame(image);
        if (image && image.close) {
          image.close();
        }
        if (state.frameCount <= 3 || state.frameCount % 120 === 0) {
          sendClientLog("frame_fetch_ok", {
            mode: "frames",
            transport: "snapshot",
            attempt: attemptTag,
            frame: state.frameCount,
            width: canvas ? (canvas.width || 0) : 0,
            height: canvas ? (canvas.height || 0) : 0
          });
        }
        if (state.screenError) {
          state.screenError = "";
          updateFullscreenButton();
          updateOverlay();
        }
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") {
          return;
        }
        if (!isCurrentStreamRun(runId)) {
          return;
        }
        var message = (err && err.message) ? err.message : "Не удалось получить кадр.";
        sendClientLog("frame_fetch_error", {
          mode: "frames",
          transport: "snapshot",
          attempt: attemptTag,
          detail: message
        }, "warning");
        state.screenError = message;
        showError(message);
        setStatus("Ошибка потока.");
        updateFullscreenButton();
        updateOverlay();
      })
      .finally(function () {
        frameInFlight = false;
        if (!isCurrentStreamRun(runId)) {
          return;
        }
        scheduleNextFrame();
      });
  }

  function startFrameLoop(reason) {
    if (!cfg.snapshotUrl || !state.monitor || !canvasCtx) {
      showError("Режим кадров недоступен в этом браузере.");
      setStatus("Экран недоступен.");
      return;
    }
    if (!state.sessionActive) {
      return;
    }
    var runId = nextStreamRunId();
    var attemptTag = "frames-" + Date.now();
    stopMjpegStream();
    state.streamAttempt = attemptTag;
    state.frameCount = 0;
    stopFrameLoop();
    setRenderMode("frames");
    if (reason) {
      showError(reason);
    } else {
      showError("");
    }
    sendClientLog("stream_request", {
      mode: "frames",
      transport: "snapshot",
      attempt: attemptTag,
      monitor: state.monitor ? state.monitor.id : 1,
      fps: fpsSelect ? fpsSelect.value : "",
      quality: qualitySelect ? qualitySelect.value : "",
      scale: scaleSelect ? scaleSelect.value : ""
    });
    clearFrameStartupWatchdog();
    frameStartupLoaded = false;
    frameStartupWatchdog = setTimeout(function () {
      if (!frameStartupLoaded && state.sessionActive && renderMode === "frames" && isCurrentStreamRun(runId)) {
        sendClientLog("stream_watchdog_timeout", {
          mode: "frames",
          transport: "snapshot",
          attempt: state.streamAttempt || "",
          frameCount: state.frameCount || 0
        }, "warning");
      }
    }, 8000);
    setStatus("Кадровый режим активен.");
    scheduleNextFrame(0);
    checkStatus(true);
  }

  function stopMjpegStream(clearSource) {
    if (mjpegWatchdog) {
      clearTimeout(mjpegWatchdog);
      mjpegWatchdog = null;
    }
    if (mjpegProbe) {
      clearInterval(mjpegProbe);
      mjpegProbe = null;
    }
    if (mjpegFetchController) {
      try {
        mjpegFetchController.abort();
      } catch (err) {
      }
      mjpegFetchController = null;
    }
    mjpegLoaded = false;
    mjpegTransport = "img";
    setStreamCanvasMode(false);
    if (screen && clearSource !== false) {
      try {
        screen.src = "about:blank";
      } catch (err) {
      }
      try {
        screen.removeAttribute("src");
      } catch (err) {
      }
      try {
        screen.src = "";
      } catch (err) {
      }
    }
  }

  function markMjpegReady() {
    if (mjpegLoaded) return;
    mjpegLoaded = true;
    if (mjpegWatchdog) {
      clearTimeout(mjpegWatchdog);
      mjpegWatchdog = null;
    }
    if (mjpegProbe) {
      clearInterval(mjpegProbe);
      mjpegProbe = null;
    }
    if (cfg.hasScreen) {
      setStatus("Поток активен.");
    }
    var readyWidth = streamCanvasMode && canvas ? (canvas.width || 0) : (screen ? (screen.naturalWidth || 0) : 0);
    var readyHeight = streamCanvasMode && canvas ? (canvas.height || 0) : (screen ? (screen.naturalHeight || 0) : 0);
    sendClientLog("stream_ready", {
      mode: renderMode,
      transport: mjpegTransport,
      attempt: state.streamAttempt || "",
      width: readyWidth,
      height: readyHeight
    });
    if (state.screenError) {
      state.screenError = "";
      showError("");
      updateOverlay();
    }
  }

  function startMjpegProbe() {
    if (!screen) return;
    if (mjpegProbe) {
      clearInterval(mjpegProbe);
    }
    mjpegProbe = setInterval(function () {
      if (!state.sessionActive || renderMode !== "mjpeg" || mjpegTransport !== "img") return;
      if (screen.naturalWidth > 0 && screen.naturalHeight > 0) {
        markMjpegReady();
      }
    }, 400);
  }

  function supportsMjpegFetchTransport() {
    return !!(
      canvasCtx &&
      window.fetch &&
      window.AbortController &&
      window.TextDecoder &&
      window.Uint8Array &&
      window.Blob
    );
  }

  function toAsciiBytes(text) {
    var out = new Uint8Array(text.length);
    for (var i = 0; i < text.length; i += 1) {
      out[i] = text.charCodeAt(i) & 0xFF;
    }
    return out;
  }

  function concatBytes(left, right) {
    if (!left || !left.length) return right || new Uint8Array(0);
    if (!right || !right.length) return left;
    var out = new Uint8Array(left.length + right.length);
    out.set(left, 0);
    out.set(right, left.length);
    return out;
  }

  function indexOfBytes(haystack, needle, fromIndex) {
    if (!haystack || !needle || !needle.length) return -1;
    var start = Math.max(0, fromIndex || 0);
    var max = haystack.length - needle.length;
    for (var i = start; i <= max; i += 1) {
      var matched = true;
      for (var j = 0; j < needle.length; j += 1) {
        if (haystack[i + j] !== needle[j]) {
          matched = false;
          break;
        }
      }
      if (matched) return i;
    }
    return -1;
  }

  function trimCrlfBytes(bytes) {
    if (!bytes || !bytes.length) return new Uint8Array(0);
    var start = 0;
    var end = bytes.length;
    while (start < end && (bytes[start] === 13 || bytes[start] === 10)) {
      start += 1;
    }
    while (end > start && (bytes[end - 1] === 13 || bytes[end - 1] === 10)) {
      end -= 1;
    }
    return bytes.slice(start, end);
  }

  function findHeaderSeparator(bytes) {
    var crlf = new Uint8Array([13, 10, 13, 10]);
    var lf = new Uint8Array([10, 10]);
    var crlfPos = indexOfBytes(bytes, crlf, 0);
    if (crlfPos >= 0) {
      return { pos: crlfPos, len: 4 };
    }
    var lfPos = indexOfBytes(bytes, lf, 0);
    if (lfPos >= 0) {
      return { pos: lfPos, len: 2 };
    }
    return null;
  }

  function decodeHeaderBytes(bytes) {
    if (!bytes || !bytes.length) return "";
    try {
      return new TextDecoder("utf-8").decode(bytes);
    } catch (err) {
      var chars = [];
      for (var i = 0; i < bytes.length; i += 1) {
        chars.push(String.fromCharCode(bytes[i]));
      }
      return chars.join("");
    }
  }

  function parseMjpegBoundary(contentType) {
    var text = String(contentType || "");
    var match = text.match(/boundary\s*=\s*(\"?)([^\";]+)\1/i);
    if (!match) {
      return "";
    }
    var boundary = String(match[2] || "").trim();
    while (boundary.indexOf("--") === 0) {
      boundary = boundary.slice(2);
    }
    return boundary;
  }

  function parseContentLength(headersText) {
    var text = String(headersText || "");
    var match = text.match(/content-length\s*:\s*(\d+)/i);
    if (!match) return 0;
    var parsed = parseInt(match[1], 10);
    if (!Number.isFinite(parsed) || parsed <= 0) return 0;
    return parsed;
  }

  function extractMjpegFrame(partBytes) {
    var part = trimCrlfBytes(partBytes);
    if (!part.length) return null;
    if (part.length >= 2 && part[0] === 45 && part[1] === 45) {
      return { done: true };
    }
    var sep = findHeaderSeparator(part);
    if (!sep) return null;
    var headersBytes = part.slice(0, sep.pos);
    var body = part.slice(sep.pos + sep.len);
    var headersText = decodeHeaderBytes(headersBytes);
    var contentLength = parseContentLength(headersText);
    if (contentLength > 0 && body.length >= contentLength) {
      body = body.slice(0, contentLength);
    }
    body = trimCrlfBytes(body);
    if (!body.length) return null;
    return { done: false, frame: body, headersText: headersText };
  }

  function decodeJpegBytes(frameBytes) {
    var blob = new Blob([frameBytes], { type: "image/jpeg" });
    if (window.createImageBitmap) {
      return createImageBitmap(blob);
    }
    return loadImageBlob(blob);
  }

  function processMjpegBuffer(parseState, onFrame) {
    var boundaryBytes = parseState.boundaryBytes;
    if (!boundaryBytes || !boundaryBytes.length) {
      return Promise.resolve();
    }
    return (async function () {
      while (true) {
        var first = indexOfBytes(parseState.buffer, boundaryBytes, 0);
        if (first < 0) {
          var keep = Math.max(boundaryBytes.length * 2, 64);
          if (parseState.buffer.length > keep) {
            parseState.buffer = parseState.buffer.slice(parseState.buffer.length - keep);
          }
          return;
        }
        if (first > 0) {
          parseState.buffer = parseState.buffer.slice(first);
        }
        var next = indexOfBytes(parseState.buffer, boundaryBytes, boundaryBytes.length);
        if (next < 0) {
          return;
        }
        var part = parseState.buffer.slice(boundaryBytes.length, next);
        parseState.buffer = parseState.buffer.slice(next);
        var extracted = extractMjpegFrame(part);
        if (!extracted) continue;
        if (extracted.done) {
          parseState.done = true;
          return;
        }
        await onFrame(extracted.frame, extracted.headersText || "");
      }
    })();
  }

  function startMjpegFetchStream(reason, forcedAttemptTag) {
    if (!supportsMjpegFetchTransport()) {
      var unsupported = reason || "Совместимый режим MJPEG через fetch недоступен в этом браузере.";
      startFrameLoop(unsupported + "\nАвтодействие: переключение в кадровый режим.");
      return;
    }
    if (!state.sessionActive || renderMode !== "mjpeg") return;
    var runId = nextStreamRunId();
    stopFrameLoop();
    stopMjpegStream();
    setRenderMode("mjpeg");
    setStreamCanvasMode(true);
    clearCanvas();
    if (reason) {
      showError(reason);
    } else {
      showError("");
    }
    setStatus("Запуск совместимого потока…");
    var attemptTag = forcedAttemptTag || ("mjpeg-fetch-" + Date.now());
    state.streamAttempt = attemptTag;
    state.frameCount = 0;
    mjpegTransport = "fetch";
    mjpegLoaded = false;
    var url = buildUrl(cfg.streamUrl, {
      monitor: state.monitor ? state.monitor.id : 1,
      fps: fpsSelect ? fpsSelect.value : "",
      quality: qualitySelect ? qualitySelect.value : "",
      scale: scaleSelect ? scaleSelect.value : "",
      diag: attemptTag,
      trace: 1,
      transport: "fetch",
      t: Date.now()
    });
    sendClientLog("stream_request", {
      mode: "mjpeg",
      transport: "fetch",
      attempt: attemptTag,
      monitor: state.monitor ? state.monitor.id : 1,
      fps: fpsSelect ? fpsSelect.value : "",
      quality: qualitySelect ? qualitySelect.value : "",
      scale: scaleSelect ? scaleSelect.value : ""
    });
    if (mjpegWatchdog) {
      clearTimeout(mjpegWatchdog);
      mjpegWatchdog = null;
    }
    mjpegWatchdog = setTimeout(function () {
      if (!mjpegLoaded && state.sessionActive && renderMode === "mjpeg" && mjpegTransport === "fetch" && isCurrentStreamRun(runId)) {
        sendClientLog(
          "stream_watchdog_timeout",
          {
            mode: renderMode,
            transport: mjpegTransport,
            attempt: state.streamAttempt || "",
            canvasWidth: canvas ? (canvas.width || 0) : 0,
            canvasHeight: canvas ? (canvas.height || 0) : 0
          },
          "warning"
        );
        handleStreamFailure("Совместимый MJPEG поток не запустился в отведенное время.", runId);
      }
    }, 8000);
    var controller = new AbortController();
    mjpegFetchController = controller;
    fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
      headers: { "Accept": "multipart/x-mixed-replace" }
    })
      .then(function (resp) {
        if (!isCurrentStreamRun(runId)) {
          throw new Error("stale_stream_run");
        }
        if (!resp.ok) {
          return parseResponseDetail(resp, "/remote-desktop/stream")
            .then(function (detail) {
              throw new Error(detail || ("HTTP " + (resp.status || 0) + " на /remote-desktop/stream"));
            });
        }
        var contentType = String(resp.headers.get("Content-Type") || "");
        var boundary = parseMjpegBoundary(contentType);
        sendClientLog("stream_fetch_response", {
          mode: "mjpeg",
          transport: "fetch",
          attempt: attemptTag,
          contentType: contentType.slice(0, 220),
          boundary: boundary || "",
          hasBodyReader: !!(resp.body && resp.body.getReader)
        });
        if (!boundary) {
          throw new Error("Сервер не передал boundary для MJPEG потока.");
        }
        if (!resp.body || !resp.body.getReader) {
          throw new Error("Потоковое чтение ответа не поддерживается браузером.");
        }
        var reader = resp.body.getReader();
        var parseState = {
          buffer: new Uint8Array(0),
          boundaryBytes: toAsciiBytes("--" + boundary),
          done: false,
          frames: 0
        };
        return (async function () {
          try {
            while (state.sessionActive && renderMode === "mjpeg" && mjpegTransport === "fetch" && isCurrentStreamRun(runId)) {
              var chunk = await reader.read();
              if (chunk.done) {
                break;
              }
              if (!chunk.value || !chunk.value.length) {
                continue;
              }
              parseState.buffer = concatBytes(parseState.buffer, chunk.value);
              await processMjpegBuffer(parseState, async function (frameBytes) {
                if (!isCurrentStreamRun(runId)) {
                  return;
                }
                parseState.frames += 1;
                var image = await decodeJpegBytes(frameBytes);
                try {
                  drawFrame(image);
                } finally {
                  if (image && image.close) {
                    image.close();
                  }
                }
                if (!mjpegLoaded) {
                  markMjpegReady();
                }
                if (parseState.frames <= 3 || parseState.frames % 120 === 0) {
                  sendClientLog("stream_fetch_frame", {
                    mode: "mjpeg",
                    transport: "fetch",
                    attempt: attemptTag,
                    frame: parseState.frames,
                    width: canvas ? (canvas.width || 0) : 0,
                    height: canvas ? (canvas.height || 0) : 0
                  });
                }
              });
              if (parseState.done) {
                break;
              }
            }
          } finally {
            try {
              reader.releaseLock();
            } catch (err) {
            }
          }
          if (!isCurrentStreamRun(runId)) {
            return parseState.frames || 0;
          }
          if (!mjpegLoaded) {
            throw new Error("Поток завершился до получения первого кадра.");
          }
          return parseState.frames || 0;
        })();
      })
      .then(function (frames) {
        if (!isCurrentStreamRun(runId)) {
          return;
        }
        sendClientLog("stream_fetch_end", {
          mode: "mjpeg",
          transport: "fetch",
          attempt: attemptTag,
          frames: frames || 0
        });
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") {
          return;
        }
        if (!isCurrentStreamRun(runId)) {
          return;
        }
        var detail = (err && err.message) ? err.message : "Ошибка совместимого MJPEG потока.";
        sendClientLog("stream_fetch_error", {
          mode: "mjpeg",
          transport: "fetch",
          attempt: attemptTag,
          detail: detail
        }, "warning");
        handleStreamFailure("Совместимый MJPEG поток не запустился. Проверьте диагностику.", runId);
      });
    checkStatus(true);
  }

  function parseResponseDetail(resp, endpointName) {
    if (!resp) return Promise.resolve("");
    var contentType = "";
    try {
      contentType = String(resp.headers.get("Content-Type") || "").toLowerCase();
    } catch (err) {
      contentType = "";
    }
    var statusLine = "HTTP " + (resp.status || 0) + " на " + endpointName;
    return resp.text()
      .then(function (body) {
        var textBody = String(body || "");
        var compactBody = textBody.replace(/\s+/g, " ").trim();
        if (contentType.indexOf("application/json") !== -1) {
          try {
            var data = JSON.parse(textBody);
            if (!data) return "";
            return String(
              data.screen_error ||
              data.screen_hint ||
              data.error ||
              data.message ||
              ""
            );
          } catch (err) {
            if (compactBody) {
              return statusLine + ". Некорректный JSON: " + compactBody.slice(0, 220);
            }
            return statusLine + ". Некорректный JSON в ответе.";
          }
        }
        if (contentType.indexOf("text/html") !== -1) {
          return statusLine + ". Сервер вернул HTML вместо JSON (возможна переадресация на страницу входа).";
        }
        if (compactBody) {
          return statusLine + ". Ответ: " + compactBody.slice(0, 220);
        }
        if (!resp.ok) {
          return statusLine;
        }
        return "";
      })
      .catch(function (err) {
        return (err && err.message) ? (statusLine + ". " + err.message) : statusLine;
      });
  }

  function probeSnapshotFailure(diagTag) {
    if (!cfg.snapshotUrl) {
      return Promise.resolve({ detail: "", ok: false, known: false });
    }
    var params = {
      monitor: state.monitor ? state.monitor.id : 1,
      quality: qualitySelect ? qualitySelect.value : "",
      scale: scaleSelect ? scaleSelect.value : "",
      diag: diagTag || "stream_failure",
      t: Date.now()
    };
    return fetch(buildUrl(cfg.snapshotUrl, params), { credentials: "same-origin", cache: "no-store" })
      .then(function (resp) {
        if (resp.ok) {
          return { detail: "", ok: true, known: true };
        }
        return parseResponseDetail(resp, "/remote-desktop/snapshot")
          .then(function (detail) {
            return { detail: detail || "", ok: false, known: true };
          });
      })
      .catch(function (err) {
        return {
          detail: (err && err.message) ? err.message : "",
          ok: false,
          known: false
        };
      });
  }

  function fetchStatusDiagnostic(diagTag) {
    if (!cfg.statusUrl) {
      return Promise.resolve({ detail: "", ok: false, known: false });
    }
    var params = {
      monitor: state.monitor ? state.monitor.id : 1,
      quality: qualitySelect ? qualitySelect.value : "",
      scale: scaleSelect ? scaleSelect.value : "",
      check: 1,
      diag: diagTag || "stream_failure"
    };
    return fetch(buildUrl(cfg.statusUrl, params), { credentials: "same-origin", cache: "no-store" })
      .then(function (resp) {
        if (!resp.ok) {
          return parseResponseDetail(resp, "/remote-desktop/status")
            .then(function (detail) {
              return { detail: detail || "", ok: false, known: true };
            });
        }
        return resp.text()
          .then(function (body) {
            try {
              var data = JSON.parse(String(body || "{}"));
              var detail = String(
                data.screen_error ||
                data.screen_hint ||
                data.error ||
                data.message ||
                ""
              );
              var ok = !!(data.ok && data.screen_ok && !detail);
              return { detail: detail, ok: ok, known: true };
            } catch (err) {
              return {
                detail: "Некорректный JSON от /remote-desktop/status",
                ok: false,
                known: true
              };
            }
          });
      })
      .catch(function (err) {
        return {
          detail: (err && err.message) ? err.message : "",
          ok: false,
          known: false
        };
      });
  }

  function resolveStreamFailureReason(diagTag) {
    return fetchStatusDiagnostic(diagTag)
      .then(function (statusDiag) {
        if (statusDiag.detail) {
          return {
            detail: statusDiag.detail,
            statusOk: statusDiag.ok,
            snapshotOk: false,
            statusKnown: statusDiag.known,
            snapshotKnown: false
          };
        }
        return probeSnapshotFailure(diagTag)
          .then(function (snapshotDiag) {
            var detail = snapshotDiag.detail || "";
            return {
              detail: detail,
              statusOk: statusDiag.ok,
              snapshotOk: snapshotDiag.ok,
              statusKnown: statusDiag.known,
              snapshotKnown: snapshotDiag.known
            };
          });
      });
  }

  function handleStreamFailure(reason, failedRunId) {
    if (typeof failedRunId === "number" && !isCurrentStreamRun(failedRunId)) return;
    if (!state.sessionActive || renderMode !== "mjpeg") return;
    var failedTransport = mjpegTransport || "img";
    var browserName = detectBrowserName();
    stopMjpegStream();
    var baseReason = reason || "Поток не запустился.";
    var diagTag = state.streamAttempt || ("stream-" + Date.now());
    resolveStreamFailureReason(diagTag).then(function (diagResult) {
      if (!state.sessionActive || renderMode !== "mjpeg") return;
      var detail = (diagResult && diagResult.detail) ? String(diagResult.detail) : "";
      var backendHealthy = !!(
        diagResult &&
        diagResult.statusOk &&
        diagResult.snapshotOk
      );
      if (!detail && backendHealthy) {
        detail = "Сервер передает кадры, но браузер не отображает MJPEG. Возможна блокировка потока браузером или несовместимость режима.";
      }
      var message = baseReason;
      if (detail && detail !== baseReason) {
        message += "\nПричина: " + detail;
      } else if (!detail) {
        message += "\nПричина: диагностические данные недоступны (проверьте /status и /snapshot).";
      }
      message += "\nКод диагностики: " + diagTag;
      state.screenError = message;
      if (backendHealthy) {
        if (failedTransport !== "fetch" && supportsMjpegFetchTransport() && browserName !== "firefox") {
          var fetchFallbackMessage = message + "\nАвтодействие: переход на совместимый MJPEG транспорт.";
          state.screenError = fetchFallbackMessage;
          sendClientLog(
            "stream_auto_fallback_fetch",
            {
              mode: renderMode,
              fromTransport: failedTransport,
              toTransport: "fetch",
              diag: diagTag,
              reason: detail || "backend_healthy_img_mjpeg_not_rendered"
            },
            "warning"
          );
          startMjpegFetchStream(fetchFallbackMessage, diagTag);
          return;
        }
        var fallbackMessage = message + "\nАвтодействие: переключение в кадровый режим.";
        state.screenError = fallbackMessage;
        sendClientLog(
          "stream_auto_fallback_frames",
          {
            mode: renderMode,
            transport: failedTransport,
            diag: diagTag,
            reason: detail || "backend_healthy_no_mjpeg_render"
          },
          "warning"
        );
        startFrameLoop(fallbackMessage);
        return;
      }
      showError(message);
      setStatus("Поток недоступен.");
      sendClientLog(
        "stream_failed",
        {
          mode: renderMode,
          transport: failedTransport,
          reason: message,
          detail: detail || "",
          diag: diagTag,
          statusOk: !!(diagResult && diagResult.statusOk),
          snapshotOk: !!(diagResult && diagResult.snapshotOk),
          statusKnown: !!(diagResult && diagResult.statusKnown),
          snapshotKnown: !!(diagResult && diagResult.snapshotKnown)
        },
        "warning"
      );
      updateFullscreenButton();
      updateOverlay();
    });
  }

  function startMjpegStream(reason) {
    if (!screen || !cfg.streamUrl) {
      startFrameLoop(reason || "Переход на режим кадров.");
      return;
    }
    var runId = nextStreamRunId();
    stopFrameLoop();
    stopMjpegStream(false);
    setRenderMode("mjpeg");
    setStreamCanvasMode(false);
    mjpegTransport = "img";
    if (reason) {
      showError(reason);
    } else {
      showError("");
    }
    setStatus("Запуск потока…");
    var attemptTag = "mjpeg-" + Date.now();
    state.streamAttempt = attemptTag;
    state.frameCount = 0;
    var url = buildUrl(cfg.streamUrl, {
      monitor: state.monitor.id,
      fps: fpsSelect ? fpsSelect.value : "",
      quality: qualitySelect ? qualitySelect.value : "",
      scale: scaleSelect ? scaleSelect.value : "",
      diag: attemptTag,
      trace: 1,
      transport: "img",
      t: Date.now()
    });
    sendClientLog("stream_request", {
      mode: "mjpeg",
      transport: "img",
      attempt: attemptTag,
      monitor: state.monitor ? state.monitor.id : 1,
      fps: fpsSelect ? fpsSelect.value : "",
      quality: qualitySelect ? qualitySelect.value : "",
      scale: scaleSelect ? scaleSelect.value : ""
    });
    mjpegLoaded = false;
    startMjpegProbe();
    mjpegWatchdog = setTimeout(function () {
      if (!mjpegLoaded && state.sessionActive && renderMode === "mjpeg" && isCurrentStreamRun(runId)) {
        sendClientLog(
          "stream_watchdog_timeout",
          {
            mode: renderMode,
            transport: mjpegTransport,
            attempt: state.streamAttempt || "",
            naturalWidth: screen ? (screen.naturalWidth || 0) : 0,
            naturalHeight: screen ? (screen.naturalHeight || 0) : 0,
            hasSrc: !!(screen && screen.getAttribute("src"))
          },
          "warning"
        );
        handleStreamFailure("Поток не запустился. Попробуйте кадровый режим или нажмите «Обновить поток».", runId);
      }
    }, 8000);
    try {
      screen.removeAttribute("src");
    } catch (err) {
    }
    screen.src = url;
    checkStatus(true);
  }

  function stopStream() {
    nextStreamRunId();
    stopFrameLoop();
    stopMjpegStream();
    state.streamAttempt = "";
    state.frameCount = 0;
  }

  function startStream() {
    if (!state.sessionActive) {
      setStatus("Сеанс выключен");
      return;
    }
    if (!cfg.hasScreen) {
      setStatus("Экран недоступен.");
      return;
    }
    if (!state.monitor) {
      setStatus("Нет доступных мониторов.");
      return;
    }
    if (renderMode === "frames") {
      startFrameLoop();
    } else {
      startMjpegStream();
    }
  }

  function refreshMonitors() {
    if (!cfg.hasScreen) {
      setStatus("Экран недоступен.");
      return;
    }
    fetch(cfg.infoUrl, { credentials: "same-origin" })
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        if (!data.ok) {
          throw new Error(data.error || "Не удалось получить мониторы.");
        }
        state.monitors = data.monitors || [];
        if (monitorSelect) {
          monitorSelect.innerHTML = "";
          state.monitors.forEach(function (mon) {
            var opt = document.createElement("option");
            opt.value = String(mon.id);
            opt.textContent = "Монитор " + mon.id + " (" + mon.width + "x" + mon.height + ")";
            if (mon.primary) {
              opt.textContent += " (основной)";
            }
            monitorSelect.appendChild(opt);
          });
        }
        if (!state.monitors.length) {
          setStatus("Мониторы не найдены.");
          showError("Не удалось получить список мониторов.");
          return;
        }
        state.monitor = state.monitors[0] || null;
        showError("");
        if (state.sessionActive) {
          startStream();
          checkStatus(true);
        }
      })
      .catch(function (err) {
        setStatus(err.message || "Ошибка получения мониторов.");
      });
  }

  function checkStatus(checkCapture) {
    if (!cfg.statusUrl) return;
    if (!state.sessionActive) return;
    var params = {
      monitor: state.monitor ? state.monitor.id : 1,
      quality: qualitySelect ? qualitySelect.value : "",
      scale: scaleSelect ? scaleSelect.value : ""
    };
    if (checkCapture) {
      params.check = 1;
    }
    fetch(buildUrl(cfg.statusUrl, params), { credentials: "same-origin" })
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        var screenError = data.screen_error || data.screen_hint || "";
        state.screenError = screenError;
        if (screenError) {
          showError(screenError);
        } else {
          showError("");
        }
        updateFullscreenButton();
        updateOverlay();
      })
      .catch(function () {});
  }

  function sendInput(payload) {
    if (!state.sessionActive) return;
    if (!state.control || !cfg.hasControl) return;
    fetch(cfg.inputUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": cfg.csrfToken,
        "X-RDP-Token": cfg.token
      },
      credentials: "same-origin",
      body: JSON.stringify(payload)
    }).catch(function () {});
  }

  function getRemoteCoords(event) {
    var surface = activeSurface();
    if (!state.monitor || !surface) return null;
    var rect = surface.getBoundingClientRect();
    var x = event.clientX - rect.left;
    var y = event.clientY - rect.top;
    if (rect.width <= 0 || rect.height <= 0) return null;
    x = Math.max(0, Math.min(rect.width, x));
    y = Math.max(0, Math.min(rect.height, y));
    var scaleX = state.monitor.width / rect.width;
    var scaleY = state.monitor.height / rect.height;
    return {
      x: Math.round(state.monitor.left + x * scaleX),
      y: Math.round(state.monitor.top + y * scaleY)
    };
  }

  function scheduleMove(event) {
    state.pendingMove = {
      clientX: event.clientX,
      clientY: event.clientY
    };
    if (state.moveScheduled) return;
    state.moveScheduled = true;
    requestAnimationFrame(flushScheduledMove);
  }

  function flushScheduledMove() {
    state.moveScheduled = false;
    if (!state.pendingMove) return;
    var now = Date.now();
    var interval = moveInterval();
    if (state.lastMove && now - state.lastMove < interval) {
      var waitFor = Math.max(1, interval - (now - state.lastMove));
      state.moveScheduled = true;
      setTimeout(function () {
        requestAnimationFrame(flushScheduledMove);
      }, waitFor);
      return;
    }
    var coords = getRemoteCoords(state.pendingMove);
    state.pendingMove = null;
    if (!coords) return;
    state.lastMove = now;
    sendInput({ action: "mouse_move", x: coords.x, y: coords.y });
  }

  function mouseButtonName(event) {
    if (event.button === 2) return "right";
    if (event.button === 1) return "middle";
    return "left";
  }

  function mapKey(event) {
    if (KEY_MAP[event.code]) return KEY_MAP[event.code];
    if (event.key && event.key.length === 1) {
      return event.key;
    }
    return null;
  }

  function isPrintable(event) {
    return event.key && event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey;
  }

  function handleKey(event, action) {
    if (!state.keyboard || !state.control || !cfg.hasControl) return;
    if (isPrintable(event)) {
      if (action === "key_down") {
        sendInput({ action: "text", text: event.key });
        event.preventDefault();
      }
      return;
    }
    var key = mapKey(event);
    if (!key) return;
    sendInput({ action: action, key: key.toLowerCase() });
    event.preventDefault();
  }

  function refreshFiles() {
    if (!filesContainer) return;
    var url = buildUrl(cfg.filesListUrl, { path: state.sharePath });
    fetch(url, {
      credentials: "same-origin",
      headers: { "X-RDP-Token": cfg.token }
    })
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        if (!data.ok) {
          throw new Error(data.error || "Ошибка загрузки файлов.");
        }
        renderFiles(data.items || [], data.path || "");
      })
      .catch(function (err) {
        filesContainer.textContent = err.message || "Не удалось загрузить файлы.";
      });
  }

  function downloadSharedFile(path, name) {
    var url = buildUrl(cfg.fileDownloadUrl, { path: path || "" });
    fetch(url, {
      credentials: "same-origin",
      headers: { "X-RDP-Token": cfg.token }
    })
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("Не удалось скачать файл.");
        }
        return resp.blob();
      })
      .then(function (blob) {
        var objectUrl = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = objectUrl;
        link.download = name || "download";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () {
          URL.revokeObjectURL(objectUrl);
        }, 1000);
      })
      .catch(function (err) {
        setStatus(err.message || "Ошибка скачивания файла.");
      });
  }

  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return "";
    var sizes = ["Б", "КБ", "МБ", "ГБ"];
    var i = 0;
    var value = bytes;
    while (value >= 1024 && i < sizes.length - 1) {
      value /= 1024;
      i += 1;
    }
    return value.toFixed(value >= 10 || i === 0 ? 0 : 1) + " " + sizes[i];
  }

  function renderFiles(items, currentPath) {
    filesContainer.innerHTML = "";
    if (state.sharePath) {
      var backRow = document.createElement("div");
      backRow.className = "rd-file-item";
      var backMeta = document.createElement("div");
      backMeta.className = "rd-file-meta";
      var backTitle = document.createElement("span");
      backTitle.textContent = "Назад";
      backMeta.appendChild(backTitle);
      backRow.appendChild(backMeta);
      var backBtn = document.createElement("button");
      backBtn.className = "btn";
      backBtn.type = "button";
      backBtn.textContent = "Перейти";
      backBtn.addEventListener("click", function () {
        var parts = state.sharePath.split("/");
        parts.pop();
        state.sharePath = parts.filter(Boolean).join("/");
        refreshFiles();
      });
      backRow.appendChild(backBtn);
      filesContainer.appendChild(backRow);
    }
    if (!items.length) {
      var empty = document.createElement("div");
      empty.textContent = "Файлы не найдены.";
      filesContainer.appendChild(empty);
      return;
    }
    items.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "rd-file-item";
      var meta = document.createElement("div");
      meta.className = "rd-file-meta";
      var title = document.createElement("span");
      title.textContent = item.is_dir ? item.name + " (папка)" : item.name;
      var info = document.createElement("small");
      info.textContent = item.is_dir ? "Папка" : formatBytes(item.size) + (item.modified ? " | " + item.modified : "");
      meta.appendChild(title);
      meta.appendChild(info);
      row.appendChild(meta);

      if (item.is_dir) {
        var openBtn = document.createElement("button");
        openBtn.className = "btn";
        openBtn.type = "button";
        openBtn.textContent = "Открыть";
        openBtn.addEventListener("click", function () {
          state.sharePath = item.path;
          refreshFiles();
        });
        row.appendChild(openBtn);
      } else {
        var downloadBtn = document.createElement("button");
        downloadBtn.className = "btn";
        downloadBtn.type = "button";
        downloadBtn.textContent = "Скачать";
        downloadBtn.addEventListener("click", function () {
          downloadSharedFile(item.path, item.name);
        });
        row.appendChild(downloadBtn);
      }
      filesContainer.appendChild(row);
    });
  }

  function uploadFiles(files) {
    if (!files || !files.length) return;
    var form = new FormData();
    Array.prototype.forEach.call(files, function (file) {
      form.append("files", file);
    });
    form.append("path", state.sharePath);
    fetch(cfg.fileUploadUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": cfg.csrfToken,
        "X-RDP-Token": cfg.token
      },
      credentials: "same-origin",
      body: form
    })
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        if (!data.ok) {
          throw new Error(data.error || "Не удалось загрузить файлы.");
        }
        refreshFiles();
      })
      .catch(function (err) {
        if (filesContainer) {
          filesContainer.textContent = err.message || "Ошибка загрузки файлов.";
        }
      });
  }

  function sendSessionState(stateName) {
    if (!cfg.sessionUrl) return;
    var payload = JSON.stringify({ state: stateName, token: cfg.token });
    var url = buildUrl(cfg.sessionUrl, { csrf_token: cfg.csrfToken });
    if (navigator.sendBeacon) {
      var blob = new Blob([payload], { type: "application/json" });
      if (navigator.sendBeacon(url, blob)) {
        return;
      }
    }
    fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": cfg.csrfToken,
        "X-RDP-Token": cfg.token
      },
      credentials: "same-origin",
      keepalive: true,
      body: payload
    }).catch(function () {});
  }

  function sendClientLog(eventName, data, level) {
    if (!cfg.clientLogUrl) return;
    var url = buildUrl(cfg.clientLogUrl, { csrf_token: cfg.csrfToken });
    var payload = JSON.stringify({
      event: eventName,
      data: data || {},
      level: level || "info",
      token: cfg.token
    });
    if (navigator.sendBeacon) {
      var blob = new Blob([payload], { type: "application/json" });
      if (navigator.sendBeacon(url, blob)) {
        return;
      }
    }
    fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": cfg.csrfToken,
        "X-RDP-Token": cfg.token
      },
      credentials: "same-origin",
      keepalive: true,
      body: payload
    }).catch(function () {});
  }

  if (monitorSelect) {
    monitorSelect.addEventListener("change", function () {
      var selected = parseInt(monitorSelect.value, 10);
      state.monitor = state.monitors.find(function (mon) { return mon.id === selected; }) || state.monitors[0] || null;
      if (state.sessionActive) {
        startStream();
      }
    });
  }

  if (qualitySelect) {
    qualitySelect.addEventListener("change", function () {
      if (state.sessionActive) startStream();
    });
  }
  if (fpsSelect) {
    fpsSelect.addEventListener("change", function () {
      if (state.sessionActive) startStream();
    });
  }
  if (scaleSelect) {
    scaleSelect.addEventListener("change", function () {
      if (state.sessionActive) startStream();
    });
  }
  if (modeInputs && modeInputs.length) {
    Array.prototype.forEach.call(modeInputs, function (input) {
      input.addEventListener("change", function () {
        if (!input.checked) return;
        markUserGesture();
        setRenderMode(input.value, true);
        if (state.sessionActive) {
          startStream();
          checkStatus(true);
        } else {
          stopStream();
          updateFullscreenButton();
          updateOverlay();
        }
      });
    });
  }
  if (reloadBtn) {
    reloadBtn.addEventListener("click", function () {
      markUserGesture();
      if (!state.sessionActive) {
        setStatus("Сеанс выключен");
        return;
      }
      startStream();
      checkStatus(true);
    });
  }

  if (controlBtn) {
    controlBtn.addEventListener("click", function () {
      markUserGesture();
      if (!state.sessionActive) {
        setStatus("Сеанс выключен");
        return;
      }
      state.control = !state.control;
      if (state.control && !state.keyboard) {
        state.keyboard = true;
      }
      updateButtons();
      if (state.control && screenWrapper) {
        screenWrapper.focus();
      }
    });
  }

  if (captureBtn) {
    captureBtn.addEventListener("click", function () {
      markUserGesture();
      if (!state.sessionActive) {
        setStatus("Сеанс выключен");
        return;
      }
      if (!cfg.hasControl) {
        setStatus("Управление недоступно.");
        return;
      }
      state.control = true;
      state.keyboard = true;
      updateButtons();
      if (screenWrapper) {
        screenWrapper.focus();
      }
    });
  }

  if (keyboardBtn) {
    keyboardBtn.addEventListener("click", function () {
      markUserGesture();
      if (!state.sessionActive) {
        setStatus("Сеанс выключен");
        return;
      }
      state.keyboard = !state.keyboard;
      updateButtons();
      if (state.keyboard && screenWrapper) {
        screenWrapper.focus();
      }
    });
  }

  if (clearFocusBtn) {
    clearFocusBtn.addEventListener("click", function () {
      if (document.activeElement) {
        document.activeElement.blur();
      }
    });
  }

  if (screenWrapper) {
    var pointerSupported = !!window.PointerEvent;
    if (pointerSupported) {
      screenWrapper.addEventListener("pointerdown", function (event) {
        if (!state.sessionActive) {
          setStatus("Сеанс выключен");
          return;
        }
        markUserGesture();
        if (!state.control) {
          if (cfg.hasControl) {
            state.control = true;
            state.keyboard = true;
            updateButtons();
            screenWrapper.focus();
          }
          return;
        }
        pointerActiveId = event.pointerId;
        if (screenWrapper.setPointerCapture) {
          try {
            screenWrapper.setPointerCapture(pointerActiveId);
          } catch (err) {
          }
        }
        var coords = getRemoteCoords(event);
        if (!coords) return;
        sendInput({ action: "mouse_down", x: coords.x, y: coords.y, button: mouseButtonName(event) });
        event.preventDefault();
      });
      screenWrapper.addEventListener("pointermove", function (event) {
        if (!state.sessionActive) return;
        if (!state.control) return;
        if (pointerActiveId !== null && event.pointerId !== pointerActiveId) return;
        scheduleMove(event);
      });
      screenWrapper.addEventListener("pointerup", function (event) {
        if (!state.sessionActive) return;
        if (!state.control) return;
        if (pointerActiveId !== null && event.pointerId !== pointerActiveId) return;
        var coords = getRemoteCoords(event);
        if (!coords) return;
        sendInput({ action: "mouse_up", x: coords.x, y: coords.y, button: mouseButtonName(event) });
        if (screenWrapper.releasePointerCapture) {
          try {
            screenWrapper.releasePointerCapture(event.pointerId);
          } catch (err) {
          }
        }
        pointerActiveId = null;
      });
      screenWrapper.addEventListener("pointercancel", function () {
        pointerActiveId = null;
      });
    } else {
      screenWrapper.addEventListener("mousemove", function (event) {
        if (!state.sessionActive) return;
        if (!state.control) return;
        scheduleMove(event);
      });
      screenWrapper.addEventListener("mousedown", function (event) {
        if (!state.sessionActive) {
          setStatus("Сеанс выключен");
          return;
        }
        markUserGesture();
        if (!state.control) {
          if (cfg.hasControl) {
            state.control = true;
            state.keyboard = true;
            updateButtons();
            screenWrapper.focus();
          }
          return;
        }
        var coords = getRemoteCoords(event);
        if (!coords) return;
        sendInput({ action: "mouse_down", x: coords.x, y: coords.y, button: mouseButtonName(event) });
      });
      screenWrapper.addEventListener("mouseup", function (event) {
        if (!state.sessionActive) return;
        if (!state.control) return;
        var coords = getRemoteCoords(event);
        if (!coords) return;
        sendInput({ action: "mouse_up", x: coords.x, y: coords.y, button: mouseButtonName(event) });
      });
    }
    screenWrapper.addEventListener("dblclick", function (event) {
      if (!state.sessionActive) return;
      if (!state.control) return;
      var coords = getRemoteCoords(event);
      if (!coords) return;
      sendInput({ action: "double_click", x: coords.x, y: coords.y, button: mouseButtonName(event) });
    });
    screenWrapper.addEventListener("contextmenu", function (event) {
      if (!state.sessionActive) return;
      if (state.control) {
        event.preventDefault();
      }
    });
    screenWrapper.addEventListener("wheel", function (event) {
      if (!state.sessionActive) return;
      if (!state.control) return;
      var coords = getRemoteCoords(event);
      if (!coords) return;
      sendInput({
        action: "scroll",
        x: coords.x,
        y: coords.y,
        delta_y: event.deltaY,
        delta_x: event.deltaX
      });
      event.preventDefault();
    }, { passive: false });

    screenWrapper.addEventListener("keydown", function (event) {
      if (!state.sessionActive) return;
      handleKey(event, "key_down");
    });
    screenWrapper.addEventListener("keyup", function (event) {
      if (!state.sessionActive) return;
      handleKey(event, "key_up");
    });
  }

  document.querySelectorAll("[data-hotkey]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (!state.control) return;
      markUserGesture();
      var keys = (btn.getAttribute("data-hotkey") || "").split("+");
      if (!keys.length) return;
      sendInput({ action: "hotkey", keys: keys });
    });
  });

  if (clipboardGetBtn) {
    clipboardGetBtn.addEventListener("click", function () {
      fetch(cfg.clipboardUrl, {
        credentials: "same-origin",
        headers: { "X-RDP-Token": cfg.token }
      })
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
          if (!data.ok) {
            throw new Error(data.error || "Не удалось получить буфер обмена.");
          }
          clipboardText.value = data.text || "";
          setStatus("Буфер обмена получен.");
        })
        .catch(function (err) {
          setStatus(err.message || "Ошибка буфера обмена.");
        });
    });
  }

  if (clipboardSendBtn) {
    clipboardSendBtn.addEventListener("click", function () {
      fetch(cfg.clipboardUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": cfg.csrfToken,
          "X-RDP-Token": cfg.token
        },
        credentials: "same-origin",
        body: JSON.stringify({ text: clipboardText.value || "" })
      })
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
          if (!data.ok) {
            throw new Error(data.error || "Не удалось отправить буфер обмена.");
          }
          setStatus("Буфер обмена отправлен.");
        })
        .catch(function (err) {
          setStatus(err.message || "Ошибка буфера обмена.");
        });
    });
  }

  if (clipboardLocalBtn) {
    clipboardLocalBtn.addEventListener("click", function () {
      if (!navigator.clipboard || !navigator.clipboard.readText) {
        setStatus("Локальный буфер обмена недоступен.");
        return;
      }
      navigator.clipboard.readText()
        .then(function (text) {
          clipboardText.value = text;
        })
        .catch(function () {
          setStatus("Не удалось прочитать локальный буфер обмена.");
        });
    });
  }

  if (textSendBtn) {
    textSendBtn.addEventListener("click", function () {
      var text = textInput.value || "";
      if (!text) {
        setStatus("Введите текст для отправки.");
        return;
      }
      sendInput({ action: "text", text: text });
      setStatus("Текст отправлен.");
    });
  }

  if (fileInput) {
    fileInput.addEventListener("change", function (event) {
      uploadFiles(event.target.files);
      fileInput.value = "";
    });
  }

  if (filesRefreshBtn) {
    filesRefreshBtn.addEventListener("click", refreshFiles);
  }

  if (fullscreenBtn) {
    fullscreenBtn.addEventListener("click", function () {
      toggleFullscreen();
    });
  }
  document.addEventListener("fullscreenchange", updateFullscreenButton);
  document.addEventListener("webkitfullscreenchange", updateFullscreenButton);
  document.addEventListener("msfullscreenchange", updateFullscreenButton);


  if (sessionToggle) {
    sessionToggle.addEventListener("click", function () {
      markUserGesture();
      if (state.sessionActive) {
        stopSession();
      } else {
        startSession();
      }
    });
  }

  if (screen) {
    screen.addEventListener("error", function () {
      if (!state.sessionActive) {
        return;
      }
      if (renderMode !== "mjpeg") {
        return;
      }
      if (!screen.getAttribute("src")) {
        return;
      }
      sendClientLog(
        "stream_img_error",
        {
          mode: renderMode,
          transport: mjpegTransport,
          attempt: state.streamAttempt || "",
          naturalWidth: screen.naturalWidth || 0,
          naturalHeight: screen.naturalHeight || 0,
          currentSrc: (screen.currentSrc || screen.src || "").slice(0, 180)
        },
        "warning"
      );
      handleStreamFailure("Поток не удалось запустить. Переключитесь на кадровый режим.");
    });
    screen.addEventListener("load", function () {
      if (!state.sessionActive) {
        return;
      }
      if (renderMode !== "mjpeg") {
        return;
      }
      sendClientLog(
        "stream_img_load",
        {
          mode: renderMode,
          transport: mjpegTransport,
          attempt: state.streamAttempt || "",
          naturalWidth: screen.naturalWidth || 0,
          naturalHeight: screen.naturalHeight || 0
        }
      );
      markMjpegReady();
    });
  }

  var initialMode = readStoredRenderMode() || readSelectedRenderMode() || "mjpeg";
  setRenderMode(initialMode, false);
  updateButtons();
  if (!cfg.hasScreen) {
    showError("Экран недоступен. Проверьте mss/Pillow и перезапустите панель.");
  }
  if (!cfg.hasControl) {
    if (captureBtn) captureBtn.disabled = true;
    if (controlBtn) controlBtn.disabled = true;
    if (keyboardBtn) keyboardBtn.disabled = true;
  }
  refreshMonitors();
  refreshFiles();

  window.addEventListener("beforeunload", function () {
    if (state.sessionActive) {
      sendSessionState("stop");
    }
  });
})();

